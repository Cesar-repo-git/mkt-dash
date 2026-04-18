"""
Pipeline orchestrator.

Manages all background workers:
  - Initial symbol scan + WS startup
  - Periodic funding rate refresh
  - Periodic OI polling
  - Periodic macro refresh
  - Periodic 4h/1d candle refresh (anchors + active symbols)
  - Symbol list refresh (detect newly qualifying symbols)
  - Session-aware scheduling (active vs off-hours poll intervals)
"""

import logging
import threading
import time
from datetime import datetime

import pytz

from config import (
    ANCHOR_SYMBOLS,
    SESSION_TZ,
    SESSION_START_HOUR,
    SESSION_END_HOUR,
    POLL_FUNDING_ACTIVE,
    POLL_FUNDING_OFFHOURS,
    POLL_MACRO_ACTIVE,
    POLL_MACRO_OFFHOURS,
    POLL_4H_CANDLES,
    POLL_SYMBOL_REFRESH,
)
from store import store
from data import binance_rest as rest
from data import binance_ws   as ws
from data import macro         as macro_mod
from classifiers import engine as classifier_engine

log = logging.getLogger(__name__)
_tz = pytz.timezone(SESSION_TZ)


# ── Session helper ────────────────────────────────────────────────────────

def _in_session() -> bool:
    now = datetime.now(_tz)
    return SESSION_START_HOUR <= now.hour < SESSION_END_HOUR


def _poll_interval(active: int, offhours: int) -> int:
    return active if _in_session() else offhours


# ── Worker: funding rates ─────────────────────────────────────────────────

def _funding_worker():
    log.info("Funding worker started")
    while True:
        try:
            rates = rest.fetch_all_funding()
            active = store.get_active_symbols()
            for sym, rate in rates.items():
                if sym in active:
                    store.set_funding(sym, rate)
        except Exception as e:
            log.error(f"Funding worker error: {e}")
        interval = _poll_interval(POLL_FUNDING_ACTIVE, POLL_FUNDING_OFFHOURS)
        log.debug(f"Funding: sleeping {interval//60}m (session={_in_session()})")
        time.sleep(interval)


# ── Worker: open interest ─────────────────────────────────────────────────

def _oi_worker():
    log.info("OI worker started")
    while True:
        try:
            active = list(store.get_active_symbols())
            # Prioritise anchors first
            ordered = ANCHOR_SYMBOLS + [s for s in active if s not in ANCHOR_SYMBOLS]
            now = datetime.now(tz=pytz.utc)
            for sym in ordered:
                oi = rest.fetch_oi(sym)
                if oi is not None:
                    store.push_oi(sym, oi, now)
        except Exception as e:
            log.error(f"OI worker error: {e}")
        interval = _poll_interval(POLL_FUNDING_ACTIVE, POLL_FUNDING_OFFHOURS)
        log.debug(f"OI: sleeping {interval//60}m")
        time.sleep(interval)


# ── Worker: macro ─────────────────────────────────────────────────────────

def _macro_worker():
    log.info("Macro worker started")
    macro_mod.update_fomc()   # immediate, cheap
    macro_mod.fetch_fear_greed()
    macro_mod.fetch_vix()
    macro_mod.fetch_etf_flows()
    while True:
        interval = _poll_interval(POLL_MACRO_ACTIVE, POLL_MACRO_OFFHOURS)
        log.debug(f"Macro: sleeping {interval//60}m")
        time.sleep(interval)
        try:
            macro_mod.refresh_all()
        except Exception as e:
            log.error(f"Macro worker error: {e}")


# ── Worker: 4h / 1d candles ───────────────────────────────────────────────

def _multitf_worker():
    """Refresh 4h and 1d candles for BTC/ETH anchors and all active symbols."""
    log.info("Multi-TF candle worker started")
    while True:
        try:
            active = list(store.get_active_symbols())
            # Anchors: full 4h + 1d
            for sym in ANCHOR_SYMBOLS:
                c4h = rest.fetch_candles_4h(sym)
                c1d = rest.fetch_candles_1d(sym)
                if c4h: store.set_candles_4h(sym, c4h)
                if c1d: store.set_candles_1d(sym, c1d)
                log.debug(f"  4h/1d refreshed: {sym}")
            # Other active symbols: 4h only (used for regime detection in Stage 2)
            others = [s for s in active if s not in ANCHOR_SYMBOLS]
            for sym in others:
                c4h = rest.fetch_candles_4h(sym)
                if c4h: store.set_candles_4h(sym, c4h)
        except Exception as e:
            log.error(f"Multi-TF worker error: {e}")
        log.debug(f"Multi-TF: sleeping {POLL_4H_CANDLES//3600}h")
        time.sleep(POLL_4H_CANDLES)


# ── Worker: symbol refresh ────────────────────────────────────────────────

def _symbol_refresh_worker():
    log.info("Symbol refresh worker started")
    while True:
        time.sleep(POLL_SYMBOL_REFRESH)
        try:
            all_syms = rest.fetch_all_usdt_perp_symbols()
            current  = store.get_active_symbols()
            added    = rest.refresh_symbol_list(current, all_syms)
            if added:
                log.info(f"Symbol refresh: {len(added)} new — requesting WS restart")
                ws._restart_needed.set()
        except Exception as e:
            log.error(f"Symbol refresh worker error: {e}")


# ── Worker: diagnostics ───────────────────────────────────────────────────

def _diag_worker():
    while True:
        time.sleep(300)
        s = store.summary()
        macro = s["macro"]
        log.info(
            f"📊 {s['active_symbols']} symbols | "
            f"{s['symbols_with_candles']} with candles | "
            f"F&G: {macro.get('fear_greed')} | "
            f"VIX: {macro.get('vix')} | "
            f"Funding tracked: {s['funding_count']} | "
            f"Session: {'🟢' if _in_session() else '⚪'}"
        )


# ── Entry point ───────────────────────────────────────────────────────────

def start():
    """
    Full pipeline startup sequence:
    1. Discover and warm up qualifying symbols (REST)
    2. Launch WebSocket connections
    3. Kick off all background workers
    """
    log.info("=" * 60)
    log.info("🚀 MKT-DASH pipeline starting")
    log.info("=" * 60)

    # Step 1 — Symbol discovery + historical warm-up
    all_syms = rest.fetch_all_usdt_perp_symbols()
    qualifying = rest.initial_scan(all_syms)
    if not qualifying:
        log.error("No qualifying symbols found — aborting")
        return

    # Step 2 — Initial funding + OI snapshot
    log.info("Fetching initial funding rates and OI...")
    rates = rest.fetch_all_funding()
    now = datetime.now(tz=pytz.utc)
    for sym in qualifying:
        if sym in rates:
            store.set_funding(sym, rates[sym])
        oi = rest.fetch_oi(sym)
        if oi is not None:
            store.push_oi(sym, oi, now)

    # Step 3 — Multi-TF candles for anchors
    for sym in ANCHOR_SYMBOLS:
        c4h = rest.fetch_candles_4h(sym)
        c1d = rest.fetch_candles_1d(sym)
        if c4h: store.set_candles_4h(sym, c4h)
        if c1d: store.set_candles_1d(sym, c1d)

    # Step 4 — WebSocket connections
    ws.start(qualifying)
    ws.start_restart_monitor()

    # Step 5 — Background workers
    workers = [
        ("funding",        _funding_worker),
        ("oi",             _oi_worker),
        ("macro",          _macro_worker),
        ("multi-tf",       _multitf_worker),
        ("symbol-refresh", _symbol_refresh_worker),
        ("diagnostics",    _diag_worker),
    ]
    for name, fn in workers:
        t = threading.Thread(target=fn, daemon=True, name=name)
        t.start()
        log.info(f"  ✓ Worker started: {name}")

    # Step 6 — Stage 2: classifier engine (after WS is live so candle callback works)
    classifier_engine.start()
    log.info("  ✓ Classifier engine started")

    log.info("=" * 60)
    log.info(f"✅ Pipeline live — {len(qualifying)} symbols tracked")
    log.info(f"   Session hours: {SESSION_START_HOUR}:00–{SESSION_END_HOUR}:00 {SESSION_TZ}")
    log.info(f"   Active now: {'YES' if _in_session() else 'NO (off-hours mode)'}")
    log.info("=" * 60)

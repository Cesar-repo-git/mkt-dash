"""
Classifier engine.

Orchestrates regime classification, MO/MR scoring, and entry trigger
detection across all active symbols.

Two run modes:
  1. Full scan   — regime + scores + triggers; runs every 5 min (session)
                   or 15 min (off-hours). Heavy: reads 1m + 4h candles.
  2. Trigger scan — entry triggers only on the symbol that just closed a candle.
                   Light: called on every WS candle close via callback.

Results are stored in a thread-safe dict and exposed via get_signals().

Signal dict schema (one per symbol):
    {
        'symbol':         str,
        'regime':         'TRENDING' | 'RANGING' | 'UNCLEAR',
        'regime_conf':    float,
        'adx':            float,
        'hurst':          float or None,

        'setup':          'MO_LONG' | 'MO_SHORT' | 'MR_LONG' | 'MR_SHORT' | None,
        'score':          float 0–100,
        'viable':         bool,
        'components':     dict,

        'trigger':        dict or None,   # from signals.py
        'trigger_label':  str,
        'trigger_age_s':  int,            # seconds since trigger fired

        'price':          float,
        'vwap':           float or None,
        'vwap_pct':       float or None,  # % price vs VWAP
        'funding':        float or None,
        'oi_usd':         float or None,
        'oi_direction':   str,
        'vol_trend':      str,

        'support':        float or None,
        'resistance':     float or None,

        'last_full_scan': datetime or None,
        'last_trigger_scan': datetime or None,
    }
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import pytz

from config import (
    ANCHOR_SYMBOLS,
    SESSION_TZ,
    SESSION_START_HOUR,
    SESSION_END_HOUR,
)
from store import store
from classifiers import regime as regime_mod
from classifiers import mo as mo_mod
from classifiers import mr as mr_mod
from classifiers import signals as sig_mod

log = logging.getLogger(__name__)

_tz = pytz.timezone(SESSION_TZ)

# ── Result store ──────────────────────────────────────────────────────────

_results:     dict[str, dict] = {}
_results_lock = threading.RLock()

# Trigger lifetime: triggers older than this are cleared from display
TRIGGER_TTL_SECONDS = 300   # 5 minutes


# ── Public API ────────────────────────────────────────────────────────────

def get_signals(
    min_score:    float  = 0.0,
    viable_only:  bool   = False,
    setup_filter: Optional[str] = None,   # 'MO_LONG' | 'MO_SHORT' | 'MR_LONG' | 'MR_SHORT'
    triggered_only: bool = False,
) -> list[dict]:
    """
    Returns a sorted list of signal dicts (highest score first).
    Anchors (BTC, ETH) always appear at the top regardless of score.
    """
    with _results_lock:
        results = list(_results.values())

    now = datetime.now(timezone.utc)

    out = []
    for r in results:
        if viable_only and not r.get("viable"):
            continue
        if min_score and r.get("score", 0) < min_score:
            continue
        if setup_filter and r.get("setup") != setup_filter:
            continue
        if triggered_only and not r.get("trigger"):
            continue

        # Expire stale triggers
        r = dict(r)
        trigger = r.get("trigger")
        if trigger:
            age = (now - trigger["candle_time"]).total_seconds()
            r["trigger_age_s"] = int(age)
            if age > TRIGGER_TTL_SECONDS:
                r["trigger"]       = None
                r["trigger_label"] = ""
                r["trigger_age_s"] = 0
        else:
            r["trigger_age_s"] = 0

        out.append(r)

    # Sort: anchors first, then by score descending
    def sort_key(r):
        is_anchor = 1 if r["symbol"] in ANCHOR_SYMBOLS else 0
        return (-is_anchor, -(r.get("score") or 0))

    return sorted(out, key=sort_key)


# ── Full classification for one symbol ───────────────────────────────────

def _classify_symbol(symbol: str) -> Optional[dict]:
    candles_1m = store.get_candles_1m(symbol)
    candles_4h = store.get_candles_4h(symbol)
    oi_snaps   = store.get_oi(symbol)
    funding    = store.get_funding(symbol)

    if len(candles_1m) < 20:
        log.debug(f"  {symbol}: not enough 1m candles ({len(candles_1m)})")
        return None

    latest_candle = candles_1m[-1]
    price  = latest_candle["close"]
    vwap   = latest_candle.get("vwap")
    latest_oi = store.get_latest_oi(symbol)

    # ── Regime ────────────────────────────────────────────────────────
    regime = regime_mod.classify(candles_4h)

    # ── Score: MO ────────────────────────────────────────────────────
    mo_result = mo_mod.score(
        symbol, candles_1m, candles_4h,
        regime, vwap, price, oi_snaps,
    )

    # ── Score: MR ────────────────────────────────────────────────────
    mr_long, mr_short = mr_mod.score(
        symbol, candles_1m, candles_4h, regime, oi_snaps
    )

    # ── Pick dominant setup based on regime + score ───────────────────
    if regime["regime"] == "TRENDING":
        primary = mo_result
    elif regime["regime"] == "RANGING":
        # Choose Long vs Short based on which has a trigger (or default Long)
        primary = mr_long
    else:
        # UNCLEAR: surface whichever scores highest overall
        candidates = [mo_result, mr_long, mr_short]
        primary = max(candidates, key=lambda x: x["score"])

    # ── Entry triggers ────────────────────────────────────────────────
    support    = primary.get("support")
    resistance = primary.get("resistance")

    breakout = sig_mod.detect_breakout(candles_1m, resistance, support)
    sfp      = sig_mod.detect_sfp(candles_1m, resistance, support)

    # Prefer the trigger that matches the primary setup direction
    trigger = None
    if regime["regime"] == "TRENDING":
        trigger = breakout
    elif regime["regime"] == "RANGING":
        trigger = sfp
    else:
        trigger = breakout or sfp

    # ── Build result ──────────────────────────────────────────────────
    vwap_pct = round((price - vwap) / vwap * 100, 3) if (vwap and vwap > 0) else None

    return {
        "symbol":          symbol,
        "regime":          regime["regime"],
        "regime_conf":     regime["confidence"],
        "adx":             regime.get("adx"),
        "hurst":           regime.get("hurst"),

        "setup":           primary["setup"],
        "score":           primary["score"],
        "viable":          primary["viable"],
        "components":      primary.get("components", {}),

        "trigger":         trigger,
        "trigger_label":   sig_mod.trigger_label(trigger),

        "price":           round(price, 4),
        "vwap":            round(vwap, 4) if vwap else None,
        "vwap_pct":        vwap_pct,
        "funding":         funding,
        "oi_usd":          latest_oi["oi_usd"] if latest_oi else None,
        "oi_direction":    primary.get("oi_direction", "UNKNOWN"),
        "vol_trend":       primary.get("vol_trend", "UNKNOWN"),

        "support":         primary.get("support"),
        "resistance":      primary.get("resistance"),

        # MO detail (always available)
        "mo_long_score":   mo_result["score"] if mo_result["setup"] == "MO_LONG" else None,
        "mo_short_score":  mo_result["score"] if mo_result["setup"] == "MO_SHORT" else None,
        "staircase_score": mo_result.get("staircase_score"),
        "trend_duration":  mo_result.get("trend_duration"),

        # MR detail
        "mr_long_score":   mr_long["score"],
        "mr_short_score":  mr_short["score"],
        "range_pct":       mr_long.get("range_pct"),
        "range_duration":  mr_long.get("range_duration"),

        "last_full_scan":     datetime.now(timezone.utc),
        "last_trigger_scan":  datetime.now(timezone.utc),
    }


# ── Fast trigger-only scan (called on every candle close) ────────────────

def on_candle(symbol: str, candle: dict):
    """
    Registered as WS candle callback. Only re-evaluates entry triggers
    to avoid running full classification 50+ times per minute.
    """
    try:
        with _results_lock:
            existing = _results.get(symbol)

        if not existing:
            return   # symbol not yet classified

        candles_1m = store.get_candles_1m(symbol, limit=5)
        support    = existing.get("support")
        resistance = existing.get("resistance")

        regime_str = existing.get("regime", "UNCLEAR")
        breakout = sig_mod.detect_breakout(candles_1m, resistance, support)
        sfp      = sig_mod.detect_sfp(candles_1m, resistance, support)

        trigger = None
        if regime_str == "TRENDING":
            trigger = breakout
        elif regime_str == "RANGING":
            trigger = sfp
        else:
            trigger = breakout or sfp

        if trigger:
            log.info(
                f"🎯 {symbol} — {sig_mod.trigger_label(trigger)} "
                f"(score {existing.get('score', 0):.0f})"
            )
            with _results_lock:
                if symbol in _results:
                    _results[symbol]["trigger"]            = trigger
                    _results[symbol]["trigger_label"]      = sig_mod.trigger_label(trigger)
                    _results[symbol]["last_trigger_scan"]  = datetime.now(timezone.utc)

    except Exception as e:
        log.error(f"on_candle error for {symbol}: {e}")


# ── Full scan loop ────────────────────────────────────────────────────────

def _in_session() -> bool:
    now = datetime.now(_tz)
    return SESSION_START_HOUR <= now.hour < SESSION_END_HOUR


def run_full_scan():
    """Classify all active symbols. Updates _results in place."""
    symbols = list(store.get_active_symbols())
    log.info(f"Full scan: {len(symbols)} symbols")
    updated = 0
    errors  = 0
    for sym in symbols:
        try:
            result = _classify_symbol(sym)
            if result:
                with _results_lock:
                    _results[sym] = result
                updated += 1
        except Exception as e:
            log.error(f"Classify {sym}: {e}")
            errors += 1
    log.info(f"Full scan complete: {updated} classified, {errors} errors")


def _scan_loop():
    active_interval   = 5 * 60    # 5 min during session
    offhours_interval = 15 * 60   # 15 min off-hours

    while True:
        interval = active_interval if _in_session() else offhours_interval
        time.sleep(interval)
        try:
            run_full_scan()
        except Exception as e:
            log.error(f"Scan loop error: {e}")


def start():
    """
    1. Run an immediate full scan to populate initial results.
    2. Register the WS candle callback for fast trigger detection.
    3. Start the background full-scan loop.
    """
    log.info("Engine starting — initial full scan")
    run_full_scan()

    # Register fast trigger callback with WS manager
    from data import binance_ws
    binance_ws.set_candle_callback(on_candle)
    log.info("Candle callback registered")

    t = threading.Thread(target=_scan_loop, daemon=True, name="engine-scan-loop")
    t.start()
    log.info("Engine scan loop started")

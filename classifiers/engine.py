"""
Classifier engine.

Orchestrates regime classification, MO/MR scoring, and entry trigger
detection across all active symbols.

Signal dict schema (one per symbol):
    {
        'symbol':           str,
        'regime':           'TRENDING' | 'RANGING' | 'UNCLEAR',
        'regime_conf':      float,
        'adx':              float,

        'setup':            'MO_LONG' | 'MO_SHORT' | 'MR_LONG' | 'MR_SHORT' | None,
        'score':            float 0–100,
        'viable':           bool,
        'components':       dict,

        'price':            float,
        'vwap':             float or None,
        'vwap_upper1':      float or None,
        'vwap_lower1':      float or None,
        'vwap_pct':         float or None,
        'funding':          float or None,

        'oi_usd':           float or None,
        'oi_direction':     str,
        'oi_chg_15m':       float or None,   # % OI change over 15 min
        'oi_chg_1h':        float or None,
        'oi_chg_4h':        float or None,
        'oi_chg_1d':        float or None,

        'vol_trend':        str,
        'vol_ma30':         float or None,
        'vol_ma60':         float or None,

        'support':          float or None,   # anchors only
        'resistance':       float or None,   # anchors only

        'prev_day_high':          float or None,
        'prev_day_low':           float or None,
        'prev_day_high_dist_pct': float or None,
        'prev_day_low_dist_pct':  float or None,

        'last_full_scan':   datetime or None,
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
from classifiers import signal_ledger as ledger
from classifiers.indicators import compute_prev_day_levels

log = logging.getLogger(__name__)

_tz = pytz.timezone(SESSION_TZ)

# ── Result store ──────────────────────────────────────────────────────────

_results:     dict[str, dict] = {}
_results_lock = threading.RLock()

TRIGGER_TTL_SECONDS = 300   # 5 minutes


# ── Public API ────────────────────────────────────────────────────────────

def get_signals(
    min_score:      float  = 0.0,
    viable_only:    bool   = False,
    setup_filter:   Optional[str] = None,
    triggered_only: bool   = False,
) -> list[dict]:
    """Returns a sorted list of signal dicts (highest score first)."""
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

    def sort_key(r):
        is_anchor = 1 if r["symbol"] in ANCHOR_SYMBOLS else 0
        return (-is_anchor, -(r.get("score") or 0))

    return sorted(out, key=sort_key)


# ── Full classification for one symbol ───────────────────────────────────

def _classify_symbol(symbol: str) -> Optional[dict]:
    candles_1m = store.get_candles_1m(symbol)
    candles_1h = store.get_candles_1h(symbol)
    candles_4h = store.get_candles_4h(symbol)
    candles_1d = store.get_candles_1d(symbol)
    oi_snaps   = store.get_oi(symbol)
    funding    = store.get_funding(symbol)

    if len(candles_1m) < 20:
        log.debug(f"  {symbol}: not enough 1m candles ({len(candles_1m)})")
        return None

    latest_candle = candles_1m[-1]
    price         = latest_candle["close"]
    latest_oi     = store.get_latest_oi(symbol)
    vwap_bands    = store.get_vwap_bands(symbol)

    # ── Regime ────────────────────────────────────────────────────────
    regime = regime_mod.classify(candles_4h)

    # ── Score: MO ─────────────────────────────────────────────────────
    mo_result = mo_mod.score(
        symbol, candles_1m, candles_1h, candles_4h,
        regime, vwap_bands, price, oi_snaps,
    )

    # ── Score: MR ─────────────────────────────────────────────────────
    mr_long, mr_short = mr_mod.score(
        symbol, candles_1m, candles_1h, candles_4h, regime, oi_snaps
    )

    # ── Pick dominant setup based on regime ───────────────────────────
    if regime["regime"] == "TRENDING":
        primary = mo_result
    elif regime["regime"] == "RANGING":
        primary = mr_long
    else:
        candidates = [mo_result, mr_long, mr_short]
        primary = max(candidates, key=lambda x: x["score"])

    # ── Entry triggers ────────────────────────────────────────────────
    support    = primary.get("support")
    resistance = primary.get("resistance")

    breakout = sig_mod.detect_breakout(candles_1m, resistance, support)
    sfp      = sig_mod.detect_sfp(candles_1m, resistance, support)

    trigger = None
    if regime["regime"] == "TRENDING":
        trigger = breakout
    elif regime["regime"] == "RANGING":
        trigger = sfp
    else:
        trigger = breakout or sfp

    # ── OI windowed % changes ─────────────────────────────────────────
    oi_chg_15m = store.get_oi_change_pct(symbol, 15)
    oi_chg_1h  = store.get_oi_change_pct(symbol, 60)
    oi_chg_4h  = store.get_oi_change_pct(symbol, 240)
    oi_chg_1d  = store.get_oi_change_pct(symbol, 1440)

    # ── VWAP fields ───────────────────────────────────────────────────
    vwap    = vwap_bands.get("vwap")    if vwap_bands else None
    upper1  = vwap_bands.get("upper1")  if vwap_bands else None
    lower1  = vwap_bands.get("lower1")  if vwap_bands else None
    vwap_pct = round((price - vwap) / vwap * 100, 3) if (vwap and vwap > 0) else None

    # ── Previous day H/L ─────────────────────────────────────────────
    prev_day = compute_prev_day_levels(candles_1d, price) if candles_1d else {
        "prev_day_high": None, "prev_day_low": None,
        "prev_day_high_dist_pct": None, "prev_day_low_dist_pct": None,
    }

    return {
        "symbol":           symbol,
        "regime":           regime["regime"],
        "regime_conf":      regime["confidence"],
        "adx":              regime.get("adx"),

        "setup":            primary["setup"],
        "score":            primary["score"],
        "viable":           primary["viable"],
        "components":       primary.get("components", {}),

        "trigger":          trigger,
        "trigger_label":    sig_mod.trigger_label(trigger),

        "price":            round(price, 4),
        "vwap":             round(vwap, 4) if vwap else None,
        "vwap_upper1":      round(upper1, 4) if upper1 else None,
        "vwap_lower1":      round(lower1, 4) if lower1 else None,
        "vwap_pct":         vwap_pct,
        "funding":          funding,

        "oi_usd":           latest_oi["oi_usd"] if latest_oi else None,
        "oi_direction":     primary.get("oi_direction", "UNKNOWN"),
        "oi_chg_15m":       round(oi_chg_15m, 3) if oi_chg_15m is not None else None,
        "oi_chg_1h":        round(oi_chg_1h, 3)  if oi_chg_1h  is not None else None,
        "oi_chg_4h":        round(oi_chg_4h, 3)  if oi_chg_4h  is not None else None,
        "oi_chg_1d":        round(oi_chg_1d, 3)  if oi_chg_1d  is not None else None,

        "vol_trend":        primary.get("vol_trend", "UNKNOWN"),
        "vol_ma30":         mo_result.get("vol_ma30"),
        "vol_ma60":         mo_result.get("vol_ma60"),

        "support":          primary.get("support"),
        "resistance":       primary.get("resistance"),
        "support_dist_pct":    primary.get("support_dist_pct"),
        "resistance_dist_pct": primary.get("resistance_dist_pct"),

        "prev_day_high":          prev_day["prev_day_high"],
        "prev_day_low":           prev_day["prev_day_low"],
        "prev_day_high_dist_pct": prev_day["prev_day_high_dist_pct"],
        "prev_day_low_dist_pct":  prev_day["prev_day_low_dist_pct"],

        # MO detail
        "mo_long_score":   mo_result["score"] if mo_result["setup"] == "MO_LONG" else None,
        "mo_short_score":  mo_result["score"] if mo_result["setup"] == "MO_SHORT" else None,
        "staircase_score": mo_result.get("staircase_score"),
        "trend_duration":  mo_result.get("trend_duration"),

        # MR detail
        "mr_long_score":   mr_long["score"],
        "mr_short_score":  mr_short["score"],
        "range_pct":       mr_long.get("range_pct"),
        "range_duration":  mr_long.get("range_duration"),
        "ma_crossings":    mr_long.get("ma_crossings"),

        "last_full_scan":  datetime.now(timezone.utc),
    }


# ── Fast trigger-only scan (called on every candle close) ────────────────

def on_candle(symbol: str, candle: dict):
    try:
        with _results_lock:
            existing = _results.get(symbol)

        if not existing:
            return

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
                    _results[symbol]["trigger"]       = trigger
                    _results[symbol]["trigger_label"] = sig_mod.trigger_label(trigger)

    except Exception as e:
        log.error(f"on_candle error for {symbol}: {e}")


# ── Full scan loop ────────────────────────────────────────────────────────

def _in_session() -> bool:
    now = datetime.now(_tz)
    return SESSION_START_HOUR <= now.hour < SESSION_END_HOUR


def run_full_scan():
    symbols = list(store.get_active_symbols())
    log.info(f"Full scan: {len(symbols)} symbols")
    updated  = 0
    viable   = 0
    errors   = 0
    for sym in symbols:
        try:
            result = _classify_symbol(sym)
            if result:
                with _results_lock:
                    _results[sym] = result
                updated += 1
                if result.get("viable"):
                    ledger.upsert(sym, result)
                    viable += 1
        except Exception as e:
            log.error(f"Classify {sym}: {e}")
            errors += 1

    expired = ledger.expire()
    log.info(
        f"Full scan complete: {updated} classified, {viable} viable, "
        f"{expired} ledger entries expired, {errors} errors"
    )


def _scan_loop():
    active_interval   = 5 * 60
    offhours_interval = 15 * 60

    while True:
        interval = active_interval if _in_session() else offhours_interval
        time.sleep(interval)
        try:
            run_full_scan()
        except Exception as e:
            log.error(f"Scan loop error: {e}")


def get_ledger() -> list[dict]:
    """Return all active signal ledger entries (for dashboard signals table)."""
    return ledger.get_all()


def start():
    log.info("Engine starting — loading signal ledger and running initial full scan")
    ledger.load()
    run_full_scan()

    from data import binance_ws
    binance_ws.set_candle_callback(on_candle)
    log.info("Candle callback registered")

    t = threading.Thread(target=_scan_loop, daemon=True, name="engine-scan-loop")
    t.start()
    log.info("Engine scan loop started")

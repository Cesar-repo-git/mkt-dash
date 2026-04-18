"""
MR (Mean Reversion) setup scorer.

Scores MR Long and MR Short independently (0–100).
Hurst Exponent removed — ADX-only regime component.

Score breakdown:
    Regime quality        30 pts  — low ADX (ranging signal)
    Range quality         30 pts  — ≥5% range, MA(30)×SMMA(120) crossings, ≥1h window
    Volume quality        20 pts  — flat or decreasing volume
    OI choppiness         20 pts  — no sustained OI direction

S/R levels: only computed for BTC/ETH anchors.

Output dict:
    {
        'setup':              'MR_LONG' | 'MR_SHORT',
        'score':              float 0–100,
        'components':         dict,
        'range_high':         float or None,
        'range_low':          float or None,
        'range_pct':          float or None,
        'range_duration':     int,
        'ma_crossings':       int,
        'vol_trend':          str,
        'oi_direction':       str,
        'adx':                float,
        'support':            float or None,   # anchors only
        'resistance':         float or None,   # anchors only
        'viable':             bool,
    }
"""

from typing import Optional

from config import ANCHOR_SYMBOLS
from classifiers.indicators import (
    compute_range_quality,
    compute_vol_slope,
    classify_vol_trend,
    classify_oi_direction,
    compute_sr_levels,
)

VIABLE_THRESHOLD = 55.0


def score(
    symbol:         str,
    candles_1m:     list,
    candles_1h:     list,
    candles_4h:     list,
    regime:         dict,
    oi_snapshots:   list,
) -> tuple:
    """
    Returns (mr_long_result, mr_short_result).
    Conditions are symmetric; setup label is the only difference.
    """
    result = _score_mr(symbol, candles_1m, candles_1h, candles_4h, regime, oi_snapshots)
    long_result  = {**result, "setup": "MR_LONG"}
    short_result = {**result, "setup": "MR_SHORT"}
    return long_result, short_result


def _score_mr(
    symbol:       str,
    candles_1m:   list,
    candles_1h:   list,
    candles_4h:   list,
    regime:       dict,
    oi_snapshots: list,
) -> dict:

    adx = regime.get("adx") or 0.0

    # ── Component: Regime quality (30 pts) — ADX only ─────────────────
    # Low ADX → more ranging
    regime_pts = min(30.0, max(0.0, (20.0 - adx) / 20.0 * 30.0))

    # ── Component: Range quality (30 pts) ─────────────────────────────
    # Prefer 1h candles (60+ bars); fall back to 1m if insufficient
    range_candles = candles_1h if len(candles_1h) >= 60 else candles_1m
    rq = compute_range_quality(range_candles)
    range_pts = (rq["score"] / 100.0 * 30.0) if rq else 0.0

    # ── Component: Volume quality (20 pts) ────────────────────────────
    vol_slope = compute_vol_slope(candles_1m)
    vol_trend = classify_vol_trend(vol_slope)
    if vol_trend == "DECREASING":
        volume_pts = 20.0
    elif vol_trend == "FLAT":
        volume_pts = 15.0
    elif vol_trend == "INCREASING":
        volume_pts = 0.0
    else:
        volume_pts = 8.0

    # ── Component: OI choppiness (20 pts) ─────────────────────────────
    oi_dir = classify_oi_direction(oi_snapshots)
    if oi_dir == "CHOPPY":
        oi_pts = 20.0
    elif oi_dir == "UNKNOWN":
        oi_pts = 10.0
    else:
        oi_pts = 0.0

    score_total = regime_pts + range_pts + volume_pts + oi_pts

    # ── S/R — anchors only ────────────────────────────────────────────
    if symbol in ANCHOR_SYMBOLS and candles_4h:
        sr = compute_sr_levels(candles_4h)
    else:
        sr = {"support": None, "resistance": None,
              "support_dist_pct": None, "resistance_dist_pct": None}

    range_high    = rq["range_high"]    if rq else None
    range_low     = rq["range_low"]     if rq else None
    range_pct     = rq["range_pct"]     if rq else None
    range_dur     = rq["duration"]      if rq else 0
    ma_crossings  = rq["ma_crossings"]  if rq else 0

    return {
        "score":          round(score_total, 1),
        "components": {
            "regime_pts":  round(regime_pts, 1),
            "range_pts":   round(range_pts, 1),
            "volume_pts":  round(volume_pts, 1),
            "oi_pts":      round(oi_pts, 1),
        },
        "range_high":     range_high,
        "range_low":      range_low,
        "range_pct":      range_pct,
        "range_duration": range_dur,
        "ma_crossings":   ma_crossings,
        "vol_trend":      vol_trend,
        "oi_direction":   oi_dir,
        "adx":            round(adx, 2),
        "support":        sr["support"],
        "resistance":     sr["resistance"],
        "viable":         score_total >= VIABLE_THRESHOLD,
    }

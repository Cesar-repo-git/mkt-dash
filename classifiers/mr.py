"""
MR (Mean Reversion) setup scorer.

Scores MR Long and MR Short independently (0–100).
Both setups share identical asset conditions (ranging, flat/decreasing vol,
choppy OI); direction only matters for the entry trigger (SFP).

Score breakdown:
    Regime quality        30 pts  — low ADX, low Hurst (strong ranging signal)
    Range quality         25 pts  — clean, sustained sideways range
    Volume quality        25 pts  — flat or decreasing volume (confirms lack of trend)
    OI choppiness         20 pts  — no sustained OI direction

Output dict:
    {
        'setup':              'MR_LONG' | 'MR_SHORT',
        'score':              float 0–100,
        'components': { ... },
        'range_high':         float or None,
        'range_low':          float or None,
        'range_pct':          float or None,
        'range_duration':     int,
        'vol_trend':          str,
        'oi_direction':       str,
        'adx':                float,
        'hurst':              float or None,
        'support':            float or None,   # = range_low from 4h
        'resistance':         float or None,   # = range_high from 4h
        'viable':             bool,
    }
"""

from typing import Optional

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
    candles_4h:     list,
    regime:         dict,
    oi_snapshots:   list,
) -> dict:
    """
    Scores both MR Long and MR Short (conditions are identical).
    Returns one result dict labelled with the higher-viability direction,
    or MR_LONG by default when equal.
    Both directions are always computed and stored as 'mr_long_score' /
    'mr_short_score' for the dashboard to show.
    """
    result = _score_mr(symbol, candles_1m, candles_4h, regime, oi_snapshots)
    return result


def _score_mr(
    symbol:       str,
    candles_1m:   list,
    candles_4h:   list,
    regime:       dict,
    oi_snapshots: list,
) -> dict:

    adx   = regime.get("adx")   or 0.0
    hurst = regime.get("hurst")

    # ── Component: Regime quality (30 pts) ────────────────────────────
    # Low ADX → more ranging; scores 0–30 as ADX falls from 20 toward 0
    adx_pts = min(30.0, max(0.0, (20.0 - adx) / 20.0 * 30.0))
    hurst_pts = 0.0
    if hurst is not None:
        # H < 0.50 → mean-reverting; max 0 pts at H=0.55, max pts at H=0.30
        hurst_pts = min(15.0, max(0.0, (0.55 - hurst) / 0.25 * 15.0))
        # Split the 30 pts: 15 from ADX, 15 from Hurst when both available
        adx_pts   = min(15.0, max(0.0, (20.0 - adx) / 20.0 * 15.0))
    regime_pts = adx_pts + hurst_pts

    # ── Component: Range quality (25 pts) ─────────────────────────────
    range_candles = candles_4h if len(candles_4h) >= 20 else candles_1m
    rq = compute_range_quality(range_candles)
    range_pts = (rq["score"] / 100.0 * 25.0) if rq else 0.0

    # ── Component: Volume quality (25 pts) ────────────────────────────
    vol_slope = compute_vol_slope(candles_1m)
    vol_trend = classify_vol_trend(vol_slope)
    if vol_trend == "DECREASING":
        volume_pts = 25.0
    elif vol_trend == "FLAT":
        volume_pts = 20.0
    elif vol_trend == "INCREASING":
        volume_pts = 0.0
    else:
        volume_pts = 10.0

    # ── Component: OI choppiness (20 pts) ─────────────────────────────
    oi_dir = classify_oi_direction(oi_snapshots)
    if oi_dir == "CHOPPY":
        oi_pts = 20.0
    elif oi_dir == "UNKNOWN":
        oi_pts = 10.0
    else:
        oi_pts = 0.0   # clear increasing/decreasing OI → not ranging

    score_total = regime_pts + range_pts + volume_pts + oi_pts

    # ── S/R (= range bounds from 4h) ─────────────────────────────────
    sr = compute_sr_levels(candles_4h) if candles_4h else {
        "support": None, "resistance": None,
        "support_dist_pct": None, "resistance_dist_pct": None,
    }

    # Range info from quality computation
    range_high = rq["range_high"] if rq else None
    range_low  = rq["range_low"]  if rq else None
    range_pct  = rq["range_pct"]  if rq else None
    range_dur  = rq["duration"]   if rq else 0

    viable = score_total >= VIABLE_THRESHOLD

    base = {
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
        "vol_trend":      vol_trend,
        "oi_direction":   oi_dir,
        "adx":            round(adx, 2),
        "hurst":          round(hurst, 3) if hurst is not None else None,
        "support":        sr["support"],
        "resistance":     sr["resistance"],
        "viable":         viable,
    }

    # Return two sub-dicts for Long and Short (same score, setup label differs)
    long_result  = {**base, "setup": "MR_LONG"}
    short_result = {**base, "setup": "MR_SHORT"}
    return long_result, short_result

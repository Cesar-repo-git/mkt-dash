"""
MO (Momentum) setup scorer.

Scores MO Long and MO Short independently (0–100).
Both use the same component weights; direction determines which
components are satisfied.

Score breakdown:
    ADX strength          25 pts  — how strong is the trend
    Staircase quality     25 pts  — HH+HL (long) or LH+LL (short) cleanliness
    Volume trend          20 pts  — gradual volume ramp-up
    VWAP alignment        15 pts  — price above (long) / below (short) VWAP
    OI direction          15 pts  — increasing OI confirms participation

Output dict (one per direction):
    {
        'setup':              'MO_LONG' | 'MO_SHORT',
        'score':              float 0–100,
        'components': {
            'adx_pts':        float,
            'staircase_pts':  float,
            'volume_pts':     float,
            'vwap_pts':       float,
            'oi_pts':         float,
        },
        'adx':                float,
        'staircase_score':    float,
        'vol_trend':          str,
        'vol_slope':          float,
        'vwap':               float,
        'price':              float,
        'vwap_pct':           float,   # price distance from VWAP as %
        'oi_direction':       str,
        'trend_duration':     int,     # consecutive candles in direction
        'support':            float or None,
        'resistance':         float or None,
        'support_dist_pct':   float or None,
        'resistance_dist_pct':float or None,
        'viable':             bool,    # score >= minimum threshold to display
    }
"""

from typing import Optional

from classifiers.indicators import (
    compute_staircase_score,
    compute_vol_slope,
    classify_vol_trend,
    classify_oi_direction,
    compute_trend_duration,
    compute_sr_levels,
)

VIABLE_THRESHOLD = 55.0   # minimum score to surface as an active setup


def score(
    symbol:         str,
    candles_1m:     list,
    candles_4h:     list,
    regime:         dict,
    latest_vwap:    Optional[float],
    latest_price:   Optional[float],
    oi_snapshots:   list,
) -> dict:
    """
    Scores both MO Long and MO Short; returns the higher-scoring direction.
    If neither is viable, returns the Long result with viable=False.
    """
    long_result  = _score_direction("MO_LONG",  symbol, candles_1m, candles_4h,
                                    regime, latest_vwap, latest_price, oi_snapshots)
    short_result = _score_direction("MO_SHORT", symbol, candles_1m, candles_4h,
                                    regime, latest_vwap, latest_price, oi_snapshots)

    if long_result["score"] >= short_result["score"]:
        return long_result
    return short_result


def _score_direction(
    setup:          str,
    symbol:         str,
    candles_1m:     list,
    candles_4h:     list,
    regime:         dict,
    latest_vwap:    Optional[float],
    latest_price:   Optional[float],
    oi_snapshots:   list,
) -> dict:

    direction = "LONG" if setup == "MO_LONG" else "SHORT"
    adx       = regime.get("adx") or 0.0

    # ── Component: ADX strength (25 pts) ──────────────────────────────
    # Scales 25→60 ADX → 0→25 pts
    adx_pts = min(25.0, max(0.0, (adx - 25.0) / (60.0 - 25.0) * 25.0))

    # ── Component: Staircase quality (25 pts) ─────────────────────────
    # Use 4h candles for structural staircase; fall back to 1m if short
    stair_candles = candles_4h if len(candles_4h) >= 20 else candles_1m
    stair_raw     = compute_staircase_score(stair_candles, direction)
    staircase_pts = (stair_raw / 100.0 * 25.0) if stair_raw is not None else 0.0

    # ── Component: Volume trend (20 pts) ──────────────────────────────
    vol_slope = compute_vol_slope(candles_1m)
    vol_trend = classify_vol_trend(vol_slope)
    if vol_trend == "INCREASING":
        volume_pts = 20.0
    elif vol_trend == "FLAT":
        volume_pts = 5.0
    else:
        volume_pts = 0.0

    # ── Component: VWAP alignment (15 pts) ────────────────────────────
    price    = latest_price or (candles_1m[-1]["close"] if candles_1m else None)
    vwap     = latest_vwap  or (candles_1m[-1].get("vwap") if candles_1m else None)
    vwap_pts = 0.0
    vwap_pct = None

    if price and vwap and vwap > 0:
        vwap_pct = (price - vwap) / vwap * 100
        if direction == "LONG":
            if vwap_pct > 0:
                # Full 15 pts if clearly above; tapers off near VWAP
                vwap_pts = min(15.0, vwap_pct * 3.0)
        else:  # SHORT
            if vwap_pct < 0:
                vwap_pts = min(15.0, abs(vwap_pct) * 3.0)

    # ── Component: OI direction (15 pts) ──────────────────────────────
    oi_dir    = classify_oi_direction(oi_snapshots)
    oi_pts    = 15.0 if oi_dir == "INCREASING" else (5.0 if oi_dir == "CHOPPY" else 0.0)

    # ── Total score ───────────────────────────────────────────────────
    score_total = adx_pts + staircase_pts + volume_pts + vwap_pts + oi_pts

    # ── S/R levels from 4h ────────────────────────────────────────────
    sr = compute_sr_levels(candles_4h) if candles_4h else {
        "support": None, "resistance": None,
        "support_dist_pct": None, "resistance_dist_pct": None,
    }

    # ── Trend duration (bonus context, not in score) ──────────────────
    trend_dur = compute_trend_duration(candles_1m[-30:] if len(candles_1m) > 30 else candles_1m,
                                       direction)

    return {
        "setup":               setup,
        "score":               round(score_total, 1),
        "components": {
            "adx_pts":         round(adx_pts, 1),
            "staircase_pts":   round(staircase_pts, 1),
            "volume_pts":      round(volume_pts, 1),
            "vwap_pts":        round(vwap_pts, 1),
            "oi_pts":          round(oi_pts, 1),
        },
        "adx":                 round(adx, 2),
        "staircase_score":     round(stair_raw, 1) if stair_raw is not None else None,
        "vol_trend":           vol_trend,
        "vol_slope":           round(vol_slope, 5) if vol_slope is not None else None,
        "vwap":                round(vwap, 4) if vwap else None,
        "price":               round(price, 4) if price else None,
        "vwap_pct":            round(vwap_pct, 3) if vwap_pct is not None else None,
        "oi_direction":        oi_dir,
        "trend_duration":      trend_dur,
        "support":             sr["support"],
        "resistance":          sr["resistance"],
        "support_dist_pct":    sr["support_dist_pct"],
        "resistance_dist_pct": sr["resistance_dist_pct"],
        "viable":              score_total >= VIABLE_THRESHOLD,
    }

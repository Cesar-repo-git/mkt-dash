"""
MO (Momentum) setup scorer.

Scores MO Long and MO Short independently (0–100).

Score breakdown:
    ADX strength          25 pts  — how strong is the trend
    Staircase quality     25 pts  — HH+HL / LH+LL with MA(30) discipline (≥1h window)
    Volume trend          20 pts  — gradual volume ramp-up (MA30/MA60 slope)
    VWAP band alignment   15 pts  — band-aware: above upper1σ / below lower1σ for full pts
    OI direction          15 pts  — increasing OI confirms participation

S/R levels: only computed for BTC/ETH anchors.

Output dict:
    {
        'setup':              'MO_LONG' | 'MO_SHORT',
        'score':              float 0–100,
        'components':         dict,
        'adx':                float,
        'staircase_score':    float,
        'vol_trend':          str,
        'vol_slope':          float,
        'vol_ma30':           float or None,
        'vol_ma60':           float or None,
        'vwap':               float or None,
        'vwap_upper1':        float or None,
        'vwap_lower1':        float or None,
        'price':              float,
        'vwap_pct':           float or None,
        'oi_direction':       str,
        'trend_duration':     int,
        'support':            float or None,   # anchors only
        'resistance':         float or None,   # anchors only
        'support_dist_pct':   float or None,
        'resistance_dist_pct':float or None,
        'viable':             bool,
    }
"""

from typing import Optional

from config import ANCHOR_SYMBOLS
from classifiers.indicators import (
    compute_staircase_score,
    compute_vol_slope,
    compute_vol_ma,
    classify_vol_trend,
    classify_oi_direction,
    compute_trend_duration,
    compute_sr_levels,
)

VIABLE_THRESHOLD = 55.0


def score(
    symbol:         str,
    candles_1m:     list,
    candles_1h:     list,
    candles_4h:     list,
    regime:         dict,
    vwap_bands:     Optional[dict],   # from store.get_vwap_bands()
    latest_price:   Optional[float],
    oi_snapshots:   list,
) -> dict:
    """Scores both MO Long and MO Short; returns the higher-scoring direction."""
    long_result  = _score_direction("MO_LONG",  symbol, candles_1m, candles_1h, candles_4h,
                                    regime, vwap_bands, latest_price, oi_snapshots)
    short_result = _score_direction("MO_SHORT", symbol, candles_1m, candles_1h, candles_4h,
                                    regime, vwap_bands, latest_price, oi_snapshots)

    if long_result["score"] >= short_result["score"]:
        return long_result
    return short_result


def _score_direction(
    setup:          str,
    symbol:         str,
    candles_1m:     list,
    candles_1h:     list,
    candles_4h:     list,
    regime:         dict,
    vwap_bands:     Optional[dict],
    latest_price:   Optional[float],
    oi_snapshots:   list,
) -> dict:

    direction = "LONG" if setup == "MO_LONG" else "SHORT"
    adx       = regime.get("adx") or 0.0

    # ── Component: ADX strength (25 pts) ──────────────────────────────
    adx_pts = min(25.0, max(0.0, (adx - 25.0) / (60.0 - 25.0) * 25.0))

    # ── Component: Staircase quality (25 pts) — uses 1h candles ───────
    # Prefer 1h candles (≥60 bars = 1h window); fall back to 1m if needed
    stair_candles = candles_1h if len(candles_1h) >= 60 else candles_1m
    stair_raw     = compute_staircase_score(stair_candles, direction)
    staircase_pts = (stair_raw / 100.0 * 25.0) if stair_raw is not None else 0.0

    # ── Component: Volume trend (20 pts) — MA30/MA60 slope ────────────
    vol_slope = compute_vol_slope(candles_1m)
    vol_trend = classify_vol_trend(vol_slope)
    vol_ma30  = compute_vol_ma(candles_1m, 30)
    vol_ma60  = compute_vol_ma(candles_1m, 60)
    if vol_trend == "INCREASING":
        volume_pts = 20.0
    elif vol_trend == "FLAT":
        volume_pts = 5.0
    else:
        volume_pts = 0.0

    # ── Component: VWAP band alignment (15 pts) ───────────────────────
    price    = latest_price or (candles_1m[-1]["close"] if candles_1m else None)
    vwap     = vwap_bands.get("vwap") if vwap_bands else None
    upper1   = vwap_bands.get("upper1") if vwap_bands else None
    lower1   = vwap_bands.get("lower1") if vwap_bands else None
    vwap_pts = 0.0
    vwap_pct = None

    if price and vwap and vwap > 0:
        vwap_pct = (price - vwap) / vwap * 100
        if direction == "LONG":
            if upper1 and price >= upper1:
                vwap_pts = 15.0   # above upper 1σ band
            elif price > vwap:
                vwap_pts = 8.0    # above VWAP but below upper1
        else:  # SHORT
            if lower1 and price <= lower1:
                vwap_pts = 15.0   # below lower 1σ band
            elif price < vwap:
                vwap_pts = 8.0    # below VWAP but above lower1

    # ── Component: OI direction (15 pts) ──────────────────────────────
    oi_dir = classify_oi_direction(oi_snapshots)
    oi_pts = 15.0 if oi_dir == "INCREASING" else (5.0 if oi_dir == "CHOPPY" else 0.0)

    score_total = adx_pts + staircase_pts + volume_pts + vwap_pts + oi_pts

    # ── S/R — anchors only ────────────────────────────────────────────
    if symbol in ANCHOR_SYMBOLS and candles_4h:
        sr = compute_sr_levels(candles_4h)
    else:
        sr = {"support": None, "resistance": None,
              "support_dist_pct": None, "resistance_dist_pct": None}

    trend_dur = compute_trend_duration(
        candles_1m[-30:] if len(candles_1m) > 30 else candles_1m, direction
    )

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
        "vol_ma30":            round(vol_ma30, 2) if vol_ma30 is not None else None,
        "vol_ma60":            round(vol_ma60, 2) if vol_ma60 is not None else None,
        "vwap":                round(vwap, 4) if vwap else None,
        "vwap_upper1":         round(upper1, 4) if upper1 else None,
        "vwap_lower1":         round(lower1, 4) if lower1 else None,
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

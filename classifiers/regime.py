"""
Regime classifier.

Uses 4h candles to determine whether a symbol is in a TRENDING or RANGING
environment — the primary gate for MO vs MR setups.

ADX-only: Hurst Exponent removed per user spec.

Output:
    {
        'regime':     'TRENDING' | 'RANGING' | 'UNCLEAR',
        'adx':        float,
        'confidence': float 0–100,
    }
"""

from typing import Optional
from classifiers.indicators import compute_adx

ADX_TREND_MIN = 25.0
ADX_RANGE_MAX = 20.0


def classify(candles_4h: list) -> dict:
    default = {"regime": "UNCLEAR", "adx": None, "confidence": 0.0}

    if not candles_4h or len(candles_4h) < 20:
        return default

    adx = compute_adx(candles_4h)
    if adx is None:
        return default

    if adx >= ADX_TREND_MIN:
        regime     = "TRENDING"
        confidence = min(100.0, (adx - ADX_TREND_MIN) / (60.0 - ADX_TREND_MIN) * 100)
    elif adx <= ADX_RANGE_MAX:
        regime     = "RANGING"
        confidence = min(100.0, (ADX_RANGE_MAX - adx) / ADX_RANGE_MAX * 100)
    else:
        regime     = "UNCLEAR"
        confidence = 0.0

    return {
        "regime":     regime,
        "adx":        round(adx, 2),
        "confidence": round(confidence, 1),
    }

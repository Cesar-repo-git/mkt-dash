"""
Regime classifier.

Uses 4h candles to determine whether a symbol is currently in a
TRENDING or RANGING environment — the primary gate for MO vs MR setups.

Output:
    {
        'regime':     'TRENDING' | 'RANGING' | 'UNCLEAR',
        'adx':        float,
        'hurst':      float,
        'confidence': float 0–100,   # how clearly the regime is established
    }
"""

from typing import Optional
from classifiers.indicators import compute_adx, compute_hurst

# Thresholds
ADX_TREND_MIN   = 25.0   # above → trending
ADX_RANGE_MAX   = 20.0   # below → ranging
HURST_TREND_MIN = 0.55
HURST_RANGE_MAX = 0.50


def classify(candles_4h: list) -> dict:
    """
    Primary regime gate.
    Requires ≥ 28 candles (4h) for ADX, ≥ 32 for Hurst.
    Falls back gracefully if data is short.
    """
    default = {
        "regime":     "UNCLEAR",
        "adx":        None,
        "hurst":      None,
        "confidence": 0.0,
    }

    if not candles_4h or len(candles_4h) < 20:
        return default

    adx   = compute_adx(candles_4h)
    hurst = compute_hurst(candles_4h)

    # Fallback: if Hurst needs more data, use ADX alone
    if adx is None:
        return default

    trending_signals = 0
    ranging_signals  = 0
    total_signals    = 0

    # ADX vote
    if adx >= ADX_TREND_MIN:
        trending_signals += 1
    elif adx <= ADX_RANGE_MAX:
        ranging_signals += 1
    total_signals += 1

    # Hurst vote (optional — only if available)
    if hurst is not None:
        if hurst >= HURST_TREND_MIN:
            trending_signals += 1
        elif hurst <= HURST_RANGE_MAX:
            ranging_signals += 1
        total_signals += 1

    if total_signals == 0:
        return {**default, "adx": adx, "hurst": hurst}

    trend_frac = trending_signals / total_signals
    range_frac = ranging_signals  / total_signals

    if trend_frac >= 0.5:
        regime     = "TRENDING"
        confidence = _regime_confidence(adx, hurst, "TRENDING")
    elif range_frac >= 0.5:
        regime     = "RANGING"
        confidence = _regime_confidence(adx, hurst, "RANGING")
    else:
        regime     = "UNCLEAR"
        confidence = 0.0

    return {
        "regime":     regime,
        "adx":        round(adx, 2),
        "hurst":      round(hurst, 3) if hurst is not None else None,
        "confidence": round(confidence, 1),
    }


def _regime_confidence(adx: float, hurst: Optional[float], regime: str) -> float:
    """
    Confidence score 0–100 reflecting how clearly the regime is established.
    """
    if regime == "TRENDING":
        # ADX: scales from 25→100 (min threshold → strong trend)
        adx_conf = min(100.0, max(0.0, (adx - ADX_TREND_MIN) / (60.0 - ADX_TREND_MIN) * 100))
        if hurst is not None:
            hurst_conf = min(100.0, max(0.0, (hurst - HURST_TREND_MIN) / (1.0 - HURST_TREND_MIN) * 100))
            return adx_conf * 0.6 + hurst_conf * 0.4
        return adx_conf

    else:  # RANGING
        adx_conf = min(100.0, max(0.0, (ADX_RANGE_MAX - adx) / ADX_RANGE_MAX * 100))
        if hurst is not None:
            hurst_conf = min(100.0, max(0.0, (HURST_RANGE_MAX - hurst) / HURST_RANGE_MAX * 100))
            return adx_conf * 0.6 + hurst_conf * 0.4
        return adx_conf

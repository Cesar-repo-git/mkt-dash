"""
Pure technical indicator functions.

All inputs are lists of candle dicts (from store) or plain price arrays.
No side effects — each function returns a value or None if data is insufficient.

Candle dict schema expected:
    { 'open', 'high', 'low', 'close', 'volume_usd', 'trades', 'vwap', 'time' }
"""

import math
from typing import Optional

import numpy as np


# ── Minimum data guards ───────────────────────────────────────────────────

def _closes(candles: list) -> np.ndarray:
    return np.array([c["close"] for c in candles], dtype=float)

def _highs(candles: list) -> np.ndarray:
    return np.array([c["high"] for c in candles], dtype=float)

def _lows(candles: list) -> np.ndarray:
    return np.array([c["low"] for c in candles], dtype=float)

def _volumes(candles: list) -> np.ndarray:
    return np.array([c["volume_usd"] for c in candles], dtype=float)

def _trades(candles: list) -> np.ndarray:
    return np.array([c.get("trades", 0) for c in candles], dtype=float)


# ── ADX (Wilder smoothing) ────────────────────────────────────────────────

def compute_adx(candles: list, period: int = 14) -> Optional[float]:
    """
    Average Directional Index via Wilder smoothing. Returns 0–100.
    > 25 → trending; < 20 → ranging.
    Requires at least 2×period + 1 candles.
    """
    if len(candles) < period * 2 + 1:
        return None

    highs  = _highs(candles)
    lows   = _lows(candles)
    closes = _closes(candles)
    n = len(candles)

    tr_raw  = np.zeros(n)
    pdm_raw = np.zeros(n)
    ndm_raw = np.zeros(n)

    for i in range(1, n):
        tr_raw[i]  = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i - 1]),
                         abs(lows[i]  - closes[i - 1]))
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm_raw[i] = up   if (up > down and up > 0)   else 0.0
        ndm_raw[i] = down if (down > up and down > 0) else 0.0

    atr  = float(tr_raw[1:period + 1].sum())
    apdm = float(pdm_raw[1:period + 1].sum())
    andm = float(ndm_raw[1:period + 1].sum())

    if atr <= 0:
        return None
    pdi0 = 100 * apdm / atr
    ndi0 = 100 * andm / atr
    den0 = pdi0 + ndi0
    dx_vals = [100 * abs(pdi0 - ndi0) / den0 if den0 > 0 else 0.0]

    for i in range(period + 1, n):
        atr  = atr  * (period - 1) / period + tr_raw[i]
        apdm = apdm * (period - 1) / period + pdm_raw[i]
        andm = andm * (period - 1) / period + ndm_raw[i]

        if atr <= 0:
            dx_vals.append(0.0)
            continue
        pdi = 100 * apdm / atr
        ndi = 100 * andm / atr
        den = pdi + ndi
        dx_vals.append(100 * abs(pdi - ndi) / den if den > 0 else 0.0)

    if len(dx_vals) < period:
        return None

    adx = sum(dx_vals[:period])
    for dx in dx_vals[period:]:
        adx = adx * (period - 1) / period + dx

    return float(adx / period)


# ── Simple MA ────────────────────────────────────────────────────────────

def compute_ma(candles: list, period: int) -> Optional[float]:
    """Latest simple moving average of closes."""
    if len(candles) < period:
        return None
    closes = _closes(candles)
    return float(closes[-period:].mean())


def compute_ma_series(candles: list, period: int) -> Optional[np.ndarray]:
    """Full MA series aligned to candles (NaN for first period-1 values)."""
    if len(candles) < period:
        return None
    closes = _closes(candles)
    result = np.full(len(closes), np.nan)
    for i in range(period - 1, len(closes)):
        result[i] = closes[i - period + 1 : i + 1].mean()
    return result


# ── SMMA (Smoothed Moving Average, aka Wilder MA) ─────────────────────────

def compute_smma(candles: list, period: int) -> Optional[float]:
    """Latest SMMA value. Uses Wilder smoothing: alpha = 1/period."""
    if len(candles) < period:
        return None
    closes = _closes(candles)
    smma = closes[:period].mean()
    for price in closes[period:]:
        smma = (smma * (period - 1) + price) / period
    return float(smma)


def compute_smma_series(candles: list, period: int) -> Optional[np.ndarray]:
    """Full SMMA series aligned to candles (NaN for first period-1 values)."""
    if len(candles) < period:
        return None
    closes = _closes(candles)
    result = np.full(len(closes), np.nan)
    smma = closes[:period].mean()
    result[period - 1] = smma
    for i in range(period, len(closes)):
        smma = (smma * (period - 1) + closes[i]) / period
        result[i] = smma
    return result


# ── Volume MAs ───────────────────────────────────────────────────────────

def compute_vol_ma(candles: list, period: int) -> Optional[float]:
    """Latest MA of volume_usd."""
    if len(candles) < period:
        return None
    vols = _volumes(candles)
    return float(vols[-period:].mean())


# ── Swing highs / lows ────────────────────────────────────────────────────

def find_swing_highs(candles: list, lookback: int = 3) -> list[dict]:
    highs = _highs(candles)
    swings = []
    for i in range(lookback, len(candles) - lookback):
        window = highs[i - lookback: i + lookback + 1]
        if highs[i] == window.max() and list(window).count(highs[i]) == 1:
            swings.append({
                "index": i,
                "price": float(highs[i]),
                "time":  candles[i]["time"],
            })
    return swings


def find_swing_lows(candles: list, lookback: int = 3) -> list[dict]:
    lows = _lows(candles)
    swings = []
    for i in range(lookback, len(candles) - lookback):
        window = lows[i - lookback: i + lookback + 1]
        if lows[i] == window.min() and list(window).count(lows[i]) == 1:
            swings.append({
                "index": i,
                "price": float(lows[i]),
                "time":  candles[i]["time"],
            })
    return swings


# ── Support / resistance levels ───────────────────────────────────────────

def compute_sr_levels(candles: list, n_swings: int = 5) -> dict:
    """
    Derives support and resistance from recent swing lows/highs on 4h candles.
    Only used for BTC/ETH anchors.
    """
    if len(candles) < 10:
        return {"support": None, "resistance": None,
                "support_dist_pct": None, "resistance_dist_pct": None}

    current_price = candles[-1]["close"]
    swing_highs = find_swing_highs(candles[-50:] if len(candles) > 50 else candles)
    swing_lows  = find_swing_lows(candles[-50:]  if len(candles) > 50 else candles)

    above = [s["price"] for s in swing_highs if s["price"] > current_price]
    resistance = min(above) if above else None

    below = [s["price"] for s in swing_lows if s["price"] < current_price]
    support = max(below) if below else None

    def dist_pct(level):
        if level is None:
            return None
        return round(abs(current_price - level) / current_price * 100, 2)

    return {
        "support":             support,
        "resistance":          resistance,
        "support_dist_pct":    dist_pct(support),
        "resistance_dist_pct": dist_pct(resistance),
    }


# ── Previous day H/L ─────────────────────────────────────────────────────

def compute_prev_day_levels(candles_1d: list, current_price: float) -> dict:
    """
    Returns previous day high/low and distance % from current price.
    Requires ≥ 2 daily candles.
    """
    empty = {"prev_day_high": None, "prev_day_low": None,
             "prev_day_high_dist_pct": None, "prev_day_low_dist_pct": None}
    if len(candles_1d) < 2:
        return empty
    prev = candles_1d[-2]   # last closed daily candle

    def dist(level):
        return round((current_price - level) / level * 100, 2) if level else None

    return {
        "prev_day_high":          float(prev["high"]),
        "prev_day_low":           float(prev["low"]),
        "prev_day_high_dist_pct": dist(prev["high"]),
        "prev_day_low_dist_pct":  dist(prev["low"]),
    }


# ── Volume slope ──────────────────────────────────────────────────────────

def compute_vol_slope(candles: list, period: int = 20) -> Optional[float]:
    """
    Linear regression slope of volume MA(period) over the last 2×period candles.
    Returns slope normalised by mean volume: positive = increasing, negative = decreasing.
    """
    if len(candles) < period * 2:
        return None

    vols = _volumes(candles[-(period * 2):])
    ma   = np.convolve(vols, np.ones(period) / period, mode="valid")

    if len(ma) < 5:
        return None

    x     = np.arange(len(ma), dtype=float)
    slope = np.polyfit(x, ma, 1)[0]
    mean_vol = ma.mean()
    return float(slope / mean_vol) if mean_vol > 0 else 0.0


def classify_vol_trend(slope: Optional[float]) -> str:
    """INCREASING | FLAT | DECREASING"""
    if slope is None:
        return "UNKNOWN"
    if slope >  0.005:
        return "INCREASING"
    if slope < -0.005:
        return "DECREASING"
    return "FLAT"


# ── Staircase quality (MO) ────────────────────────────────────────────────

def compute_staircase_score(candles: list, direction: str) -> Optional[float]:
    """
    Measures how clean a trending staircase is over a ≥60 candle window
    (minimum 1h of 1m bars, ideally 2h = 120 candles).

    Scoring components:
    - MA(30) discipline: % of candles where close respects MA(30)
      (above for LONG, below for SHORT) — less touches = better
    - HH+HL (LONG) or LH+LL (SHORT) swing structure
    - Candle consistency: % of bars moving in the right direction

    Returns 0–100. Requires ≥ 60 candles.
    """
    if len(candles) < 60:
        return None

    # Use a 2h window (120 bars) if available, else 1h (60 bars)
    window = candles[-120:] if len(candles) >= 120 else candles[-60:]

    closes = _closes(window)

    # ── MA(30) discipline ─────────────────────────────────────────────
    ma30 = compute_ma_series(window, 30)
    if ma30 is not None:
        valid = ~np.isnan(ma30)
        if valid.sum() > 0:
            if direction == "LONG":
                # Respecting MA(30) = close above MA; touches = crosses below
                respects = np.sum(closes[valid] >= ma30[valid])
            else:
                respects = np.sum(closes[valid] <= ma30[valid])
            ma_score = respects / valid.sum() * 100
        else:
            ma_score = 50.0
    else:
        ma_score = 50.0

    # ── Candle consistency ────────────────────────────────────────────
    if direction == "LONG":
        consistent = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    else:
        consistent = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    candle_score = consistent / (len(closes) - 1) * 100

    # ── Swing-point structure ─────────────────────────────────────────
    highs_list = find_swing_highs(window, lookback=3)
    lows_list  = find_swing_lows(window, lookback=3)

    swing_score = 0.0
    if len(highs_list) >= 2 and len(lows_list) >= 2:
        def pct_higher(points):
            wins = sum(1 for i in range(1, len(points)) if points[i]["price"] > points[i - 1]["price"])
            return wins / (len(points) - 1)

        def pct_lower(points):
            wins = sum(1 for i in range(1, len(points)) if points[i]["price"] < points[i - 1]["price"])
            return wins / (len(points) - 1)

        if direction == "LONG":
            swing_score = (pct_higher(highs_list) * 0.5 + pct_higher(lows_list) * 0.5) * 100
        else:
            swing_score = (pct_lower(highs_list) * 0.5 + pct_lower(lows_list) * 0.5) * 100

        # Blend all three components
        return round(ma_score * 0.40 + swing_score * 0.35 + candle_score * 0.25, 1)

    # No swing points — weight MA discipline and candle consistency
    return round(ma_score * 0.55 + candle_score * 0.45, 1)


# ── Trend duration ────────────────────────────────────────────────────────

def compute_trend_duration(candles: list, direction: str) -> int:
    """
    Counts how many consecutive closed candles have been moving in the given
    direction (close > open for LONG; close < open for SHORT).
    """
    count = 0
    for c in reversed(candles):
        if direction == "LONG"  and c["close"] > c["open"]:
            count += 1
        elif direction == "SHORT" and c["close"] < c["open"]:
            count += 1
        else:
            break
    return count


# ── OI direction ─────────────────────────────────────────────────────────

def classify_oi_direction(oi_snapshots: list) -> str:
    """
    Given a list of OI dicts { 'oi_usd', 'change_pct', ... }, classifies
    the OI trend over the last N readings.
    Returns: 'INCREASING' | 'DECREASING' | 'CHOPPY' | 'UNKNOWN'
    """
    if len(oi_snapshots) < 3:
        return "UNKNOWN"

    recent = oi_snapshots[-6:]
    changes = [
        s["change_pct"] for s in recent
        if s.get("change_pct") is not None
    ]

    if len(changes) < 3:
        return "UNKNOWN"

    pos = sum(1 for c in changes if c > 0)
    neg = sum(1 for c in changes if c < 0)
    total = len(changes)

    if pos / total >= 0.67:
        return "INCREASING"
    if neg / total >= 0.67:
        return "DECREASING"
    return "CHOPPY"


# ── Range quality (MR) ────────────────────────────────────────────────────

def compute_range_quality(candles: list) -> Optional[dict]:
    """
    Assesses how well-defined a sideways range is for MR setups.

    Requirements:
    - Minimum 60 candles (1h window); uses up to 120 (2h)
    - Range width ≥ 5% (hard minimum for MR viability)
    - MA(30) × SMMA(120) crossings: more crossings = choppier = better for MR
    - % of candles within the range

    Returns:
        {
            'score':        float 0–100,
            'range_high':   float,
            'range_low':    float,
            'range_pct':    float,
            'duration':     int,
            'ma_crossings': int,    # MA(30) × SMMA(120) cross count
        }
    or None if insufficient data or range too narrow.
    """
    if len(candles) < 60:
        return None

    window = candles[-120:] if len(candles) >= 120 else candles[-60:]

    closes = _closes(window)
    highs  = _highs(window)
    lows   = _lows(window)

    range_high = float(highs.max())
    range_low  = float(lows.min())
    mid        = (range_high + range_low) / 2.0
    range_pct  = (range_high - range_low) / mid * 100

    # Hard gate: MR requires meaningful range
    if range_pct < 5.0:
        return None

    mean  = closes.mean()
    std   = closes.std()
    within = np.sum(np.abs(closes - mean) <= 1.5 * std) / len(closes) * 100

    # Penalise extremely wide ranges (> 25%)
    width_score = 100.0
    if range_pct > 25.0:
        width_score = max(0.0, 100.0 - (range_pct - 25.0) * 4)

    # MA(30) × SMMA(120) crossings — more = choppier = better for MR
    ma30   = compute_ma_series(window, 30)
    smma120 = compute_smma_series(window, min(120, len(window)))
    ma_crossings = 0
    if ma30 is not None and smma120 is not None:
        valid = ~(np.isnan(ma30) | np.isnan(smma120))
        diff = ma30[valid] - smma120[valid]
        if len(diff) > 1:
            signs = np.sign(diff)
            ma_crossings = int(np.sum(np.diff(signs) != 0))

    # More crossings → higher chop score (cap at 10 crossings = 100%)
    chop_score = min(100.0, ma_crossings / 10.0 * 100.0)

    score = within * 0.40 + width_score * 0.25 + chop_score * 0.35

    # Duration: consecutive candles within range
    duration = 0
    for c in reversed(window):
        if range_low <= c["close"] <= range_high:
            duration += 1
        else:
            break

    return {
        "score":        round(score, 1),
        "range_high":   range_high,
        "range_low":    range_low,
        "range_pct":    round(range_pct, 2),
        "duration":     duration,
        "ma_crossings": ma_crossings,
    }


# ── EMA ───────────────────────────────────────────────────────────────────

def compute_ema(candles: list, period: int) -> Optional[float]:
    """Returns latest EMA value."""
    if len(candles) < period:
        return None
    closes = _closes(candles)
    k = 2.0 / (period + 1)
    ema = closes[:period].mean()
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return float(ema)

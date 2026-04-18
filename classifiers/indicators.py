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

    Wilder's method: steady-state = input * period, so initial = sum(first period values).
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

    # Wilder initial = SUM (not mean) — steady state is val * period
    atr  = float(tr_raw[1:period + 1].sum())
    apdm = float(pdm_raw[1:period + 1].sum())
    andm = float(ndm_raw[1:period + 1].sum())

    # Seed first DX from initial smoothed values
    if atr <= 0:
        return None
    pdi0 = 100 * apdm / atr
    ndi0 = 100 * andm / atr
    den0 = pdi0 + ndi0
    dx_vals = [100 * abs(pdi0 - ndi0) / den0 if den0 > 0 else 0.0]

    # Roll forward: Wilder update = prev * (p-1)/p + new_raw
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

    # ADX = Wilder smooth of DX; initial = sum of first `period` DX values
    adx = sum(dx_vals[:period])
    for dx in dx_vals[period:]:
        adx = adx * (period - 1) / period + dx

    # Normalise: ADX steady state = dx * period → divide by period
    return float(adx / period)


# ── Hurst Exponent (variance-time method) ────────────────────────────────

def compute_hurst(candles: list) -> Optional[float]:
    """
    Estimates Hurst exponent via variance-time scaling of log-price changes.
    Var(log_price[t+τ] - log_price[t]) ~ τ^(2H)

    H > 0.55 → persistent / trending
    H < 0.45 → anti-persistent / mean-reverting
    H ≈ 0.50 → random walk

    Uses 4h candle closes; requires ≥ 32 candles.
    """
    if len(candles) < 32:
        return None

    log_prices = np.log(_closes(candles))

    lags     = [2, 4, 8, 16]
    log_lags = []
    log_vars = []

    for lag in lags:
        if lag >= len(log_prices):
            continue
        # τ-step log-price change (NOT difference of log-returns)
        changes = log_prices[lag:] - log_prices[:-lag]
        var = float(np.var(changes))
        if var > 0:
            log_lags.append(math.log(lag))
            log_vars.append(math.log(var))

    if len(log_lags) < 3:
        return None

    # slope of log(Var) vs log(τ) ≈ 2H
    slope = np.polyfit(log_lags, log_vars, 1)[0]
    return float(np.clip(slope / 2.0, 0.0, 1.0))


# ── Swing highs / lows ────────────────────────────────────────────────────

def find_swing_highs(candles: list, lookback: int = 3) -> list[dict]:
    """
    Returns list of swing highs: { 'index': int, 'price': float, 'time': datetime }
    A swing high has the highest 'high' within ±lookback candles.
    """
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
    """
    Returns list of swing lows: { 'index': int, 'price': float, 'time': datetime }
    """
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
    Returns:
        {
            'support':    float or None,   # nearest swing low below current price
            'resistance': float or None,   # nearest swing high above current price
            'support_dist_pct':    float,  # distance to support as % of price
            'resistance_dist_pct': float,
        }
    """
    if len(candles) < 10:
        return {"support": None, "resistance": None,
                "support_dist_pct": None, "resistance_dist_pct": None}

    current_price = candles[-1]["close"]
    swing_highs = find_swing_highs(candles[-50:] if len(candles) > 50 else candles)
    swing_lows  = find_swing_lows(candles[-50:]  if len(candles) > 50 else candles)

    # Nearest resistance above price
    above = [s["price"] for s in swing_highs if s["price"] > current_price]
    resistance = min(above) if above else None

    # Nearest support below price
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
    Measures how 'clean' a trending staircase is.
    direction: 'LONG'  → looks for HH + HL pattern
               'SHORT' → looks for LH + LL pattern

    Uses swing-point analysis where possible; falls back to a
    candle-by-candle consistency score for strong trends with few
    pullbacks (common in fast-moving crypto markets).

    Returns 0–100. Requires ≥ 20 candles.
    """
    if len(candles) < 20:
        return None

    recent = candles[-60:] if len(candles) > 60 else candles

    # ── Fallback: candle consistency score ───────────────────────────
    # % of candles moving in the right direction + higher/lower closes
    closes = _closes(recent)
    if direction == "LONG":
        consistent = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    else:
        consistent = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    candle_score = consistent / (len(closes) - 1) * 100

    # ── Swing-point score ─────────────────────────────────────────────
    highs_list = find_swing_highs(recent, lookback=3)
    lows_list  = find_swing_lows(recent, lookback=3)

    if len(highs_list) < 2 or len(lows_list) < 2:
        # Not enough swing points — return candle consistency only
        return round(candle_score, 1)

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

    # Blend: swing structure is more meaningful; candle consistency is a safe fallback
    return round(swing_score * 0.7 + candle_score * 0.3, 1)


# ── Trend duration ────────────────────────────────────────────────────────

def compute_trend_duration(candles: list, direction: str) -> int:
    """
    Counts how many consecutive closed candles have been moving in the given
    direction (close > open for LONG; close < open for SHORT).
    Useful as a quality enhancer for MO setups.
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

    recent = oi_snapshots[-6:]   # last 6 readings (≈ 30 min on 5min poll)
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
    Assesses how well-defined a sideways range is.
    Returns:
        {
            'score':        float 0–100,
            'range_high':   float,
            'range_low':    float,
            'range_pct':    float,   # width as % of mid-price
            'duration':     int,     # candles spent in range
        }
    or None if insufficient data.
    """
    if len(candles) < 20:
        return None

    recent = candles[-40:] if len(candles) > 40 else candles
    closes = _closes(recent)
    highs  = _highs(recent)
    lows   = _lows(recent)

    range_high = float(highs.max())
    range_low  = float(lows.min())
    mid        = (range_high + range_low) / 2.0
    range_pct  = (range_high - range_low) / mid * 100

    mean  = closes.mean()
    std   = closes.std()

    # Pct of candles within 1.5 std of mean → range cleanliness
    within = np.sum(np.abs(closes - mean) <= 1.5 * std) / len(closes) * 100

    # Penalise if range is too narrow (< 0.5%) or too wide (> 15%)
    width_score = 100.0
    if range_pct < 0.5:
        width_score = 20.0
    elif range_pct > 15.0:
        width_score = max(0.0, 100.0 - (range_pct - 15.0) * 5)

    score = within * 0.6 + width_score * 0.4

    # Duration: how many consecutive candles remained within the range
    duration = 0
    for c in reversed(recent):
        if range_low <= c["close"] <= range_high:
            duration += 1
        else:
            break

    return {
        "score":      round(score, 1),
        "range_high": range_high,
        "range_low":  range_low,
        "range_pct":  round(range_pct, 2),
        "duration":   duration,
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

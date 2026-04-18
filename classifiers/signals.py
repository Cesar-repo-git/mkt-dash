"""
Entry trigger detection.

These run on every new closed 1m candle (via WS callback) for fast response.

MO triggers:
  BREAKOUT_LONG  — latest candle close > resistance (prev close was below)
  BREAKOUT_SHORT — latest candle close < support   (prev close was above)

MR triggers:
  SFP_LONG       — candle wick sweeps below support  → close back above it
  SFP_SHORT      — candle wick sweeps above resistance → close back below it

Each detector returns None (no trigger) or a trigger dict:
    {
        'type':        str,       # BREAKOUT_LONG | BREAKOUT_SHORT | SFP_LONG | SFP_SHORT
        'price':       float,     # trigger candle close
        'level':       float,     # the S/R level that was breached
        'candle_time': datetime,
        'confirmed':   bool,      # True = trigger is fresh (latest candle)
    }
"""

from typing import Optional
from datetime import datetime


def detect_breakout(
    candles_1m: list,
    resistance: Optional[float],
    support:    Optional[float],
) -> Optional[dict]:
    """
    Detects a breakout on the last two closed 1m candles.
    Requires at least 2 candles and valid S/R levels.
    """
    if len(candles_1m) < 2 or (resistance is None and support is None):
        return None

    current  = candles_1m[-1]
    previous = candles_1m[-2]

    # MO Long breakout: prev closed below resistance, current closed above
    if resistance is not None:
        if previous["close"] < resistance <= current["close"]:
            return {
                "type":        "BREAKOUT_LONG",
                "price":       current["close"],
                "level":       resistance,
                "candle_time": current["time"],
                "confirmed":   True,
            }

    # MO Short breakout: prev closed above support, current closed below
    if support is not None:
        if previous["close"] > support >= current["close"]:
            return {
                "type":        "BREAKOUT_SHORT",
                "price":       current["close"],
                "level":       support,
                "candle_time": current["time"],
                "confirmed":   True,
            }

    return None


def detect_sfp(
    candles_1m: list,
    resistance: Optional[float],
    support:    Optional[float],
    wick_buffer_pct: float = 0.05,
) -> Optional[dict]:
    """
    Detects a Swing Failure Pattern on the most recent closed 1m candle.

    SFP Long  (MR):
      - Candle low  swept below support    (low < support)
      - Candle close recovered back above support
      - Creates a visible lower wick below the level

    SFP Short (MR):
      - Candle high swept above resistance  (high > resistance)
      - Candle close rejected back below resistance
      - Creates a visible upper wick above the level

    wick_buffer_pct: minimum wick extension beyond the level (as % of price)
                     to filter noise from tiny pin bars.
    """
    if len(candles_1m) < 1:
        return None

    c = candles_1m[-1]   # most recent closed candle
    price = c["close"]

    # SFP Long: wick sweeps below support, closes above
    if support is not None:
        min_wick = support * (1 - wick_buffer_pct / 100)
        if c["low"] <= min_wick and c["close"] > support:
            return {
                "type":        "SFP_LONG",
                "price":       price,
                "level":       support,
                "candle_time": c["time"],
                "confirmed":   True,
                "wick_depth":  round((support - c["low"]) / support * 100, 3),
            }

    # SFP Short: wick sweeps above resistance, closes below
    if resistance is not None:
        max_wick = resistance * (1 + wick_buffer_pct / 100)
        if c["high"] >= max_wick and c["close"] < resistance:
            return {
                "type":        "SFP_SHORT",
                "price":       price,
                "level":       resistance,
                "candle_time": c["time"],
                "confirmed":   True,
                "wick_depth":  round((c["high"] - resistance) / resistance * 100, 3),
            }

    return None


def trigger_label(trigger: Optional[dict]) -> str:
    """Human-readable trigger string for dashboard display."""
    if trigger is None:
        return ""
    t = trigger["type"]
    lvl = trigger["level"]
    price = trigger["price"]
    if t == "BREAKOUT_LONG":
        return f"BREAKOUT LONG — closed above {lvl:.4f}"
    if t == "BREAKOUT_SHORT":
        return f"BREAKOUT SHORT — closed below {lvl:.4f}"
    if t == "SFP_LONG":
        return f"SFP LONG — wick swept {lvl:.4f}, closed {price:.4f}"
    if t == "SFP_SHORT":
        return f"SFP SHORT — wick swept {lvl:.4f}, closed {price:.4f}"
    return t

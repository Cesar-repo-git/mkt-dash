"""
Thread-safe in-memory data store.

All pipeline workers write here. Stage 2 (strategy classifiers) and
Stage 3 (dashboard) read from here via the accessor methods.

Candle dict schema (1m):
    {
        'time':       datetime (UTC),
        'open':       float,
        'high':       float,
        'low':        float,
        'close':      float,
        'volume_usd': float,   # quote asset volume
        'trades':     int,     # number of trades in candle
        'vwap':       float,   # session VWAP up to this candle
    }

OI snapshot schema:
    {
        'time':       datetime (UTC),
        'oi_usd':     float,
        'change_pct': float,   # vs previous snapshot, None if first
    }

Top mover schema (stored in _top_movers list):
    {
        'symbol':       str,
        'price_change_pct': float,   # 24h % change
        'last_price':   float,
        'volume_usd':   float,       # 24h quote volume
    }
"""

import threading
from collections import deque, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import CANDLES_1M_MAXLEN, ANCHOR_SYMBOLS


class MarketStore:
    def __init__(self):
        self._lock = threading.RLock()

        # 1m closed candles: symbol → deque of candle dicts
        self._candles_1m: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=CANDLES_1M_MAXLEN)
        )

        # Multi-TF candles: symbol → list of candle dicts
        self._candles_1h: dict[str, list] = {}
        self._candles_4h: dict[str, list] = {}
        self._candles_1d: dict[str, list] = {}

        # Funding rates: symbol → float (latest)
        self._funding: dict[str, float] = {}

        # OI history: symbol → deque (last 300 snapshots ≈ 25h at 5m poll)
        self._oi: dict[str, deque] = defaultdict(lambda: deque(maxlen=300))

        # Top movers: list of dicts [{symbol, price_change_pct, last_price, volume_usd}]
        self._top_movers: list = []

        # Session VWAP accumulators: symbol → {pv_sum, vol_sum, pv2_sum, session_date}
        # pv2_sum = Σ(typical² × volume) for std dev band calculation
        self._vwap_acc: dict[str, dict] = {}

        # Active qualifying symbols
        self._active_symbols: set[str] = set(ANCHOR_SYMBOLS)

        # Macro snapshot
        self._macro: dict = {
            "fear_greed":    None,   # 0–100
            "fear_greed_label": None,
            "vix":           None,   # float
            "etf_flow_24h":  None,   # USD millions, net
            "fomc_next":     None,   # date string "YYYY-MM-DD"
            "fomc_days_away": None,  # int
        }

        # Timestamps of last successful update per data type
        self._last_updated: dict[str, Optional[datetime]] = defaultdict(lambda: None)

    # ── Active symbols ─────────────────────────────────────────────────────

    def add_symbol(self, symbol: str):
        with self._lock:
            self._active_symbols.add(symbol)

    def remove_symbol(self, symbol: str):
        with self._lock:
            self._active_symbols.discard(symbol)

    def get_active_symbols(self) -> set[str]:
        with self._lock:
            return set(self._active_symbols)

    # ── 1m candles ────────────────────────────────────────────────────────

    def push_candle_1m(self, symbol: str, candle: dict):
        """Append a closed 1m candle, computing session VWAP in-place."""
        with self._lock:
            candle["vwap"] = self._update_vwap(symbol, candle)
            self._candles_1m[symbol].append(candle)
            self._last_updated[f"candle_1m_{symbol}"] = datetime.now(timezone.utc)

    def get_candles_1m(self, symbol: str, limit: Optional[int] = None) -> list:
        with self._lock:
            candles = list(self._candles_1m[symbol])
            return candles[-limit:] if limit else candles

    def _update_vwap(self, symbol: str, candle: dict) -> float:
        """Compute cumulative session VWAP; resets at UTC midnight."""
        today = datetime.now(timezone.utc).date()
        acc = self._vwap_acc.get(symbol)
        if acc is None or acc["session_date"] != today:
            acc = {"pv_sum": 0.0, "pv2_sum": 0.0, "vol_sum": 0.0, "session_date": today}
            self._vwap_acc[symbol] = acc

        typical = (candle["high"] + candle["low"] + candle["close"]) / 3.0
        vol = candle["volume_usd"]
        acc["pv_sum"]  += typical * vol
        acc["pv2_sum"] += typical * typical * vol
        acc["vol_sum"] += vol

        return acc["pv_sum"] / acc["vol_sum"] if acc["vol_sum"] > 0 else candle["close"]

    def get_vwap_bands(self, symbol: str, multipliers: tuple = (1.0, 2.0)) -> Optional[dict]:
        """
        Return VWAP and upper/lower std dev bands for the current session.
        bands keys: vwap, upper1, lower1, upper2, lower2
        """
        with self._lock:
            acc = self._vwap_acc.get(symbol)
            if not acc or acc["vol_sum"] == 0:
                return None
            vwap = acc["pv_sum"] / acc["vol_sum"]
            variance = (acc["pv2_sum"] / acc["vol_sum"]) - (vwap ** 2)
            std = variance ** 0.5 if variance > 0 else 0.0
            result = {"vwap": vwap}
            for m in multipliers:
                label = str(int(m)) if m == int(m) else str(m)
                result[f"upper{label}"] = vwap + m * std
                result[f"lower{label}"] = vwap - m * std
            return result

    # ── 1h candles (all active symbols) ──────────────────────────────────

    def set_candles_1h(self, symbol: str, candles: list):
        with self._lock:
            self._candles_1h[symbol] = candles
            self._last_updated[f"candle_1h_{symbol}"] = datetime.now(timezone.utc)

    def get_candles_1h(self, symbol: str) -> list:
        with self._lock:
            return self._candles_1h.get(symbol, [])

    # ── Multi-TF candles (anchors / regime) ───────────────────────────────

    def set_candles_4h(self, symbol: str, candles: list):
        with self._lock:
            self._candles_4h[symbol] = candles
            self._last_updated[f"candle_4h_{symbol}"] = datetime.now(timezone.utc)

    def get_candles_4h(self, symbol: str) -> list:
        with self._lock:
            return self._candles_4h.get(symbol, [])

    def set_candles_1d(self, symbol: str, candles: list):
        with self._lock:
            self._candles_1d[symbol] = candles
            self._last_updated[f"candle_1d_{symbol}"] = datetime.now(timezone.utc)

    def get_candles_1d(self, symbol: str) -> list:
        with self._lock:
            return self._candles_1d.get(symbol, [])

    # ── Funding rates ─────────────────────────────────────────────────────

    def set_funding(self, symbol: str, rate: float):
        with self._lock:
            self._funding[symbol] = rate

    def get_funding(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._funding.get(symbol)

    def get_all_funding(self) -> dict:
        with self._lock:
            return dict(self._funding)

    # ── Open interest ─────────────────────────────────────────────────────

    def push_oi(self, symbol: str, oi_usd: float, ts: datetime):
        with self._lock:
            history = self._oi[symbol]
            prev = history[-1]["oi_usd"] if history else None
            change_pct = ((oi_usd - prev) / prev * 100) if prev else None
            history.append({"time": ts, "oi_usd": oi_usd, "change_pct": change_pct})
            self._last_updated[f"oi_{symbol}"] = ts

    def get_oi(self, symbol: str, limit: Optional[int] = None) -> list:
        with self._lock:
            history = list(self._oi[symbol])
            return history[-limit:] if limit else history

    def get_latest_oi(self, symbol: str) -> Optional[dict]:
        with self._lock:
            h = self._oi[symbol]
            return h[-1] if h else None

    def get_oi_change_pct(self, symbol: str, minutes: int) -> Optional[float]:
        """
        % change in OI over the last `minutes` window.
        Finds the oldest snapshot within the window and compares to latest.
        Returns None if insufficient history.
        """
        with self._lock:
            history = list(self._oi[symbol])
        if len(history) < 2:
            return None
        now = history[-1]["time"]
        cutoff = now - timedelta(minutes=minutes)
        # Find the snapshot closest to (but not newer than) the cutoff
        baseline = None
        for snap in history:
            if snap["time"] <= cutoff:
                baseline = snap
        if baseline is None:
            return None
        latest_oi = history[-1]["oi_usd"]
        base_oi = baseline["oi_usd"]
        if base_oi == 0:
            return None
        return (latest_oi - base_oi) / base_oi * 100

    # ── Top movers ────────────────────────────────────────────────────────

    def set_top_movers(self, movers: list):
        """Store sorted list of top 24h gainers and losers."""
        with self._lock:
            self._top_movers = movers

    def get_top_movers(self) -> list:
        with self._lock:
            return list(self._top_movers)

    # ── Macro ─────────────────────────────────────────────────────────────

    def set_macro(self, key: str, value):
        with self._lock:
            self._macro[key] = value
            self._last_updated[f"macro_{key}"] = datetime.now(timezone.utc)

    def get_macro(self) -> dict:
        with self._lock:
            return dict(self._macro)

    # ── Diagnostics ───────────────────────────────────────────────────────

    def summary(self) -> dict:
        with self._lock:
            return {
                "active_symbols": len(self._active_symbols),
                "symbols_with_candles": sum(
                    1 for s in self._active_symbols if self._candles_1m[s]
                ),
                "macro": dict(self._macro),
                "funding_count": len(self._funding),
                "oi_count": len(self._oi),
            }


# Singleton — import this everywhere
store = MarketStore()

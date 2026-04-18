"""
Binance Futures REST helpers.

Covers:
  - Symbol discovery (all active USDT-M perpetuals)
  - OHLCV fetch (1m, 4h, 1d)
  - Volume-based symbol qualification
  - Funding rates (bulk, single call)
  - Open interest (per symbol)
"""

import logging
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

from config import (
    BINANCE_FUTURES_REST,
    REST_CALL_DELAY,
    REST_MAX_WEIGHT_MIN,
    VOLUME_USD_MIN,
    ANCHOR_SYMBOLS,
    CANDLES_1H_LIMIT,
    CANDLES_4H_LIMIT,
    CANDLES_1D_LIMIT,
)
from store import store

log = logging.getLogger(__name__)

# ── Rate limiter ──────────────────────────────────────────────────────────

_rate_lock   = threading.Lock()
_weight_used = 0
_window_start = time.monotonic()


def _check_rate(weight: int = 1):
    global _weight_used, _window_start
    with _rate_lock:
        elapsed = time.monotonic() - _window_start
        if elapsed >= 60:
            _weight_used = 0
            _window_start = time.monotonic()
        if _weight_used + weight > REST_MAX_WEIGHT_MIN:
            wait = 60 - elapsed
            log.warning(f"Rate limit cushion hit — sleeping {wait:.1f}s")
            time.sleep(max(wait, 0) + 1)
            _weight_used = 0
            _window_start = time.monotonic()
        _weight_used += weight


def _get(path: str, params: dict = None, weight: int = 1) -> Optional[dict | list]:
    _check_rate(weight)
    time.sleep(REST_CALL_DELAY)
    url = BINANCE_FUTURES_REST + path
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"REST {path} failed: {e}")
        return None


# ── Symbol discovery ──────────────────────────────────────────────────────

def fetch_all_usdt_perp_symbols() -> list[str]:
    """Return all active USDT-margined perpetual symbols."""
    data = _get("/fapi/v1/exchangeInfo", weight=1)
    if not data:
        return []
    symbols = [
        s["symbol"]
        for s in data.get("symbols", [])
        if s.get("quoteAsset") == "USDT"
        and s.get("contractType") == "PERPETUAL"
        and s.get("status") == "TRADING"
    ]
    log.info(f"Exchange info: {len(symbols)} USDT perpetuals found")
    return symbols


def qualifies_by_volume(symbol: str) -> bool:
    """
    Check last closed 1m candle's quote volume against threshold.
    Used for initial screening and periodic refresh.
    Weight: 2 (klines with limit ≤ 100).
    """
    data = _get("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 3}, weight=2)
    if not data or len(data) < 2:
        return False
    last_closed = data[-2]   # -1 is still open
    vol_usd = float(last_closed[7])
    return vol_usd >= VOLUME_USD_MIN


# ── OHLCV ─────────────────────────────────────────────────────────────────

def _parse_klines(raw: list, symbol: str) -> list[dict]:
    candles = []
    for k in raw:
        candles.append({
            "time":       datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            "open":       float(k[1]),
            "high":       float(k[2]),
            "low":        float(k[3]),
            "close":      float(k[4]),
            "volume_usd": float(k[7]),
            "trades":     int(k[8]),
        })
    return candles


def fetch_candles_1m(symbol: str, limit: int = 100) -> list[dict]:
    """Fetch recent 1m closed candles (used for initial symbol warm-up)."""
    weight = 2 if limit <= 100 else 5
    data = _get("/fapi/v1/klines",
                {"symbol": symbol, "interval": "1m", "limit": limit + 1},
                weight=weight)
    if not data:
        return []
    return _parse_klines(data[:-1], symbol)   # drop the open (current) candle


def fetch_candles_1h(symbol: str, limit: int = CANDLES_1H_LIMIT) -> list[dict]:
    weight = 2 if limit <= 100 else 5
    data = _get("/fapi/v1/klines",
                {"symbol": symbol, "interval": "1h", "limit": limit + 1},
                weight=weight)
    if not data:
        return []
    return _parse_klines(data[:-1], symbol)


def fetch_candles_4h(symbol: str, limit: int = CANDLES_4H_LIMIT) -> list[dict]:
    weight = 2 if limit <= 100 else 5
    data = _get("/fapi/v1/klines",
                {"symbol": symbol, "interval": "4h", "limit": limit + 1},
                weight=weight)
    if not data:
        return []
    return _parse_klines(data[:-1], symbol)


def fetch_candles_1d(symbol: str, limit: int = CANDLES_1D_LIMIT) -> list[dict]:
    weight = 2 if limit <= 100 else 5
    data = _get("/fapi/v1/klines",
                {"symbol": symbol, "interval": "1d", "limit": limit + 1},
                weight=weight)
    if not data:
        return []
    return _parse_klines(data[:-1], symbol)


# ── Funding rates (bulk) ──────────────────────────────────────────────────

def fetch_all_funding() -> dict[str, float]:
    """
    Single call returns funding for all symbols.
    Uses /fapi/v1/premiumIndex (weight = 10).
    """
    data = _get("/fapi/v1/premiumIndex", weight=10)
    if not data:
        return {}
    rates = {}
    for item in data:
        sym = item.get("symbol", "")
        rate = item.get("lastFundingRate")
        if sym and rate is not None:
            rates[sym] = float(rate)
    log.info(f"Funding rates fetched: {len(rates)} symbols")
    return rates


# ── Open interest (per symbol) ────────────────────────────────────────────

def fetch_oi(symbol: str) -> Optional[float]:
    """Returns current OI in USD for one symbol. Weight: 1."""
    data = _get("/fapi/v1/openInterest", {"symbol": symbol}, weight=1)
    if not data:
        return None
    try:
        oi_coins = float(data["openInterest"])
        price    = float(data.get("price") or _get_mark_price(symbol) or 0)
        return oi_coins * price if price else None
    except Exception:
        return None


def _get_mark_price(symbol: str) -> Optional[float]:
    data = _get("/fapi/v1/premiumIndex", {"symbol": symbol}, weight=1)
    if data and isinstance(data, dict):
        return float(data.get("markPrice", 0)) or None
    return None


# ── 24h tickers (top movers) ─────────────────────────────────────────────

def fetch_24h_tickers(top_n: int = 5) -> list[dict]:
    """
    Fetch 24h price change stats for all USDT perpetuals.
    Returns top_n gainers + top_n losers sorted by |change_pct|.
    Weight: 40 (bulk ticker endpoint).
    """
    data = _get("/fapi/v1/ticker/24hr", weight=40)
    if not data:
        return []
    tickers = []
    for t in data:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            tickers.append({
                "symbol":           sym,
                "price_change_pct": float(t["priceChangePercent"]),
                "last_price":       float(t["lastPrice"]),
                "volume_usd":       float(t["quoteVolume"]),
            })
        except (KeyError, ValueError):
            continue
    gainers = sorted(tickers, key=lambda x: x["price_change_pct"], reverse=True)[:top_n]
    losers  = sorted(tickers, key=lambda x: x["price_change_pct"])[:top_n]
    return gainers + losers


# ── Bulk initialisation helpers ───────────────────────────────────────────

def warmup_symbol(symbol: str) -> bool:
    """
    Load 1m + 1h history into store for all symbols; also 4h + 1d for anchors.
    Returns True on success.
    """
    candles_1m = fetch_candles_1m(symbol, limit=100)
    if not candles_1m:
        return False

    for c in candles_1m:
        store.push_candle_1m(symbol, c)

    c1h = fetch_candles_1h(symbol)
    if c1h:
        store.set_candles_1h(symbol, c1h)

    if symbol in ANCHOR_SYMBOLS:
        c4h = fetch_candles_4h(symbol)
        c1d = fetch_candles_1d(symbol)
        if c4h:
            store.set_candles_4h(symbol, c4h)
        if c1d:
            store.set_candles_1d(symbol, c1d)

    return True


def initial_scan(all_symbols: list[str]) -> list[str]:
    """
    Filter all_symbols by volume threshold, warm up each qualifying symbol.
    Always includes ANCHOR_SYMBOLS.
    Returns list of qualifying symbols.
    """
    log.info(f"Initial scan: checking {len(all_symbols)} symbols for vol ≥ ${VOLUME_USD_MIN:,.0f}/min")
    qualifying = []

    for i, sym in enumerate(all_symbols, 1):
        if i % 50 == 0:
            log.info(f"  ... {i}/{len(all_symbols)} checked, {len(qualifying)} qualify so far")
        try:
            is_anchor = sym in ANCHOR_SYMBOLS
            passes = is_anchor or qualifies_by_volume(sym)
            if passes:
                ok = warmup_symbol(sym)
                if ok:
                    qualifying.append(sym)
                    store.add_symbol(sym)
                    log.debug(f"  ✓ {sym}")
        except Exception as e:
            log.warning(f"  ✗ {sym}: {e}")

    log.info(f"Initial scan complete: {len(qualifying)} symbols qualify")
    return qualifying


def refresh_symbol_list(current: set[str], all_symbols: list[str]) -> list[str]:
    """
    Check symbols not currently tracked; add new ones that now qualify.
    Returns list of newly added symbols.
    """
    candidates = [s for s in all_symbols if s not in current]
    if not candidates:
        return []
    log.info(f"Symbol refresh: checking {len(candidates)} new candidates")
    added = []
    for sym in candidates:
        try:
            if qualifies_by_volume(sym):
                ok = warmup_symbol(sym)
                if ok:
                    store.add_symbol(sym)
                    added.append(sym)
                    log.info(f"  ➕ {sym} added")
        except Exception as e:
            log.warning(f"  Refresh error {sym}: {e}")
    return added

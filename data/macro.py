"""
Macro data fetchers: Fear & Greed, VIX, BTC ETF flows, FOMC calendar.

All functions write directly to the store singleton.
Each is designed to fail gracefully — missing macro data is non-fatal.
"""

import logging
from datetime import date, datetime, timezone

import requests

from config import (
    FEAR_GREED_URL,
    COINGLASS_ETF_URL,
    COINGLASS_API_KEY,
    FOMC_DATES_2026,
)
from store import store

log = logging.getLogger(__name__)


# ── Fear & Greed ──────────────────────────────────────────────────────────

def fetch_fear_greed():
    """
    Crypto Fear & Greed Index from alternative.me — free, no key required.
    """
    try:
        r = requests.get(FEAR_GREED_URL, timeout=8)
        r.raise_for_status()
        data = r.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]   # e.g. "Extreme Fear"
        store.set_macro("fear_greed", value)
        store.set_macro("fear_greed_label", label)
        log.info(f"Fear & Greed: {value} ({label})")
    except Exception as e:
        log.warning(f"Fear & Greed fetch failed: {e}")


# ── VIX ───────────────────────────────────────────────────────────────────

def fetch_vix():
    """
    CBOE VIX via yfinance. Reflects equity market volatility — useful
    macro context for risk-on / risk-off regime.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="2d", interval="1d")
        if hist.empty:
            log.warning("VIX: empty response from yfinance")
            return
        vix = float(hist["Close"].iloc[-1])
        store.set_macro("vix", round(vix, 2))
        log.info(f"VIX: {vix:.2f}")
    except Exception as e:
        log.warning(f"VIX fetch failed: {e}")


# ── BTC ETF flows ─────────────────────────────────────────────────────────

def fetch_etf_flows():
    """
    Daily net BTC ETF flow in USD millions via CoinGlass.
    Requires COINGLASS_API_KEY in config for live data.
    Falls back gracefully if key is missing.
    """
    if not COINGLASS_API_KEY:
        log.debug("ETF flows: no CoinGlass API key configured — skipping")
        return
    try:
        headers = {"CG-API-KEY": COINGLASS_API_KEY}
        r = requests.get(COINGLASS_ETF_URL, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        # CoinGlass returns list of daily entries; pick most recent
        entries = data.get("data", [])
        if not entries:
            return
        latest = entries[-1]
        net_flow = float(latest.get("netFlow", 0))   # USD millions
        store.set_macro("etf_flow_24h", round(net_flow, 2))
        log.info(f"ETF net flow (24h): ${net_flow:,.1f}M")
    except Exception as e:
        log.warning(f"ETF flow fetch failed: {e}")


# ── FOMC calendar ─────────────────────────────────────────────────────────

def update_fomc():
    """
    Determine the next upcoming FOMC decision date from the hardcoded 2026 list.
    Writes fomc_next and fomc_days_away to the store.
    """
    today = date.today()
    upcoming = [
        d for d in FOMC_DATES_2026
        if date.fromisoformat(d) >= today
    ]
    if not upcoming:
        store.set_macro("fomc_next", None)
        store.set_macro("fomc_days_away", None)
        return

    next_date = date.fromisoformat(upcoming[0])
    days_away = (next_date - today).days
    store.set_macro("fomc_next", upcoming[0])
    store.set_macro("fomc_days_away", days_away)
    log.info(f"Next FOMC: {upcoming[0]} ({days_away} days away)")


# ── Combined refresh ──────────────────────────────────────────────────────

def refresh_all():
    """Refresh all macro data sources. Called on schedule by pipeline."""
    fetch_fear_greed()
    fetch_vix()
    fetch_etf_flows()
    update_fomc()   # cheap — just date arithmetic after initial call

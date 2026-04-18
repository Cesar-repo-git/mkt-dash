"""
Central configuration for the mkt-dash pipeline.
Edit values here; nothing else should need changing for environment setup.
"""

# ── Binance ────────────────────────────────────────────────────────────────
BINANCE_FUTURES_REST = "https://fapi.binance.com"
BINANCE_WS_BASE      = "wss://fstream.binance.com/stream?streams="

# Volume filter: minimum average USD volume per 1-minute candle to qualify
VOLUME_USD_MIN = 100_000

# Candle history kept in memory per symbol (500 × 1m ≈ 8 h of data)
CANDLES_1M_MAXLEN = 500

# How many 4h / 1d candles to fetch for BTC/ETH anchor analysis
CANDLES_4H_LIMIT = 60   # ~10 days
CANDLES_1D_LIMIT = 30   # ~1 month

# Symbols always included regardless of volume filter
ANCHOR_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# ── WebSocket ──────────────────────────────────────────────────────────────
WS_SYMBOLS_PER_CONN  = 50      # Binance allows up to 200 streams/conn; 50 is safe
WS_PING_INTERVAL     = 180     # seconds
WS_PING_TIMEOUT      = 30
WS_RECONNECT_DELAY   = 15      # seconds before reconnect attempt
WS_MAX_RECONNECTS    = 10
WS_RESTART_COOLDOWN  = 120     # min seconds between full WS restarts

# ── Polling intervals (seconds) ────────────────────────────────────────────
POLL_FUNDING_ACTIVE    =  5 * 60   # funding + OI during session
POLL_FUNDING_OFFHOURS  = 15 * 60
POLL_MACRO_ACTIVE      = 15 * 60   # VIX, F&G, ETF during session
POLL_MACRO_OFFHOURS    = 60 * 60
POLL_4H_CANDLES        =  4 * 60 * 60
POLL_SYMBOL_REFRESH    = 15 * 60   # re-scan for new qualifying symbols

# ── Session hours (WET = Europe/Lisbon) ───────────────────────────────────
SESSION_TZ         = "Europe/Lisbon"
SESSION_START_HOUR = 8    # 08:00 WET
SESSION_END_HOUR   = 23   # 23:00 WET

# ── Rate limiting ──────────────────────────────────────────────────────────
REST_CALL_DELAY        = 0.08   # seconds between REST calls
REST_MAX_WEIGHT_MIN    = 1_800  # conservative ceiling (Binance limit is 2400/min)

# ── Macro sources ──────────────────────────────────────────────────────────
FEAR_GREED_URL  = "https://api.alternative.me/fng/?limit=1"
COINGLASS_ETF_URL = "https://open-api.coinglass.com/public/v2/etf/bitcoin_etf_flow_all_data"
COINGLASS_API_KEY = ""   # optional — set to enable ETF flow data

# FOMC meeting dates 2026 (decision day = second day)
FOMC_DATES_2026 = [
    "2026-01-29",
    "2026-03-19",
    "2026-04-29",
    "2026-06-10",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

# ── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
LOG_DATE   = "%H:%M:%S"

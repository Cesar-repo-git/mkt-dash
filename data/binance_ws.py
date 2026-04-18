"""
Binance Futures WebSocket manager.

Maintains one connection per chunk of 50 symbols, all streaming 1m klines.
On closed candle: pushes to store, checks volume still qualifies,
drops symbol if below threshold.

Reconnect and restart logic adapted from Momentum_Script.txt (V3).
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import websocket

from config import (
    BINANCE_WS_BASE,
    VOLUME_USD_MIN,
    WS_SYMBOLS_PER_CONN,
    WS_PING_INTERVAL,
    WS_PING_TIMEOUT,
    WS_RECONNECT_DELAY,
    WS_MAX_RECONNECTS,
    WS_RESTART_COOLDOWN,
)
from store import store

log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────

_ws_connections:  list[websocket.WebSocketApp] = []
_ws_lock          = threading.Lock()
_restart_lock     = threading.Lock()
_restart_needed   = threading.Event()
_last_restart     = 0.0   # monotonic timestamp
_reconnect_counts: dict[int, int] = {}   # conn_index → attempts
_ws_healthy       = threading.Event()
_ws_healthy.set()

# Optional callback invoked after every closed candle is pushed to store.
# Signature: on_candle(symbol: str, candle: dict) → None
_on_candle_cb: Callable | None = None


def set_candle_callback(cb: Callable):
    global _on_candle_cb
    _on_candle_cb = cb


# ── Message handling ──────────────────────────────────────────────────────

def _on_message(ws, raw):
    try:
        if not isinstance(raw, str) or not raw.startswith("{"):
            return
        msg = json.loads(raw)
        data = msg.get("data", msg)

        if data.get("e") != "kline":
            return
        kline = data["k"]

        if not kline["x"]:
            return   # candle not yet closed

        symbol = kline["s"]
        if symbol not in store.get_active_symbols():
            return

        candle = {
            "time":       datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
            "open":       float(kline["o"]),
            "high":       float(kline["H"]),
            "low":        float(kline["l"]),
            "close":      float(kline["c"]),
            "volume_usd": float(kline["q"]),   # quote volume = USD for USDT pairs
            "trades":     int(kline["n"]),
        }

        # Drop symbol if it falls below volume threshold (anchors exempt)
        from config import ANCHOR_SYMBOLS
        if symbol not in ANCHOR_SYMBOLS and candle["volume_usd"] < VOLUME_USD_MIN:
            log.info(f"📉 {symbol} dropped — vol ${candle['volume_usd']:,.0f} < threshold")
            store.remove_symbol(symbol)
            return

        store.push_candle_1m(symbol, candle)

        if _on_candle_cb:
            try:
                _on_candle_cb(symbol, candle)
            except Exception as e:
                log.error(f"on_candle_cb error for {symbol}: {e}")

    except Exception as e:
        log.error(f"WS message error: {e}")


def _on_error(ws, error):
    if "10054" not in str(error):   # suppress Windows conn-reset noise
        log.warning(f"WS error: {error}")
    _ws_healthy.clear()


def _on_close(ws, code, msg):
    _ws_healthy.clear()
    log.warning(f"WS closed (code={code})")


def _on_open(ws):
    _ws_healthy.set()
    log.debug("WS connection opened")


# ── Connection management ─────────────────────────────────────────────────

def _build_stream_url(symbols: list[str]) -> str:
    streams = "/".join(f"{s.lower()}@kline_1m" for s in symbols)
    return BINANCE_WS_BASE + streams


def _launch_connection(symbols: list[str], conn_index: int):
    url = _build_stream_url(symbols)
    reconnects = 0

    while reconnects <= WS_MAX_RECONNECTS:
        ws = websocket.WebSocketApp(
            url,
            on_message=_on_message,
            on_error=_on_error,
            on_close=_on_close,
            on_open=_on_open,
        )
        with _ws_lock:
            if conn_index < len(_ws_connections):
                _ws_connections[conn_index] = ws
            else:
                while len(_ws_connections) <= conn_index:
                    _ws_connections.append(None)
                _ws_connections[conn_index] = ws

        ws.run_forever(ping_interval=WS_PING_INTERVAL, ping_timeout=WS_PING_TIMEOUT)

        if _restart_needed.is_set():
            log.info(f"Conn {conn_index}: restart requested — exiting thread")
            return

        reconnects += 1
        log.warning(f"Conn {conn_index}: reconnect {reconnects}/{WS_MAX_RECONNECTS} in {WS_RECONNECT_DELAY}s")
        time.sleep(WS_RECONNECT_DELAY)

    log.error(f"Conn {conn_index}: max reconnects reached — giving up")


def start(symbols: list[str]):
    """Start WebSocket connections for the given symbol list."""
    global _last_restart
    _restart_needed.clear()

    chunks = [symbols[i:i + WS_SYMBOLS_PER_CONN]
              for i in range(0, len(symbols), WS_SYMBOLS_PER_CONN)]

    log.info(f"🌐 Starting {len(chunks)} WS connections for {len(symbols)} symbols")

    with _ws_lock:
        _ws_connections.clear()

    for idx, chunk in enumerate(chunks):
        t = threading.Thread(
            target=_launch_connection,
            args=(chunk, idx),
            daemon=True,
            name=f"ws-conn-{idx}",
        )
        t.start()
        if idx < len(chunks) - 1:
            time.sleep(1)   # stagger connections

    _last_restart = time.monotonic()
    log.info("✅ WebSocket connections launched")


def restart(symbols: list[str]):
    """Tear down existing connections and restart with updated symbol list."""
    if not _restart_lock.acquire(blocking=False):
        log.warning("WS restart already in progress — skipping")
        return

    try:
        elapsed = time.monotonic() - _last_restart
        if elapsed < WS_RESTART_COOLDOWN:
            remaining = WS_RESTART_COOLDOWN - elapsed
            log.info(f"⏳ WS restart cooldown: {remaining:.0f}s remaining")
            return

        log.info("🔄 WS restart: closing existing connections")
        _restart_needed.set()
        with _ws_lock:
            for ws in _ws_connections:
                try:
                    if ws:
                        ws.close()
                except Exception:
                    pass
        time.sleep(3)
        start(symbols)
    finally:
        _restart_lock.release()


# ── Restart monitor (background thread) ───────────────────────────────────

def _restart_monitor():
    """Watches for restart requests triggered by symbol refresh."""
    while True:
        time.sleep(30)
        if _restart_needed.is_set():
            syms = list(store.get_active_symbols())
            if syms:
                restart(syms)


def start_restart_monitor():
    t = threading.Thread(target=_restart_monitor, daemon=True, name="ws-restart-monitor")
    t.start()
    log.info("WS restart monitor started")

"""
Persistent signal ledger.

Tracks viable signals across scanner cycles with timestamps and repeat counts.
Survives pipeline restarts via JSON persistence on disk.

Ledger entry schema:
    {
        'symbol':       str,
        'setup':        str,       # MO_LONG | MO_SHORT | MR_LONG | MR_SHORT
        'score':        float,
        'first_seen':   str,       # ISO 8601 UTC
        'last_seen':    str,       # ISO 8601 UTC — updated each scan cycle
        'count':        int,       # how many scan cycles this signal has been confirmed
        'price':        float,
        'vwap':         float or None,
        'vwap_upper1':  float or None,
        'vwap_lower1':  float or None,
        'vwap_pct':     float or None,
        'funding':      float or None,
        'oi_usd':       float or None,
        'oi_direction': str,
        'oi_chg_15m':   float or None,
        'oi_chg_1h':    float or None,
        'oi_chg_4h':    float or None,
        'oi_chg_1d':    float or None,
        'vol_trend':    str,
        'vol_ma30':     float or None,
        'vol_ma60':     float or None,
        'adx':          float or None,
        'regime':       str,
        'staircase_score': float or None,
        'range_pct':    float or None,
        'ma_crossings': int or None,
        'trend_duration': int or None,
        'prev_day_high':          float or None,
        'prev_day_low':           float or None,
        'prev_day_high_dist_pct': float or None,
        'prev_day_low_dist_pct':  float or None,
        'support':      float or None,
        'resistance':   float or None,
    }

Expiry: entries where `last_seen` is older than SIGNAL_TTL_HOURS are removed
        on each call to expire().
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

SIGNAL_TTL_HOURS = 4
PERSIST_PATH     = os.path.join(os.path.dirname(__file__), "..", "data", "signal_ledger.json")

# Fields to copy from classifier result into ledger entry
_COPY_FIELDS = [
    "setup", "score", "price",
    "vwap", "vwap_upper1", "vwap_lower1", "vwap_pct",
    "funding",
    "oi_usd", "oi_direction", "oi_chg_15m", "oi_chg_1h", "oi_chg_4h", "oi_chg_1d",
    "vol_trend", "vol_ma30", "vol_ma60",
    "adx", "regime",
    "staircase_score", "range_pct", "ma_crossings", "trend_duration",
    "prev_day_high", "prev_day_low", "prev_day_high_dist_pct", "prev_day_low_dist_pct",
    "support", "resistance",
]

# ── Internal state ────────────────────────────────────────────────────────

_ledger: dict[str, dict] = {}   # symbol → ledger entry
_lock   = threading.RLock()


# ── Persistence ───────────────────────────────────────────────────────────

def _load():
    """Load ledger from disk on startup. Silently ignores missing file."""
    global _ledger
    try:
        if not os.path.exists(PERSIST_PATH):
            return
        with open(PERSIST_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _ledger = raw
        log.info(f"Signal ledger loaded: {len(_ledger)} entries from disk")
    except Exception as e:
        log.warning(f"Could not load signal ledger: {e}")
        _ledger = {}


def _save():
    """Persist current ledger to disk. Called after every upsert/expire."""
    try:
        os.makedirs(os.path.dirname(PERSIST_PATH), exist_ok=True)
        with open(PERSIST_PATH, "w", encoding="utf-8") as f:
            json.dump(_ledger, f, indent=2)
    except Exception as e:
        log.warning(f"Could not save signal ledger: {e}")


# ── Public API ────────────────────────────────────────────────────────────

def load():
    """Call once at startup to restore persisted ledger."""
    with _lock:
        _load()


def upsert(symbol: str, result: dict):
    """
    Add or update a ledger entry for a viable signal.
    - New symbol: creates entry with first_seen = now, count = 1
    - Existing symbol: updates last_seen, increments count, refreshes data fields
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    with _lock:
        if symbol in _ledger:
            entry = _ledger[symbol]
            entry["last_seen"] = now_iso
            entry["count"]     = entry.get("count", 1) + 1
            for field in _COPY_FIELDS:
                entry[field] = result.get(field)
        else:
            entry = {"symbol": symbol, "first_seen": now_iso, "last_seen": now_iso, "count": 1}
            for field in _COPY_FIELDS:
                entry[field] = result.get(field)
            _ledger[symbol] = entry

        _save()


def expire():
    """Remove entries not renewed within SIGNAL_TTL_HOURS. Returns count removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SIGNAL_TTL_HOURS)
    removed = []

    with _lock:
        for sym, entry in list(_ledger.items()):
            try:
                last = datetime.fromisoformat(entry["last_seen"])
                if last < cutoff:
                    removed.append(sym)
            except Exception:
                removed.append(sym)

        for sym in removed:
            del _ledger[sym]

        if removed:
            log.info(f"Signal ledger: expired {len(removed)} stale entries — {removed}")
            _save()

    return len(removed)


def remove(symbol: str):
    """Manually remove a symbol from the ledger."""
    with _lock:
        if symbol in _ledger:
            del _ledger[symbol]
            _save()


def get_all() -> list[dict]:
    """
    Return all active ledger entries as a list, sorted by first_seen descending
    (newest alerts at top).
    """
    with _lock:
        entries = list(_ledger.values())
    return sorted(entries, key=lambda x: x.get("first_seen", ""), reverse=True)


def get_entry(symbol: str) -> Optional[dict]:
    with _lock:
        return dict(_ledger[symbol]) if symbol in _ledger else None


def count() -> int:
    with _lock:
        return len(_ledger)

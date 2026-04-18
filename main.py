"""
Entry point.
Run:  python main.py
"""

import logging
import signal
import sys
import time

from config import LOG_LEVEL, LOG_FORMAT, LOG_DATE
import pipeline

# ── Logging setup ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    datefmt=LOG_DATE,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("mkt_dash.log", encoding="utf-8"),
    ],
)

# Silence noisy third-party loggers
logging.getLogger("websocket").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


def _shutdown(sig, frame):
    log.info("🛑 Shutdown signal received — stopping")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    pipeline.start()

    # Keep main thread alive; all work is in daemon threads
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("🛑 Interrupted — stopping")

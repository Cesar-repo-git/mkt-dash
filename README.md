# mkt-dash — Live Crypto Market Dashboard

24/7 live market dashboard supporting trading decisions on crypto perpetuals.
Runs on Hetzner VPS, accessible via browser at `http://VPS_IP:8050`.

## Architecture

```
Stage 1 — Data Pipeline
  Binance fstream WebSocket  → live 1m candles for all qualifying USDT perps
  Binance REST               → 4h/1d OHLCV, funding rates, open interest
  Macro sources              → VIX (yfinance), Fear & Greed (alternative.me),
                               ETF flows (CoinGlass), FOMC calendar

Stage 2 — Strategy Classifiers
  Regime engine  → ADX + Hurst → TRENDING / RANGING / UNCLEAR
  MO scorer      → Momentum Long/Short (ADX, staircase, vol, VWAP, OI)
  MR scorer      → Mean Reversion Long/Short (range quality, vol flatness, OI choppiness)
  Signal engine  → Breakout detection (MO) + SFP detection (MR)

Stage 3 — Dashboard (Plotly Dash)
  Dark minimalist UI at VPS_IP:8050
  Auto-refresh: 1min during session (08:00–23:00 WET), 15min off-hours
```

## 4 Trading Setups

| Setup | Regime | Entry Trigger |
|---|---|---|
| MO Long  | Trending | 1 candle close above resistance |
| MO Short | Trending | 1 candle close below support |
| MR Long  | Ranging  | SFP — wick sweeps below support, closes above |
| MR Short | Ranging  | SFP — wick sweeps above resistance, closes below |

## Setup

```bash
pip install -r requirements.txt
python main.py
```

## Volume filter
Only symbols with ≥ $100k USD average volume per 1m candle are tracked.
BTC/USDT and ETH/USDT are always included as anchors.

## VPS deployment
Managed via systemd service. See `deploy/mkt-dash.service`.

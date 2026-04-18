"""
Stage 3 — Plotly Dash web dashboard.

Tabs:
  Main  — header + macro strip + BTC/ETH cards + charts
  Longs — signals table (long setups) + overview card
  Shorts— signals table (short setups) + overview card

Signals table: shows ledger entries with timestamp, repeat count (#N),
               auto-expires after 4h (handled by signal_ledger).

Refresh: 60s during session, 900s off-hours.
Access:  http://VPS_IP:8050
"""

import threading
import logging
from datetime import datetime, timezone

import pytz
import plotly.graph_objects as go
import dash
import dash_bootstrap_components as dbc
from dash import dcc, html, dash_table, Input, Output

from config import SESSION_TZ, SESSION_START_HOUR, SESSION_END_HOUR, ANCHOR_SYMBOLS
from store import store
from classifiers.engine import get_signals, get_ledger

log = logging.getLogger(__name__)

_tz = pytz.timezone(SESSION_TZ)

# ── Palette ───────────────────────────────────────────────────────────────

C = {
    "bg":       "#0d1117",
    "card":     "#161b22",
    "border":   "#30363d",
    "text":     "#e6edf3",
    "dim":      "#8b949e",
    "mo_long":  "#3fb950",
    "mo_short": "#f85149",
    "mr_long":  "#58a6ff",
    "mr_short": "#d29922",
    "green":    "#3fb950",
    "red":      "#f85149",
    "neutral":  "#8b949e",
    "trending": "#3fb950",
    "ranging":  "#58a6ff",
}

SETUP_COLOR = {
    "MO_LONG":  C["mo_long"],
    "MO_SHORT": C["mo_short"],
    "MR_LONG":  C["mr_long"],
    "MR_SHORT": C["mr_short"],
}

REFRESH_ACTIVE   = 60_000
REFRESH_OFFHOURS = 900_000

FONT = "'Inter', 'Segoe UI', sans-serif"
MONO = "'JetBrains Mono', 'Fira Code', monospace"

BASE_TEXT  = {"fontFamily": FONT, "fontSize": "14px", "color": C["text"]}
LABEL_TEXT = {"fontFamily": FONT, "fontSize": "11px", "color": C["dim"],
              "textTransform": "uppercase", "letterSpacing": "0.08em"}


# ── Helpers ───────────────────────────────────────────────────────────────

def _in_session() -> bool:
    now = datetime.now(_tz)
    return SESSION_START_HOUR <= now.hour < SESSION_END_HOUR


def _fmt_price(p):
    if p is None:
        return "—"
    if p >= 1000:
        return f"${p:,.1f}"
    if p >= 1:
        return f"${p:,.3f}"
    return f"${p:.5f}"


def _fmt_pct(v, decimals=2):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def _fmt_funding(v):
    if v is None:
        return "—"
    pct = v * 100
    color = C["green"] if pct >= 0 else C["red"]
    return html.Span(f"{pct:+.4f}%", style={"color": color, "fontFamily": MONO})


def _fmt_ts(iso_str):
    """Format ISO timestamp to HH:MM DD/MM."""
    try:
        dt = datetime.fromisoformat(iso_str).astimezone(_tz)
        return dt.strftime("%H:%M %d/%m")
    except Exception:
        return "—"


def _setup_badge(setup):
    color = SETUP_COLOR.get(setup, C["dim"])
    return html.Span(
        setup or "—",
        style={
            "background": color + "22",
            "color": color,
            "border": f"1px solid {color}55",
            "borderRadius": "4px",
            "padding": "2px 7px",
            "fontSize": "12px",
            "fontWeight": "700",
            "fontFamily": FONT,
        },
    )


def _regime_badge(regime):
    color = {"TRENDING": C["trending"], "RANGING": C["ranging"]}.get(regime, C["dim"])
    return html.Span(
        regime or "—",
        style={
            "background": color + "22",
            "color": color,
            "border": f"1px solid {color}55",
            "borderRadius": "4px",
            "padding": "2px 7px",
            "fontSize": "12px",
            "fontWeight": "600",
            "fontFamily": FONT,
        },
    )


def _score_bar(score):
    if score is None:
        return "—"
    color = C["green"] if score >= 70 else (C["mr_short"] if score >= 55 else C["dim"])
    return html.Div([
        html.Span(f"{score:.0f}",
                  style={"color": color, "fontWeight": "700", "marginRight": "8px",
                         "fontFamily": MONO, "fontSize": "14px"}),
        html.Div(
            html.Div(style={
                "width": f"{score}%", "height": "5px",
                "background": color, "borderRadius": "3px",
            }),
            style={"width": "70px", "background": C["border"], "borderRadius": "3px",
                   "display": "inline-block", "verticalAlign": "middle"},
        ),
    ], style={"display": "flex", "alignItems": "center"})


def _row(label, value):
    return html.Div([
        html.Span(label, style={**LABEL_TEXT, "width": "110px", "display": "inline-block"}),
        html.Span([value] if not isinstance(value, list) else value),
    ], style={"marginBottom": "6px", "fontSize": "14px"})


# ── Macro strip ───────────────────────────────────────────────────────────

def _macro_strip():
    macro = store.get_macro()
    fg    = macro.get("fear_greed")
    fgl   = macro.get("fear_greed_label", "")
    vix   = macro.get("vix")
    fomc  = macro.get("fomc_next")
    days  = macro.get("fomc_days_away")
    etf   = macro.get("etf_flow_24h")

    fg_color = C["red"] if (fg or 50) < 30 else (C["green"] if (fg or 50) > 60 else C["mr_short"])

    items = [
        ("F&G",      f"{fg} — {fgl}" if fg is not None else "—",    fg_color),
        ("VIX",      f"{vix:.1f}" if vix else "—",                   C["mr_short"] if (vix or 0) > 25 else C["text"]),
        ("FOMC",     f"{fomc} ({days}d)" if fomc else "—",           C["mr_short"] if (days or 99) <= 7 else C["text"]),
        ("ETF Flow", f"${etf:+,.0f}M" if etf is not None else "N/A", C["green"] if (etf or 0) > 0 else C["red"]),
        ("Symbols",  f"{len(store.get_active_symbols())} tracked",   C["text"]),
    ]

    cards = []
    for label, value, color in items:
        cards.append(
            dbc.Col(
                html.Div([
                    html.Div(label, style=LABEL_TEXT),
                    html.Div(value, style={"fontSize": "14px", "color": color,
                                           "fontWeight": "600", "marginTop": "3px",
                                           "fontFamily": MONO}),
                ], style={"background": C["card"], "border": f"1px solid {C['border']}",
                          "borderRadius": "6px", "padding": "10px 14px"}),
                style={"padding": "0 4px"},
            )
        )
    return dbc.Row(cards, style={"margin": "0 0 14px 0"})


# ── Anchor card ───────────────────────────────────────────────────────────

def _anchor_card(symbol):
    signals = get_signals()
    sig = next((s for s in signals if s["symbol"] == symbol), None)

    price      = sig.get("price")      if sig else None
    regime     = sig.get("regime", "—") if sig else "—"
    setup      = sig.get("setup")      if sig else None
    score      = sig.get("score")      if sig else None
    funding    = sig.get("funding")    if sig else None
    oi_dir     = sig.get("oi_direction", "—") if sig else "—"
    oi_1h      = sig.get("oi_chg_1h")  if sig else None
    adx        = sig.get("adx")        if sig else None
    vwap       = sig.get("vwap")       if sig else None
    vwap_pct   = sig.get("vwap_pct")   if sig else None
    support    = sig.get("support")    if sig else None
    resistance = sig.get("resistance") if sig else None
    pdh        = sig.get("prev_day_high") if sig else None
    pdl        = sig.get("prev_day_low")  if sig else None
    pdh_dist   = sig.get("prev_day_high_dist_pct") if sig else None
    pdl_dist   = sig.get("prev_day_low_dist_pct")  if sig else None

    regime_color = {"TRENDING": C["trending"], "RANGING": C["ranging"]}.get(regime, C["dim"])
    vwap_color   = C["green"] if (vwap_pct or 0) > 0 else C["red"]

    pdh_str = f"{_fmt_price(pdh)} ({_fmt_pct(pdh_dist)})" if pdh else "—"
    pdl_str = f"{_fmt_price(pdl)} ({_fmt_pct(pdl_dist)})" if pdl else "—"

    rows = [
        ("Price",      html.Span(_fmt_price(price),
                                 style={"color": C["text"], "fontWeight": "700",
                                        "fontSize": "18px", "fontFamily": MONO})),
        ("Regime",     html.Span(f"{regime}  ADX {adx:.0f}" if adx else regime,
                                 style={"color": regime_color, "fontWeight": "600"})),
        ("Setup",      _setup_badge(setup)),
        ("Score",      _score_bar(score)),
        ("VWAP",       html.Span(f"{_fmt_price(vwap)}  ({_fmt_pct(vwap_pct)})",
                                 style={"color": vwap_color, "fontFamily": MONO})),
        ("Support",    html.Span(_fmt_price(support), style={"color": C["green"], "fontFamily": MONO})),
        ("Resistance", html.Span(_fmt_price(resistance), style={"color": C["red"], "fontFamily": MONO})),
        ("Funding",    _fmt_funding(funding)),
        ("OI 1h",      html.Span(f"{oi_dir}  {_fmt_pct(oi_1h)}",
                                 style={"color": C["green"] if (oi_1h or 0) > 0 else C["red"],
                                        "fontFamily": MONO})),
        ("Prev D High", html.Span(pdh_str, style={"color": C["red"], "fontFamily": MONO})),
        ("Prev D Low",  html.Span(pdl_str, style={"color": C["green"], "fontFamily": MONO})),
    ]

    return dbc.Col(
        html.Div([
            html.Div(symbol,
                     style={"color": C["text"], "fontWeight": "700", "fontSize": "15px",
                            "marginBottom": "12px", "borderBottom": f"1px solid {C['border']}",
                            "paddingBottom": "8px", "fontFamily": FONT}),
            *[_row(label, value) for label, value in rows],
        ], style={"background": C["card"], "border": f"1px solid {C['border']}",
                  "borderRadius": "8px", "padding": "16px"}),
        md=4, style={"padding": "0 4px"},
    )


# ── Overview card (for Longs/Shorts tabs) ─────────────────────────────────

def _overview_card(direction: str):
    """direction: 'LONG' or 'SHORT'"""
    ledger  = get_ledger()
    signals = get_signals()

    setups_long  = {"MO_LONG", "MR_LONG"}
    setups_short = {"MO_SHORT", "MR_SHORT"}
    target_setups = setups_long if direction == "LONG" else setups_short

    active = [e for e in ledger if e.get("setup") in target_setups]
    mo     = sum(1 for e in active if "MO" in (e.get("setup") or ""))
    mr     = sum(1 for e in active if "MR" in (e.get("setup") or ""))

    regime_counts = {}
    for s in signals:
        r = s.get("regime", "UNCLEAR")
        regime_counts[r] = regime_counts.get(r, 0) + 1

    def stat(label, val, color):
        return html.Div([
            html.Span(str(val), style={"color": color, "fontWeight": "700",
                                       "fontSize": "22px", "fontFamily": MONO}),
            html.Span(f"  {label}", style={**LABEL_TEXT, "fontSize": "12px"}),
        ], style={"marginBottom": "8px"})

    color = C["mo_long"] if direction == "LONG" else C["mo_short"]

    return dbc.Col(
        html.Div([
            html.Div(f"{direction} OVERVIEW",
                     style={**LABEL_TEXT, "marginBottom": "12px",
                            "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"}),
            stat(f"Active {direction} signals", len(active), color),
            stat("Momentum (MO)", mo, C["mo_long"] if direction == "LONG" else C["mo_short"]),
            stat("Mean Rev (MR)", mr, C["mr_long"] if direction == "LONG" else C["mr_short"]),
            html.Hr(style={"borderColor": C["border"], "margin": "10px 0"}),
            stat("Trending",  regime_counts.get("TRENDING", 0), C["trending"]),
            stat("Ranging",   regime_counts.get("RANGING", 0),  C["ranging"]),
            stat("Unclear",   regime_counts.get("UNCLEAR", 0),  C["dim"]),
        ], style={"background": C["card"], "border": f"1px solid {C['border']}",
                  "borderRadius": "8px", "padding": "16px"}),
        md=3, style={"padding": "0 4px"},
    )


# ── Signals table (ledger-backed) ─────────────────────────────────────────

def _signals_table(direction: str):
    """
    direction: 'LONG' or 'SHORT'
    Reads from the persistent signal ledger.
    Columns: First Seen | # | Symbol | Setup | Score | Price | Funding |
             OI 1h% | Vol | ADX | Prev D H dist | Prev D L dist
    """
    ledger = get_ledger()

    target_setups = ({"MO_LONG", "MR_LONG"} if direction == "LONG"
                     else {"MO_SHORT", "MR_SHORT"})
    entries = [e for e in ledger if e.get("setup") in target_setups]

    rows = []
    for e in entries:
        symbol  = e.get("symbol", "")
        setup   = e.get("setup", "—")
        score   = e.get("score")
        count   = e.get("count", 1)
        price   = e.get("price")
        funding = e.get("funding")
        oi_1h   = e.get("oi_chg_1h")
        oi_dir  = e.get("oi_direction", "")
        vol     = e.get("vol_trend", "")
        adx     = e.get("adx")
        pdh_d   = e.get("prev_day_high_dist_pct")
        pdl_d   = e.get("prev_day_low_dist_pct")
        first   = e.get("first_seen", "")

        rows.append({
            "first_seen": _fmt_ts(first),
            "#":          f"#{count}" if count > 1 else "1",
            "symbol":     symbol,
            "setup":      setup,
            "score":      round(score, 0) if score is not None else 0,
            "price":      _fmt_price(price),
            "funding":    f"{funding*100:+.4f}%" if funding is not None else "—",
            "oi_1h":      _fmt_pct(oi_1h, 2),
            "oi_dir":     oi_dir or "—",
            "vol":        vol or "—",
            "adx":        f"{adx:.1f}" if adx is not None else "—",
            "pdh_dist":   _fmt_pct(pdh_d, 2),
            "pdl_dist":   _fmt_pct(pdl_d, 2),
        })

    columns = [
        {"name": "First Seen", "id": "first_seen"},
        {"name": "#",          "id": "#"},
        {"name": "Symbol",     "id": "symbol"},
        {"name": "Setup",      "id": "setup"},
        {"name": "Score",      "id": "score",   "type": "numeric"},
        {"name": "Price",      "id": "price"},
        {"name": "Funding",    "id": "funding"},
        {"name": "OI 1h%",     "id": "oi_1h"},
        {"name": "OI Dir",     "id": "oi_dir"},
        {"name": "Vol",        "id": "vol"},
        {"name": "ADX",        "id": "adx"},
        {"name": "PD High %",  "id": "pdh_dist"},
        {"name": "PD Low %",   "id": "pdl_dist"},
    ]

    mo_setup  = "MO_LONG"  if direction == "LONG" else "MO_SHORT"
    mr_setup  = "MR_LONG"  if direction == "LONG" else "MR_SHORT"
    setup_color = C["mo_long"] if direction == "LONG" else C["mo_short"]
    mr_color    = C["mr_long"] if direction == "LONG" else C["mr_short"]

    style_data_conditional = [
        # Repeat entries
        {"if": {"filter_query": '{#} != "1"'},
         "backgroundColor": C["card"], "fontStyle": "italic"},
        # Setup colours
        {"if": {"filter_query": f'{{setup}} = "{mo_setup}"', "column_id": "setup"},
         "color": setup_color, "fontWeight": "700"},
        {"if": {"filter_query": f'{{setup}} = "{mr_setup}"', "column_id": "setup"},
         "color": mr_color, "fontWeight": "700"},
        # Score shading
        {"if": {"filter_query": "{score} >= 70", "column_id": "score"},
         "color": C["green"], "fontWeight": "700"},
        {"if": {"filter_query": "{score} >= 55 && {score} < 70", "column_id": "score"},
         "color": C["mr_short"]},
        {"if": {"filter_query": "{score} < 55", "column_id": "score"},
         "color": C["dim"]},
        # Anchors bold
        {"if": {"filter_query": '{symbol} = "BTCUSDT" || {symbol} = "ETHUSDT"',
                "column_id": "symbol"},
         "fontWeight": "700", "color": C["text"]},
        # Positive OI 1h
        {"if": {"filter_query": '{oi_1h} contains "+"', "column_id": "oi_1h"},
         "color": C["green"]},
        {"if": {"filter_query": '{oi_1h} contains "-"', "column_id": "oi_1h"},
         "color": C["red"]},
        # Repeat count highlight
        {"if": {"filter_query": '{#} != "1"', "column_id": "#"},
         "color": C["mr_short"], "fontWeight": "700"},
    ]

    table = dash_table.DataTable(
        data=rows,
        columns=columns,
        sort_action="native",
        sort_by=[{"column_id": "score", "direction": "desc"}],
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": C["card"],
            "color": C["dim"],
            "fontWeight": "600",
            "fontSize": "12px",
            "textTransform": "uppercase",
            "letterSpacing": "0.06em",
            "border": f"1px solid {C['border']}",
            "padding": "10px 12px",
            "fontFamily": FONT,
        },
        style_data={
            "backgroundColor": C["bg"],
            "color": C["text"],
            "fontSize": "14px",
            "border": f"1px solid {C['border']}",
            "padding": "8px 12px",
            "fontFamily": MONO,
        },
        style_cell={"textAlign": "left"},
        style_data_conditional=style_data_conditional,
        page_size=50,
    )

    if not rows:
        table = html.Div(
            f"No active {direction.lower()} signals.",
            style={"color": C["dim"], "padding": "24px", "textAlign": "center",
                   "fontSize": "14px", "fontFamily": FONT}
        )

    return table


# ── 4h Candlestick chart ──────────────────────────────────────────────────

def _chart_4h(symbol):
    candles = store.get_candles_4h(symbol)
    signals = get_signals()
    sig = next((s for s in signals if s["symbol"] == symbol), None)

    fig = go.Figure()

    if candles:
        fig.add_trace(go.Candlestick(
            x=[c["time"] for c in candles],
            open=[c["open"] for c in candles],
            high=[c["high"] for c in candles],
            low=[c["low"]  for c in candles],
            close=[c["close"] for c in candles],
            name=symbol,
            increasing_line_color=C["green"],
            decreasing_line_color=C["red"],
            increasing_fillcolor="rgba(63,185,80,0.35)",
            decreasing_fillcolor="rgba(248,81,73,0.35)",
        ))

    if sig:
        support    = sig.get("support")
        resistance = sig.get("resistance")
        vwap       = sig.get("vwap")
        upper1     = sig.get("vwap_upper1")
        lower1     = sig.get("vwap_lower1")

        if support:
            fig.add_hline(y=support, line_color=C["green"], line_dash="dash", line_width=1,
                          annotation_text=f"S {_fmt_price(support)}",
                          annotation_font_color=C["green"])
        if resistance:
            fig.add_hline(y=resistance, line_color=C["red"], line_dash="dash", line_width=1,
                          annotation_text=f"R {_fmt_price(resistance)}",
                          annotation_font_color=C["red"])
        if vwap:
            fig.add_hline(y=vwap, line_color=C["neutral"], line_dash="dot", line_width=1,
                          annotation_text="VWAP", annotation_font_color=C["neutral"])
        if upper1:
            fig.add_hline(y=upper1, line_color=C["ranging"], line_dash="dot", line_width=1,
                          annotation_text="+1σ", annotation_font_color=C["ranging"])
        if lower1:
            fig.add_hline(y=lower1, line_color=C["ranging"], line_dash="dot", line_width=1,
                          annotation_text="−1σ", annotation_font_color=C["ranging"])

    fig.update_layout(
        title=dict(text=f"{symbol} — 4H", font=dict(color=C["dim"], size=13)),
        paper_bgcolor=C["card"],
        plot_bgcolor=C["bg"],
        font=dict(color=C["text"], size=12),
        xaxis=dict(showgrid=False, color=C["dim"], rangeslider_visible=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(48,54,61,0.4)", color=C["dim"]),
        margin=dict(l=10, r=10, t=36, b=10),
        height=300,
        showlegend=False,
    )
    return fig


# ── Top movers strip ──────────────────────────────────────────────────────

def _top_movers_strip():
    movers = store.get_top_movers()
    if not movers:
        return html.Div()

    gainers = [m for m in movers if m["price_change_pct"] >= 0][:5]
    losers  = [m for m in movers if m["price_change_pct"] < 0][:5]

    def card(m, is_gain):
        color = C["green"] if is_gain else C["red"]
        return html.Div([
            html.Div(m["symbol"].replace("USDT", ""),
                     style={"fontWeight": "700", "fontSize": "13px", "color": C["text"],
                            "fontFamily": FONT}),
            html.Div(_fmt_pct(m["price_change_pct"]),
                     style={"color": color, "fontWeight": "700", "fontSize": "14px",
                            "fontFamily": MONO}),
        ], style={"background": C["card"], "border": f"1px solid {color}44",
                  "borderRadius": "6px", "padding": "8px 12px",
                  "textAlign": "center", "minWidth": "90px"})

    return html.Div([
        html.Div("TOP MOVERS (24H)", style={**LABEL_TEXT, "marginBottom": "8px"}),
        html.Div([
            html.Div("▲ GAINERS", style={**LABEL_TEXT, "color": C["green"], "marginBottom": "6px"}),
            html.Div([card(m, True) for m in gainers],
                     style={"display": "flex", "gap": "8px", "flexWrap": "wrap"}),
        ], style={"marginBottom": "10px"}),
        html.Div([
            html.Div("▼ LOSERS", style={**LABEL_TEXT, "color": C["red"], "marginBottom": "6px"}),
            html.Div([card(m, False) for m in losers],
                     style={"display": "flex", "gap": "8px", "flexWrap": "wrap"}),
        ]),
    ], style={"background": C["card"], "border": f"1px solid {C['border']}",
              "borderRadius": "8px", "padding": "14px", "marginBottom": "14px"})


# ── App layout ────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    title="My Dashboard (Beta)",
    update_title=None,
)

_header = html.Div([
    dbc.Row([
        dbc.Col(
            html.Div("My Dashboard (Beta)",
                     style={"color": C["text"], "fontWeight": "800", "fontSize": "20px",
                            "letterSpacing": "0.08em", "fontFamily": FONT}),
            width="auto",
        ),
        dbc.Col(
            html.Div(id="header-status",
                     style={"color": C["dim"], "fontSize": "13px",
                            "textAlign": "right", "fontFamily": MONO}),
        ),
    ], align="center"),
], style={"background": C["card"], "border": f"1px solid {C['border']}",
          "borderRadius": "8px", "padding": "12px 18px", "marginBottom": "14px"})

app.layout = html.Div([
    dcc.Interval(id="refresh-interval", interval=REFRESH_ACTIVE, n_intervals=0),

    _header,

    dbc.Tabs([

        # ── Main tab ──────────────────────────────────────────────────
        dbc.Tab(label="Main", tab_id="main", children=[
            html.Div(style={"height": "14px"}),
            html.Div(id="macro-strip"),
            html.Div(id="anchor-row", style={"marginBottom": "14px"}),
            html.Div(id="top-movers"),
            dbc.Row([
                dbc.Col(dcc.Graph(id="chart-btc", config={"displayModeBar": False}),
                        md=6, style={"padding": "0 4px"}),
                dbc.Col(dcc.Graph(id="chart-eth", config={"displayModeBar": False}),
                        md=6, style={"padding": "0 4px"}),
            ], style={"margin": "0"}),
        ]),

        # ── Longs tab ─────────────────────────────────────────────────
        dbc.Tab(label="Longs", tab_id="longs", children=[
            html.Div(style={"height": "14px"}),
            dbc.Row([
                dbc.Col(
                    html.Div([
                        html.Div("LONG SIGNALS",
                                 style={**LABEL_TEXT, "marginBottom": "10px"}),
                        html.Div(id="longs-table"),
                    ], style={"background": C["card"], "border": f"1px solid {C['border']}",
                              "borderRadius": "8px", "padding": "16px"}),
                    md=9, style={"padding": "0 4px"},
                ),
                html.Div(id="longs-overview"),
            ], style={"margin": "0"}),
        ]),

        # ── Shorts tab ────────────────────────────────────────────────
        dbc.Tab(label="Shorts", tab_id="shorts", children=[
            html.Div(style={"height": "14px"}),
            dbc.Row([
                dbc.Col(
                    html.Div([
                        html.Div("SHORT SIGNALS",
                                 style={**LABEL_TEXT, "marginBottom": "10px"}),
                        html.Div(id="shorts-table"),
                    ], style={"background": C["card"], "border": f"1px solid {C['border']}",
                              "borderRadius": "8px", "padding": "16px"}),
                    md=9, style={"padding": "0 4px"},
                ),
                html.Div(id="shorts-overview"),
            ], style={"margin": "0"}),
        ]),

    ], id="tabs", active_tab="main",
       style={"borderBottom": f"1px solid {C['border']}", "marginBottom": "0"}),

], style={"background": C["bg"], "minHeight": "100vh", "padding": "16px",
          "fontFamily": FONT})


# ── Callbacks ─────────────────────────────────────────────────────────────

@app.callback(
    Output("header-status", "children"),
    Output("macro-strip",   "children"),
    Output("anchor-row",    "children"),
    Output("top-movers",    "children"),
    Output("chart-btc",     "figure"),
    Output("chart-eth",     "figure"),
    Output("refresh-interval", "interval"),
    Input("refresh-interval",  "n_intervals"),
)
def refresh_main(_n):
    now      = datetime.now(_tz)
    session  = _in_session()
    interval = REFRESH_ACTIVE if session else REFRESH_OFFHOURS

    status = html.Span([
        html.Span(now.strftime("%H:%M:%S %Z"), style={"marginRight": "16px"}),
        html.Span(
            "● SESSION ACTIVE" if session else "○ OFF-HOURS",
            style={"color": C["green"] if session else C["dim"], "fontWeight": "600"},
        ),
    ])

    anchor_row = dbc.Row([
        _anchor_card("BTCUSDT"),
        _anchor_card("ETHUSDT"),
    ], style={"margin": "0 0 14px 0"})

    return (
        status,
        _macro_strip(),
        anchor_row,
        _top_movers_strip(),
        _chart_4h("BTCUSDT"),
        _chart_4h("ETHUSDT"),
        interval,
    )


@app.callback(
    Output("longs-table",    "children"),
    Output("longs-overview", "children"),
    Input("refresh-interval", "n_intervals"),
)
def refresh_longs(_n):
    return _signals_table("LONG"), _overview_card("LONG")


@app.callback(
    Output("shorts-table",    "children"),
    Output("shorts-overview", "children"),
    Input("refresh-interval", "n_intervals"),
)
def refresh_shorts(_n):
    return _signals_table("SHORT"), _overview_card("SHORT")


# ── Start ─────────────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 8050):
    def _run():
        log.info(f"Dashboard starting on {host}:{port}")
        app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="dashboard")
    t.start()
    log.info(f"Dashboard thread launched — http://{host}:{port}")

"""
Stage 3 — Plotly Dash web dashboard.

Layout:
  ┌─ Header bar: title | last update | session status ──────────┐
  ├─ Macro strip: F&G | VIX | FOMC | ETF flow | active symbols ─┤
  ├─ Anchor row: BTC card | ETH card | summary stats ────────────┤
  ├─ Signals table (all qualifying symbols, sorted by score) ────┤
  └─ Charts row: BTC 4h candlestick | ETH 4h candlestick ────────┘

Refresh: 60s during session (08:00–23:00 WET), 900s off-hours.
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
from classifiers.engine import get_signals

log = logging.getLogger(__name__)

_tz = pytz.timezone(SESSION_TZ)

# ── Palette ───────────────────────────────────────────────────────────────

C = {
    "bg":        "#0d1117",
    "card":      "#161b22",
    "border":    "#30363d",
    "text":      "#e6edf3",
    "dim":       "#8b949e",
    "mo_long":   "#3fb950",
    "mo_short":  "#f85149",
    "mr_long":   "#58a6ff",
    "mr_short":  "#d29922",
    "trigger":   "#ff9500",
    "trending":  "#3fb950",
    "ranging":   "#58a6ff",
    "unclear":   "#8b949e",
    "green":     "#3fb950",
    "red":       "#f85149",
    "neutral":   "#8b949e",
}

SETUP_COLOR = {
    "MO_LONG":  C["mo_long"],
    "MO_SHORT": C["mo_short"],
    "MR_LONG":  C["mr_long"],
    "MR_SHORT": C["mr_short"],
}

REFRESH_ACTIVE   = 60_000    # ms — session hours
REFRESH_OFFHOURS = 900_000   # ms — off-hours


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
    return html.Span(f"{pct:+.4f}%", style={"color": color})


def _regime_badge(regime):
    color = {"TRENDING": C["trending"], "RANGING": C["ranging"]}.get(regime, C["unclear"])
    return html.Span(
        regime or "—",
        style={
            "background": color + "22",
            "color": color,
            "border": f"1px solid {color}55",
            "borderRadius": "4px",
            "padding": "1px 6px",
            "fontSize": "11px",
            "fontWeight": "600",
        },
    )


def _setup_badge(setup):
    color = SETUP_COLOR.get(setup, C["dim"])
    return html.Span(
        setup or "—",
        style={
            "background": color + "22",
            "color": color,
            "border": f"1px solid {color}55",
            "borderRadius": "4px",
            "padding": "1px 6px",
            "fontSize": "11px",
            "fontWeight": "700",
        },
    )


def _score_bar(score):
    if score is None:
        return "—"
    color = C["green"] if score >= 70 else (C["mr_short"] if score >= 55 else C["dim"])
    return html.Div([
        html.Span(f"{score:.0f}", style={"color": color, "fontWeight": "700", "marginRight": "6px"}),
        html.Div(
            html.Div(style={
                "width": f"{score}%",
                "height": "4px",
                "background": color,
                "borderRadius": "2px",
            }),
            style={"width": "60px", "background": C["border"], "borderRadius": "2px", "display": "inline-block", "verticalAlign": "middle"},
        ),
    ], style={"display": "flex", "alignItems": "center"})


# ── Macro strip ───────────────────────────────────────────────────────────

def _macro_strip():
    macro = store.get_macro()
    fg    = macro.get("fear_greed")
    fgl   = macro.get("fear_greed_label", "")
    vix   = macro.get("vix")
    fomc  = macro.get("fomc_next")
    days  = macro.get("fomc_days_away")
    etf   = macro.get("etf_flow_24h")

    # Fear & Greed colour
    if fg is not None:
        fg_color = C["red"] if fg < 30 else (C["green"] if fg > 60 else C["mr_short"])
    else:
        fg_color = C["dim"]

    items = [
        ("F&G", f"{fg} — {fgl}" if fg is not None else "—", fg_color),
        ("VIX", f"{vix:.1f}" if vix else "—", C["mr_short"] if (vix or 0) > 25 else C["text"]),
        ("FOMC", f"{fomc} ({days}d)" if fomc else "—", C["mr_short"] if (days or 99) <= 7 else C["text"]),
        ("ETF Flow", f"${etf:+,.0f}M" if etf is not None else "N/A", C["green"] if (etf or 0) > 0 else C["red"]),
        ("Symbols", f"{len(store.get_active_symbols())} tracked", C["text"]),
    ]

    cards = []
    for label, value, color in items:
        cards.append(
            dbc.Col(
                html.Div([
                    html.Div(label, style={"fontSize": "10px", "color": C["dim"], "textTransform": "uppercase", "letterSpacing": "0.08em"}),
                    html.Div(value, style={"fontSize": "13px", "color": color, "fontWeight": "600", "marginTop": "2px"}),
                ], style={"background": C["card"], "border": f"1px solid {C['border']}", "borderRadius": "6px", "padding": "8px 12px"}),
                style={"padding": "0 4px"},
            )
        )
    return dbc.Row(cards, style={"margin": "0 0 12px 0"})


# ── Anchor card ───────────────────────────────────────────────────────────

def _anchor_card(symbol):
    signals = get_signals()
    sig = next((s for s in signals if s["symbol"] == symbol), None)

    price     = _fmt_price(sig.get("price") if sig else None)
    regime    = sig.get("regime", "—") if sig else "—"
    setup     = sig.get("setup") if sig else None
    score     = sig.get("score") if sig else None
    vwap_pct  = sig.get("vwap_pct") if sig else None
    funding   = sig.get("funding") if sig else None
    support   = _fmt_price(sig.get("support") if sig else None)
    resistance= _fmt_price(sig.get("resistance") if sig else None)
    trigger   = sig.get("trigger_label", "") if sig else ""
    adx       = sig.get("adx") if sig else None

    regime_color = {"TRENDING": C["trending"], "RANGING": C["ranging"]}.get(regime, C["dim"])

    rows = [
        ("Price",       html.Span(price, style={"color": C["text"], "fontWeight": "700", "fontSize": "16px"})),
        ("Regime",      html.Span(f"{regime}  ADX {adx:.0f}" if adx else regime, style={"color": regime_color, "fontWeight": "600"})),
        ("Setup",       _setup_badge(setup)),
        ("Score",       _score_bar(score)),
        ("VWAP dist",   html.Span(_fmt_pct(vwap_pct), style={"color": C["green"] if (vwap_pct or 0) > 0 else C["red"]})),
        ("Support",     html.Span(support, style={"color": C["green"]})),
        ("Resistance",  html.Span(resistance, style={"color": C["red"]})),
        ("Funding",     _fmt_funding(funding)),
    ]

    body = [
        html.Div([
            html.Span(label, style={"color": C["dim"], "fontSize": "11px", "width": "80px", "display": "inline-block"}),
            html.Span([value] if not isinstance(value, list) else value),
        ], style={"marginBottom": "5px"})
        for label, value in rows
    ]

    if trigger:
        body.insert(0,
            html.Div(trigger, style={
                "background": C["trigger"] + "22",
                "border": f"1px solid {C['trigger']}",
                "color": C["trigger"],
                "borderRadius": "4px",
                "padding": "4px 8px",
                "fontSize": "11px",
                "fontWeight": "700",
                "marginBottom": "8px",
                "textAlign": "center",
            })
        )

    return dbc.Col(
        html.Div([
            html.Div(symbol, style={"color": C["text"], "fontWeight": "700", "fontSize": "13px", "marginBottom": "10px",
                                     "borderBottom": f"1px solid {C['border']}", "paddingBottom": "6px"}),
            *body,
        ], style={"background": C["card"], "border": f"1px solid {C['border']}", "borderRadius": "8px", "padding": "14px"}),
        md=4, style={"padding": "0 4px"},
    )


def _summary_card():
    signals  = get_signals()
    viable   = [s for s in signals if s.get("viable")]
    triggered= [s for s in signals if s.get("trigger")]
    trending = sum(1 for s in signals if s.get("regime") == "TRENDING")
    ranging  = sum(1 for s in signals if s.get("regime") == "RANGING")

    mo_long  = sum(1 for s in viable if s.get("setup") == "MO_LONG")
    mo_short = sum(1 for s in viable if s.get("setup") == "MO_SHORT")
    mr_long  = sum(1 for s in viable if s.get("setup") == "MR_LONG")
    mr_short = sum(1 for s in viable if s.get("setup") == "MR_SHORT")

    def stat(label, val, color):
        return html.Div([
            html.Span(str(val), style={"color": color, "fontWeight": "700", "fontSize": "20px"}),
            html.Span(f"  {label}", style={"color": C["dim"], "fontSize": "11px"}),
        ], style={"marginBottom": "6px"})

    return dbc.Col(
        html.Div([
            html.Div("OVERVIEW", style={"color": C["dim"], "fontSize": "10px", "letterSpacing": "0.1em", "marginBottom": "10px",
                                         "borderBottom": f"1px solid {C['border']}", "paddingBottom": "6px"}),
            stat("MO Long viable",  mo_long,   C["mo_long"]),
            stat("MO Short viable", mo_short,  C["mo_short"]),
            stat("MR Long viable",  mr_long,   C["mr_long"]),
            stat("MR Short viable", mr_short,  C["mr_short"]),
            html.Hr(style={"borderColor": C["border"], "margin": "8px 0"}),
            stat("Triggered",  len(triggered), C["trigger"]),
            stat("Trending",   trending,        C["trending"]),
            stat("Ranging",    ranging,         C["ranging"]),
        ], style={"background": C["card"], "border": f"1px solid {C['border']}", "borderRadius": "8px", "padding": "14px"}),
        md=4, style={"padding": "0 4px"},
    )


# ── Signals table ─────────────────────────────────────────────────────────

def _signals_table():
    signals = get_signals(viable_only=False)

    rows = []
    for s in signals:
        trigger = s.get("trigger_label", "")
        regime  = s.get("regime", "")
        setup   = s.get("setup", "")
        score   = s.get("score")
        vwap_p  = s.get("vwap_pct")
        funding = s.get("funding")
        oi_dir  = s.get("oi_direction", "")
        vol_t   = s.get("vol_trend", "")

        rows.append({
            "symbol":   s.get("symbol", ""),
            "regime":   regime,
            "setup":    setup or "—",
            "score":    round(score, 0) if score is not None else 0,
            "viable":   "YES" if s.get("viable") else "no",
            "trigger":  trigger or "—",
            "price":    _fmt_price(s.get("price")),
            "vwap%":    _fmt_pct(vwap_p) if vwap_p is not None else "—",
            "funding%": f"{funding*100:+.4f}%" if funding is not None else "—",
            "oi":       oi_dir or "—",
            "vol":      vol_t or "—",
            "support":  _fmt_price(s.get("support")),
            "resist":   _fmt_price(s.get("resistance")),
        })

    columns = [
        {"name": "Symbol",    "id": "symbol"},
        {"name": "Regime",    "id": "regime"},
        {"name": "Setup",     "id": "setup"},
        {"name": "Score",     "id": "score",   "type": "numeric"},
        {"name": "Viable",    "id": "viable"},
        {"name": "Trigger",   "id": "trigger"},
        {"name": "Price",     "id": "price"},
        {"name": "VWAP %",    "id": "vwap%"},
        {"name": "Funding",   "id": "funding%"},
        {"name": "OI",        "id": "oi"},
        {"name": "Vol",       "id": "vol"},
        {"name": "Support",   "id": "support"},
        {"name": "Resist",    "id": "resist"},
    ]

    style_data_conditional = [
        # Triggered rows — highlight row
        {"if": {"filter_query": '{trigger} != "—"'},
         "backgroundColor": C["trigger"] + "11", "border": f"1px solid {C['trigger']}33"},
        # Viable YES
        {"if": {"filter_query": '{viable} = "YES"', "column_id": "viable"},
         "color": C["green"], "fontWeight": "700"},
        # Setup colours
        {"if": {"filter_query": '{setup} = "MO_LONG"',  "column_id": "setup"}, "color": C["mo_long"],  "fontWeight": "700"},
        {"if": {"filter_query": '{setup} = "MO_SHORT"', "column_id": "setup"}, "color": C["mo_short"], "fontWeight": "700"},
        {"if": {"filter_query": '{setup} = "MR_LONG"',  "column_id": "setup"}, "color": C["mr_long"],  "fontWeight": "700"},
        {"if": {"filter_query": '{setup} = "MR_SHORT"', "column_id": "setup"}, "color": C["mr_short"], "fontWeight": "700"},
        # Regime colours
        {"if": {"filter_query": '{regime} = "TRENDING"', "column_id": "regime"}, "color": C["trending"]},
        {"if": {"filter_query": '{regime} = "RANGING"',  "column_id": "regime"}, "color": C["ranging"]},
        # Score colouring
        {"if": {"filter_query": "{score} >= 70", "column_id": "score"}, "color": C["green"],    "fontWeight": "700"},
        {"if": {"filter_query": "{score} >= 55 && {score} < 70", "column_id": "score"}, "color": C["mr_short"]},
        {"if": {"filter_query": "{score} < 55",  "column_id": "score"}, "color": C["dim"]},
        # Anchors — bold symbol
        {"if": {"filter_query": '{symbol} = "BTCUSDT" || {symbol} = "ETHUSDT"', "column_id": "symbol"},
         "fontWeight": "700", "color": C["text"]},
        # Trigger column
        {"if": {"filter_query": '{trigger} != "—"', "column_id": "trigger"},
         "color": C["trigger"], "fontWeight": "700"},
    ]

    return dash_table.DataTable(
        data=rows,
        columns=columns,
        sort_action="native",
        sort_by=[{"column_id": "score", "direction": "desc"}],
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": C["card"],
            "color": C["dim"],
            "fontWeight": "600",
            "fontSize": "11px",
            "textTransform": "uppercase",
            "letterSpacing": "0.06em",
            "border": f"1px solid {C['border']}",
            "padding": "8px 10px",
        },
        style_data={
            "backgroundColor": C["bg"],
            "color": C["text"],
            "fontSize": "12px",
            "border": f"1px solid {C['border']}",
            "padding": "6px 10px",
        },
        style_cell={"textAlign": "left", "fontFamily": "monospace"},
        style_data_conditional=style_data_conditional,
        page_size=50,
        id="signals-table",
    )


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
        if support:
            fig.add_hline(y=support,    line_color=C["green"], line_dash="dash", line_width=1,
                          annotation_text=f"S {_fmt_price(support)}", annotation_font_color=C["green"])
        if resistance:
            fig.add_hline(y=resistance, line_color=C["red"],   line_dash="dash", line_width=1,
                          annotation_text=f"R {_fmt_price(resistance)}", annotation_font_color=C["red"])

    fig.update_layout(
        title=dict(text=f"{symbol} — 4H", font=dict(color=C["dim"], size=12)),
        paper_bgcolor=C["card"],
        plot_bgcolor=C["bg"],
        font=dict(color=C["text"], size=11),
        xaxis=dict(showgrid=False, color=C["dim"], rangeslider_visible=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(48,54,61,0.4)", color=C["dim"]),
        margin=dict(l=10, r=10, t=30, b=10),
        height=280,
        showlegend=False,
    )
    return fig


# ── App layout ────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    title="MKT-DASH",
    update_title=None,
)

app.layout = html.Div([
    dcc.Interval(id="refresh-interval", interval=REFRESH_ACTIVE, n_intervals=0),

    # ── Header ────────────────────────────────────────────────────────
    html.Div([
        dbc.Row([
            dbc.Col(
                html.Div("MKT-DASH", style={"color": C["text"], "fontWeight": "800", "fontSize": "18px", "letterSpacing": "0.15em"}),
                width="auto",
            ),
            dbc.Col(
                html.Div(id="header-status", style={"color": C["dim"], "fontSize": "12px", "textAlign": "right"}),
            ),
        ], align="center"),
    ], style={"background": C["card"], "border": f"1px solid {C['border']}", "borderRadius": "8px",
              "padding": "10px 16px", "marginBottom": "12px"}),

    # ── Macro strip ───────────────────────────────────────────────────
    html.Div(id="macro-strip"),

    # ── Anchor row ────────────────────────────────────────────────────
    html.Div(id="anchor-row", style={"marginBottom": "12px"}),

    # ── Signals table ─────────────────────────────────────────────────
    html.Div([
        html.Div("SIGNALS", style={"color": C["dim"], "fontSize": "10px", "fontWeight": "600",
                                    "letterSpacing": "0.1em", "marginBottom": "8px"}),
        html.Div(id="signals-container"),
    ], style={"background": C["card"], "border": f"1px solid {C['border']}", "borderRadius": "8px",
              "padding": "14px", "marginBottom": "12px"}),

    # ── Charts ────────────────────────────────────────────────────────
    dbc.Row([
        dbc.Col(dcc.Graph(id="chart-btc", config={"displayModeBar": False}), md=6, style={"padding": "0 4px"}),
        dbc.Col(dcc.Graph(id="chart-eth", config={"displayModeBar": False}), md=6, style={"padding": "0 4px"}),
    ], style={"margin": "0"}),

], style={"background": C["bg"], "minHeight": "100vh", "padding": "16px", "fontFamily": "'Inter', 'Segoe UI', monospace"})


# ── Callbacks ─────────────────────────────────────────────────────────────

@app.callback(
    Output("header-status",    "children"),
    Output("macro-strip",      "children"),
    Output("anchor-row",       "children"),
    Output("signals-container","children"),
    Output("chart-btc",        "figure"),
    Output("chart-eth",        "figure"),
    Output("refresh-interval", "interval"),
    Input("refresh-interval",  "n_intervals"),
)
def refresh(_n):
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
        _summary_card(),
    ], style={"margin": "0 0 12px 0"})

    return (
        status,
        _macro_strip(),
        anchor_row,
        _signals_table(),
        _chart_4h("BTCUSDT"),
        _chart_4h("ETHUSDT"),
        interval,
    )


# ── Start ─────────────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 8050):
    """Launch Dash in a background daemon thread."""
    def _run():
        log.info(f"Dashboard starting on {host}:{port}")
        app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="dashboard")
    t.start()
    log.info(f"Dashboard thread launched — http://{host}:{port}")

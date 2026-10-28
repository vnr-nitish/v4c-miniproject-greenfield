"""The app's look: page CSS (cards, coloured tiles) and a matching Plotly template.

Colours carry meaning everywhere: each department has its own colour, risk bands go from green
(low) to red (very high), heatmaps use one sequential scale.
"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

PALETTE = ["#6366F1", "#14B8A6", "#F59E0B", "#EC4899", "#3B82F6", "#84CC16", "#F97316"]
DEPARTMENT_COLORS = {"Human Resources": "#EC4899", "Research & Development": "#6366F1", "Sales": "#14B8A6"}
BAND_COLORS = {"Low": "#22C55E", "Medium": "#3B82F6", "High": "#F59E0B", "Very high": "#EF4444"}
PRIORITY_COLORS = {"Low": "#22C55E", "Medium": "#3B82F6", "High": "#F59E0B", "Critical": "#EF4444"}
RISK_SCALE = ["#22C55E", "#FACC15", "#F97316", "#EF4444"]
SEQ_SCALE = ["#1E293B", "#3B82F6", "#14B8A6", "#FACC15"]
TILE_COLORS = ["#6366F1", "#14B8A6", "#F59E0B", "#EC4899", "#3B82F6", "#84CC16", "#F97316"]
TEXT, MUTED, GRID = "#E5E7EB", "#94A3B8", "rgba(148,163,184,0.16)"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;800&display=swap');
html, body, [class*="css"], .stMarkdown { font-family: 'Plus Jakarta Sans', sans-serif; }
.stApp { background: #0B1220; }
.block-container { padding-top: 2rem; max-width: 1400px; }
.hero { padding: 1.6rem 1.8rem; border-radius: 20px; margin-bottom: 1.2rem; position: relative; overflow: hidden;
        background: linear-gradient(115deg, #312E81 0%, #0F766E 55%, #B45309 120%); }
.hero h1 { margin: 0; font-size: 2rem; font-weight: 800; color: #fff; }
.hero p { margin: .35rem 0 0; color: rgba(255,255,255,.82); font-size: 1rem; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: .9rem; margin: .2rem 0 1.2rem; }
.tile { padding: 1rem 1.1rem; border-radius: 16px; background: #111A2E; border: 1px solid rgba(148,163,184,.14);
        border-top: 4px solid var(--c); transition: transform .2s ease, box-shadow .2s ease; }
.tile:hover { transform: translateY(-3px); box-shadow: 0 12px 28px rgba(0,0,0,.35); }
.tile .label { color: #94A3B8; font-size: .75rem; text-transform: uppercase; letter-spacing: .07em; font-weight: 600; }
.tile .value { font-size: 1.75rem; font-weight: 800; color: #F8FAFC; margin-top: .15rem; }
.delta-up { color: #22C55E; font-size: .82rem; } .delta-down { color: #EF4444; font-size: .82rem; }
.delta-flat { color: #94A3B8; font-size: .82rem; }
.note { padding: .75rem 1rem; border-left: 4px solid #14B8A6; background: rgba(20,184,166,.08);
        border-radius: 10px; color: #CBD5E1; font-size: .9rem; margin: .6rem 0 1.1rem; }
.medals { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: .9rem; margin-bottom: 1rem; }
.medal { padding: 1rem 1.1rem; border-radius: 16px; background: #111A2E; border: 1px solid rgba(148,163,184,.14);
         border-left: 5px solid var(--c); }
.medal h4 { margin: 0 0 .5rem; color: var(--c); }
.medal div { margin: .25rem 0; color: #E5E7EB; } .medal small { color: #94A3B8; }
.scd-old { border: 1px solid #EF4444; border-radius: 12px; padding: .6rem 1rem; background: rgba(239,68,68,.08); }
.scd-new { border: 1px solid #22C55E; border-radius: 12px; padding: .6rem 1rem; background: rgba(34,197,94,.08); }
div[data-testid="stForm"] { border-radius: 16px; border-color: rgba(148,163,184,.2); background: #0F172A; }
div[data-testid="stMetric"] { background: #111A2E; border-radius: 14px; padding: .6rem .9rem;
                              border: 1px solid rgba(148,163,184,.14); }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def _register_template() -> None:
    axis = dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, color=MUTED)
    pio.templates["hr_dark"] = go.layout.Template(layout=dict(
        paper_bgcolor="#111A2E", plot_bgcolor="#111A2E", colorway=PALETTE,
        font=dict(family="Plus Jakarta Sans, sans-serif", color=TEXT), xaxis=axis, yaxis=axis,
        legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=-0.15), margin=dict(l=16, r=16, t=56, b=16),
        hoverlabel=dict(bgcolor="#0B1220", bordercolor="#14B8A6", font=dict(color=TEXT))))
    pio.templates.default = "plotly_dark+hr_dark"


_register_template()

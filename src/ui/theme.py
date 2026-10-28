"""The app's look: page CSS (glass cards, tilt tiles) and a matching Plotly template."""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

ACCENT = "#8B7CFF"
PALETTE = ["#8B7CFF", "#2DD4BF", "#F472B6", "#FBBF24", "#60A5FA", "#A3E635", "#FB923C"]
BAND_COLORS = {"Low": "#2DD4BF", "Medium": "#60A5FA", "High": "#FBBF24", "Very high": "#F43F5E"}
TEXT, MUTED, GRID = "#E6E9F5", "#9AA3BF", "rgba(154,163,191,0.18)"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText { font-family: 'Space Grotesk', sans-serif; }
.stApp { background: radial-gradient(1200px 600px at 10% -10%, #1d1b4b 0%, transparent 60%),
                     radial-gradient(900px 500px at 110% 10%, #0f3d3e 0%, transparent 55%), #0A0E1A; }
.hero { padding: 1.1rem 1.4rem; border-radius: 18px; margin-bottom: 1rem;
        background: linear-gradient(120deg, rgba(139,124,255,.22), rgba(45,212,191,.10));
        border: 1px solid rgba(139,124,255,.35); }
.hero h1 { margin: 0; font-size: 1.9rem; background: linear-gradient(90deg, #C4B5FD, #5EEAD4);
           -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.hero p { margin: .3rem 0 0; color: #9AA3BF; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: .8rem; margin: .4rem 0 1rem; }
.tile { perspective: 600px; }
.tile-inner { padding: .9rem 1rem; border-radius: 16px; background: rgba(20,27,45,.72);
              border: 1px solid rgba(154,163,191,.18); backdrop-filter: blur(8px);
              transition: transform .35s ease, box-shadow .35s ease; transform-style: preserve-3d; }
.tile:hover .tile-inner { transform: rotateX(8deg) rotateY(-8deg) translateZ(6px);
                          box-shadow: 0 18px 40px rgba(139,124,255,.25); }
.tile .label { color: #9AA3BF; font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; }
.tile .value { font-size: 1.7rem; font-weight: 700; color: #E6E9F5; }
.tile .delta-up { color: #2DD4BF; font-size: .85rem; } .tile .delta-down { color: #F43F5E; font-size: .85rem; }
.tile .delta-flat { color: #9AA3BF; font-size: .85rem; }
.note { padding: .7rem 1rem; border-left: 3px solid #8B7CFF; background: rgba(139,124,255,.08);
        border-radius: 8px; color: #C9CEE0; font-size: .9rem; margin: .5rem 0 1rem; }
.scd-old { border: 1px solid #F43F5E; border-radius: 12px; padding: .7rem 1rem; background: rgba(244,63,94,.07); }
.scd-new { border: 1px solid #2DD4BF; border-radius: 12px; padding: .7rem 1rem; background: rgba(45,212,191,.07); }
div[data-testid="stForm"] { border-radius: 16px; border-color: rgba(154,163,191,.22); background: rgba(20,27,45,.45); }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def _register_template() -> None:
    axis = dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, color=MUTED)
    scene_axis = dict(backgroundcolor="rgba(0,0,0,0)", gridcolor=GRID, zerolinecolor=GRID, color=MUTED,
                      showbackground=True)
    pio.templates["hr_dark"] = go.layout.Template(layout=dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", colorway=PALETTE,
        font=dict(family="Space Grotesk, sans-serif", color=TEXT), xaxis=axis, yaxis=axis,
        scene=dict(xaxis=scene_axis, yaxis=scene_axis, zaxis=scene_axis),
        legend=dict(bgcolor="rgba(0,0,0,0)"), margin=dict(l=10, r=10, t=40, b=10),
        hoverlabel=dict(bgcolor="#141B2D", bordercolor=ACCENT, font=dict(color=TEXT))))
    pio.templates.default = "hr_dark"


_register_template()

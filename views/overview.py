"""Command centre: headline numbers and the 3D Workforce Galaxy."""
import streamlit as st

from src.dal import AnalyticsManager
from src.ui import charts
from src.ui.common import attempt, current_filters, note, page_header, tiles

page_header("HR Analytics Command Centre",
            "100,000 employees · 7 review cycles · live OLTP forms and a star-schema warehouse with SCD Type 2 history")
am = AnalyticsManager()
filters = current_filters()

with st.spinner("Reading the warehouse…"):
    overview = attempt(lambda: am.overview(filters))
if overview:
    tiles([
        ("Active headcount", f"{overview['headcount']:,}", None, True),
        (f"Attrition {overview['attrition_year'] or ''}", f"{overview['attrition_pct'] or 0:.2f} %",
         overview["attrition_change"], False),
        (f"Avg rating {overview['latest_cycle'] or ''}", f"{overview['avg_rating'] or 0:.3f}",
         overview["rating_change"], True),
        ("Avg KPI score", f"{overview['avg_kpi'] or 0:.2f}", overview["kpi_change"], True),
        ("Outstanding reviews", f"{overview['outstanding_pct'] or 0:.1f} %", None, True),
        ("Open projects", f"{overview['open_projects']:,}", None, True),
        ("Overdue projects", f"{overview['overdue_projects']:,}", None, False),
    ])

st.subheader("Workforce Galaxy")
st.caption("Every point is an active employee — tenure × monthly pay × attrition-risk score. Drag to rotate, "
           "scroll to zoom, hover for the person and the factors behind their score.")
with st.spinner("Scoring the workforce (first visit after a data change takes ~20 s)…"):
    workforce = attempt(am.scored_workforce)
if workforce is not None:
    selected = filters.select(workforce)
    sample = selected.groupby("risk_band").sample(frac=min(1.0, 6000 / max(len(selected), 1)), random_state=42)
    st.plotly_chart(charts.workforce_galaxy(sample), width="stretch")
    note(f"Showing a stratified sample of {len(sample):,} of {len(selected):,} employees (same sample for "
         "every visitor) — enough to see the structure without sending 84,000 points to the browser.")

"""Command centre: headline numbers, where the people are, and how risk is spread."""
import streamlit as st

from src.dal import AnalyticsManager
from src.ui import charts, theme
from src.ui.common import attempt, current_filters, note, page_header, tiles

page_header("HR Analytics Command Centre",
            "100,000 employees · 7 review cycles · live forms on the OLTP database and dashboards on the "
            "star-schema warehouse with SCD Type 2 history")
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

with st.spinner("Scoring the workforce (first visit after a data change takes ~20 s)…"):
    workforce = attempt(am.scored_workforce)
if workforce is not None:
    scope = filters.select(workforce)
    left, right = st.columns([2, 1])
    left.plotly_chart(charts.headcount_treemap(scope), width="stretch")
    bands = scope.groupby("risk_band", as_index=False).size().rename(columns={"size": "employees"})
    bands = bands.set_index("risk_band").reindex(list(theme.BAND_COLORS)).dropna().reset_index()
    right.plotly_chart(charts.donut(bands, "risk_band", "employees", theme.BAND_COLORS,
                                    "Attrition-risk bands"), width="stretch")
    per_dept = scope.groupby("department_name", as_index=False).agg(
        employees=("employee_id", "size"), avg_income=("monthly_income", "mean"))
    right.plotly_chart(charts.bars(per_dept, "department_name", "employees", "department_name",
                                   title="Headcount by department", text="employees"), width="stretch")
    note("Click a tile of the treemap to zoom into a department; the colour shows where high attrition risk "
         "is concentrated. Every chart follows the filters in the sidebar.")

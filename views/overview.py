"""Command centre: headline numbers, where the people are, and how risk is spread."""
import pandas as pd
import streamlit as st

from src.dal import AnalyticsFilter, AnalyticsManager
from src.ui import charts, theme
from src.ui.common import attempt, department_filter, page_header, tiles

page_header("HR Analytics Command Centre")
am = AnalyticsManager()
filter_column, _ = st.columns([1, 3])
filters = AnalyticsFilter(department_filter(filter_column))

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

with st.spinner("Reading the warehouse…"):
    workforce = attempt(am.scored_workforce)
if workforce is not None:
    scope = filters.select(workforce)
    # Two rows of two charts, each pair the same height, so the charts line up across the page
    left, right = st.columns([2, 1])
    left.plotly_chart(charts.headcount_treemap(scope), width="stretch")
    bands = scope.groupby("risk_band", as_index=False).size().rename(columns={"size": "employees"})
    bands = bands.set_index("risk_band").reindex(list(theme.BAND_COLORS)).dropna().reset_index()
    right.plotly_chart(charts.donut(bands, "risk_band", "employees", theme.BAND_COLORS,
                                    "Attrition-risk bands"), width="stretch")
    left, right = st.columns([2, 1])
    tenure_labels = ["<1", "1", "2", "3-4", "5-9", "10-19", "20+"]
    by_tenure = (scope.assign(years=pd.cut(scope.tenure_years, [-1, 0, 1, 2, 4, 9, 19, 99], labels=tenure_labels))
                 .groupby(["years", "band_order", "risk_band"], observed=True, as_index=False).size()
                 .rename(columns={"size": "employees"}).sort_values(["years", "band_order"]))
    by_tenure["years"] = by_tenure.years.astype(str)
    left.plotly_chart(charts.bars(by_tenure, "years", "employees", "risk_band", colors=theme.BAND_COLORS,
                                  title="Active employees by years at the company · colour = risk band"),
                      width="stretch")
    per_dept = scope.groupby("department_name", as_index=False).agg(
        employees=("employee_id", "size"), avg_income=("monthly_income", "mean"))
    right.plotly_chart(charts.bars(per_dept, "department_name", "employees", "department_name",
                                   title="Headcount by department", text="employees"), width="stretch")

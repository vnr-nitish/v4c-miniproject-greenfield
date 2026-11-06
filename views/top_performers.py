"""Top performers: the best current employees of each department over the chosen review cycles."""
import streamlit as st

from src.dal import AnalyticsFilter, AnalyticsManager
from src.ui.common import attempt, current_filters, page_header

ROW_HEIGHT = 35

page_header("Top performers")
am, base = AnalyticsManager(), current_filters()
cycles = st.session_state.get("cycles") or []

c1, c2, c3 = st.columns([3, 1, 1])
chosen = c1.multiselect("Review cycles", cycles, default=base.fiscal_years or cycles[-3:])
top_n = c2.number_input("Ranks per department", 1, 20, 5)
most_reviews = len(chosen) or len(cycles) or 1             # one review per employee and cycle
min_reviews = c3.number_input("Minimum reviews", 1, most_reviews, min(2, most_reviews))

filters = AnalyticsFilter(base.departments, base.job_levels, tuple(chosen))
top = attempt(lambda: am.top_performers(filters, top_n=top_n, min_reviews=min_reviews, active_only=True))
if top is not None and len(top):
    departments = sorted(top.department_name.unique())
    for tab, department in zip(st.tabs(departments), departments):
        rows = top[top.department_name == department]
        tab.dataframe(rows[["department_rank", "employee_code", "full_name", "current_role", "job_level", "reviews",
                            "avg_rating", "avg_kpi"]],
                      hide_index=True, width="stretch", height=ROW_HEIGHT * (len(rows) + 1) + 3,
                      column_config={"department_rank": st.column_config.NumberColumn("Rank", format="#%d"),
                                     "employee_code": "Employee ID", "full_name": "Name", "current_role": "Job role",
                                     "job_level": "Level", "reviews": "Reviews", "avg_rating": "Avg rating",
                                     "avg_kpi": st.column_config.NumberColumn("Avg KPI", format="%.2f")})
elif top is not None:
    st.info("No employees match these filters.")

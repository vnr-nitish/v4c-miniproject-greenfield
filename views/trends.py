"""Year-over-year performance trends (LAG over review cycles) with a 3D trend surface."""
import streamlit as st

from src.dal import AnalyticsManager
from src.ui import charts
from src.ui.common import attempt, current_filters, note, page_header, tiles

page_header("Performance trends", "Year-over-year results per review cycle — change computed in SQL with LAG()")
am, filters = AnalyticsManager(), current_filters()

METRICS = {"avg_kpi": ("Average KPI score", "rating_change"), "avg_rating": ("Average rating", "rating_change"),
           "outstanding_pct": ("Outstanding reviews (%)", "outstanding_change"),
           "overtime_pct": ("Overtime (%)", None), "avg_job_satisfaction": ("Job satisfaction (1-4)", None),
           "avg_training_sessions": ("Training sessions", None)}
left, right = st.columns([2, 1])
metric = left.segmented_control("Metric", list(METRICS), format_func=lambda m: METRICS[m][0], default="avg_kpi")
by_department = right.toggle("Compare departments", value=True)
metric = metric or "avg_kpi"

trend = attempt(lambda: am.yoy_trends(filters))
if trend is not None and len(trend):
    last = trend.iloc[-1]
    tiles([(f"{label} · {last.period}", f"{last[m]:,.2f}", (last[m] - trend.iloc[-2][m]) if len(trend) > 1 else None,
            m != "overtime_pct") for m, (label, _) in list(METRICS.items())[:4]])
    frame = attempt(lambda: am.yoy_trends(filters, by_department=True)) if by_department else trend
    if frame is not None:
        st.plotly_chart(charts.lines(frame, "period", metric, "department_name" if by_department else None,
                                     METRICS[metric][0]), width="stretch")
    with st.expander("Year-over-year table (LAG)"):
        st.dataframe(trend, hide_index=True, width="stretch")
    note("Ratings and KPI are steady across cycles by design: every year of the generated history follows the "
         "IBM snapshot (synthesizer D26), so no trend was invented. Real movement shows up in overtime, by job role "
         "and in attrition — explore it in the surface below.")

st.subheader("Trend surface · job role × review cycle")
SURFACE = {"overtime_pct": "Overtime (%)", "kpi": "Average KPI", "outstanding_pct": "Outstanding (%)",
           "job_satisfaction": "Job satisfaction", "training_sessions": "Training sessions"}
surface_metric = st.pills("Surface metric", list(SURFACE), format_func=SURFACE.get, default="overtime_pct") \
    or "overtime_pct"
roles = attempt(lambda: am.role_trends(filters, surface_metric))
if roles is not None and len(roles):
    grid = roles.pivot(index="job_role", columns="period", values="value")
    st.plotly_chart(charts.surface(grid, "Review cycle", "Job role", SURFACE[surface_metric]), width="stretch")

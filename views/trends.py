"""Year-over-year performance trends per review cycle."""
import streamlit as st

from src.dal import AnalyticsManager
from src.ui import charts
from src.ui.common import attempt, current_filters, page_header, tiles

page_header("Performance trends")
am, filters = AnalyticsManager(), current_filters()

METRICS = {"avg_kpi": "Average KPI score", "avg_rating": "Average rating", "outstanding_pct": "Outstanding reviews (%)",
           "overtime_pct": "Overtime (%)", "avg_job_satisfaction": "Job satisfaction (1-4)",
           "avg_training_sessions": "Training sessions"}

trend = attempt(lambda: am.yoy_trends(filters))
if trend is not None and len(trend):
    last = trend.iloc[-1]
    tiles([(f"{label} · {last.period}", f"{last[m]:,.2f}", (last[m] - trend.iloc[-2][m]) if len(trend) > 1 else None,
            m != "overtime_pct") for m, label in list(METRICS.items())[:4]])
    left, right = st.columns([5, 1], vertical_alignment="bottom")
    metric = left.segmented_control("Metric", list(METRICS), format_func=METRICS.get, default="avg_kpi", required=True)
    by_department = right.toggle("Compare departments", value=True)
    frame = attempt(lambda: am.yoy_trends(filters, by_department=True)) if by_department else trend
    if frame is not None:
        st.plotly_chart(charts.lines(frame, "period", metric, "department_name" if by_department else None,
                                     METRICS[metric]), width="stretch")
    with st.expander("Year-over-year table"):
        st.dataframe(trend, hide_index=True, width="stretch")

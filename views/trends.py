"""Year-over-year performance trends per review cycle."""
import streamlit as st

from src.dal import AnalyticsFilter, AnalyticsManager
from src.ui import charts
from src.ui.common import attempt, department_filter, job_level_filter, page_header, tiles

page_header("Performance trends")
# the object that runs the warehouse queries
am = AnalyticsManager()
department_column, level_column, _ = st.columns([1, 1, 2])
# the Department and Job level drop-downs
filters = AnalyticsFilter(department_filter(department_column), job_level_filter(level_column))

# the measures the user can chart
METRICS = {"avg_kpi": "Average KPI score", "avg_rating": "Average rating", "outstanding_pct": "Outstanding reviews (%)",
           "overtime_pct": "Overtime (%)", "avg_job_satisfaction": "Job satisfaction (1-4)",
           "avg_training_sessions": "Training sessions"}

# one row per review cycle with its averages and the change against the previous cycle
trend = attempt(lambda: am.yoy_trends(filters))
if trend is not None and len(trend):
    # the latest cycle, for the four tiles
    last = trend.iloc[-1]
    tiles([(f"{label} · {last.period}", f"{last[m]:,.2f}", (last[m] - trend.iloc[-2][m]) if len(trend) > 1 else None,
            m != "overtime_pct") for m, label in list(METRICS.items())[:4]])
    left, right = st.columns([5, 1], vertical_alignment="bottom")
    # which measure to draw
    metric = left.segmented_control("Metric", list(METRICS), format_func=METRICS.get, default="avg_kpi", required=True)
    # Comparing one department with itself means nothing, so the switch shows only for all departments
    by_department = not filters.departments and right.toggle("Compare departments", value=True)
    # one line per department, or one line for the whole company
    frame = attempt(lambda: am.yoy_trends(filters, by_department=True)) if by_department else trend
    if frame is not None:
        # draw the line chart
        st.plotly_chart(charts.lines(frame, "period", metric, "department_name" if by_department else None,
                                     METRICS[metric]), width="stretch")
    st.markdown("**Year over year**")
    # the year-over-year table
    st.dataframe(trend, hide_index=True, width="stretch",
                 column_config={"period": "Review cycle", "reviews": "Reviews", "avg_rating": "Avg rating", "avg_kpi": "Avg KPI",
                                "outstanding_pct": "Outstanding (%)", "low_rated_pct": "Low rated (%)",
                                "overtime_pct": "Overtime (%)", "avg_job_satisfaction": "Job satisfaction",
                                "avg_training_sessions": "Training sessions", "rating_change": "Rating change",
                                "kpi_change": "KPI change", "outstanding_change": "Outstanding change",
                                "reviews_change_pct": "Reviews change (%)"})
elif trend is not None:
    st.info("No reviews match these filters.")

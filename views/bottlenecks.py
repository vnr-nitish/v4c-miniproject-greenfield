"""Project bottlenecks: bubble chart of overdue projects and delivery-delay analysis."""
import streamlit as st

from src.dal import AnalyticsManager
from src.ui import charts
from src.ui.common import attempt, current_filters, note, page_header

page_header("Project bottlenecks", "Open projects past their planned end, and where delivery slips")
am, filters = AnalyticsManager(), current_filters()

overdue = attempt(lambda: am.bottleneck_projects(filters, limit=500))
if overdue is not None and len(overdue):
    st.subheader(f"{len(overdue)} overdue projects")
    st.plotly_chart(charts.bubbles(overdue), width="stretch")
    note("Top right = the real bottlenecks: long overdue <i>and</i> a team working a lot of overtime. "
         "Bubble size = budget, colour = priority.")
    st.dataframe(overdue, hide_index=True, width="stretch",
                 column_config={"days_overdue": st.column_config.ProgressColumn(
                     "Days overdue", min_value=0, max_value=int(overdue.days_overdue.max()), format="%d")})

st.subheader("Completed projects: how often and how late")
group = st.segmented_control("Group by", ["priority", "budget_band", "department", "year"], default="priority") \
    or "priority"
delivery = attempt(lambda: am.project_delivery(filters, by=group))
if delivery is not None:
    left, right = st.columns(2)
    left.plotly_chart(charts.bars(delivery, group, "delayed_pct", title="Finished late (%)"), width="stretch")
    right.plotly_chart(charts.bars(delivery, group, "avg_delay_when_late", title="Average delay when late (days)"),
                       width="stretch")

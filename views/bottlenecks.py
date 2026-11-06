"""Project bottlenecks: overdue projects, and how often and how late completed projects finished."""
import streamlit as st

from src.dal import AnalyticsManager
from src.models.project import PRIORITIES
from src.ui import charts
from src.ui.common import attempt, current_filters, note, page_header

GROUPS = {"priority": "Priority", "budget_band": "Budget band", "department": "Department", "year": "Year"}
ORDER = {"priority": list(PRIORITIES), "budget_band": ["<250K", "250K-1M", "1M-3M", "3M+"]}

page_header("Project bottlenecks")
am, filters = AnalyticsManager(), current_filters()
tab_overdue, tab_completed = st.tabs(["Overdue projects", "Completed projects"])

with tab_overdue:
    overdue = attempt(lambda: am.bottleneck_projects(filters, limit=500))
    if overdue is not None and len(overdue):
        st.subheader(f"{len(overdue)} overdue projects")
        st.plotly_chart(charts.bubbles(overdue), width="stretch")
        note("Projects in the top right need attention first: they are long overdue and their teams work a lot of "
             "overtime. A bigger bubble means a bigger budget.")
        st.dataframe(overdue, hide_index=True, width="stretch",
                     column_config={"overdue_rank": "Rank", "project_code": "Code", "project_name": "Name",
                                    "owning_department_name": "Department", "project_manager_name": "Project manager",
                                    "status": "Status", "priority": "Priority", "budget": "Budget", "start_date": "Start",
                                    "planned_end_date": "Planned end",
                                    "days_overdue": st.column_config.ProgressColumn(
                                        "Days overdue", min_value=0, max_value=int(overdue.days_overdue.max()), format="%d"),
                                    "team_reviews": "Team reviews", "team_overtime_pct": "Team overtime (%)",
                                    "team_avg_kpi": "Team avg KPI"})
    elif overdue is not None:
        st.info("No project is overdue.")

with tab_completed:
    group = st.segmented_control("Group by", list(GROUPS), format_func=GROUPS.get, default="priority", required=True)
    delivery = attempt(lambda: am.project_delivery(filters, by=group))
    if delivery is not None and len(delivery):
        if group in ORDER:
            delivery = delivery.set_index(group).reindex(ORDER[group]).dropna(how="all").reset_index()
        delivery = delivery.assign(average_days=delivery.avg_delay_when_late.round().astype("Int64"))
        left, right = st.columns(2)
        left.plotly_chart(charts.bars(delivery, group, "delayed_pct", title="Finished late (%)", text="delayed_pct"),
                          width="stretch")
        right.plotly_chart(charts.bars(delivery, group, "average_days", title="Average delay when late (days)",
                                       text="average_days"), width="stretch")

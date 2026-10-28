"""Top performers by department — DENSE_RANK() in SQL, shown as a 3D podium and a leaderboard."""
import streamlit as st

from src.dal import AnalyticsFilter, AnalyticsManager
from src.ui import charts
from src.ui.common import attempt, current_filters, note, page_header

page_header("Top performers", "Ranked per department with DENSE_RANK() — equal results share a rank")
am, base = AnalyticsManager(), current_filters()
cycles = st.session_state.get("cycles") or []

c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
chosen = c1.multiselect("Review cycles", cycles, default=base.fiscal_years or cycles[-3:])
top_n = c2.slider("Ranks per department", 1, 20, 5)
min_reviews = c3.slider("Minimum reviews", 1, 5, 2 if len(chosen) > 1 else 1)
active_only = c4.toggle("Current staff only", value=True)

filters = AnalyticsFilter(base.departments, base.job_levels, tuple(chosen))
top = attempt(lambda: am.top_performers(filters, top_n=top_n, min_reviews=min_reviews, active_only=active_only))
if top is not None and len(top):
    st.plotly_chart(charts.podium(top), width="stretch")
    st.caption("Drag to rotate the podium. * = rank shared with others (hover for the count).")
    department = st.segmented_control("Leaderboard", sorted(top.department_name.unique()),
                                      default=sorted(top.department_name.unique())[0])
    board = top[top.department_name == department] if department else top
    st.dataframe(board[["department_rank", "sharing_rank", "employee_code", "full_name", "current_role",
                        "job_level", "reviews", "avg_rating", "avg_kpi", "ranked_employees"]],
                 hide_index=True, width="stretch",
                 column_config={"department_rank": st.column_config.NumberColumn("Rank", format="#%d"),
                                "sharing_rank": "Sharing rank", "avg_kpi": st.column_config.ProgressColumn(
                                    "Avg KPI", min_value=0, max_value=100, format="%.2f"),
                                "ranked_employees": "Ranked in department"})
elif top is not None:
    st.info("No employees match these filters.")

note("Ranking is computed in the warehouse at query time, so it always respects the filters above.")
with st.expander("The SQL behind this page"):
    st.code("""WITH per_employee AS (
    SELECT dd.department_name, de.employee_id, COUNT(*) AS reviews,
           AVG(f.performance_rating) AS avg_rating, AVG(f.kpi_score) AS avg_kpi
    FROM hr_dw.fact_performance_reviews f
    JOIN hr_dw.dim_date d        ON d.date_sk = f.date_sk
    JOIN hr_dw.dim_department dd ON dd.department_sk = f.department_sk
    JOIN hr_dw.dim_employee de   ON de.employee_sk = f.employee_sk
    WHERE d.fiscal_year IN (...)            -- the filters
    GROUP BY dd.department_name, de.employee_id
    HAVING COUNT(*) >= :min_reviews
), ranked AS (
    SELECT p.*, DENSE_RANK() OVER (PARTITION BY p.department_name
                                   ORDER BY p.avg_rating DESC, p.avg_kpi DESC) AS department_rank
    FROM per_employee p
)
SELECT ... FROM ranked WHERE department_rank <= :top_n""", language="sql")

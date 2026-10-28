"""Submit a performance review; defaults come from the employee's manager and main project."""
from datetime import date
from decimal import Decimal

import streamlit as st

from src.dal import EmployeeManager, ReviewManager
from src.models import Review
from src.models.review import RATING_LABELS
from src.ui.common import attempt, editor_unlocked, note, page_header

page_header("Performance review", "One review per employee and year — it reaches the warehouse on the next ETL run")
if not editor_unlocked():
    st.stop()
rm, em = ReviewManager(), EmployeeManager()

c1, c2 = st.columns(2)
code = c1.text_input("Employee code", placeholder="EMP000123")
year = c2.selectbox("Performance year", list(range(date.today().year, 2018, -1)), index=1)
employee = attempt(lambda: em.get_by_code(code)) if code else None
if employee:
    suggestion = rm.suggest(employee.employee_id, year)
    st.caption(f"{employee.full_name} · suggested reviewer: {suggestion['reviewer_name'] or '—'} · "
               f"main project that year: {suggestion['project_name'] or '—'}")
    with st.form("review"):
        rating = st.segmented_control("Rating", list(RATING_LABELS), format_func=lambda r: f"{r} · {RATING_LABELS[r]}",
                                      default=3)
        c1, c2, c3 = st.columns(3)
        kpi = c1.slider("KPI score", 0.0, 100.0, 80.0, 0.5)
        training = c2.number_input("Training sessions", 0, 20, 2)
        overtime = c3.toggle("Worked overtime")
        reviewer = c1.text_input("Reviewer employee id", str(suggestion["reviewer_id"] or ""))
        project = c2.text_input("Project id (optional)", str(suggestion["project_id"] or ""))
        held = c3.date_input("Review held on", min(date.today(), date(year + 1, 2, 1)),
                             min_value=date(year, 1, 1), max_value=date.today())
        comments = st.text_area("Comments", max_chars=500)
        if st.form_submit_button("Submit review", type="primary"):
            saved = attempt(lambda: rm.submit(Review(
                employee_id=employee.employee_id, reviewer_id=int(reviewer) if reviewer.isdigit() else 0,
                review_year=int(year), review_date=held, rating_id=int(rating or 3), kpi_score=Decimal(str(kpi)),
                training_sessions=int(training), overtime_flag=overtime,
                project_id=int(project) if project.isdigit() else None, comments=comments or None)))
            if saved:
                st.success(f"Review {saved.review_id} saved ({saved.rating_label}).")
    st.markdown("**Reviews on record**")
    st.dataframe(rm.reviews_for(employee.employee_id), hide_index=True, width="stretch")
note("The warehouse is loaded in batches (decision B5): run the ETL from <b>Operations</b> to see new reviews "
     "in the dashboards.")

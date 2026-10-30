"""Employee 360: profile, career timeline, and the job change that triggers SCD Type 2 in the warehouse."""
from datetime import date

import streamlit as st

from src.dal import EmployeeManager, ProjectManager, ReferenceDataManager, ReviewManager
from src.models import Position
from src.ui import charts
from src.ui.common import attempt, data_changed, editor_unlocked, page_header
from src.ui.forms import position_picker

page_header("Employee 360", "Profile, career timeline and job changes — a department change creates a new SCD Type 2 "
                            "version in the warehouse")
em = EmployeeManager()


@st.cache_data(ttl=3600, show_spinner=False)
def example_codes() -> list[str]:
    return EmployeeManager().example_codes(6)


examples = attempt(example_codes) or []
c1, c2 = st.columns([2, 3])
code = c1.text_input("Employee code", value=st.session_state.get("e360_code", examples[0] if examples else ""))
picked = c2.pills("Examples with a long history", examples)
if picked and picked != code:
    st.session_state["e360_code"] = picked
    st.rerun()
if not code:
    st.stop()
employee = attempt(lambda: em.get_by_code(code))
if not employee:
    st.stop()
profile = em.profile(employee.employee_id)
history = em.history(employee.employee_id)

st.markdown(f"### {employee.full_name} · {employee.employee_code} "
            f"{'🟢 active' if employee.is_active else '⚪ left ' + str(employee.exit_date)}")
m = st.columns(5)
m[0].metric("Department", profile["department_name"])
m[1].metric("Role", profile["job_role"], f"level {profile['job_level']}")
m[2].metric("Monthly income", f"{profile['monthly_income']:,.0f}")
m[3].metric("Tenure", f"{employee.tenure_on(date.today())} years")
m[4].metric("SCD2 versions", len(history))

tab_path, tab_change, tab_more = st.tabs(["Career timeline", "Change position", "Reviews · projects · exit"])
with tab_path:
    st.plotly_chart(charts.career_timeline(history),
                    width="stretch")
    st.dataframe(history, hide_index=True, width="stretch")

with tab_change:
    if employee.is_active and editor_unlocked("change"):
        current = em.current_position(employee.employee_id)
        new = position_picker(f"change_{employee.employee_id}", current)
        c1, c2 = st.columns(2)
        effective = c1.date_input("Effective date", date.today(), max_value=date.today())
        reason = c2.selectbox("Reason", [None, "Promotion", "Transfer", "Role Change", "Manager Change",
                                         "Salary Revision"], format_func=lambda r: r or "Derive automatically")
        if st.button("Apply change", type="primary"):
            change = attempt(lambda: em.change_position(employee.employee_id, Position(**new), effective, reason))
            if change:
                data_changed()
                st.session_state["e360_changed"] = True
                st.success(f"{change.change_reason} recorded — warehouse batch {change.batch_id}.")
                old, cur = st.columns(2)
                cols = ["version_number", "department_name", "job_role", "job_level", "monthly_income",
                        "start_date", "end_date", "is_current"]
                with old:
                    st.markdown("<div class='scd-old'><b>Expired version</b></div>", unsafe_allow_html=True)
                    st.dataframe(change.expired_version[cols].to_frame(), width="stretch")
                with cur:
                    st.markdown("<div class='scd-new'><b>New current version</b></div>", unsafe_allow_html=True)
                    st.dataframe(change.new_version[cols].to_frame(), width="stretch")
                st.plotly_chart(charts.career_timeline(change.versions), width="stretch")
    elif not employee.is_active:
        st.info("This employee has left; their history is kept but can no longer change.")

with tab_more:
    st.markdown("**Reviews**")
    st.dataframe(ReviewManager().reviews_for(employee.employee_id), hide_index=True, width="stretch")
    st.markdown("**Project assignments**")
    st.dataframe(ProjectManager().assignments_for(employee.employee_id), hide_index=True, width="stretch")
    if employee.is_active:
        with st.expander("Record an exit"):
            if editor_unlocked("exit"):
                reasons = ReferenceDataManager().exit_reasons()
                with st.form("exit"):
                    exit_date = st.date_input("Exit date", date.today(), max_value=date.today())
                    reason_id = st.selectbox("Reason", reasons.exit_reason_id,
                                             format_func=dict(zip(reasons.exit_reason_id, reasons.reason_name)).get)
                    if st.form_submit_button("Record exit"):
                        closed = attempt(lambda: em.record_exit(employee.employee_id, exit_date, int(reason_id)))
                        if closed is not None:
                            data_changed()
                            st.success(f"Exit recorded; {closed} assignment(s) closed; warehouse updated.")

"""Projects: create, staff (with over-allocation check), browse and close."""
from datetime import date, timedelta
from decimal import Decimal

import streamlit as st

from src.dal import EmployeeManager, ProjectManager, ReferenceDataManager
from src.exceptions import OverAllocationError
from src.models import Assignment, Project
from src.ui.common import attempt, editor_unlocked, page_header

page_header("Projects", "Create projects, staff them and close them — new projects reach the warehouse on the next ETL run")
pm, ref = ProjectManager(), ReferenceDataManager()
tab_browse, tab_create, tab_assign = st.tabs(["Browse & close", "Create", "Assign people"])

with tab_browse:
    c1, c2 = st.columns([2, 1])
    text = c1.text_input("Search by code or name")
    status = c2.selectbox("Status", [None, "Planned", "Active", "On Hold", "Completed", "Cancelled"],
                          format_func=lambda s: s or "Any")
    found = attempt(lambda: pm.search(text, status=status, limit=300))
    if found is not None:
        event = st.dataframe(found, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row")
        if event.selection.rows:
            project = pm.get(int(found.iloc[event.selection.rows[0]].project_id))
            st.markdown(f"#### {project.project_code} · {project.project_name}")
            st.dataframe(pm.team(project.project_id), hide_index=True, width="stretch")
            if not project.is_closed and editor_unlocked("close"):
                c1, c2, c3 = st.columns(3)
                outcome = c1.selectbox("Close as", ["Completed", "Cancelled"])
                on = c2.date_input("Actual end", date.today(), min_value=project.start_date, max_value=date.today())
                if c3.button("Close project", type="primary"):
                    closed = attempt(lambda: pm.update(project.close(outcome, on)))
                    if closed:
                        st.success(f"{closed.project_code} closed; open assignments ended on {on}.")

with tab_create:
    if editor_unlocked("create"):
        departments = ref.departments()
        department_id = st.selectbox("Owning department", departments.department_id,
                                     format_func=dict(zip(departments.department_id, departments.department_name)).get)
        managers = ref.manager_candidates(int(department_id), min_level=3)
        with st.form("create_project"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Project name")
            manager = c2.selectbox("Project manager", managers.employee_id,
                                   format_func=dict(zip(managers.employee_id, managers.full_name)).get)
            start = c1.date_input("Start", date.today())
            planned_end = c2.date_input("Planned end", date.today() + timedelta(days=120))
            budget = c1.number_input("Budget", min_value=1000, value=500_000, step=10_000)
            priority = c2.segmented_control("Priority", ["Low", "Medium", "High", "Critical"], default="Medium")
            status = c1.radio("Status", ["Planned", "Active"], horizontal=True)
            description = st.text_area("Description", max_chars=500)
            if st.form_submit_button("Create project", type="primary"):
                created = attempt(lambda: pm.create(Project(
                    project_name=name, department_id=int(department_id), start_date=start,
                    planned_end_date=planned_end, budget=Decimal(budget), status=status, priority=priority or "Medium",
                    description=description or None, project_manager_id=int(manager) if manager else None)))
                if created:
                    st.success(f"Project **{created.project_code}** created.")

with tab_assign:
    if editor_unlocked("assign"):
        open_projects = attempt(lambda: pm.search(status="Active", limit=500))
        if open_projects is not None and len(open_projects):
            with st.form("assign"):
                c1, c2 = st.columns(2)
                project_id = c1.selectbox("Active project", open_projects.project_id, format_func=dict(
                    zip(open_projects.project_id, open_projects.project_code + " · " + open_projects.project_name)).get)
                code = c2.text_input("Employee code", placeholder="EMP000123")
                role = c1.text_input("Project role", "Developer")
                allocation = c2.slider("Allocation (%)", 5, 100, 50, step=5)
                start = c1.date_input("From", date.today())
                anyway = c2.checkbox("Assign even if this takes them above 100 %")
                if st.form_submit_button("Assign", type="primary"):
                    def assign():
                        employee = EmployeeManager().get_by_code(code)
                        return pm.assign(Assignment(employee_id=employee.employee_id, project_id=int(project_id),
                                                    project_role=role, allocation_pct=int(allocation), start_date=start),
                                         allow_over_allocation=anyway)
                    try:
                        done = attempt(assign)
                    except OverAllocationError:      # attempt() shows it; kept for clarity
                        done = None
                    if done:
                        st.success(f"Assigned ({allocation} %).")

"""Projects: a dashboard of all projects, one project's details and updates, create, and assign people."""
from datetime import date, timedelta
from decimal import Decimal

import streamlit as st

from src.dal import EmployeeManager, ProjectManager, ReferenceDataManager
from src.exceptions import OverAllocationError
from src.models import Assignment, Project
from src.models.project import PRIORITIES
from src.ui.common import announce, attempt, fact_cards, page_header, show_announcement
from src.ui.forms import _choice, _manager_candidates

page_header("Projects")
show_announcement()
pm, ref = ProjectManager(), ReferenceDataManager()

OPEN_STATUSES = ["Planned", "Active", "On Hold"]
TABLE_HEADINGS = {"project_code": "Code", "project_name": "Name", "department_name": "Department",
                  "project_manager": "Project manager", "status": "Status", "priority": "Priority", "start_date": "Start",
                  "planned_end_date": "Planned end", "actual_end_date": "Actual end", "budget": "Budget"}


@st.fragment
def dashboard_tab() -> None:
    c1, c2 = st.columns([2, 1])
    text = c1.text_input("Search by code or name")
    status = c2.selectbox("Status", [None, *OPEN_STATUSES, "Completed", "Cancelled"], format_func=lambda s: s or "Any")
    found = attempt(lambda: pm.search(text, status=status, limit=300))
    if found is not None:
        st.dataframe(found[list(TABLE_HEADINGS)], hide_index=True, width="stretch", column_config=TABLE_HEADINGS)


def read_project(code: str) -> tuple:
    with pm.read_session():
        project = pm.get_by_code(code)
        return project, pm.search(project.project_code).iloc[0], pm.team(project.project_id)


def timing(project: Project) -> str:
    if project.delay_days is not None:
        return f"{project.delay_days} days late" if project.is_delayed else "On time"
    if project.is_overdue_on(date.today()):
        return f"{(date.today() - project.planned_end_date).days} days overdue"
    return "Closed" if project.is_closed else "On schedule"


def edit_project(project: Project) -> None:
    managers = _manager_candidates(project.department_id, 3)
    names = dict(zip(managers.employee_id, managers.full_name))
    manager_ids = [None, *names]
    if project.project_manager_id is not None and project.project_manager_id not in names:
        manager_ids.insert(1, project.project_manager_id)
        names[project.project_manager_id] = f"Current manager · ID {project.project_manager_id}"
    with st.form(f"edit_project_{project.project_id}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Project name", project.project_name)
        manager = c2.selectbox("Project manager", manager_ids, index=manager_ids.index(project.project_manager_id),
                               format_func=lambda m: "— none —" if m is None else names[m])
        status = c1.selectbox("Status", OPEN_STATUSES, index=OPEN_STATUSES.index(project.status))
        priority = c2.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(project.priority))
        planned_end = c1.date_input("Planned end", project.planned_end_date, min_value=project.start_date)
        budget = c2.number_input("Budget", min_value=1.0, value=float(project.budget), step=10_000.0, format="%.2f")
        description = st.text_area("Description", project.description or "", max_chars=500)
        submitted = st.form_submit_button("Save changes", type="primary")
    if submitted:
        saved = attempt(lambda: pm.update(project.with_changes(
            project_name=name, project_manager_id=None if manager is None else int(manager), status=status,
            priority=priority, planned_end_date=planned_end, budget=Decimal(str(budget)), description=description or None)))
        if saved:
            announce("Project updated", f"**{saved.project_code}** · {saved.project_name} was updated.")


def close_project(project: Project) -> None:
    with st.form(f"close_project_{project.project_id}"):
        c1, c2 = st.columns(2)
        outcome = c1.selectbox("Close as", ["Completed", "Cancelled"])
        on = c2.date_input("Actual end", date.today(), min_value=project.start_date, max_value=date.today())
        submitted = st.form_submit_button("Close project", type="primary")
    if submitted:
        closed = attempt(lambda: pm.update(project.close(outcome, on)))
        if closed:
            announce("Project closed", f"**{closed.project_code}** was closed as {outcome}; its open assignments "
                     f"ended on {on}.")


@st.fragment
def details_tab() -> None:
    with st.form("project_lookup", border=False):
        c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
        typed = c1.text_input("Project code", placeholder="PRJ00123")
        if c2.form_submit_button("Submit", type="primary", width="stretch"):
            st.session_state["project_code"] = typed
    code = st.session_state.get("project_code", "").strip()
    if not code:
        return
    loaded = attempt(lambda: read_project(code))
    if not loaded:
        return
    project, row, team = loaded
    st.markdown(f"### {project.project_code} · {project.project_name}")
    fact_cards({
        "Project": [("Status", project.status), ("Priority", project.priority), ("Department", row.department_name),
                    ("Project manager", row.project_manager), ("Description", project.description)],
        "Schedule and budget": [("Start", project.start_date), ("Planned end", project.planned_end_date),
                                ("Actual end", project.actual_end_date), ("Timing", timing(project)),
                                ("Budget", f"{project.budget:,.0f}")],
    })
    st.markdown("**Team**")
    st.dataframe(team[["employee_code", "full_name", "project_role", "allocation_pct", "start_date", "end_date"]],
                 hide_index=True, width="stretch",
                 column_config={"employee_code": "Employee ID", "full_name": "Name", "project_role": "Role",
                                "allocation_pct": "Allocation (%)", "start_date": "From", "end_date": "To"})
    if project.is_closed:
        st.info(f"This project was closed on {project.actual_end_date}, so it can no longer change.")
    else:
        action = st.segmented_control("What do you want to do?", ["Edit details", "Close project"], default="Edit details",
                                      required=True, key=f"project_action_{project.project_id}")
        if action == "Edit details":
            edit_project(project)
        else:
            close_project(project)


@st.fragment
def create_tab() -> None:
    """All the fields in one box. Not a st.form: the owning department narrows the project managers."""
    # Every field's key carries this number: a new number means new, empty fields
    number = st.session_state.setdefault("create_project_number", 0)
    departments = ref.departments()
    department_ids = departments.department_id.tolist()
    department_key = f"create_department_{number}"
    department_id = _choice(department_key, department_ids, None)
    managers = _manager_candidates(int(department_id), 3)
    with st.container(border=True):
        c1, c2 = st.columns(2)                      # a row each, so the two sides stay level
        name = c1.text_input("Project name", key=f"create_name_{number}")
        c2.selectbox("Owning department", department_ids, key=department_key, index=department_ids.index(department_id),
                     format_func=dict(zip(departments.department_id, departments.department_name)).get)
        c1, c2 = st.columns(2)
        manager = c1.selectbox("Project manager", managers.employee_id, key=f"create_manager_{number}_{department_id}",
                               format_func=dict(zip(managers.employee_id, managers.full_name)).get)
        priority = c2.segmented_control("Priority", PRIORITIES, default="Medium", required=True, key=f"create_priority_{number}")
        c1, c2 = st.columns(2)
        start = c1.date_input("Start", date.today(), key=f"create_start_{number}")
        planned_end = c2.date_input("Planned end", date.today() + timedelta(days=120), key=f"create_end_{number}")
        c1, c2 = st.columns(2)
        budget = c1.number_input("Budget", min_value=1000, value=500_000, step=10_000, key=f"create_budget_{number}")
        status = c2.radio("Status", OPEN_STATUSES[:2], horizontal=True, key=f"create_status_{number}")
        description = st.text_area("Description", max_chars=500, key=f"create_description_{number}")
        if st.button("Create project", type="primary"):
            created = attempt(lambda: pm.create(Project(
                project_name=name, department_id=int(department_id), start_date=start, planned_end_date=planned_end,
                budget=Decimal(budget), status=status, priority=priority, description=description or None,
                project_manager_id=int(manager) if manager else None)))
            if created:
                st.session_state["create_project_number"] = number + 1
                announce("Project created", f"Project **{created.project_code}** · {created.project_name} was created.")


def assign_tab() -> None:
    open_projects = attempt(lambda: pm.search(status="Active", limit=500))
    if open_projects is None or not len(open_projects):
        return
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


tab_dashboard, tab_details, tab_create, tab_assign = st.tabs(["Dashboard", "Project details", "Create", "Assign people"])
with tab_dashboard:
    dashboard_tab()
with tab_details:
    details_tab()
with tab_create:
    create_tab()
with tab_assign:
    assign_tab()

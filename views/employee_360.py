"""Employee 360: find anyone, then their details, career timeline, updates, reviews and projects, and exit."""
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from src.dal import EmployeeManager, ProjectManager, ReferenceDataManager, ReviewManager
from src.models import Employee, Position
from src.models.employee import GENDERS, MARITAL_STATUSES
from src.ui.common import announce, attempt, data_changed, fact_cards, page_header, show_announcement
from src.ui.forms import position_picker

SEARCH_LIMIT = 50

page_header("Employee 360")
show_announcement()
em = EmployeeManager()


def read_employee(employee_id: int) -> tuple:
    """Everything this page shows about one employee, on one connection: 12 round trips to the
    database instead of 40, and each one takes about 0.25 s from the live site."""
    with em.read_session():
        return (em.get(employee_id), em.profile(employee_id), em.history(employee_id),
                ReviewManager().reviews_for(employee_id), ProjectManager().assignments_for(employee_id))


def pick_from_matches(found: pd.DataFrame, query: str) -> int | None:
    """Which employee to show: the one exact match or only match, else the row the user clicks."""
    exact = found[found.match_rank == 0]
    if len(exact) == 1:
        return int(exact.employee_id.iloc[0])
    if len(found) == 1:
        return int(found.employee_id.iloc[0])
    st.caption(f"{'The first ' if len(found) == SEARCH_LIMIT else ''}{len(found)} matches — click a row to open that employee.")
    table = found.assign(status=found.is_active.map(lambda active: "Active" if active else "Left"))
    event = st.dataframe(table[["employee_code", "full_name", "email", "phone", "department_name", "job_role", "status"]],
                         hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=f"e360_matches_{query}",
                         column_config={"employee_code": "Employee ID", "full_name": "Name", "email": "E-mail",
                                        "phone": "Phone", "department_name": "Department", "job_role": "Job role",
                                        "status": "Status"})
    return int(found.employee_id.iloc[event.selection.rows[0]]) if event.selection.rows else None


def years(count: int) -> str:
    return f"{count} year" if count == 1 else f"{count} years"


def details(employee: Employee, profile: dict[str, Any], versions: int) -> dict[str, list[tuple[str, Any]]]:
    today = date.today()
    employment = [("Employee code", employee.employee_code), ("Employee ID", employee.employee_id),
                  ("Status", "Active" if employee.is_active else "Left"), ("Hire date", employee.hire_date),
                  ("Tenure", years(employee.tenure_on(today))), ("Versions in the warehouse", versions)]
    if not employee.is_active:
        kind = "voluntary" if profile["is_voluntary_exit"] else "involuntary"
        employment += [("Exit date", employee.exit_date), ("Exit reason", f"{profile['exit_reason']} ({kind})")]
    return {
        "Current position": [
            ("Department", profile["department_name"]), ("Job role", profile["job_role"]),
            ("Job level", f"{profile['job_level']} · {profile['job_level_title']}"), ("Manager", profile["manager_name"]),
            ("Monthly income", f"{profile['monthly_income']:,.0f}"), ("Stock option level", profile["stock_option_level"]),
            ("Business travel", profile["business_travel"]), ("In this position since", profile["position_since"]),
            ("Last change", profile["last_change_reason"])],
        "Employment": employment,
        "Contact and personal": [
            ("E-mail", employee.email), ("Phone", employee.phone), ("Gender", employee.gender),
            ("Date of birth", f"{employee.date_of_birth} (age {employee.age_on(today)})"),
            ("Marital status", employee.marital_status), ("Distance from home", f"{employee.distance_from_home_km} km")],
        "Background": [
            ("Education level", profile["education_level"]), ("Education field", profile["education_field"]),
            ("Prior experience", years(employee.prior_experience_years)),
            ("Previous employers", employee.prior_companies_count),
            ("Total experience", years(employee.total_experience_on(today)))],
    }


def change_position(employee: Employee, profile: dict[str, Any]) -> None:
    current = Position(department_id=profile["department_id"], job_role_id=profile["job_role_id"],
                       job_level_id=profile["job_level"], manager_id=profile["manager_id"],
                       monthly_income=profile["monthly_income"], stock_option_level=profile["stock_option_level"],
                       business_travel=profile["business_travel"])
    new = position_picker(f"change_{employee.employee_id}", current)
    c1, c2 = st.columns(2)
    effective = c1.date_input("Effective date", date.today(), max_value=date.today())
    reason = c2.selectbox("Reason", [None, "Promotion", "Transfer", "Role Change", "Manager Change",
                                     "Salary Revision"], format_func=lambda r: r or "Derive automatically")
    if st.button("Apply change", type="primary"):
        change = attempt(lambda: em.change_position(employee.employee_id, Position(**new), effective, reason))
        if change:
            data_changed()
            columns = ["version_number", "department_name", "job_role", "job_level", "monthly_income",
                       "start_date", "end_date", "is_current"]
            announce("Position updated", f"{change.change_reason} recorded for {employee.full_name} — warehouse batch "
                     f"{change.batch_id}.", {"The old version was closed and a new one opened (SCD Type 2):":
                                             change.versions.tail(2)[columns]})


def edit_details(employee: Employee) -> None:
    ref = ReferenceDataManager()
    levels, fields = ref.education_levels(), ref.education_fields()
    with st.form(f"details_{employee.employee_id}"):
        c1, c2, c3 = st.columns(3)
        first_name = c1.text_input("First name", employee.first_name)
        last_name = c2.text_input("Last name", employee.last_name)
        email = c3.text_input("E-mail", employee.email)
        phone = c1.text_input("Phone", employee.phone)
        gender = c2.radio("Gender", GENDERS, index=GENDERS.index(employee.gender), horizontal=True)
        marital = c3.selectbox("Marital status", MARITAL_STATUSES, index=MARITAL_STATUSES.index(employee.marital_status))
        birth = c1.date_input("Date of birth", employee.date_of_birth, min_value=min(date(1950, 1, 1), employee.date_of_birth),
                              max_value=date.today())
        distance = c2.number_input("Distance from home (km)", 1, 50, employee.distance_from_home_km)
        experience = c3.number_input("Prior experience (years)", 0, 50, employee.prior_experience_years)
        level_ids, field_ids = levels.education_level_id.tolist(), fields.education_field_id.tolist()
        education = c1.selectbox("Education level", level_ids, index=level_ids.index(employee.education_level_id),
                                 format_func=dict(zip(levels.education_level_id, levels.level_name)).get)
        field = c2.selectbox("Education field", field_ids, index=field_ids.index(employee.education_field_id),
                             format_func=dict(zip(fields.education_field_id, fields.field_name)).get)
        companies = c3.number_input("Previous employers", 0, 20, employee.prior_companies_count)
        submitted = st.form_submit_button("Save details", type="primary")
    if submitted:
        saved = attempt(lambda: em.update_details(employee.with_changes(
            first_name=first_name, last_name=last_name, email=email, phone=phone, gender=gender, marital_status=marital,
            date_of_birth=birth, distance_from_home_km=int(distance), prior_experience_years=int(experience),
            education_level_id=int(education), education_field_id=int(field), prior_companies_count=int(companies))))
        if saved:
            data_changed()
            announce("Details saved", f"The details of {saved.full_name} ({saved.employee_code}) were saved.")


@st.fragment
def update_tab(employee: Employee, profile: dict[str, Any]) -> None:
    """A fragment: each choice in this tab redraws only the tab, without reading the whole page again."""
    if not employee.is_active:
        st.info("This employee has left; their history is kept but can no longer change.")
        return
    part = st.segmented_control("What do you want to update?", ["Position", "Personal details"], default="Position",
                                required=True, key=f"e360_update_{employee.employee_id}")
    if part == "Position":
        change_position(employee, profile)
    else:
        edit_details(employee)


def exit_tab(employee: Employee, profile: dict[str, Any]) -> None:
    if not employee.is_active:
        st.info(f"{employee.full_name} left on {employee.exit_date}: {profile['exit_reason']}.")
        return
    reasons = ReferenceDataManager().exit_reasons()
    with st.form(f"exit_{employee.employee_id}"):
        c1, c2 = st.columns(2)
        exit_date = c1.date_input("Exit date", date.today(), min_value=employee.hire_date, max_value=date.today())
        reason_id = c2.selectbox("Reason", reasons.exit_reason_id,
                                 format_func=dict(zip(reasons.exit_reason_id, reasons.reason_name)).get)
        submitted = st.form_submit_button("Record exit", type="primary")
    if submitted:
        closed = attempt(lambda: em.record_exit(employee.employee_id, exit_date, int(reason_id)))
        if closed is not None:
            data_changed()
            announce("Exit recorded", f"{employee.full_name} left on {exit_date}; {closed} assignment(s) closed and "
                     "the warehouse updated.")


query = st.text_input("Search", key="e360_search", placeholder="Name, e-mail, phone number or employee ID")
if not query.strip():
    st.stop()
found = attempt(lambda: em.search(query, active_only=False, limit=SEARCH_LIMIT))
if found is None:
    st.stop()
if found.empty:
    st.info(f"No employee matches “{query.strip()}”. Try part of a name, an e-mail, a phone number or an employee ID.")
    st.stop()
employee_id = pick_from_matches(found, query)
if employee_id is None:
    st.stop()
loaded = attempt(lambda: read_employee(employee_id))
if not loaded:
    st.stop()
employee, profile, history, reviews, assignments = loaded

st.markdown(f"### {employee.full_name} · {employee.employee_code} "
            f"{'🟢 active' if employee.is_active else '⚪ left ' + str(employee.exit_date)}")
tab_details, tab_timeline, tab_update, tab_work, tab_exit = st.tabs(
    ["Details", "Career timeline", "Update", "Reviews & projects", "Exit"])
with tab_details:
    fact_cards(details(employee, profile, len(history)))
with tab_timeline:
    st.dataframe(history, hide_index=True, width="stretch")
with tab_update:
    update_tab(employee, profile)
with tab_work:
    st.markdown("**Reviews**")
    st.dataframe(reviews, hide_index=True, width="stretch")
    st.markdown("**Projects**")
    st.dataframe(assignments, hide_index=True, width="stretch")
with tab_exit:
    exit_tab(employee, profile)

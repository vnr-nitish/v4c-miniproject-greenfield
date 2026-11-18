"""Onboard a new employee: OLTP rows + the first SCD Type 2 version in the warehouse, in one transaction."""
from datetime import date, timedelta

import streamlit as st

from src.dal import EmployeeManager, ReferenceDataManager
from src.models import Employee, Position
from src.ui.common import announce, attempt, data_changed, page_header, show_announcement
from src.ui.forms import position_picker

# HOW SAVING WORKS: the page only draws the form. Form -> Employee and Position objects (they validate
# themselves) -> EmployeeManager.onboard() -> stored procedures -> hr_oltp + hr_dw, in one transaction.
page_header("Onboard an employee")
# The drop-down lists (education levels and fields) come from the lookup tables through
# ReferenceDataManager, which reads each list once and keeps it in memory.
ref = ReferenceDataManager()
show_announcement()

# Every field's key carries this number: a new number means new, empty fields
form_number = st.session_state.setdefault("onboard_form_number", 0)

st.markdown("#### 1 · Position")
# Part 1 of the form: department -> role -> level -> pay -> manager. Each choice narrows the next
# (src/ui/forms.py), so only valid combinations can be picked.
position = position_picker(f"onboard_{form_number}")

st.markdown("#### 2 · Person")
# the lists for the two education drop-downs
education_levels, education_fields = ref.education_levels(), ref.education_fields()
# Part 2: the person. Inside st.form nothing is sent to the server until the Onboard button is clicked.
with st.form(f"onboard_form_{form_number}", clear_on_submit=False):
    c1, c2, c3 = st.columns(3)
    # each field gives back what the user typed or chose
    first_name, last_name = c1.text_input("First name"), c2.text_input("Last name")
    email, phone = c3.text_input("E-mail"), c1.text_input("Phone", placeholder="+91 98765 43210")
    gender = c2.radio("Gender", ["Female", "Male"], horizontal=True)
    marital = c3.selectbox("Marital status", ["Single", "Married", "Divorced"])
    birth = c1.date_input("Date of birth", date(1996, 1, 1), min_value=date(1950, 1, 1), max_value=date.today())
    hire = c2.date_input("Hire date", date.today(), max_value=date.today())
    distance = c3.number_input("Distance from home (km)", 1, 50, 8)
    education = c1.selectbox("Education level", education_levels.education_level_id,
                             format_func=dict(zip(education_levels.education_level_id, education_levels.level_name)).get)
    field = c2.selectbox("Education field", education_fields.education_field_id,
                         format_func=dict(zip(education_fields.education_field_id, education_fields.field_name)).get)
    experience = c3.number_input("Prior experience (years)", 0, 50, 2)
    companies = c1.number_input("Previous employers", 0, 20, 1)
    # True only on the run right after the button is clicked
    submitted = st.form_submit_button("Onboard", type="primary")

# The Onboard button was clicked: build the objects and save them.
if submitted:
    def onboard():
        # Creating the Employee object checks every field at once (validation_errors in src/models/employee.py).
        # A broken rule raises ValidationError, and nothing reaches the database.
        employee = Employee(first_name=first_name, last_name=last_name, email=email, phone=phone, gender=gender,
                            date_of_birth=birth, marital_status=marital, education_level_id=int(education),
                            education_field_id=int(field), distance_from_home_km=int(distance),
                            prior_experience_years=int(experience), prior_companies_count=int(companies),
                            hire_date=hire)
        # The manager does the database work: one transaction, two stored procedures (OLTP, then warehouse).
        return EmployeeManager().onboard(employee, Position(**position))
    # attempt() runs the save and turns any error into a readable message on the page instead of a crash.
    hired = attempt(onboard)
    if hired:
        # The warehouse changed: the dashboards check for the new version on their next visit.
        data_changed()
        # read the new dim_employee row, to show it
        versions = attempt(lambda: EmployeeManager().history(hired.employee_id))
        # a new number gives new, empty fields next time
        st.session_state["onboard_form_number"] = form_number + 1
        # Redraw the page with an empty form, and show a pop-up with the new code and the dim_employee row.
        announce("Employee onboarded", f"Welcome aboard, {hired.full_name} — employee code **{hired.employee_code}**.",
                 {} if versions is None else
                 {"Warehouse row created in the same transaction (`dim_employee`, SCD Type 2 version 1):": versions},
                 f"Open **Employee 360** with code {hired.employee_code} to change their position.")
st.caption(f"Tip: hire dates up to {date.today() - timedelta(days=1)} let you try a job change the same day.")

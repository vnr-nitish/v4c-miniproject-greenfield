"""Onboard a new employee: OLTP rows + the first SCD Type 2 version in the warehouse, in one transaction."""
from datetime import date, timedelta

import streamlit as st

from src.dal import EmployeeManager, ReferenceDataManager
from src.models import Employee, Position
from src.ui.common import attempt, data_changed, editor_unlocked, page_header
from src.ui.forms import position_picker

page_header("Onboard an employee", "Validated as you go; the new hire appears in the warehouse immediately")
if not editor_unlocked():
    st.stop()
ref = ReferenceDataManager()

st.markdown("#### 1 · Position")
position = position_picker("onboard")

st.markdown("#### 2 · Person")
education_levels, education_fields = ref.education_levels(), ref.education_fields()
with st.form("onboard_form", clear_on_submit=False):
    c1, c2, c3 = st.columns(3)
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
    submitted = st.form_submit_button("Onboard", type="primary")

if submitted:
    def onboard():
        employee = Employee(first_name=first_name, last_name=last_name, email=email, phone=phone, gender=gender,
                            date_of_birth=birth, marital_status=marital, education_level_id=int(education),
                            education_field_id=int(field), distance_from_home_km=int(distance),
                            prior_experience_years=int(experience), prior_companies_count=int(companies),
                            hire_date=hire)
        return EmployeeManager().onboard(employee, Position(**position))
    hired = attempt(onboard)
    if hired:
        data_changed()
        st.success(f"Welcome aboard, {hired.full_name} — employee code **{hired.employee_code}**.")
        st.balloons()
        st.markdown("Warehouse row created in the same transaction (`dim_employee`, SCD Type 2 version 1):")
        st.dataframe(EmployeeManager().history(hired.employee_id), hide_index=True, width="stretch")
        st.caption(f"Open **Employee 360** with code {hired.employee_code} to change their position.")
st.caption(f"Tip: hire dates up to {date.today() - timedelta(days=1)} let you try a job change the same day.")

"""Form building blocks shared by the data-entry pages."""
from __future__ import annotations

from decimal import Decimal

import streamlit as st

from src.dal import ReferenceDataManager
from src.models import Position


@st.cache_data(ttl=300, show_spinner=False)
def _manager_candidates(department_id: int, min_level: int):
    """Cached for 5 minutes: the query reads the 100K-row current-employee view (~2 s on the cloud)."""
    return ReferenceDataManager().manager_candidates(department_id, min_level)


def position_picker(key: str, current: Position | None = None) -> dict:
    """Department -> only the roles it offers -> only the levels the role allows -> pay inside the
    level's band -> a manager from that department. Outside a form, so each choice narrows the next."""
    ref = ReferenceDataManager()
    departments = ref.departments()
    dept_ids = departments.department_id.tolist()
    c1, c2, c3 = st.columns(3)
    department_id = c1.selectbox("Department", dept_ids, key=f"{key}_dept",
                                 index=dept_ids.index(current.department_id) if current and current.department_id in dept_ids else 0,
                                 format_func=dict(zip(departments.department_id, departments.department_name)).get)
    roles = ref.roles_for(department_id)
    role_ids = roles.job_role_id.tolist()
    job_role_id = c2.selectbox("Job role", role_ids, key=f"{key}_role_{department_id}",
                               index=role_ids.index(current.job_role_id) if current and current.job_role_id in role_ids else 0,
                               format_func=dict(zip(roles.job_role_id, roles.role_name)).get)
    levels = ref.levels_for(job_role_id)
    level_ids = levels.job_level_id.tolist()
    job_level_id = c3.selectbox("Job level", level_ids, key=f"{key}_level_{job_role_id}",
                                index=level_ids.index(current.job_level_id) if current and current.job_level_id in level_ids else 0,
                                format_func=lambda lv: f"{lv} · {levels.set_index('job_level_id').level_title[lv]}")
    band = levels.set_index("job_level_id").loc[job_level_id]
    low, high = int(band.salary_band_min), int(band.salary_band_max)
    default = int(current.monthly_income) if current and low <= current.monthly_income <= high else (low + high) // 2
    c4, c5, c6 = st.columns(3)
    income = c4.slider("Monthly income (inside the level's band)", low, high, default, step=50,
                       key=f"{key}_pay_{job_level_id}")
    candidates = _manager_candidates(int(department_id), min(job_level_id + 1, 5))
    manager_ids = [None] + candidates.employee_id.tolist()
    names = dict(zip(candidates.employee_id, candidates.full_name + " · L" + candidates.job_level.astype(str)))
    manager_id = c5.selectbox("Manager", manager_ids, key=f"{key}_mgr_{department_id}_{job_level_id}",
                              format_func=lambda m: "— none —" if m is None else names[m],
                              index=1 if len(manager_ids) > 1 else 0)
    travel = c6.selectbox("Business travel", ["Non-Travel", "Travel_Rarely", "Travel_Frequently"], index=1,
                          key=f"{key}_travel")
    stock = st.select_slider("Stock option level", [0, 1, 2, 3],
                             value=current.stock_option_level if current else 0, key=f"{key}_stock")
    return dict(department_id=int(department_id), job_role_id=int(job_role_id), job_level_id=int(job_level_id),
                monthly_income=Decimal(income), manager_id=None if manager_id is None else int(manager_id),
                stock_option_level=stock, business_travel=travel)

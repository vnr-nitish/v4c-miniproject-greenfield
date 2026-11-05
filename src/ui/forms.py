"""Form building blocks shared by the data-entry pages."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import streamlit as st

from src.dal import ReferenceDataManager
from src.models import Position


@st.cache_data(ttl=3600, show_spinner=False)
def _manager_candidates(department_id: int, min_level: int):
    """Cached because the query reads the 100K-row current-employee view (~2 s on the cloud);
    data_changed() clears it when people join, move or leave."""
    return ReferenceDataManager().manager_candidates(department_id, min_level)


def _choice(key: str, options: list, default: Any) -> Any:
    """What a select box will return once it is drawn: the user's choice, else its default."""
    chosen = st.session_state.get(key, default)
    return chosen if chosen in options else options[0]


def position_picker(key: str, current: Position | None = None) -> dict:
    """Department -> only the roles it offers -> only the levels the role allows -> pay inside the
    level's band -> a manager from that department. Outside a form, so each choice narrows the next.

    Everything is read before the first field is drawn, so all the fields appear together. That
    needs the current choices up front, and Streamlit already holds them in session_state."""
    ref = ReferenceDataManager()
    with st.spinner("Preparing the form…"):
        ref.preload()
        departments = ref.departments()
        dept_ids = departments.department_id.tolist()
        department_id = _choice(f"{key}_dept", dept_ids, current.department_id if current else None)
        roles = ref.roles_for(department_id)
        role_ids = roles.job_role_id.tolist()
        role_key = f"{key}_role_{department_id}"
        job_role_id = _choice(role_key, role_ids, current.job_role_id if current else None)
        levels = ref.levels_for(job_role_id)
        level_ids = levels.job_level_id.tolist()
        level_key = f"{key}_level_{job_role_id}"
        job_level_id = _choice(level_key, level_ids, current.job_level_id if current else None)
        candidates = _manager_candidates(int(department_id), min(job_level_id + 1, 5))

    c1, c2, c3 = st.columns(3)
    c1.selectbox("Department", dept_ids, key=f"{key}_dept", index=dept_ids.index(department_id),
                 format_func=dict(zip(departments.department_id, departments.department_name)).get)
    c2.selectbox("Job role", role_ids, key=role_key, index=role_ids.index(job_role_id),
                 format_func=dict(zip(roles.job_role_id, roles.role_name)).get)
    c3.selectbox("Job level", level_ids, key=level_key, index=level_ids.index(job_level_id),
                 format_func=lambda lv: f"{lv} · {levels.set_index('job_level_id').level_title[lv]}")
    band = levels.set_index("job_level_id").loc[job_level_id]
    low, high = int(band.salary_band_min), int(band.salary_band_max)
    default = int(current.monthly_income) if current and low <= current.monthly_income <= high else (low + high) // 2
    c4, c5, c6, c7 = st.columns([3, 3, 2, 2])
    income = c4.slider("Monthly income (inside the level's band)", low, high, default, step=50,
                       key=f"{key}_pay_{job_level_id}")
    manager_ids = [None] + candidates.employee_id.tolist()
    names = dict(zip(candidates.employee_id, candidates.full_name + " · L" + candidates.job_level.astype(str)))
    manager_id = c5.selectbox("Manager", manager_ids, key=f"{key}_mgr_{department_id}_{job_level_id}",
                              format_func=lambda m: "— none —" if m is None else names[m],
                              index=1 if len(manager_ids) > 1 else 0)
    travel = c6.selectbox("Business travel", ["Non-Travel", "Travel_Rarely", "Travel_Frequently"], index=1,
                          key=f"{key}_travel")
    stock = c7.segmented_control("Stock option level", [0, 1, 2, 3], required=True, key=f"{key}_stock",
                                 default=current.stock_option_level if current else 0)
    return dict(department_id=int(department_id), job_role_id=int(job_role_id), job_level_id=int(job_level_id),
                monthly_income=Decimal(income), manager_id=None if manager_id is None else int(manager_id),
                stock_option_level=stock, business_travel=travel)

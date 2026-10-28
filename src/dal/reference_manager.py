"""ReferenceDataManager — the lookup lists behind the forms' drop-downs."""
from __future__ import annotations

import pandas as pd

from src.db_manager import DatabaseConnection
from src.exceptions import NotFoundError

_QUERIES = {
    "departments": "SELECT department_id, department_code, department_name "
                   "FROM hr_oltp.departments ORDER BY department_name",
    "job_roles": "SELECT djr.department_id, r.job_role_id, r.role_name, r.min_job_level_id, r.max_job_level_id "
                 "FROM hr_oltp.department_job_roles djr JOIN hr_oltp.job_roles r USING (job_role_id) "
                 "ORDER BY djr.department_id, r.role_name",
    "job_levels": "SELECT job_level_id, level_title, salary_band_min, salary_band_max "
                  "FROM hr_oltp.job_levels ORDER BY job_level_id",
    "education_levels": "SELECT education_level_id, level_name FROM hr_oltp.education_levels "
                        "ORDER BY education_level_id",
    "education_fields": "SELECT education_field_id, field_name FROM hr_oltp.education_fields ORDER BY field_name",
    "exit_reasons": "SELECT exit_reason_id, reason_name, is_voluntary FROM hr_oltp.exit_reasons "
                    "ORDER BY is_voluntary DESC, reason_name",
    "ratings": "SELECT rating_id, rating_label FROM hr_oltp.performance_rating_levels ORDER BY rating_id",
}


class ReferenceDataManager(DatabaseConnection):
    """Read-only access to the reference tables. They change rarely, so each list is read once
    and cached for the lifetime of the application; refresh() reloads them."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not hasattr(self, "_cache"):           # a Singleton: __init__ runs on every ReferenceDataManager()
            self._cache: dict[str, pd.DataFrame] = {}

    def _lookup(self, name: str) -> pd.DataFrame:
        if name not in self._cache:
            self._cache[name] = self.fetch_frame(_QUERIES[name])
        return self._cache[name].copy()           # callers may not change the cached list

    def refresh(self) -> None:
        self._cache.clear()

    def departments(self) -> pd.DataFrame:
        return self._lookup("departments")

    def roles_for(self, department_id: int) -> pd.DataFrame:
        """The job roles offered in a department, with their allowed level range."""
        roles = self._lookup("job_roles")
        return roles[roles.department_id == department_id].reset_index(drop=True)

    def levels_for(self, job_role_id: int) -> pd.DataFrame:
        """The job levels a role allows, with each level's salary band."""
        roles = self._lookup("job_roles")
        matches = roles[roles.job_role_id == job_role_id]
        if matches.empty:
            raise NotFoundError(f"Job role {job_role_id} not found")
        role = matches.iloc[0]
        levels = self._lookup("job_levels")
        return levels[levels.job_level_id.between(role.min_job_level_id, role.max_job_level_id)].reset_index(drop=True)

    def job_levels(self) -> pd.DataFrame:
        return self._lookup("job_levels")

    def education_levels(self) -> pd.DataFrame:
        return self._lookup("education_levels")

    def education_fields(self) -> pd.DataFrame:
        return self._lookup("education_fields")

    def exit_reasons(self) -> pd.DataFrame:
        return self._lookup("exit_reasons")

    def ratings(self) -> pd.DataFrame:
        return self._lookup("ratings")

    def manager_candidates(self, department_id: int, min_level: int = 1) -> pd.DataFrame:
        """Active employees of a department at or above a level — choices for a manager field.
        Not cached: people join, move and leave."""
        return self.fetch_frame(
            "SELECT employee_id, employee_code, full_name, job_role, job_level "
            "FROM hr_oltp.vw_employee_current "
            "WHERE is_active AND department_id = %s AND job_level >= %s "
            "ORDER BY job_level DESC, full_name", (department_id, min_level))

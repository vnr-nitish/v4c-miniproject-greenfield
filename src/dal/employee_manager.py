"""EmployeeManager — create, read and update employees; every change reaches the warehouse at once.

Employees are never deleted: their history and reviews must stay reportable, and every foreign
key is ON DELETE RESTRICT. Leaving is recorded with record_exit() instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.db_manager import DatabaseConnection
from src.exceptions import NotFoundError, ValidationError
from src.models import Employee, Position

logger = logging.getLogger(__name__)

_EMPLOYEE_SQL = ("SELECT employee_id, employee_code, first_name, last_name, email, phone, gender, date_of_birth, "
                 "marital_status, education_level_id, education_field_id, distance_from_home_km, "
                 "prior_experience_years, prior_companies_count, hire_date, exit_date, exit_reason_id "
                 "FROM hr_oltp.employees ")

_HISTORY_SQL = ("SELECT employee_sk, version_number, department_name, job_role, job_level, job_level_title, "
                "manager_name, monthly_income, salary_band, business_travel, change_reason, "
                "start_date, end_date, is_current "
                "FROM hr_dw.dim_employee WHERE employee_id = %s ORDER BY version_number")


@dataclass(frozen=True)
class PositionChange:
    """The outcome of a job change: the new OLTP version and the warehouse history after it."""

    employee_id: int
    history_id: int
    change_reason: str
    effective_date: date
    batch_id: int
    versions: pd.DataFrame          # every dim_employee version; the last two show the SCD Type 2 step

    @property
    def expired_version(self) -> pd.Series:
        return self.versions.iloc[-2]

    @property
    def new_version(self) -> pd.Series:
        return self.versions.iloc[-1]


class EmployeeManager(DatabaseConnection):
    """Data-access manager for employees and their job positions."""

    # ------------------------------------------------------------------ create

    def onboard(self, employee: Employee, position: Position) -> Employee:
        """Hire a person: OLTP rows and the first warehouse version, in one transaction.

        Returns the employee with the id and code the database assigned. If someone else is
        onboarded at the same moment and takes the next code first, the hire is retried with
        the code after it.
        """
        if employee.employee_id is not None or employee.exit_date is not None:
            raise ValidationError("A new hire has no employee id and no exit date yet")

        def hire() -> tuple[int, str]:
            with self.transaction() as cur:
                employee_id, employee_code = self.call_in(cur, "hr_oltp.sp_onboard_employee", (
                    employee.first_name, employee.last_name, employee.email, employee.phone, employee.gender,
                    employee.date_of_birth, employee.marital_status, employee.education_level_id,
                    employee.education_field_id, employee.distance_from_home_km, employee.prior_experience_years,
                    employee.prior_companies_count, employee.hire_date, position.department_id,
                    position.job_role_id, position.job_level_id, position.manager_id, position.monthly_income,
                    position.stock_option_level, position.business_travel), out_params=2)
                self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee_id,), out_params=1)
            return employee_id, employee_code

        employee_id, employee_code = self.run_with_retry(hire, unique_key="uq_employees_code")
        logger.info("Onboarded %s (id %s)", employee_code, employee_id)
        return employee.with_changes(employee_id=employee_id, employee_code=employee_code)

    # ------------------------------------------------------------------ read

    def get(self, employee_id: int) -> Employee:
        row = self.fetch_one(_EMPLOYEE_SQL + "WHERE employee_id = %s", (employee_id,))
        if row is None:
            raise NotFoundError(f"Employee {employee_id} not found")
        return Employee.from_row(row)

    def get_by_code(self, employee_code: str) -> Employee:
        row = self.fetch_one(_EMPLOYEE_SQL + "WHERE employee_code = %s", (employee_code.strip().upper(),))
        if row is None:
            raise NotFoundError(f"Employee {employee_code} not found")
        return Employee.from_row(row)

    def current_position(self, employee_id: int) -> Position:
        row = self.fetch_one(
            "SELECT department_id, job_role_id, job_level_id, manager_id, monthly_income, stock_option_level, "
            "business_travel FROM hr_oltp.employee_job_history "
            "WHERE employee_id = %s AND effective_end_date IS NULL", (employee_id,))
        if row is None:
            raise NotFoundError(f"Employee {employee_id} not found")
        return Position.from_row(row)

    def profile(self, employee_id: int) -> dict:
        """Everything about the employee's current state, with labels (department name, role, manager …)."""
        row = self.fetch_one("SELECT * FROM hr_oltp.vw_employee_current WHERE employee_id = %s", (employee_id,))
        if row is None:
            raise NotFoundError(f"Employee {employee_id} not found")
        return row

    def search(self, text: str | None = None, department_id: int | None = None,
               active_only: bool = True, limit: int = 100) -> pd.DataFrame:
        """Find employees by code or part of the name, optionally within a department."""
        text = (text or "").strip()
        return self.fetch_frame(
            "SELECT employee_id, employee_code, full_name, department_name, job_role, job_level, manager_name, "
            "       monthly_income, hire_date, exit_date, is_active "
            "FROM hr_oltp.vw_employee_current "
            "WHERE (%(text)s = '' OR employee_code = UPPER(%(text)s) "
            "       OR CONCAT(first_name, ' ', last_name) LIKE CONCAT('%%', %(text)s, '%%')) "
            "  AND (%(department)s IS NULL OR department_id = %(department)s) "
            "  AND (NOT %(active_only)s OR is_active) "
            "ORDER BY employee_code LIMIT %(limit)s",
            {"text": text, "department": department_id, "active_only": active_only, "limit": limit})

    def example_codes(self, limit: int = 8) -> list[str]:
        """Active employees with the longest SCD Type 2 history — good examples for the 360 view."""
        rows = self.fetch_all(
            "SELECT employee_code FROM hr_dw.dim_employee WHERE is_current AND exit_date IS NULL "
            "AND version_number >= 4 ORDER BY version_number DESC, employee_code LIMIT %s", (limit,))
        return [row["employee_code"] for row in rows]

    def history(self, employee_id: int) -> pd.DataFrame:
        """The employee's SCD Type 2 versions in the warehouse, oldest first."""
        return self.fetch_frame(_HISTORY_SQL, (employee_id,))

    # ------------------------------------------------------------------ update

    def update_details(self, employee: Employee) -> Employee:
        """Correct personal details (name, contact, marital status, education …) — SCD Type 1:
        the warehouse overwrites them on every version. Hire and exit have their own rules and
        are not changed here."""
        if employee.employee_id is None:
            raise ValidationError("The employee id is required to update an employee")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE hr_oltp.employees SET first_name = %s, last_name = %s, email = %s, phone = %s, "
                "gender = %s, date_of_birth = %s, marital_status = %s, education_level_id = %s, "
                "education_field_id = %s, distance_from_home_km = %s, prior_experience_years = %s, "
                "prior_companies_count = %s WHERE employee_id = %s",
                (employee.first_name, employee.last_name, employee.email, employee.phone, employee.gender,
                 employee.date_of_birth, employee.marital_status, employee.education_level_id,
                 employee.education_field_id, employee.distance_from_home_km, employee.prior_experience_years,
                 employee.prior_companies_count, employee.employee_id))
            if cur.rowcount == 0:
                raise NotFoundError(f"Employee {employee.employee_id} not found")
            self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee.employee_id,), out_params=1)
        return self.get(employee.employee_id)

    def change_position(self, employee_id: int, position: Position, effective_date: date | None = None,
                        change_reason: str | None = None) -> PositionChange:
        """Change department, role, level, manager or pay — the Phase 4 requirement.

        The OLTP change (close the current version, open a new one) and the warehouse SCD Type 2
        update (expire the old row, insert the new one) commit together or not at all.
        effective_date defaults to the database server's today; change_reason is derived when
        not given (Promotion > Transfer > Role Change > Manager Change > Salary Revision).
        """
        with self.transaction(dictionary=True) as cur:
            if effective_date is None:
                cur.execute("SELECT CURDATE() AS today")
                effective_date = cur.fetchone()["today"]
            (history_id,) = self.call_in(cur, "hr_oltp.sp_change_employee_position", (
                employee_id, position.department_id, position.job_role_id, position.job_level_id,
                position.manager_id, position.monthly_income, position.stock_option_level,
                position.business_travel, change_reason, effective_date), out_params=1)
            (batch_id,) = self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee_id,), out_params=1)
            cur.execute("SELECT change_reason FROM hr_oltp.employee_job_history WHERE history_id = %s",
                        (history_id,))
            reason = cur.fetchone()["change_reason"]
            versions = self.frame_in(cur, _HISTORY_SQL, (employee_id,))
        logger.info("Employee %s: %s from %s (warehouse batch %s)", employee_id, reason, effective_date, batch_id)
        return PositionChange(employee_id, history_id, reason, effective_date, batch_id, versions)

    def record_exit(self, employee_id: int, exit_date: date, exit_reason_id: int) -> int:
        """The employee leaves: exit recorded, open assignments ended, warehouse updated (Type 1:
        attrition flag and exit details on every version). Returns the assignments closed."""
        with self.transaction() as cur:
            (closed,) = self.call_in(cur, "hr_oltp.sp_record_exit", (employee_id, exit_date, exit_reason_id),
                                     out_params=1)
            self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee_id,), out_params=1)
        logger.info("Employee %s left on %s; %s assignments closed", employee_id, exit_date, closed)
        return closed

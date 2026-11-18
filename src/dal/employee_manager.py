"""EmployeeManager — create, read and update employees; every change reaches the warehouse at once.

Employees are never deleted: their history and reviews must stay reportable, and every foreign
key is ON DELETE RESTRICT. Leaving is recorded with record_exit() instead.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.db_manager import DatabaseConnection
from src.exceptions import NotFoundError, ValidationError
from src.models import Employee, Position

logger = logging.getLogger(__name__)

# The SELECT that reads employees; get() and get_by_code() add a WHERE to it.
_EMPLOYEE_SQL = ("SELECT employee_id, employee_code, first_name, last_name, email, phone, gender, date_of_birth, "
                 "marital_status, education_level_id, education_field_id, distance_from_home_km, "
                 "prior_experience_years, prior_companies_count, hire_date, exit_date, exit_reason_id "
                 "FROM hr_oltp.employees ")

# The warehouse versions (dim_employee) of one employee, oldest first.
_HISTORY_SQL = ("SELECT employee_sk, version_number, department_name, job_role, job_level, job_level_title, "
                "manager_name, monthly_income, salary_band, business_travel, change_reason, "
                "start_date, end_date, is_current "
                "FROM hr_dw.dim_employee WHERE employee_id = %s ORDER BY version_number")

# SQL pieces reused in search(): the full name, and the phone number with only its digits.
_FULL_NAME = "CONCAT(e.first_name, ' ', e.last_name)"
_PHONE_DIGITS = "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(e.phone, ' ', ''), '-', ''), '+', ''), '(', ''), ')', '')"
_NUMBER_OR_CODE = re.compile(r"(EMP)?\d+", re.IGNORECASE)
_PHONE_INPUT = re.compile(r"[\d +()-]+")


# The result of a job change, handed back to the page: ids, reason, batch, and the versions to show.
@dataclass(frozen=True)
class PositionChange:
    """The outcome of a job change: the new OLTP version and the warehouse history after it."""

    employee_id: int
    history_id: int
    change_reason: str
    effective_date: date
    batch_id: int
    versions: pd.DataFrame          # every dim_employee version; the last two show the SCD Type 2 step

    # The version that was just closed.
    @property
    def expired_version(self) -> pd.Series:
        return self.versions.iloc[-2]

    # The new current version.
    @property
    def new_version(self) -> pd.Series:
        return self.versions.iloc[-1]


# OOP (Inheritance): EmployeeManager IS-A DatabaseConnection, so it gets the shared pool, transaction() and
# error handling. Encapsulation: all employee SQL lives here; pages only call its methods.
class EmployeeManager(DatabaseConnection):
    """Data-access manager for employees and their job positions."""

    # ------------------------------------------------------------------ create

    # CREATE (the C of CRUD), called by the Onboard page.
    def onboard(self, employee: Employee, position: Position) -> Employee:
        """Hire a person: OLTP rows and the first warehouse version, in one transaction.

        Returns the employee with the id and code the database assigned. If someone else is
        onboarded at the same moment and takes the next code first, the hire is retried with
        the code after it.
        """
        # a new hire can't already have an id or an exit date
        if employee.employee_id is not None or employee.exit_date is not None:
            raise ValidationError("A new hire has no employee id and no exit date yet")

        # The work to do, as a small function, so run_with_retry() can run it again if needed.
        def hire() -> tuple[int, str]:
            # One transaction for both steps below: either both are saved or neither is.
            with self.transaction() as cur:
                # Step 1, OLTP: the procedure checks the position, makes the next code (EMP + 6 digits) and
                # inserts the rows into hr_oltp.employees and employee_job_history (change reason 'Hire').
                employee_id, employee_code = self.call_in(cur, "hr_oltp.sp_onboard_employee", (
                    employee.first_name, employee.last_name, employee.email, employee.phone, employee.gender,
                    employee.date_of_birth, employee.marital_status, employee.education_level_id,
                    employee.education_field_id, employee.distance_from_home_km, employee.prior_experience_years,
                    employee.prior_companies_count, employee.hire_date, position.department_id,
                    position.job_role_id, position.job_level_id, position.manager_id, position.monthly_income,
                    position.stock_option_level, position.business_travel), out_params=2)
                # Step 2, warehouse: the employee gets their first hr_dw.dim_employee row (SCD Type 2 version 1).
                self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee_id,), out_params=1)
            return employee_id, employee_code

        # Run it; if another user took the same new code at the same moment, try again with the next one.
        employee_id, employee_code = self.run_with_retry(hire, unique_key="uq_employees_code")
        logger.info("Onboarded %s (id %s)", employee_code, employee_id)
        # give back the employee with the id and code the database assigned
        return employee.with_changes(employee_id=employee_id, employee_code=employee_code)

    # ------------------------------------------------------------------ read

    # READ: one employee from hr_oltp.employees, returned as an Employee object.
    def get(self, employee_id: int) -> Employee:
        row = self.fetch_one(_EMPLOYEE_SQL + "WHERE employee_id = %s", (employee_id,))
        if row is None:
            raise NotFoundError(f"Employee {employee_id} not found")
        return Employee.from_row(row)

    # READ by code; 'emp000123' works too (made upper-case).
    def get_by_code(self, employee_code: str) -> Employee:
        row = self.fetch_one(_EMPLOYEE_SQL + "WHERE employee_code = %s", (employee_code.strip().upper(),))
        if row is None:
            raise NotFoundError(f"Employee {employee_code} not found")
        return Employee.from_row(row)

    # READ the open job version (no end date) as a Position object.
    def current_position(self, employee_id: int) -> Position:
        row = self.fetch_one(
            "SELECT department_id, job_role_id, job_level_id, manager_id, monthly_income, stock_option_level, "
            "business_travel FROM hr_oltp.employee_job_history "
            "WHERE employee_id = %s AND effective_end_date IS NULL", (employee_id,))
        if row is None:
            raise NotFoundError(f"Employee {employee_id} not found")
        return Position.from_row(row)

    # READ the view vw_employee_current: one row with names instead of ids.
    def profile(self, employee_id: int) -> dict:
        """Everything about the employee's current state, with labels (department name, role, manager …)."""
        row = self.fetch_one("SELECT * FROM hr_oltp.vw_employee_current WHERE employee_id = %s", (employee_id,))
        if row is None:
            raise NotFoundError(f"Employee {employee_id} not found")
        return row

    def search(self, text: str | None = None, department_id: int | None = None,
               active_only: bool = True, limit: int = 100) -> pd.DataFrame:
        """Find employees by name, e-mail, phone number, employee code or id; any part of a name,
        an e-mail or a phone number will do. Exact matches come first (match_rank 0).

        The matching runs on hr_oltp.employees alone and only the people found are joined to their
        job, because going through the 100K-row view for every search takes seconds on the cloud.
        """
        # tidy the search text: single spaces only
        text = " ".join((text or "").split())
        # only the digits, for finding ids and phone numbers
        digits = re.sub(r"\D", "", text)
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")     # typed literally
        # the values for the %(name)s placeholders in the SQL below
        params = {"text": text, "code": text.upper(), "contains": f"%{escaped}%", "starts": f"{escaped}%",
                  "department": department_id, "active_only": active_only, "limit": limit}
        # conditions for an exact match (shown first) and for a partial match
        exact = ["e.employee_code = %(code)s", "e.email = %(text)s"]
        partial = [f"{_FULL_NAME} LIKE %(contains)s", "e.email LIKE %(contains)s"]
        if digits and _NUMBER_OR_CODE.fullmatch(text):                                   # 220, EMP220, EMP000220
            params.update(employee_id=int(digits), code=f"EMP{int(digits):06d}")
            exact.append("e.employee_id = %(employee_id)s")
        # it looks like a phone number: also compare digit by digit
        if len(digits) >= 4 and _PHONE_INPUT.fullmatch(text):
            params.update(digits=digits, digits_within=f"%{digits}%")
            exact.append(f"{_PHONE_DIGITS} = %(digits)s")
            partial.append(f"{_PHONE_DIGITS} LIKE %(digits_within)s")
        # inner query: the matching ids (rank 0 exact, 1 name starts with it, 2 contains it);
        # outer query: their current job from the view vw_employee_current
        return self.fetch_frame(
            f"""SELECT v.employee_id, v.employee_code, v.full_name, v.email, v.phone, v.department_name, v.job_role,
                       v.job_level, v.manager_name, v.monthly_income, v.hire_date, v.exit_date, v.is_active, m.match_rank
                FROM (SELECT e.employee_id,
                             CASE WHEN {' OR '.join(exact)} THEN 0
                                  WHEN {_FULL_NAME} LIKE %(starts)s THEN 1 ELSE 2 END AS match_rank
                      FROM hr_oltp.employees e
                      WHERE (%(text)s = '' OR {' OR '.join(exact + partial)})
                        AND (NOT %(active_only)s OR e.is_active)
                        AND (%(department)s IS NULL OR EXISTS (
                                SELECT 1 FROM hr_oltp.employee_job_history h
                                WHERE h.employee_id = e.employee_id AND h.effective_end_date IS NULL
                                  AND h.department_id = %(department)s))
                      ORDER BY match_rank, e.employee_code
                      LIMIT %(limit)s) m
                JOIN hr_oltp.vw_employee_current v ON v.employee_id = m.employee_id
                ORDER BY m.match_rank, v.employee_code""", params)

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

    # UPDATE personal details (SCD Type 1 in the warehouse: corrected on every version).
    def update_details(self, employee: Employee) -> Employee:
        """Correct personal details (name, contact, marital status, education …) — SCD Type 1:
        the warehouse overwrites them on every version. Hire and exit have their own rules and
        are not changed here."""
        # an update needs to know which employee
        if employee.employee_id is None:
            raise ValidationError("The employee id is required to update an employee")
        with self.transaction() as cur:
            # overwrite the personal details in hr_oltp.employees
            cur.execute(
                "UPDATE hr_oltp.employees SET first_name = %s, last_name = %s, email = %s, phone = %s, "
                "gender = %s, date_of_birth = %s, marital_status = %s, education_level_id = %s, "
                "education_field_id = %s, distance_from_home_km = %s, prior_experience_years = %s, "
                "prior_companies_count = %s WHERE employee_id = %s",
                (employee.first_name, employee.last_name, employee.email, employee.phone, employee.gender,
                 employee.date_of_birth, employee.marital_status, employee.education_level_id,
                 employee.education_field_id, employee.distance_from_home_km, employee.prior_experience_years,
                 employee.prior_companies_count, employee.employee_id))
            # no row changed = there is no such employee
            if cur.rowcount == 0:
                raise NotFoundError(f"Employee {employee.employee_id} not found")
            # same transaction: the warehouse corrects these details on every version (SCD Type 1)
            self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee.employee_id,), out_params=1)
        return self.get(employee.employee_id)

    # UPDATE the job (department, role, level, manager, pay): the SCD Type 2 requirement.
    def change_position(self, employee_id: int, position: Position, effective_date: date | None = None,
                        change_reason: str | None = None) -> PositionChange:
        """Change department, role, level, manager or pay — the Phase 4 requirement.

        The OLTP change (close the current version, open a new one) and the warehouse SCD Type 2
        update (expire the old row, insert the new one) commit together or not at all.
        effective_date defaults to the database server's today; change_reason is derived when
        not given (Promotion > Transfer > Role Change > Manager Change > Salary Revision).
        """
        # one transaction for the OLTP change and the warehouse step
        with self.transaction(dictionary=True) as cur:
            # no date chosen: use the database's today
            if effective_date is None:
                cur.execute("SELECT CURDATE() AS today")
                effective_date = cur.fetchone()["today"]
            # OLTP: close the current job version (it gets an end date) and insert the new one.
            (history_id,) = self.call_in(cur, "hr_oltp.sp_change_employee_position", (
                employee_id, position.department_id, position.job_role_id, position.job_level_id,
                position.manager_id, position.monthly_income, position.stock_option_level,
                position.business_travel, change_reason, effective_date), out_params=1)
            # Warehouse, same transaction: expire the old dim_employee row (end_date, is_current = 0) and insert the
            # new current version.
            (batch_id,) = self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee_id,), out_params=1)
            # read the reason the procedure chose (Promotion, Transfer ...)
            cur.execute("SELECT change_reason FROM hr_oltp.employee_job_history WHERE history_id = %s",
                        (history_id,))
            reason = cur.fetchone()["change_reason"]
            # and the warehouse versions, to show them in the pop-up
            versions = self.frame_in(cur, _HISTORY_SQL, (employee_id,))
        logger.info("Employee %s: %s from %s (warehouse batch %s)", employee_id, reason, effective_date, batch_id)
        return PositionChange(employee_id, history_id, reason, effective_date, batch_id, versions)

    # Employees are never deleted, because their history must stay reportable: leaving is an exit date.
    def record_exit(self, employee_id: int, exit_date: date, exit_reason_id: int) -> int:
        """The employee leaves: exit recorded, open assignments ended, warehouse updated (Type 1:
        attrition flag and exit details on every version). Returns the assignments closed."""
        with self.transaction() as cur:
            (closed,) = self.call_in(cur, "hr_oltp.sp_record_exit", (employee_id, exit_date, exit_reason_id),
                                     out_params=1)
            self.call_in(cur, "hr_dw.sp_scd2_apply_employee", (employee_id,), out_params=1)
        logger.info("Employee %s left on %s; %s assignments closed", employee_id, exit_date, closed)
        return closed

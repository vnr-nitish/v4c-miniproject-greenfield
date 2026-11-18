"""Employee (the person) and Position (the job they hold) — mirroring the OLTP split between
hr_oltp.employees and hr_oltp.employee_job_history."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from src.models.base import (Entity, Rules, as_date, as_decimal, full_years, in_range, is_date, is_int,
                             is_text)

# The allowed values, the same as the database's CHECK constraints.
GENDERS = ("Male", "Female")
MARITAL_STATUSES = ("Single", "Married", "Divorced")
BUSINESS_TRAVEL = ("Non-Travel", "Travel_Rarely", "Travel_Frequently")

# Patterns (regular expressions) the text fields must match: EMP + 6 digits, a name, an e-mail, a phone.
_CODE = re.compile(r"EMP\d{6}")
_NAME = re.compile(r"[^\W\d_]+([ .'’-][^\W\d_]+)*\.?")           # letters, single space . ' ’ - between (D’Alia)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d ()-]{6,18}")


# OOP (Inheritance): Employee gets validation and immutability from Entity. It mirrors the
# hr_oltp.employees table: one attribute per column.
@dataclass(frozen=True)
class Employee(Entity):
    """A person employed by the company. Their job lives in Position (with its history)."""

    # The attributes = the columns of hr_oltp.employees; ': str', ': date' say what type each one holds.
    first_name: str
    last_name: str
    email: str
    phone: str
    gender: str
    date_of_birth: date
    marital_status: str
    education_level_id: int
    education_field_id: int
    distance_from_home_km: int
    prior_experience_years: int
    prior_companies_count: int
    hire_date: date
    # '| None = None' means optional: empty for a new or current employee
    exit_date: date | None = None
    exit_reason_id: int | None = None
    employee_id: int | None = None
    employee_code: str | None = None

    # Tidy the input: trim spaces, e-mail in lower case, datetimes to dates.
    def _normalize(self) -> None:
        for name in ("first_name", "last_name", "email", "phone"):
            value = getattr(self, name)
            if isinstance(value, str):
                self._set(name, value.strip())
        if isinstance(self.email, str):
            self._set("email", self.email.lower())
        for name in ("date_of_birth", "hire_date", "exit_date"):
            self._set(name, as_date(getattr(self, name)))

    # The Employee's own rules (polymorphism: Position, Project and Review have different ones). These are the
    # checks behind the warnings on the Onboard form.
    def validation_errors(self) -> dict[str, str]:
        # collect every broken rule: r.require(condition, field, message) adds the message when the condition is False
        r = Rules()
        r.require(self.employee_code is None or (isinstance(self.employee_code, str)
                                                 and _CODE.fullmatch(self.employee_code) is not None),
                  "employee_code", "Employee code must look like EMP000123")
        for name, label in (("first_name", "First name"), ("last_name", "Last name")):
            value = getattr(self, name)
            r.require(is_text(value, 50) and _NAME.fullmatch(value) is not None, name,
                      f"{label} must be 1-50 letters (spaces, hyphens and apostrophes allowed)")
        r.require(is_text(self.email, 120) and _EMAIL.fullmatch(self.email) is not None,
                  "email", "Enter a valid e-mail address")
        r.require(is_text(self.phone, 20) and _PHONE.fullmatch(self.phone) is not None,
                  "phone", "Enter a valid phone number (7-20 digits, may start with +)")
        r.require(self.gender in GENDERS, "gender", f"Gender must be one of {', '.join(GENDERS)}")
        r.require(self.marital_status in MARITAL_STATUSES, "marital_status",
                  f"Marital status must be one of {', '.join(MARITAL_STATUSES)}")
        r.require(in_range(self.education_level_id, 1, 5) and is_int(self.education_level_id),
                  "education_level_id", "Choose an education level")
        r.require(is_int(self.education_field_id) and self.education_field_id >= 1,
                  "education_field_id", "Choose an education field")
        r.require(is_int(self.distance_from_home_km) and 1 <= self.distance_from_home_km <= 50,
                  "distance_from_home_km", "Distance from home must be 1-50 km")
        r.require(is_int(self.prior_experience_years) and 0 <= self.prior_experience_years <= 50,
                  "prior_experience_years", "Prior experience must be 0-50 years")
        r.require(is_int(self.prior_companies_count) and 0 <= self.prior_companies_count <= 20,
                  "prior_companies_count", "Previous employers must be 0-20")
        # decision D19: experience before joining means at least one previous employer, and vice versa
        r.require(not (is_int(self.prior_experience_years) and is_int(self.prior_companies_count))
                  or (self.prior_experience_years == 0) == (self.prior_companies_count == 0),
                  "prior_companies_count",
                  "Previous employers must be 0 exactly when there is no prior experience")
        r.require(is_date(self.date_of_birth), "date_of_birth", "Enter the date of birth")
        r.require(is_date(self.hire_date), "hire_date", "Enter the hire date")
        if is_date(self.date_of_birth) and is_date(self.hire_date):
            r.require(full_years(self.date_of_birth, self.hire_date) >= 18, "hire_date",
                      "An employee must be at least 18 on the hire date")
        r.require(self.exit_date is None or is_date(self.exit_date), "exit_date", "Enter a valid exit date")
        r.require((self.exit_date is None) == (self.exit_reason_id is None), "exit_reason_id",
                  "Exit date and exit reason must be given together")
        if is_date(self.exit_date) and is_date(self.hire_date):
            r.require(self.exit_date >= self.hire_date, "exit_date", "The exit date cannot be before the hire date")
        return r.errors

    # ------------------------------------------------------------------ behaviour

    # Behaviour that belongs to the object: computed from its attributes, never stored.
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    # True while the employee hasn't left.
    @property
    def is_active(self) -> bool:
        return self.exit_date is None

    # Age in whole years on a given day.
    def age_on(self, day: date) -> int:
        return full_years(self.date_of_birth, day)

    def tenure_on(self, day: date) -> int:
        """Completed years at the company on a given day (counted up to the exit date at most)."""
        end = min(day, self.exit_date) if self.exit_date else day
        return max(0, full_years(self.hire_date, end))

    def total_experience_on(self, day: date) -> int:
        """Prior experience plus tenure — IBM's TotalWorkingYears."""
        return self.prior_experience_years + self.tenure_on(day)


# A job position = one row of hr_oltp.employee_job_history. Every job change creates a new Position
# version, and that history is what SCD Type 2 copies into the warehouse.
@dataclass(frozen=True)
class Position(Entity):
    """A job: department, role, level, manager and pay. Every change becomes a new version."""

    # The attributes = the columns of one hr_oltp.employee_job_history row.
    department_id: int
    job_role_id: int
    job_level_id: int
    monthly_income: Decimal
    stock_option_level: int = 0
    business_travel: str = "Travel_Rarely"
    manager_id: int | None = None

    # Pay becomes an exact 2-decimal amount.
    def _normalize(self) -> None:
        self._set("monthly_income", as_decimal(self.monthly_income))

    # Rules that need only the position itself. Rules that need other tables (is this role offered in this
    # department?) are checked in MySQL by the stored procedure sp_validate_position.
    def validation_errors(self) -> dict[str, str]:
        r = Rules()
        r.require(is_int(self.department_id) and self.department_id >= 1, "department_id", "Choose a department")
        r.require(is_int(self.job_role_id) and self.job_role_id >= 1, "job_role_id", "Choose a job role")
        r.require(is_int(self.job_level_id) and 1 <= self.job_level_id <= 5, "job_level_id", "Job level must be 1-5")
        r.require(in_range(self.monthly_income, Decimal("0.01"), Decimal("99999999.99")),
                  "monthly_income", "Monthly income must be a positive amount")
        r.require(is_int(self.stock_option_level) and 0 <= self.stock_option_level <= 3,
                  "stock_option_level", "Stock option level must be 0-3")
        r.require(self.business_travel in BUSINESS_TRAVEL, "business_travel",
                  f"Business travel must be one of {', '.join(BUSINESS_TRAVEL)}")
        r.require(self.manager_id is None or (is_int(self.manager_id) and self.manager_id >= 1),
                  "manager_id", "Choose a valid manager")
        return r.errors

    # ------------------------------------------------------------------ behaviour

    # Compares with the previous position and lists what changed, e.g. {'department_id': (3, 2)}.
    def changes_from(self, previous: "Position") -> dict[str, tuple[Any, Any]]:
        """What differs from an earlier position, as {attribute: (old, new)}."""
        return {name: (getattr(previous, name), getattr(self, name))
                for name in ("department_id", "job_role_id", "job_level_id", "manager_id",
                             "monthly_income", "stock_option_level", "business_travel")
                if getattr(previous, name) != getattr(self, name)}

    # True if the new level is higher.
    def is_promotion_from(self, previous: "Position") -> bool:
        return self.job_level_id > previous.job_level_id

    # True if the department changed.
    def is_transfer_from(self, previous: "Position") -> bool:
        return self.department_id != previous.department_id

"""Project and Assignment (an employee's allocation to a project)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.models.base import Entity, Rules, as_date, as_decimal, in_range, is_date, is_int, is_text

PROJECT_STATUSES = ("Planned", "Active", "On Hold", "Completed", "Cancelled")
CLOSED_STATUSES = ("Completed", "Cancelled")
PRIORITIES = ("Low", "Medium", "High", "Critical")

_CODE = re.compile(r"PRJ\d{5}")


@dataclass(frozen=True)
class Project(Entity):
    """A project owned by a department; planned vs actual end makes delays measurable."""

    project_name: str
    department_id: int
    start_date: date
    planned_end_date: date
    budget: Decimal
    status: str = "Planned"
    priority: str = "Medium"
    description: str | None = None
    project_manager_id: int | None = None
    actual_end_date: date | None = None
    project_id: int | None = None
    project_code: str | None = None

    def _normalize(self) -> None:
        if isinstance(self.project_name, str):
            self._set("project_name", self.project_name.strip())
        if isinstance(self.description, str):
            self._set("description", self.description.strip() or None)
        self._set("budget", as_decimal(self.budget))
        for name in ("start_date", "planned_end_date", "actual_end_date"):
            self._set(name, as_date(getattr(self, name)))

    def validation_errors(self) -> dict[str, str]:
        r = Rules()
        r.require(self.project_code is None or (isinstance(self.project_code, str)
                                                and _CODE.fullmatch(self.project_code) is not None),
                  "project_code", "Project code must look like PRJ00123")
        r.require(is_text(self.project_name, 120), "project_name", "Project name must be 1-120 characters")
        r.require(self.description is None or is_text(self.description, 500, 0), "description",
                  "Description can be at most 500 characters")
        r.require(is_int(self.department_id) and self.department_id >= 1, "department_id", "Choose a department")
        r.require(self.project_manager_id is None or (is_int(self.project_manager_id) and self.project_manager_id >= 1),
                  "project_manager_id", "Choose a valid project manager")
        r.require(in_range(self.budget, Decimal("0.01"), Decimal("999999999999.99")), "budget",
                  "Budget must be a positive amount")
        r.require(self.status in PROJECT_STATUSES, "status", f"Status must be one of {', '.join(PROJECT_STATUSES)}")
        r.require(self.priority in PRIORITIES, "priority", f"Priority must be one of {', '.join(PRIORITIES)}")
        r.require(is_date(self.start_date), "start_date", "Enter the start date")
        r.require(is_date(self.planned_end_date), "planned_end_date", "Enter the planned end date")
        if is_date(self.start_date) and is_date(self.planned_end_date):
            r.require(self.planned_end_date >= self.start_date, "planned_end_date",
                      "The planned end cannot be before the start")
        r.require(self.actual_end_date is None or is_date(self.actual_end_date), "actual_end_date",
                  "Enter a valid actual end date")
        if is_date(self.actual_end_date) and is_date(self.start_date):
            r.require(self.actual_end_date >= self.start_date, "actual_end_date",
                      "The actual end cannot be before the start")
        r.require((self.status in CLOSED_STATUSES) == (self.actual_end_date is not None), "actual_end_date",
                  "Completed and cancelled projects need an actual end date; open projects have none")
        return r.errors

    # ------------------------------------------------------------------ behaviour

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def planned_duration_days(self) -> int:
        return (self.planned_end_date - self.start_date).days

    @property
    def delay_days(self) -> int | None:
        """Days late (negative = early) — measured for completed projects only, as in the warehouse."""
        return (self.actual_end_date - self.planned_end_date).days if self.status == "Completed" else None

    @property
    def is_delayed(self) -> bool | None:
        return None if self.delay_days is None else self.delay_days > 0

    def is_overdue_on(self, day: date) -> bool:
        """Still open although the planned end has passed."""
        return not self.is_closed and day > self.planned_end_date

    def close(self, status: str, on: date) -> "Project":
        """The project completed or cancelled on a given day (a new, validated object)."""
        return self.with_changes(status=status, actual_end_date=on)


@dataclass(frozen=True)
class Assignment(Entity):
    """An employee allocated to a project for a period (allocation in % of their time)."""

    employee_id: int
    project_id: int
    project_role: str
    allocation_pct: int
    start_date: date
    end_date: date | None = None
    assignment_id: int | None = None

    def _normalize(self) -> None:
        if isinstance(self.project_role, str):
            self._set("project_role", self.project_role.strip())
        self._set("start_date", as_date(self.start_date))
        self._set("end_date", as_date(self.end_date))

    def validation_errors(self) -> dict[str, str]:
        r = Rules()
        r.require(is_int(self.employee_id) and self.employee_id >= 1, "employee_id", "Choose an employee")
        r.require(is_int(self.project_id) and self.project_id >= 1, "project_id", "Choose a project")
        r.require(is_text(self.project_role, 30), "project_role", "Project role must be 1-30 characters")
        r.require(is_int(self.allocation_pct) and 1 <= self.allocation_pct <= 100, "allocation_pct",
                  "Allocation must be 1-100 %")
        r.require(is_date(self.start_date), "start_date", "Enter the start date")
        r.require(self.end_date is None or is_date(self.end_date), "end_date", "Enter a valid end date")
        if is_date(self.start_date) and is_date(self.end_date):
            r.require(self.end_date >= self.start_date, "end_date", "An assignment cannot end before it starts")
        return r.errors

    # ------------------------------------------------------------------ behaviour

    @property
    def is_open(self) -> bool:
        return self.end_date is None

    def active_on(self, day: date) -> bool:
        return self.start_date <= day and (self.end_date is None or day <= self.end_date)

    def end(self, on: date) -> "Assignment":
        return self.with_changes(end_date=on)

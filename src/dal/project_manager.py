"""ProjectManager — projects and the employees assigned to them (full CRUD)."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import pandas as pd

from src.db_manager import DatabaseConnection
from src.exceptions import BusinessRuleError, NotFoundError, OverAllocationError, ValidationError
from src.models import Assignment, Project
from src.models.project import CLOSED_STATUSES

logger = logging.getLogger(__name__)

_PROJECT_SQL = ("SELECT project_id, project_code, project_name, description, department_id, project_manager_id, "
                "start_date, planned_end_date, actual_end_date, budget, status, priority FROM hr_oltp.projects ")


class ProjectManager(DatabaseConnection):
    """Data-access manager for projects and project assignments."""

    # ------------------------------------------------------------------ projects

    def create(self, project: Project) -> Project:
        """Create a project; the next project code (PRJ + 5 digits) is assigned by the database
        (retried with the code after it if a simultaneous insert took it first)."""
        if project.project_id is not None:
            raise ValidationError("A new project has no project id yet")

        def insert() -> tuple[int, str]:
            with self.transaction() as cur:
                self._require_active_employee(cur, project.project_manager_id, "The project manager")
                cur.execute(
                    "INSERT INTO hr_oltp.projects (project_code, project_name, description, department_id, "
                    "       project_manager_id, start_date, planned_end_date, actual_end_date, budget, status, "
                    "       priority) "
                    "SELECT CONCAT('PRJ', LPAD(COALESCE(CAST(SUBSTRING(MAX(project_code), 4) AS UNSIGNED), 0) + 1, "
                    "                          5, '0')), "
                    "       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s "
                    "FROM hr_oltp.projects",
                    (project.project_name, project.description, project.department_id, project.project_manager_id,
                     project.start_date, project.planned_end_date, project.actual_end_date, project.budget,
                     project.status, project.priority))
                project_id = cur.lastrowid
                cur.execute("SELECT project_code FROM hr_oltp.projects WHERE project_id = %s", (project_id,))
                (code,) = cur.fetchone()
            return project_id, code

        project_id, code = self.run_with_retry(insert, unique_key="uq_projects_code")
        logger.info("Created project %s (id %s)", code, project_id)
        return project.with_changes(project_id=project_id, project_code=code)

    def get(self, project_id: int) -> Project:
        row = self.fetch_one(_PROJECT_SQL + "WHERE project_id = %s", (project_id,))
        if row is None:
            raise NotFoundError(f"Project {project_id} not found")
        return Project.from_row(row)

    def search(self, text: str | None = None, status: str | None = None, department_id: int | None = None,
               limit: int = 500) -> pd.DataFrame:
        """Projects by code or part of the name, status and department — with department and
        manager names, newest first."""
        return self.fetch_frame(
            "SELECT p.project_id, p.project_code, p.project_name, d.department_name, "
            "       CONCAT(m.first_name, ' ', m.last_name) AS project_manager, p.status, p.priority, "
            "       p.start_date, p.planned_end_date, p.actual_end_date, p.budget "
            "FROM hr_oltp.projects p "
            "JOIN hr_oltp.departments d ON d.department_id = p.department_id "
            "LEFT JOIN hr_oltp.employees m ON m.employee_id = p.project_manager_id "
            "WHERE (%(status)s IS NULL OR p.status = %(status)s) "
            "  AND (%(department)s IS NULL OR p.department_id = %(department)s) "
            "  AND (%(text)s = '' OR p.project_code = UPPER(%(text)s) OR p.project_name LIKE CONCAT('%%', %(text)s, '%%')) "
            "ORDER BY p.start_date DESC, p.project_id DESC LIMIT %(limit)s",
            {"status": status, "department": department_id, "text": (text or "").strip(), "limit": limit})

    def update(self, project: Project) -> Project:
        """Save changed project details. Closing a project (Completed / Cancelled) also ends the
        open assignments on the actual end date."""
        if project.project_id is None:
            raise ValidationError("The project id is required to update a project")
        with self.transaction() as cur:
            cur.execute("SELECT project_manager_id FROM hr_oltp.projects WHERE project_id = %s FOR UPDATE",
                        (project.project_id,))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"Project {project.project_id} not found")
            if project.project_manager_id != row[0]:
                self._require_active_employee(cur, project.project_manager_id, "The project manager")
            cur.execute(
                "UPDATE hr_oltp.projects SET project_name = %s, description = %s, department_id = %s, "
                "project_manager_id = %s, start_date = %s, planned_end_date = %s, actual_end_date = %s, "
                "budget = %s, status = %s, priority = %s WHERE project_id = %s",
                (project.project_name, project.description, project.department_id, project.project_manager_id,
                 project.start_date, project.planned_end_date, project.actual_end_date, project.budget,
                 project.status, project.priority, project.project_id))
            if project.is_closed:
                cur.execute(
                    "UPDATE hr_oltp.project_assignments SET end_date = GREATEST(start_date, %s) "
                    "WHERE project_id = %s AND (end_date IS NULL OR end_date > %s)",
                    (project.actual_end_date, project.project_id, project.actual_end_date))
                logger.info("Project %s closed; %s assignments ended", project.project_id, cur.rowcount)
        return self.get(project.project_id)

    def delete(self, project_id: int) -> None:
        """Delete a project created by mistake: only while it is Planned and nobody is assigned or
        reviewed on it (otherwise cancel it — history is never deleted)."""
        with self.transaction() as cur:
            cur.execute("SELECT status FROM hr_oltp.projects WHERE project_id = %s FOR UPDATE", (project_id,))
            row = cur.fetchone()
            if row is None:
                raise NotFoundError(f"Project {project_id} not found")
            if row[0] != "Planned":
                raise BusinessRuleError(f"Only planned projects can be deleted; this one is {row[0]} — cancel it instead")
            cur.execute("DELETE FROM hr_oltp.projects WHERE project_id = %s", (project_id,))
            # It may already be in the warehouse; without reviews no fact row can point to it.
            # Like every warehouse change it is a logged batch (the analytics cache depends on it).
            cur.execute("DELETE FROM hr_dw.dim_project WHERE project_id = %s", (project_id,))
            if cur.rowcount:
                (batch_id,) = self.call_in(cur, "hr_dw.sp_etl_start_batch", (f"delete project {project_id}",),
                                           out_params=1)
                self.call_in(cur, "hr_dw.sp_etl_log_step",
                             (batch_id, "delete dim_project", 1, "SUCCESS", None, datetime.now()))
                self.call_in(cur, "hr_dw.sp_etl_finish_batch", (batch_id, None))
        logger.info("Deleted project %s", project_id)

    # ------------------------------------------------------------------ assignments

    def assign(self, assignment: Assignment, allow_over_allocation: bool = False) -> Assignment:
        """Allocate an employee to an open project.

        Refuses closed projects, start dates before the project starts, and people who are not
        employed on the start date. If the employee's total allocation on the start date would
        exceed 100 %, raises OverAllocationError — unless allow_over_allocation is True (the
        form's "assign anyway"); over-allocation is what the bottleneck analysis looks for.
        """
        if assignment.assignment_id is not None:
            raise ValidationError("A new assignment has no assignment id yet")
        with self.transaction() as cur:
            cur.execute("SELECT status, start_date FROM hr_oltp.projects WHERE project_id = %s",
                        (assignment.project_id,))
            project = cur.fetchone()
            if project is None:
                raise NotFoundError(f"Project {assignment.project_id} not found")
            status, project_start = project
            if status in CLOSED_STATUSES:
                raise BusinessRuleError(f"The project is {status}; people can no longer be assigned to it")
            if assignment.start_date < project_start:
                raise BusinessRuleError(f"The assignment cannot start before the project ({project_start})")
            self._require_active_employee(cur, assignment.employee_id, "The employee", on=assignment.start_date)
            current = self._allocation_on(cur, assignment.employee_id, assignment.start_date)
            if current + assignment.allocation_pct > 100 and not allow_over_allocation:
                raise OverAllocationError(
                    f"The employee is already allocated {current} % on {assignment.start_date}; "
                    f"adding {assignment.allocation_pct} % makes {current + assignment.allocation_pct} %",
                    current, assignment.allocation_pct)
            cur.execute(
                "INSERT INTO hr_oltp.project_assignments (employee_id, project_id, project_role, allocation_pct, "
                "start_date, end_date) VALUES (%s, %s, %s, %s, %s, %s)",
                (assignment.employee_id, assignment.project_id, assignment.project_role, assignment.allocation_pct,
                 assignment.start_date, assignment.end_date))
            assignment_id = cur.lastrowid
        return assignment.with_changes(assignment_id=assignment_id)

    def end_assignment(self, assignment_id: int, end_date: date) -> None:
        """The employee stops working on the project after end_date."""
        self._change_assignment("UPDATE hr_oltp.project_assignments SET end_date = %s WHERE assignment_id = %s",
                                (end_date, assignment_id), assignment_id)

    def remove_assignment(self, assignment_id: int) -> None:
        """Delete an assignment entered by mistake (to stop a real one, use end_assignment)."""
        self._change_assignment("DELETE FROM hr_oltp.project_assignments WHERE assignment_id = %s",
                                (assignment_id,), assignment_id)

    def team(self, project_id: int) -> pd.DataFrame:
        """Everyone assigned to a project, current assignments first."""
        return self.fetch_frame(
            "SELECT a.assignment_id, e.employee_code, CONCAT(e.first_name, ' ', e.last_name) AS full_name, "
            "       a.project_role, a.allocation_pct, a.start_date, a.end_date, a.end_date IS NULL AS is_open "
            "FROM hr_oltp.project_assignments a JOIN hr_oltp.employees e ON e.employee_id = a.employee_id "
            "WHERE a.project_id = %s ORDER BY is_open DESC, a.start_date DESC", (project_id,))

    def assignments_for(self, employee_id: int) -> pd.DataFrame:
        """An employee's project assignments, newest first."""
        return self.fetch_frame(
            "SELECT a.assignment_id, p.project_code, p.project_name, p.status AS project_status, a.project_role, "
            "       a.allocation_pct, a.start_date, a.end_date "
            "FROM hr_oltp.project_assignments a JOIN hr_oltp.projects p ON p.project_id = a.project_id "
            "WHERE a.employee_id = %s ORDER BY a.start_date DESC", (employee_id,))

    def allocation_on(self, employee_id: int, day: date) -> int:
        """The employee's total allocation (%) across the assignments active on a day."""
        with self.transaction() as cur:
            return self._allocation_on(cur, employee_id, day)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _allocation_on(cur: Any, employee_id: int, day: date) -> int:
        cur.execute("SELECT COALESCE(SUM(allocation_pct), 0) FROM hr_oltp.project_assignments "
                    "WHERE employee_id = %s AND start_date <= %s AND (end_date IS NULL OR end_date >= %s)",
                    (employee_id, day, day))
        return int(cur.fetchone()[0])

    @staticmethod
    def _require_active_employee(cur: Any, employee_id: int | None, who: str, on: date | None = None) -> None:
        """Employed (hired, not left) — today, or on a given day."""
        if employee_id is None:
            return
        cur.execute("SELECT hire_date, exit_date FROM hr_oltp.employees WHERE employee_id = %s", (employee_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"Employee {employee_id} not found")
        hire_date, exit_date = row
        if exit_date is not None and (on is None or on > exit_date):
            raise BusinessRuleError(f"{who} left the company on {exit_date}")
        if on is not None and on < hire_date:
            raise BusinessRuleError(f"{who} only joined on {hire_date}")

    def _change_assignment(self, sql: str, params: tuple, assignment_id: int) -> None:
        with self.transaction() as cur:
            cur.execute(sql, params)
            if cur.rowcount == 0:
                raise NotFoundError(f"Assignment {assignment_id} not found")

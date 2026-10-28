"""ReviewManager — submit, read and withdraw performance reviews.

Reviews reach the warehouse on the next ETL run (decision B5). A review that is already in the
warehouse is final; one that is not yet loaded can be withdrawn and submitted again.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from src.db_manager import DatabaseConnection
from src.exceptions import BusinessRuleError, NotFoundError, ValidationError
from src.models import Review

logger = logging.getLogger(__name__)

_REVIEW_SQL = ("SELECT review_id, employee_id, reviewer_id, review_year, review_date, rating_id, kpi_score, "
               "training_sessions, overtime_flag, project_id, comments FROM hr_oltp.performance_reviews ")


class ReviewManager(DatabaseConnection):
    """Data-access manager for performance reviews."""

    def submit(self, review: Review) -> Review:
        """Record a review. Checked against other records first: the review date is not in the
        future; the employee was employed on it and joined by the end of the review year; the
        reviewer was employed on it. One review per employee and year (a unique key)."""
        if review.review_id is not None:
            raise ValidationError("A new review has no review id yet")
        with self.transaction() as cur:
            cur.execute("SELECT CURDATE()")
            if review.review_date > cur.fetchone()[0]:
                raise BusinessRuleError("The review date cannot be in the future")
            for person, who in ((review.employee_id, "The employee"), (review.reviewer_id, "The reviewer")):
                cur.execute("SELECT hire_date, exit_date FROM hr_oltp.employees WHERE employee_id = %s", (person,))
                row = cur.fetchone()
                if row is None:
                    raise NotFoundError(f"{who} ({person}) does not exist")
                hire_date, exit_date = row
                if hire_date > review.review_date or (exit_date is not None and exit_date < review.review_date):
                    raise BusinessRuleError(f"{who} was not employed on {review.review_date}")
                if person == review.employee_id and hire_date > date(review.review_year, 12, 31):
                    raise BusinessRuleError(f"The employee joined after {review.review_year}")
            cur.execute(
                "INSERT INTO hr_oltp.performance_reviews (employee_id, review_year, review_date, reviewer_id, "
                "project_id, rating_id, kpi_score, training_sessions, overtime_flag, comments) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (review.employee_id, review.review_year, review.review_date, review.reviewer_id, review.project_id,
                 review.rating_id, review.kpi_score, review.training_sessions, review.overtime_flag,
                 review.comments))
            review_id = cur.lastrowid
        logger.info("Review %s submitted for employee %s, %s", review_id, review.employee_id, review.review_year)
        return review.with_changes(review_id=review_id)

    def get(self, review_id: int) -> Review:
        row = self.fetch_one(_REVIEW_SQL + "WHERE review_id = %s", (review_id,))
        if row is None:
            raise NotFoundError(f"Review {review_id} not found")
        return Review.from_row(row)

    def reviews_for(self, employee_id: int) -> pd.DataFrame:
        """An employee's reviews with reviewer, project and rating label, newest first."""
        return self.fetch_frame(
            "SELECT r.review_id, r.review_year, r.review_date, rl.rating_label, r.rating_id, r.kpi_score, "
            "       r.training_sessions, r.overtime_flag, CONCAT(v.first_name, ' ', v.last_name) AS reviewer, "
            "       p.project_name, r.comments "
            "FROM hr_oltp.performance_reviews r "
            "JOIN hr_oltp.employees v ON v.employee_id = r.reviewer_id "
            "JOIN hr_oltp.performance_rating_levels rl ON rl.rating_id = r.rating_id "
            "LEFT JOIN hr_oltp.projects p ON p.project_id = r.project_id "
            "WHERE r.employee_id = %s ORDER BY r.review_year DESC", (employee_id,))

    def suggest(self, employee_id: int, review_year: int) -> dict:
        """Defaults for the review form: the reviewer is the employee's manager at the end of the
        review year (or today, if earlier); the project is their main assignment that year
        (highest allocation) — the same rule the synthesizer used."""
        with self.transaction(dictionary=True) as cur:
            cur.execute("SELECT LEAST(CURDATE(), %s) AS day", (date(review_year, 12, 31),))
            day = cur.fetchone()["day"]
            cur.execute(
                "SELECT h.manager_id AS reviewer_id, CONCAT(m.first_name, ' ', m.last_name) AS reviewer_name "
                "FROM hr_oltp.employee_job_history h LEFT JOIN hr_oltp.employees m ON m.employee_id = h.manager_id "
                "WHERE h.employee_id = %s AND h.effective_start_date <= %s "
                "  AND (h.effective_end_date IS NULL OR h.effective_end_date > %s)", (employee_id, day, day))
            reviewer = cur.fetchone() or {"reviewer_id": None, "reviewer_name": None}
            cur.execute(
                "SELECT a.project_id, p.project_name FROM hr_oltp.project_assignments a "
                "JOIN hr_oltp.projects p ON p.project_id = a.project_id "
                "WHERE a.employee_id = %s AND a.start_date <= %s AND (a.end_date IS NULL OR a.end_date >= %s) "
                "ORDER BY a.allocation_pct DESC, a.start_date DESC LIMIT 1",
                (employee_id, date(review_year, 12, 31), date(review_year, 1, 1)))
            project = cur.fetchone() or {"project_id": None, "project_name": None}
        return {**reviewer, **project}

    def withdraw(self, review_id: int) -> None:
        """Delete a review submitted by mistake — only while it is not yet in the warehouse."""
        with self.transaction() as cur:
            cur.execute("SELECT 1 FROM hr_dw.fact_performance_reviews WHERE review_id = %s", (review_id,))
            if cur.fetchone():
                raise BusinessRuleError("This review is already in the warehouse and reported — it can no longer "
                                        "be withdrawn")
            cur.execute("DELETE FROM hr_oltp.performance_reviews WHERE review_id = %s", (review_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Review {review_id} not found")
        logger.info("Review %s withdrawn", review_id)

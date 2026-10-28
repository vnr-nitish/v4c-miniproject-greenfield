"""Review — one annual performance review of an employee."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.models.base import Entity, Rules, as_date, as_decimal, in_range, is_date, is_int

# IBM's official PerformanceRating scale (sql/02_oltp_reference_data.sql; a test checks they match)
RATING_LABELS = {1: "Low", 2: "Good", 3: "Excellent", 4: "Outstanding"}


@dataclass(frozen=True)
class Review(Entity):
    """The review of one employee for one performance year, usually held early the next year."""

    employee_id: int
    reviewer_id: int
    review_year: int
    review_date: date
    rating_id: int
    kpi_score: Decimal
    training_sessions: int = 0
    overtime_flag: bool = False
    project_id: int | None = None
    comments: str | None = None
    review_id: int | None = None

    def _normalize(self) -> None:
        self._set("review_date", as_date(self.review_date))
        self._set("kpi_score", as_decimal(self.kpi_score))
        if self.overtime_flag in (0, 1):                         # TINYINT from the database
            self._set("overtime_flag", bool(self.overtime_flag))
        if isinstance(self.comments, str):
            self._set("comments", self.comments.strip() or None)

    def validation_errors(self) -> dict[str, str]:
        r = Rules()
        r.require(is_int(self.employee_id) and self.employee_id >= 1, "employee_id", "Choose an employee")
        r.require(is_int(self.reviewer_id) and self.reviewer_id >= 1, "reviewer_id", "Choose a reviewer")
        r.require(self.reviewer_id != self.employee_id, "reviewer_id", "An employee cannot review themselves")
        r.require(is_int(self.review_year) and 2000 <= self.review_year <= 2100, "review_year",
                  "Review year must be 2000-2100")
        r.require(is_date(self.review_date), "review_date", "Enter the review date")
        if is_date(self.review_date) and is_int(self.review_year):
            r.require(self.review_year <= self.review_date.year <= self.review_year + 1, "review_date",
                      "The review must be held in the review year or the year after")
        r.require(self.rating_id in RATING_LABELS and is_int(self.rating_id), "rating_id", "Rating must be 1-4")
        r.require(in_range(self.kpi_score, 0, 100), "kpi_score", "KPI score must be 0-100")
        r.require(is_int(self.training_sessions) and 0 <= self.training_sessions <= 20, "training_sessions",
                  "Training sessions must be 0-20")
        r.require(isinstance(self.overtime_flag, bool), "overtime_flag", "Overtime must be yes or no")
        r.require(self.project_id is None or (is_int(self.project_id) and self.project_id >= 1), "project_id",
                  "Choose a valid project")
        r.require(self.comments is None or (isinstance(self.comments, str) and len(self.comments) <= 500),
                  "comments", "Comments can be at most 500 characters")
        return r.errors

    # ------------------------------------------------------------------ behaviour

    @property
    def rating_label(self) -> str:
        return RATING_LABELS[self.rating_id]

    @property
    def is_outstanding(self) -> bool:
        return self.rating_id == max(RATING_LABELS)

    @property
    def is_held_in_following_year(self) -> bool:
        """Reviews are normally held early in the year after the performance year."""
        return self.review_date.year == self.review_year + 1

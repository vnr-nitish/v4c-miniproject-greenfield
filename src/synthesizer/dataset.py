"""SyntheticDataset — holds every generated table and converts it to the staging layout."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# column order of each staging table (sql/06_staging_ddl.sql), excluding stg_row_id/batch_id/loaded_at
STAGING_COLUMNS = {
    "stg_employees": [
        "source_employee_number", "employee_code", "first_name", "last_name", "email", "phone",
        "date_of_birth", "hire_date", "exit_date", "exit_reason", "age", "attrition", "business_travel",
        "daily_rate", "department", "distance_from_home", "education", "education_field", "employee_count",
        "environment_satisfaction", "gender", "hourly_rate", "job_involvement", "job_level", "job_role",
        "job_satisfaction", "marital_status", "monthly_income", "monthly_rate", "num_companies_worked",
        "over18", "over_time", "percent_salary_hike", "performance_rating", "relationship_satisfaction",
        "standard_hours", "stock_option_level", "total_working_years", "training_times_last_year",
        "work_life_balance", "years_at_company", "years_in_current_role", "years_since_last_promotion",
        "years_with_curr_manager"],
    "stg_job_history": [
        "employee_code", "version_number", "department", "job_role", "job_level", "manager_code",
        "monthly_income", "stock_option_level", "business_travel", "change_reason", "effective_start_date",
        "effective_end_date"],
    "stg_surveys": [
        "employee_code", "survey_date", "environment_satisfaction", "job_satisfaction",
        "relationship_satisfaction", "job_involvement", "work_life_balance"],
    "stg_reviews": [
        "employee_code", "review_year", "review_date", "reviewer_code", "project_code", "performance_rating",
        "kpi_score", "training_sessions", "over_time", "comments"],
    "stg_projects": [
        "project_code", "project_name", "description", "department", "project_manager_code", "start_date",
        "planned_end_date", "actual_end_date", "budget", "status", "priority"],
    "stg_assignments": [
        "employee_code", "project_code", "project_role", "allocation_pct", "start_date", "end_date"],
}


@dataclass
class SyntheticDataset:
    """Generator outputs (with internal helper columns) plus the derived staging frames."""

    employees: pd.DataFrame
    job_history: pd.DataFrame
    surveys: pd.DataFrame
    reviews: pd.DataFrame
    projects: pd.DataFrame
    assignments: pd.DataFrame
    anomaly_manifest: dict = field(default_factory=dict)
    staging: dict[str, pd.DataFrame] = field(default_factory=dict)

    def build_staging(self) -> dict[str, pd.DataFrame]:
        """Rename / select columns into the exact staging layout (before anomalies are planted)."""
        reviews = self.reviews.rename(columns={"year": "review_year", "rating": "performance_rating"})
        reviews["over_time"] = reviews.overtime.map({True: "Yes", False: "No"})
        frames = {
            "stg_employees": self.employees,
            "stg_job_history": self.job_history,
            "stg_surveys": self.surveys,
            "stg_reviews": reviews,
            "stg_projects": self.projects,
            "stg_assignments": self.assignments,
        }
        self.staging = {name: frames[name][cols].copy() for name, cols in STAGING_COLUMNS.items()}
        return self.staging

    def row_counts(self) -> dict[str, int]:
        return {name: len(df) for name, df in self.staging.items()}

    def to_csv(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, df in self.staging.items():
            df.to_csv(directory / f"{name}.csv", index=False)
        (directory / "anomaly_manifest.json").write_text(json.dumps(self.anomaly_manifest, indent=2))

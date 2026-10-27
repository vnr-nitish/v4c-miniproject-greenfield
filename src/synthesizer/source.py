"""Loads and validates the IBM HR Analytics source file."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.exceptions import ValidationError

EXPECTED_COLUMNS = [
    "Age", "Attrition", "BusinessTravel", "DailyRate", "Department", "DistanceFromHome", "Education",
    "EducationField", "EmployeeCount", "EmployeeNumber", "EnvironmentSatisfaction", "Gender", "HourlyRate",
    "JobInvolvement", "JobLevel", "JobRole", "JobSatisfaction", "MaritalStatus", "MonthlyIncome",
    "MonthlyRate", "NumCompaniesWorked", "Over18", "OverTime", "PercentSalaryHike", "PerformanceRating",
    "RelationshipSatisfaction", "StandardHours", "StockOptionLevel", "TotalWorkingYears",
    "TrainingTimesLastYear", "WorkLifeBalance", "YearsAtCompany", "YearsInCurrentRole",
    "YearsSinceLastPromotion", "YearsWithCurrManager",
]


class IBMSourceData:
    """The 1,470-row IBM snapshot, validated before anything is generated from it."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._frame: pd.DataFrame | None = None

    @property
    def frame(self) -> pd.DataFrame:
        if self._frame is None:
            self._frame = self._load()
        return self._frame

    def _load(self) -> pd.DataFrame:
        if not self._path.exists():
            raise ValidationError(f"Source file not found: {self._path}")
        df = pd.read_csv(self._path)
        self._validate(df)
        return df

    @staticmethod
    def _validate(df: pd.DataFrame) -> None:
        missing = set(EXPECTED_COLUMNS) - set(df.columns)
        if missing:
            raise ValidationError(f"Source file is missing columns: {sorted(missing)}")
        if df[EXPECTED_COLUMNS].isna().any().any():
            raise ValidationError("Source file contains null values")
        if not df["EmployeeNumber"].is_unique:
            raise ValidationError("EmployeeNumber is not unique in the source file")
        rules = {
            "YearsInCurrentRole <= YearsAtCompany": df.YearsInCurrentRole <= df.YearsAtCompany,
            "YearsSinceLastPromotion <= YearsAtCompany": df.YearsSinceLastPromotion <= df.YearsAtCompany,
            "YearsWithCurrManager <= YearsAtCompany": df.YearsWithCurrManager <= df.YearsAtCompany,
            "YearsAtCompany <= TotalWorkingYears": df.YearsAtCompany <= df.TotalWorkingYears,
            "Age - TotalWorkingYears >= 18": df.Age - df.TotalWorkingYears >= 18,
        }
        broken = [name for name, ok in rules.items() if not ok.all()]
        if broken:
            raise ValidationError(f"Source file breaks tenure rules: {broken}")

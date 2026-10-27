"""EmployeeGenerator — scales the IBM snapshot to N synthetic employees (decision D16)."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from src.synthesizer.base import BaseGenerator
from src.synthesizer.reference import EXIT_REASONS, JOB_LEVELS

IBM_TO_STAGING = {
    "Age": "age", "Attrition": "attrition", "BusinessTravel": "business_travel", "DailyRate": "daily_rate",
    "Department": "department", "DistanceFromHome": "distance_from_home", "Education": "education",
    "EducationField": "education_field", "EmployeeCount": "employee_count",
    "EmployeeNumber": "source_employee_number", "EnvironmentSatisfaction": "environment_satisfaction",
    "Gender": "gender", "HourlyRate": "hourly_rate", "JobInvolvement": "job_involvement",
    "JobLevel": "job_level", "JobRole": "job_role", "JobSatisfaction": "job_satisfaction",
    "MaritalStatus": "marital_status", "MonthlyIncome": "monthly_income", "MonthlyRate": "monthly_rate",
    "NumCompaniesWorked": "num_companies_worked", "Over18": "over18", "OverTime": "over_time",
    "PercentSalaryHike": "percent_salary_hike", "PerformanceRating": "performance_rating",
    "RelationshipSatisfaction": "relationship_satisfaction", "StandardHours": "standard_hours",
    "StockOptionLevel": "stock_option_level", "TotalWorkingYears": "total_working_years",
    "TrainingTimesLastYear": "training_times_last_year", "WorkLifeBalance": "work_life_balance",
    "YearsAtCompany": "years_at_company", "YearsInCurrentRole": "years_in_current_role",
    "YearsSinceLastPromotion": "years_since_last_promotion", "YearsWithCurrManager": "years_with_curr_manager",
}

STRATA = ["Attrition", "Department", "JobRole", "JobLevel"]
EXIT_REASON_NAMES = list(EXIT_REASONS)
BASE_EXIT_WEIGHTS = np.array([0.30, 0.14, 0.12, 0.10, 0.08, 0.02, 0.10, 0.09, 0.05])


class EmployeeGenerator(BaseGenerator):
    """Stratified bootstrap of the IBM rows + bounded jitter + Faker identity + dates."""

    def generate(self, source: pd.DataFrame) -> pd.DataFrame:
        df = self._stratified_sample(source)
        df = df.rename(columns=IBM_TO_STAGING)
        self._jitter(df)
        self._dates(df)
        self._exit_reasons(df)
        self._identity(df)
        return df.sort_values("employee_code", ignore_index=True)

    # ------------------------------------------------------------------ steps

    def _stratified_sample(self, source: pd.DataFrame) -> pd.DataFrame:
        """Each (attrition, department, role, level) stratum gets exactly its proportional share."""
        n = self.config.n_employees
        groups = list(source.groupby(STRATA, sort=True).indices.values())
        sizes = np.array([len(g) for g in groups], dtype=float)
        exact = sizes / sizes.sum() * n
        counts = np.floor(exact).astype(int)
        leftover = n - counts.sum()
        counts[np.argsort(-(exact - counts), kind="stable")[:leftover]] += 1   # largest remainder
        picks = np.concatenate([self._rng.choice(g, size=c, replace=True) for g, c in zip(groups, counts) if c])
        self._rng.shuffle(picks)
        return source.iloc[picks].reset_index(drop=True)

    def _jitter(self, df: pd.DataFrame) -> None:
        n = len(df)
        cfg = self.config
        # income: +/- 7 %, kept inside the level's salary band (finding F3)
        low = df.job_level.map(lambda lvl: JOB_LEVELS[lvl][1]).to_numpy()
        high = df.job_level.map(lambda lvl: JOB_LEVELS[lvl][2]).to_numpy()
        factor = self._rng.uniform(1 - cfg.income_jitter, 1 + cfg.income_jitter, n)
        df["monthly_income"] = np.clip(np.round(df.monthly_income * factor), low, high).astype(float)
        # age: +/- 1 year, never below TotalWorkingYears + 18 (IBM rule) and within 18-60
        age = df.age + self._rng.integers(-1, 2, n)
        df["age"] = np.clip(age, np.maximum(df.total_working_years + 18, 18), 60).astype(int)
        # distance: +/- 2 km within the observed 1-29
        df["distance_from_home"] = np.clip(df.distance_from_home + self._rng.integers(-2, 3, n), 1, 29)
        # the three noise columns (finding F5): +/- 5 %
        for col in ("daily_rate", "hourly_rate", "monthly_rate"):
            df[col] = np.round(df[col] * self._rng.uniform(1 - cfg.rate_jitter, 1 + cfg.rate_jitter, n)).astype(int)

    def _dates(self, df: pd.DataFrame) -> None:
        """Turn IBM tenure values into dates that reproduce them exactly (decision D17)."""
        n = len(df)
        cfg = self.config
        snapshot = np.datetime64(cfg.snapshot_date, "D")
        leaver = (df.attrition == "Yes").to_numpy()
        exit_dates = self.random_dates(np.full(n, np.datetime64(cfg.exit_window_start, "D")), np.full(n, snapshot))
        ref = np.where(leaver, exit_dates, snapshot)

        birth_offset = self._rng.integers(0, 360, n)
        hire_offset = self._rng.integers(0, 360, n)
        # a person hired at exactly 18 must not be hired before their 18th birthday
        at_18 = (df.age - df.years_at_company).to_numpy() <= 18
        hire_offset = np.where(at_18, np.minimum(hire_offset, birth_offset), hire_offset)

        df["ref_date"] = ref
        dob = self.years_before(ref, df.age, birth_offset)
        hire = self.years_before(ref, df.years_at_company, hire_offset)
        # Calendar arithmetic across leap years can leave someone hired at exactly 18 a day short.
        # For those rows: earliest birthday and latest hire date that still reproduce Age and
        # YearsAtCompany exactly — this always leaves a full 18 years in between.
        short = self.full_years(dob, hire) < 18
        if short.any():
            dob[short] = self.years_before(ref[short], df.age.to_numpy()[short], np.full(short.sum(), 359))
            hire[short] = self.years_before(ref[short], df.years_at_company.to_numpy()[short], np.zeros(short.sum()))
        df["date_of_birth"] = dob
        df["hire_date"] = hire
        df["exit_date"] = np.where(leaver, ref, np.datetime64("NaT"))

    def _exit_reasons(self, df: pd.DataFrame) -> None:
        """Leavers get a reason whose probability depends on their profile."""
        n = len(df)
        w = np.tile(BASE_EXIT_WEIGHTS, (n, 1))
        idx = {name: i for i, name in enumerate(EXIT_REASON_NAMES)}
        stressed = ((df.over_time == "Yes") | (df.work_life_balance <= 2)).to_numpy()
        w[stressed, idx["Work-Life Balance"]] *= 2.5
        w[(df.job_level == 1).to_numpy(), idx["Compensation"]] *= 1.8
        w[(df.performance_rating >= 4).to_numpy(), idx["Performance Termination"]] = 0
        w[(df.age < 55).to_numpy(), idx["Retirement"]] = 0
        w[(df.age >= 55).to_numpy(), idx["Retirement"]] = 3.0
        w /= w.sum(axis=1, keepdims=True)
        choice = (w.cumsum(axis=1) < self._rng.random((n, 1))).sum(axis=1)
        reasons = np.array(EXIT_REASON_NAMES, dtype=object)[np.minimum(choice, len(EXIT_REASON_NAMES) - 1)]
        df["exit_reason"] = np.where(df.attrition == "Yes", reasons, None)

    def _identity(self, df: pd.DataFrame) -> None:
        """Faker name pools sampled with the seeded generator: fast and reproducible (D20)."""
        n = len(df)
        fk = self._faker
        male = np.array([fk.first_name_male() for _ in range(3000)], dtype=object)
        female = np.array([fk.first_name_female() for _ in range(3000)], dtype=object)
        last = np.array([fk.last_name() for _ in range(3000)], dtype=object)
        phones = np.array([fk.phone_number() for _ in range(5000)], dtype=object)

        is_male = (df.gender == "Male").to_numpy()
        df["first_name"] = np.where(is_male, male[self._rng.integers(0, len(male), n)],
                                    female[self._rng.integers(0, len(female), n)])
        df["last_name"] = last[self._rng.integers(0, len(last), n)]
        df["phone"] = phones[self._rng.integers(0, len(phones), n)]

        # employee codes follow hiring order: EMP000001 is the longest-serving employee
        order = np.lexsort((self._rng.random(n), df.hire_date.to_numpy()))
        number = np.empty(n, dtype=int)
        number[order] = np.arange(1, n + 1)
        df["employee_code"] = [f"EMP{k:06d}" for k in number]
        clean = lambda s: re.sub(r"[^a-z]", "", s.lower())                       # noqa: E731
        df["email"] = [f"{clean(f)}.{clean(l)}.{k}@{self.config.email_domain}"
                       for f, l, k in zip(df.first_name, df.last_name, number)]

"""SurveyGenerator and ReviewGenerator — yearly engagement surveys and performance reviews.

The LATEST survey / review of each employee carries the IBM values exactly. Earlier years are
generated backwards with persistence, so personal trends look natural (synthesizer_design.md §6).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.synthesizer.base import BaseGenerator

SURVEY_COLUMNS = ["environment_satisfaction", "job_satisfaction", "relationship_satisfaction",
                  "job_involvement", "work_life_balance"]
EARLIER_RATING_P = {1: 0.02, 2: 0.08, 3: 0.72, 4: 0.18}
COMMENTS = {
    1: ["Performance below expectations; improvement plan agreed.", "Several objectives missed this year."],
    2: ["Met some objectives; development needed in key areas.", "Solid effort, inconsistent delivery."],
    3: ["Consistently met expectations.", "Reliable contributor to team goals.", "Delivered all key objectives."],
    4: ["Outstanding contribution; exceeded all objectives.", "Role model for the team.",
        "Exceptional results and leadership."],
}


class _YearlyGenerator(BaseGenerator):
    """Shared logic: employee x cycle-year eligibility and backward random walks."""

    def _employee_years(self, emp: pd.DataFrame) -> pd.DataFrame:
        years = np.array(self.config.cycle_years)
        grid = pd.DataFrame({
            "emp_idx": np.repeat(np.arange(len(emp)), len(years)),
            "year": np.tile(years, len(emp)),
        })
        grid["hire_date"] = emp.hire_date.to_numpy().astype("datetime64[D]")[grid.emp_idx]
        grid["exit_date"] = emp.exit_date.to_numpy().astype("datetime64[D]")[grid.emp_idx]
        grid["leaver"] = (emp.attrition == "Yes").to_numpy()[grid.emp_idx]
        return grid

    def _walk_back(self, frame: pd.DataFrame, latest: np.ndarray, low: int, high: int,
                   p_active: tuple, p_leaver: tuple) -> np.ndarray:
        """Value per row: the latest row keeps `latest`; going back in time each year moves -1/0/+1.
        For leavers +1 is more likely, i.e. scores were higher before they declined towards exit."""
        n = len(frame)
        leaver = frame.leaver.to_numpy()
        steps_a = self._rng.choice([-1, 0, 1], size=n, p=p_active)
        steps_l = self._rng.choice([-1, 0, 1], size=n, p=p_leaver)
        steps = np.where(leaver, steps_l, steps_a)
        steps[frame["rank_back"].to_numpy() == 0] = 0
        cum = pd.Series(steps).groupby(frame.emp_idx.to_numpy()).cumsum().to_numpy()
        return np.clip(latest + cum, low, high)

    @staticmethod
    def _rank_back(frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.sort_values(["emp_idx", "year"], ascending=[True, False], ignore_index=True)
        frame["rank_back"] = frame.groupby("emp_idx").cumcount()        # 0 = latest year
        return frame


class SurveyGenerator(_YearlyGenerator):

    def generate(self, emp: pd.DataFrame) -> pd.DataFrame:
        snapshot = np.datetime64(self.config.snapshot_date, "D")
        grid = self._employee_years(emp)
        starts = (grid.year.astype(str) + "-09-01").to_numpy().astype("datetime64[D]")
        ends = (grid.year.astype(str) + "-10-31").to_numpy().astype("datetime64[D]")
        grid["survey_date"] = self.random_dates(starts, ends)
        employed = (grid.hire_date <= grid.survey_date) & (
            grid.exit_date.isna() | (grid.exit_date > grid.survey_date)) & (grid.survey_date <= snapshot)
        s = self._rank_back(grid[employed.to_numpy()].copy())
        for col in SURVEY_COLUMNS:
            latest = emp[col].to_numpy()[s.emp_idx]
            s[col] = self._walk_back(s, latest, 1, 4, (0.15, 0.70, 0.15), (0.10, 0.65, 0.25)).astype(int)
        s["employee_code"] = emp.employee_code.to_numpy()[s.emp_idx]
        return s.sort_values(["emp_idx", "year"], ignore_index=True)


class ReviewGenerator(_YearlyGenerator):

    def generate(self, emp: pd.DataFrame, history: pd.DataFrame, department_heads: dict) -> pd.DataFrame:
        snapshot = np.datetime64(self.config.snapshot_date, "D")
        grid = self._employee_years(emp)
        next_year = (grid.year + 1).astype(str)
        grid["review_date"] = self.random_dates((next_year + "-01-15").to_numpy().astype("datetime64[D]"),
                                                (next_year + "-03-15").to_numpy().astype("datetime64[D]"))
        cutoff = (grid.year.astype(str) + "-09-30").to_numpy().astype("datetime64[D]")
        eligible = (grid.hire_date <= cutoff) & (
            grid.exit_date.isna() | (grid.exit_date > grid.review_date)) & (grid.review_date <= snapshot)
        r = self._rank_back(grid[eligible.to_numpy()].copy())
        n = len(r)
        latest = r.rank_back.to_numpy() == 0

        # rating: latest = IBM; earlier years keep it 60 % of the time, else the earlier-years mix
        ibm_rating = emp.performance_rating.to_numpy()[r.emp_idx]
        drawn = self._rng.choice(list(EARLIER_RATING_P), size=n, p=list(EARLIER_RATING_P.values()))
        keep = self._rng.random(n) < 0.60
        r["rating"] = np.where(latest | keep, ibm_rating, drawn)

        # KPI score: correlated with the rating, not determined by it
        mean = np.select([r.rating == 4, r.rating == 3, r.rating == 2], [92.0, 78.0, 60.0], 42.0)
        sd = np.where(r.rating == 4, 4.0, 7.0)
        lo = np.select([r.rating == 4, r.rating == 3, r.rating == 2], [85, 60, 40], 20)
        hi = np.select([r.rating == 4, r.rating == 3, r.rating == 2], [100, 95, 75], 60)
        r["kpi_score"] = np.round(np.clip(self._rng.normal(mean, sd), lo, hi), 2)

        ibm_training = emp.training_times_last_year.to_numpy()[r.emp_idx]
        r["training_sessions"] = np.where(latest, ibm_training,
                                          np.clip(ibm_training + self._rng.integers(-1, 2, n), 0, 6))
        ibm_ot = (emp.over_time == "Yes").to_numpy()[r.emp_idx]
        flip = self._rng.random(n) < 0.20
        r["overtime"] = np.where(latest, ibm_ot, np.where(flip, ~ibm_ot, ibm_ot))

        pick = self._rng.integers(0, 10, n)
        has_comment = self._rng.random(n) < 0.35
        r["comments"] = [COMMENTS[int(rt)][p % len(COMMENTS[int(rt)])] if c else None
                         for rt, p, c in zip(r.rating, pick, has_comment)]

        r["employee_code"] = emp.employee_code.to_numpy()[r.emp_idx]
        r["reviewer_code"] = self._reviewers(emp, history, r, department_heads)
        return r.sort_values(["emp_idx", "year"], ignore_index=True)

    @staticmethod
    def _reviewers(emp: pd.DataFrame, history: pd.DataFrame, r: pd.DataFrame, heads: dict) -> np.ndarray:
        """The reviewer is the employee's manager on the review date (from the job history).
        Department heads, who have no manager, are reviewed by another department's head."""
        h = history[["emp_idx", "effective_start_date", "department", "manager_code"]].copy()
        h["effective_start_date"] = h.effective_start_date.astype("datetime64[s]")
        q = r[["emp_idx", "review_date"]].copy()
        q["review_date"] = q.review_date.astype("datetime64[s]")
        q["_order"] = np.arange(len(q))
        at = pd.merge_asof(q.sort_values("review_date"), h.sort_values("effective_start_date"),
                           left_on="review_date", right_on="effective_start_date", by="emp_idx",
                           direction="backward").sort_values("_order")
        codes = emp.employee_code.to_numpy()
        reviewer = at.manager_code.to_numpy(dtype=object)
        head_codes = list(heads.values())
        dept_head = at.department.map(heads).to_numpy(dtype=object)
        own = codes[at.emp_idx.to_numpy()]
        missing = pd.isna(reviewer)
        fallback = np.where(dept_head != own, dept_head,
                            [next((c for c in head_codes if c != o), None) for o in own])
        return np.where(missing, fallback, reviewer)

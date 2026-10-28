"""SurveyGenerator and ReviewGenerator — yearly engagement surveys and performance reviews.

The LATEST survey / review of each employee carries the IBM values exactly. Earlier years are
generated backwards so that every year has the same distribution as the IBM snapshot of the
employees who stayed, while each person's values persist from year to year
(synthesizer_design.md §6, D26). KPI scores follow a truncated normal distribution per rating (D27).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.synthesizer.base import BaseGenerator

SURVEY_COLUMNS = ["environment_satisfaction", "job_satisfaction", "relationship_satisfaction",
                  "job_involvement", "work_life_balance"]
# rating -> (mean, standard deviation, lowest, highest) of the KPI score
KPI_BANDS = {4: (92.0, 4.0, 85.0, 100.0), 3: (78.0, 7.0, 60.0, 95.0), 2: (60.0, 7.0, 40.0, 75.0),
             1: (42.0, 7.0, 20.0, 60.0)}
COMMENTS = {
    1: ["Performance below expectations; improvement plan agreed.", "Several objectives missed this year."],
    2: ["Met some objectives; development needed in key areas.", "Solid effort, inconsistent delivery."],
    3: ["Consistently met expectations.", "Reliable contributor to team goals.", "Delivered all key objectives."],
    4: ["Outstanding contribution; exceeded all objectives.", "Role model for the team.",
        "Exceptional results and leadership."],
}


class _YearlyGenerator(BaseGenerator):
    """Shared logic: employee x cycle-year eligibility and backward walks through the years."""

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

    @staticmethod
    def _stayers_distribution(emp: pd.DataFrame, column: str) -> pd.Series:
        """The IBM distribution of a column among the employees who did not leave."""
        return emp.loc[emp.attrition == "No", column].value_counts(normalize=True).sort_index()

    def _walk_back(self, frame: pd.DataFrame, latest: np.ndarray, target: pd.Series, change: float) -> np.ndarray:
        """Value per row: the latest row keeps `latest`; going back one year at a time, the value
        moves to a neighbouring value with probability `change`, accepted with the
        Metropolis-Hastings probability. The walk's stationary distribution is `target` (the
        stayers' IBM distribution), so for people who stayed every earlier year has that
        distribution — no year differs from the snapshot — while a leaver's values drift back
        towards it: their decline happens close to the exit.

        `frame` is ordered by employee and then backwards in time (see _rank_back), so the row
        before a row with rank_back = k is the same employee's year k - 1.
        """
        states = np.union1d(target.index.to_numpy(), np.unique(latest))
        pi = target.reindex(states, fill_value=0.0).to_numpy()
        last = len(states) - 1
        neighbours = np.where((np.arange(len(states)) == 0) | (np.arange(len(states)) == last), 1, 2)
        if last == 0:
            return np.full(len(frame), states[0])
        values = np.searchsorted(states, latest)
        rank_back = frame["rank_back"].to_numpy()
        for k in range(1, int(rank_back.max(initial=0)) + 1):
            rows = np.flatnonzero(rank_back == k)
            i = values[rows - 1]
            up = self._rng.random(len(rows)) < 0.5
            j = np.where(i == 0, 1, np.where(i == last, last - 1, np.where(up, i + 1, i - 1)))
            # Metropolis-Hastings: accept with (pi_j / q(i->j)) / (pi_i / q(j->i)), q = 1 / neighbours
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(pi[i] > 0, (pi[j] * neighbours[i]) / (pi[i] * neighbours[j]), 1.0)
            moves = (self._rng.random(len(rows)) < change) & (self._rng.random(len(rows)) < ratio)
            values[rows] = np.where(moves, j, i)
        return states[values]

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
            s[col] = self._walk_back(s, latest, self._stayers_distribution(emp, col),
                                     self.config.survey_change).astype(int)
        s["employee_code"] = emp.employee_code.to_numpy()[s.emp_idx]
        return s.sort_values(["emp_idx", "year"], ignore_index=True)


class ReviewGenerator(_YearlyGenerator):

    def generate(self, emp: pd.DataFrame, history: pd.DataFrame, department_heads: dict) -> pd.DataFrame:
        cfg = self.config
        snapshot = np.datetime64(cfg.snapshot_date, "D")
        grid = self._employee_years(emp)
        next_year = (grid.year + 1).astype(str)
        grid["review_date"] = self.random_dates((next_year + "-01-15").to_numpy().astype("datetime64[D]"),
                                                (next_year + "-03-15").to_numpy().astype("datetime64[D]"))
        cutoff = (grid.year.astype(str) + "-09-30").to_numpy().astype("datetime64[D]")
        eligible = (grid.hire_date <= cutoff) & (
            grid.exit_date.isna() | (grid.exit_date > grid.review_date)) & (grid.review_date <= snapshot)
        r = self._rank_back(grid[eligible.to_numpy()].copy())
        n = len(r)

        # rating, overtime, training: latest = IBM; earlier years walk back towards the stayers' mix
        r["rating"] = self._walk_back(r, emp.performance_rating.to_numpy()[r.emp_idx],
                                      self._stayers_distribution(emp, "performance_rating"),
                                      cfg.rating_change).astype(int)
        r["overtime"] = self._walk_back(r, (emp.over_time == "Yes").to_numpy()[r.emp_idx],
                                        self._stayers_distribution(emp.assign(ot=emp.over_time == "Yes"), "ot"),
                                        cfg.overtime_change).astype(bool)
        r["training_sessions"] = self._walk_back(r, emp.training_times_last_year.to_numpy()[r.emp_idx],
                                                 self._stayers_distribution(emp, "training_times_last_year"),
                                                 cfg.training_change).astype(int)

        # KPI score: correlated with the rating, not determined by it
        bands = np.array([KPI_BANDS[int(rating)] for rating in r.rating])
        r["kpi_score"] = np.round(self._truncated_normal(*bands.T), 2)

        pick = self._rng.integers(0, 10, n)
        has_comment = self._rng.random(n) < 0.35
        r["comments"] = [COMMENTS[int(rt)][p % len(COMMENTS[int(rt)])] if c else None
                         for rt, p, c in zip(r.rating, pick, has_comment)]

        r["employee_code"] = emp.employee_code.to_numpy()[r.emp_idx]
        r["reviewer_code"] = self._reviewers(emp, history, r, department_heads)
        return r.sort_values(["emp_idx", "year"], ignore_index=True)

    def _truncated_normal(self, mean: np.ndarray, sd: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray:
        """Normal values redrawn until they fall inside [low, high] — unlike clipping, no value
        piles up at the limits (D27)."""
        values = self._rng.normal(mean, sd)
        outside = (values < low) | (values > high)
        while outside.any():
            values[outside] = self._rng.normal(mean[outside], sd[outside])
            outside = (values < low) | (values > high)
        return values

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

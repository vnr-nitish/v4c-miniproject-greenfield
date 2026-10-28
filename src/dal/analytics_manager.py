"""AnalyticsManager — the warehouse (star schema) queries behind the Phase 4 dashboards.

Everything here reads hr_dw only. The SQL lives here as parameterized CTEs with window functions
(decision B7), not in warehouse views, so rankings and comparisons are computed over exactly the
rows the dashboard filters select:

    yoy_trends()          LAG()                    change against the previous review cycle
    attrition_trend()     LAG(), SCD Type 2        headcount per department as it was at the time
    top_performers()      DENSE_RANK()             ranks per department, ties share a rank
    scored_workforce()    ROW_NUMBER(), NTILE(), PERCENT_RANK()
                                                   latest review, pay quartile within level, percentile
    risk_backtest()       NTILE(), SUM() OVER ()   how well the score separated past leavers
    bottleneck_projects() RANK()                   overdue open projects and their teams' strain

The attrition-risk score (decision B8) is a points score built from work-related factors that
the data shows matter and that HR can act on. Personal characteristics (marital status, age,
gender) are deliberately left out even where they correlate with attrition.

Results are cached per warehouse version: the id of the latest finished ETL batch (every change
to hr_dw — an ETL run, an SCD Type 2 apply, a project deletion — is a logged batch) and the
date. A new batch makes every older entry unreachable, so the cache never serves stale results.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from src.db_manager import DatabaseConnection
from src.exceptions import ValidationError

_FISCAL_YEAR = re.compile(r"FY\d{4}-\d{2}")
_CACHE_SIZE = 128


@dataclass(frozen=True)
class AnalyticsFilter:
    """The dashboard's filters. Empty means "all". Departments are given by name.

    fiscal_years selects review cycles (reviews for performance year 2024 are held early in 2025,
    i.e. in FY2024-25). The attrition-risk views describe the current workforce, so they use
    departments and job levels only.
    """

    departments: tuple[str, ...] = ()
    job_levels: tuple[int, ...] = ()
    fiscal_years: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        errors = {}
        object.__setattr__(self, "departments", tuple(str(d) for d in self.departments))
        object.__setattr__(self, "fiscal_years", tuple(str(y) for y in self.fiscal_years))
        try:
            object.__setattr__(self, "job_levels", tuple(int(level) for level in self.job_levels))
        except (TypeError, ValueError):
            errors["job_levels"] = "Job levels must be whole numbers"
        if "job_levels" not in errors and any(not 1 <= level <= 5 for level in self.job_levels):
            errors["job_levels"] = "Job levels must be 1-5"
        if any(not _FISCAL_YEAR.fullmatch(y) for y in self.fiscal_years):
            errors["fiscal_years"] = "Fiscal years look like FY2024-25"
        if errors:
            raise ValidationError(errors)

    def where(self, department: str | None = None, job_level: str | None = None,
              fiscal_year: str | None = None) -> tuple[str, list[Any]]:
        """' AND …' conditions on the given columns, and their parameters. The column names come
        from this module's own SQL; only the values come from the user, and they are bound."""
        sql, params = "", []
        for column, values in ((department, self.departments), (job_level, self.job_levels),
                               (fiscal_year, self.fiscal_years)):
            if column and values:
                sql += f" AND {column} IN ({', '.join(['%s'] * len(values))})"
                params.extend(values)
        return sql, params

    def select(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The same department / job-level filter applied to a DataFrame."""
        keep = pd.Series(True, index=frame.index)
        if self.departments:
            keep &= frame.department_name.isin(self.departments)
        if self.job_levels:
            keep &= frame.job_level.isin(self.job_levels)
        return frame[keep]


@dataclass(frozen=True)
class RiskFactor:
    """One attrition-risk factor: a condition on the scoring columns and the points it adds."""

    label: str
    points: int
    condition: str


# Calibrated on the warehouse (docs/backend_design.md B8, B21): one-year attrition after a review
# is about 3x as high with overtime (6.4 % vs 2.0 %), at job level 1 (5.5 % vs <= 2.7 %) and
# without stock options (5.0 % vs <= 3.7 %); the one-point factors each add 1.5-2.6x.
RISK_FACTORS = (
    RiskFactor("Works overtime", 2, "overtime_flag = 1"),
    RiskFactor("Entry level (job level 1)", 2, "job_level = 1"),
    RiskFactor("No stock options", 2, "stock_option_level = 0"),
    RiskFactor("Less than 2 years at the company", 1, "tenure_years < 2"),
    RiskFactor("Low environment satisfaction", 1, "environment_satisfaction = 1"),
    RiskFactor("Low job satisfaction", 1, "job_satisfaction = 1"),
    RiskFactor("Poor work-life balance", 1, "work_life_balance = 1"),
    RiskFactor("Low job involvement", 1, "job_involvement = 1"),
    RiskFactor("Travels frequently", 1, "business_travel = 'Travel_Frequently'"),
    RiskFactor("Lives more than 10 km away", 1, "distance_from_home_km > 10"),
    RiskFactor("Paid in the lower half of the level", 1, "pay_quartile <= 2"),
)
MAX_RISK_SCORE = sum(f.points for f in RISK_FACTORS)

# (lowest score, band), highest band first; back-tested one-year attrition 19.5 %, 8.0 %, 1.9 %, 0.7 %
RISK_BANDS = ((8, "Very high"), (6, "High"), (3, "Medium"), (0, "Low"))

_SCORE_SQL = " + ".join(f"{f.points} * COALESCE({f.condition}, 0)" for f in RISK_FACTORS)
_FLAGS_SQL = ", ".join(f"COALESCE({f.condition}, 0) AS factor_{i}" for i, f in enumerate(RISK_FACTORS))
_REASONS_SQL = ("CONCAT_WS(', ', "
                + ", ".join(f"IF({f.condition}, '{f.label}', NULL)" for f in RISK_FACTORS) + ")")
_BAND_SQL = "CASE " + " ".join(f"WHEN score >= {low} THEN '{band}'" for low, band in RISK_BANDS) + " END"
_BAND_ORDER_SQL = ("CASE " + " ".join(f"WHEN score >= {low} THEN {len(RISK_BANDS) - i}"
                                      for i, (low, _) in enumerate(RISK_BANDS)) + " END")

# Every active employee with their score. The windows run over narrow rows (ids and numbers);
# names are joined at the end. Pay quartiles and percentiles are company-wide, so a dashboard
# filter never changes anyone's score.
_SCORED_WORKFORCE_SQL = f"""
WITH ranked_reviews AS (
    SELECT de.employee_id, f.review_id,
           ROW_NUMBER() OVER (PARTITION BY de.employee_id ORDER BY f.date_sk DESC, f.review_id DESC) AS rn
    FROM hr_dw.fact_performance_reviews f
    JOIN hr_dw.dim_employee de ON de.employee_sk = f.employee_sk
    WHERE de.exit_date IS NULL
),
latest_review AS (
    SELECT r.employee_id, f.date_sk, f.overtime_flag, f.environment_satisfaction, f.job_satisfaction,
           f.work_life_balance, f.job_involvement, f.performance_rating
    FROM ranked_reviews r
    JOIN hr_dw.fact_performance_reviews f ON f.review_id = r.review_id
    WHERE r.rn = 1
),
workforce AS (
    SELECT e.employee_sk, e.job_level, e.stock_option_level, e.business_travel, e.distance_from_home_km,
           TIMESTAMPDIFF(YEAR, e.hire_date, CURDATE())                         AS tenure_years,
           NTILE(4) OVER (PARTITION BY e.job_level ORDER BY e.monthly_income)   AS pay_quartile,
           r.overtime_flag, r.environment_satisfaction, r.job_satisfaction, r.work_life_balance,
           r.job_involvement, r.performance_rating AS last_rating, r.date_sk AS last_review_sk
    FROM hr_dw.dim_employee e
    LEFT JOIN latest_review r ON r.employee_id = e.employee_id
    WHERE e.is_current AND e.exit_date IS NULL
),
scored AS (
    SELECT w.*, {_SCORE_SQL} AS score FROM workforce w
),
banded AS (
    SELECT s.*, {_BAND_SQL} AS risk_band, {_BAND_ORDER_SQL} AS band_order,
           ROUND(100 * PERCENT_RANK() OVER (ORDER BY s.score), 1) AS risk_percentile
    FROM scored s
),
explained AS (
    SELECT b.*, {_REASONS_SQL} AS risk_factors, {_FLAGS_SQL} FROM banded b
)
SELECT e.employee_id, e.employee_code, e.full_name, e.department_name, e.job_role, e.job_level, e.manager_name,
       b.tenure_years, e.monthly_income, b.pay_quartile, e.stock_option_level, e.business_travel,
       e.distance_from_home_km, b.overtime_flag, b.environment_satisfaction, b.job_satisfaction,
       b.work_life_balance, b.job_involvement, b.last_rating, d.full_date AS last_review_date,
       b.score, b.risk_band, b.band_order, b.risk_percentile, b.risk_factors,
       {", ".join(f"b.factor_{i}" for i in range(len(RISK_FACTORS)))}
FROM explained b
JOIN hr_dw.dim_employee e ON e.employee_sk = b.employee_sk
LEFT JOIN hr_dw.dim_date d ON d.date_sk = b.last_review_sk
ORDER BY b.score DESC, e.employee_code"""

_PERIODS = {"fiscal_year": "d.fiscal_year", "calendar_year": "d.calendar_year"}
_DELIVERY_GROUPS = {"priority": "priority", "budget_band": "budget_band",
                    "department": "owning_department_name", "year": "YEAR(planned_end_date)"}
_OPEN_STATUSES = "('Planned', 'Active', 'On Hold')"


def _copy(value: Any) -> Any:
    """Cached results are handed out as copies, so a caller cannot change the cache."""
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, dict):
        return dict(value)
    return value


class AnalyticsManager(DatabaseConnection):
    """Read-only analytics over the star schema, cached per warehouse version."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not hasattr(self, "_cache"):           # a Singleton: __init__ runs on every AnalyticsManager()
            self._cache: OrderedDict[tuple, Any] = OrderedDict()
            self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ cache

    def data_version(self) -> tuple:
        """(latest finished batch id, today). A batch's status leaves STARTED in the same commit
        as its data, so a query running during an ETL can never be cached as the new version."""
        row = self.fetch_one(
            "SELECT COALESCE(MAX(batch_id), 0), CURDATE() FROM hr_dw.etl_batch_log "
            "WHERE log_id = batch_id AND status <> 'STARTED'", dictionary=False)
        return tuple(row)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def _cached(self, key: tuple, compute: Callable[[], Any]) -> Any:
        if self.in_rollback_session():            # uncommitted data must never reach the shared cache
            return compute()
        key = (self.data_version(),) + key
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return _copy(self._cache[key])
        result = compute()                        # outside the lock: other queries can be served meanwhile
        with self._cache_lock:
            self._cache[key] = result
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        return _copy(result)

    # ------------------------------------------------------------------ filter options

    def filter_options(self) -> dict[str, list]:
        """What the dashboard's filters can offer: departments, job levels, review cycles."""
        def compute() -> dict[str, list]:
            departments = self.fetch_frame(
                "SELECT department_name FROM hr_dw.dim_department ORDER BY department_name")
            cycles = self.fetch_frame(
                "SELECT fiscal_year FROM hr_dw.dim_date "
                "WHERE date_sk BETWEEN (SELECT MIN(date_sk) FROM hr_dw.fact_performance_reviews) "
                "                  AND (SELECT MAX(date_sk) FROM hr_dw.fact_performance_reviews) "
                "GROUP BY fiscal_year ORDER BY fiscal_year")
            return {"departments": departments.department_name.tolist(), "job_levels": [1, 2, 3, 4, 5],
                    "fiscal_years": cycles.fiscal_year.tolist()}
        return self._cached(("filter_options",), compute)

    # ------------------------------------------------------------------ overview

    def overview(self, filters: AnalyticsFilter = AnalyticsFilter()) -> dict[str, Any]:
        """Headline numbers: headcount, attrition in the latest complete fiscal year, the latest
        review cycle against the one before it, and project delivery."""
        def compute() -> dict[str, Any]:
            where, params = filters.where("department_name", "job_level")
            headcount = self.fetch_one(
                "SELECT COUNT(*) AS headcount FROM hr_dw.dim_employee "
                "WHERE is_current AND exit_date IS NULL" + where, params)["headcount"]
            where, params = filters.where("owning_department_name")
            projects = self.fetch_one(
                f"SELECT COALESCE(SUM(status IN {_OPEN_STATUSES}), 0) AS open_projects, "
                f"       COALESCE(SUM(status IN {_OPEN_STATUSES} AND planned_end_date < CURDATE()), 0) "
                "            AS overdue_projects, "
                "        ROUND(100 * AVG(CASE WHEN status = 'Completed' THEN is_delayed END), 1) AS delayed_pct "
                "FROM hr_dw.dim_project WHERE project_id <> 0" + where, params)
            cycles = self.yoy_trends(filters)
            latest = cycles.iloc[-1].to_dict() if len(cycles) else {}
            years = self.attrition_trend(AnalyticsFilter(filters.departments, filters.job_levels))
            complete = years[years.is_complete == 1]
            attrition = complete.iloc[-1].to_dict() if len(complete) else {}
            return {
                "headcount": int(headcount),
                "attrition_year": attrition.get("fiscal_year"),
                "leavers": int(attrition["leavers"]) if attrition else 0,
                "attrition_pct": attrition.get("attrition_pct"),
                "attrition_change": attrition.get("attrition_change"),
                "latest_cycle": latest.get("period"),
                "reviews": int(latest["reviews"]) if latest else 0,
                "avg_rating": latest.get("avg_rating"),
                "rating_change": latest.get("rating_change"),
                "avg_kpi": latest.get("avg_kpi"),
                "kpi_change": latest.get("kpi_change"),
                "outstanding_pct": latest.get("outstanding_pct"),
                "open_projects": int(projects["open_projects"]),
                "overdue_projects": int(projects["overdue_projects"]),
                "delayed_pct": None if projects["delayed_pct"] is None else float(projects["delayed_pct"]),
            }
        return self._cached(("overview", filters), compute)

    # ------------------------------------------------------------------ year-over-year trends

    def yoy_trends(self, filters: AnalyticsFilter = AnalyticsFilter(), period: str = "fiscal_year",
                   by_department: bool = False) -> pd.DataFrame:
        """Review results per cycle, with the change against the previous cycle (LAG).

        period: 'fiscal_year' (default — one review cycle per fiscal year) or 'calendar_year'
        of the review date. by_department compares each department with its own previous year.
        """
        if period not in _PERIODS:
            raise ValidationError({"period": f"Period must be one of {', '.join(_PERIODS)}"})
        where, params = filters.where("dd.department_name", "de.job_level", "d.fiscal_year")
        # the employee dimension is joined only when a job-level filter needs it (463K lookups otherwise)
        employee_join = ("JOIN hr_dw.dim_employee de ON de.employee_sk = f.employee_sk "
                         if filters.job_levels else "")
        department = "dd.department_name, " if by_department else ""
        partition = "PARTITION BY department_name " if by_department else ""
        sql = f"""
            WITH cycles AS (
                SELECT {_PERIODS[period]} AS period, {department}
                       COUNT(*)                                AS reviews,
                       AVG(f.performance_rating)               AS avg_rating,
                       AVG(f.kpi_score)                        AS avg_kpi,
                       100 * AVG(f.performance_rating = 4)     AS outstanding_pct,
                       100 * AVG(f.performance_rating <= 2)    AS low_rated_pct,
                       100 * AVG(f.overtime_flag)              AS overtime_pct,
                       AVG(f.job_satisfaction)                 AS avg_job_satisfaction,
                       AVG(f.training_sessions)                AS avg_training_sessions
                FROM hr_dw.fact_performance_reviews f
                JOIN hr_dw.dim_date d        ON d.date_sk = f.date_sk
                JOIN hr_dw.dim_department dd ON dd.department_sk = f.department_sk
                {employee_join}
                WHERE 1 = 1 {where}
                GROUP BY period{', dd.department_name' if by_department else ''}
            )
            SELECT period, {'department_name, ' if by_department else ''}reviews,
                   ROUND(avg_rating, 3)                                        AS avg_rating,
                   ROUND(avg_kpi, 2)                                           AS avg_kpi,
                   ROUND(outstanding_pct, 1)                                   AS outstanding_pct,
                   ROUND(low_rated_pct, 1)                                     AS low_rated_pct,
                   ROUND(overtime_pct, 1)                                      AS overtime_pct,
                   ROUND(avg_job_satisfaction, 2)                              AS avg_job_satisfaction,
                   ROUND(avg_training_sessions, 2)                             AS avg_training_sessions,
                   ROUND(avg_rating - LAG(avg_rating) OVER w, 3)               AS rating_change,
                   ROUND(avg_kpi - LAG(avg_kpi) OVER w, 2)                     AS kpi_change,
                   ROUND(outstanding_pct - LAG(outstanding_pct) OVER w, 1)     AS outstanding_change,
                   ROUND(100 * (reviews / LAG(reviews) OVER w - 1), 1)         AS reviews_change_pct
            FROM cycles
            WINDOW w AS ({partition}ORDER BY period)
            ORDER BY {'department_name, ' if by_department else ''}period"""
        return self._cached(("yoy_trends", filters, period, by_department), lambda: self.fetch_frame(sql, params))

    def attrition_trend(self, filters: AnalyticsFilter = AnalyticsFilter(),
                        by_department: bool = False) -> pd.DataFrame:
        """Attrition per fiscal year: leavers / average headcount, with the change against the
        previous year (LAG).

        Headcounts are point-in-time through SCD Type 2: an employee is counted in the department
        and job level of the version valid on the day — at the start and the end of the year, and
        on the day they left — not in their current one. The current fiscal year is included
        while it runs (is_complete = 0); headcount_end is then today's.
        """
        where, params = filters.where("e.department_name", "e.job_level", "fy.fiscal_year")
        department = "e.department_name, " if by_department else ""
        partition = "PARTITION BY department_name " if by_department else ""
        sql = f"""
            WITH fiscal AS (
                SELECT fiscal_year, MIN(full_date) AS fy_start, MAX(full_date) AS fy_end,
                       LEAST(MAX(full_date), CURDATE()) AS end_ref, MAX(full_date) < CURDATE() AS is_complete
                FROM hr_dw.dim_date
                GROUP BY fiscal_year
                HAVING MIN(full_date) <= CURDATE()
                   AND MAX(full_date) >= (SELECT d.full_date FROM hr_dw.dim_date d
                                          WHERE d.date_sk = (SELECT MIN(date_sk) FROM hr_dw.fact_performance_reviews))
            ),
            counts AS (
                SELECT fy.fiscal_year, {department} fy.is_complete,
                       SUM(fy.fy_start BETWEEN e.start_date AND e.end_date
                           AND (e.exit_date IS NULL OR e.exit_date >= fy.fy_start))           AS headcount_start,
                       SUM(fy.end_ref BETWEEN e.start_date AND e.end_date
                           AND (e.exit_date IS NULL OR e.exit_date > fy.end_ref))             AS headcount_end,
                       SUM(e.exit_date BETWEEN fy.fy_start AND fy.end_ref
                           AND e.exit_date BETWEEN e.start_date AND e.end_date)               AS leavers,
                       SUM(e.exit_date BETWEEN fy.fy_start AND fy.end_ref
                           AND e.exit_date BETWEEN e.start_date AND e.end_date
                           AND e.is_voluntary_exit)                                           AS voluntary_leavers
                FROM hr_dw.dim_employee e
                STRAIGHT_JOIN fiscal fy ON e.start_date <= fy.fy_end AND e.end_date >= fy.fy_start
                WHERE 1 = 1 {where}
                GROUP BY fy.fiscal_year, {department} fy.is_complete
            )
            SELECT fiscal_year, {'department_name, ' if by_department else ''}is_complete,
                   headcount_start, headcount_end, leavers, voluntary_leavers,
                   ROUND(100 * leavers / NULLIF((headcount_start + headcount_end) / 2, 0), 2) AS attrition_pct,
                   ROUND(100 * leavers / NULLIF((headcount_start + headcount_end) / 2, 0)
                         - LAG(100 * leavers / NULLIF((headcount_start + headcount_end) / 2, 0)) OVER w, 2)
                                                                                         AS attrition_change
            FROM counts
            WINDOW w AS ({partition}ORDER BY fiscal_year)
            ORDER BY {'department_name, ' if by_department else ''}fiscal_year"""
        return self._cached(("attrition_trend", filters, by_department), lambda: self.fetch_frame(sql, params))

    # ------------------------------------------------------------------ top performers

    def top_performers(self, filters: AnalyticsFilter = AnalyticsFilter(), top_n: int = 10,
                       min_reviews: int = 1, active_only: bool = True) -> pd.DataFrame:
        """The best employees of each department over the selected cycles.

        Ranked with DENSE_RANK() per department by average rating, then average KPI: equal
        results share a rank (sharing_rank says how many) and the next rank follows without a
        gap, so a department can show more than top_n people. The department is the one at
        review time — someone who moved is ranked in each department they were reviewed in;
        current_department and current_role show where they are now.
        """
        if not (isinstance(top_n, int) and 1 <= top_n <= 100):
            raise ValidationError({"top_n": "Show 1-100 ranks per department"})
        if not (isinstance(min_reviews, int) and min_reviews >= 1):
            raise ValidationError({"min_reviews": "At least 1 review is required"})
        where, params = filters.where("dd.department_name", "de.job_level", "d.fiscal_year")
        # Leaving is a Type 1 attribute (the same on every version), so it is checked after the
        # aggregation: as a scan filter it misleads the optimizer into reading every active
        # employee's reviews before the cycle filter (7.9 s instead of about 1 s).
        sql = f"""
            WITH per_employee AS (
                SELECT dd.department_name, de.employee_id,
                       COUNT(*)                  AS reviews,
                       AVG(f.performance_rating) AS avg_rating,
                       AVG(f.kpi_score)          AS avg_kpi,
                       MIN(de.exit_date IS NULL) AS is_active
                FROM hr_dw.fact_performance_reviews f
                JOIN hr_dw.dim_date d        ON d.date_sk = f.date_sk
                JOIN hr_dw.dim_department dd ON dd.department_sk = f.department_sk
                JOIN hr_dw.dim_employee de   ON de.employee_sk = f.employee_sk
                WHERE 1 = 1 {where}
                GROUP BY dd.department_name, de.employee_id
                HAVING COUNT(*) >= %s
            ),
            ranked AS (
                SELECT p.*,
                       DENSE_RANK() OVER (PARTITION BY p.department_name
                                          ORDER BY p.avg_rating DESC, p.avg_kpi DESC) AS department_rank,
                       COUNT(*)     OVER (PARTITION BY p.department_name)             AS ranked_employees
                FROM per_employee p
                WHERE {'p.is_active' if active_only else 'TRUE'}
            )
            SELECT r.department_name, r.department_rank,
                   COUNT(*) OVER (PARTITION BY r.department_name, r.department_rank) AS sharing_rank,
                   cur.employee_code, cur.full_name, cur.department_name AS current_department,
                   cur.job_role AS current_role, cur.job_level, cur.attrition_flag AS has_left, r.reviews,
                   ROUND(r.avg_rating, 2) AS avg_rating, ROUND(r.avg_kpi, 2) AS avg_kpi, r.ranked_employees
            FROM ranked r
            JOIN hr_dw.dim_employee cur ON cur.employee_id = r.employee_id AND cur.is_current
            WHERE r.department_rank <= %s
            ORDER BY r.department_name, r.department_rank, cur.employee_code"""
        return self._cached(("top_performers", filters, top_n, min_reviews, active_only),
                            lambda: self.fetch_frame(sql, params + [min_reviews, top_n]))

    # ------------------------------------------------------------------ attrition risk

    def scored_workforce(self) -> pd.DataFrame:
        """Every active employee with their attrition-risk score, band, percentile and factors
        (factor_0, factor_1, … are the RISK_FACTORS flags). One query for the whole company; the
        filtered views below are slices of it."""
        return self._cached(("scored_workforce",), lambda: self.fetch_frame(_SCORED_WORKFORCE_SQL))

    def _at_risk(self, filters: AnalyticsFilter, min_band: str) -> pd.DataFrame:
        """The scored workforce within the filters, at or above a risk band."""
        bands = [band for _, band in RISK_BANDS]
        if min_band not in bands:
            raise ValidationError({"min_band": f"Band must be one of {', '.join(bands)}"})
        scored = filters.select(self.scored_workforce())
        return scored[scored.band_order >= len(bands) - bands.index(min_band)]

    def attrition_risk(self, filters: AnalyticsFilter = AnalyticsFilter(), limit: int = 200,
                       min_band: str = "Low") -> pd.DataFrame:
        """Active employees by attrition-risk score, highest first, with the factors behind it."""
        scored = self._at_risk(filters, min_band)
        return scored.drop(columns=[c for c in scored.columns if c.startswith("factor_")]) \
                     .head(limit).reset_index(drop=True)

    def risk_summary(self, filters: AnalyticsFilter = AnalyticsFilter()) -> pd.DataFrame:
        """Active employees per department and risk band, with each band's share of the department."""
        scored = filters.select(self.scored_workforce())
        summary = (scored.groupby(["department_name", "risk_band", "band_order"], as_index=False)
                         .size().rename(columns={"size": "employees"}))
        summary["share_pct"] = (100 * summary.employees
                                / summary.groupby("department_name").employees.transform("sum")).round(1)
        return summary.sort_values(["department_name", "band_order"], ignore_index=True)

    def risk_factor_prevalence(self, filters: AnalyticsFilter = AnalyticsFilter(),
                               min_band: str = "High") -> pd.DataFrame:
        """How common each risk factor is among the active employees at or above a band."""
        scored = self._at_risk(filters, min_band)
        rows = [{"factor": f.label, "points": f.points, "employees": int(scored[f"factor_{i}"].sum()),
                 "share_pct": round(100 * scored[f"factor_{i}"].mean(), 1) if len(scored) else 0.0}
                for i, f in enumerate(RISK_FACTORS)]
        return pd.DataFrame(rows).sort_values("share_pct", ascending=False, ignore_index=True)

    def risk_backtest(self) -> pd.DataFrame:
        """Evidence for the score: every past review scored with what was known at the time
        (the employee version and survey of that review), and how many of those employees left
        within the following year.

        Only reviews whose following year is fully observed are used (up to one year before the
        latest recorded exit). lift = the band's attrition / overall attrition.
        """
        sql = f"""
            WITH observed AS (
                SELECT f.overtime_flag, de.job_level, de.stock_option_level, f.environment_satisfaction,
                       f.job_satisfaction, f.work_life_balance, f.job_involvement,
                       de.business_travel, de.distance_from_home_km,
                       TIMESTAMPDIFF(YEAR, de.hire_date, d.full_date) AS tenure_years,
                       NTILE(4) OVER (PARTITION BY d.fiscal_year, de.job_level
                                      ORDER BY f.monthly_income_at_review) AS pay_quartile,
                       (de.exit_date IS NOT NULL AND de.exit_date <= d.full_date + INTERVAL 365 DAY) AS left_within_year
                FROM hr_dw.fact_performance_reviews f
                JOIN hr_dw.dim_employee de ON de.employee_sk = f.employee_sk
                JOIN hr_dw.dim_date d      ON d.date_sk = f.date_sk
                WHERE d.full_date <= (SELECT MAX(exit_date) - INTERVAL 365 DAY
                                      FROM hr_dw.dim_employee WHERE is_current)
            ),
            scored AS (SELECT left_within_year, {_SCORE_SQL} AS score FROM observed),
            banded AS (SELECT left_within_year, {_BAND_SQL} AS risk_band, {_BAND_ORDER_SQL} AS band_order
                       FROM scored)
            SELECT risk_band, band_order,
                   COUNT(*)                                                                    AS reviews,
                   ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                            AS share_of_reviews_pct,
                   SUM(left_within_year)                                                       AS leavers,
                   ROUND(100 * AVG(left_within_year), 2)                                       AS left_within_year_pct,
                   ROUND(100 * SUM(left_within_year) / SUM(SUM(left_within_year)) OVER (), 1)  AS share_of_leavers_pct,
                   ROUND(AVG(left_within_year) / (SUM(SUM(left_within_year)) OVER () / SUM(COUNT(*)) OVER ()), 2)
                                                                                               AS lift
            FROM banded
            GROUP BY risk_band, band_order
            ORDER BY band_order"""
        return self._cached(("risk_backtest",), lambda: self.fetch_frame(sql))

    # ------------------------------------------------------------------ project bottlenecks

    def project_delivery(self, filters: AnalyticsFilter = AnalyticsFilter(), by: str = "priority") -> pd.DataFrame:
        """Delivery of completed projects grouped by priority, budget band, department or year
        of the planned end: how many finished late, and by how much."""
        if by not in _DELIVERY_GROUPS:
            raise ValidationError({"by": f"Group by one of {', '.join(_DELIVERY_GROUPS)}"})
        where, params = filters.where("owning_department_name")
        sql = f"""
            SELECT {_DELIVERY_GROUPS[by]} AS `{by}`,
                   COUNT(*)                                                AS completed_projects,
                   SUM(is_delayed)                                         AS delayed_projects,
                   ROUND(100 * AVG(is_delayed), 1)                         AS delayed_pct,
                   ROUND(AVG(delay_days), 1)                               AS avg_delay_days,
                   ROUND(AVG(CASE WHEN is_delayed THEN delay_days END), 1) AS avg_delay_when_late,
                   MAX(delay_days)                                         AS worst_delay_days
            FROM hr_dw.dim_project
            WHERE status = 'Completed' AND project_id <> 0 {where}
            GROUP BY `{by}`
            ORDER BY {'`year`' if by == 'year' else 'delayed_pct DESC'}"""
        return self._cached(("project_delivery", filters, by), lambda: self.fetch_frame(sql, params))

    def bottleneck_projects(self, filters: AnalyticsFilter = AnalyticsFilter(), limit: int = 25) -> pd.DataFrame:
        """Open projects past their planned end, most overdue first (RANK), with the strain on
        their reviewed team members: overtime share and average KPI."""
        where, params = filters.where("p.owning_department_name")
        sql = f"""
            WITH overdue AS (
                SELECT p.project_sk, p.project_code, p.project_name, p.owning_department_name, p.project_manager_name,
                       p.status, p.priority, p.budget, p.start_date, p.planned_end_date,
                       DATEDIFF(CURDATE(), p.planned_end_date) AS days_overdue
                FROM hr_dw.dim_project p
                WHERE p.status IN {_OPEN_STATUSES} AND p.planned_end_date < CURDATE() AND p.project_id <> 0 {where}
            ),
            team AS (
                SELECT f.project_sk, COUNT(*) AS team_reviews,
                       ROUND(100 * AVG(f.overtime_flag), 1) AS team_overtime_pct,
                       ROUND(AVG(f.kpi_score), 1)           AS team_avg_kpi
                FROM hr_dw.fact_performance_reviews f
                WHERE f.project_sk IN (SELECT project_sk FROM overdue)
                GROUP BY f.project_sk
            )
            SELECT RANK() OVER (ORDER BY o.days_overdue DESC) AS overdue_rank,
                   o.project_code, o.project_name, o.owning_department_name, o.project_manager_name,
                   o.status, o.priority, o.budget, o.start_date, o.planned_end_date, o.days_overdue,
                   COALESCE(t.team_reviews, 0) AS team_reviews, t.team_overtime_pct, t.team_avg_kpi
            FROM overdue o LEFT JOIN team t ON t.project_sk = o.project_sk
            ORDER BY overdue_rank, o.project_code
            LIMIT %s"""
        return self._cached(("bottleneck_projects", filters, limit), lambda: self.fetch_frame(sql, params + [limit]))

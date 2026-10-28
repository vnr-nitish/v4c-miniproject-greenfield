"""SynthesisValidator — proves the synthetic data is valid and realistic, and writes the report.

Three kinds of evidence (docs/synthesizer_design.md):
1. Rule checks: every business rule of the OLTP schema holds (violations must be 0).
2. Fidelity: the IBM tenure values can be reconstructed from the generated history.
3. Realism: synthetic distributions and correlations compared with the IBM source.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from src.synthesizer.base import BaseGenerator
from src.synthesizer.dataset import SyntheticDataset
from src.synthesizer.performance import KPI_BANDS
from src.synthesizer.reference import DEPARTMENT_ROLES, JOB_LEVELS, JOB_ROLES

full_years = BaseGenerator.full_years
D = "datetime64[D]"


def markdown_table(df: pd.DataFrame) -> str:
    """Render a small DataFrame as a Markdown table (index as the first column)."""
    header = "| " + " | ".join([str(df.index.name or "")] + [str(c) for c in df.columns]) + " |"
    rule = "|---" + "|---:" * len(df.columns) + "|"
    body = ["| " + " | ".join([str(i)] + [f"{v:,.2f}" if isinstance(v, (float, np.floating)) else str(v)
                                           for v in row]) + " |"
            for i, row in zip(df.index, df.itertuples(index=False))]
    return "\n".join([header, rule, *body])


class SynthesisValidator:

    def __init__(self, source: pd.DataFrame, data: SyntheticDataset, config, extras: dict) -> None:
        self._src = source
        self._d = data
        self._cfg = config
        self._extras = extras          # department heads, unassigned manager segments, ...
        self.checks: list[tuple[str, str, int]] = []      # (area, rule, violations)
        self.fidelity: list[tuple[str, float]] = []        # (IBM column, % reproduced exactly)

    # ================================================================== rule checks

    def run(self) -> "SynthesisValidator":
        self._check_employees()
        self._check_history()
        self._check_fidelity()
        self._check_reviews_surveys()
        self._check_projects()
        return self

    def _add(self, area: str, rule: str, violations) -> None:
        self.checks.append((area, rule, int(np.sum(violations))))

    @property
    def total_violations(self) -> int:
        return sum(v for _, _, v in self.checks)

    def _check_employees(self) -> None:
        e = self._d.employees
        ref = e.ref_date.to_numpy().astype(D)
        dob, hire = e.date_of_birth.to_numpy().astype(D), e.hire_date.to_numpy().astype(D)
        ext = e.exit_date.to_numpy().astype(D)
        low = e.job_level.map(lambda lv: JOB_LEVELS[lv][1])
        high = e.job_level.map(lambda lv: JOB_LEVELS[lv][2])
        self._add("employees", "employee_code is unique", len(e) - e.employee_code.nunique())
        self._add("employees", "email is unique", len(e) - e.email.nunique())
        self._add("employees", "date of birth reproduces Age", full_years(dob, ref) != e.age.to_numpy())
        self._add("employees", "hire date reproduces YearsAtCompany", full_years(hire, ref) != e.years_at_company.to_numpy())
        self._add("employees", "hired at age 18 or older", full_years(dob, hire) < 18)
        self._add("employees", "exit date on or after hire date", ~np.isnat(ext) & (ext < hire))
        self._add("employees", "income inside the level's salary band", (e.monthly_income < low) | (e.monthly_income > high))
        self._add("employees", "leavers have an exit reason, stayers none",
                  (e.attrition == "Yes") != e.exit_reason.notna())

    def _check_history(self) -> None:
        h = self._d.job_history.sort_values(["emp_idx", "version_number"])
        e = self._d.employees
        start, end = h.effective_start_date.to_numpy().astype(D), h.effective_end_date.to_numpy().astype(D)
        open_per_emp = pd.isna(h.effective_end_date).groupby(h.emp_idx).sum()
        self._add("job history", "exactly one current version per employee", open_per_emp != 1)
        first = h.groupby("emp_idx").head(1)
        self._add("job history", "first version starts on the hire date",
                  first.effective_start_date.to_numpy().astype(D) != e.hire_date.to_numpy().astype(D)[first.emp_idx])
        nxt = h.groupby("emp_idx").effective_start_date.shift(-1).to_numpy().astype(D)
        has_next = ~np.isnat(nxt)
        self._add("job history", "versions are contiguous (end = next start)", has_next & (end != nxt))
        self._add("job history", "end date after start date", ~np.isnat(end) & (end <= start))
        valid_pair = [r in DEPARTMENT_ROLES[d] for d, r in zip(h.department, h.job_role)]
        self._add("job history", "valid department-role pair", ~np.array(valid_pair))
        lo = h.job_role.map(lambda r: JOB_ROLES[r][0])
        hi = h.job_role.map(lambda r: JOB_ROLES[r][1])
        self._add("job history", "level inside the role's range", (h.job_level < lo) | (h.job_level > hi))
        band_lo = h.job_level.map(lambda lv: JOB_LEVELS[lv][1])
        band_hi = h.job_level.map(lambda lv: JOB_LEVELS[lv][2])
        self._add("job history", "income inside the level's band", (h.monthly_income < band_lo) | (h.monthly_income > band_hi))
        cur = h[pd.isna(h.effective_end_date)].set_index("emp_idx").sort_index()
        same = ((cur.department.to_numpy() == e.department.to_numpy()) & (cur.job_role.to_numpy() == e.job_role.to_numpy())
                & (cur.job_level.to_numpy() == e.job_level.to_numpy())
                & (cur.monthly_income.to_numpy() == e.monthly_income.to_numpy()))
        self._add("job history", "current version equals the IBM row", ~same)
        mgr = h.manager_idx.to_numpy()
        has_mgr = mgr >= 0
        self._add("job history", "nobody is their own manager", has_mgr & (mgr == h.emp_idx.to_numpy()))
        m_hire = e.hire_date.to_numpy().astype(D)[np.maximum(mgr, 0)]
        m_exit = e.exit_date.to_numpy().astype(D)[np.maximum(mgr, 0)]
        employed = (m_hire <= start) & (np.isnat(m_exit) | (m_exit > start))
        self._add("job history", "manager employed when the version starts", has_mgr & ~employed)

    def _check_fidelity(self) -> None:
        """Reconstruct IBM tenure values from the history exactly as vw_employee_snapshot does."""
        h = self._d.job_history.sort_values(["emp_idx", "version_number"]).copy()
        e = self._d.employees
        ref = e.ref_date.to_numpy().astype(D)
        g = h.groupby("emp_idx")
        first = h.version_number == 1
        role_change = first | (g.job_role.shift() != h.job_role)
        mgr_prev = g.manager_idx.shift()
        mgr_change = first | (mgr_prev != h.manager_idx)
        promo = h.change_reason == "Promotion"
        h["s"] = h.effective_start_date

        def latest(mask):
            return h[mask].groupby("emp_idx").s.max().reindex(range(len(e))).to_numpy().astype(D)

        role_since = latest(role_change)
        mgr_since = latest(mgr_change)
        last_promo = latest(promo)
        last_promo = np.where(np.isnat(last_promo), e.hire_date.to_numpy().astype(D), last_promo)
        pairs = [
            ("YearsInCurrentRole", full_years(role_since, ref), e.years_in_current_role),
            ("YearsSinceLastPromotion", full_years(last_promo, ref), e.years_since_last_promotion),
            ("YearsWithCurrManager", full_years(mgr_since, ref), e.years_with_curr_manager),
        ]
        for name, rebuilt, ibm in pairs:
            self.fidelity.append((name, float(np.mean(rebuilt == ibm.to_numpy()) * 100)))
        prev_income = g.monthly_income.shift()
        changed = h[prev_income.notna() & (prev_income != h.monthly_income)].copy()
        changed["pct"] = np.round((changed.monthly_income / prev_income[changed.index] - 1) * 100)
        last = changed.groupby("emp_idx").tail(1).set_index("emp_idx").pct
        ibm_hike = e.percent_salary_hike.reindex(last.index)
        self.fidelity.append(("PercentSalaryHike (employees with a salary change)",
                              float(np.mean(np.abs(last - ibm_hike) <= 1) * 100)))

    def _check_reviews_surveys(self) -> None:
        r, s, e = self._d.reviews, self._d.surveys, self._d.employees
        self._add("reviews", "one review per employee per year", r.duplicated(["emp_idx", "year"]))
        hire = e.hire_date.to_numpy().astype(D)[r.emp_idx]
        ext = e.exit_date.to_numpy().astype(D)[r.emp_idx]
        rd = r.review_date.to_numpy().astype(D)
        self._add("reviews", "reviewed only while employed", (hire > rd) | (~np.isnat(ext) & (ext <= rd)))
        self._add("reviews", "has a reviewer who is not the employee",
                  r.reviewer_code.isna() | (r.reviewer_code == r.employee_code))
        latest = r.groupby("emp_idx").tail(1)
        self._add("reviews", "latest review carries the IBM rating",
                  latest.rating.to_numpy() != e.performance_rating.to_numpy()[latest.emp_idx])
        # D26: earlier years have the snapshot's distribution (the IBM values of the employees who
        # stayed); the tolerances allow for sampling noise in small builds and for leavers' decline
        stayers = e[e.attrition == "No"]
        self._add("reviews", "earlier reviews use only the ratings IBM uses",
                  ~r.rating.isin(e.performance_rating.unique()))
        yearly = r.groupby("year")
        self._add("reviews", "each year's share of outstanding ratings within 3 points of the snapshot",
                  (yearly.rating.apply(lambda x: (x == 4).mean()) - (stayers.performance_rating == 4).mean()).abs()
                  > 0.03)
        self._add("reviews", "each year's overtime share within 5 points of the snapshot",
                  (yearly.overtime.mean() - (stayers.over_time == "Yes").mean()).abs() > 0.05)
        # D27: KPI scores are drawn inside their band, not clipped onto its limits
        self._add("reviews", "KPI scores do not pile up at a band limit (at most 0.1 % of reviews)",
                  [self._kpi_at_band_limit() > 0.001])
        sd = s.survey_date.to_numpy().astype(D)
        shire = e.hire_date.to_numpy().astype(D)[s.emp_idx]
        sext = e.exit_date.to_numpy().astype(D)[s.emp_idx]
        self._add("surveys", "surveyed only while employed", (shire > sd) | (~np.isnat(sext) & (sext <= sd)))
        slatest = s.groupby("emp_idx").tail(1)
        for col in ("environment_satisfaction", "job_satisfaction", "relationship_satisfaction",
                    "job_involvement", "work_life_balance"):
            self._add("surveys", f"latest survey carries the IBM {col}",
                      slatest[col].to_numpy() != e[col].to_numpy()[slatest.emp_idx])
            self._add("surveys", f"each year's mean {col} within 0.10 of the snapshot",
                      (s.groupby("year")[col].mean() - stayers[col].mean()).abs() > 0.10)

    def _kpi_at_band_limit(self) -> float:
        """Share of reviews whose KPI score equals the lowest or highest value of its rating's band."""
        r = self._d.reviews
        low = r.rating.map(lambda rating: KPI_BANDS[rating][2])
        high = r.rating.map(lambda rating: KPI_BANDS[rating][3])
        return float(((r.kpi_score == low) | (r.kpi_score == high)).mean())

    def _check_projects(self) -> None:
        p, a, e = self._d.projects, self._d.assignments, self._d.employees
        closed = p.status.isin(["Completed", "Cancelled"])
        self._add("projects", "actual end date exactly when Completed/Cancelled", closed != p.actual_end_date.notna())
        self._add("projects", "actual end not before start", p.actual_end_date.notna() & (p.actual_end_date < p.start_date))
        hire = e.hire_date.to_numpy().astype(D)[a.emp_idx]
        ext = e.exit_date.to_numpy().astype(D)[a.emp_idx]
        st, en = a.start_date.to_numpy().astype(D), a.end_date.to_numpy().astype(D)
        self._add("assignments", "assignment starts after hire", st < hire)
        self._add("assignments", "assignment ends by the exit date", ~np.isnat(ext) & (np.isnat(en) | (en > ext)))
        self._add("assignments", "allocation between 1 and 100", (a.allocation_pct < 1) | (a.allocation_pct > 100))

    # ================================================================== realism comparisons

    def _share_table(self, column_ibm: str, synthetic: pd.Series) -> pd.DataFrame:
        ibm = self._src[column_ibm].value_counts(normalize=True) * 100
        syn = synthetic.value_counts(normalize=True) * 100
        t = pd.DataFrame({"IBM %": ibm, "Synthetic %": syn}).fillna(0)
        t["Diff (pp)"] = t["Synthetic %"] - t["IBM %"]
        return t.sort_index()

    def comparisons(self) -> dict[str, pd.DataFrame]:
        e = self._d.employees
        out = {}
        for ibm_col, syn_col in [("Department", "department"), ("JobRole", "job_role"), ("JobLevel", "job_level"),
                                 ("Gender", "gender"), ("MaritalStatus", "marital_status"),
                                 ("BusinessTravel", "business_travel"), ("OverTime", "over_time"),
                                 ("Attrition", "attrition")]:
            out[ibm_col] = self._share_table(ibm_col, e[syn_col])

        def attrition_by(ibm_key, syn_key):
            ibm = self._src.groupby(ibm_key).Attrition.apply(lambda s: (s == "Yes").mean() * 100)
            syn = e.groupby(syn_key).attrition.apply(lambda s: (s == "Yes").mean() * 100)
            return pd.DataFrame({"IBM attrition %": ibm, "Synthetic attrition %": syn})

        out["Attrition by OverTime"] = attrition_by("OverTime", "over_time")
        out["Attrition by JobLevel"] = attrition_by("JobLevel", "job_level")
        src_tenure = np.where(self._src.YearsAtCompany <= 2, "0-2 years", "3+ years")
        syn_tenure = np.where(e.years_at_company <= 2, "0-2 years", "3+ years")
        out["Attrition by tenure"] = pd.DataFrame({
            "IBM attrition %": self._src.groupby(src_tenure).Attrition.apply(lambda s: (s == "Yes").mean() * 100),
            "Synthetic attrition %": e.groupby(syn_tenure).attrition.apply(lambda s: (s == "Yes").mean() * 100)})
        out["Median monthly income by JobLevel"] = pd.DataFrame({
            "IBM": self._src.groupby("JobLevel").MonthlyIncome.median(),
            "Synthetic": e.groupby("job_level").monthly_income.median()})
        return out

    def correlation_difference(self) -> tuple[float, float]:
        cols = {"Age": "age", "DistanceFromHome": "distance_from_home", "Education": "education",
                "EnvironmentSatisfaction": "environment_satisfaction", "JobInvolvement": "job_involvement",
                "JobLevel": "job_level", "JobSatisfaction": "job_satisfaction", "MonthlyIncome": "monthly_income",
                "NumCompaniesWorked": "num_companies_worked", "PercentSalaryHike": "percent_salary_hike",
                "PerformanceRating": "performance_rating", "RelationshipSatisfaction": "relationship_satisfaction",
                "StockOptionLevel": "stock_option_level", "TotalWorkingYears": "total_working_years",
                "TrainingTimesLastYear": "training_times_last_year", "WorkLifeBalance": "work_life_balance",
                "YearsAtCompany": "years_at_company", "YearsInCurrentRole": "years_in_current_role",
                "YearsSinceLastPromotion": "years_since_last_promotion",
                "YearsWithCurrManager": "years_with_curr_manager"}
        ibm = self._src[list(cols)].assign(Attrition=(self._src.Attrition == "Yes").astype(int)).corr()
        syn = (self._d.employees[list(cols.values())].set_axis(list(cols), axis=1)
               .assign(Attrition=(self._d.employees.attrition == "Yes").astype(int)).corr())
        diff = (ibm - syn).abs().to_numpy()[np.triu_indices(len(ibm), 1)]
        return float(diff.max()), float(diff.mean())

    def history_stats(self) -> dict:
        h, e, r, s, a, p = (self._d.job_history, self._d.employees, self._d.reviews, self._d.surveys,
                            self._d.assignments, self._d.projects)
        versions = h.groupby("emp_idx").size()
        promos = h.change_reason == "Promotion"
        open_v = pd.isna(h.effective_end_date)
        recent = h.effective_start_date >= pd.Timestamp(f"{self._cfg.first_cycle_year}-01-01")
        completed = p[p.status == "Completed"]
        delayed = (completed.actual_end_date > completed.planned_end_date).mean() * 100 if len(completed) else 0
        # concurrent load: when an assignment starts, the sum of the employee's allocations active
        # on that same date; an employee is over-allocated if that ever exceeds 100 %
        aa = a[["emp_idx", "allocation_pct", "start_date", "end_date"]].reset_index(names="aid")
        pair = aa.merge(aa, on="emp_idx", suffixes=("", "_o"))
        o_end = pair.end_date_o.fillna(pd.Timestamp("2099-12-31"))
        pair = pair[(pair.start_date_o <= pair.start_date) & (o_end >= pair.start_date)]
        concurrent = pair.groupby("aid").allocation_pct_o.sum()
        over_emp = a.emp_idx.to_numpy()[concurrent[concurrent > 100].index]
        over_share = len(np.unique(over_emp)) / max(a.emp_idx.nunique(), 1) * 100
        return {
            "versions per employee (mean)": round(float(versions.mean()), 2),
            "versions per employee (distribution)": versions.clip(upper=6).value_counts().sort_index()
                                                    .rename(index={6: "6+"}).to_dict(),
            "change reasons": h.change_reason.value_counts().to_dict(),
            "promotions that are pay-grade only (D18)": f"{h.pay_grade_promotion.sum() / max(promos.sum(), 1) * 100:.1f} %",
            "added career-step promotions (D24)": int(h.career_step_promotion.sum()),
            "current versions with a manager": f"{(h[open_v].manager_idx >= 0).mean() * 100:.2f} %",
            "versions since 2019 with a manager": f"{(h[recent].manager_idx >= 0).mean() * 100:.2f} %",
            "all versions with a manager": f"{(h.manager_idx >= 0).mean() * 100:.2f} %",
            "manager segments left unassigned (too early in company history)": int(self._extras["unassigned_segments"]),
            "department heads": self._extras["department_heads"],
            "reviews per cycle year": r.year.value_counts().sort_index().to_dict(),
            "rating distribution (all reviews)": (r.rating.value_counts(normalize=True).sort_index() * 100).round(1).to_dict(),
            "outstanding ratings per cycle year (%)": (r.groupby("year").rating.apply(lambda x: (x == 4).mean())
                                                       * 100).round(1).to_dict(),
            "overtime per cycle year (%)": (r.groupby("year").overtime.mean() * 100).round(1).to_dict(),
            "mean KPI score per cycle year": r.groupby("year").kpi_score.mean().round(2).to_dict(),
            "mean job satisfaction per survey year": s.groupby("year").job_satisfaction.mean().round(2).to_dict(),
            "KPI scores exactly at a band limit (D27)": f"{self._kpi_at_band_limit() * 100:.2f} %",
            "surveys per year": s.year.value_counts().sort_index().to_dict(),
            "projects by status": p.status.value_counts().to_dict(),
            "completed projects finishing late": f"{delayed:.1f} %",
            "assignments per employee (mean, employees with any)": round(float(a.groupby("emp_idx").size().mean()), 2),
            "employees ever over-allocated (>100 % concurrently)": f"{over_share:.1f} %",
            "reviews without a project (-> 'No Project' member)": f"{r.project_code.isna().mean() * 100:.1f} %",
        }

    # ================================================================== report

    def to_markdown(self, counts: dict, timings: dict, manifest: dict) -> str:
        cfg = self._cfg
        lines = [
            "# Synthesis Report",
            "",
            f"Generated {datetime.now():%Y-%m-%d %H:%M} by `python -m src.synthesizer.run` — "
            f"{cfg.n_employees:,} employees, seed {cfg.seed}, snapshot {cfg.snapshot_date}.",
            "Design and decisions: [`synthesizer_design.md`](synthesizer_design.md).",
            "",
            "## 1. Volume",
            "",
            "| Staging table | Rows |",
            "|---|---:|",
            *[f"| `{t}` | {n:,} |" for t, n in counts.items()],
            f"| **Total** | **{sum(counts.values()):,}** |",
            "",
            "## 2. Performance",
            "",
            "| Step | Seconds |",
            "|---|---:|",
            *[f"| {k} | {v:.1f} |" for k, v in timings.items()],
            "",
            "## 3. Rule checks (every business rule — violations must be 0)",
            "",
            "| Area | Rule | Violations |",
            "|---|---|---:|",
            *[f"| {a} | {r} | {v} |" for a, r, v in self.checks],
            f"| **All** | **{len(self.checks)} rules** | **{self.total_violations}** |",
            "",
            "## 4. Fidelity — IBM tenure values reconstructed from the generated history",
            "",
            "| IBM column | Reproduced exactly |",
            "|---|---:|",
            *[f"| {c} | {v:.2f} % |" for c, v in self.fidelity],
            "",
            "PercentSalaryHike counts a match within ±1 percentage point (salaries are whole numbers and "
            "earlier salaries are clipped into their level's band).",
            "",
            "## 5. Realism — synthetic vs IBM source",
            "",
        ]
        cmax, cmean = self.correlation_difference()
        lines += [f"**Correlation matrix** (20 numeric columns + attrition): largest absolute difference "
                  f"{cmax:.3f}, mean absolute difference {cmean:.3f}.", ""]
        for title, table in self.comparisons().items():
            lines += [f"### {title}", "", markdown_table(table), ""]
        lines += ["## 6. History, reviews, surveys and projects", "", "| Measure | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in self.history_stats().items()]
        lines += ["", "## 7. Planted data-quality issues (decision D21)", "",
                  "These are deliberately present in staging; the staging → OLTP ETL must fix every one.", "",
                  "| Issue | Count |", "|---|---|"]
        for k, v in manifest.items():
            lines.append(f"| {k} | {v} |")
        return "\n".join(lines) + "\n"

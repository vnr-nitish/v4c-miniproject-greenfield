"""ProjectGenerator and AssignmentGenerator (synthesizer_design.md §6)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from faker import Faker

from src.synthesizer.base import BaseGenerator
from src.synthesizer.reference import DEPARTMENT_ROLES, PROJECT_ROLES

PRIORITIES = ["Low", "Medium", "High", "Critical"]
PRIORITY_P = [0.20, 0.40, 0.30, 0.10]
PRIORITY_BUDGET = {"Low": 200_000, "Medium": 500_000, "High": 1_200_000, "Critical": 2_500_000}
ALLOCATIONS = np.array([20, 30, 40, 50, 60, 70, 80, 90, 100])
ALLOCATION_P = np.array([0.08, 0.10, 0.16, 0.20, 0.16, 0.10, 0.08, 0.05, 0.07])


class ProjectGenerator(BaseGenerator):

    def generate(self, emp: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config
        n = cfg.n_projects
        rng = self._rng
        snapshot = np.datetime64(cfg.snapshot_date, "D")
        # project jargon reads naturally in en_US; a separately seeded Faker keeps it reproducible
        text = Faker("en_US")
        text.seed_instance(cfg.seed)

        share = emp.department.value_counts(normalize=True)
        department = rng.choice(share.index.to_numpy(), size=n, p=share.to_numpy())
        start = self.random_dates(np.full(n, np.datetime64(cfg.project_start_from, "D")),
                                  np.full(n, np.datetime64("2026-09-30", "D")))
        planned_end = start + rng.integers(90, 731, n).astype("timedelta64[D]")
        priority = rng.choice(PRIORITIES, size=n, p=PRIORITY_P)

        status = np.empty(n, dtype=object)
        actual_end = np.full(n, np.datetime64("NaT"), dtype="datetime64[D]")
        future = start > snapshot
        status[future] = "Planned"
        due = ~future & (planned_end <= snapshot)
        running = ~future & ~due
        status[running] = np.where(rng.random(running.sum()) < 0.08, "On Hold", "Active")

        cancelled = due & (rng.random(n) < 0.06)
        span = (planned_end - start).astype(int)
        actual_end[cancelled] = start[cancelled] + (rng.random(cancelled.sum()) * span[cancelled]).astype(int).astype("timedelta64[D]")
        status[cancelled] = "Cancelled"
        finishing = due & ~cancelled
        late = rng.random(n) < cfg.late_project_share
        delay = np.where(late, rng.integers(1, 121, n), rng.integers(-30, 1, n))
        proposed = planned_end + delay.astype("timedelta64[D]")
        proposed = np.maximum(proposed, start)
        done = finishing & (proposed <= snapshot)
        actual_end[done] = proposed[done]
        status[done] = "Completed"
        status[finishing & ~done] = "Active"            # past its planned end and still running: late

        duration_years = np.maximum(span, 90) / 365
        budget = np.array([PRIORITY_BUDGET[p] for p in priority]) * duration_years * rng.lognormal(0, 0.35, n)

        projects = pd.DataFrame({
            "project_code": [f"PRJ{k:05d}" for k in range(1, n + 1)],
            "project_name": [text.catch_phrase().title()[:120] for _ in range(n)],
            "description": [(text.bs().capitalize() + ".")[:500] for _ in range(n)],
            "department": department,
            "start_date": start,
            "planned_end_date": planned_end,
            "actual_end_date": actual_end,
            "budget": np.round(budget, -3),
            "status": status,
            "priority": priority,
        })
        projects["project_manager_code"] = self._managers(emp, history, projects)
        return projects

    def _managers(self, emp: pd.DataFrame, history: pd.DataFrame, projects: pd.DataFrame) -> np.ndarray:
        """A level-3+ member of the owning department, employed when the project starts."""
        vs = history.effective_start_date.to_numpy().astype("datetime64[D]")
        ve = history.effective_end_date.to_numpy().astype("datetime64[D]")
        ve = np.where(np.isnat(ve), np.datetime64("9999-12-31"), ve)
        emp_exit = emp.exit_date.to_numpy().astype("datetime64[D]")
        codes = emp.employee_code.to_numpy()
        pools = {d: np.flatnonzero((history.department.to_numpy() == d) & (history.job_level.to_numpy() >= 3))
                 for d in DEPARTMENT_ROLES}
        result = np.empty(len(projects), dtype=object)
        for k, (d, s) in enumerate(zip(projects.department, projects.start_date.to_numpy().astype("datetime64[D]"))):
            pool = pools[d]
            e = history.emp_idx.to_numpy()[pool]
            ok = (vs[pool] <= s) & (ve[pool] > s) & (np.isnat(emp_exit[e]) | (emp_exit[e] > s))
            hit = pool[ok]
            result[k] = codes[history.emp_idx.to_numpy()[hit[self._rng.integers(len(hit))]]] if len(hit) else None
        return result


class AssignmentGenerator(BaseGenerator):

    def generate(self, emp: pd.DataFrame, history: pd.DataFrame, projects: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config
        rng = self._rng
        snapshot = np.datetime64(cfg.snapshot_date, "D")
        window_start = np.datetime64(cfg.project_start_from, "D")
        hire = emp.hire_date.to_numpy().astype("datetime64[D]")
        exit_ = emp.exit_date.to_numpy().astype("datetime64[D]")
        lo = np.maximum(hire, window_start)
        hi = np.where(np.isnat(exit_), snapshot, np.minimum(exit_, snapshot))

        # one anchor date per employment year in the project era (92 % of years staffed on a
        # project), plus occasionally a second concurrent project
        years = np.arange(window_start.astype("datetime64[Y]").astype(int) + 1970, cfg.snapshot_date.year + 1)
        grid_emp = np.repeat(np.arange(len(emp)), len(years))
        y0 = np.tile([np.datetime64(f"{y}-01-01", "D") for y in years], len(emp))
        y1 = np.tile([np.datetime64(f"{y}-12-31", "D") for y in years], len(emp))
        a_lo, a_hi = np.maximum(y0, lo[grid_emp]), np.minimum(y1, hi[grid_emp])
        employed = a_lo <= a_hi
        staffed = employed & (rng.random(len(grid_emp)) < 0.92)
        second = staffed & (rng.random(len(grid_emp)) < 0.25)
        pick = np.concatenate([np.flatnonzero(staffed), np.flatnonzero(second)])
        is_second = np.concatenate([np.zeros(staffed.sum(), bool), np.ones(second.sum(), bool)])
        emp_idx = grid_emp[pick]
        anchor = self.random_dates(a_lo[pick], a_hi[pick])

        # department on the anchor date, from the job history
        h = history[["emp_idx", "effective_start_date", "department", "job_level"]].copy()
        h["effective_start_date"] = h.effective_start_date.astype("datetime64[s]")
        q = pd.DataFrame({"emp_idx": emp_idx, "anchor": anchor.astype("datetime64[s]"), "_o": np.arange(len(emp_idx)),
                          "second": is_second})
        q = pd.merge_asof(q.sort_values("anchor"), h.sort_values("effective_start_date"), left_on="anchor",
                          right_on="effective_start_date", by="emp_idx", direction="backward").sort_values("_o")

        # Dates as integer day numbers and projects pre-bucketed by (department, month): the loop
        # below then needs only cheap Python operations for ~700,000 anchor dates.
        epoch, big = np.datetime64("1970-01-01", "D"), 10 ** 7
        to_days = lambda x: np.where(np.isnat(x), big, (x - epoch).astype(np.int64))   # noqa: E731
        p_start = to_days(projects.start_date.to_numpy().astype("datetime64[D]")).tolist()
        p_actual = to_days(projects.actual_end_date.to_numpy().astype("datetime64[D]"))
        p_open = np.where(p_actual == big, (snapshot - epoch).astype(np.int64), p_actual).tolist()
        p_actual = p_actual.tolist()
        hire_d, exit_d = to_days(hire).tolist(), to_days(exit_).tolist()

        p_month0 = projects.start_date.to_numpy().astype("datetime64[M]").astype(np.int64)
        p_month1 = (np.array(p_open, dtype="int64").astype("datetime64[D]").astype("datetime64[M]")).astype(np.int64)
        buckets: dict[tuple, list] = {}
        for p, (dep, m0, m1) in enumerate(zip(projects.department, p_month0, p_month1)):
            for m in range(m0, m1 + 1):
                buckets.setdefault((dep, m), []).append(p)

        # each employee's anchors in date order, so "already on a project?" is decided chronologically
        q = q.sort_values(["emp_idx", "anchor"], kind="stable", ignore_index=True)
        n = len(q)
        anchors = q.anchor.to_numpy().astype("datetime64[D]")
        a_days = (anchors - epoch).astype(np.int64).tolist()
        a_month = anchors.astype("datetime64[M]").astype(np.int64).tolist()
        u_try = rng.random((n, 8)).tolist()
        u_lead, u_role, u_over, u_alloc = (rng.random(n).tolist() for _ in range(4))
        # allocation options per remaining capacity (loads are multiples of 10)
        alloc_cdf = {}
        for cap in range(-100, 101, 10):
            ok = ALLOCATIONS[ALLOCATIONS <= cap]
            w = ALLOCATION_P[ALLOCATIONS <= cap]
            alloc_cdf[cap] = (ok.tolist(), (w.cumsum() / w.sum()).tolist() if len(ok) else [])
        full = (ALLOCATIONS.tolist(), (ALLOCATION_P.cumsum() / ALLOCATION_P.sum()).tolist())

        rows = []
        taken: dict[int, list] = {}          # employee -> [(project, start, end, allocation)]
        for k, (e, d, lvl, second_anchor) in enumerate(zip(q.emp_idx.tolist(), q.department.tolist(),
                                                           q.job_level.tolist(), q.second.tolist())):
            a = a_days[k]
            mine = taken.setdefault(e, [])
            load, busy = 0, False
            for _, s0, e0, al in mine:
                if s0 <= a <= e0:
                    busy, load = True, load + al
            # a yearly anchor only staffs someone who is not on a project yet; a "second project"
            # anchor adds a concurrent project within the remaining capacity
            if busy and (not second_anchor or load >= 100):
                continue
            cands = buckets.get((d, a_month[k]))
            if not cands:
                continue
            mine_projects = {x[0] for x in mine}
            p = -1
            for u in u_try[k]:
                c = cands[int(u * len(cands))]
                if p_start[c] <= a <= p_open[c] and c not in mine_projects:
                    p = c
                    break
            if p < 0:
                continue
            start = max(p_start[p], hire_d[e], a)        # people join a running project when assigned
            end = min(p_actual[p], exit_d[e])
            role = "Project Lead" if lvl >= 3 and u_lead[k] < 0.30 else \
                PROJECT_ROLES[1 + int(u_role[k] * (len(PROJECT_ROLES) - 1))]
            # respect remaining capacity; ~5 % of the time an over-allocation slips through
            options, cdf = alloc_cdf[100 - load] if u_over[k] >= 0.05 else full
            if not options:
                continue
            allocation = options[min(int(np.searchsorted(cdf, u_alloc[k])), len(options) - 1)]
            mine.append((p, start, end, allocation))
            rows.append((e, p, role, allocation, start, end))

        a = pd.DataFrame(rows, columns=["emp_idx", "project_idx", "project_role", "allocation_pct",
                                        "start_date", "end_date"])
        a["start_date"] = (epoch + a.start_date.to_numpy().astype("timedelta64[D]")).astype("datetime64[s]")
        end_days = a.end_date.to_numpy()
        a["end_date"] = np.where(end_days >= big, np.datetime64("NaT"),
                                 epoch + np.minimum(end_days, big - 1).astype("timedelta64[D]")).astype("datetime64[s]")
        a = a.drop_duplicates(["emp_idx", "project_idx"], ignore_index=True)
        a["employee_code"] = emp.employee_code.to_numpy()[a.emp_idx]
        a["project_code"] = projects.project_code.to_numpy()[a.project_idx]
        return a.sort_values(["emp_idx", "start_date"], ignore_index=True)

    @staticmethod
    def primary_projects(reviews: pd.DataFrame, assignments: pd.DataFrame) -> np.ndarray:
        """A review's project: the employee's highest-allocation assignment overlapping that year."""
        r = reviews[["emp_idx", "year"]].copy()
        r["_r"] = np.arange(len(r))
        m = r.merge(assignments[["emp_idx", "project_code", "allocation_pct", "start_date", "end_date"]], on="emp_idx")
        y_start = pd.to_datetime(m.year.astype(str) + "-01-01")
        y_end = pd.to_datetime(m.year.astype(str) + "-12-31")
        end = m.end_date.fillna(pd.Timestamp("2099-12-31"))
        m = m[(m.start_date <= y_end) & (end >= y_start)]
        best = m.sort_values(["_r", "allocation_pct", "start_date"], ascending=[True, False, True]).drop_duplicates("_r")
        out = np.full(len(reviews), None, dtype=object)
        out[best._r.to_numpy()] = best.project_code.to_numpy()
        return out

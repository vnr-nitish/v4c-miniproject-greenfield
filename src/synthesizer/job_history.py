"""JobHistoryGenerator — engineers the SCD Type 2 history from IBM tenure columns.

Each employee's IBM row is their CURRENT state. YearsSinceLastPromotion, YearsInCurrentRole and
YearsWithCurrManager say when the last promotion, role change and manager change happened. Those
become dated events, and the history is built BACKWARDS from the current state
(docs/synthesizer_design.md section 5).

Business rules every generated version satisfies (the same rules the OLTP schema enforces):
valid department-role pair, level inside the role's range, salary inside the level's band,
contiguous half-open periods, exactly one open version per employee.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.synthesizer.base import BaseGenerator
from src.synthesizer.reference import DEPARTMENT_ROLES, JOB_ROLES, band, roles_valid_at

FAR_FUTURE = np.datetime64("9999-12-31", "D")
DEPARTMENTS = list(DEPARTMENT_ROLES)


def plausible_hire_level(prior_experience_years: int) -> int:
    """Highest level a new hire plausibly joins at, given experience before joining (decision D24).
    Level 2 is allowed for fresh hires because three roles start at level 2 (finding F2)."""
    if prior_experience_years <= 6:
        return 2
    if prior_experience_years <= 11:
        return 3
    if prior_experience_years <= 17:
        return 4
    return 5


def pick_previous_role(rng: np.random.Generator, options: list[tuple[str, str]]) -> tuple[str, str]:
    """Choose the role held before a role change. Careers mostly move out of roles that also exist
    at junior levels, so candidates are weighted by 1 / (role's minimum level)^2 — e.g. a level-3
    Healthcare Representative most likely came from Research Scientist or Laboratory Technician."""
    weights = np.array([1.0 / JOB_ROLES[r][0] ** 2 for _, r in options])
    return options[rng.choice(len(options), p=weights / weights.sum())]


class JobHistoryGenerator(BaseGenerator):

    def generate(self, employees: pd.DataFrame) -> pd.DataFrame:
        events = self._event_dates(employees)
        history = self._build_versions(employees, events)
        self._assign_managers(employees, history)
        return history

    # ------------------------------------------------------------------ 1. event dates

    def _event_dates(self, emp: pd.DataFrame) -> dict[str, np.ndarray]:
        """Promotion / role-change / manager-change dates; NaT when the event never happened."""
        n = len(emp)
        ref = emp.ref_date.to_numpy().astype("datetime64[D]")
        yac = emp.years_at_company.to_numpy()
        years = {"role": emp.years_in_current_role.to_numpy(),
                 "promo": emp.years_since_last_promotion.to_numpy(),
                 "mgr": emp.years_with_curr_manager.to_numpy()}
        offset = {k: self._rng.integers(0, 360, n) for k in years}
        # events in the same year share one date (they become one version)
        offset["promo"] = np.where(years["promo"] == years["role"], offset["role"], offset["promo"])
        offset["mgr"] = np.where(years["mgr"] == years["role"], offset["role"],
                                 np.where(years["mgr"] == years["promo"], offset["promo"], offset["mgr"]))
        dates = {}
        for k, y in years.items():
            d = self.years_before(ref, y, offset[k])
            dates[k] = np.where(y < yac, d, np.datetime64("NaT"))   # y == YearsAtCompany: since hire
        return dates

    # ------------------------------------------------------------------ 2. versions, built backwards

    def _build_versions(self, emp: pd.DataFrame, events: dict[str, np.ndarray]) -> pd.DataFrame:
        rng = self._rng
        transfer_share = self.config.transfer_share
        rows = []
        cols = ["department", "job_role", "job_level", "monthly_income", "stock_option_level",
                "business_travel", "hire_date", "percent_salary_hike"]
        data = {c: emp[c].to_numpy() for c in cols}
        data["prior_experience"] = (emp.total_working_years - emp.years_at_company).to_numpy()
        data["years_in_current_role"] = emp.years_in_current_role.to_numpy()
        data["years_with_curr_manager"] = emp.years_with_curr_manager.to_numpy()
        data["years_at_company"] = emp.years_at_company.to_numpy()
        exit_or_end = emp.exit_date.to_numpy().astype("datetime64[D]")

        for i in range(len(emp)):
            dept, role, level = data["department"][i], data["job_role"][i], int(data["job_level"][i])
            income = float(data["monthly_income"][i])
            # distinct event dates, most recent first, with the event types happening on each
            on_date: dict[np.datetime64, set] = {}
            for kind in ("promo", "role", "mgr"):
                d = events[kind][i]
                if not np.isnat(d):
                    on_date.setdefault(d, set()).add(kind)
            salary_event_seen = False
            backwards = []          # (start, dept, role, level, income, reason, manager_boundary, pay_grade)

            for d in sorted(on_date, reverse=True):
                kinds = on_date[d]
                promo, role_chg = "promo" in kinds, "role" in kinds
                prev_level = level
                pay_grade = False
                if promo and level > 1:
                    if role_chg or level - 1 >= JOB_ROLES[role][0]:
                        prev_level = level - 1
                    else:
                        pay_grade = True        # decision D18: salary rises, level does not
                elif promo:
                    pay_grade = True            # level 1 cannot fall further (finding F8)

                prev_dept, prev_role = dept, role
                if role_chg:
                    same_dept = [(dept, r) for r in roles_valid_at(dept, prev_level) if r != role]
                    # A transfer always brings a new manager, so it is only consistent with IBM when the
                    # current manager started at or after this role change (YearsWithCurrManager <=
                    # YearsInCurrentRole). It is forced when no other role exists in the department.
                    transfer_ok = data["years_with_curr_manager"][i] <= data["years_in_current_role"][i]
                    # transfer options: another department, and the role must change too (otherwise
                    # YearsInCurrentRole would not be reproducible). A level-5 R&D Manager, for example,
                    # has none: every other department's level-5 role is also Manager.
                    transfers = [(dp, r) for dp in DEPARTMENTS if dp != dept
                                 for r in roles_valid_at(dp, prev_level) if r != role]
                    wants_transfer = not same_dept or (transfer_ok and rng.random() < transfer_share)
                    if wants_transfer and transfers:
                        prev_dept, prev_role = pick_previous_role(rng, transfers)
                    elif same_dept:
                        prev_dept, prev_role = pick_previous_role(rng, same_dept)

                changes_salary = promo or role_chg
                if changes_salary:
                    if not salary_event_seen:
                        hike = float(data["percent_salary_hike"][i])          # IBM's last hike
                    elif promo:
                        hike = rng.uniform(11, 25)
                    elif prev_dept != dept:
                        hike = rng.uniform(0, 5)
                    else:
                        hike = rng.uniform(0, 8)
                    salary_event_seen = True
                    low, high = band(prev_level)
                    prev_income = float(np.clip(round(income / (1 + hike / 100)), low, high))
                else:
                    prev_income = income

                # "Promotion" wins over "Transfer": the last promotion is found by its reason, while a
                # transfer remains visible as a department change.
                if promo:
                    reason = "Promotion"
                elif prev_dept != dept:
                    reason = "Transfer"
                elif prev_role != role:
                    reason = "Role Change"
                else:
                    reason = "Manager Change"
                boundary = ("mgr" in kinds) or prev_dept != dept
                backwards.append((d, dept, role, level, income, reason, boundary, pay_grade, False))
                dept, role, level, income = prev_dept, prev_role, prev_level, prev_income

            # decision D24 — career depth: IBM fixes only the LAST promotion's date, so a long career
            # can contain earlier promotions. If the hire-era level is implausibly senior for the
            # experience the person had when hired, add earlier promotions between the hire date and
            # the first IBM-dated event (never touching any IBM tenure value).
            hire_date = np.datetime64(data["hire_date"][i], "D")
            ever_promoted = not np.isnat(events["promo"][i])
            target = plausible_hire_level(int(data["prior_experience"][i]))
            # IBM pins the role only if the CURRENT role has been held since hire; any earlier role
            # was chosen by this generator and may change at an added promotion (a career step).
            pinned = role == data["job_role"][i] and data["years_in_current_role"][i] == data["years_at_company"][i]
            floor = max(target, JOB_ROLES[role][0]) if pinned else target
            if ever_promoted and level > floor:
                horizon = min(on_date) if on_date else np.datetime64(self.config.snapshot_date, "D")
                room = int((horizon - hire_date).astype(int) // 365) - 1          # whole years available
                extra = min(level - floor, max(room, 0))
                if extra > 0:
                    step = (horizon - hire_date).astype(int) // (extra + 1)
                    for j in range(extra, 0, -1):                                # latest extra first
                        d = hire_date + np.timedelta64(int(step * j + rng.integers(-30, 31)), "D")
                        prev_level, prev_role = level - 1, role
                        if prev_level < JOB_ROLES[role][0]:                      # step up from a junior role
                            _, prev_role = pick_previous_role(rng, [(dept, r) for r in roles_valid_at(dept, prev_level)])
                        low, high = band(prev_level)
                        prev_income = float(np.clip(round(income / (1 + rng.uniform(11, 25) / 100)), low, high))
                        backwards.append((d, dept, role, level, income, "Promotion", False, False, True))
                        role, level, income = prev_role, prev_level, prev_income

            backwards.append((data["hire_date"][i], dept, role, level, income, "Hire", True, False, False))
            versions = backwards[::-1]
            for v, (start, dp, rl, lv, inc, reason, boundary, pay_grade, career_step) in enumerate(versions):
                end = versions[v + 1][0] if v + 1 < len(versions) else np.datetime64("NaT")
                rows.append((i, v + 1, dp, rl, lv, inc, reason, start, end, boundary, pay_grade, career_step))

        hist = pd.DataFrame(rows, columns=["emp_idx", "version_number", "department", "job_role", "job_level",
                                           "monthly_income", "change_reason", "effective_start_date",
                                           "effective_end_date", "manager_boundary", "pay_grade_promotion",
                                           "career_step_promotion"])
        hist["effective_start_date"] = hist.effective_start_date.astype("datetime64[s]")
        hist["effective_end_date"] = hist.effective_end_date.astype("datetime64[s]")
        hist["employee_code"] = emp.employee_code.to_numpy()[hist.emp_idx]
        hist["stock_option_level"] = data["stock_option_level"][hist.emp_idx]
        hist["business_travel"] = data["business_travel"][hist.emp_idx]
        hist["employment_end"] = exit_or_end[hist.emp_idx]
        return hist

    # ------------------------------------------------------------------ 3. org chart

    def _assign_managers(self, emp: pd.DataFrame, hist: pd.DataFrame) -> None:
        """Each manager segment gets a manager from the same department, with a higher level, who is
        employed for the whole segment. Level-5 staff report to an earlier-hired level-5 colleague
        (normally the department head), which rules out reporting cycles."""
        rng = self._rng
        snapshot = np.datetime64(self.config.snapshot_date, "D")
        hire = emp.hire_date.to_numpy().astype("datetime64[D]")
        emp_end = np.where(np.isnat(emp.exit_date.to_numpy().astype("datetime64[D]")), FAR_FUTURE,
                           emp.exit_date.to_numpy().astype("datetime64[D]"))

        # segments: consecutive versions between manager boundaries
        hist["segment"] = hist.manager_boundary.astype(int).groupby(hist.emp_idx).cumsum()
        seg = (hist.groupby(["emp_idx", "segment"], sort=False)
                   .agg(department=("department", "first"), start=("effective_start_date", "min"),
                        max_level=("job_level", "max"))
                   .reset_index())
        seg["start"] = seg.start.to_numpy().astype("datetime64[D]")
        nxt = seg.groupby("emp_idx").start.shift(-1).to_numpy().astype("datetime64[D]")
        own_end = np.minimum(emp_end[seg.emp_idx], snapshot)
        seg["end"] = np.where(np.isnat(nxt), own_end, nxt)

        # candidate manager versions, per department
        vs = hist.effective_start_date.to_numpy().astype("datetime64[D]")
        ve = hist.effective_end_date.to_numpy().astype("datetime64[D]")
        ve = np.where(np.isnat(ve), FAR_FUTURE, ve)
        c_emp = hist.emp_idx.to_numpy()
        c_lvl = hist.job_level.to_numpy()
        pools = {}
        for d in DEPARTMENTS:
            in_dept = np.flatnonzero((hist.department.to_numpy() == d) & (c_lvl >= 2))
            pools[d] = {need: in_dept[c_lvl[in_dept] > need] for need in range(1, 5)}
            pools[d][5] = in_dept[c_lvl[in_dept] == 5]

        # department heads: earliest-hired level-5 employee still employed at the snapshot
        current = hist[hist.effective_end_date.isna()]
        heads = {}
        for d in DEPARTMENTS:
            cand = current[(current.department == d) & (current.job_level == 5)
                           & np.isnat(emp.exit_date.to_numpy().astype("datetime64[D]")[current.emp_idx])]
            if len(cand):
                heads[d] = int(cand.emp_idx.to_numpy()[np.argmin(hire[cand.emp_idx])])

        manager = np.full(len(seg), -1, dtype=np.int64)
        unassigned = 0
        seg_emp = seg.emp_idx.to_numpy()
        for k, (e, d, s, en, need) in enumerate(zip(seg_emp, seg.department, seg.start, seg.end, seg.max_level)):
            # a new segment means a manager change: never pick the previous manager again
            prev_mgr = manager[k - 1] if k > 0 and seg_emp[k - 1] == e else -2
            if heads.get(d) == e and need == 5:
                continue                                    # the department head has no manager
            pool = pools[d][min(int(need), 5)]
            if len(pool) == 0:
                unassigned += 1
                continue

            def ok(idx: np.ndarray) -> np.ndarray:
                good = (vs[idx] <= s) & (ve[idx] > s) & (c_emp[idx] != e) & (c_emp[idx] != prev_mgr)
                good &= emp_end[c_emp[idx]] >= en           # employed for the whole segment
                if need == 5:
                    good &= hire[c_emp[idx]] < hire[e]        # senior level-5 colleague only
                return good

            if need == 5 and d in heads and heads[d] != e and hire[heads[d]] < hire[e]:
                head_rows = pool[c_emp[pool] == heads[d]]
                hit = head_rows[ok(head_rows)]
                if len(hit):
                    manager[k] = c_emp[hit[0]]
                    continue
            sample = pool[rng.integers(0, len(pool), size=min(40, len(pool)))]
            hit = sample[ok(sample)]
            if not len(hit):
                hit = pool[ok(pool)]
            # no fallback to a manager who leaves part-way through: an honest "no recorded manager"
            # (early company history, few senior staff) is better than an invalid one
            if len(hit):
                manager[k] = c_emp[hit[rng.integers(len(hit))]]
            else:
                unassigned += 1

        seg["manager_idx"] = manager
        hist["manager_idx"] = (hist[["emp_idx", "segment"]]
                               .merge(seg[["emp_idx", "segment", "manager_idx"]], on=["emp_idx", "segment"],
                                      how="left").manager_idx.to_numpy())
        codes = emp.employee_code.to_numpy()
        hist["manager_code"] = np.where(hist.manager_idx >= 0, codes[np.maximum(hist.manager_idx, 0)], None)
        self.unassigned_segments = unassigned
        self.department_heads = {d: codes[i] for d, i in heads.items()}

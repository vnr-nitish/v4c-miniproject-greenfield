# Data Synthesizer Design

**Project:** Enterprise Employee Analytics & Data Warehouse System
**Phase:** 1 — Dataset sourcing and synthesizing
**Status:** Approved design
**Code:** [`src/synthesizer/`](../src/synthesizer) · **Results:** [`synthesis_report.md`](synthesis_report.md)

---

## 1. What the problem statement requires

> *"Write a Python script using the pandas and Faker libraries to ingest this base data and
> synthetically scale it up to 100,000+ rows."*
> *"Use Python to artificially generate historical records for a subset of employees … You must
> engineer this data to satisfy the SCD Type 2 requirement."*
> *"Load this massive, synthesized dataset into a staging schema in MySQL."*

Evaluation criterion: *"Ability to logically generate 100,000+ realistic rows and handle that
volume without performance degradation."*

## 2. What is produced

| Staging table | Rows (seed 42) | Content |
|---|---:|---|
| `stg_employees` | 100,500 | The scaled IBM dataset — 100,000 employees + 500 planted duplicates: all 35 IBM columns (snake_case) plus Faker identity, hire / exit dates, exit reason, and the source IBM row number |
| `stg_job_history` | 308,986 | Job versions: hire, promotion, role change, transfer, manager change (3.09 per employee) |
| `stg_surveys` | 468,646 | One engagement survey per employee per year, 2019–2025 |
| `stg_reviews` | 464,400 | One performance review per employee per year, 2019–2025 (+1,389 planted resubmissions) |
| `stg_projects` | 2,000 | Projects 2018–2026 |
| `stg_assignments` | 503,240 | Employee-to-project allocations (about one per employment year) |
| **Total** | **1,847,772** | generated + validated in ~70 s, loaded in ~93 s on a laptop |

Full figures, every rule check and the realism comparison: [`synthesis_report.md`](synthesis_report.md).

## 3. Guiding principle: the synthesizer imitates a source system

The synthesizer behaves like the legacy HR system the company is migrating from: it produces
**raw, source-like data** — text labels instead of IDs ("Research & Development", not 2), business
keys (`EMP000123`), the original IBM columns including their known quirks, and a small number of
deliberately planted data-quality issues. **All cleaning and transformation happen afterwards in
SQL** (staging → OLTP stored procedures using CTEs and window functions), exactly as the problem
statement asks. This keeps a clean separation: Python = generate; SQL = clean and transform.

---

## 4. Decisions and their rationale

### D16 — Scaling method: stratified bootstrap with bounded jitter

**How it works**
1. The 1,470 IBM rows are grouped into strata by (Attrition, Department, JobRole, JobLevel).
2. Each stratum receives exactly its proportional share of the 100,000 synthetic employees
   (largest-remainder rounding), and rows are drawn from it at random with replacement.
3. Numeric values receive small, bounded random variation ("jitter"):
   MonthlyIncome × 0.93–1.07 clipped to the level's salary band; Age ± 1 year while keeping
   Age − TotalWorkingYears ≥ 18; DistanceFromHome ± 2 km within 1–29; the noise columns
   (DailyRate, HourlyRate, MonthlyRate) ± 5 %.
4. Tenure columns are kept exactly, because they drive the job history and must stay mutually
   consistent.
5. Faker generates the identity: gender-matched first names, last names, unique e-mails, phone
   numbers, and a date of birth consistent with Age.

**Why**
- Every synthetic employee inherits a *real* combination of attributes, so the relationships
  that matter analytically survive: OverTime → attrition, JobLevel → income (correlation 0.95),
  satisfaction → attrition, tenure → attrition.
- Stratification guarantees the population mix (65 % R&D, 16 % attrition, …) matches the source
  exactly rather than approximately.
- Jitter means no two synthetic employees are identical, while values never leave valid ranges.

**Alternatives rejected**
- *Sampling each column independently* — destroys every correlation (a Sales Representative could
  earn a Director's salary).
- *Machine-learning generators (SDV, CTGAN)* — heavy extra dependencies, a black box that is hard
  to explain or validate, and not what the problem statement asks for (pandas + Faker).
- *Plain duplication × 68* — identical rows; unrealistic and trivially detectable.

### D17 — Timeline

| Item | Choice | Why |
|---|---|---|
| Snapshot ("as of") date | **2026-06-30** | The IBM row describes an employee *as of* a date; using mid-2026 means the 2025 review cycle (held early 2026) is complete |
| Review cycles | Performance years **2019–2025**, held 15 Jan – 15 Mar of the following year | Seven years gives meaningful year-over-year trends; reviews in early next year mirror real practice |
| Review eligibility | Hired on or before 30 Sep of the year **and** still employed on the review date | New joiners are not reviewed for a quarter-year; leavers are not reviewed after leaving |
| Surveys | Held 1 Sep – 31 Oct each year 2019–2025, for everyone employed that day | An annual engagement survey |
| Leavers (16 %) | Exit dates spread uniformly from 2019-01-01 to the snapshot; their IBM values describe them **at exit** | Lets dashboards show attrition by year |
| Date arithmetic | Hire date = reference date − YearsAtCompany years − (0–359 days); likewise for date of birth and every history event | Guarantees that full-year differences reproduce the IBM values exactly |

### D18 — Promotion vs role level range: pay-grade promotion

**The conflict (measured):** for **310 of 1,470 IBM employees (21.1 %)** the current role started
*before* the last promotion, and that promotion would have taken them one level *below* the
role's minimum (finding F2). Example: a level-2 Sales Executive "in role 5 years, promoted
2 years ago" would have been a level-1 Sales Executive — a combination that never occurs.
Affected: Sales Executive 166, Manufacturing Director 67, Healthcare Representative 47, Research
Director 24, Manager 6.

**Decision:** for these employees the last promotion is a **pay-grade promotion** — salary rises,
level does not.

**Why:** finding F8 already shows 440 level-1 employees who were "promoted" without any possible
level change, so pay-grade promotions are part of this dataset's reality. The choice keeps
**100 % of the IBM tenure columns intact** and **breaks no business rule**. Level-raising
promotions remain the norm where possible (541 of 1,291 promotions in the source).

**Alternatives rejected**
- *Feeder role at promotion* (e.g. Sales Representative → Sales Executive): realistic career
  ladder, but contradicts IBM's YearsInCurrentRole for 21 % of employees.
- *Widen the role ranges* (allow Sales Executive at level 1): changes approved reference data and
  weakens finding F2.

### D19 — NumCompaniesWorked cleaning rule (finding F7)

**Evidence:** every employee with NumCompaniesWorked = 0 has prior experience (median 1 year);
474 of the 521 employees with value 1 have no prior experience. The field therefore counts the
*current* employer, and 0 is a coding error.

| IBM value | `prior_companies_count` |
|---|---|
| no prior experience (TotalWorkingYears = YearsAtCompany) | 0 |
| 0 with prior experience | 1 (coding error: one previous employer) |
| 1 with prior experience (47 rows) | 1 (they must have worked somewhere before) |
| 2 – 9 | NumCompaniesWorked − 1 (current employer excluded) |

The synthesizer keeps the **raw** value (quirk included); the rule is applied in SQL during the
staging → OLTP load, so the cleaning is visible and testable (§3).

### D20 — Faker locale `en_IN`

Indian names and phone numbers, consistent with the Indian fiscal year in `dim_date` (decision D9).
Name pools are generated once with Faker and sampled with the seeded random generator, which is
fast for 100,000 rows and fully reproducible. E-mails use the reserved domain `example.com`
(never a real company) and include the employee number to guarantee uniqueness.

### D21 — Planted data-quality issues in staging

The problem statement asks for CTEs and window functions to *clean* the data; perfectly clean data
would give them nothing to do. Small, realistic issues are therefore planted, and their exact
counts are written to a manifest so the ETL tests can prove every one was fixed:

| Issue | Rate | Fixed in SQL by |
|---|---|---|
| Duplicate employee rows (record sent twice) | 0.5 % | `ROW_NUMBER() OVER (PARTITION BY employee_code …)` keep one |
| Inconsistent department / role labels (case, stray spaces, aliases such as "R&D", "HR") | 2 % | `TRIM`, case-insensitive matching, alias mapping CTE |
| Gender sent as codes ("M" / "F") | 1 % | `CASE` standardization |
| Duplicate review submissions for the same employee-year | 0.3 % | `ROW_NUMBER()` keep the latest submission |

### D22 — `db_manager.py` built now

The staging loader needs a database connection, and the problem statement requires a
`DatabaseConnection` Singleton in Phase 3. Building it now avoids a throw-away connection helper.
Design: one shared connection **pool** for the whole application; each class that inherits from
`DatabaseConnection` (the loader now, the data-access managers in Phase 3) is itself a Singleton
and draws connections from that one pool.

### D24 — Career depth: earlier promotions for long careers (added during implementation)

**The problem (measured on a 20,000-employee build):** IBM gives only three tenure anchors, so the
backward build can undo at most one promotion. Long careers therefore started too senior:
**6.1 %** of employees were hired straight into level 3+ with at most two years of prior
experience — e.g. a level-3 Manager hired at 18 in 1980.

**Decision:** IBM fixes only the date of the *last* promotion, so earlier promotions are allowed.
When the hire-era level is implausible for the experience the person had when hired, earlier
promotions are added between the hire date and the first IBM-dated event (at least a year apart).
Plausible hire level: up to 6 years of prior experience → level 2 at most (three roles start at
level 2); 7–11 → 3; 12–17 → 4; 18+ → 5. If a lower level is below the era's role minimum, the
person steps up from a junior-capable role in the same department. Roles that IBM pins since the
hire date are never changed. Previous roles at role changes are also chosen with weights
1 / (role minimum level)², because careers mostly move *out of* roles that exist at junior levels.

**Result:** implausible senior hires fell from 6.1 % to about 1 % (the remainder: roles pinned by
IBM since hire, or careers too short to fit another promotion). **No IBM tenure value changes**
(YearsSinceLastPromotion stays 100 % reproducible). 8,671 such promotions were added.

---

## 4b. Refinements made while implementing

Problems found by testing and by the validator, and how each was resolved:

| # | Problem found | Resolution |
|---|---|---|
| I1 | A promotion on the same date as a transfer was labelled "Transfer", so the last promotion could not be found by its reason | "Promotion" takes precedence; a transfer stays visible as a department change |
| I2 | A transfer always brings a new manager; when IBM says the current manager started *before* the role change, a transfer there would contradict IBM | Transfers only where `YearsWithCurrManager ≤ YearsInCurrentRole` (or when no other role exists in the department) |
| I3 | A transfer could keep the same role name (Manager → Manager), so YearsInCurrentRole could not be reproduced | Transfers must change the role; when no such transfer exists (e.g. level-5 R&D Manager), a role change within the department is used |
| I4 | At a manager-change event the random choice could pick the same manager again | The previous manager is excluded |
| I5 | A last-resort fallback accepted a manager who left part-way through a period (28 violations in a 5,000 build) | Fallback removed: early-history periods with no eligible senior colleague have no recorded manager (3.9 % of all versions; 1.1 % of current versions). Reviews then go to the department head |
| I6 | Leap-year calendar arithmetic left 12 people hired one day before their 18th birthday | For those rows the birthday is placed earliest and the hire date latest within their windows, which reproduces Age and YearsAtCompany exactly and guarantees 18 full years |
| I7 | First assignment design left 45 % of reviews without a project; a second attempt over-allocated 58 % of employees, because an assignment started on the *project's* start date and overlapped earlier assignments retroactively | One staffing anchor per employment year (92 %), plus an occasional second concurrent project; people join a project on the anchor date; each allocation respects remaining capacity, with a deliberate ~5 % chance of over-allocation. Result: 3.7 % of reviews without a project, 1.8 % of employees ever over-allocated |
| I8 | The validator's first over-allocation measure added up consecutive (not concurrent) assignments within a year | Measured as true concurrent load on each assignment's start date |
| I9 | Assignment generation took ~75 s at 100,000 employees | Projects pre-bucketed by department and month, dates as integer days, random numbers drawn in advance: 9 s |

---

## 5. How the job history is built (source of SCD Type 2)

Each employee's current state (department, role, level, income, stock options, travel) is the
IBM row. The three IBM tenure columns become dated events, generated **backwards** from the
reference date (snapshot, or exit date for leavers):

| IBM column | Event | Previous state |
|---|---|---|
| YearsSinceLastPromotion | **Promotion** | level − 1 when the role allows it, otherwise a pay-grade promotion (D18); salary ÷ (1 + hike) |
| YearsInCurrentRole | **Role change** | another valid role in the same department at that level; ~10 % (or whenever no other role exists, e.g. a level-1 Sales Representative) a **transfer** from another department |
| YearsWithCurrManager | **Manager change** | a different manager |
| YearsAtCompany | **Hire** | first version |

- Events in the same year share one date and become one version (e.g. promotion + role change).
- **Salary:** the most recent salary-changing event uses the IBM `PercentSalaryHike`; earlier ones
  draw a rating-consistent hike. Role changes add 0–8 %, transfers 0–5 %, manager changes 0 %.
  Every salary is clipped into its level's band.
- **Every version satisfies the OLTP rules:** a valid department–role pair, a level inside the
  role's range, a salary inside the level's band, contiguous periods, exactly one open version.
- **Org chart:** each employee reports to someone in the same department with a **higher level**
  who is employed for that whole period; level-5 employees report to their department head; the
  three department heads have no manager. A manager changes only at a manager-change event or a
  transfer, so IBM's YearsWithCurrManager is respected.
- **Reviewer = the manager on the review date**, so reviews and reporting lines agree.

## 6. Surveys, reviews, projects

- The **latest** survey and review of each employee carry the IBM values exactly (satisfaction
  scores, PerformanceRating, OverTime, TrainingTimesLastYear).
- **Earlier years** are generated backwards with persistence: each score stays the same or moves
  ±1; for leavers, earlier scores drift slightly upward (satisfaction declines towards exit).
- **Ratings:** earlier years keep the latest rating 60 % of the time, otherwise draw from
  {1: 2 %, 2: 8 %, 3: 72 %, 4: 18 %} — the full IBM 1–4 scale.
- **KPI score** is correlated with, but not determined by, the rating (rating 4 → 85–100,
  3 → around 78, 2 → around 60, 1 → around 42).
- **Projects:** ~2,000, split across departments by headcount; Faker names; ~40 % of finished
  projects finish late by 1–120 days (delay analysis); managers are level-3+ staff of the owning
  department.
- **Assignments:** for each employment year in the project era, 92 % of employees are staffed on a
  project (joining it on a random date that year), and 25 % of those also take a second, concurrent
  project within their remaining capacity. Allocations are 20–100 %; about 5 % of new allocations
  are allowed to exceed capacity, so 1.8 % of employees are over-allocated at some point
  (bottleneck analysis). A review's project is the employee's highest-allocation assignment that
  year; the 3.7 % of reviews without one map to the `−1` "No Project" member.
- **Headcount grows over time** (46,489 reviews in 2019 → 83,173 in 2025). This follows directly
  from IBM's tenure distribution (median 5 years at the company): most of today's workforce joined
  recently, so the company was smaller in earlier years — a growing company.

## 7. Code structure

| Module | Class | Responsibility |
|---|---|---|
| `config.py` | `SynthesizerConfig` | All parameters in one frozen dataclass |
| `reference.py` | — | Reference data mirrored from `sql/02` (a test checks they match) |
| `base.py` | `BaseGenerator` | Abstract base: seeded random generator, Faker, config, date helpers |
| `source.py` | `IBMSourceData` | Load and validate the IBM file (35 columns, no nulls, unique ids) |
| `employees.py` | `EmployeeGenerator` | Stratified bootstrap, jitter, Faker identity, dates, exit reasons |
| `job_history.py` | `JobHistoryGenerator` | Backward event build, career paths, transfers, org chart |
| `performance.py` | `SurveyGenerator`, `ReviewGenerator` | Yearly surveys and reviews |
| `projects.py` | `ProjectGenerator`, `AssignmentGenerator` | Projects and allocations |
| `quality.py` | `AnomalyInjector` | Planted issues + manifest |
| `dataset.py` | `SyntheticDataset` | Container; writes CSVs |
| `validator.py` | `SynthesisValidator` | Rule checks and IBM-vs-synthetic comparison → report |
| `loader.py` | `StagingLoader` | Batched bulk insert into `hr_staging` |
| `run.py` | — | `python -m src.synthesizer.run --employees 100000 --seed 42` |

Reproducible (fixed seed → identical output), vectorized with pandas / NumPy, intermediate CSVs
in `data/synthesized/` (git-ignored).

---

## 8. Questions you may be asked

**Q: Why not just copy the 1,470 rows 68 times?**
Identical rows are unrealistic and trivially detectable. Stratified bootstrap keeps the real
attribute combinations, and jitter makes every employee unique while staying inside valid ranges.

**Q: How do you know the synthetic data is realistic?**
`synthesis_report.md` compares it with the IBM data: department / role / level mix, attrition
overall and by overtime, level and tenure, income by level, and the correlation matrix — plus
rule checks that must all show zero violations.

**Q: Where does the history come from if the IBM data is a single snapshot?**
From IBM's own tenure columns: YearsSinceLastPromotion, YearsInCurrentRole and
YearsWithCurrManager say *when* the last promotion, role change and manager change happened.
Each becomes a dated event, built backwards from the employee's current state.

**Q: Why are some promotions "pay-grade" promotions without a level change?**
Because for 21 % of employees a level change is impossible without breaking the role's level
range, and the dataset already contains 440 such promotions at level 1 (F8). This keeps every IBM
tenure value exact and every business rule intact (D18).

**Q: Why put dirty data into staging on purpose?**
To prove the cleaning logic works. A real source system is never perfectly clean, the problem
statement asks for CTEs and window functions to clean the data, and the planted issues are
counted, so tests can verify each one was fixed (D21).

**Q: Why clean NumCompaniesWorked in SQL rather than in Python?**
Staging should look like the source system. Cleaning in the staging → OLTP procedures keeps every
transformation in one auditable place (D19, §3).

**Q: Why Indian names?**
For consistency with the Indian fiscal year used in the warehouse (D20).

**Q: How does it handle 100,000+ rows without slowing down?**
Vectorized pandas / NumPy, Faker name pools instead of 100,000 individual Faker calls, and
batched multi-row inserts of 5,000 rows. Timings are in `synthesis_report.md`.

**Q: Is the output reproducible?**
Yes — a fixed seed produces identical data on every run (a test checks this).

**Q: How faithful is the history to the IBM data?**
YearsInCurrentRole and YearsSinceLastPromotion are reproduced for 100 % of employees,
YearsWithCurrManager for 98 %, and the last salary hike within ±1 point for 92 %. The gaps come
from early company history with no eligible manager, and from salaries clipped into level bands.

**Q: Why were extra promotions added (D24)?**
With only three dated events, people with 20+ years of service would have joined as senior
managers at 18. IBM fixes only the *last* promotion's date, so adding earlier promotions is
consistent with the source and makes careers realistic.

**Q: Why do some employees have no recorded manager?**
In the company's early years (1980s–90s) there were too few senior colleagues employed for the
whole period. Rather than invent an invalid manager, those periods have none — 1.1 % of current
job versions, 0.6 % of versions since 2019. Their reviews go to the department head.

**Q: Why does headcount grow from 2019 to 2025?**
IBM's median tenure is 5 years, so most of today's employees joined recently; working backwards,
the company was smaller in earlier years.

**Q: How do you know the volume is handled efficiently?**
The report's performance table: 100,000 employees and 1.85 million rows generated and validated
in about 70 seconds and loaded in about 93 seconds on a laptop, using vectorized pandas / NumPy
and 5,000-row batched inserts.

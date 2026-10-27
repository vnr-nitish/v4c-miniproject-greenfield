# Project Journal

A chronological record of how the **Enterprise Employee Analytics & Data Warehouse System** was
built: every step, the problems met along the way, how they were resolved, and why each decision
was taken. This journal is the source for the final README and the project Google Doc.

---

## Step 0 — Project kickoff (2026-10-27)

- Created an empty working folder `Downloads/mini project` and opened it in VS Code.
- Studied the full problem statement (*Rising Stars – Greenfield Mini-Project, Version 2*, 5 pages):
  - **Goal:** migrate from disconnected legacy systems to a centralized, cloud-ready data warehouse
    for employee performance, attrition and project allocation.
  - **Users:** HR and Executive teams, through a Streamlit web application.
  - **Technologies:** Python 3 (OOP, pandas, Faker), MySQL (OLTP + OLAP), Streamlit, GitHub,
    Streamlit Community Cloud, draw.io.
  - **Six deliverables:** GitHub repository · data synthesizer script · editable ER and dimensional
    diagrams · SQL scripts (DDL, DML, procedures, CTEs, window functions) · Streamlit app ·
    README + Google Doc.
  - **Five phases:** (1) architecture, modeling, data ingestion · (2) advanced SQL & ETL ·
    (3) OOP Python backend · (4) Streamlit UI & dashboards · (5) version control & cloud.
  - **Evaluation:** data synthesis & scale · data warehousing (surrogate keys, star schema, SCD2) ·
    SQL mastery · Python architecture · presentation.
- Produced a structured plan before writing any code:
  - Three-layer database architecture: `hr_staging` → `hr_oltp` → `hr_dw`.
  - A feature-branch-per-phase Git workflow merged through pull requests.
  - A checkpoint at the end of every step, verified before moving on.
  - **Working principle:** go phase by phase; nothing proceeds until the current step is verified.

## Step 1 — Prerequisites audit

An audit of the machine found:

| Item | Finding | Action |
|---|---|---|
| Project folder | Empty | — |
| Python | 3.11.6 with pandas, streamlit, altair | Use a dedicated virtual environment |
| Faker, plotly, MySQL driver | Missing | Install in the venv (Step 2) |
| MySQL Server | Not installed (Workbench is only a client) | Installed **MySQL Community Server 8.0.46** — Windows service `MySQL80`, port 3306, auto-start |
| Dataset | Not present | Downloaded `WA_Fn-UseC_-HR-Employee-Attrition.csv` from Kaggle |
| GitHub repository | — | Created `vnr-nitish/v4c-miniproject-greenfield` |
| Git | The whole user home folder was already a Git repository | Project needs its own repository (Step 3) |
| GitHub CLI | Not installed | Not required — pull requests are opened on github.com |

**Dataset verification:** 1,470 rows × 35 columns, 0 nulls, 0 duplicate rows, `EmployeeNumber`
unique. Early observations: `EmployeeCount`, `Over18`, `StandardHours` are constant;
`PerformanceRating` only takes values 3 and 4; attrition is 16 % (237 Yes / 1,233 No);
`YearsAtCompany` reaches 40.

## Step 2 — Python virtual environment

```
python -m venv .venv
.venv\Scripts\pip install faker plotly mysql-connector-python python-dotenv pytest streamlit pandas altair
```

Verified versions: Faker 40.43.0 · plotly 7.1.0 · mysql-connector-python 26.10.0 ·
streamlit 1.66.1 · pandas 3.1.0 · numpy 2.4.6 · altair 6.3.0 · python-dotenv 1.2.4 · pytest 9.1.1.
**Why a venv:** isolates project dependencies from the global Python and makes
`requirements.txt` reproducible for Streamlit Community Cloud.

## Step 3 — Git repository

- Initialized a repository inside the project folder (separate from the home-folder repository),
  linked it to GitHub and pulled the existing `main` (initial README).
- Added `.gitignore` excluding `.venv/`, `.env`, `.streamlit/secrets.toml`, generated data and IDE files.
- Added `.env.example` as the credential template.

## Step 4 — Dedicated MySQL application user

**Why not root:** principle of least privilege — the application gets only the rights it needs,
and the root password is never written into the project.

```sql
CREATE USER 'hr_app'@'localhost' IDENTIFIED BY '********';
GRANT ALL PRIVILEGES ON hr_staging.* TO 'hr_app'@'localhost';
GRANT ALL PRIVILEGES ON hr_oltp.*    TO 'hr_app'@'localhost';
GRANT ALL PRIVILEGES ON hr_dw.*      TO 'hr_app'@'localhost';
GRANT CREATE ON *.* TO 'hr_app'@'localhost';
FLUSH PRIVILEGES;
```

**Problem:** re-running `CREATE USER` with a new password failed with
*Error 1396 — Operation CREATE USER failed*.
**Cause:** the first run had already created the user (the following GRANTs succeeded, which is
only possible for an existing user).
**Fix:** `ALTER USER 'hr_app'@'localhost' IDENTIFIED BY '...'`; verified with
`SHOW GRANTS FOR 'hr_app'@'localhost'` (4 grants).
The password is stored only in the local, git-ignored `.env` file.

## Step 5 — Connection test and server settings

A Python test connected as `hr_app` and confirmed:

| Check | Result |
|---|---|
| Connection | MySQL 8.0.46, user `hr_app@localhost` |
| Schemas | `hr_staging`, `hr_oltp`, `hr_dw` created (utf8mb4) |
| Table create + bulk insert | OK |
| Stored procedure using a CTE and `DENSE_RANK()` | Created, executed, correct ranks returned |

Server settings and their consequences for the design:

| Setting | Value | Consequence |
|---|---|---|
| `local_infile` | OFF | Bulk loading uses batched `executemany` inserts — works identically on the cloud database |
| `sql_mode` | strict (incl. `STRICT_TRANS_TABLES`, `ONLY_FULL_GROUP_BY`) | Invalid data raises errors instead of being silently altered |
| `log_bin_trust_function_creators` | OFF | Application user cannot create functions/triggers → all ETL and SCD2 logic in **stored procedures** |
| `max_allowed_packet` | 64 MB | Sufficient for 5,000-row insert batches |

Driver note: mysql-connector-python 26.x deprecates `cursor.stored_results()`; the DB layer
accounts for this.

## Step 6 — Project skeleton and first pull request

- Branch `feature/project-setup`: folder structure (`src/models`, `src/dal`, `src/synthesizer`,
  `sql`, `diagrams`, `pages`, `tests`, `docs`), dataset moved to `data/raw/`, `requirements.txt`
  pinned to the verified versions, Streamlit theme config, initial README.
- **Problem:** push rejected — *GH007: Your push would publish a private email address*.
  **Fix:** the repository uses the GitHub no-reply address
  (`170617683+vnr-nitish@users.noreply.github.com`), which keeps the e-mail private while still
  attributing every commit to the GitHub account.
- Opened **PR #1** → merged into `main`.

**Branching strategy:** one feature branch per phase, merged into `main` through a pull request:
`feature/project-setup` → `feature/database-design` → `feature/data-synthesizer` →
`feature/etl-pipeline` → `feature/oop-backend` → `feature/streamlit-data-entry` →
`feature/streamlit-dashboard` → `feature/deployment-docs`.

## Step 7 — Phase 1: OLTP schema design (branch `feature/database-design`)

1. **First proposal (v1, 10 tables)** — reviewed and **rejected**: it grouped columns together so
   all 35 could not be traced, and it was derived from the column list rather than from the data.
2. **Data profiling** — [`analysis/profile_dataset.py`](../analysis/profile_dataset.py) measured
   domains, functional dependencies and business rules, producing findings F1–F11.
   Key discoveries: Manager is the only role in several departments (M:N), each role has a fixed
   level range, the performance rating fully determines the salary-hike range, the three "rate"
   columns are uncorrelated noise, and `NumCompaniesWorked` is internally inconsistent.
3. **Redesign (v2, 14 tables: 8 reference + 6 core)** — every one of the 35 columns mapped
   (20 stored, 8 derived, 6 dropped, 1 lineage), normalized to BCNF, with effective-dated job
   history as the single source of truth for SCD Type 2.
4. **Decisions approved:**

| ID | Decision |
|---|---|
| D1 | 14-table structure (8 reference + 6 core) |
| D2 | `manager_id` kept inside `employee_job_history` (one timeline) |
| D3 | IBM official performance rating scale 1–4 |
| D4 | Survey scales enforced with CHECK constraints, not lookup tables |
| D5 | Volume option A — 100,000 synthetic employees (~1.5 M OLTP rows) |

Full design: [`docs/oltp_design.md`](oltp_design.md).

## Step 8 — ER diagram

- Editable draw.io diagram: [`diagrams/oltp_er_diagram.drawio`](../diagrams/oltp_er_diagram.drawio)
  — 14 tables, 102 columns, 20 relationships in crow's-foot notation; colour-coded
  (blue = reference, yellow = bridge, green = core); legend and key-integrity-rules panel.
- Verified before use: XML well-formed, no duplicate IDs, every relationship connects existing
  columns, no overlapping tables.
- **Review round 1 (rendered in draw.io):** content correct, three layout issues fixed:
  1. Data types such as `TINYINT UNSIGNED NULL` wrapped and were clipped → type column widened
     from 110 px to 160 px (tables 340 → 410 px, gaps 80 → 120 px).
  2. Relationship labels overlapped table text in the narrow gaps → labels removed; the FK column
     names (`manager_id`, `reviewer_id`, `project_manager_id`) already name each relationship, and
     the composite FK is explained in the *Key integrity rules* panel.
  3. Page-break lines cut through the diagram → page enlarged to 2620 × 1260 to fit everything.
  Note: draw.io's dark appearance inverts colours on screen only; the file uses light colours.
- **Review round 2 (light mode):** the pasted copy came from a stale editor buffer (pre-fix
  version), so the round-1 fixes were not yet visible. One further issue was found: the
  `projects.project_manager_id → employees` line ran diagonally across two tables. It now follows
  an explicit orthogonal path through the empty gaps between tables.
- **Review round 3:** rendered correctly in draw.io — types on one line, no overlapping text, routed
  line clear of all tables. **ER diagram approved.**
- Committed to `feature/database-design` (profiling script, OLTP design, ER diagram, journal).

## Step 9 — Phase 1: Dimensional model / star schema (`hr_dw`)

1. **Method:** Kimball's four-step dimensional design — business process → grain → dimensions →
   facts — followed by SCD strategy, surrogate keys and a check against every required dashboard.
2. **Measured before designing** (from the IBM tenure columns): an average of **3.0 job versions per
   employee** (→ `dim_employee` ≈ 300K rows) and **4.77 review-years per employee** in 2019–2025
   (→ fact ≈ 477K rows). Low satisfaction sharply raises attrition (WorkLifeBalance 1 → 31.2 %,
   EnvironmentSatisfaction 1 → 25.4 %, JobSatisfaction 1 → 22.8 %), so survey measures must reach
   the warehouse.
3. **Proposal:** grain = one row per performance review; `fact_performance_reviews` with four
   dimensions (`dim_employee` SCD Type 2, `dim_department`, `dim_project`, `dim_date`); survey scores
   as measures in the same fact; point-in-time surrogate-key lookup for historically correct reporting.
4. **Special-member review:** the first proposal placed an "Unknown" row (sk = 0) in every
   dimension. On review it was narrowed: only `project_sk` is legitimately optional, so only
   `dim_project` gets a special member, with key **−1** (MySQL's AUTO_INCREMENT treats an inserted
   0 as "generate next value" unless `NO_AUTO_VALUE_ON_ZERO` is set). All other foreign keys are
   mandatory; failed lookups go to an `etl_rejected_rows` table instead of a fake key, which keeps
   headcounts correct and data errors visible.
5. **Decisions approved:** D6 atomic review grain · D7 survey measures in the fact · D8 SCD2 tracks
   every job-history attribute (1 : 1 with OLTP versions) · D9 Indian fiscal year in `dim_date` ·
   D10 `dim_project` Type 1 · D11 special member only in `dim_project` (−1) · D12 declared foreign
   keys · D13 no PII in the warehouse · D14 inclusive end dates in the warehouse (half-open in OLTP).
6. Clarified in the OLTP design that `effective_end_date` is exclusive (half-open periods).

Full design: [`docs/dw_design.md`](dw_design.md).

## Step 10 — Star schema diagram

- Editable draw.io diagram: [`diagrams/dw_star_schema.drawio`](../diagrams/dw_star_schema.drawio)
  — fact table at the centre, four dimensions, two ETL metadata tables; 105 columns.
  Type 2 attributes are tinted yellow and SCD control columns pink; markers T1 / T2 / NK / DD / M
  show each column's role.
- Verified before use: XML well-formed, no duplicate IDs, all relationships connect existing
  columns, no overlapping elements.
- **Review round 1:** content correct. One layout flaw: the `department_sk` line crossed the
  `date_sk` line. Fixed by swapping the positions of `dim_date` (now top) and `dim_department`
  (now middle), so the fact's lines fan out without crossing.
- **Review round 2:** rendered correctly — no crossing lines. **Star schema diagram approved.**
- Committed to `feature/database-design`; **PR #2** (database design: ER model, star schema, design
  documentation) opened and merged into `main`.

## Step 12 — Phase 2: DDL scripts (branch `feature/database-ddl`)

**Plan approved first:** file list and run order, how each design rule becomes SQL, reference-data
values, a rebuild safety guard, and automated constraint tests. Staging DDL was deliberately moved
to the synthesizer branch, because staging tables mirror the synthesizer's output.

**Question answered — where do the "1–5" values come from?** Job levels 1–5 and education 1–5 are
the IBM dataset's own codes; the rating scale 1–4 is IBM's official PerformanceRating scale. A new
measurement showed job level is **independent of rating** (correlation −0.021) but **strongly tied
to income** (0.95). Recorded as finding **F12** and added to the profiling script.

**Files delivered:**

| File | Contents |
|---|---|
| `sql/00_create_schemas.sql` | `hr_staging`, `hr_oltp`, `hr_dw` (utf8mb4) |
| `sql/01_oltp_ddl.sql` | 14 tables, 20 foreign keys (all RESTRICT), CHECK constraints, generated columns, indexes |
| `sql/02_oltp_reference_data.sql` | the 8 lookup tables (52 rows), each value traceable to a finding |
| `sql/03_oltp_views.sql` | `vw_employee_current`; `vw_employee_snapshot` rebuilds IBM-style columns with CTEs, `LAG()` and `ROW_NUMBER()` (lossless-decomposition proof) |
| `sql/04_dw_ddl.sql` | 4 dimensions, fact table, 2 ETL metadata tables, 4 foreign keys |
| `sql/05_dw_seed.sql` | special member `dim_project` (sk −1, id 0) |
| `scripts/setup_database.py` | runs the scripts in order (handles `DELIMITER`), refuses to rebuild over loaded data unless `--force` |
| `tests/conftest.py`, `tests/test_schema.py` | 45 automated tests, each in a rolled-back transaction |

**Result:** all scripts build in about 1.3 s (65 statements). **45 / 45 tests pass**: structure
(table counts, reference rows, every FK is RESTRICT, special member present), a full valid employee
lifecycle, the snapshot view's reconstructed tenure / promotion / manager / salary-hike values, and
negative tests proving MySQL rejects invalid data (invalid department–role pair, two current
versions, hire under 18, exit without reason, out-of-range scores, self-review, own manager,
duplicate reviews, deleting a department in use, and the warehouse equivalents).

**Refinements discovered while implementing** (design documents and diagrams updated to match):

| # | Finding | Change |
|---|---|---|
| R1 | `survey_year` was derivable from `survey_date` — a redundant column | now a generated column `YEAR(survey_date)` |
| R2 | `review_year` (the year reviewed) and `review_date` (when the review is held, often the next January) are genuinely different | both kept; CHECK keeps the date within the year or the following one |
| R3 | `YEAR_MONTH` is a reserved word in MySQL | `dim_date.year_month` renamed `year_month_label` |
| R4 | MySQL error 3818: a CHECK cannot reference an AUTO_INCREMENT column, so the special-member rule could not be written on `project_sk` | rule enforced through the natural key: special row has `project_id = 0` (NOT NULL, UNIQUE); the fact load becomes `COALESCE(project_id, 0)` |
| R5 | The warehouse could hold two current versions of an employee if an ETL run went wrong | `dim_employee.current_version_key` (generated + UNIQUE), the same guarantee as in OLTP |
| R6 | Not every reviewed year has a survey (e.g. hired after the survey date) | fact survey measures are nullable (NULL measures are acceptable; NULL foreign keys are not) |

Also added: code-format checks (`EMP000001`, `PRJ00001`); a project has an actual end date exactly
when it is Completed or Cancelled; an employee's latest job version stays open after they leave, so
every employee always has exactly one current version.

Note: rolled-back test inserts still consume AUTO_INCREMENT values (InnoDB never reuses them); a
rebuild before the data load resets the counters.

**Verification in MySQL Workbench:** `hr_oltp` showed 14 tables and 2 views. `hr_staging` and
`hr_dw` were stuck on "fetching…" — a Workbench cache issue after the schemas were dropped and
recreated while it was open, fixed by refreshing the Navigator. Confirmed independently through
`information_schema`: `hr_oltp` 14 tables + 2 views, 40 CHECK constraints, 20 foreign keys;
`hr_dw` 7 tables, 15 CHECK constraints, 4 foreign keys; `hr_staging` empty by design (its tables
arrive with the synthesizer).

**Agreed for the end of the project:** both draw.io diagrams will be re-rendered once more from the
final files and checked against the final schema.

- Committed to `feature/database-ddl`; **PR #3** opened and merged into `main`.

## Step 13 — Phase 1: Data synthesizer design (branch `feature/data-synthesizer`)

**Measured before designing:**
- NumCompaniesWorked: every "0" has prior experience (median 1 year); 474 of 521 "1"s have none
  → the field counts the current employer and "0" is a coding error.
- Of 1,291 promoted employees, 851 rose a level; for **310 (21.1 %)** the previous level would
  fall below their role's minimum while their current role started *before* the promotion —
  a direct conflict between IBM's tenure columns and the role level ranges.
- 245 promotions coincide with a role change; 485 manager changes coincide with a role change.
- Early-tenure attrition: 29.8 % within the first two years vs 12.0 % afterwards.

**Decisions approved** (full rationale and a Q&A section in
[`docs/synthesizer_design.md`](synthesizer_design.md)):

| ID | Decision |
|---|---|
| D16 | Stratified bootstrap with bounded jitter (keeps real attribute combinations and correlations) |
| D17 | Snapshot 2026-06-30; review cycles 2019–2025 held Jan–Mar of the next year; surveys Sep–Oct; exits spread 2019 → snapshot |
| D18 | Conflict resolved with **pay-grade promotions** (consistent with finding F8): 100 % of IBM tenure values kept, no business rule broken |
| D19 | NumCompaniesWorked cleaning rule; applied in SQL during the staging → OLTP load |
| D20 | Faker locale `en_IN` (consistent with the Indian fiscal year) |
| D21 | Small, counted data-quality issues planted in staging so the SQL cleaning can be proven |
| D22 | `db_manager.py` (Singleton, shared pool) built now for the loader and reused in Phase 3 |

Guiding principle: the synthesizer imitates the legacy source system (raw labels, business keys,
original quirks); all cleaning and transformation happen in SQL.

## Step 14 — Data synthesizer implementation

**Built** (object-oriented package `src/synthesizer/`): `SynthesizerConfig`, an abstract
`BaseGenerator`, `IBMSourceData`, `EmployeeGenerator`, `JobHistoryGenerator`, `SurveyGenerator`,
`ReviewGenerator`, `ProjectGenerator`, `AssignmentGenerator`, `AnomalyInjector`,
`SyntheticDataset`, `SynthesisValidator`, `StagingLoader`, and the `SynthesisPipeline` CLI
(`python -m src.synthesizer.run`). Also `src/db_manager.py` (the `DatabaseConnection` Singleton
with one shared pool — each subclass is itself a Singleton) and `src/exceptions.py`, plus
`sql/06_staging_ddl.sql` (six permissive staging tables).

**Approach:** build small, measure, fix, scale up — 3,000 → 5,000 → 20,000 → 100,000 employees,
with the validator checking 33 business rules on every run.

**Problems found and fixed along the way** (details: `synthesizer_design.md` §4b):
- Long careers started implausibly senior (6.1 % hired into level 3+ with ≤ 2 years' experience)
  → new decision **D24** "career depth": earlier promotions before the first IBM-dated event,
  junior-capable previous roles; reduced to ~1 % with every IBM tenure value unchanged.
- Consistency with the future SQL view: promotion label priority (I1), transfers only where IBM's
  manager dates allow (I2), transfers must change role (I3), previous manager excluded (I4).
- A level-5 R&D Manager had no possible transfer target → crash on an empty choice; fixed with a
  same-department fallback.
- Invalid fallback managers (I5) and a leap-year edge case at age 18 (I6) — both caught by the
  validator, both fixed.
- Assignments: 45 % of reviews without a project, then 58 % over-allocated, then a retroactive-
  overlap bug — resolved by per-year staffing, joining projects on the anchor date and capacity-
  aware allocations (I7); the validator's over-allocation measure itself was corrected (I8).
- Assignment generation optimized from ~75 s to 9 s at full scale (I9).

**Final run (seed 42, 100,000 employees):** 1,847,772 staging rows; **0 violations across 33 rule
checks**; fidelity: YearsInCurrentRole 100 %, YearsSinceLastPromotion 100 %, YearsWithCurrManager
98.1 %, last salary hike (±1 pt) 92.1 %; correlation matrix vs IBM within 0.009 (mean 0.002);
department / role / level / attrition mix identical to IBM; attrition by overtime 30.1 % vs IBM
30.5 %. Generated and validated in ~70 s, loaded into `hr_staging` in ~93 s (5,000-row batches,
8,000–25,000 rows/s). Verified directly in MySQL: 1,847,772 rows, one batch; staging ≈ 236 MB.
Report: [`docs/synthesis_report.md`](synthesis_report.md).

**Tests:** `tests/test_synthesizer.py` (9 tests: rules, fidelity, population mix, correlations,
uniqueness, planted-issue manifest, reproducibility, reference data mirrors the database,
Singleton sharing one pool). Whole suite: **54 / 54 pass**.

**D24 reviewed and approved.** Rationale accepted: it removes a visible realism defect (senior
hires at 18), contradicts no IBM value (IBM fixes only the *last* promotion), mirrors the logic of
D18, touches only 2.8 % of job versions, and gives the SCD Type 2 history believable career paths.
Alternatives (dropping it, changing hire levels or ages directly) would either leave the defect
or override source data.

## Step 11 — Design questions answered and documented

Two questions raised during the star-schema review were answered and written into the design
document for the final report:

1. **Why a star schema and not a snowflake?** — new section 3 of `dw_design.md`: comparison table
   (joins, speed, SQL simplicity, storage, SCD2 behaviour), a worked query (2 joins vs 5), why
   normalization belongs in OLTP rather than the warehouse, and when a snowflake *would* be
   right. Recorded as decision **D15**.
2. **Why not sk = 0 for missing foreign keys?** — section 9 of `dw_design.md` expanded: three
   options compared (NULL FKs, sk = 0 in every dimension, targeted special member), with the
   concrete problems of each — silently dropped rows, inflated headcounts, distorted averages,
   hidden data errors, meaningless SCD columns, and MySQL's AUTO_INCREMENT treatment of 0.

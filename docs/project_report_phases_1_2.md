# Enterprise Employee Analytics & Data Warehouse System
## Project Report — Phase 1 (Architecture, Modeling, Data Ingestion) and Phase 2 (SQL & ETL)

**Team project · Rising Stars Greenfield Mini-Project · Stack:** Python 3.11 (pandas, Faker), MySQL 8, Streamlit, Plotly, GitHub, Streamlit Community Cloud
**Repository:** https://github.com/vnr-nitish/v4c-miniproject-greenfield

---

## Contents
1. The source dataset (IBM HR Analytics)
2. Data synthesis — from 1,470 to 100,000 employees
3. OLTP database design (ER model)
4. OLAP data-warehouse design (star schema)
5. Phase 2 — DDL execution
6. Phase 2 — the ETL pipeline
7. Results and verification

---

## 1. The source dataset (IBM HR Analytics Employee Attrition & Performance)

### 1.1 Overview

| Property | Value |
|---|---|
| File | `WA_Fn-UseC_-HR-Employee-Attrition.csv` (Kaggle) |
| Rows | **1,470** — one per employee |
| Columns | **35** |
| Time dimension | **none** — a single snapshot; no dates, no history |
| Departments | **3**: Research & Development 961 · Sales 446 · Human Resources 63 |
| Job roles | **9** |
| Job levels | **5** (1 = entry … 5 = executive) |
| Attrition | Yes **237 (16.1 %)** · No 1,233 |
| Performance rating | only **3 (Excellent)** and **4 (Outstanding)** occur |

### 1.2 Departments, roles and levels in the source

| Job role | Employees | Department(s) | Levels seen |
|---|---:|---|---|
| Sales Executive | 326 | Sales | 2 – 4 |
| Research Scientist | 292 | Research & Development | 1 – 3 |
| Laboratory Technician | 259 | Research & Development | 1 – 3 |
| Manufacturing Director | 145 | Research & Development | 2 – 4 |
| Healthcare Representative | 131 | Research & Development | 2 – 4 |
| Manager | 102 | **all three departments** | 3 – 5 |
| Sales Representative | 83 | Sales | 1 – 2 |
| Research Director | 80 | Research & Development | 3 – 5 |
| Human Resources | 52 | Human Resources | 1 – 3 |

| Job level | Employees | Monthly income seen |
|---|---:|---|
| 1 | 543 | 1,009 – 4,968 |
| 2 | 534 | 2,042 – 9,998 |
| 3 | 218 | 5,210 – 13,757 |
| 4 | 106 | 11,103 – 17,924 |
| 5 | 69 | 18,041 – 19,999 |

### 1.3 All 35 columns

Scales: satisfaction / involvement 1 Low · 2 Medium · 3 High · 4 Very High; work-life balance
1 Bad · 2 Good · 3 Better · 4 Best; rating 1 Low · 2 Good · 3 Excellent · 4 Outstanding; education
1 Below College · 2 College · 3 Bachelor · 4 Master · 5 Doctor.

| # | Column | Values in the file | Meaning | Used in the project as |
|---:|---|---|---|---|
| 1 | Age | 18 – 60 (median 36) | age | date of birth |
| 2 | Attrition | Yes 237 · No 1,233 | has left? | exit date + exit reason |
| 3 | BusinessTravel | Rarely 1,043 · Frequently 277 · Non-Travel 150 | travel frequency | job version attribute |
| 4 | DailyRate | 102 – 1,499 | pay rate (random noise) | staging only |
| 5 | Department | R&D 961 · Sales 446 · HR 63 | department | job version attribute |
| 6 | DistanceFromHome | 1 – 29 km | commute | employee |
| 7 | Education | 1 – 5 | education level | employee (lookup) |
| 8 | EducationField | Life Sciences 606 · Medical 464 · Marketing 159 · Technical Degree 132 · Other 82 · HR 27 | field of study | employee (lookup) |
| 9 | EmployeeCount | always 1 | constant | dropped |
| 10 | EmployeeNumber | 1 – 2,068 | IBM id | lineage in staging |
| 11 | EnvironmentSatisfaction | 1 – 4 | survey | yearly survey |
| 12 | Gender | Male 882 · Female 588 | gender | employee |
| 13 | HourlyRate | 30 – 100 | noise | staging only |
| 14 | JobInvolvement | 1 – 4 | survey | yearly survey |
| 15 | JobLevel | 1 – 5 | seniority | job version |
| 16 | JobRole | 9 roles | role | job version |
| 17 | JobSatisfaction | 1 – 4 | survey | yearly survey |
| 18 | MaritalStatus | Married 673 · Single 470 · Divorced 327 | status | employee |
| 19 | MonthlyIncome | 1,009 – 19,999 (median 4,919) | pay | job version |
| 20 | MonthlyRate | 2,094 – 26,999 | noise | staging only |
| 21 | NumCompaniesWorked | 0 – 9 | previous employers | employee (cleaned) |
| 22 | Over18 | always Y | constant | dropped |
| 23 | OverTime | Yes 416 · No 1,054 | overtime | yearly review |
| 24 | PercentSalaryHike | 11 – 25 % | last pay rise | last pay change in job history |
| 25 | PerformanceRating | 3, 4 | rating | yearly review |
| 26 | RelationshipSatisfaction | 1 – 4 | survey | yearly survey |
| 27 | StandardHours | always 80 | constant | dropped |
| 28 | StockOptionLevel | 0 – 3 | stock options | job version |
| 29 | TotalWorkingYears | 0 – 40 | whole career | prior experience = Total − YearsAtCompany |
| 30 | TrainingTimesLastYear | 0 – 6 | trainings | yearly review |
| 31 | WorkLifeBalance | 1 – 4 | survey | yearly survey |
| 32 | YearsAtCompany | 0 – 40 (median 5) | tenure | **hire date** |
| 33 | YearsInCurrentRole | 0 – 18 | time in role | date of **last role change** |
| 34 | YearsSinceLastPromotion | 0 – 15 | time since promotion | date of **last promotion** |
| 35 | YearsWithCurrManager | 0 – 17 | time with manager | date of **last manager change** |

### 1.4 What profiling the data told us (and the design decision each finding drove)

| # | Finding (measured on the 1,470 rows) | Consequence |
|---|---|---|
| F1 | 8 of 9 roles occur in one department; **Manager occurs in all three** | Department ↔ role is many-to-many → bridge table with the **11 valid pairs** |
| F2 | Each role spans a fixed range of levels (e.g. Manager 3–5) | `job_roles.min/max_job_level_id`, enforced by procedures |
| F3 | Each level has a salary band | `job_levels.salary_band_min/max` |
| F4 | The rating fully determines the salary-hike range (3 → 11–19 %, 4 → 20–25 %) | A transitive dependency → its own table `performance_rating_levels` |
| F5 | DailyRate, HourlyRate, MonthlyRate do not correlate with income (≈ 0) | Random noise → excluded from the database design |
| F6 | Tenure rules hold for every row (years in role ≤ years at company ≤ total years) | CHECK constraints and generation rules |
| F7 | NumCompaniesWorked contradicts experience for 671 rows | A documented cleaning rule (D19) |
| F8 | 440 level-1 employees were "promoted" although level 1 is the lowest | A promotion is not always a level change → pay-grade promotions |
| F10 | Attrition 30.5 % with overtime vs 10.4 % without | Overtime recorded per year for trend analysis |

---

## 2. Data synthesis — from 1,470 to 100,000 employees (Phase 1, task 1)

### 2.1 The goal and the challenge

Scale the dataset to **100,000+ employees** with pandas and Faker, and **engineer history** (department
changes, promotions, pay rises) so the warehouse can demonstrate **SCD Type 2** — although the source
is a single snapshot with no dates.

**Key idea:** the four tenure columns (YearsAtCompany, YearsInCurrentRole, YearsSinceLastPromotion,
YearsWithCurrManager) say *when* things happened. Counting back from a snapshot date turns them into
dated events, so the history is built **backwards from the present** and the original IBM values can
be recovered exactly from the database.

### 2.2 Decisions

| ID | Decision | Why |
|---|---|---|
| D16 | **Stratified bootstrap with bounded jitter**: every group of (attrition, department, role, level) gets its exact share of the 100,000; values vary slightly (income ±7 % inside the level's band, age ±1, distance ±2 km) | Keeps real attribute combinations and correlations; every person unique |
| D17 | **Timeline:** snapshot **30 June 2026**; review cycles 2019–2025, held 15 Jan – 15 Mar of the next year; surveys September–October; exits spread from 2019 to the snapshot | Seven years of trends; realistic review calendar |
| D18 | **Pay-grade promotions** when a level change would break the role's level range | Keeps 100 % of IBM tenure values and every business rule (finding F8) |
| D19 | **NumCompaniesWorked cleaning rule**, applied in SQL during the migration | Fixes the contradiction found in F7 where cleaning belongs (the ETL) |
| D20 | Faker locale **en_IN** (Indian names) | Consistent with the Indian fiscal year in the warehouse |
| D21 | **Plant small, counted data-quality problems** in staging | Proves the SQL cleaning works; a test compares counts |
| D24 | **Earlier promotions for long careers** | With only three dated events, long careers started too senior (6.1 % implausible hires → about 1 %) |
| D26 | **Review and survey history follows the snapshot's distribution** | A first version produced a fake jump in the last review cycle (see 2.6) |
| D27 | **KPI from a truncated normal distribution** per rating | Clipping had piled 1,810 scores at exactly 100 |

### 2.3 How each part was generated

| Table | Rows | How it was generated |
|---|---:|---|
| Employees | 100,000 | Stratified sample of IBM rows (D16); Faker identities; hire date = snapshot (or exit) − YearsAtCompany; the 16.1 % leavers get an exit date between 2019 and June 2026 |
| Job history | 308,986 versions | Backwards from the current job: last promotion, last role change and last manager change dated from the IBM columns; 10 % department transfers; earlier promotions for long careers (D24); pay rises consistent with PercentSalaryHike. Every employee reports to someone in the same department at a higher level |
| Surveys | 468,646 | One per person per year while employed; the latest survey = the IBM answers; earlier years step back with small changes, keeping the IBM mix every year (D26) |
| Reviews | 463,015 | One per person per year for people who joined by 30 September and are still employed on the review date; latest review = IBM rating, overtime, training; KPI drawn from a range set by the rating (D27); reviewer = manager on the review date |
| Projects | 2,000 | Split over departments by headcount; managers are level 3+; about 40 % of finished projects end late by 1–120 days |
| Assignments | 503,240 | 92 % of people staffed each year, 25 % on a second project; allocations 20–100 %; about 5 % allowed above 100 % (bottlenecks) |
| **Total** | **≈ 1.85 million rows** | |

**KPI rule (D27):** rating 4 → around 92 (85–100), 3 → around 78 (60–95), 2 → around 60 (40–75),
1 → around 42 (20–60); out-of-range values are drawn again rather than clipped.

### 2.4 Planted data-quality problems (D21)

| Problem | Count | Cleaned in Phase 2 by |
|---|---:|---|
| Employee sent twice | 500 | `ROW_NUMBER()`, newest kept |
| Gender written as M / F | 1,027 | `CASE` → Male / Female |
| Department / role labels: wrong case, spaces, aliases ("R&D", "HR", "Sales Dept") | 6,172 | `TRIM` + label map |
| Review resubmitted with a corrected KPI | 1,389 | `ROW_NUMBER()` per employee and year, latest kept |

### 2.5 Loading into staging

The six generated tables are bulk-loaded into the **`hr_staging`** schema (tables mirror the files,
**no constraints**, so dirty data can land) with batched multi-row inserts of 5,000 rows — 1.85
million rows in about 1–1.5 minutes. Generation and validation of 100,000 employees takes about
30 seconds with vectorized pandas / NumPy.

### 2.6 Validation, and what did not work at first

The synthesizer checks **42 business rules** (0 violations) and measures **fidelity**: rebuilding the
IBM columns from the generated history gives YearsInCurrentRole and YearsSinceLastPromotion **100 %**,
YearsWithCurrManager **98.09 %**, the last salary hike within one point **92.09 %**.

| Problem found | Fix |
|---|---|
| A promotion on the same date as a transfer was labelled "Transfer" | "Promotion" takes precedence |
| Transfers could keep the same role or contradict the manager dates | Transfers must change role and respect IBM's manager dates |
| 28 managers who left mid-period in a 5,000-employee test | Fallback removed; early periods without an eligible manager stay empty (1 % of current versions) |
| First assignment design left 45 % of reviews without a project, then over-allocated 58 % | Yearly staffing anchor: 3.7 % without project, 1.8 % ever over-allocated |
| Assignment generation took 75 s | Pre-bucketed and vectorized: 9 s |
| The latest review cycle looked like a sudden improvement (low ratings 4 % → 0 %, overtime 35 % → 24 %) | Earlier years now follow the snapshot's distribution (D26); verified that employees, job history, projects and assignments stayed identical |

---

## 3. OLTP database design — `hr_oltp` (Phase 1, task 2)

### 3.1 Method

Data profiling → functional-dependency analysis → normalization to **3NF / BCNF** → temporal modeling
(effective-dated history) → physical design for 100,000+ employees.

### 3.2 The 14 tables

**8 reference (lookup) tables**

| Table | Holds | Primary key | Notes |
|---|---|---|---|
| departments | 3 departments (HR, RND, SAL) | department_id | code and name unique |
| job_roles | 9 roles + allowed level range | job_role_id | min / max level → job_levels |
| department_job_roles | the 11 valid department–role pairs | (department_id, job_role_id) | bridge for many-to-many (F1) |
| job_levels | 5 levels + salary bands | job_level_id | Entry, Intermediate, Senior, Lead, Executive |
| education_levels | 1–5 | education_level_id | |
| education_fields | 6 fields | education_field_id | |
| performance_rating_levels | 1–4 + salary-hike range | rating_id | Low 0–4 %, Good 5–10 %, Excellent 11–19 %, Outstanding 20–25 % |
| exit_reasons | 9 reasons + voluntary flag | exit_reason_id | 6 voluntary, 3 involuntary |

**6 core tables**

| Table | Holds | Primary key | Important keys and rules |
|---|---|---|---|
| employees | the person (no job data) | employee_id | business key `employee_code` EMP000001 (unique, format check); e-mail unique; at least 18 on the hire date; exit date ↔ exit reason together; `is_active` generated |
| employee_job_history | **every job version**: department, role, level, manager, pay, stock, travel, change reason, effective dates | history_id | composite FK (department, role) → the valid pair; `manager_id` → employees; unique (employee, start date); **one current version per employee** enforced by a generated unique column |
| employee_surveys | yearly satisfaction survey (5 scores 1–4) | survey_id | unique (employee, year) |
| projects | 2,000 projects | project_id | code PRJ00001; actual end date set exactly when Completed / Cancelled |
| project_assignments | employee ↔ project, allocation % | assignment_id | unique (employee, project, start) |
| performance_reviews | yearly review: rating, KPI, training, overtime, reviewer, project | review_id | unique (employee, review year); reviewer ≠ employee; review date in the review year or the next |

### 3.3 Relationships (how to read the ER diagram)

The diagram (`diagrams/oltp_er_diagram.drawio`) has **employees** in the centre:

| Parent (one) | Child (many) | Meaning |
|---|---|---|
| employees | employee_job_history | every employee has 1 or more job versions (the first is "Hire") |
| employees (as manager) | employee_job_history | a job version names the person's manager (self-reference) |
| department_job_roles | employee_job_history | a job version is a valid department–role pair |
| job_levels | employee_job_history | the version's level |
| departments / job_roles | department_job_roles | the bridge resolving many-to-many |
| job_levels | job_roles | each role's minimum and maximum level |
| employees | employee_surveys | one survey per year |
| employees (reviewed) and employees (reviewer) | performance_reviews | two relationships to the same table |
| projects, performance_rating_levels | performance_reviews | the main project that year; the rating label |
| departments, employees (project manager) | projects | owning department; project manager |
| employees, projects | project_assignments | many-to-many staffing |
| education_levels, education_fields, exit_reasons | employees | lookups |

### 3.4 Data integrity in five layers

1. Declarative constraints — primary, foreign, unique and CHECK constraints.
2. A generated-column unique index — at most one current job version per employee.
3. Stored procedures — rules constraints cannot express (role offered by the department, level in
   range, pay in band, no overlapping history).
4. Python entity classes — validate input before it reaches the database (Phase 3).
5. Validation SQL — run after every load.

All foreign keys are **ON DELETE RESTRICT**: HR history is never deleted; leaving is an exit date.

### 3.5 Decisions, and what did not work

| ID | Decision | Instead of | Why |
|---|---|---|---|
| D1 | 14 tables: 8 reference + 6 core | a first 10-table design | The first design did not trace all 35 columns and was not data-driven — it was rejected and redone after profiling |
| D2 | Manager kept inside the job history | a separate reporting-line table | One timeline; a simpler, correct SCD Type 2 ETL |
| D3 | IBM's official 1–4 rating scale | a custom 1–5 scale | Faithful to the source |
| D4 | Survey scales enforced by CHECK | lookup tables | No 5 extra indexes on ~470K rows |
| D5 | 100,000 employees | 30,000 | Meets "100,000+" literally |
| D25 | Keep 14 tables after reviewing the five entities in the problem statement | exactly five tables | Five tables reach 3NF only by dropping the job history, surveys and business rules |

Also rejected: a yearly salary-history table (would make the employee dimension larger than the fact
table), one generic lookup table (an anti-pattern), triggers for SCD2 (procedures are testable),
MySQL ENUM (CHECK is standard SQL).

---

## 4. OLAP data-warehouse design — `hr_dw` (Phase 1, task 3)

### 4.1 Method — Kimball's four steps

| Step | Decision |
|---|---|
| 1. Business process | the annual performance review (with that year's survey) |
| 2. Grain | **one row per performance review** = one employee in one review year (atomic) |
| 3. Dimensions | employee (SCD Type 2), department, project, date |
| 4. Facts | rating, KPI, training, overtime, income at review, five survey scores |

**Star, not snowflake (D15):** the problem statement asks for a star; fewer joins; each dimension is
self-contained (department and manager names flattened into the employee dimension); normalizing would
save only about 20 MB.

### 4.2 The star

```
                  dim_date
                     │
dim_department ── fact_performance_reviews ── dim_project
                     │
                dim_employee  (SCD Type 2)
```

| Table | Type | Surrogate key | Natural key | Rows |
|---|---|---|---|---:|
| fact_performance_reviews | fact | review_id (degenerate) | — | 463,015 |
| dim_employee | **SCD Type 2** | employee_sk | employee_id | 308,986 versions |
| dim_department | SCD Type 1 | department_sk | department_id | 3 |
| dim_project | SCD Type 1, flattened | project_sk (**−1 = "No Project"**) | project_id | 2,001 |
| dim_date | static | date_sk = YYYYMMDD | full_date | 5,844 (2015–2030) |

### 4.3 The columns

**`dim_employee`** — one row per job version:

| Group | Columns | Change handling |
|---|---|---|
| Keys | employee_sk (surrogate), employee_id (natural), employee_code | — |
| Person | full name, gender, date of birth, marital status, education level and field, distance, prior experience and companies, hire date, exit date and reason, voluntary flag, attrition flag | **Type 1**: a correction overwrites every version |
| Job | department code and name, job role, job level and title, manager id and name, monthly income, salary band, stock options, travel, change reason | **Type 2**: a change creates a new version |
| SCD control | **start_date**, **end_date** (9999-12-31 when current), **is_current**, version_number, row_hash (MD5 of the Type 2 columns) | — |

No e-mail or phone in the warehouse (data minimization, D13).

**`fact_performance_reviews`:** keys date_sk, employee_sk (**the version valid on the review date**),
department_sk, project_sk; measures performance_rating, kpi_score, training_sessions, overtime_flag,
monthly_income_at_review, environment / job / relationship satisfaction, work-life balance, job
involvement (survey measures may be empty if there was no survey that year).

**`dim_date`:** day, weekday, week, month, quarter, calendar year, year-month, **Indian fiscal year**
(April–March, e.g. FY2024-25) and fiscal quarter.

**`dim_project`:** code, name, owning department and manager names (flattened), dates, status,
priority, budget, and derived planned duration, delay days, is_delayed, budget band.

### 4.4 SCD Type 2 — how history works

| employee_sk | department | role | level | start_date | end_date | is_current |
|---|---|---|---|---|---|---|
| 1001 | R&D | Laboratory Technician | 1 | 2017-03-01 | 2020-03-31 | 0 |
| 1002 | R&D | Laboratory Technician | 2 | 2020-04-01 | 2023-06-30 | 0 |
| 1003 | R&D | Research Scientist | 2 | 2023-07-01 | 9999-12-31 | 1 |

A review held in 2021 links to **1002** — the employee *as they were* — so history is reported
truthfully. The fact load finds that version with
`review_date BETWEEN start_date AND end_date`. OLTP uses half-open periods (end = next start);
the warehouse uses inclusive end dates (Kimball's BETWEEN convention).

### 4.5 Surrogate keys and the special member

Every dimension has a warehouse-generated surrogate key. A review without a project points to
**project_sk = −1 "No Project Assigned"** (16,961 reviews) instead of a NULL key; every other key is
mandatory — a failed lookup is a data error, logged as a rejected row, not hidden behind "Unknown".

### 4.6 Decisions

| ID | Decision | Why |
|---|---|---|
| D6 | Grain = one row per review | atomic; matches the business event |
| D7 | Survey measures in the review fact | same grain; one star |
| D8 | Every job-history attribute is Type 2 (1 : 1 with OLTP versions) | simple and auditable |
| D9 | Indian fiscal year in dim_date | realistic for an Indian HR context |
| D10 | dim_project Type 1 | no required analysis needs project history |
| D11 | Special member only in dim_project (−1) | correct counts, visible data errors |
| D12 | Foreign keys declared in the warehouse | integrity and a self-documenting schema |
| D13 | No personal contact data in the warehouse | data minimization |
| D14 | Inclusive end dates in the warehouse, half-open in OLTP | BETWEEN lookups; conversion documented |
| D15 | Star, not snowflake | required; fewer joins |

---

## 5. Phase 2 — DDL execution

### 5.1 The scripts

Nine numbered SQL scripts in `sql/`, run in order:

| Script | Statements | What it creates |
|---|---:|---|
| 00_create_schemas.sql | 3 | the schemas `hr_staging`, `hr_oltp`, `hr_dw` (utf8mb4) |
| 01_oltp_ddl.sql | 31 | the 14 OLTP tables with keys, foreign keys, CHECKs, generated columns, indexes |
| 02_oltp_reference_data.sql | 9 | the reference rows (departments, roles, valid pairs, levels with bands, ratings, exit reasons, education) |
| 03_oltp_views.sql | 3 | `vw_employee_current`, `vw_employee_snapshot` (rebuilds the IBM columns) |
| 04_dw_ddl.sql | 17 | the star schema + ETL log tables |
| 05_dw_seed.sql | 2 | warehouse seed rows |
| 06_staging_ddl.sql | 13 | the six staging tables (no constraints) |
| 07_oltp_procedures.sql | 11 | migration and application procedures |
| 08_dw_procedures.sql | 23 | the warehouse ETL procedures |
| **Total** | **112** | |

### 5.2 How they were executed

- **`python scripts/setup_database.py`** runs all nine in order (in Phase 3 it became a thin front end
  over the `SchemaManager` class). A custom SQL splitter handles comments, quoted text and the
  `DELIMITER` blocks around procedures, so procedures keep their comments on the server.
- **Safety:** if a script would drop tables that already hold data, it refuses unless `--force` is
  given; `--only 03 07 08` re-installs views and procedures without touching data.
- The same scripts can be run one by one in **MySQL Workbench**, and the same command built the
  **cloud database** (Aiven MySQL) unchanged.
- A dedicated database user `hr_app` (not root) owns the work; credentials live only in `.env`.

---

## 6. Phase 2 — the ETL pipeline

### 6.1 Architecture: two stages, one audit trail

```
hr_staging ──(Stage 1: migration, once)──► hr_oltp ──(Stage 2: warehouse ETL, repeatable)──► hr_dw
 raw, dirty     de-duplicate · clean · resolve      dimensions · SCD Type 2 · fact
                labels and codes · validate
                       every step logged in hr_dw.etl_batch_log (+ etl_rejected_rows)
```

Run with `python scripts/run_etl.py --migrate` (first time) or `python scripts/run_etl.py`
(warehouse refresh), or the **Run warehouse ETL** button in the web app.

### 6.2 Stage 1 — `sp_migrate_from_staging` (staging → OLTP)

Tables load in foreign-key order (employees → job history → projects → assignments → surveys →
reviews). Each table follows the same four-step **CTE** pattern:

| Step | SQL technique | Purpose |
|---|---|---|
| ranked | `ROW_NUMBER() OVER (PARTITION BY business key ORDER BY stg_row_id DESC)` | keep one copy of a record sent twice |
| cleaned | `TRIM`, case-insensitive matching, `CASE` | standardize labels and codes |
| resolved | joins to the lookup tables; a `CASE` names the first broken rule | turn text into ids, validate |
| loaded / rejected | `INSERT`; rejects to `etl_rejected_rows` | nothing is silently lost |

Results: every planted problem fixed in exactly the manifest's numbers (500 duplicates, 1,027 gender
codes, 6,172 labels, 1,389 resubmissions), 16,500 NumCompaniesWorked values recoded (D19), job-version
end dates re-checked with `LEAD()` (0 corrections), **0 rejected rows**.

### 6.3 Stage 2 — `sp_run_etl` (OLTP → warehouse)

| Step | Procedure | Technique |
|---|---|---|
| 1 | `sp_load_dim_date` | **recursive CTE** generating 2015–2030 with the Indian fiscal year |
| 2 | `sp_load_dim_department` | SCD Type 1 (update changed, insert new) |
| 3 | `sp_load_dim_project` | SCD Type 1 + derived delay, duration, budget band; the −1 member |
| 4 | `sp_load_dim_employee_scd2` | **SCD Type 2** with `ROW_NUMBER()`, `LEAD()`, MD5 row hash |
| 5 | `sp_load_fact_performance_reviews` | incremental (`NOT EXISTS`), **point-in-time** key lookup |

Dimensions load before the fact, so a review always finds its dimension rows.

### 6.4 The SCD Type 2 algorithm

| Order | Detects | Action |
|---|---|---|
| 1 | a personal attribute changed (name, marital status, exit) | Type 1: overwrite every version |
| 2 | same version, different row hash | correct the version in place |
| 3 | a newer version started | **expire** the old row: end date = new start − 1 day, is_current = 0 |
| 4 | a version the warehouse has not seen | **insert** a new row with a new surrogate key |

Expire runs before insert, so there is never more than one current version. **One procedure, three
uses:** the initial load (308,986 versions), incremental runs (only differences — a re-run with no
change writes 0 rows), and a single employee changed in the web app (0.02 s).

### 6.5 Logging and error handling

Every run is a **batch** in `etl_batch_log` (a header plus one row per step and per data-quality
fix, so the log is also a data-quality report). An `EXIT HANDLER` catches any SQL error: `ROLLBACK`
undoes the run, the batch is marked `FAILED` with the step and MySQL's message, and the error is
re-raised to the caller. Only the entry points commit; the application's job change and its warehouse
update share **one transaction** (E3).

### 6.6 Decisions, and what did not work

| ID | Decision / finding | Result |
|---|---|---|
| E1 | The migration is one-time, with an explicit full-reload switch | refuses to run twice; no duplicates |
| E2 | One SCD2 procedure for initial, incremental and single-employee loads | one tested code path |
| E3 | Only entry points commit | the app's change and its warehouse version commit together |
| E4 | Department aliases handled in a cleaning CTE | no extra table or diagram change |
| E7 | `INSERT … ON DUPLICATE KEY UPDATE` counted wrongly and left key gaps | replaced by update-then-insert |
| E9 | A refused migration rolled back its own log header | the header is committed first |
| E10 | A re-run with no changes took 93 s (`EXPLAIN ANALYZE`: buffer pool overflow, unordered writes) | person / version split, ordered inserts: **47 s** |
| E11 | `GREATEST(unsigned, signed)` returned DECIMAL and failed a system variable | explicit CAST; the failure proved the error handler |
| Cloud | The free cloud MySQL (1 CPU, 32 MB buffer pool) could not finish the hour-long migration | the finished tables were copied from the local database and verified table by table |

---

## 7. Results and verification

| Run (100,000 employees, laptop, MySQL 8.0) | Rows | Time |
|---|---:|---:|
| Stage 1 migration | 1,845,887 loaded, 0 rejected | about 4 minutes |
| Stage 2 initial warehouse load | 774,004 (308,986 versions, 463,015 facts) | about 2.5 minutes |
| Stage 2 re-run, nothing changed | 0 | 47 s |
| One employee from the app | 2 | 0.02 s |

**Verified automatically** (`tests/test_etl.py`, also runnable in Workbench from
`sql/validation_queries.sql`):
- every staged record loaded or explained; every planted problem fixed in the manifest's numbers;
- **lossless reconstruction:** the IBM columns rebuilt from the OLTP database match staging — 100 %
  for age, tenure, years in role, years since promotion, department, role, level, income; 98.09 % for
  years with the manager; 92.09 % for the last salary hike;
- warehouse versions 1 : 1 with OLTP versions, periods contiguous, one current version per employee;
- every fact linked to the employee version valid on its review date;
- re-running the ETL changes nothing; a department change creates exactly one new version.

The whole project's test suite (Phases 1–5) has **228 tests, all passing**.

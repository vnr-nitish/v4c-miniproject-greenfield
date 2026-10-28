# Data Warehouse Design — `hr_dw` (Star Schema)

**Project:** Enterprise Employee Analytics & Data Warehouse System
**Phase:** 1 — Architecture, Modeling and Data Ingestion
**Status:** Approved design
**Diagram:** [`diagrams/dw_star_schema.drawio`](../diagrams/dw_star_schema.drawio)
**Source:** the normalized OLTP database — see [`oltp_design.md`](oltp_design.md)

---

## 1. Purpose

The OLTP database (`hr_oltp`) is optimized for writing and integrity: 14 normalized tables, many
joins. Analytical questions — *how did performance trend year over year? who are the top performers
in each department? who is at risk of leaving?* — need a structure optimized for reading:
few joins, descriptive attributes in one place, history preserved. That structure is a
**dimensional model (star schema)**, loaded from OLTP by stored procedures.

---

## 2. Method — Kimball's four-step dimensional design

1. **Select the business process** — what activity generates the measurements.
2. **Declare the grain** — exactly what one fact row represents.
3. **Identify the dimensions** — the who / what / where / when context of each measurement.
4. **Identify the facts** — the numeric measurements.

Followed by: slowly-changing-dimension strategy, surrogate keys, and a check that every required
dashboard can be answered from the model.

---

## 3. Why a star schema and not a snowflake schema

### 3.1 The two shapes

- **Star schema** — each dimension is **one denormalized table** joined directly to the fact
  table. Descriptive text (department name, job role, level title, education labels) is stored
  in the dimension row itself.
- **Snowflake schema** — dimensions are **normalized** into chains of sub-tables. Applied to this
  model, `dim_employee` would hold foreign keys to `dim_department`, `dim_job_role`,
  `dim_job_level`, `dim_education_level`, `dim_education_field` and `dim_exit_reason`;
  `dim_date` would split into month → quarter → fiscal-year tables; `dim_project` would point to
  `dim_department`. Roughly 12 tables instead of 5.

### 3.2 Comparison for this project

| Criterion | Star (chosen) | Snowflake |
|---|---|---|
| Problem statement | Explicitly requires a star schema | Not requested |
| Joins for a typical dashboard question | 2–3 | 5–8 |
| Query speed on ~477K fact rows | Faster — fewer joins | Slower — every extra join is extra work per query |
| SQL in the Streamlit dashboards | Short and readable | Long join chains, easier to get wrong |
| Storage | Repeats some text: ≈ 70 bytes × ~300K employee versions ≈ **20 MB** (estimate) | Saves those ≈ 20 MB — negligible |
| SCD Type 2 history | **Self-contained**: each employee version stores the department / role / level *as they were* | Fragile: if a snowflaked department or role row changes, every historical version pointing to it changes too, unless the sub-table is versioned as well |
| Update anomalies (the problem normalization solves) | Not applicable — only the ETL writes to the warehouse, always rebuilt from the normalized OLTP source | Same — so normalization brings no benefit here |

### 3.3 Worked example

*"Average performance rating by department and job level in FY 2024-25"*

Star schema — **2 joins**:

```sql
SELECT e.department_name, e.job_level_title, AVG(f.performance_rating) AS avg_rating
FROM fact_performance_reviews f
JOIN dim_employee e ON e.employee_sk = f.employee_sk
JOIN dim_date     d ON d.date_sk     = f.date_sk
WHERE d.fiscal_year = 'FY2024-25'
GROUP BY e.department_name, e.job_level_title;
```

A snowflake version of the same question would join fact → `dim_employee` → `dim_department`,
`dim_employee` → `dim_job_level`, and fact → `dim_date` → `dim_month` → `dim_fiscal_year` —
**5 joins** for the same answer.

### 3.4 Where normalization belongs

Normalization exists to protect data that **users write**: it prevents update, insert and delete
anomalies. That is the job of the OLTP database, which is normalized to BCNF
([`oltp_design.md`](oltp_design.md)). The warehouse is **read-optimized** and written only by the
ETL, so it is deliberately denormalized. The architecture therefore uses each technique where it
helps: a normalized OLTP source of truth feeding a denormalized star for analytics.

### 3.5 When a snowflake would be the right choice

A snowflake is justified for very large dimensions (millions of rows) with deep, low-cardinality
hierarchies where storage matters, for hierarchies shared by several dimensions that change
independently, or for tools that require normalized dimensions. None applies here: the largest
dimension has ~300K rows and the repeated text costs ≈ 20 MB.

---

## 4. Evidence from the data

Measured on the IBM dataset before designing (anchors: YearsAtCompany, YearsInCurrentRole,
YearsSinceLastPromotion, YearsWithCurrManager; snapshot at end of 2025):

| Measurement | Result | Design consequence |
|---|---|---|
| Distinct job-change points per employee | 1 version: 127 · 2: 308 · 3: 466 · 4: 569 — **mean 3.0** | `dim_employee` ≈ 100,000 × 3.0 ≈ **300K rows** |
| Review-years per employee in a 2019–2025 window | **mean 4.77** | `fact_performance_reviews` ≈ **477K rows** |
| Attrition by lowest vs highest satisfaction | WorkLifeBalance 1 → **31.2 %**; EnvironmentSatisfaction 1 → **25.4 %**; JobSatisfaction 1 → **22.8 %** (vs 11–17 % when satisfied) | Survey measures are essential for attrition-risk analysis → included in the fact (D7) |

---

## 5. Business process and grain

- **Business process:** the annual performance-review cycle (with the engagement survey of the
  same year).
- **Grain:** **one row per performance review = one employee in one review year.**
  This is the atomic grain — the most detailed level available. Kimball's rule: start atomic,
  because detail can always be rolled up but a summary can never be broken down.

---

## 6. Dimensions

All dimension tables use **surrogate keys** (warehouse-generated integers, independent of the OLTP
keys). The OLTP key is kept as the **natural key (NK)** for lineage and lookups.

### 6.1 `dim_employee` — SCD Type 2

| Group | Column | Type | Change handling |
|---|---|---|---|
| Keys | **employee_sk** | INT, PK, AUTO_INCREMENT | surrogate key |
| | employee_id | INT UNSIGNED | natural key (OLTP) |
| | employee_code | CHAR(9) | business key |
| Person / employment | full_name | VARCHAR(101) | Type 1 |
| | gender | VARCHAR(10) | Type 1 |
| | date_of_birth | DATE | Type 1 |
| | marital_status | VARCHAR(10) | Type 1 |
| | education_level | VARCHAR(30) | Type 1 (label, flattened from lookup) |
| | education_field | VARCHAR(60) | Type 1 |
| | distance_from_home_km | TINYINT UNSIGNED | Type 1 |
| | prior_experience_years | TINYINT UNSIGNED | Type 1 |
| | prior_companies_count | TINYINT UNSIGNED | Type 1 |
| | hire_date | DATE | Type 1 |
| | exit_date | DATE NULL | Type 1 |
| | exit_reason | VARCHAR(60) NULL | Type 1 |
| | is_voluntary_exit | BOOLEAN NULL | Type 1 |
| | attrition_flag | BOOLEAN | Type 1 (exit_date is set) |
| Job — historized | department_code, department_name | VARCHAR | **Type 2** |
| | job_role | VARCHAR(60) | **Type 2** |
| | job_level, job_level_title | TINYINT / VARCHAR(30) | **Type 2** |
| | manager_employee_id, manager_name | INT UNSIGNED NULL / VARCHAR(101) NULL | **Type 2** |
| | monthly_income | DECIMAL(10,2) | **Type 2** |
| | salary_band | VARCHAR(20) | **Type 2** (derived from monthly_income) |
| | stock_option_level | TINYINT UNSIGNED | **Type 2** |
| | business_travel | VARCHAR(20) | **Type 2** |
| | change_reason | VARCHAR(20) | **Type 2** (why this version started) |
| SCD control | start_date | DATE | first day the version is valid |
| | end_date | DATE | last day valid, **inclusive**; 9999-12-31 for the current version |
| | is_current | BOOLEAN | 1 for the current version |
| | version_number | SMALLINT UNSIGNED | 1, 2, 3 … per employee |
| | row_hash | CHAR(32) | MD5 of all Type 2 columns — fast change detection |
| | current_version_key | INT UNSIGNED NULL | **GENERATED**: employee_id when is_current, else NULL; UNIQUE → at most one current version per employee, enforced by the database (same technique as OLTP) |
| Audit | etl_batch_id, loaded_at, updated_at | INT / DATETIME | lineage |

Keys and indexes: UNIQUE (employee_id, start_date); UNIQUE (employee_id, version_number);
UNIQUE (current_version_key); index (employee_id, is_current).
Integrity checks: `end_date >= start_date`; `is_current` is true exactly when `end_date = 9999-12-31`;
`attrition_flag` is true exactly when `exit_date` is set.
**PII minimization:** email and phone are **not** copied to the warehouse — analytics never needs
them.

### 6.2 `dim_department` — SCD Type 1

| Column | Type | Notes |
|---|---|---|
| **department_sk** | INT, PK, AUTO_INCREMENT | surrogate key |
| department_id | TINYINT UNSIGNED, UNIQUE | natural key |
| department_code | VARCHAR(10) | |
| department_name | VARCHAR(60) | |
| updated_at | DATETIME | audit |

### 6.3 `dim_project` — SCD Type 1, flattened

| Column | Type | Notes |
|---|---|---|
| **project_sk** | INT, PK, AUTO_INCREMENT | surrogate key; **−1 = "No Project Assigned"** (§9) |
| project_id | SMALLINT UNSIGNED, UNIQUE | natural key; **0 for the special member** (OLTP ids start at 1) — see §9.6 |
| project_code | CHAR(8) | |
| project_name | VARCHAR(120) | |
| owning_department_name | VARCHAR(60) | flattened (star, not snowflake) |
| project_manager_name | VARCHAR(101) NULL | flattened |
| start_date, planned_end_date | DATE NULL | |
| actual_end_date | DATE NULL | NULL while running |
| planned_duration_days | SMALLINT NULL | derived |
| delay_days | SMALLINT NULL | derived: actual − planned end |
| is_delayed | BOOLEAN NULL | derived |
| status | VARCHAR(15) | |
| priority | VARCHAR(10) | |
| budget | DECIMAL(14,2) NULL | |
| budget_band | VARCHAR(20) | derived |
| updated_at | DATETIME | audit |

### 6.4 `dim_date`

| Column | Type | Example |
|---|---|---|
| **date_sk** | INT, PK | 20240315 (smart key YYYYMMDD — the one dimension where a meaningful key is standard) |
| full_date | DATE, UNIQUE | 2024-03-15 |
| day_of_month | TINYINT | 15 |
| day_name | VARCHAR(9) | Friday |
| day_of_week | TINYINT | 5 (ISO: Monday = 1) |
| is_weekend | BOOLEAN | 0 |
| week_of_year | TINYINT | 11 |
| month_number, month_name | TINYINT / VARCHAR(9) | 3 / March |
| quarter_number, quarter_label | TINYINT / CHAR(2) | 1 / Q1 |
| calendar_year | SMALLINT | 2024 |
| year_month_label | CHAR(7) | 2024-03 (named so because `YEAR_MONTH` is a reserved word in MySQL) |
| fiscal_year | CHAR(9) | FY2023-24 (Indian financial year, April–March) |
| fiscal_quarter | CHAR(2) | Q4 |

Range 2015-01-01 → 2030-12-31 (~5,844 rows), generated by a stored procedure using a
**recursive CTE**.

---

## 7. Fact table — `fact_performance_reviews`

| Column | Type | Role | Additivity |
|---|---|---|---|
| **review_id** | INT UNSIGNED, PK | degenerate dimension (OLTP review id) — lineage and incremental loads | — |
| date_sk | INT, FK → dim_date | review date | — |
| employee_sk | INT, FK → dim_employee | **employee version valid on the review date** | — |
| department_sk | INT, FK → dim_department | department on the review date | — |
| project_sk | INT, FK → dim_project | primary project that year, or −1 | — |
| performance_rating | TINYINT (1–4) | measure | non-additive (average) |
| kpi_score | DECIMAL(5,2) (0–100) | measure | non-additive (average) |
| training_sessions | TINYINT UNSIGNED | measure | additive |
| overtime_flag | TINYINT (0/1) | measure | additive (sum = overtime headcount) |
| monthly_income_at_review | DECIMAL(10,2) | measure | semi-additive (across people, not across time) |
| environment_satisfaction | TINYINT (1–4) | measure | non-additive |
| job_satisfaction | TINYINT (1–4) | measure | non-additive |
| relationship_satisfaction | TINYINT (1–4) | measure | non-additive |
| work_life_balance | TINYINT (1–4) | measure | non-additive |
| job_involvement | TINYINT (1–4) | measure | non-additive |
| etl_batch_id, loaded_at | INT / DATETIME | audit | — |

Survey measures share the review grain (employee × year), so they live in the same fact table
(decision D7) — one star, exactly as the problem statement specifies. They are **nullable**: an
employee may have a review but no survey in a year (for example, hired after the survey date).
A NULL *measure* is acceptable in Kimball modeling — averages simply ignore it — whereas a NULL
*foreign key* is not (see §9).
CHECK constraints guard every measure's range (rating 1–4, KPI 0–100, survey scales 1–4,
overtime 0/1, income > 0).
Indexes: every FK; composite (date_sk, department_sk) for year-by-department trends.

---

## 8. SCD Type 2 design

### 8.1 Which attributes create a new version

Every attribute of an OLTP `employee_job_history` row is tracked as Type 2 (decision D8), so
**warehouse versions map 1 : 1 to OLTP job-history versions** — simple to load and easy to audit.
Personal and employment attributes are Type 1: a correction overwrites every version of the
employee.

### 8.2 Worked example

| employee_sk | employee_id | department | job_role | level | income | start_date | end_date | is_current |
|---|---|---|---|---|---|---|---|---|
| 1001 | 42 | R&D | Laboratory Technician | 1 | 2,400 | 2017-03-01 | 2020-03-31 | 0 |
| 1002 | 42 | R&D | Laboratory Technician | 2 | 2,950 | 2020-04-01 | 2023-06-30 | 0 |
| 1003 | 42 | R&D | Research Scientist | 2 | 3,400 | 2023-07-01 | 9999-12-31 | 1 |

A review dated 2021 links to **sk 1002** — the employee *as they were* when reviewed — not to
today's row. This is what makes historical reporting correct.

### 8.3 Point-in-time surrogate-key lookup (fact load)

```sql
JOIN hr_dw.dim_employee d
  ON  d.employee_id = r.employee_id
  AND r.review_date BETWEEN d.start_date AND d.end_date
```

### 8.4 Date convention

| Layer | Convention | Current version |
|---|---|---|
| OLTP `employee_job_history` | half-open `[effective_start_date, effective_end_date)` — end = next version's start | `effective_end_date IS NULL` |
| DW `dim_employee` | inclusive `[start_date, end_date]` — Kimball convention, enables `BETWEEN` | `end_date = 9999-12-31`, `is_current = 1` |

ETL conversion: `end_date = COALESCE(effective_end_date - INTERVAL 1 DAY, '9999-12-31')`.

### 8.5 Initial load vs incremental change

All three cases run through **one procedure**, `sp_load_dim_employee_scd2(batch, employee_id)`,
which compares OLTP versions with warehouse versions by `(employee_id, start_date)`
(implemented in Phase 2 — full description in [`etl_design.md`](etl_design.md) §5):

- **Initial load:** all OLTP versions inserted at once; `version_number` from `ROW_NUMBER()`,
  `end_date` from `LEAD(start_date) − 1 day`, `row_hash` from `MD5(CONCAT_WS(...))`.
- **Incremental (ETL run):** a new OLTP version → expire the DW row (`end_date = change date − 1`,
  `is_current = 0`) and insert the new version; a changed `row_hash` on an existing version → a
  correction, overwritten in place; changed personal attributes → Type 1 overwrite on every
  version.
- **UI-triggered (Phase 4 requirement):** when HR changes an employee's department in Streamlit,
  one transaction (1) calls `hr_oltp.sp_change_employee_position`, which closes the current OLTP
  history row and inserts the new one, then (2) calls `hr_dw.sp_scd2_apply_employee(employee_id)`,
  which runs the same SCD2 procedure for that one employee immediately.

---

## 9. Surrogate keys and special members

### 9.1 Why surrogate keys

1. **History** — SCD Type 2 stores several versions of one employee; the natural key
   (`employee_id`) repeats, so it cannot be the primary key. A surrogate key gives every version
   its own identity.
2. **Independence from the source** — if OLTP keys are ever re-numbered or a second source
   system is added, the warehouse keys do not change.
3. **Performance** — fact rows join on compact integers.
4. **Special members** — a surrogate key can represent a situation that has no source row
   ("No Project Assigned").

### 9.2 The question: what happens when a fact row has no matching dimension row?

Kimball's general recommendation is to never leave a NULL foreign key in a fact table and to add
an "Unknown" special member to dimensions, conventionally with key 0 or −1. Applying that rule
blindly to every dimension was evaluated and rejected. Three options were compared:

| Option | Description |
|---|---|
| A | NULL foreign key when the dimension value is missing |
| B | An "Unknown" row with **sk = 0** in **every** dimension |
| C (**chosen**) | A special member **only where a relationship is genuinely optional**, key **−1**; every other foreign key mandatory, with failed lookups rejected and logged |

### 9.3 Which foreign keys can actually be missing

| Fact FK | Can it be missing? | Treatment |
|---|---|---|
| date_sk | Never — review_date is NOT NULL; dim_date covers 2015–2030 | NOT NULL FK, no special row |
| department_sk | Never — every job version has a department | NOT NULL FK, no special row |
| employee_sk | Only through a data error (review outside every job version) | NOT NULL FK, no special row; failures go to `etl_rejected_rows` |
| project_sk | **Yes, legitimately** — no project assignment that year | Special member **−1 "No Project Assigned"** |

### 9.4 Problems with option A — NULL foreign keys

- An inner join to `dim_project` silently **drops** every review without a project, so totals
  and averages are wrong without any error being raised.
- Every dashboard query would need `LEFT JOIN … COALESCE(…, 'No project')` — easy to forget once.
- `GROUP BY` produces an unlabeled NULL group in charts.

### 9.5 Problems with option B — an sk = 0 "Unknown" row in every dimension

1. **Inflated counts.** A fake employee row is counted by every headcount query:
   `COUNT(*) WHERE is_current = 1` returns 100,001 instead of 100,000. Every query would need an
   extra `WHERE employee_sk <> 0`, and forgetting it once produces a silent error.
2. **Distorted averages.** The fake row must hold *some* value in NOT NULL columns. Placeholder
   values such as `monthly_income = 0` or `job_level = 0` pull averages down whenever the row is
   included.
3. **Hidden data errors.** If a review cannot find its employee version, that is a real defect
   (for example, a review dated before the hire date). Loading it against "Unknown" makes the
   warehouse look complete while the error stays invisible.
4. **Meaningless SCD columns.** The fake row would need `start_date`, `end_date`, `is_current`,
   `version_number` and `row_hash` values that describe nothing.
5. **MySQL AUTO_INCREMENT behaviour with 0.** Inserting the value 0 into an AUTO_INCREMENT column
   does not store 0 — MySQL generates the next sequence number instead — unless the session
   `sql_mode` contains `NO_AUTO_VALUE_ON_ZERO`. The row would be stored correctly only if every
   script, tool and reload (for example, when copying the database to the cloud) remembered that
   setting; otherwise the "Unknown" row silently receives a normal key such as 1 and every fact
   pointing to 0 breaks its foreign key.

### 9.6 The chosen design (option C)

- **Only `dim_project` has a special member:** `project_sk = −1`, "No Project Assigned" — the
  only legitimately optional relationship. Fact rows therefore never contain NULL keys, and inner
  joins never drop reviews.
- **Key −1 instead of 0:** AUTO_INCREMENT never generates negative values, so −1 can be inserted
  explicitly with no `sql_mode` setting on any server. A negative key is also self-evidently "not
  a real project", and dashboards exclude it with a readable `WHERE project_sk > 0`. To allow −1,
  all warehouse surrogate keys are signed `INT` (maximum ≈ 2.1 billion — far beyond need); OLTP
  keys remain UNSIGNED.
- **Natural key 0 pins the special row.** The first implementation tried
  `CHECK ((project_sk = -1) = (project_id IS NULL))`; MySQL rejected it with
  *error 3818 — "Check constraint cannot refer to an auto-increment column"*. The rule is therefore
  enforced through the natural key: the special row has `project_id = 0` (OLTP project ids start
  at 1, so 0 can never collide), `project_id` is NOT NULL and UNIQUE (so exactly one special row
  can exist), and `CHECK ((project_id = 0) = (project_code = 'N/A'))` ties it to its label. This
  also simplifies the fact load: a review without a project is matched with
  `COALESCE(project_id, 0)`, with no special-case branch.
- **All other foreign keys are mandatory.** A failed lookup writes the source row to
  `etl_rejected_rows` with the reason, and the ETL run is flagged in `etl_batch_log`. Data errors
  are visible and fixable instead of hidden.
- **No late-arriving dimensions:** `sp_run_etl` always loads dimensions before facts, and the UI
  creates the `dim_employee` version immediately when an employee is onboarded or changed, so a
  valid review always finds its dimension rows.

---

## 10. How each required dashboard is answered

| Requirement (problem statement) | Source in the star |
|---|---|
| Year-over-year performance trends | fact × dim_date (calendar or fiscal year); `LAG()` for change vs previous year |
| Top-performing employees by department | fact × dim_department × dim_employee with `DENSE_RANK() OVER (PARTITION BY department ORDER BY avg rating, avg kpi)` — computed at query time so it respects dashboard filters |
| Employee attrition risk | dim_employee (attrition_flag, tenure, level, income band) + latest fact row (satisfaction, overtime, rating) |
| Project analysis (additional) | dim_project (delay_days, status, priority) × fact (kpi per project) |

---

## 11. ETL outline (implemented in Phase 2 — see [`etl_design.md`](etl_design.md))

| Procedure | Purpose | SQL techniques |
|---|---|---|
| `sp_load_dim_date` | generate 2015–2030 | recursive CTE |
| `sp_load_dim_department` | Type 1 | update changed rows, insert new ones (etl_design E7) |
| `sp_load_dim_project` | Type 1 + derived attributes, special member | CTE |
| `sp_load_dim_employee_scd2` | initial + incremental + single-employee SCD2 | `ROW_NUMBER()`, `LEAD()`, `MD5` row hash |
| `sp_scd2_apply_employee` | single-employee SCD2 from the UI, inside the app's transaction | calls the procedure above |
| `sp_load_fact_performance_reviews` | incremental fact load, point-in-time lookup, rejects | CTE, `NOT EXISTS` |
| `sp_run_etl` | orchestrates all steps, logs each one | error handler, transaction |

**ETL metadata tables** (operational, not part of the star):

| Table | Columns |
|---|---|
| `etl_batch_log` | log_id PK, batch_id, step_name, rows_affected, status, message, started_at, finished_at |
| `etl_rejected_rows` | reject_id PK, batch_id, source_table, source_key, reason, rejected_at |

---

## 12. Physical design

- Engine InnoDB, character set utf8mb4.
- **Foreign keys declared** from the fact to every dimension (integrity, and the star is visible in
  MySQL Workbench reverse engineering). The cost at ~477K rows is negligible.
- Surrogate keys: signed `INT` (§9); `date_sk` as `INT` YYYYMMDD.
- Indexes: fact FKs; fact (date_sk, department_sk); dim_employee (employee_id, start_date) UNIQUE
  and (employee_id, is_current); dim_project / dim_department natural keys UNIQUE.

---

## 13. Alternatives considered and rejected

| Alternative | Reason for rejection |
|---|---|
| Snowflake schema (dim_employee → dim_department FK) | More joins, slower queries, fragile SCD2 history, negligible storage saving (see §3) |
| SCD Type 3 (`previous_department` column) | Only one level of history; the problem statement requires Type 2 |
| SCD Type 6 (current values on every row) | Useful but adds complexity; can be added later without redesign |
| Unknown-member row (sk = 0) in every dimension | Inflates headcounts, distorts averages, hides data errors, needs `NO_AUTO_VALUE_ON_ZERO` in MySQL (see §9.5) |
| NULL `project_sk` | Inner joins silently drop reviews without a project |
| Separate `fact_engagement_surveys` (two fact tables) | Same grain as reviews; one star is simpler and matches the specification |
| Storing department ranks in the fact | Ranks depend on dashboard filters → computed with `DENSE_RANK()` at query time |
| Monthly snapshot fact | 100K × 84 months ≈ 8.4M rows with no business requirement |
| Project-allocation fact | Attrition risk (required "or" option) is fully covered; possible extension |
| Reviewer as a role-playing employee dimension | No required analysis needs it |
| PII (email, phone) in the warehouse | Not needed for analytics — data minimization |

---

## 14. Decision log

| ID | Decision | Alternatives | Rationale |
|---|---|---|---|
| D6 | Grain = one row per performance review | Monthly snapshot | Atomic grain; matches the business event |
| D7 | Survey measures inside `fact_performance_reviews` | Separate survey fact | Same grain; one star; essential for attrition risk |
| D8 | SCD2 tracks every job-history attribute (1 : 1 with OLTP versions) | Track a subset and merge versions | Simple, auditable, no gaps-and-islands merging |
| D9 | `dim_date` includes Indian fiscal year (April–March) | Calendar year only | Realistic for an Indian HR context |
| D10 | `dim_project` is SCD Type 1 | Type 2 | No required analysis needs project history |
| D11 | Special member only in `dim_project` (−1); other FKs mandatory, failures → `etl_rejected_rows` | sk = 0 in every dimension; NULL FKs | Correct headcounts, visible data errors, no AUTO_INCREMENT zero trap |
| D12 | Foreign keys declared in the warehouse | ETL-enforced only | Integrity and self-documenting schema; negligible cost |
| D13 | No PII in the warehouse | Copy all employee columns | Data minimization |
| D14 | Inclusive end dates in the warehouse, half-open in OLTP | Same convention in both | Kimball `BETWEEN` convention; conversion documented |
| D15 | Star schema, not snowflake | Snowflake (normalized dimensions) | Required by the problem statement; fewer joins; self-contained SCD2 history; storage saving ≈ 20 MB only (§3) |

---

## 15. Expected volumes

| Table | Rows |
|---|---|
| dim_date | ~5,844 |
| dim_department | 3 |
| dim_project | 2,000 + 1 special member |
| dim_employee | ≈ 309,000 (one row per job version; 308,986 generated) |
| fact_performance_reviews | ≈ 463,000 (one row per review; 463,015 generated after removing resubmissions) |

Estimated in §4 (300K / 477K); the generated figures come from `synthesis_report.md` and will be
confirmed by the ETL load.

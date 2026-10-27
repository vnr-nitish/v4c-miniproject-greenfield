# OLTP Database Design — `hr_oltp`

**Project:** Enterprise Employee Analytics & Data Warehouse System
**Phase:** 1 — Architecture, Modeling and Data Ingestion
**Status:** Approved design (v2)
**Diagram:** [`diagrams/oltp_er_diagram.drawio`](../diagrams/oltp_er_diagram.drawio)

---

## 1. Purpose and scope

The OLTP (Online Transaction Processing) database is the operational backend of the Streamlit
application. HR users onboard employees, create projects, assign people to projects and submit
performance reviews here. It is also the **single source of truth** from which the OLAP data
warehouse (`hr_dw`, star schema) is loaded by stored procedures.

Because every later layer — the ETL, the SCD Type 2 logic, the dashboards — depends on it, the
OLTP schema was designed from the data upward rather than from a list of column names.

Layered architecture:

```
IBM CSV (1,470 rows) ──► Python synthesizer (100,000 employees)
        ──► hr_staging  (raw, flat, no constraints, keeps source lineage)
        ──► hr_oltp     (this design: normalized 3NF/BCNF, fully constrained)
        ──► hr_dw       (Kimball star schema, surrogate keys, SCD Type 2)
        ──► Streamlit dashboards
```

---

## 2. Source dataset — data dictionary (all 35 columns)

**Dataset:** IBM HR Analytics Employee Attrition & Performance (Kaggle)
**File:** `data/raw/WA_Fn-UseC_-HR-Employee-Attrition.csv`
**Shape:** 1,470 rows × 35 columns · 0 null values · 0 duplicate rows · `EmployeeNumber` unique

| # | Column | Type | Values / range | Meaning |
|---|---|---|---|---|
| 1 | Age | int | 18 – 60 | Employee age in years at snapshot |
| 2 | Attrition | text | Yes / No (237 / 1,233) | Whether the employee left the company |
| 3 | BusinessTravel | text | Non-Travel, Travel_Rarely, Travel_Frequently | Travel frequency of the position |
| 4 | DailyRate | int | 102 – 1,499 | Daily rate (see finding F5 — noise) |
| 5 | Department | text | Human Resources, Research & Development, Sales | Department |
| 6 | DistanceFromHome | int | 1 – 29 | Distance from home to office (km) |
| 7 | Education | int | 1 – 5 | 1 Below College, 2 College, 3 Bachelor, 4 Master, 5 Doctor |
| 8 | EducationField | text | 6 values | Human Resources, Life Sciences, Marketing, Medical, Other, Technical Degree |
| 9 | EmployeeCount | int | always 1 | Constant |
| 10 | EmployeeNumber | int | 1 – 2,068 (unique) | Employee identifier |
| 11 | EnvironmentSatisfaction | int | 1 – 4 | 1 Low, 2 Medium, 3 High, 4 Very High |
| 12 | Gender | text | Male / Female (882 / 588) | Gender |
| 13 | HourlyRate | int | 30 – 100 | Hourly rate (noise) |
| 14 | JobInvolvement | int | 1 – 4 | 1 Low, 2 Medium, 3 High, 4 Very High |
| 15 | JobLevel | int | 1 – 5 | Seniority level |
| 16 | JobRole | text | 9 values | Healthcare Representative, Human Resources, Laboratory Technician, Manager, Manufacturing Director, Research Director, Research Scientist, Sales Executive, Sales Representative |
| 17 | JobSatisfaction | int | 1 – 4 | 1 Low, 2 Medium, 3 High, 4 Very High |
| 18 | MaritalStatus | text | Single, Married, Divorced | Marital status |
| 19 | MonthlyIncome | int | 1,009 – 19,999 | Monthly salary |
| 20 | MonthlyRate | int | 2,094 – 26,999 | Monthly rate (noise) |
| 21 | NumCompaniesWorked | int | 0 – 9 | Number of companies worked at (see F7) |
| 22 | Over18 | text | always Y | Constant |
| 23 | OverTime | text | Yes / No (416 / 1,054) | Works overtime |
| 24 | PercentSalaryHike | int | 11 – 25 | Last salary hike % |
| 25 | PerformanceRating | int | 3 – 4 only | Scale 1 Low, 2 Good, 3 Excellent, 4 Outstanding |
| 26 | RelationshipSatisfaction | int | 1 – 4 | 1 Low, 2 Medium, 3 High, 4 Very High |
| 27 | StandardHours | int | always 80 | Constant |
| 28 | StockOptionLevel | int | 0 – 3 | Stock option grant level |
| 29 | TotalWorkingYears | int | 0 – 40 | Total career experience |
| 30 | TrainingTimesLastYear | int | 0 – 6 | Trainings attended last year |
| 31 | WorkLifeBalance | int | 1 – 4 | 1 Bad, 2 Good, 3 Better, 4 Best |
| 32 | YearsAtCompany | int | 0 – 40 | Tenure at this company |
| 33 | YearsInCurrentRole | int | 0 – 18 | Years in current role |
| 34 | YearsSinceLastPromotion | int | 0 – 15 | Years since last promotion |
| 35 | YearsWithCurrManager | int | 0 – 17 | Years with current manager |

---

## 3. Design method

The schema was produced with the standard database-design lifecycle:

1. **Data profiling** — measure the real domains, distributions and rules in the data
   (script: [`analysis/profile_dataset.py`](../analysis/profile_dataset.py)).
2. **Functional-dependency analysis** — determine which attributes determine which others.
3. **Normalization** — 1NF → 2NF → 3NF → BCNF (Codd; Boyce–Codd).
4. **Temporal modeling** — effective-dated history tables, the source of SCD Type 2 in the warehouse.
5. **Physical design** — data types, keys, constraints and indexes sized for 100,000+ employees.

---

## 4. Profiling findings and the design decision each one drives

| # | Finding (measured on the 1,470 rows) | Design consequence |
|---|---|---|
| F1 | 8 of 9 job roles occur in exactly one department; **Manager occurs in all three**. Role ↔ Department is therefore many-to-many. | Bridge table `department_job_roles` with the **11 valid pairs**; a composite foreign key makes an invalid pair (e.g. Sales Executive in R&D) impossible. |
| F2 | Each role spans a fixed band of levels: Manager 3–5, Research Director 3–5, Healthcare Rep / Manufacturing Director / Sales Executive 2–4, Human Resources / Laboratory Technician / Research Scientist 1–3, Sales Representative 1–2. | `job_roles.min_job_level_id` / `max_job_level_id`, enforced by stored procedures. |
| F3 | Each job level has a salary band: L1 1,009–4,968 · L2 2,042–9,998 · L3 5,210–13,757 · L4 11,103–17,924 · L5 18,041–19,999. | `job_levels.salary_band_min` / `salary_band_max`. |
| F4 | **PerformanceRating fully determines the hike range**: rating 3 → 11–19 %, rating 4 → 20–25 %, no overlap. | A transitive dependency → `performance_rating_levels(hike_min_pct, hike_max_pct)`. |
| F5 | DailyRate, HourlyRate, MonthlyRate correlate 0.008, −0.016, 0.035 with MonthlyIncome; DailyRate × 20 ranges from 0.1× to 27× MonthlyIncome. | These columns are random noise → excluded from OLTP (retained in staging for lineage). |
| F6 | Tenure rules hold for all rows (0 violations): YearsInCurrentRole, YearsSinceLastPromotion, YearsWithCurrManager ≤ YearsAtCompany ≤ TotalWorkingYears; Age − TotalWorkingYears ≥ 18. | CHECK constraints and synthesizer generation rules. |
| F7 | **Data-quality issue:** NumCompaniesWorked = 0 for 197 employees who all have prior experience; = 1 for 474 employees with no prior experience. | Inconsistent field → documented cleaning rule produces `prior_companies_count`. |
| F8 | 440 level-1 employees have been "promoted" (YearsSinceLastPromotion < YearsAtCompany) although level 1 is the lowest. | A promotion is not always a level change → history must allow a pay-grade promotion within the same level. |
| F9 | Every Single employee has StockOptionLevel 0. | Artifact of IBM's fictional data; noted and preserved by the synthesizer. |
| F10 | Attrition is 30.5 % with OverTime vs 10.4 % without — the strongest single predictor. | Overtime is recorded **per year** to allow trend analysis. |
| F11 | BusinessTravel shows no dependency on JobRole. | It is a property of the person's position (history), not of the role. |

---

## 5. Column-by-column mapping (all 35)

Treatments: **Stored** · **Lookup** (value moved to a reference table, referenced by FK) ·
**Derived** (computed from stored data, never stored) · **Dropped** · **Lineage** (staging only).

| # | IBM column | Destination in `hr_oltp` | Treatment & reason |
|---|---|---|---|
| 1 | Age | `employees.date_of_birth` | Derived — a stored age becomes wrong over time |
| 2 | Attrition | `employees.exit_date`, `exit_reason_id` | Derived — Yes/No becomes *when* and *why* |
| 3 | BusinessTravel | `employee_job_history.business_travel` | Stored — changes with the position (F11) |
| 4 | DailyRate | staging only | Dropped — noise (F5) |
| 5 | Department | `departments` → `employee_job_history` | Lookup |
| 6 | DistanceFromHome | `employees.distance_from_home_km` | Stored |
| 7 | Education | `education_levels` → `employees` | Lookup (code → label) |
| 8 | EducationField | `education_fields` → `employees` | Lookup |
| 9 | EmployeeCount | — | Dropped — constant 1 |
| 10 | EmployeeNumber | `stg_employees.source_employee_number` | Lineage — each synthetic employee gets a new `employee_id` (PK) and `employee_code`; staging records which IBM row it was derived from |
| 11 | EnvironmentSatisfaction | `employee_surveys` | Stored yearly |
| 12 | Gender | `employees.gender` | Stored |
| 13 | HourlyRate | staging only | Dropped — noise (F5) |
| 14 | JobInvolvement | `employee_surveys` | Stored yearly |
| 15 | JobLevel | `job_levels` → `employee_job_history` | Lookup + history |
| 16 | JobRole | `job_roles` (via `department_job_roles`) → `employee_job_history` | Lookup + history |
| 17 | JobSatisfaction | `employee_surveys` | Stored yearly |
| 18 | MaritalStatus | `employees.marital_status` | Stored |
| 19 | MonthlyIncome | `employee_job_history.monthly_income` | Stored in history — drives SCD Type 2 |
| 20 | MonthlyRate | staging only | Dropped — noise (F5) |
| 21 | NumCompaniesWorked | `employees.prior_companies_count` | Stored after cleaning (F7) |
| 22 | Over18 | — | Dropped — replaced by rule CHECK `hire_date ≥ date_of_birth + 18 years` |
| 23 | OverTime | `performance_reviews.overtime_flag` | Stored per year (F10) |
| 24 | PercentSalaryHike | computed with `LAG()` over salary history | Derived — guideline kept in `performance_rating_levels` (F4) |
| 25 | PerformanceRating | `performance_rating_levels` → `performance_reviews` | Lookup + yearly |
| 26 | RelationshipSatisfaction | `employee_surveys` | Stored yearly |
| 27 | StandardHours | — | Dropped — constant 80 |
| 28 | StockOptionLevel | `employee_job_history.stock_option_level` | Stored in history |
| 29 | TotalWorkingYears | `employees.prior_experience_years` | Derived — total = prior experience + tenure |
| 30 | TrainingTimesLastYear | `performance_reviews.training_sessions` | Stored per year |
| 31 | WorkLifeBalance | `employee_surveys` | Stored yearly |
| 32 | YearsAtCompany | `employees.hire_date` | Derived |
| 33 | YearsInCurrentRole | start of current role version in history | Derived |
| 34 | YearsSinceLastPromotion | latest `Promotion` version in history | Derived |
| 35 | YearsWithCurrManager | start of current manager in history | Derived |

**Totals:** 20 stored (directly or via lookup) · 8 derived · 6 dropped · 1 lineage = **35**.

**Lossless decomposition:** the view `vw_employee_snapshot` reconstructs every meaningful IBM
column from the normalized tables. This is tested by rebuilding the original layout and comparing it.

---

## 6. Normalization walk-through

**Problems in the flat file**
- *Redundancy* — the text "Research & Development" is repeated 961 times.
- *Transitive dependencies* — lookup values depend on other non-key values.
- *Stale derived values* — Age, YearsAtCompany, etc. are wrong one day after the snapshot.
- *No history* — one row per employee cannot hold earlier departments, roles or salaries.

| Normal form | Applied as |
|---|---|
| **1NF** | Values are already atomic, but history is a *hidden repeating group* (an employee has many job versions) → moved to `employee_job_history`. |
| **2NF** | Yearly tables are keyed by (employee, year); person attributes would depend on only part of that key → they stay in `employees`. |
| **3NF** | Transitive dependencies removed: Education → label, JobLevel → salary band, JobRole → level range, Rating → hike range, ExitReason → voluntary flag, Department → code. |
| **BCNF** | In every table, every determinant is a candidate key. |
| **Derived data** | Never stored; computed by views or MySQL generated columns. |

---

## 7. Table specifications (14 tables)

Conventions: snake_case, plural table names, `<entity>_id` keys; constraint prefixes `pk_`, `fk_`,
`uq_`, `ck_`, `ix_`. All columns are NOT NULL unless marked NULL.

### 7.1 Reference (lookup) tables — 8

**`departments`** — organizational departments.
| Column | Type | Constraints |
|---|---|---|
| department_id | TINYINT UNSIGNED | PK |
| department_code | VARCHAR(10) | UQ (HR, RND, SAL) |
| department_name | VARCHAR(60) | UQ |

**`job_roles`** — job roles with their allowed level range (F2).
| Column | Type | Constraints |
|---|---|---|
| job_role_id | TINYINT UNSIGNED | PK |
| role_name | VARCHAR(60) | UQ |
| min_job_level_id | TINYINT UNSIGNED | FK → job_levels |
| max_job_level_id | TINYINT UNSIGNED | FK → job_levels; CHECK min ≤ max |

**`department_job_roles`** — bridge resolving the M:N between departments and roles (F1).
| Column | Type | Constraints |
|---|---|---|
| department_id | TINYINT UNSIGNED | PK, FK → departments |
| job_role_id | TINYINT UNSIGNED | PK, FK → job_roles |

**`job_levels`** — seniority levels and salary bands (F3).
| Column | Type | Constraints |
|---|---|---|
| job_level_id | TINYINT UNSIGNED | PK (1–5) |
| level_title | VARCHAR(30) | |
| salary_band_min | DECIMAL(10,2) | |
| salary_band_max | DECIMAL(10,2) | CHECK max > min |

**`education_levels`** — 1 Below College … 5 Doctor.
| Column | Type | Constraints |
|---|---|---|
| education_level_id | TINYINT UNSIGNED | PK (1–5) |
| level_name | VARCHAR(30) | UQ |

**`education_fields`** — field of study.
| Column | Type | Constraints |
|---|---|---|
| education_field_id | TINYINT UNSIGNED | PK |
| field_name | VARCHAR(60) | UQ |

**`performance_rating_levels`** — IBM's official 1–4 scale with salary-hike guideline (F4).
| Column | Type | Constraints |
|---|---|---|
| rating_id | TINYINT UNSIGNED | PK (1 Low, 2 Good, 3 Excellent, 4 Outstanding) |
| rating_label | VARCHAR(20) | UQ |
| hike_min_pct | DECIMAL(5,2) | |
| hike_max_pct | DECIMAL(5,2) | CHECK max ≥ min |

**`exit_reasons`** — why an employee left; separates voluntary from involuntary attrition.
| Column | Type | Constraints |
|---|---|---|
| exit_reason_id | TINYINT UNSIGNED | PK |
| reason_name | VARCHAR(60) | UQ |
| is_voluntary | BOOLEAN | |

### 7.2 Core tables — 6

**`employees`** — the person (no job data; job data lives in history).
| Column | Type | Constraints / notes |
|---|---|---|
| employee_id | INT UNSIGNED | PK, AUTO_INCREMENT |
| employee_code | CHAR(9) | UQ — business key, e.g. EMP000001 |
| first_name, last_name | VARCHAR(50) | |
| email | VARCHAR(120) | UQ |
| phone | VARCHAR(20) | |
| gender | VARCHAR(10) | CHECK IN ('Male','Female') |
| date_of_birth | DATE | |
| marital_status | VARCHAR(10) | CHECK IN ('Single','Married','Divorced') |
| education_level_id | TINYINT UNSIGNED | FK → education_levels |
| education_field_id | TINYINT UNSIGNED | FK → education_fields |
| distance_from_home_km | TINYINT UNSIGNED | CHECK 1–50 |
| prior_experience_years | TINYINT UNSIGNED | experience before joining |
| prior_companies_count | TINYINT UNSIGNED | cleaned (F7) |
| hire_date | DATE | CHECK hire_date ≥ date_of_birth + 18 years |
| exit_date | DATE NULL | CHECK exit_date ≥ hire_date |
| exit_reason_id | TINYINT UNSIGNED NULL | FK → exit_reasons; set if and only if exit_date is set |
| is_active | BOOLEAN | GENERATED: `exit_date IS NULL` |
| created_at, updated_at | DATETIME | audit |

**`employee_job_history`** — effective-dated job versions; the single source of truth for
department, role, level, manager and pay, and the source of SCD Type 2.
| Column | Type | Constraints / notes |
|---|---|---|
| history_id | INT UNSIGNED | PK |
| employee_id | INT UNSIGNED | FK → employees |
| department_id, job_role_id | TINYINT UNSIGNED | composite FK → department_job_roles |
| job_level_id | TINYINT UNSIGNED | FK → job_levels |
| manager_id | INT UNSIGNED NULL | FK → employees; CHECK manager_id ≠ employee_id |
| monthly_income | DECIMAL(10,2) | |
| stock_option_level | TINYINT UNSIGNED | CHECK 0–3 |
| business_travel | VARCHAR(20) | CHECK IN ('Non-Travel','Travel_Rarely','Travel_Frequently') |
| change_reason | VARCHAR(20) | CHECK IN ('Hire','Promotion','Transfer','Role Change','Salary Revision','Manager Change') |
| effective_start_date | DATE | UQ with employee_id |
| effective_end_date | DATE NULL | **exclusive** (half-open period `[start, end)`): equals the next version's start date; NULL = current version; CHECK end > start |
| current_version_key | INT UNSIGNED NULL | GENERATED: employee_id when end date is NULL, else NULL; **UNIQUE** → at most one current version per employee |
| created_at | DATETIME | audit |

**`employee_surveys`** — yearly engagement survey.
| Column | Type | Constraints |
|---|---|---|
| survey_id | INT UNSIGNED | PK |
| employee_id | INT UNSIGNED | FK → employees; UQ (employee_id, survey_year) |
| survey_year | SMALLINT UNSIGNED | |
| survey_date | DATE | |
| environment_satisfaction, job_satisfaction, relationship_satisfaction, job_involvement, work_life_balance | TINYINT | CHECK 1–4 each |
| created_at | DATETIME | |

**`projects`**
| Column | Type | Constraints / notes |
|---|---|---|
| project_id | SMALLINT UNSIGNED | PK |
| project_code | CHAR(8) | UQ, e.g. PRJ00001 |
| project_name | VARCHAR(120) | |
| description | VARCHAR(500) NULL | |
| department_id | TINYINT UNSIGNED | FK → departments (owning department) |
| project_manager_id | INT UNSIGNED NULL | FK → employees |
| start_date, planned_end_date | DATE | CHECK planned_end ≥ start |
| actual_end_date | DATE NULL | planned vs actual → delay / bottleneck analysis |
| budget | DECIMAL(14,2) | CHECK > 0 |
| status | VARCHAR(15) | CHECK IN ('Planned','Active','On Hold','Completed','Cancelled') |
| priority | VARCHAR(10) | CHECK IN ('Low','Medium','High','Critical') |
| created_at, updated_at | DATETIME | |

**`project_assignments`** — resolves M:N between employees and projects.
| Column | Type | Constraints / notes |
|---|---|---|
| assignment_id | INT UNSIGNED | PK |
| employee_id | INT UNSIGNED | FK → employees |
| project_id | SMALLINT UNSIGNED | FK → projects |
| project_role | VARCHAR(30) | |
| allocation_pct | TINYINT UNSIGNED | CHECK 1–100; summed over overlapping dates → over-allocation |
| start_date | DATE | UQ (employee_id, project_id, start_date) |
| end_date | DATE NULL | CHECK end ≥ start |
| created_at | DATETIME | |

**`performance_reviews`** — one annual review per employee.
| Column | Type | Constraints / notes |
|---|---|---|
| review_id | INT UNSIGNED | PK |
| employee_id | INT UNSIGNED | FK → employees; UQ (employee_id, review_year) |
| review_year | SMALLINT UNSIGNED | |
| review_date | DATE | |
| reviewer_id | INT UNSIGNED | FK → employees; CHECK reviewer_id ≠ employee_id |
| project_id | SMALLINT UNSIGNED NULL | FK → projects (primary project that year) |
| rating_id | TINYINT UNSIGNED | FK → performance_rating_levels |
| kpi_score | DECIMAL(5,2) | CHECK 0–100 (objective goal attainment, independent of rating) |
| training_sessions | TINYINT UNSIGNED | from TrainingTimesLastYear |
| overtime_flag | BOOLEAN | from OverTime (F10) |
| comments | VARCHAR(500) NULL | |
| created_at | DATETIME | |

---

## 8. Relationships and cardinalities

| Parent (one) | Child (many) | Cardinality | FK column(s) |
|---|---|---|---|
| education_levels | employees | 1 : 0..* | education_level_id |
| education_fields | employees | 1 : 0..* | education_field_id |
| exit_reasons | employees | 0..1 : 0..* | exit_reason_id |
| employees | employee_job_history | 1 : 1..* | employee_id (every employee has a Hire version) |
| employees (manager) | employee_job_history | 0..1 : 0..* | manager_id |
| department_job_roles | employee_job_history | 1 : 0..* | (department_id, job_role_id) |
| job_levels | employee_job_history | 1 : 0..* | job_level_id |
| departments | department_job_roles | 1 : 0..* | department_id |
| job_roles | department_job_roles | 1 : 0..* | job_role_id |
| job_levels | job_roles | 1 : 0..* (×2) | min_job_level_id, max_job_level_id |
| employees | employee_surveys | 1 : 0..* | employee_id |
| employees | performance_reviews | 1 : 0..* (×2) | employee_id, reviewer_id |
| projects | performance_reviews | 0..1 : 0..* | project_id |
| performance_rating_levels | performance_reviews | 1 : 0..* | rating_id |
| departments | projects | 1 : 0..* | department_id |
| employees (project manager) | projects | 0..1 : 0..* | project_manager_id |
| employees | project_assignments | 1 : 0..* | employee_id |
| projects | project_assignments | 1 : 0..* | project_id |

---

## 9. Data integrity — five layers

1. **Declarative constraints** — PK, FK, UNIQUE, CHECK (enforced by MySQL 8.0.16+).
2. **Generated-column unique index** — at most one current job version per employee, enforced by
   the database itself without triggers.
3. **Stored procedures** — multi-row rules that constraints cannot express: no overlapping history
   periods, role level range, salary band, first history version starts on the hire date.
   (MySQL has no exclusion constraints.)
4. **Python entity classes** — validate input before it reaches the database.
5. **Validation SQL script** — run after every ETL load.

All foreign keys use **ON DELETE RESTRICT**: HR history is never cascade-deleted; an employee
leaving is recorded with `exit_date`, not by deleting the row.

---

## 10. Physical design for 100,000+ employees

- **Smallest sufficient key types** — TINYINT UNSIGNED for lookups, SMALLINT UNSIGNED for projects,
  INT UNSIGNED for core entities. Smaller keys → smaller indexes on the large tables.
- **DECIMAL** for money (never FLOAT); **DATE** for business dates; DATETIME only for audit columns.
- **InnoDB**, character set **utf8mb4** (collation utf8mb4_0900_ai_ci).
- **Composite indexes** matching real access paths: `(employee_id, effective_start_date)`,
  `(review_year, rating_id)`, `(project_id, start_date)`, `(exit_date)`.
- Every foreign key is indexed (InnoDB requires it).

---

## 11. Alternatives considered and rejected

| Alternative | Reason for rejection |
|---|---|
| Lookup tables for the 1–4 survey scales | Would add 5 FKs — and therefore 5 indexes — on a ~450K-row table only to hold static labels. A CHECK constraint gives the same integrity at no cost; labels are applied in the warehouse. |
| One generic lookup table for all codes ("One True Lookup Table") | Known anti-pattern: foreign keys can no longer enforce type or domain. |
| Separate yearly salary-history table | Creates a new SCD2 version for every employee every year, making `dim_employee` larger than the fact table (Kimball anti-pattern). Salary changes are recorded on job events instead. |
| Separate reporting-line (manager) table | Forces the ETL to merge two independent timelines; manager is part of the position and kept in job history. |
| `departments.head_employee_id` | Creates a circular FK with employees; the department head is derivable (most senior Manager). |
| PII/contact split, office locations, clients, skills tables | No source data and not required by the problem statement; would add joins with no analytical value. |
| Storing age, tenure, status, is_current | Derived values — generated columns or views instead. |
| MySQL ENUM type | CHECK is standard SQL, easier to alter, and behaves identically on the cloud database. |
| Triggers for SCD2 | Not permitted for the application user on this server (`log_bin_trust_function_creators = OFF`) and harder to test; stored procedures are used instead. |

---

## 12. Decision log

| ID | Decision | Alternatives | Rationale |
|---|---|---|---|
| D1 | 14 tables: 8 reference + 6 core | 10-table v1 design | v1 did not trace all 35 columns and was not data-driven |
| D2 | `manager_id` inside `employee_job_history` | Separate reporting-line table | One timeline; simpler, correct SCD2 ETL |
| D3 | IBM official rating scale 1–4 | Custom 1–5 | Faithful to source; synthesizer adds ratings 1–2 for earlier years |
| D4 | Survey scales enforced by CHECK | Lookup tables | No index overhead on ~450K rows; labels applied in warehouse |
| D5 | Volume option A — 100,000 employees | 30,000 employees | Satisfies "scale to 100,000+ rows" literally |

---

## 13. Expected volumes (option A)

| Table | Approx. rows |
|---|---|
| employees | 100,000 |
| employee_job_history | ~300,000 |
| employee_surveys | ~450,000 |
| performance_reviews | ~450,000 |
| projects | ~2,000 |
| project_assignments | ~250,000 |
| **OLTP total** | **~1.5 million** |

Estimates; exact figures will be measured after the first load and recorded here.

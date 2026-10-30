# Enterprise Employee Analytics & Data Warehouse System
## Project Report — Phase 3 (Python OOP Backend) and Phase 4 (Streamlit Web Application)

**Live app:** https://v4c-miniproject-greenfield-rvuytjcpc39yzy8xnsjus4.streamlit.app
**Repository:** https://github.com/vnr-nitish/v4c-miniproject-greenfield

---

## Contents
1. Phase 3 — what was required
2. The architecture: four layers
3. `db_manager.py` — the Singleton database connection
4. Error handling — application exceptions
5. Entity classes
6. The Data Access Layer — the managers
7. Business rules in three layers (new stored procedures)
8. Analytics — the warehouse queries behind the dashboards
9. Speed and caching
10. Object-oriented principles — where each one is used
11. Testing the backend
12. Phase 4 — what was required
13. How the web application works
14. Look and design decisions
15. The pages, one by one
16. When a change appears on the dashboards
17. Testing the web application
18. Phase 3 and 4 decisions in one table

---

# PHASE 3 — PYTHON APPLICATION LOGIC (OOP)

## 1. What was required

| Requirement (problem statement) | How it was met |
|---|---|
| `db_manager.py`: a `DatabaseConnection` class using the **Singleton** pattern to manage MySQL connections safely | One instance per class, one shared connection pool, transactions that always commit or roll back |
| **Entity classes** for Employee, Project and Review that encapsulate attributes and behaviours | `src/models/`: Employee, Project, Review, plus Position and Assignment, which the forms need |
| **Manager classes** (e.g. EmployeeManager, AnalyticsManager) that **inherit** from the DB class to handle **CRUD** | `src/dal/`: seven managers, each `class …Manager(DatabaseConnection)` |
| **Robust try-except** error handling | One `transaction()` with try / except / finally used by every manager; MySQL errors turned into clear messages |
| "Moving away from functional programming" | Every database operation is a method of a class; the scripts are thin command-line front ends |

## 2. The architecture: four layers

```
 Streamlit pages (views/)       forms and charts only — no SQL
        │  build entities, call managers, show messages
        ▼
 Managers (src/dal/)            SQL, transactions, rules that need other records
        │  inherit from
        ▼
 DatabaseConnection             Singleton, ONE shared connection pool, transactions, error translation
        │
        ▼
 MySQL: hr_oltp (forms) · hr_dw (dashboards)

 Entity classes (src/models/)   validated objects passed between the layers; never touch the database
```

| Folder / file | Contents |
|---|---|
| `src/db_manager.py` | `DatabaseSettings`, `DatabaseConnection`, error translation |
| `src/exceptions.py` | the application's exception classes |
| `src/models/` | `Entity` (abstract base), `Employee`, `Position`, `Project`, `Assignment`, `Review` |
| `src/dal/` | `EmployeeManager`, `ProjectManager`, `ReviewManager`, `ReferenceDataManager`, `AnalyticsManager`, `EtlManager`, `SchemaManager` |

## 3. `db_manager.py` — the Singleton database connection

### 3.1 How the Singleton works

| Feature | How |
|---|---|
| **One instance per class** | `__new__` keeps one object per class in a dictionary: `EmployeeManager() is EmployeeManager()` is always true |
| **One shared pool** | the MySQL connection pool (5 connections) is created once, on first use, under a lock so two threads can't both create it |
| **Every manager is a Singleton for free** | because each manager inherits from `DatabaseConnection` |
| **Settings** | host, port, user, password from `.env` locally or from Streamlit secrets in the cloud — never in the code |

Result: the whole application, every web visitor included, uses at most five database connections.

### 3.2 Transactions: try / except / finally written once

```python
with self.transaction() as cur:      # borrows a connection from the pool
    cur.execute(...)                 # any number of statements and procedure calls
# success          -> COMMIT
# MySQL error      -> ROLLBACK, then raise a clear application error
# any other error  -> ROLLBACK, re-raise
# always           -> close the cursor, return the connection to the pool
```

Every manager method runs inside `transaction()`, so error handling cannot be forgotten.

### 3.3 Safety features added after the requirements review

| Feature | Problem it solves |
|---|---|
| **Wait for a free connection** (up to 10 s) | with several web visitors, all 5 connections can be busy; the driver would fail at once |
| **Retry a taken code** (`run_with_retry`) | two users onboarding at the same moment get the same next employee code; the second is retried with the next code |
| **Stale connections reconnected** | handled by the MySQL driver's pool |
| **Rollback session for tests** | tests use the real 100,000-employee data and roll everything back (§11) |

## 4. Error handling — application exceptions

```
HRAppError
├── ConfigurationError      a setting is missing or invalid
├── DatabaseError           the database could not do its job
├── ValidationError         input breaks a rule            → the user can fix the form
├── BusinessRuleError       the database refused the change
│   ├── DuplicateError          the record already exists
│   └── OverAllocationError     an assignment would take someone above 100 %
└── NotFoundError           the record does not exist
```

| MySQL error | Becomes | Message the user sees (example) |
|---|---|---|
| 1644 — a rule in a stored procedure | BusinessRuleError | "This job role does not exist in the selected department" |
| 1062 — duplicate key | DuplicateError | "This e-mail address is already used by another employee" |
| 1452 — foreign key on insert | BusinessRuleError | "The selected project does not exist" |
| 1451 — foreign key on delete | BusinessRuleError | "The project still has assignments — cancel it instead of deleting it" |
| 3819 — CHECK constraint | ValidationError | "An employee cannot review themselves" |
| 1406, 1264 … — bad value | ValidationError | "Data too long for column …" |
| anything else | DatabaseError | the MySQL message |

A form shows validation and business-rule problems as yellow warnings and database problems as a
red error — never a raw error number.

## 5. Entity classes (`src/models/`)

### 5.1 Design: immutable objects that check themselves (decision B1)

- Every entity is a **frozen dataclass** inheriting from the abstract class **`Entity`**.
- It **validates itself when it is created** — an invalid Employee cannot exist — and reports
  **every** broken rule at once, so a form can show all problems together.
- It **cannot be changed** afterwards: a change is a new object (`with_changes()`), validated again.
- `from_row()` builds any entity from a database row; `to_record()` turns it back into a dictionary.

### 5.2 The five entities

| Entity | Mirrors table | Rules it checks | Behaviour (methods / properties) |
|---|---|---|---|
| **Employee** | employees | code format EMP000123; names; e-mail and phone format; gender and marital status from the list; distance 1–50 km; experience 0–50 and previous employers 0–20 (0 exactly when no experience); **at least 18 on the hire date**; exit date and reason together, not before the hire | `full_name`, `is_active`, `age_on(day)`, `tenure_on(day)`, `total_experience_on(day)` |
| **Position** | employee_job_history | department, role, level 1–5, positive pay, stock 0–3, travel from the list | `changes_from(old)`, `is_promotion_from(old)`, `is_transfer_from(old)` |
| **Project** | projects | code PRJ00123; name; positive budget; status and priority from the list; planned end ≥ start; **an actual end exactly when Completed or Cancelled** | `is_closed`, `delay_days`, `is_delayed`, `is_overdue_on(day)`, `close(status, on)` |
| **Assignment** | project_assignments | allocation 1–100 %; end ≥ start | `is_open`, `active_on(day)`, `end(on)` |
| **Review** | performance_reviews | not a self-review; rating 1–4; KPI 0–100; training 0–20; review held in the review year or the next | `rating_label`, `is_outstanding`, `is_held_in_following_year` |

**Checked against the data:** all 1,377,241 existing database rows were loaded into their entity
classes — 0 rejected. (The first run rejected 153 surnames like "D'Alia" with a typographic
apostrophe; the rule was wrong, not the data, and was fixed.)

## 6. The Data Access Layer — the managers (`src/dal/`)

Each manager **inherits from `DatabaseConnection`** (so it is a Singleton and uses the shared pool)
and hides all SQL behind business methods.

| Manager | Create | Read | Update | Delete |
|---|---|---|---|---|
| **EmployeeManager** | `onboard()` — OLTP rows **and** the first warehouse version | `get()`, `get_by_code()`, `profile()`, `search()`, `current_position()`, `history()` | `update_details()` (personal data, Type 1); **`change_position()`** (SCD Type 2); `record_exit()` | never — leaving is recorded as an exit |
| **ProjectManager** | `create()`, `assign()` | `get()`, `search()`, `team()`, `assignments_for()`, `allocation_on()` | `update()` (closing ends assignments), `end_assignment()` | `delete()` only for an unused Planned project; `remove_assignment()` |
| **ReviewManager** | `submit()` | `get()`, `reviews_for()`, `suggest()` (form defaults) | — | `withdraw()` only before the warehouse load |
| **ReferenceDataManager** | — | departments, roles offered by a department, levels a role allows with salary bands, manager candidates, education, exit reasons, ratings (cached) | — | — |
| **AnalyticsManager** | — | every dashboard query (§8) | — | — |
| **EtlManager** | — | ETL batches, steps, rejected rows | runs the two ETL stages | — |
| **SchemaManager** | installs the numbered SQL scripts | scripts, server version, which tables hold data | re-installs views and procedures | refuses to drop tables that hold data |

**`change_position()`** — the Phase 4 SCD Type 2 requirement, in one transaction:
1. `sp_change_employee_position` closes the current OLTP job version and opens a new one;
2. `sp_scd2_apply_employee` expires the current warehouse row (end date = yesterday,
   `is_current = 0`) and inserts the new current row;
3. it returns both rows, so the page can show the step. Measured: 14 ms.

## 7. Business rules in three layers (decision B2)

| Layer | Checks | Why there |
|---|---|---|
| **Entity** (Python) | formats, ranges, date order | instant feedback in the form |
| **Stored procedure / manager** | role offered by the department, level inside the role's range, pay inside the level's band, manager is active, project open, reviewer employed on the review date | needs other tables; also protects users of MySQL Workbench |
| **Constraints** (database) | keys, foreign keys, CHECKs | the last guard, whatever the client |

New stored procedures (they do not commit; the manager commits the OLTP change and its warehouse
update together):

| Procedure | Does |
|---|---|
| `sp_validate_position` | the four position rules, written once, used by both procedures below |
| `sp_onboard_employee` | generates the next code, inserts the person and the first job version ("Hire") |
| `sp_record_exit` | records an exit, ends open assignments, refuses an exit before the last review |
| `sp_change_employee_position` (Phase 2) | now uses `sp_validate_position` |

## 8. Analytics — the warehouse queries behind the dashboards

`AnalyticsManager` reads only the star schema, with **parameterized CTEs and window functions** in the
manager (not views), so rankings are computed over exactly the rows the dashboard's filters select.

| Dashboard need | Method | SQL technique |
|---|---|---|
| Headline numbers | `overview()` | headcount, latest-year attrition, latest cycle vs the previous one |
| **Year-over-year trends** | `yoy_trends()` | **`LAG()`** over review cycles, per department with `PARTITION BY` |
| Attrition per fiscal year | `attrition_trend()` | point-in-time headcounts through **SCD Type 2**, `LAG()` |
| **Top performers** | `top_performers()` | **`DENSE_RANK() OVER (PARTITION BY department ORDER BY avg rating DESC, avg KPI DESC)`** |
| **Attrition risk** | `scored_workforce()`, `attrition_risk()`, `risk_summary()`, `risk_backtest()` | `ROW_NUMBER()` (latest review), `NTILE(4)` (pay quartile), `PERCENT_RANK()` |
| **Project bottlenecks** | `project_delivery()`, `bottleneck_projects()` | `RANK()`, grouping by priority / budget / department / year |

### The attrition-risk score (decision B8)

A transparent **points score**, not a black-box model. Each factor was measured on the warehouse: the
share of employees who left within a year of a review.

| Warning sign | Points | Left within a year: with → without |
|---|---:|---|
| Works overtime | 2 | 6.4 % → 2.0 % |
| Entry level (job level 1) | 2 | 5.5 % → 0.8–2.7 % |
| No stock options | 2 | 5.0 % → 1.4–3.7 % |
| Less than 2 years at the company | 1 | 4.9–5.1 % → 2.2–2.9 % |
| Low environment satisfaction | 1 | 5.4 % → 2.5–3.0 % |
| Low job satisfaction | 1 | 4.4 % → 2.3–3.3 % |
| Poor work-life balance | 1 | 6.8 % → 2.8–3.4 % |
| Low job involvement | 1 | 7.4 % → 1.7–3.9 % |
| Travels frequently | 1 | 4.7 % → 1.4–3.0 % |
| Lives more than 10 km away | 1 | 3.9–4.2 % → 2.6–2.9 % |
| Paid in the lower half of the level | 1 | 3.7–4.5 % → 1.7–2.8 % |

| Band | Score | Back-test: left within a year | Share of all leavers |
|---|---|---:|---:|
| Low | 0–2 | 0.72 % | 6.9 % |
| Medium | 3–5 | 1.84 % | 29.8 % |
| High | 6–7 | 7.96 % | 38.9 % |
| Very high | 8+ | **19.55 %** | 24.5 % |

**Deliberately left out:** marital status, age and gender. They are protected personal
characteristics; HR should not score people on them and cannot act on them.

## 9. Speed and caching

| Measured (cloud database) | Before | After |
|---|---|---|
| A write (onboard, job change, review) | 2–15 ms | — |
| A repeated dashboard query | 2–20 s | **under 1 ms** (cache) |
| The cache's "has the warehouse changed?" check | 0.45 s each, 3–5 per page | once a minute, or at once after a change made in the app |
| Manager drop-down list | ~2 s per click | cached 5 minutes |

How the cache stays correct: results are keyed by the **warehouse version** — the latest finished ETL
batch. Every change to the warehouse (an ETL run, an employee's SCD Type 2 update) is a logged batch,
so a new batch makes old results unreachable. Query tuning with `EXPLAIN ANALYZE` also cut
year-over-year trends from 5.2 s to 2.4 s and top performers from 7.9 s to 4.9 s.

## 10. Object-oriented principles — where each one is used

| Principle | Where |
|---|---|
| **Abstraction** | `Entity` is an abstract base class — `validation_errors()` is abstract; managers expose business operations (`onboard`, `change_position`) and hide SQL and transactions |
| **Encapsulation** | entities are frozen and validate themselves, so their state can never be invalid; SQL lives only inside managers; callers see application exceptions, not MySQL errors |
| **Inheritance** | seven managers inherit connection handling, transactions, error translation, retries and the Singleton from `DatabaseConnection`; five entities inherit validation and conversion from `Entity` |
| **Polymorphism** | `Entity` calls each subclass's own `_normalize()` and `validation_errors()`; `from_row()` builds any entity; every manager uses the same `transaction()` |
| **Singleton** | `DatabaseConnection.__new__` — one instance per class, one shared pool |

## 11. Testing the backend

| Test file | Tests | Covers |
|---|---:|---|
| `tests/test_models.py` | 72 (no database) | every entity rule, immutability, re-validation, error translation, analytics filters |
| `tests/test_managers.py` | 51 (100,000-employee data) | Singletons on one pool; onboarding; **department change = SCD Type 2 step**; exits; projects; over-allocation; reviews; every analytics query checked against an independent SQL query; caching; busy pool; code retry |
| `tests/test_schema_manager.py` | 6 | the SQL splitter; the guard against dropping loaded tables |

**Rollback session:** write tests run inside `rollback_session()` — every manager shares one
connection and everything is rolled back, so the real data is used and never changed. A test proves
it, and after the full suite every table's checksum equals the one before.

---

# PHASE 4 — STREAMLIT WEB APPLICATION

## 12. What was required

| Requirement | Where in the app |
|---|---|
| Forms to **onboard employees, create projects, submit reviews** | Onboard employee · Projects · Performance review pages |
| **Changing an employee's department triggers SCD Type 2** in the warehouse | Employee 360 → Change position: shows the expired and the new warehouse row side by side |
| Connect directly to the **data warehouse** (star schema) | every analytics page reads `hr_dw` through `AnalyticsManager` |
| Interactive **Plotly** charts: **year-over-year trends** | Performance trends page |
| **Top performers by department** with **DENSE_RANK()** | Top performers page (the SQL is shown on the page) |
| **Attrition risk or project bottlenecks** | **both** — Attrition risk and Project bottlenecks pages |
| Replace the command-line interface | everyday actions in the app, including a **Run ETL** button |

## 13. How the web application works

```
 Browser ──► Streamlit app (Streamlit Community Cloud)
               app.py         navigation · sidebar filters · health check · background warm-up
               views/*.py     one file per page — widgets and charts, no SQL
               src/ui/        theme, chart builders, the shared position picker, helpers
                    │ calls
                    ▼
               src/dal/ managers ──► MySQL on Aiven: hr_oltp (forms) · hr_dw (dashboards)
```

- **Every click re-runs the page** from top to bottom (Streamlit's model) — caching makes that fast.
- **Forms write through managers:** the page builds an entity (which validates itself), then calls
  the manager, which saves everything in one transaction.
- **Dashboards read the warehouse only.**
- **Messages:** yellow = something you can fix; red = a database problem.
- **Background warm-up:** when the app starts, a thread computes the slow company-wide analytics so
  the first visitor does not wait.
- **Passcode:** on the public site, data entry and the ETL button are unlocked with the editor
  passcode (the `APP_PASSCODE` secret); dashboards are open to everyone.

## 14. Look and design decisions

| ID | Decision | Why |
|---|---|---|
| U1 | Dark "command centre" theme with a gradient header and colour-coded tiles | a distinctive, consistent look |
| U3 | Data entry behind a passcode on the public site | nobody can change data without it |
| U5 | **Colours carry meaning**: one colour per department everywhere, risk bands green → red, priorities likewise | replaced a first design that used one purple everywhere |
| U7 | Background warm-up of the analytics cache | the first visitor doesn't wait for the risk score |
| U8 | **3D charts replaced by 2D charts** after a team review | 3D was hard to read precisely and to explain; each 2D chart answers one question |
| — | One chart per question, plus the exact table where it matters | charts show the pattern, tables give the values |

## 15. The pages, one by one

### Sidebar: navigation and filters
Four sections — Overview, Data entry, Analytics (warehouse), Operations — and three **filters**
(Department, Job level, Review cycle) that apply to every analytics page at once.

### 15.1 Command centre (Overview)

| Element | What it shows | How it is calculated |
|---|---|---|
| Active headcount (83,880) | people employed today | employees with no exit date (100,000 ever; 16,122 left) |
| Attrition FY2025-26 (2.59 %) | share who left in the last complete fiscal year | 2,153 leavers ÷ average headcount ((82,632 + 83,835) ÷ 2) |
| Avg rating FY2025-26 (3.155) | average rating of the latest review cycle | average over 83,188 reviews; = 3 + share of 4s |
| Avg KPI score (80.12) | average KPI of the same cycle | sum of KPI ÷ number of reviews |
| Outstanding reviews (15.5 %) | share rated 4 | outstanding ÷ all reviews in the cycle |
| Open projects (332) | not finished | status Planned, Active or On Hold |
| Overdue projects (89) | open and past the planned end | planned end before today — grows as days pass |
| ▲ / ▼ under a tile | change vs the previous year | `LAG()`; green = good, red = bad |

Charts: a **treemap** of headcount by department → role, coloured by the share at high risk (click to
zoom); a **donut** of the four risk bands; **bars** of headcount per department; **stacked bars** of
employees by years at the company, split by risk band.

### 15.2 Onboard employee
1. **Position first** (each choice narrows the next): Department → only the roles it offers → only
   the levels the role allows → monthly income slider limited to the level's salary band → manager
   (active employees of the department at a higher level) → travel, stock options.
2. **Person:** names, e-mail, phone, gender, marital status, date of birth, hire date, distance,
   education, experience, previous employers.
3. **Onboard:** the Employee entity lists every broken rule under its field; `onboard()` saves the
   person, the first job version and the **first warehouse row** in one transaction; success shows
   the new code, the warehouse row, and **balloons** (only on a successful hire).

### 15.3 Employee 360 · job change
- **Find:** type an employee code or click an example (people with the longest history).
- **Career timeline:** one bar per SCD Type 2 version, coloured by department, plus the version table.
- **Change position:** the same position picker, pre-filled; effective date (today or earlier, one
  change per day); reason chosen or derived (Promotion > Transfer > Role Change > Manager Change >
  Salary Revision). **Apply change** → the page shows the **expired** warehouse row (red) and the
  **new current** row (green) side by side, and the timeline with the new bar.
- **Reviews · projects · exit:** the person's reviews and assignments; **record an exit** (ends open
  assignments, marks the person as a leaver in the warehouse).

### 15.4 Projects

| Tab | What you can do |
|---|---|
| Browse & close | search by code or name, filter by status; click a project to see its team; close it as Completed or Cancelled (open assignments end) |
| Create | owning department, project manager, name, dates, budget, priority, status; the next PRJ code is generated |
| Assign people | project, employee code, role, allocation %, start date; a warning if the person would go above 100 %, with "assign anyway" |

### 15.5 Performance review
Employee code + performance year → the page suggests the **reviewer** (the manager at the end of
that year) and the **main project**; the form takes rating, KPI, training, overtime, the review date
and comments. Refused with a message: a second review for the same year, a future date, a person who
joined after that year, a reviewer not employed on that date, self-review.

### 15.6 Performance trends (year-over-year)
Metric buttons (KPI, rating, outstanding %, overtime %, satisfaction, training); **compare
departments** (one line each); tiles with the change against the previous cycle; the `LAG()` table;
a **heatmap** of a metric by job role × review cycle.

### 15.7 Top performers (DENSE_RANK)
Controls: review cycles, ranks per department, minimum number of reviews, current staff only.
**Medal cards** 🥇🥈🥉 per department (with "+N tied"), a **leaderboard chart**, a table per department
with rank and tie count, and the actual **SQL** shown on the page.

### 15.8 Attrition risk
A **heatmap** of the share at high risk by job level × years at the company; employees per band per
department; which warning signs the at-risk people share; the **at-risk list** — click a person to see
their score and factors; the **back-test** evidence; **attrition per fiscal year**.

### 15.9 Project bottlenecks
A **bubble chart** of overdue open projects (days overdue × team overtime; size = budget; colour =
priority) — top right = the real bottlenecks; the overdue list; % of completed projects finished late
and the average delay by priority, budget band, department or year.

### 15.10 ETL & data health (Operations)
The current warehouse version; **Run warehouse ETL** (with every step and row count shown live); the
recent batches with their steps and any rejected rows.

## 16. When a change appears on the dashboards

| Action | OLTP database | Warehouse / dashboards |
|---|---|---|
| Onboard an employee | at once | **at once** (warehouse version 1) |
| Change position · record an exit | at once | **at once** (SCD Type 2 step / leaver flag) |
| Create / close a project, assign, submit a review | at once | after **Run warehouse ETL** — the warehouse is loaded in batches (decision B5) |

## 17. Testing the web application

`tests/test_app.py` — **17 tests** with Streamlit's `AppTest`: every page renders against the real
data without an error; the app shows its three filters; invalid onboarding shows field messages; every
data-entry page renders its passcode form when a passcode is set. The UI tests run both against the
local database and against the cloud database used by the live site. Whole project: **228 tests,
all passing**.

## 18. Phase 3 and 4 decisions in one table

| ID | Decision | Instead of | Why |
|---|---|---|---|
| B1 | Frozen dataclasses on an abstract `Entity`, validated on creation | private attributes with setters | an invalid object cannot exist; every rule reported at once |
| B2 | Onboarding and position rules in shared stored procedures | rules only in Python | one place, also protects Workbench users |
| B3 | Managers inherit directly from `DatabaseConnection` | an extra base layer | exactly what the problem statement asks |
| B4 | Entities for single records, DataFrames for lists | entities everywhere | Streamlit tables and Plotly take DataFrames |
| B5 | Employee changes reach the warehouse at once; reviews and projects on the next ETL | load the fact after every review | SCD2 needs the version on the day; a warehouse loads in batches |
| B6 | Tests in a rolled-back session | a separate test database | real 100K data, nothing ever saved |
| B7 | Analytics SQL in the manager, not in views | warehouse views | rankings follow the filters |
| B8 | Rule-based, back-tested risk score | a machine-learning model | transparent and explainable |
| B9 | Marital status, age and gender excluded from the score | use every predictor | protected characteristics; HR cannot act on them |
| B12 | Cache keyed by the warehouse version | no cache | repeated dashboards in under 1 ms |
| B18–B20 | Wait for a busy pool; retry a taken code; `SchemaManager` class | fail at once; function scripts | several users at once; nothing outside the Singleton |
| U8 | 2D charts instead of 3D | 3D views | easier to read and to explain |

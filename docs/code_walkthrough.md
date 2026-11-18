# Code walkthrough: how the project works, and where the code is

This guide follows the data through the code, with a link to the exact line for every step. Open it in VS Code
(preview) or on GitHub and click a link to jump to that line. The code itself has short comments at the same
places; look for lines starting with `# OOP (…)`, `# HOW …` and `# Step …`.

## 1. The layers

```
Browser
  │
views/*.py              Streamlit pages: forms and charts. No SQL here.
  │  builds objects, asks for data
src/models/*.py         Employee, Position, Project, Assignment, Review: objects that check themselves
  │
src/dal/*_manager.py    EmployeeManager, ProjectManager, ReviewManager, AnalyticsManager, EtlManager …
  │  all inherit from
src/db_manager.py       DatabaseConnection: Singleton, one connection pool, transactions, error messages
  │  SQL statements and CALL sp_… (stored procedures)
MySQL                   hr_staging ──► hr_oltp (the forms' tables) ──► hr_dw (the dashboards' star schema)
```

Each layer only talks to the one below it. That's why a page never contains SQL, and why every database
change goes through the same transaction and error handling.

## 2. What happens when you click "Onboard"

| # | What happens | Where |
|---|---|---|
| 1 | The page draws the form. The position part narrows each choice by the one before it (department → role → level → pay → manager). The drop-down lists come from the lookup tables through `ReferenceDataManager`. | [onboard.py:25](../views/onboard.py#L25), [forms.py:28](../src/ui/forms.py#L28) |
| 2 | You click **Onboard**. The page builds an `Employee` object from what you typed. | [onboard.py:55](../views/onboard.py#L55) |
| 3 | The object checks itself as soon as it's created: names, e-mail, phone, age 18+ at hire, and so on. If a rule is broken it raises `ValidationError`, the page lists every problem, and **nothing is sent to the database**. | [base.py:34](../src/models/base.py#L34), [employee.py:65](../src/models/employee.py#L65) |
| 4 | The page calls `EmployeeManager().onboard(employee, position)`. Only the manager talks to the database. | [employee_manager.py:71](../src/dal/employee_manager.py#L71) |
| 5 | The manager opens **one transaction**. | [db_manager.py:300](../src/db_manager.py#L300) |
| 6 | **OLTP step:** it calls the stored procedure `sp_onboard_employee`. The procedure checks the hire date and the position rules (role offered in that department, level allowed, pay inside the band, manager still employed), makes the next code (EMP + 6 digits), and inserts one row into `hr_oltp.employees` and one into `hr_oltp.employee_job_history` with the reason "Hire". | [employee_manager.py:88](../src/dal/employee_manager.py#L88), [07_oltp_procedures.sql:672](../sql/07_oltp_procedures.sql#L672), [07_oltp_procedures.sql:630](../sql/07_oltp_procedures.sql#L630) |
| 7 | **Warehouse step, same transaction:** it calls `sp_scd2_apply_employee`, which logs a batch in `hr_dw.etl_batch_log` and inserts the employee's first `hr_dw.dim_employee` row (version 1, `is_current` = 1). | [employee_manager.py:96](../src/dal/employee_manager.py#L96), [08_dw_procedures.sql:542](../sql/08_dw_procedures.sql#L542), [08_dw_procedures.sql:294](../sql/08_dw_procedures.sql#L294) |
| 8 | **COMMIT** saves both steps together. If anything fails, **ROLLBACK** undoes both, so there is never a half-saved employee. A MySQL error becomes a readable message, such as "This e-mail address is already used by another employee". | [db_manager.py:328](../src/db_manager.py#L328), [db_manager.py:75](../src/db_manager.py#L75) |
| 9 | Two people saving at the same moment could get the same new code; the second save is simply tried again with the next code. | [db_manager.py:277](../src/db_manager.py#L277) |
| 10 | Back on the page: `data_changed()` tells the dashboards a new warehouse batch exists, and a pop-up shows the new code and the `dim_employee` row. | [onboard.py:66](../views/onboard.py#L66), [common.py:55](../src/ui/common.py#L55) |

**In one sentence for the trainer:** "When I click Onboard, the page builds an Employee object that validates
itself; EmployeeManager then opens one transaction and calls two stored procedures, one writing the OLTP tables
and one writing the first SCD Type 2 row in the warehouse, and both are committed together."

### The other forms

| Page and button | Manager method | Written to hr_oltp | Warehouse |
|---|---|---|---|
| Employee 360 › Update › Position › **Apply change** | [employee_manager.py:226](../src/dal/employee_manager.py#L226) | `sp_change_employee_position` closes the current job version and inserts the new one ([07_oltp_procedures.sql:547](../sql/07_oltp_procedures.sql#L547)) | Same transaction: the old `dim_employee` row expires (`end_date`, `is_current` = 0) and a new current row is inserted. This is SCD Type 2. |
| Employee 360 › Update › Personal details | [employee_manager.py:200](../src/dal/employee_manager.py#L200) | `UPDATE employees` | Same transaction: corrected on every version (SCD Type 1) |
| Employee 360 › **Exit** | [employee_manager.py:259](../src/dal/employee_manager.py#L259) | `sp_record_exit`: exit date and reason, open assignments ended | Same transaction |
| Projects › create, edit, assign | [project_manager.py:32](../src/dal/project_manager.py#L32), [project_manager.py:164](../src/dal/project_manager.py#L164) | `projects`, `project_assignments` | At the next ETL run |
| Performance review › **Submit** | [review_manager.py:29](../src/dal/review_manager.py#L29) | `performance_reviews` | At the next ETL run |
| ETL & data health › **Run warehouse ETL** | [etl_manager.py:29](../src/dal/etl_manager.py#L29) | — | `sp_run_etl`: all dimensions, SCD Type 2, then the new fact rows ([08_dw_procedures.sql:557](../sql/08_dw_procedures.sql#L557)) |

## 3. How a page gets its data (example: Performance trends)

| # | What happens | Where |
|---|---|---|
| 1 | The page reads its filters and asks the manager: `am.yoy_trends(filters)`. | [trends.py:21](../views/trends.py#L21) |
| 2 | The method builds one SQL query on the star schema: a CTE groups the reviews per cycle, and the window function `LAG()` compares each cycle with the one before it. | [analytics_manager.py:453](../src/dal/analytics_manager.py#L453) |
| 3 | Every analytics method goes through the cache. The warehouse "version" is the newest finished batch plus today's date, so a new save or ETL run makes old results invisible. | [analytics_manager.py:279](../src/dal/analytics_manager.py#L279), [analytics_manager.py:235](../src/dal/analytics_manager.py#L235) |
| 4 | Lookup order: memory first, then the results saved in `hr_dw.analytics_snapshot`, and only then the SQL itself. | [analytics_manager.py:290](../src/dal/analytics_manager.py#L290) |
| 5 | The SQL runs through `fetch_frame()`, which returns a pandas DataFrame. | [db_manager.py:419](../src/db_manager.py#L419) |
| 6 | The page draws the tiles, the Plotly line chart and the table. | [trends.py:25](../views/trends.py#L25), [charts.py:56](../src/ui/charts.py#L56) |
| 7 | In the background, a thread started by `app.py` restores the saved results when the app starts and recomputes the default views when the version changes, so visitors don't wait. | [app.py:32](../app.py#L32), [analytics_manager.py:328](../src/dal/analytics_manager.py#L328) |

| Dashboard page | Manager method | SQL technique |
|---|---|---|
| Command centre | [analytics_manager.py:406](../src/dal/analytics_manager.py#L406) | Combines the methods below |
| Performance trends | [analytics_manager.py:453](../src/dal/analytics_manager.py#L453) | CTE + `LAG()` |
| Top performers | [analytics_manager.py:564](../src/dal/analytics_manager.py#L564) | `DENSE_RANK()` per department |
| Attrition risk | [analytics_manager.py:621](../src/dal/analytics_manager.py#L621), [analytics_manager.py:643](../src/dal/analytics_manager.py#L643) | `ROW_NUMBER()`, `NTILE()`, `PERCENT_RANK()` |
| Project bottlenecks | [analytics_manager.py:728](../src/dal/analytics_manager.py#L728) | `RANK()` |
| ETL & data health | [etl_manager.py:39](../src/dal/etl_manager.py#L39) | Reads `etl_batch_log` and `etl_rejected_rows` |

The data-entry pages read the OLTP tables the same way, through their managers: Employee 360 reads the
employee, their current job (view `vw_employee_current`), their warehouse versions and their reviews on one
connection ([employee_360.py:24](../views/employee_360.py#L24)).

## 4. How the 6 tables were generated (synthesizer)

Command: `python -m src.synthesizer.run` (about 3 minutes). The pipeline runs the generators in this order,
because each table needs the ones before it ([run.py:56](../src/synthesizer/run.py#L56)):

| # | Generator | Staging table | OLTP table | How |
|---|---|---|---|---|
| 1 | [employees.py:39](../src/synthesizer/employees.py#L39) | `stg_employees` | `employees` | 1,470 IBM rows become 100,000 (see below) |
| 2 | [job_history.py:40](../src/synthesizer/job_history.py#L40) | `stg_job_history` | `employee_job_history` | IBM's "years since last promotion / in current role / with current manager" become dated versions: the SCD Type 2 history |
| 3 | [projects.py:20](../src/synthesizer/projects.py#L20) | `stg_projects` | `projects` | 2,000 projects; Faker writes the names and descriptions |
| 4 | [projects.py:107](../src/synthesizer/projects.py#L107) | `stg_assignments` | `project_assignments` | Who worked on which project, when, and with what share of their time |
| 5 | [performance.py:93](../src/synthesizer/performance.py#L93) | `stg_surveys` | `employee_surveys` | One survey per employee per year worked, 2019–2025 |
| 6 | [performance.py:117](../src/synthesizer/performance.py#L117) | `stg_reviews` | `performance_reviews` | One review per employee per year; the reviewer is their manager on that date |

**How 1,470 rows become 100,000** ([employees.py:42](../src/synthesizer/employees.py#L42)):
1. **Stratified sampling:** IBM rows are copied so every group (stayed or left, department, role, level) keeps
   exactly its share; each IBM person is copied about 68 times ([employees.py:55](../src/synthesizer/employees.py#L55)).
2. **Jitter:** each copy's pay (±7 %, inside its level's band), age (±1 year) and distance (±2 km) change a
   little, so copies aren't identical ([employees.py:72](../src/synthesizer/employees.py#L72)).
3. **Dates:** IBM's "years at company" and age become a hire date and a birth date that reproduce them
   exactly ([employees.py:89](../src/synthesizer/employees.py#L89)).
4. **Identity:** a new name, phone number and e-mail for every copy ([employees.py:141](../src/synthesizer/employees.py#L141)).

**Where Faker is used** (only for text that has to look real):
- Created once, seeded with 42 and set to Indian names (`en_IN`): [run.py:41](../src/synthesizer/run.py#L41).
- First names, last names and phone numbers: [employees.py:141](../src/synthesizer/employees.py#L141). Pools of 3,000
  names and 5,000 numbers are made once, then picked at random, which is faster than calling Faker 100,000 times.
- Project names (`catch_phrase`) and descriptions (`bs`): [projects.py:72](../src/synthesizer/projects.py#L72).
- Everything else (pay, dates, ratings, KPIs) comes from one seeded numpy random generator, so the same seed
  always gives exactly the same data.

**After generating:** every rule is checked, with 0 violations required ([validator.py:37](../src/synthesizer/validator.py#L37));
small, counted data-quality problems are planted for the ETL to clean ([quality.py:17](../src/synthesizer/quality.py#L17));
and the tables are inserted into `hr_staging`, 5,000 rows per batch ([loader.py:28](../src/synthesizer/loader.py#L28)).

**Why it's fast with 100,000 rows:** the generators work on whole columns at once with numpy and pandas
(vectorized), instead of a Python loop over 100,000 employees. Plain Python loops appear only for small things:
the Faker pools, the 5,000-row batches, and the list of generators.

## 5. The SQL scripts, and how they got to the cloud

| Script | What it creates |
|---|---|
| `00_create_schemas.sql` | The three databases: `hr_staging`, `hr_oltp`, `hr_dw` |
| `01_oltp_ddl.sql` | The 14 OLTP tables, with primary keys, foreign keys and checks |
| `02_oltp_reference_data.sql` | The lookup rows: departments, roles, levels, ratings, exit reasons … |
| `03_oltp_views.sql` | Views such as `vw_employee_current` and `vw_employee_snapshot` |
| `04_dw_ddl.sql` | The star schema: 1 fact table, 4 dimensions, and 3 support tables |
| `05_dw_seed.sql` | The "no project" row of `dim_project` |
| `06_staging_ddl.sql` | The 6 staging tables (`stg_…`) |
| `07_oltp_procedures.sql` | Stage 1 of the ETL and the procedures the forms use |
| `08_dw_procedures.sql` | Stage 2 of the ETL: dimensions, SCD Type 2, fact table, logging |

**How the scripts are run:** `python scripts/setup_database.py` ([setup_database.py:29](../scripts/setup_database.py#L29)) uses
`SchemaManager` ([schema_manager.py:193](../src/dal/schema_manager.py#L193)), which reads the files in number order,
splits each into statements and runs them. It refuses to drop tables that already hold data unless `--force`
is given. MySQL Workbench is where you can look at the databases and run queries by hand; the project itself
runs the scripts through this command.

**The order on any database:**
1. `python scripts/setup_database.py`: schemas, tables, views and procedures.
2. `python -m src.synthesizer.run`: the 6 generated tables into `hr_staging`.
3. `python scripts/run_etl.py --migrate`: Stage 1, `sp_migrate_from_staging` (cleans, deduplicates, logs every fix) into `hr_oltp`, then Stage 2, `sp_run_etl`, into `hr_dw` ([run_etl.py:41](../scripts/run_etl.py#L41)).

**Local MySQL and the Aiven cloud use the same code.** Only the settings in `.env` differ: host, port, user and
password ([db_manager.py:111](../src/db_manager.py#L111)). Pointing `.env` at Aiven and running
`setup_database.py` built the cloud schemas. The free Aiven plan (1 CPU) couldn't finish the hour-long migration
transaction, so the finished OLTP and warehouse rows were copied from the local database with
`mysqldump --no-create-info` into `mysql`, and checked table by table: identical data, seed 42 (README, "Deploy").
On the live site the same settings come from Streamlit Secrets instead of `.env`.

## 6. The OOP concepts, and where to point

| Concept | Where | What to say |
|---|---|---|
| Class and object | [employee.py:29](../src/models/employee.py#L29) | An Employee object holds one employee's data and behaviour, such as `full_name` and `age_on()` |
| Encapsulation | [base.py:29](../src/models/base.py#L29) | Entities are frozen, so nobody can change their data after creation. SQL stays inside the managers. |
| Inheritance | [employee_manager.py:65](../src/dal/employee_manager.py#L65) | Every manager *is a* DatabaseConnection and reuses its pool, transactions and error handling. Entities inherit from `Entity`, generators from `BaseGenerator`. |
| Abstraction | [base.py:29](../src/models/base.py#L29), [base.py:15](../src/synthesizer/base.py#L15) | Abstract classes with `@abstractmethod`: you can't create an "Entity", only an Employee, Project, … |
| Polymorphism | [employee.py:65](../src/models/employee.py#L65) | Each entity has its own `validation_errors()`, and each generator its own `generate()`; the caller doesn't need to know which one it has |
| Singleton | [db_manager.py:157](../src/db_manager.py#L157) | `__new__` returns the existing object, so the whole app shares one connection pool |
| Error handling | [db_manager.py:75](../src/db_manager.py#L75), [common.py:104](../src/ui/common.py#L104), `src/exceptions.py` | try/except at every layer; MySQL errors become the app's own exceptions with readable messages |
| Context managers | [db_manager.py:300](../src/db_manager.py#L300) | `with self.transaction() as cur:` always commits or rolls back, and always returns the connection |

## 7. Quick answers for the evaluation

- **Where is the SQL for the forms?** In the managers (`src/dal/`) and the stored procedures (`sql/07`, `sql/08`). Pages have none.
- **How do you stop half-saved data?** Every save is one transaction: commit on success, rollback on any error.
- **How does the UI trigger SCD Type 2?** "Apply change" calls `change_position()`, which runs the OLTP procedure and `sp_scd2_apply_employee` in one transaction.
- **Why are the dashboards fast?** Results are cached per warehouse version, saved in `analytics_snapshot` and recomputed in the background.
- **Is the data the same every time?** Yes: numpy and Faker are both seeded with 42.
- **How did the data get to the cloud?** Same scripts with the Aiven settings in `.env`; the finished rows were copied with mysqldump and verified.

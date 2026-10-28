# ETL Design — Stored Procedures

**Project:** Enterprise Employee Analytics & Data Warehouse System
**Phase:** 2 — ETL (staging → OLTP → warehouse)
**Status:** Implemented and verified
**Code:** [`sql/07_oltp_procedures.sql`](../sql/07_oltp_procedures.sql) ·
[`sql/08_dw_procedures.sql`](../sql/08_dw_procedures.sql) ·
[`src/dal/etl_manager.py`](../src/dal/etl_manager.py) · [`scripts/run_etl.py`](../scripts/run_etl.py)
**Proof:** [`tests/test_etl.py`](../tests/test_etl.py) · [`sql/validation_queries.sql`](../sql/validation_queries.sql)

---

## 1. What the problem statement requires

> *"Write Stored Procedures to move data from the OLTP tables to the OLAP Data Warehouse. Use CTEs
> and Window Functions … to clean, rank, and transform the synthesized dataset … Ensure your ETL
> logic correctly handles the SCD Type 2 inserts/updates."*

And for the application (Phase 4): *"updating an employee's department must trigger an SCD Type 2
update in the warehouse."*

## 2. Architecture: two stages, one audit trail

```
hr_staging ──(Stage 1: migration, once)──► hr_oltp ──(Stage 2: warehouse ETL, repeatable)──► hr_dw
 raw, dirty      deduplicate · clean · resolve       dimensions · SCD Type 2 · fact
                 labels and codes · validate
                                    │                                       │
                                    └──── hr_dw.etl_batch_log / etl_rejected_rows ────┘
```

| Stage | Procedure | Runs | Owns its transaction |
|---|---|---|---|
| 1 | `hr_oltp.sp_migrate_from_staging(p_full_reload, OUT batch)` | once (legacy migration) | yes |
| 2 | `hr_dw.sp_run_etl(OUT batch)` | every run (nightly / "Run ETL" button) | yes |
| app | `hr_oltp.sp_change_employee_position(…)` / `sp_onboard_employee(…)` / `sp_record_exit(…)` + `hr_dw.sp_scd2_apply_employee(emp, OUT batch)` | on every hire, job change and exit in the app ([`backend_design.md`](backend_design.md) §6) | no — the app commits both together |

Inside Stage 2, `sp_run_etl` calls the step procedures in dependency order — dimensions before the
fact, so a review always finds its dimension rows:

| Step procedure | Technique |
|---|---|
| `sp_load_dim_date(batch, start, end)` | **recursive CTE** generates 2015-01-01 … 2030-12-31 with the Indian fiscal year |
| `sp_load_dim_department(batch)` | SCD Type 1 (update changed rows, insert new ones) |
| `sp_load_dim_project(batch)` | SCD Type 1 + derived duration, delay, `is_delayed`, budget band; guarantees the −1 special member |
| `sp_load_dim_employee_scd2(batch, employee NULL)` | **SCD Type 2** — `ROW_NUMBER()`, `LEAD()`, `MD5` row hash (§5) |
| `sp_load_fact_performance_reviews(batch)` | incremental (`NOT EXISTS`), **point-in-time** surrogate-key lookup, rejects logged |

Helpers: `sp_etl_start_batch`, `sp_etl_log_step`, `sp_etl_finish_batch`, `sp_reset_warehouse`.

## 3. Stage 1 — staging → OLTP (`sp_migrate_from_staging`)

Tables load in foreign-key order: employees → job history → projects → assignments → surveys →
reviews. Every table follows the same four-step pattern, written as CTEs:

1. **ranked** — `ROW_NUMBER() OVER (PARTITION BY <business key> ORDER BY stg_row_id DESC)`: a
   record sent twice is kept once (the newest staging row).
2. **cleaned** — `TRIM`, case-insensitive label matching, code standardization.
3. **resolved** — text labels and business codes become ids through joins to the lookup tables;
   a `CASE` names the first broken rule (`reject_reason`).
4. **loaded / rejected** — rows with a reason go to `hr_dw.etl_rejected_rows`; the rest are
   inserted.

| Data-quality issue (planted, decision D21) | Fix in SQL | Result (seed 42) |
|---|---|---:|
| Employee sent twice | `ROW_NUMBER()` per `employee_code`, newest kept | 500 removed |
| Gender as "M" / "F" | `CASE UPPER(TRIM(gender))` → Male / Female | 1,027 standardized |
| Department / role labels: case, stray spaces, aliases "R&D", "HR", "Sales Dept" | `UPPER(TRIM())` match against a label map of names, codes and aliases | 6,172 standardized |
| Review resubmitted with a corrected KPI | `ROW_NUMBER()` per employee and review year, latest kept | 1,389 removed |
| NumCompaniesWorked counts the current employer; 0 is a coding error (D19) | `CASE`: no prior experience → 0; 0 or 1 with experience → 1; otherwise value − 1 | 16,500 recoded |
| Job-version end dates | recomputed with `LEAD(start)`, compared with the staged value | 0 corrections needed |

Every count equals the synthesizer's anomaly manifest exactly — the label noise, for example, was
planted as 1,473 + 1,571 + 1,568 + 1,560 = 6,172 rows (a test checks all of them). (Counts after the
review-history fix, synthesizer_design.md D26; the first run planted 979 gender codes and 6,289
labels — the rates are unchanged, only which rows were picked.)

**Validation rules (reject reasons).** Employees: invalid code format, unknown gender, education
level / field or exit reason, exit date without reason (or the reverse), attrition flag
contradicting the exit date, more years at the company than total working years. Job history:
unknown employee, department label, role or level; role not in the department; level outside the
role's range; income outside the level's band; unknown manager. Projects, assignments, surveys,
reviews: unknown references, out-of-range values, self-review, overtime not Yes / No. The
database constraints stay the last line of defence: a row that broke one would roll the whole
migration back.

**Deterministic ids.** Employees are inserted in code order, so `EMP000001` gets id 1 (projects
likewise); `TRUNCATE` on a full reload resets the counters, so every rebuild produces the same ids.

## 4. Stage 2 — dimensions and fact

**`dim_date`.** A recursive CTE produces one row per day (5,844 rows). Because each recursion level
is one day, the session's `cte_max_recursion_depth` (default 1,000) is raised to the number of
days first. Fiscal year April–March: `FY2024-25` = 2024-04-01 … 2025-03-31; April–June is fiscal
Q1. Idempotent: existing days are skipped.

**`dim_department`, `dim_project` (SCD Type 1).** Changed attributes are overwritten, new rows
inserted. Project attributes derived on the way: `planned_duration_days`; `delay_days` and
`is_delayed` for **completed** projects only (a cancelled project's end date is its cancellation
date; a running project has no actual end yet); budget band.

| Band | Boundaries | Rows |
|---|---|---|
| `salary_band` (monthly income) | <3K · 3K-6K · 6K-10K · 10K-15K · 15K+ | 79,601 · 116,200 · 55,156 · 37,409 · 20,620 versions |
| `budget_band` (project budget) | <250K · 250K-1M · 1M-3M · 3M+ | 360 · 961 · 549 · 130 projects |

Boundaries were chosen after profiling the data so that every band is populated.

**Fact.** Only reviews not yet loaded (`NOT EXISTS` on `review_id`). Each review is joined to:
`dim_date` by its review date; the `dim_employee` version **valid on the review date**
(`review_date BETWEEN start_date AND end_date`); that version's department; its project or the
special member (`COALESCE(project_id, 0)` → `project_sk = −1`); the same-year survey (nullable
measures, D7). `monthly_income_at_review` comes from the version, so pay is reported as it was at
the time. A failed lookup sends the review to `etl_rejected_rows` instead of a NULL key
(dw_design.md §9.6). Result: 463,015 facts, 0 rejected, 16,961 reviews (3.7 %) on the "No Project"
member.

## 5. SCD Type 2 — `sp_load_dim_employee_scd2`

The history is recorded where it happens — in `hr_oltp.employee_job_history` (effective-dated) —
and every OLTP version becomes exactly one warehouse version (D8). The procedure stages the
source in two temporary tables that mirror the OLTP split — **the person** (Type 1 attributes)
and **the job versions** (Type 2 attributes, `version_number` from `ROW_NUMBER()`, inclusive
`end_date` from `LEAD(start) − 1 day`, `row_hash` = MD5 of the Type 2 columns) — then compares
them with the warehouse by `(employee_id, start_date)`:

| Step | Detects | Action |
|---|---|---|
| 1. Type 1 | a personal attribute changed (name, marital status, exit …) | overwrite on **all** versions of the employee |
| 2. Correct | same version, different `row_hash` | overwrite the version's Type 2 values in place |
| 3. Expire | a version's end changed (a newer version started) | `end_date` = new start − 1 day, `is_current = 0` |
| 4. Insert | a version the warehouse has not seen | new row, new surrogate key |

Expire runs before insert, so "one current version per employee" holds at every moment — the
UNIQUE index on `current_version_key` would reject a second one.

**One code path, three uses** (decision E2): initial load (step 4 inserts all 308,986 versions),
incremental run (only differences are written — a re-run with no OLTP change writes 0 rows), and a
single employee from the app (`p_employee_id` given: 0.02 s).

**Worked example — a department change in the app** (test
`test_department_change_creates_a_new_warehouse_version`):

| employee_sk | version | department | role | start_date | end_date | is_current |
|---|---|---|---|---|---|---|
| (old) | n | Research & Development | Manager | … | **yesterday** | **0** ← expired |
| (new) | n + 1 | **Sales** | Manager | **today** | 9999-12-31 | **1** ← inserted |

Reviews dated before today keep pointing to the old version; reviews from today on find the new
one — history is reported as it was.

**Real example from the loaded data** (section 6 of `validation_queries.sql`): employee
EMP020091 has four versions, and each of their seven reviews links to the version valid on its
review date — so a dashboard shows the role, level and pay *at the time of each review*:

| version | role | level | income | valid | reviews linked |
|---|---|---|---:|---|---|
| 1 | Manufacturing Director | 4 | 16,587 | 2015-07-11 … 2020-06-08 | 2020-01-29 |
| 2 | Manufacturing Director | 4 | 16,587 | 2020-06-09 … 2021-11-13 (manager change) | 2021-03-10 |
| 3 | Research Director | 4 | 17,395 | 2021-11-14 … 2025-04-09 (role change) | 2022-02-24, 2023-03-06, 2024-01-28, 2025-02-10 |
| 4 | Research Director | 5 | 19,830 | 2025-04-10 … 9999-12-31 (promotion) | 2026-02-15 |

## 6. The application path — one transaction for both databases

```
START TRANSACTION
  CALL hr_oltp.sp_change_employee_position(...)   -- close current version, open new one
  CALL hr_dw.sp_scd2_apply_employee(emp, @batch)  -- expire + insert in dim_employee
COMMIT            -- or ROLLBACK: both changes, or neither
```

`sp_change_employee_position` never updates a version in place. It checks, each with a readable
error (SQLSTATE 45000): employee exists and is active; effective date after the current
version's start and not in the future; role exists in the department; level inside the role's
range; income inside the level's band; manager is another active employee; something actually
changed. If no change reason is given it is derived with the synthesizer's priority: Promotion
(level up) > Transfer (department) > Role Change > Manager Change > Salary Revision.
Since Phase 3 the four position rules live in `sp_validate_position`, shared with onboarding
([`backend_design.md`](backend_design.md) §6).

## 7. Logging and error handling

- **Batch header.** `sp_etl_start_batch` inserts a `STARTED` row and returns its `log_id` as the
  batch id — unique even if two runs start at once. The entry points commit the header
  immediately, so even a run that fails at once stays visible.
- **One row per step** with rows affected, status and a message; the migration also writes one
  `fix: …` row per data-quality fix, so the log doubles as the data-quality report.
- **Header closed** by `sp_etl_finish_batch`: rows loaded, `SUCCESS`, `WARNING` (some step had
  rejects) or `FAILED`.
- **Errors.** The entry points declare an `EXIT HANDLER FOR SQLEXCEPTION`: `GET DIAGNOSTICS` reads
  the error, `ROLLBACK` undoes the run, the header is marked `FAILED` with the step and MySQL
  message, and `RESIGNAL` passes the original error to the caller. Seen in practice: a type error
  during development produced
  `FAILED · dim_date: [42000] Incorrect argument type to variable 'cte_max_recursion_depth'`.

## 8. Results (100,000 employees, seed 42, laptop, MySQL 8.0.46, 128 MB buffer pool)

End-to-end rebuild with the finished code (`python scripts/run_etl.py --migrate --full-reload`),
last run after the review-history fix (synthesizer_design.md D26–D27):

| Run | Rows | Time |
|---|---:|---:|
| Stage 1 migration (6 load steps + 6 fix / check steps) | 1,845,887 loaded, 0 rejected | 253 s |
| Stage 2 initial load (3 departments, 2,000 projects, 308,986 versions, 463,015 facts; dim_date kept by the reset) | 774,004 | 165 s (SCD2 66 s, fact 99 s) |
| Stage 2 re-run, nothing changed | 0 | 47 s |
| One employee from the app (`sp_scd2_apply_employee`) | 2 | 0.02 s |

Warehouse content after the load: salary bands 79,601 / 116,200 / 55,156 / 37,409 / 20,620
versions; budget bands 360 / 961 / 549 / 130 projects; 602 of 1,563 completed projects delayed
(38.5 %, average delay 12.3 days); 1,053 facts without a same-year survey (NULL survey measures).

Verification (all automated in `tests/test_etl.py`, also runnable in Workbench from
`sql/validation_queries.sql`):

- staging → OLTP: every unique staged record loaded, all planted issues fixed in exactly the
  manifest's numbers;
- job history contiguous and starting at the hire date;
- **lossless reconstruction**: the IBM columns rebuilt from OLTP through `vw_employee_snapshot`
  as of the snapshot date match staging — age, tenure, total working years, years in role, years
  since promotion, department, role, level, income, … **100 %**; years with manager **98.09 %**;
  last salary hike within one point **92.09 %** — exactly the fidelity the synthesizer measured,
  so nothing was lost in the ETL;
- OLTP → warehouse: versions 1 : 1, periods contiguous (`LEAD`), version numbers sequential,
  Type 1 / Type 2 values equal to OLTP;
- every fact on the version valid on its review date, with the right date, department, project,
  income and measures;
- re-running changes nothing;
- the department-change and personal-change flows, and eight refused invalid changes.

## 9. Decisions

Approved before implementation:

| ID | Decision | Alternatives rejected | Why |
|---|---|---|---|
| E1 | Migration as a **one-time procedure** with an explicit `p_full_reload` switch; refuses to run twice | re-runnable merge; Python loader | It *is* a one-time legacy migration; refusing a second run prevents duplicates; a full reload also empties the warehouse, which is derived from OLTP |
| E2 | **One SCD Type 2 procedure** for initial, incremental and single-employee loads | separate initial / incremental / UI procedures | One tested code path; nothing to keep in sync |
| E3 | **Transaction ownership:** only batch entry points commit; step procedures and the app path join the caller's transaction | each procedure commits | The app can commit the OLTP change and its warehouse version together — no half-applied change |
| E4 | Department **aliases as a cleaning CTE** in the procedure (built once into a temporary label map) | a permanent `department_aliases` table | No schema or ER-diagram change for a migration-only concern |
| E5 | **`EtlManager(DatabaseConnection)` built now** — the first data-access manager | plain script now, class in Phase 3 | The CLI now and the Streamlit "Run ETL" button later share one implementation |
| E6 | `vw_employee_snapshot` exposes `role_since`, `manager_since`, `last_promotion_date`; salary / budget bands as in §4 | compute tenure at `CURDATE()` only | The view measures tenure as of today, the staging data as of 2026-06-30; the dates let the reconciliation use the snapshot date |

Refinements made while implementing:

| ID | Found | Change |
|---|---|---|
| E7 | `INSERT … ON DUPLICATE KEY UPDATE` reports 1 per insert and 2 per update, and consumes an AUTO_INCREMENT value for every *updated* row (gaps grow on every run) | Type 1 dimensions use update-changed-rows, then insert-new-rows: exact counts, no key gaps |
| E8 | The UI would have to choose a change reason the database can work out itself | `p_change_reason` NULL = derived, using the synthesizer's priority |
| E9 | With autocommit off (the app's pool), a migration refused before its transaction began rolled back its own header — the failed attempt left no trace | entry points commit the batch header first; tested with the guard |
| E10 | The SCD2 re-run took 89 s although nothing had changed. Measured with `EXPLAIN ANALYZE`: personal columns repeated on 309K version rows overflowed the 128 MB buffer pool, and rows were written out of primary-key order | source split into person + version temporary tables, inserted in key order: **89 s → 44 s** for the full compare; one employee 0.02 s |
| E11 | A type mismatch: `GREATEST(unsigned, signed)` returns DECIMAL, which a system variable rejects | `CAST(… AS UNSIGNED)` — and the failure demonstrated the error handler (§7) |
| E12 | Re-running `setup_database.py` to install procedures would also rerun the table DDL | `--only 07 08` runs selected scripts; the data-protection check applies only to scripts that drop tables; comments are kept so procedures show them in Workbench |

## 10. Questions you may be asked

**Q: Where are the CTEs and window functions?**
Stage 1: `ROW_NUMBER()` for de-duplication of every table, `LEAD()` to recompute job-version end
dates, CTE chains ranked → cleaned → resolved. Stage 2: a **recursive** CTE for the calendar,
`ROW_NUMBER()` for version numbers and `LEAD()` for SCD end dates. Validation: `LEAD()` / `LAG()`
to prove periods have no gaps or overlaps.

**Q: How does the SCD Type 2 logic work?**
§5: it compares OLTP job versions with warehouse versions; a new OLTP version expires the old
warehouse row (end date = day before, `is_current = 0`) and inserts a new current row. Expire
runs before insert, and a UNIQUE index guarantees one current version per employee.

**Q: Why compare by `(employee_id, start_date)` and not just a hash of the current row?**
Because the OLTP keeps the full effective-dated history, the warehouse can mirror it version by
version — even several changes between two ETL runs are captured, not just the latest state. The
`row_hash` is still used, to detect *corrections* to an existing version.

**Q: What happens if the ETL fails half-way?**
Everything is rolled back (one transaction), the batch is marked FAILED with the step and the
MySQL message, and the error is re-raised to the caller. The next run starts cleanly.

**Q: How do you know nothing was lost between staging and the warehouse?**
Reconciliation (§8): counts per table, every planted issue counted against the manifest, the
lossless reconstruction of the IBM columns, and the fact-to-version checks — automated tests and
Workbench queries.

**Q: Why are rows rejected instead of loaded with an "Unknown" key?**
A failed lookup is a data error; hiding it behind an "Unknown" member makes the warehouse look
complete while it is not (dw_design.md §9). Rejected rows are listed with the reason, and the run
is flagged WARNING.

**Q: Is the ETL incremental?**
Yes. Dimensions write only differences; the fact loads only new reviews. A re-run with no change
writes 0 rows (test `test_rerunning_the_warehouse_load_changes_nothing`).

**Q: How does a department change in the app reach the warehouse immediately?**
§6: the app calls `sp_change_employee_position` and `sp_scd2_apply_employee` in one transaction;
the SCD2 procedure runs for that one employee in about 20 ms.

**Q: Why does the migration refuse to run twice?**
A second run would load every employee again. It is a one-time migration; a deliberate rebuild
uses `p_full_reload = TRUE`, which also empties the warehouse because its natural keys are OLTP
ids.

**Q: Why log each data-quality fix separately?**
So the log is a data-quality report: anyone can see in Workbench that 500 duplicates were removed
and 1,027 gender codes fixed — and a test compares those numbers with the planted ones.

**Q: Why are delays measured only for completed projects?**
A cancelled project's end date is its cancellation date, not a delivery date, and a running
project has no actual end date. Overdue running projects can be found at query time from
`planned_end_date`.

**Q: How fast is it, and what limits it?**
§8. The laptop server runs with MySQL's default 128 MB buffer pool, smaller than the data, so I/O
dominates; the SCD2 source was redesigned to stay small (E10). A larger buffer pool would speed
up every step without code changes.

**Q: Why do batch ids jump (1, 14, 15, 21 …)?**
A batch id is the `log_id` of the batch's header row (§7) — unique without any extra sequence. The
step rows of a batch take the ids in between.

**Q: Why can surrogate keys have gaps?**
InnoDB never reuses AUTO_INCREMENT values: inserts that are rolled back (the tests and the
Workbench demo) and the block-wise pre-allocation of bulk `INSERT … SELECT` consume values.
Surrogate keys carry no meaning, so gaps are harmless; the loaded data itself is gapless
(`employee_sk` 1 … 308,986).

**Q: What are the known limits?**
History is append-only: a back-dated change (before the current version's start) is refused
rather than re-sequencing versions. Facts are insert-only: a review corrected in OLTP after it was
loaded is not re-loaded. `vw_employee_snapshot` rebuilds every employee with window functions
(≈ 17 s for one employee, ≈ 27 s for all 100,000), because MySQL cannot push a filter into its
window-function CTEs — it is meant for bulk reconciliation and analysis; single-employee screens
use `vw_employee_current` (0.01 s) or the warehouse. All three are deliberate choices for this
scope.

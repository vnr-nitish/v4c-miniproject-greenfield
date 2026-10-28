-- =============================================================================
-- validation_queries.sql  —  proof that the ETL worked; run in MySQL Workbench after
--                             python scripts/run_etl.py --migrate
-- Not numbered, so scripts/setup_database.py never runs it. Sections 1-6 are read-only;
-- section 7 changes one employee inside a transaction and rolls it back.
-- The same checks run automatically in tests/test_etl.py.
-- Checks return: check, expected, actual, PASS / FAIL.
-- =============================================================================


-- 1. ETL runs ---------------------------------------------------------------------------

-- the latest batches (a batch header is the row whose log_id = batch_id)
SELECT batch_id, step_name AS process, status, rows_affected, message, started_at,
       TIMESTAMPDIFF(SECOND, started_at, finished_at) AS seconds
FROM hr_dw.etl_batch_log
WHERE log_id = batch_id
ORDER BY batch_id DESC
LIMIT 10;

-- the latest successful migration, step by step: this is the data-quality report
SELECT step_name, status, rows_affected, message
FROM hr_dw.etl_batch_log
WHERE batch_id = (SELECT MAX(batch_id) FROM hr_dw.etl_batch_log
                  WHERE log_id = batch_id AND step_name = 'migrate staging -> oltp' AND status <> 'FAILED')
ORDER BY log_id;

-- rows any batch could not load (expected: none)
SELECT batch_id, source_table, COUNT(*) AS rows_rejected, MIN(reason) AS example_reason
FROM hr_dw.etl_rejected_rows
GROUP BY batch_id, source_table;


-- 2. Staging -> OLTP: every staged record loaded once ---------------------------------------

WITH counts AS (
    SELECT 'employees' AS entity,
           (SELECT COUNT(*) FROM hr_staging.stg_employees)                                   AS staged,
           (SELECT COUNT(DISTINCT TRIM(employee_code)) FROM hr_staging.stg_employees)        AS unique_records,
           (SELECT COUNT(*) FROM hr_oltp.employees)                                          AS loaded
    UNION ALL
    SELECT 'employee_job_history',
           (SELECT COUNT(*) FROM hr_staging.stg_job_history),
           (SELECT COUNT(DISTINCT TRIM(employee_code), effective_start_date) FROM hr_staging.stg_job_history),
           (SELECT COUNT(*) FROM hr_oltp.employee_job_history)
    UNION ALL
    SELECT 'projects',
           (SELECT COUNT(*) FROM hr_staging.stg_projects),
           (SELECT COUNT(DISTINCT TRIM(project_code)) FROM hr_staging.stg_projects),
           (SELECT COUNT(*) FROM hr_oltp.projects)
    UNION ALL
    SELECT 'project_assignments',
           (SELECT COUNT(*) FROM hr_staging.stg_assignments),
           (SELECT COUNT(DISTINCT TRIM(employee_code), TRIM(project_code), start_date) FROM hr_staging.stg_assignments),
           (SELECT COUNT(*) FROM hr_oltp.project_assignments)
    UNION ALL
    SELECT 'employee_surveys',
           (SELECT COUNT(*) FROM hr_staging.stg_surveys),
           (SELECT COUNT(DISTINCT TRIM(employee_code), YEAR(survey_date)) FROM hr_staging.stg_surveys),
           (SELECT COUNT(*) FROM hr_oltp.employee_surveys)
    UNION ALL
    SELECT 'performance_reviews',
           (SELECT COUNT(*) FROM hr_staging.stg_reviews),
           (SELECT COUNT(DISTINCT TRIM(employee_code), review_year) FROM hr_staging.stg_reviews),
           (SELECT COUNT(*) FROM hr_oltp.performance_reviews)
)
SELECT entity, staged, staged - unique_records AS duplicates_removed, unique_records, loaded,
       IF(loaded = unique_records, 'PASS', 'FAIL') AS result
FROM counts;


-- 3. Staging -> OLTP: the cleaning rules hold ------------------------------------------------

SELECT check_name, expected, actual, IF(actual = expected, 'PASS', 'FAIL') AS result
FROM (
    SELECT 'gender codes left (M / F)' AS check_name, 0 AS expected,
           COUNT(*) AS actual
    FROM hr_oltp.employees WHERE gender NOT IN ('Male', 'Female')
    UNION ALL
    SELECT 'D19: prior companies contradict prior experience', 0,
           COALESCE(SUM((prior_experience_years = 0) <> (prior_companies_count = 0)), 0)
    FROM hr_oltp.employees
    UNION ALL
    SELECT 'resubmitted reviews not keeping the latest KPI', 0, COUNT(*)
    FROM (SELECT TRIM(employee_code) AS code, review_year, kpi_score,
                 ROW_NUMBER() OVER (PARTITION BY TRIM(employee_code), review_year ORDER BY stg_row_id DESC) AS rn,
                 COUNT(*)     OVER (PARTITION BY TRIM(employee_code), review_year) AS copies
          FROM hr_staging.stg_reviews) s
    JOIN hr_oltp.employees e           ON e.employee_code = s.code
    JOIN hr_oltp.performance_reviews r ON r.employee_id = e.employee_id AND r.review_year = s.review_year
    WHERE s.rn = 1 AND s.copies > 1 AND r.kpi_score <> s.kpi_score
    UNION ALL
    SELECT 'employees without exactly one open job version', 0, COUNT(*)
    FROM hr_oltp.employees e
    WHERE (SELECT COUNT(*) FROM hr_oltp.employee_job_history h
           WHERE h.employee_id = e.employee_id AND h.effective_end_date IS NULL) <> 1
    UNION ALL
    SELECT 'job versions not ending where the next starts (LEAD)', 0, COUNT(*)
    FROM (SELECT effective_end_date,
                 LEAD(effective_start_date) OVER (PARTITION BY employee_id ORDER BY effective_start_date) AS next_start
          FROM hr_oltp.employee_job_history) v
    WHERE NOT (effective_end_date <=> next_start)
) checks;


-- 4. OLTP -> warehouse: SCD Type 2 and the fact table ----------------------------------------

SELECT check_name, expected, actual, IF(actual = expected, 'PASS', 'FAIL') AS result
FROM (
    SELECT 'dim_employee versions = OLTP job versions' AS check_name,
           (SELECT COUNT(*) FROM hr_oltp.employee_job_history) AS expected,
           (SELECT COUNT(*) FROM hr_dw.dim_employee) AS actual
    UNION ALL
    SELECT 'current versions = employees',
           (SELECT COUNT(*) FROM hr_oltp.employees),
           (SELECT COUNT(*) FROM hr_dw.dim_employee WHERE is_current)
    UNION ALL
    SELECT 'SCD2 gaps or overlaps (end = next start - 1 day)', 0, COUNT(*)
    FROM (SELECT end_date, LEAD(start_date) OVER (PARTITION BY employee_id ORDER BY start_date) AS next_start
          FROM hr_dw.dim_employee) v
    WHERE end_date <> COALESCE(next_start - INTERVAL 1 DAY, DATE '9999-12-31')
    UNION ALL
    SELECT 'fact rows = OLTP reviews',
           (SELECT COUNT(*) FROM hr_oltp.performance_reviews),
           (SELECT COUNT(*) FROM hr_dw.fact_performance_reviews)
    UNION ALL
    SELECT 'fact rows linked to a version not valid on the review date', 0, COUNT(*)
    FROM hr_dw.fact_performance_reviews f
    JOIN hr_oltp.performance_reviews r ON r.review_id = f.review_id
    JOIN hr_dw.dim_employee de         ON de.employee_sk = f.employee_sk
    WHERE r.review_date NOT BETWEEN de.start_date AND de.end_date
    UNION ALL
    SELECT 'reviews without a project -> special member -1',
           (SELECT COUNT(*) FROM hr_oltp.performance_reviews WHERE project_id IS NULL),
           (SELECT COUNT(*) FROM hr_dw.fact_performance_reviews WHERE project_sk = -1)
    UNION ALL
    SELECT 'dim_date days 2015-2030', 5844, (SELECT COUNT(*) FROM hr_dw.dim_date)
) checks;


-- 5. Lossless decomposition: rebuild the IBM columns from OLTP (snapshot date 2026-06-30) ----
--    Expected: 100 % for every column except years_with_curr_manager (~98 %, early company
--    years had no eligible manager) and the last salary hike (~92 % within 1 point, salaries
--    clipped into level bands) — the same fidelity the synthesizer measured.

WITH staged AS (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY TRIM(s.employee_code) ORDER BY s.stg_row_id DESC) AS rn
    FROM hr_staging.stg_employees s
),
pairs AS (
    SELECT s.age, s.years_at_company, s.total_working_years, s.years_in_current_role,
           s.years_since_last_promotion, s.years_with_curr_manager, s.percent_salary_hike,
           s.job_level, s.monthly_income,
           e.date_of_birth, e.hire_date, e.prior_experience_years,
           COALESCE(e.exit_date, DATE '2026-06-30') AS ref,
           v.role_since, v.manager_since, v.last_promotion_date,
           v.job_level AS v_job_level, v.monthly_income AS v_income, v.percent_salary_hike AS v_hike
    FROM staged s
    JOIN hr_oltp.employees e            ON e.employee_code = TRIM(s.employee_code)
    JOIN hr_oltp.vw_employee_snapshot v ON v.employee_id = e.employee_id
    WHERE s.rn = 1
)
SELECT COUNT(*) AS employees,
       ROUND(100 * AVG(TIMESTAMPDIFF(YEAR, date_of_birth, ref) = age), 2)                         AS age_pct,
       ROUND(100 * AVG(TIMESTAMPDIFF(YEAR, hire_date, ref) = years_at_company), 2)               AS years_at_company_pct,
       ROUND(100 * AVG(prior_experience_years + TIMESTAMPDIFF(YEAR, hire_date, ref)
                       = total_working_years), 2)                                                 AS total_working_years_pct,
       ROUND(100 * AVG(TIMESTAMPDIFF(YEAR, role_since, ref) = years_in_current_role), 2)         AS years_in_role_pct,
       ROUND(100 * AVG(TIMESTAMPDIFF(YEAR, COALESCE(last_promotion_date, hire_date), ref)
                       = years_since_last_promotion), 2)                                          AS years_since_promotion_pct,
       ROUND(100 * AVG(TIMESTAMPDIFF(YEAR, manager_since, ref) = years_with_curr_manager), 2)    AS years_with_manager_pct,
       ROUND(100 * AVG(v_job_level = job_level AND v_income = monthly_income), 2)                AS level_and_income_pct,
       ROUND(100 * AVG(ABS(v_hike - percent_salary_hike) <= 1), 2)                               AS salary_hike_within_1_pct
FROM pairs;


-- 6. Walkthrough: one employee's SCD Type 2 history and the reviews linked to it ---------------

-- the employee whose reviews link to the most different versions: the clearest example of the
-- point-in-time lookup
SET @demo_employee := (SELECT e.employee_id
                       FROM hr_dw.fact_performance_reviews f
                       JOIN hr_dw.dim_employee e ON e.employee_sk = f.employee_sk
                       GROUP BY e.employee_id
                       ORDER BY COUNT(DISTINCT f.employee_sk) DESC, e.employee_id
                       LIMIT 1);

SELECT employee_sk, employee_code, version_number, department_name, job_role, job_level,
       monthly_income, salary_band, manager_name, change_reason, start_date, end_date, is_current
FROM hr_dw.dim_employee
WHERE employee_id = @demo_employee
ORDER BY version_number;

-- each review points to the version that was valid on its review date (point-in-time lookup)
SELECT f.review_id, d.full_date AS review_date, d.fiscal_year, e.employee_sk, e.version_number,
       e.job_role, e.job_level, f.monthly_income_at_review, f.performance_rating, f.kpi_score,
       p.project_name
FROM hr_dw.fact_performance_reviews f
JOIN hr_dw.dim_date d     ON d.date_sk = f.date_sk
JOIN hr_dw.dim_employee e ON e.employee_sk = f.employee_sk
JOIN hr_dw.dim_project p  ON p.project_sk = f.project_sk
WHERE e.employee_id = @demo_employee
ORDER BY d.full_date;


-- 7. Try the application's SCD Type 2 flow (Phase 4 requirement) — rolled back at the end -----
--    An active R&D Manager is transferred to Sales; the OLTP change and the warehouse update
--    run in ONE transaction, exactly as the Streamlit app will do it.

START TRANSACTION;

SELECT employee_id, job_level, monthly_income, stock_option_level, business_travel
INTO @emp, @level, @income, @stock, @travel
FROM hr_oltp.vw_employee_current
WHERE is_active AND department_code = 'RND' AND job_role = 'Manager'
ORDER BY employee_id LIMIT 1;

SELECT employee_id INTO @sales_head
FROM hr_oltp.vw_employee_current
WHERE is_active AND department_code = 'SAL' AND job_level = 5
ORDER BY employee_id LIMIT 1;

-- department 3 = Sales, role 4 = Manager; change reason NULL = derived ('Transfer')
CALL hr_oltp.sp_change_employee_position(@emp, 3, 4, @level, @sales_head, @income, @stock, @travel,
                                         NULL, CURDATE(), @history_id);
CALL hr_dw.sp_scd2_apply_employee(@emp, @batch_id);

-- the previous version now ends yesterday; a new current version starts today
SELECT employee_sk, version_number, department_name, job_role, manager_name, change_reason,
       start_date, end_date, is_current
FROM hr_dw.dim_employee
WHERE employee_id = @emp
ORDER BY version_number;

SELECT step_name, message FROM hr_dw.etl_batch_log WHERE batch_id = @batch_id ORDER BY log_id;

ROLLBACK;

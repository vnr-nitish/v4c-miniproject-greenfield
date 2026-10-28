-- =============================================================================
-- 08_dw_procedures.sql  —  hr_dw stored procedures (Stage 2 of the ETL: hr_oltp -> hr_dw)
-- Design: docs/etl_design.md, docs/dw_design.md sections 8-9
--
-- Logging       sp_etl_start_batch, sp_etl_log_step, sp_etl_finish_batch (used by both stages)
-- Maintenance   sp_reset_warehouse               empty everything derived from hr_oltp
-- Dimensions    sp_load_dim_date                 calendar + Indian fiscal year (recursive CTE)
--               sp_load_dim_department           SCD Type 1
--               sp_load_dim_project              SCD Type 1 + derived delay / budget attributes
--               sp_load_dim_employee_scd2        SCD Type 2: initial, incremental or one employee
-- Fact          sp_load_fact_performance_reviews incremental, point-in-time surrogate-key lookup
-- Entry points  sp_run_etl                       the whole warehouse load in one transaction
--               sp_scd2_apply_employee           one employee, right after a change in the app
--
-- Transaction rule (decision E3): only the batch entry points — sp_run_etl here and
-- hr_oltp.sp_migrate_from_staging — start and commit transactions. Every other procedure joins
-- the caller's transaction, so the application can commit an OLTP change together with its
-- warehouse update, or roll both back.
--
-- Safe to re-run: procedures are dropped and recreated; no table is touched.
-- =============================================================================

USE hr_dw;

DROP PROCEDURE IF EXISTS sp_etl_start_batch;
DROP PROCEDURE IF EXISTS sp_etl_log_step;
DROP PROCEDURE IF EXISTS sp_etl_finish_batch;
DROP PROCEDURE IF EXISTS sp_reset_warehouse;
DROP PROCEDURE IF EXISTS sp_load_dim_date;
DROP PROCEDURE IF EXISTS sp_load_dim_department;
DROP PROCEDURE IF EXISTS sp_load_dim_project;
DROP PROCEDURE IF EXISTS sp_load_dim_employee_scd2;
DROP PROCEDURE IF EXISTS sp_load_fact_performance_reviews;
DROP PROCEDURE IF EXISTS sp_scd2_apply_employee;
DROP PROCEDURE IF EXISTS sp_run_etl;

DELIMITER $$


-- -----------------------------------------------------------------------------
-- Logging. Every run is a batch: its first row in etl_batch_log is the batch header
-- (log_id = batch_id), followed by one row per step. Using the header's log_id as the
-- batch id makes batch ids unique even when two runs start at the same moment.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_etl_start_batch(IN p_process VARCHAR(60), OUT p_batch_id INT)
    COMMENT 'Open a batch: insert the header row and return its id'
BEGIN
    INSERT INTO etl_batch_log (batch_id, step_name, status, started_at)
    VALUES (0, p_process, 'STARTED', SYSDATE());
    SET p_batch_id = LAST_INSERT_ID();
    UPDATE etl_batch_log SET batch_id = p_batch_id WHERE log_id = p_batch_id;
END$$

CREATE PROCEDURE sp_etl_log_step(IN p_batch_id INT, IN p_step VARCHAR(60), IN p_rows INT,
                                 IN p_status VARCHAR(10), IN p_message VARCHAR(500), IN p_started DATETIME)
    COMMENT 'Record one finished step of a batch'
BEGIN
    INSERT INTO etl_batch_log (batch_id, step_name, rows_affected, status, message, started_at, finished_at)
    VALUES (p_batch_id, p_step, COALESCE(p_rows, 0), p_status, p_message, p_started, SYSDATE());
END$$

CREATE PROCEDURE sp_etl_finish_batch(IN p_batch_id INT, IN p_error VARCHAR(500))
    COMMENT 'Close a batch: FAILED if p_error is given, else WARNING if any step warned, else SUCCESS'
BEGIN
    DECLARE v_rows     INT;
    DECLARE v_steps    INT;
    DECLARE v_warnings INT;

    SELECT COALESCE(SUM(IF(step_name LIKE 'load %', rows_affected, 0)), 0),
           COUNT(*),
           COALESCE(SUM(status = 'WARNING'), 0)
    INTO v_rows, v_steps, v_warnings
    FROM etl_batch_log
    WHERE batch_id = p_batch_id AND log_id <> p_batch_id;

    UPDATE etl_batch_log
    SET status        = CASE WHEN p_error IS NOT NULL THEN 'FAILED'
                             WHEN v_warnings > 0     THEN 'WARNING'
                             ELSE 'SUCCESS' END,
        rows_affected = v_rows,
        message       = COALESCE(p_error, CONCAT(v_steps, ' steps, ', v_warnings, ' with warnings')),
        finished_at   = SYSDATE()
    WHERE log_id = p_batch_id;
END$$


-- -----------------------------------------------------------------------------
-- sp_reset_warehouse: empties every table derived from hr_oltp (its natural keys are OLTP
-- ids, so it must be rebuilt whenever hr_oltp is reloaded). dim_date is kept — it does not
-- depend on OLTP — and so are the ETL logs. The dim_project special member is recreated by
-- the next sp_load_dim_project. TRUNCATE commits implicitly, so this is never part of a
-- larger transaction.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_reset_warehouse()
    COMMENT 'Empty the warehouse tables derived from hr_oltp (keeps dim_date and the logs)'
BEGIN
    SET FOREIGN_KEY_CHECKS = 0;
    TRUNCATE TABLE fact_performance_reviews;
    TRUNCATE TABLE dim_employee;
    TRUNCATE TABLE dim_project;
    TRUNCATE TABLE dim_department;
    SET FOREIGN_KEY_CHECKS = 1;
END$$


-- -----------------------------------------------------------------------------
-- sp_load_dim_date: one row per day from p_start to p_end, generated by a recursive CTE.
-- Idempotent: days already present are skipped. Indian fiscal year runs April-March
-- (FY2024-25 = 2024-04-01 .. 2025-03-31), so April-June is fiscal Q1.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_load_dim_date(IN p_batch_id INT, IN p_start DATE, IN p_end DATE)
    COMMENT 'Calendar dimension from a recursive CTE, with the Indian fiscal year'
BEGIN
    DECLARE v_started DATETIME DEFAULT SYSDATE();
    DECLARE v_rows    INT;

    -- a recursive CTE stops at cte_max_recursion_depth (default 1000) — one level per day here
    SET SESSION cte_max_recursion_depth = CAST(GREATEST(@@SESSION.cte_max_recursion_depth,
                                                        DATEDIFF(p_end, p_start) + 1) AS UNSIGNED);

    INSERT INTO dim_date (date_sk, full_date, day_of_month, day_name, day_of_week, is_weekend,
                          week_of_year, month_number, month_name, quarter_number, quarter_label,
                          calendar_year, year_month_label, fiscal_year, fiscal_quarter)
    WITH RECURSIVE calendar (d) AS (
        SELECT p_start
        UNION ALL
        SELECT d + INTERVAL 1 DAY FROM calendar WHERE d < p_end
    ),
    fiscal AS (
        SELECT d, YEAR(d) - (MONTH(d) < 4) AS fy_start      -- January-March belong to the previous FY
        FROM calendar
    )
    SELECT YEAR(d) * 10000 + MONTH(d) * 100 + DAY(d),
           d,
           DAY(d),
           DAYNAME(d),
           WEEKDAY(d) + 1,                                   -- ISO: Monday = 1
           WEEKDAY(d) >= 5,
           WEEK(d, 3),                                       -- ISO week number
           MONTH(d),
           MONTHNAME(d),
           QUARTER(d),
           CONCAT('Q', QUARTER(d)),
           YEAR(d),
           DATE_FORMAT(d, '%Y-%m'),
           CONCAT('FY', fy_start, '-', LPAD((fy_start + 1) % 100, 2, '0')),
           CONCAT('Q', (MONTH(d) + 8) % 12 DIV 3 + 1)        -- April -> Q1 ... March -> Q4
    FROM fiscal f
    WHERE NOT EXISTS (SELECT 1 FROM dim_date x WHERE x.full_date = f.d);
    SET v_rows = ROW_COUNT();

    CALL sp_etl_log_step(p_batch_id, 'load dim_date', v_rows, 'SUCCESS',
                         CONCAT('days ', p_start, ' .. ', p_end, ', new ', v_rows), v_started);
END$$


-- -----------------------------------------------------------------------------
-- sp_load_dim_department: SCD Type 1 — changed attributes are overwritten, new departments
-- inserted. Update-then-insert (rather than INSERT ... ON DUPLICATE KEY UPDATE) gives exact
-- counts and does not burn AUTO_INCREMENT values on every run (refinement E7).
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_load_dim_department(IN p_batch_id INT)
    COMMENT 'SCD Type 1 load of dim_department'
BEGIN
    DECLARE v_started  DATETIME DEFAULT SYSDATE();
    DECLARE v_updated  INT;
    DECLARE v_inserted INT;

    UPDATE dim_department dd
    JOIN hr_oltp.departments d ON d.department_id = dd.department_id
    SET dd.department_code = d.department_code,
        dd.department_name = d.department_name
    WHERE NOT (dd.department_code <=> d.department_code AND dd.department_name <=> d.department_name);
    SET v_updated = ROW_COUNT();

    INSERT INTO dim_department (department_id, department_code, department_name)
    SELECT d.department_id, d.department_code, d.department_name
    FROM hr_oltp.departments d
    WHERE NOT EXISTS (SELECT 1 FROM dim_department dd WHERE dd.department_id = d.department_id)
    ORDER BY d.department_id;
    SET v_inserted = ROW_COUNT();

    CALL sp_etl_log_step(p_batch_id, 'load dim_department', v_inserted + v_updated, 'SUCCESS',
                         CONCAT('inserted ', v_inserted, ', updated ', v_updated), v_started);
END$$


-- -----------------------------------------------------------------------------
-- sp_load_dim_project: SCD Type 1, flattened (department and manager names copied in), plus
-- derived attributes. Delay is measured for completed projects only: a cancelled project's
-- end date is its cancellation date, and a running project has no actual end yet.
-- Also guarantees the special member (project_sk -1, project_id 0) exists.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_load_dim_project(IN p_batch_id INT)
    COMMENT 'SCD Type 1 load of dim_project with derived delay and budget band'
BEGIN
    DECLARE v_started  DATETIME DEFAULT SYSDATE();
    DECLARE v_updated  INT;
    DECLARE v_inserted INT;

    INSERT INTO dim_project (project_sk, project_id, project_code, project_name, owning_department_name,
                             status, priority, budget_band)
    SELECT -1, 0, 'N/A', 'No Project Assigned', 'N/A', 'N/A', 'N/A', 'N/A'
    FROM DUAL
    WHERE NOT EXISTS (SELECT 1 FROM dim_project WHERE project_id = 0);

    DROP TEMPORARY TABLE IF EXISTS tmp_src_project;
    CREATE TEMPORARY TABLE tmp_src_project (PRIMARY KEY (project_id))
    WITH projects AS (
        SELECT p.*,
               d.department_name                                     AS owning_department_name,
               CONCAT(m.first_name, ' ', m.last_name)                AS project_manager_name,
               IF(p.status = 'Completed', DATEDIFF(p.actual_end_date, p.planned_end_date), NULL) AS delay
        FROM hr_oltp.projects p
        JOIN hr_oltp.departments d ON d.department_id = p.department_id
        LEFT JOIN hr_oltp.employees m ON m.employee_id = p.project_manager_id
    )
    SELECT project_id, project_code, project_name, owning_department_name, project_manager_name,
           start_date, planned_end_date, actual_end_date,
           DATEDIFF(planned_end_date, start_date)                    AS planned_duration_days,
           delay                                                     AS delay_days,
           delay > 0                                                 AS is_delayed,
           status, priority, budget,
           CASE WHEN budget <  250000 THEN '<250K'
                WHEN budget < 1000000 THEN '250K-1M'
                WHEN budget < 3000000 THEN '1M-3M'
                ELSE '3M+' END                                       AS budget_band
    FROM projects;

    UPDATE dim_project dp
    JOIN tmp_src_project s ON s.project_id = dp.project_id
    SET dp.project_code = s.project_code,                   dp.project_name = s.project_name,
        dp.owning_department_name = s.owning_department_name, dp.project_manager_name = s.project_manager_name,
        dp.start_date = s.start_date,                       dp.planned_end_date = s.planned_end_date,
        dp.actual_end_date = s.actual_end_date,             dp.planned_duration_days = s.planned_duration_days,
        dp.delay_days = s.delay_days,                       dp.is_delayed = s.is_delayed,
        dp.status = s.status,                               dp.priority = s.priority,
        dp.budget = s.budget,                               dp.budget_band = s.budget_band
    WHERE NOT (dp.project_code <=> s.project_code AND dp.project_name <=> s.project_name
               AND dp.owning_department_name <=> s.owning_department_name
               AND dp.project_manager_name <=> s.project_manager_name
               AND dp.start_date <=> s.start_date AND dp.planned_end_date <=> s.planned_end_date
               AND dp.actual_end_date <=> s.actual_end_date
               AND dp.planned_duration_days <=> s.planned_duration_days
               AND dp.delay_days <=> s.delay_days AND dp.is_delayed <=> s.is_delayed
               AND dp.status <=> s.status AND dp.priority <=> s.priority
               AND dp.budget <=> s.budget AND dp.budget_band <=> s.budget_band);
    SET v_updated = ROW_COUNT();

    INSERT INTO dim_project (project_id, project_code, project_name, owning_department_name,
                             project_manager_name, start_date, planned_end_date, actual_end_date,
                             planned_duration_days, delay_days, is_delayed, status, priority, budget, budget_band)
    SELECT s.project_id, s.project_code, s.project_name, s.owning_department_name,
           s.project_manager_name, s.start_date, s.planned_end_date, s.actual_end_date,
           s.planned_duration_days, s.delay_days, s.is_delayed, s.status, s.priority, s.budget, s.budget_band
    FROM tmp_src_project s
    WHERE NOT EXISTS (SELECT 1 FROM dim_project dp WHERE dp.project_id = s.project_id)
    ORDER BY s.project_id;
    SET v_inserted = ROW_COUNT();

    DROP TEMPORARY TABLE IF EXISTS tmp_src_project;
    CALL sp_etl_log_step(p_batch_id, 'load dim_project', v_inserted + v_updated, 'SUCCESS',
                         CONCAT('inserted ', v_inserted, ', updated ', v_updated), v_started);
END$$


-- -----------------------------------------------------------------------------
-- sp_load_dim_employee_scd2: SCD Type 2 load of dim_employee — ONE code path for the initial
-- load, every incremental run, and a single employee changed in the application
-- (p_employee_id NULL = everyone). Decision E2.
--
-- The history itself is recorded in hr_oltp.employee_job_history (effective-dated), and every
-- OLTP version becomes exactly one warehouse version (decision D8). The procedure therefore
-- compares OLTP versions with warehouse versions by (employee_id, start_date):
--   1. Type 1    personal attributes changed       -> overwritten on ALL versions of the employee
--   2. Correct   a version's Type 2 values changed  -> overwritten in place (row_hash differs)
--   3. Expire    a version has ended in OLTP        -> end_date = next start - 1 day, is_current = 0
--   4. Insert    a version not in the warehouse yet -> new row, version_number from ROW_NUMBER()
-- Expiring before inserting keeps "one current version per employee" true at every moment,
-- which the UNIQUE index on current_version_key enforces.
--
-- The source is staged in two temporary tables that mirror the OLTP split: the person
-- (Type 1 attributes, one row per employee) and the job versions (Type 2 attributes). Keeping
-- the personal columns out of the 309K version rows keeps the staged data small, and inserting
-- in primary-key order avoids random page splits — together they halved the run time
-- (refinement E10, measurements in docs/etl_design.md).
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_load_dim_employee_scd2(IN p_batch_id INT, IN p_employee_id INT UNSIGNED)
    COMMENT 'SCD Type 2 load of dim_employee (all employees, or one when p_employee_id is given)'
BEGIN
    DECLARE v_started   DATETIME DEFAULT SYSDATE();
    DECLARE v_type1     INT;
    DECLARE v_corrected INT;
    DECLARE v_expired   INT;
    DECLARE v_inserted  INT;

    -- Source, part 1: the person — Type 1 attributes, labels resolved
    DROP TEMPORARY TABLE IF EXISTS tmp_src_person;
    CREATE TEMPORARY TABLE tmp_src_person (PRIMARY KEY (employee_id))
    SELECT e.employee_id,
           e.employee_code,
           CONCAT(e.first_name, ' ', e.last_name)                      AS full_name,
           e.gender,
           e.date_of_birth,
           e.marital_status,
           el.level_name                                               AS education_level,
           ef.field_name                                               AS education_field,
           e.distance_from_home_km,
           e.prior_experience_years,
           e.prior_companies_count,
           e.hire_date,
           e.exit_date,
           er.reason_name                                              AS exit_reason,
           er.is_voluntary                                             AS is_voluntary_exit,
           e.exit_date IS NOT NULL                                     AS attrition_flag
    FROM hr_oltp.employees e
    JOIN hr_oltp.education_levels el  ON el.education_level_id = e.education_level_id
    JOIN hr_oltp.education_fields ef  ON ef.education_field_id = e.education_field_id
    LEFT JOIN hr_oltp.exit_reasons er ON er.exit_reason_id = e.exit_reason_id
    WHERE p_employee_id IS NULL OR e.employee_id = p_employee_id
    ORDER BY e.employee_id;

    -- Source, part 2: every job version shaped like the Type 2 part of a dim_employee row
    DROP TEMPORARY TABLE IF EXISTS tmp_src_version;
    CREATE TEMPORARY TABLE tmp_src_version (PRIMARY KEY (employee_id, start_date))
    WITH versions AS (
        SELECT h.employee_id, h.department_id, h.job_role_id, h.job_level_id, h.manager_id,
               h.monthly_income, h.stock_option_level, h.business_travel, h.change_reason,
               h.effective_start_date,
               ROW_NUMBER()                 OVER w AS version_number,
               LEAD(h.effective_start_date) OVER w AS next_start
        FROM hr_oltp.employee_job_history h
        WHERE p_employee_id IS NULL OR h.employee_id = p_employee_id
        WINDOW w AS (PARTITION BY h.employee_id ORDER BY h.effective_start_date)
    ),
    shaped AS (
        SELECT v.employee_id,
               d.department_code,
               d.department_name,
               r.role_name                                             AS job_role,
               v.job_level_id                                          AS job_level,
               jl.level_title                                          AS job_level_title,
               v.manager_id                                            AS manager_employee_id,
               CONCAT(m.first_name, ' ', m.last_name)                  AS manager_name,
               v.monthly_income,
               CASE WHEN v.monthly_income <  3000 THEN '<3K'
                    WHEN v.monthly_income <  6000 THEN '3K-6K'
                    WHEN v.monthly_income < 10000 THEN '6K-10K'
                    WHEN v.monthly_income < 15000 THEN '10K-15K'
                    ELSE '15K+' END                                    AS salary_band,
               v.stock_option_level,
               v.business_travel,
               v.change_reason,
               v.effective_start_date                                  AS start_date,
               -- OLTP periods are half-open, warehouse periods inclusive (dw_design.md 8.4)
               COALESCE(v.next_start - INTERVAL 1 DAY, DATE '9999-12-31') AS end_date,
               v.next_start IS NULL                                    AS is_current,
               v.version_number
        FROM versions v
        JOIN hr_oltp.departments d    ON d.department_id = v.department_id
        JOIN hr_oltp.job_roles r      ON r.job_role_id = v.job_role_id
        JOIN hr_oltp.job_levels jl    ON jl.job_level_id = v.job_level_id
        LEFT JOIN hr_oltp.employees m ON m.employee_id = v.manager_id
    )
    SELECT s.*,
           -- fingerprint of the Type 2 attributes: one comparison detects any change
           MD5(CONCAT_WS('|', s.department_code, s.department_name, s.job_role, s.job_level,
                         s.job_level_title, IFNULL(s.manager_employee_id, ''), IFNULL(s.manager_name, ''),
                         s.monthly_income, s.salary_band, s.stock_option_level, s.business_travel,
                         s.change_reason))                             AS row_hash
    FROM shaped s
    ORDER BY s.employee_id, s.start_date;

    -- 1. Type 1: overwrite personal attributes on every version of the employee
    UPDATE dim_employee d
    JOIN tmp_src_person s ON s.employee_id = d.employee_id
    SET d.employee_code = s.employee_code,                 d.full_name = s.full_name,
        d.gender = s.gender,                               d.date_of_birth = s.date_of_birth,
        d.marital_status = s.marital_status,               d.education_level = s.education_level,
        d.education_field = s.education_field,             d.distance_from_home_km = s.distance_from_home_km,
        d.prior_experience_years = s.prior_experience_years, d.prior_companies_count = s.prior_companies_count,
        d.hire_date = s.hire_date,                         d.exit_date = s.exit_date,
        d.exit_reason = s.exit_reason,                     d.is_voluntary_exit = s.is_voluntary_exit,
        d.attrition_flag = s.attrition_flag
    WHERE NOT (d.employee_code <=> s.employee_code AND d.full_name <=> s.full_name
               AND d.gender <=> s.gender AND d.date_of_birth <=> s.date_of_birth
               AND d.marital_status <=> s.marital_status AND d.education_level <=> s.education_level
               AND d.education_field <=> s.education_field
               AND d.distance_from_home_km <=> s.distance_from_home_km
               AND d.prior_experience_years <=> s.prior_experience_years
               AND d.prior_companies_count <=> s.prior_companies_count
               AND d.hire_date <=> s.hire_date AND d.exit_date <=> s.exit_date
               AND d.exit_reason <=> s.exit_reason AND d.is_voluntary_exit <=> s.is_voluntary_exit
               AND d.attrition_flag <=> s.attrition_flag);
    SET v_type1 = ROW_COUNT();

    -- 2. Correct: the same version, but its Type 2 values were corrected in OLTP
    UPDATE dim_employee d
    JOIN tmp_src_version s ON s.employee_id = d.employee_id AND s.start_date = d.start_date
    SET d.department_code = s.department_code,   d.department_name = s.department_name,
        d.job_role = s.job_role,                 d.job_level = s.job_level,
        d.job_level_title = s.job_level_title,   d.manager_employee_id = s.manager_employee_id,
        d.manager_name = s.manager_name,         d.monthly_income = s.monthly_income,
        d.salary_band = s.salary_band,           d.stock_option_level = s.stock_option_level,
        d.business_travel = s.business_travel,   d.change_reason = s.change_reason,
        d.row_hash = s.row_hash
    WHERE d.row_hash <> s.row_hash;
    SET v_corrected = ROW_COUNT();

    -- 3. Expire: the version has been superseded by a newer one
    UPDATE dim_employee d
    JOIN tmp_src_version s ON s.employee_id = d.employee_id AND s.start_date = d.start_date
    SET d.end_date   = s.end_date,
        d.is_current = s.is_current
    WHERE d.end_date <> s.end_date;
    SET v_expired = ROW_COUNT();

    -- 4. Insert: versions the warehouse has not seen yet
    INSERT INTO dim_employee (employee_id, employee_code, full_name, gender, date_of_birth, marital_status,
                              education_level, education_field, distance_from_home_km, prior_experience_years,
                              prior_companies_count, hire_date, exit_date, exit_reason, is_voluntary_exit,
                              attrition_flag, department_code, department_name, job_role, job_level,
                              job_level_title, manager_employee_id, manager_name, monthly_income, salary_band,
                              stock_option_level, business_travel, change_reason, start_date, end_date,
                              is_current, version_number, row_hash, etl_batch_id)
    SELECT p.employee_id, p.employee_code, p.full_name, p.gender, p.date_of_birth, p.marital_status,
           p.education_level, p.education_field, p.distance_from_home_km, p.prior_experience_years,
           p.prior_companies_count, p.hire_date, p.exit_date, p.exit_reason, p.is_voluntary_exit,
           p.attrition_flag, s.department_code, s.department_name, s.job_role, s.job_level,
           s.job_level_title, s.manager_employee_id, s.manager_name, s.monthly_income, s.salary_band,
           s.stock_option_level, s.business_travel, s.change_reason, s.start_date, s.end_date,
           s.is_current, s.version_number, s.row_hash, p_batch_id
    FROM tmp_src_version s
    JOIN tmp_src_person p ON p.employee_id = s.employee_id
    WHERE NOT EXISTS (SELECT 1 FROM dim_employee d
                      WHERE d.employee_id = s.employee_id AND d.start_date = s.start_date)
    ORDER BY s.employee_id, s.start_date;
    SET v_inserted = ROW_COUNT();

    DROP TEMPORARY TABLE IF EXISTS tmp_src_person, tmp_src_version;
    CALL sp_etl_log_step(p_batch_id, 'load dim_employee (SCD2)', v_inserted + v_expired + v_corrected + v_type1,
                         'SUCCESS',
                         CONCAT(IF(p_employee_id IS NULL, 'all employees', CONCAT('employee ', p_employee_id)),
                                ': new versions ', v_inserted, ', expired ', v_expired,
                                ', corrected ', v_corrected, ', type 1 updates ', v_type1),
                         v_started);
END$$


-- -----------------------------------------------------------------------------
-- sp_load_fact_performance_reviews: incremental — only reviews not yet in the fact table.
-- Each review is linked to the employee version valid ON ITS REVIEW DATE (point-in-time
-- lookup), to that version's department, to its project or the -1 "No Project Assigned"
-- member, and to the same-year engagement survey (nullable measures, decision D7).
-- A review whose lookups fail is written to etl_rejected_rows instead (dw_design.md 9.6).
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_load_fact_performance_reviews(IN p_batch_id INT)
    COMMENT 'Incremental fact load with point-in-time surrogate-key lookup'
BEGIN
    DECLARE v_started  DATETIME DEFAULT SYSDATE();
    DECLARE v_rejected INT;
    DECLARE v_inserted INT;

    DROP TEMPORARY TABLE IF EXISTS tmp_src_fact;
    CREATE TEMPORARY TABLE tmp_src_fact (PRIMARY KEY (review_id))
    WITH new_reviews AS (
        SELECT r.*
        FROM hr_oltp.performance_reviews r
        WHERE NOT EXISTS (SELECT 1 FROM fact_performance_reviews f WHERE f.review_id = r.review_id)
    )
    SELECT n.review_id,
           dd.date_sk,
           de.employee_sk,
           dep.department_sk,
           dp.project_sk,
           n.rating_id                  AS performance_rating,
           n.kpi_score,
           n.training_sessions,
           n.overtime_flag,
           de.monthly_income            AS monthly_income_at_review,
           s.environment_satisfaction,
           s.job_satisfaction,
           s.relationship_satisfaction,
           s.work_life_balance,
           s.job_involvement,
           CASE WHEN dd.date_sk IS NULL        THEN 'review date outside dim_date'
                WHEN de.employee_sk IS NULL    THEN 'no employee version valid on the review date'
                WHEN dep.department_sk IS NULL THEN 'department missing from dim_department'
                WHEN dp.project_sk IS NULL     THEN 'project missing from dim_project'
           END                          AS reject_reason
    FROM new_reviews n
    LEFT JOIN dim_date dd ON dd.full_date = n.review_date
    LEFT JOIN dim_employee de                                   -- point-in-time lookup
           ON de.employee_id = n.employee_id
          AND n.review_date BETWEEN de.start_date AND de.end_date
    LEFT JOIN dim_department dep ON dep.department_code = de.department_code
    LEFT JOIN dim_project dp ON dp.project_id = COALESCE(n.project_id, 0)   -- 0 = special member
    LEFT JOIN hr_oltp.employee_surveys s
           ON s.employee_id = n.employee_id AND s.survey_year = n.review_year;

    INSERT INTO etl_rejected_rows (batch_id, source_table, source_key, reason)
    SELECT p_batch_id, 'hr_oltp.performance_reviews', CONCAT('review_id=', review_id), reject_reason
    FROM tmp_src_fact
    WHERE reject_reason IS NOT NULL;
    SET v_rejected = ROW_COUNT();

    INSERT INTO fact_performance_reviews (review_id, date_sk, employee_sk, department_sk, project_sk,
                                          performance_rating, kpi_score, training_sessions, overtime_flag,
                                          monthly_income_at_review, environment_satisfaction, job_satisfaction,
                                          relationship_satisfaction, work_life_balance, job_involvement,
                                          etl_batch_id)
    SELECT review_id, date_sk, employee_sk, department_sk, project_sk,
           performance_rating, kpi_score, training_sessions, overtime_flag,
           monthly_income_at_review, environment_satisfaction, job_satisfaction,
           relationship_satisfaction, work_life_balance, job_involvement,
           p_batch_id
    FROM tmp_src_fact
    WHERE reject_reason IS NULL
    ORDER BY review_id;
    SET v_inserted = ROW_COUNT();

    DROP TEMPORARY TABLE IF EXISTS tmp_src_fact;
    CALL sp_etl_log_step(p_batch_id, 'load fact_performance_reviews', v_inserted,
                         IF(v_rejected = 0, 'SUCCESS', 'WARNING'),
                         CONCAT('inserted ', v_inserted, ', rejected ', v_rejected), v_started);
END$$


-- -----------------------------------------------------------------------------
-- sp_scd2_apply_employee: the application's entry point after onboarding an employee or
-- changing their job (Phase 4 requirement). Runs the same SCD Type 2 procedure for one
-- employee. Does NOT commit: the caller commits it together with the OLTP change.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_scd2_apply_employee(IN p_employee_id INT UNSIGNED, OUT p_batch_id INT)
    COMMENT 'SCD Type 2 for one employee, inside the caller''s transaction'
BEGIN
    CALL sp_etl_start_batch(CONCAT('apply employee ', p_employee_id), p_batch_id);
    CALL sp_load_dim_employee_scd2(p_batch_id, p_employee_id);
    CALL sp_etl_finish_batch(p_batch_id, NULL);
END$$


-- -----------------------------------------------------------------------------
-- sp_run_etl: the whole warehouse load — dimensions before the fact (so a review always
-- finds its dimension rows), all in one transaction. On any error everything is rolled back,
-- the batch is marked FAILED with the step and the MySQL message, and the error is re-raised.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_run_etl(OUT p_batch_id INT)
    COMMENT 'Stage 2 ETL: hr_oltp -> hr_dw (all dimensions, then the fact)'
BEGIN
    DECLARE v_step     VARCHAR(60) DEFAULT 'start';
    DECLARE v_sqlstate CHAR(5);
    DECLARE v_error    TEXT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_sqlstate = RETURNED_SQLSTATE, v_error = MESSAGE_TEXT;
        ROLLBACK;
        CALL sp_etl_finish_batch(p_batch_id, LEFT(CONCAT(v_step, ': [', v_sqlstate, '] ', v_error), 500));
        COMMIT;
        RESIGNAL;
    END;

    CALL sp_etl_start_batch('run warehouse ETL', p_batch_id);
    COMMIT;                                      -- the header survives a failure, so every run is visible
    START TRANSACTION;

    SET v_step = 'dim_date';
    CALL sp_load_dim_date(p_batch_id, '2015-01-01', '2030-12-31');
    SET v_step = 'dim_department';
    CALL sp_load_dim_department(p_batch_id);
    SET v_step = 'dim_project';
    CALL sp_load_dim_project(p_batch_id);
    SET v_step = 'dim_employee';
    CALL sp_load_dim_employee_scd2(p_batch_id, NULL);
    SET v_step = 'fact_performance_reviews';
    CALL sp_load_fact_performance_reviews(p_batch_id);

    CALL sp_etl_finish_batch(p_batch_id, NULL);
    COMMIT;
END$$

DELIMITER ;

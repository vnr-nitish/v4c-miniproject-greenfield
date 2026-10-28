-- =============================================================================
-- 07_oltp_procedures.sql  —  hr_oltp stored procedures
-- Design: docs/etl_design.md
--
-- sp_migrate_from_staging      Stage 1 of the ETL: the one-time migration of the legacy data in
--                              hr_staging into the normalized hr_oltp tables. Deduplicates,
--                              cleans and validates with CTEs and window functions, resolves text
--                              labels and business codes to ids, writes rows it cannot load to
--                              hr_dw.etl_rejected_rows and logs every step in hr_dw.etl_batch_log.
-- sp_change_employee_position  Records a job change (department, role, level, manager, pay) as a
--                              new effective-dated version. Used by the application; it does not
--                              commit — the caller commits it together with the warehouse update.
-- sp_validate_position         The position rules shared by onboarding and job changes.
-- sp_onboard_employee          Hires a person: employee row + first job version (no commit).
-- sp_record_exit               Records an exit and closes open assignments (no commit).
--
-- Safe to re-run: procedures are dropped and recreated; no table is touched.
-- =============================================================================

USE hr_oltp;

DROP PROCEDURE IF EXISTS sp_migrate_from_staging;
DROP PROCEDURE IF EXISTS sp_change_employee_position;
DROP PROCEDURE IF EXISTS sp_validate_position;
DROP PROCEDURE IF EXISTS sp_onboard_employee;
DROP PROCEDURE IF EXISTS sp_record_exit;

DELIMITER $$


-- -----------------------------------------------------------------------------
-- sp_migrate_from_staging
--
-- Loads the tables in foreign-key order: employees -> job history -> projects ->
-- assignments -> surveys -> reviews. Every table follows the same pattern:
--   a. ranked   ROW_NUMBER() over the business key, newest staging row first -> duplicates out
--   b. cleaned  TRIM / case-insensitive label matching / code standardization
--   c. resolved text labels and business codes -> ids; a CASE names the first broken rule
--   d. rejected rows with a reason go to hr_dw.etl_rejected_rows, the rest are inserted
-- Each data-quality fix is logged as its own step ("fix: ..."), so the log doubles as the
-- data-quality report and can be compared with the synthesizer's anomaly manifest.
--
-- All-or-nothing: the load runs in one transaction. Any error rolls everything back, marks
-- the batch FAILED and re-raises the error. p_full_reload = TRUE first empties hr_oltp and
-- the warehouse (which is derived from it); otherwise the procedure refuses to run twice.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_migrate_from_staging(IN p_full_reload BOOLEAN, OUT p_batch_id INT)
    COMMENT 'Stage 1 ETL: hr_staging -> hr_oltp (deduplicate, clean, resolve, validate)'
BEGIN
    DECLARE v_step     VARCHAR(60) DEFAULT 'start';
    DECLARE v_started  DATETIME;
    DECLARE v_staged   INT;
    DECLARE v_unique   INT;
    DECLARE v_loaded   INT;
    DECLARE v_rejected INT;
    DECLARE v_fixed    INT;
    DECLARE v_sqlstate CHAR(5);
    DECLARE v_error    TEXT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_sqlstate = RETURNED_SQLSTATE, v_error = MESSAGE_TEXT;
        ROLLBACK;
        SET FOREIGN_KEY_CHECKS = 1;
        CALL hr_dw.sp_etl_finish_batch(p_batch_id, LEFT(CONCAT(v_step, ': [', v_sqlstate, '] ', v_error), 500));
        COMMIT;
        RESIGNAL;
    END;

    CALL hr_dw.sp_etl_start_batch('migrate staging -> oltp', p_batch_id);
    COMMIT;                                      -- the header survives a failure, so every run is visible

    -- 0. Full reload, or refuse to load the same data twice ---------------------------
    SET v_step = 'prepare';
    IF p_full_reload THEN
        CALL hr_dw.sp_reset_warehouse();
        SET FOREIGN_KEY_CHECKS = 0;              -- TRUNCATE resets AUTO_INCREMENT, so ids repeat
        TRUNCATE TABLE performance_reviews;
        TRUNCATE TABLE project_assignments;
        TRUNCATE TABLE employee_surveys;
        TRUNCATE TABLE projects;
        TRUNCATE TABLE employee_job_history;
        TRUNCATE TABLE employees;
        SET FOREIGN_KEY_CHECKS = 1;
    ELSEIF EXISTS (SELECT 1 FROM employees) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'hr_oltp already holds employees; call with p_full_reload = TRUE to rebuild it';
    END IF;

    START TRANSACTION;

    -- 1. Department label map: canonical names, codes, and the aliases the legacy system
    --    is known to send (decision E4). Built once, used by job history and projects.
    SET v_step = 'department labels';
    DROP TEMPORARY TABLE IF EXISTS tmp_department_labels;
    CREATE TEMPORARY TABLE tmp_department_labels (PRIMARY KEY (label))
    WITH aliases (alias, department_name) AS (
        SELECT 'R&D', 'Research & Development' UNION ALL
        SELECT 'HR', 'Human Resources' UNION ALL
        SELECT 'Sales Dept', 'Sales'
    ),
    labels AS (
        SELECT UPPER(department_name) AS label, department_id FROM departments
        UNION
        SELECT UPPER(department_code), department_id FROM departments
        UNION
        SELECT UPPER(a.alias), d.department_id
        FROM aliases a JOIN departments d ON d.department_name = a.department_name
    )
    SELECT label, department_id FROM labels;


    -- 2. Employees ---------------------------------------------------------------------
    SET v_step = 'employees', v_started = SYSDATE();
    DROP TEMPORARY TABLE IF EXISTS tmp_employees;
    CREATE TEMPORARY TABLE tmp_employees (INDEX (employee_code))
    WITH ranked AS (
        -- some employees were sent twice: number the copies, newest first
        SELECT s.*,
               TRIM(s.employee_code)                                    AS code,
               ROW_NUMBER() OVER (PARTITION BY TRIM(s.employee_code) ORDER BY s.stg_row_id DESC) AS copy_no
        FROM hr_staging.stg_employees s
    ),
    cleaned AS (
        SELECT r.stg_row_id,
               r.code                                                   AS employee_code,
               TRIM(r.first_name)                                       AS first_name,
               TRIM(r.last_name)                                        AS last_name,
               LOWER(TRIM(r.email))                                     AS email,
               TRIM(r.phone)                                            AS phone,
               r.gender                                                 AS gender_raw,
               CASE UPPER(TRIM(r.gender))
                    WHEN 'MALE'   THEN 'Male'   WHEN 'M' THEN 'Male'
                    WHEN 'FEMALE' THEN 'Female' WHEN 'F' THEN 'Female'
               END                                                      AS gender,
               r.date_of_birth,
               TRIM(r.marital_status)                                   AS marital_status,
               r.education                                              AS education_level_id,
               TRIM(r.education_field)                                  AS education_field,
               r.distance_from_home                                     AS distance_from_home_km,
               r.total_working_years - r.years_at_company               AS prior_experience_years,
               r.num_companies_worked,
               -- D19: NumCompaniesWorked counts the current employer, and 0 is a coding error
               CASE WHEN r.total_working_years = r.years_at_company THEN 0
                    WHEN r.num_companies_worked <= 1                THEN 1
                    ELSE r.num_companies_worked - 1
               END                                                      AS prior_companies_count,
               r.hire_date,
               r.exit_date,
               NULLIF(TRIM(r.exit_reason), '')                          AS exit_reason,
               UPPER(TRIM(r.attrition))                                 AS attrition
        FROM ranked r
        WHERE r.copy_no = 1 OR r.code IS NULL
    )
    SELECT c.*,
           ef.education_field_id,
           er.exit_reason_id,
           CASE WHEN c.employee_code IS NULL
                  OR c.employee_code NOT REGEXP '^EMP[0-9]{6}$'           THEN 'invalid employee code'
                WHEN c.gender IS NULL                                     THEN 'unknown gender value'
                WHEN el.education_level_id IS NULL                        THEN 'unknown education level'
                WHEN ef.education_field_id IS NULL                        THEN 'unknown education field'
                WHEN c.exit_reason IS NOT NULL AND er.exit_reason_id IS NULL THEN 'unknown exit reason'
                WHEN (c.exit_date IS NULL) <> (c.exit_reason IS NULL)     THEN 'exit date and exit reason must come together'
                WHEN (c.attrition = 'YES') <> (c.exit_date IS NOT NULL)   THEN 'attrition flag disagrees with exit date'
                WHEN c.prior_experience_years < 0                         THEN 'years at company exceed total working years'
           END                                                          AS reject_reason
    FROM cleaned c
    LEFT JOIN education_levels el ON el.education_level_id = c.education_level_id
    LEFT JOIN education_fields ef ON UPPER(ef.field_name) = UPPER(c.education_field)
    LEFT JOIN exit_reasons er     ON UPPER(er.reason_name) = UPPER(c.exit_reason);

    INSERT INTO hr_dw.etl_rejected_rows (batch_id, source_table, source_key, reason)
    SELECT p_batch_id, 'hr_staging.stg_employees', COALESCE(employee_code, CONCAT('stg_row_id=', stg_row_id)),
           reject_reason
    FROM tmp_employees
    WHERE reject_reason IS NOT NULL;
    SET v_rejected = ROW_COUNT();

    INSERT INTO employees (employee_code, first_name, last_name, email, phone, gender, date_of_birth,
                           marital_status, education_level_id, education_field_id, distance_from_home_km,
                           prior_experience_years, prior_companies_count, hire_date, exit_date, exit_reason_id)
    SELECT employee_code, first_name, last_name, email, phone, gender, date_of_birth,
           marital_status, education_level_id, education_field_id, distance_from_home_km,
           prior_experience_years, prior_companies_count, hire_date, exit_date, exit_reason_id
    FROM tmp_employees
    WHERE reject_reason IS NULL
    ORDER BY employee_code;                      -- ids follow the employee codes: EMP000001 -> 1
    SET v_loaded = ROW_COUNT();

    SELECT COUNT(*) INTO v_staged FROM hr_staging.stg_employees;
    SELECT COUNT(*) INTO v_unique FROM tmp_employees;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'load employees', v_loaded, IF(v_rejected = 0, 'SUCCESS', 'WARNING'),
         CONCAT('staged ', v_staged, ', unique ', v_unique, ', loaded ', v_loaded, ', rejected ', v_rejected),
         v_started);
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'fix: duplicate employee rows removed', v_staged - v_unique,
         'SUCCESS', 'ROW_NUMBER() per employee_code, newest staging row kept', v_started);

    SELECT COALESCE(SUM(CAST(gender_raw AS BINARY) <> CAST(gender AS BINARY)), 0) INTO v_fixed
    FROM tmp_employees WHERE reject_reason IS NULL;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'fix: gender codes standardized', v_fixed,
         'SUCCESS', 'M / F -> Male / Female', v_started);

    SELECT COALESCE(SUM(prior_companies_count <> num_companies_worked - 1), 0) INTO v_fixed
    FROM tmp_employees WHERE reject_reason IS NULL;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'fix: NumCompaniesWorked recoded (D19)', v_fixed,
         'SUCCESS', 'value contradicted prior experience; recoded by the D19 rule', v_started);


    -- 3. Job history ---------------------------------------------------------------------
    SET v_step = 'job history', v_started = SYSDATE();
    DROP TEMPORARY TABLE IF EXISTS tmp_job_history;
    CREATE TEMPORARY TABLE tmp_job_history (INDEX (employee_id, effective_start_date))
    WITH ranked AS (
        SELECT s.*,
               TRIM(s.employee_code)                                    AS code,
               ROW_NUMBER() OVER (PARTITION BY TRIM(s.employee_code), s.effective_start_date
                                  ORDER BY s.stg_row_id DESC)           AS copy_no
        FROM hr_staging.stg_job_history s
    ),
    versions AS (
        -- end dates are recomputed: each version ends exactly when the next one starts
        SELECT r.*,
               LEAD(r.effective_start_date) OVER (PARTITION BY r.code ORDER BY r.effective_start_date) AS next_start
        FROM ranked r
        WHERE r.copy_no = 1
    )
    SELECT v.stg_row_id,
           v.code                                                       AS employee_code,
           v.effective_start_date,
           e.employee_id,
           dl.department_id,
           jr.job_role_id,
           jl.job_level_id,
           m.employee_id                                                AS manager_id,
           v.monthly_income,
           v.stock_option_level,
           TRIM(v.business_travel)                                      AS business_travel,
           TRIM(v.change_reason)                                        AS change_reason,
           v.next_start                                                 AS effective_end_date,
           NOT (v.effective_end_date <=> v.next_start)                  AS end_date_corrected,
           CAST(v.department AS BINARY) <> CAST(d.department_name AS BINARY)
             OR CAST(v.job_role AS BINARY) <> CAST(jr.role_name AS BINARY) AS label_cleaned,
           CASE WHEN e.employee_id IS NULL                              THEN 'unknown or rejected employee'
                WHEN dl.department_id IS NULL                           THEN 'unknown department label'
                WHEN jr.job_role_id IS NULL                             THEN 'unknown job role'
                WHEN jl.job_level_id IS NULL                            THEN 'unknown job level'
                WHEN djr.job_role_id IS NULL                            THEN 'role does not exist in this department'
                WHEN v.job_level NOT BETWEEN jr.min_job_level_id AND jr.max_job_level_id
                                                                        THEN 'job level outside the role''s range'
                WHEN v.monthly_income NOT BETWEEN jl.salary_band_min AND jl.salary_band_max
                                                                        THEN 'income outside the level''s salary band'
                WHEN v.manager_code IS NOT NULL AND m.employee_id IS NULL THEN 'unknown manager'
           END                                                          AS reject_reason
    FROM versions v
    LEFT JOIN employees e                   ON e.employee_code = v.code
    LEFT JOIN tmp_department_labels dl      ON dl.label = UPPER(TRIM(v.department))
    LEFT JOIN departments d                 ON d.department_id = dl.department_id
    LEFT JOIN job_roles jr                  ON UPPER(jr.role_name) = UPPER(TRIM(v.job_role))
    LEFT JOIN job_levels jl                 ON jl.job_level_id = v.job_level
    LEFT JOIN department_job_roles djr      ON djr.department_id = dl.department_id
                                           AND djr.job_role_id = jr.job_role_id
    LEFT JOIN employees m                   ON m.employee_code = TRIM(v.manager_code);

    INSERT INTO hr_dw.etl_rejected_rows (batch_id, source_table, source_key, reason)
    SELECT p_batch_id, 'hr_staging.stg_job_history', CONCAT(employee_code, ' ', effective_start_date), reject_reason
    FROM tmp_job_history
    WHERE reject_reason IS NOT NULL;
    SET v_rejected = ROW_COUNT();

    INSERT INTO employee_job_history (employee_id, department_id, job_role_id, job_level_id, manager_id,
                                      monthly_income, stock_option_level, business_travel, change_reason,
                                      effective_start_date, effective_end_date)
    SELECT employee_id, department_id, job_role_id, job_level_id, manager_id,
           monthly_income, stock_option_level, business_travel, change_reason,
           effective_start_date, effective_end_date
    FROM tmp_job_history
    WHERE reject_reason IS NULL
    ORDER BY employee_id, effective_start_date;
    SET v_loaded = ROW_COUNT();

    SELECT COUNT(*) INTO v_staged FROM hr_staging.stg_job_history;
    SELECT COUNT(*) INTO v_unique FROM tmp_job_history;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'load employee_job_history', v_loaded,
         IF(v_rejected = 0, 'SUCCESS', 'WARNING'),
         CONCAT('staged ', v_staged, ', unique ', v_unique, ', loaded ', v_loaded, ', rejected ', v_rejected),
         v_started);

    SELECT COALESCE(SUM(label_cleaned), 0) INTO v_fixed FROM tmp_job_history WHERE reject_reason IS NULL;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'fix: department / role labels standardized', v_fixed,
         'SUCCESS', 'TRIM + case-insensitive match + alias map (R&D, HR, Sales Dept)', v_started);

    SELECT COALESCE(SUM(end_date_corrected), 0) INTO v_fixed FROM tmp_job_history WHERE reject_reason IS NULL;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'check: end dates match LEAD(start)', v_fixed,
         IF(v_fixed = 0, 'SUCCESS', 'WARNING'), 'versions whose staged end date had to be corrected', v_started);


    -- 4. Projects ------------------------------------------------------------------------
    SET v_step = 'projects', v_started = SYSDATE();
    DROP TEMPORARY TABLE IF EXISTS tmp_projects;
    CREATE TEMPORARY TABLE tmp_projects
    WITH ranked AS (
        SELECT s.*,
               TRIM(s.project_code)                                     AS code,
               ROW_NUMBER() OVER (PARTITION BY TRIM(s.project_code) ORDER BY s.stg_row_id DESC) AS copy_no
        FROM hr_staging.stg_projects s
    )
    SELECT r.stg_row_id,
           r.code                                                       AS project_code,
           TRIM(r.project_name)                                         AS project_name,
           NULLIF(TRIM(r.description), '')                              AS description,
           dl.department_id,
           m.employee_id                                                AS project_manager_id,
           r.start_date,
           r.planned_end_date,
           r.actual_end_date,
           r.budget,
           TRIM(r.status)                                               AS status,
           TRIM(r.priority)                                             AS priority,
           CASE WHEN r.code IS NULL OR r.code NOT REGEXP '^PRJ[0-9]{5}$' THEN 'invalid project code'
                WHEN dl.department_id IS NULL                           THEN 'unknown department label'
                WHEN r.project_manager_code IS NOT NULL AND m.employee_id IS NULL THEN 'unknown project manager'
                WHEN (TRIM(r.status) IN ('Completed', 'Cancelled')) <> (r.actual_end_date IS NOT NULL)
                                                                        THEN 'actual end date must be set exactly when closed'
           END                                                          AS reject_reason
    FROM ranked r
    LEFT JOIN tmp_department_labels dl ON dl.label = UPPER(TRIM(r.department))
    LEFT JOIN employees m              ON m.employee_code = TRIM(r.project_manager_code)
    WHERE r.copy_no = 1 OR r.code IS NULL;

    INSERT INTO hr_dw.etl_rejected_rows (batch_id, source_table, source_key, reason)
    SELECT p_batch_id, 'hr_staging.stg_projects', COALESCE(project_code, CONCAT('stg_row_id=', stg_row_id)),
           reject_reason
    FROM tmp_projects
    WHERE reject_reason IS NOT NULL;
    SET v_rejected = ROW_COUNT();

    INSERT INTO projects (project_code, project_name, description, department_id, project_manager_id,
                          start_date, planned_end_date, actual_end_date, budget, status, priority)
    SELECT project_code, project_name, description, department_id, project_manager_id,
           start_date, planned_end_date, actual_end_date, budget, status, priority
    FROM tmp_projects
    WHERE reject_reason IS NULL
    ORDER BY project_code;                       -- ids follow the project codes: PRJ00001 -> 1
    SET v_loaded = ROW_COUNT();

    SELECT COUNT(*) INTO v_staged FROM hr_staging.stg_projects;
    SELECT COUNT(*) INTO v_unique FROM tmp_projects;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'load projects', v_loaded, IF(v_rejected = 0, 'SUCCESS', 'WARNING'),
         CONCAT('staged ', v_staged, ', unique ', v_unique, ', loaded ', v_loaded, ', rejected ', v_rejected),
         v_started);


    -- 5. Project assignments -------------------------------------------------------------
    SET v_step = 'project assignments', v_started = SYSDATE();
    DROP TEMPORARY TABLE IF EXISTS tmp_assignments;
    CREATE TEMPORARY TABLE tmp_assignments
    WITH ranked AS (
        SELECT s.*,
               TRIM(s.employee_code)                                    AS code,
               TRIM(s.project_code)                                     AS project,
               ROW_NUMBER() OVER (PARTITION BY TRIM(s.employee_code), TRIM(s.project_code), s.start_date
                                  ORDER BY s.stg_row_id DESC)           AS copy_no
        FROM hr_staging.stg_assignments s
    )
    SELECT r.stg_row_id,
           r.code                                                       AS employee_code,
           r.project                                                    AS project_code,
           e.employee_id,
           p.project_id,
           TRIM(r.project_role)                                         AS project_role,
           r.allocation_pct,
           r.start_date,
           r.end_date,
           CASE WHEN e.employee_id IS NULL                              THEN 'unknown or rejected employee'
                WHEN p.project_id IS NULL                               THEN 'unknown or rejected project'
                WHEN COALESCE(r.allocation_pct, 0) NOT BETWEEN 1 AND 100 THEN 'allocation outside 1-100 %'
                WHEN r.end_date < r.start_date                          THEN 'assignment ends before it starts'
           END                                                          AS reject_reason
    FROM ranked r
    LEFT JOIN employees e ON e.employee_code = r.code
    LEFT JOIN projects p  ON p.project_code = r.project
    WHERE r.copy_no = 1;

    INSERT INTO hr_dw.etl_rejected_rows (batch_id, source_table, source_key, reason)
    SELECT p_batch_id, 'hr_staging.stg_assignments', CONCAT(employee_code, ' ', project_code, ' ', start_date),
           reject_reason
    FROM tmp_assignments
    WHERE reject_reason IS NOT NULL;
    SET v_rejected = ROW_COUNT();

    INSERT INTO project_assignments (employee_id, project_id, project_role, allocation_pct, start_date, end_date)
    SELECT employee_id, project_id, project_role, allocation_pct, start_date, end_date
    FROM tmp_assignments
    WHERE reject_reason IS NULL
    ORDER BY start_date, employee_id, project_id;
    SET v_loaded = ROW_COUNT();

    SELECT COUNT(*) INTO v_staged FROM hr_staging.stg_assignments;
    SELECT COUNT(*) INTO v_unique FROM tmp_assignments;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'load project_assignments', v_loaded,
         IF(v_rejected = 0, 'SUCCESS', 'WARNING'),
         CONCAT('staged ', v_staged, ', unique ', v_unique, ', loaded ', v_loaded, ', rejected ', v_rejected),
         v_started);


    -- 6. Engagement surveys ----------------------------------------------------------------
    SET v_step = 'surveys', v_started = SYSDATE();
    DROP TEMPORARY TABLE IF EXISTS tmp_surveys;
    CREATE TEMPORARY TABLE tmp_surveys
    WITH ranked AS (
        SELECT s.*,
               TRIM(s.employee_code)                                    AS code,
               ROW_NUMBER() OVER (PARTITION BY TRIM(s.employee_code), YEAR(s.survey_date)
                                  ORDER BY s.stg_row_id DESC)           AS copy_no
        FROM hr_staging.stg_surveys s
    )
    SELECT r.stg_row_id,
           r.code                                                       AS employee_code,
           e.employee_id,
           r.survey_date,
           r.environment_satisfaction,
           r.job_satisfaction,
           r.relationship_satisfaction,
           r.job_involvement,
           r.work_life_balance,
           CASE WHEN e.employee_id IS NULL                              THEN 'unknown or rejected employee'
                WHEN r.survey_date IS NULL                              THEN 'missing survey date'
                WHEN COALESCE(LEAST(r.environment_satisfaction, r.job_satisfaction, r.relationship_satisfaction,
                                    r.job_involvement, r.work_life_balance), 0) < 1
                  OR GREATEST(r.environment_satisfaction, r.job_satisfaction, r.relationship_satisfaction,
                              r.job_involvement, r.work_life_balance) > 4  THEN 'survey score missing or outside 1-4'
           END                                                          AS reject_reason
    FROM ranked r
    LEFT JOIN employees e ON e.employee_code = r.code
    WHERE r.copy_no = 1;

    INSERT INTO hr_dw.etl_rejected_rows (batch_id, source_table, source_key, reason)
    SELECT p_batch_id, 'hr_staging.stg_surveys', CONCAT(employee_code, ' ', survey_date), reject_reason
    FROM tmp_surveys
    WHERE reject_reason IS NOT NULL;
    SET v_rejected = ROW_COUNT();

    INSERT INTO employee_surveys (employee_id, survey_date, environment_satisfaction, job_satisfaction,
                                  relationship_satisfaction, job_involvement, work_life_balance)
    SELECT employee_id, survey_date, environment_satisfaction, job_satisfaction,
           relationship_satisfaction, job_involvement, work_life_balance
    FROM tmp_surveys
    WHERE reject_reason IS NULL
    ORDER BY survey_date, employee_id;
    SET v_loaded = ROW_COUNT();

    SELECT COUNT(*) INTO v_staged FROM hr_staging.stg_surveys;
    SELECT COUNT(*) INTO v_unique FROM tmp_surveys;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'load employee_surveys', v_loaded,
         IF(v_rejected = 0, 'SUCCESS', 'WARNING'),
         CONCAT('staged ', v_staged, ', unique ', v_unique, ', loaded ', v_loaded, ', rejected ', v_rejected),
         v_started);


    -- 7. Performance reviews ---------------------------------------------------------------
    SET v_step = 'reviews', v_started = SYSDATE();
    DROP TEMPORARY TABLE IF EXISTS tmp_reviews;
    CREATE TEMPORARY TABLE tmp_reviews
    WITH ranked AS (
        -- a review can be resubmitted with a corrected score: keep the latest submission
        SELECT s.*,
               TRIM(s.employee_code)                                    AS code,
               ROW_NUMBER() OVER (PARTITION BY TRIM(s.employee_code), s.review_year
                                  ORDER BY s.stg_row_id DESC)           AS copy_no
        FROM hr_staging.stg_reviews s
    )
    SELECT r.stg_row_id,
           r.code                                                       AS employee_code,
           r.review_year,
           r.review_date,
           e.employee_id,
           rv.employee_id                                               AS reviewer_id,
           p.project_id,
           rl.rating_id,
           r.kpi_score,
           r.training_sessions,
           CASE UPPER(TRIM(r.over_time)) WHEN 'YES' THEN TRUE WHEN 'NO' THEN FALSE END AS overtime_flag,
           NULLIF(TRIM(r.comments), '')                                 AS comments,
           CASE WHEN e.employee_id IS NULL                              THEN 'unknown or rejected employee'
                WHEN rv.employee_id IS NULL                             THEN 'unknown reviewer'
                WHEN rv.employee_id = e.employee_id                     THEN 'employee cannot review themselves'
                WHEN r.project_code IS NOT NULL AND p.project_id IS NULL THEN 'unknown or rejected project'
                WHEN rl.rating_id IS NULL                               THEN 'unknown performance rating'
                WHEN COALESCE(UPPER(TRIM(r.over_time)), '') NOT IN ('YES', 'NO') THEN 'overtime must be Yes or No'
           END                                                          AS reject_reason
    FROM ranked r
    LEFT JOIN employees e                  ON e.employee_code = r.code
    LEFT JOIN employees rv                 ON rv.employee_code = TRIM(r.reviewer_code)
    LEFT JOIN projects p                   ON p.project_code = TRIM(r.project_code)
    LEFT JOIN performance_rating_levels rl ON rl.rating_id = r.performance_rating
    WHERE r.copy_no = 1;

    INSERT INTO hr_dw.etl_rejected_rows (batch_id, source_table, source_key, reason)
    SELECT p_batch_id, 'hr_staging.stg_reviews', CONCAT(employee_code, ' ', review_year), reject_reason
    FROM tmp_reviews
    WHERE reject_reason IS NOT NULL;
    SET v_rejected = ROW_COUNT();

    INSERT INTO performance_reviews (employee_id, review_year, review_date, reviewer_id, project_id, rating_id,
                                     kpi_score, training_sessions, overtime_flag, comments)
    SELECT employee_id, review_year, review_date, reviewer_id, project_id, rating_id,
           kpi_score, training_sessions, overtime_flag, comments
    FROM tmp_reviews
    WHERE reject_reason IS NULL
    ORDER BY review_date, employee_id;
    SET v_loaded = ROW_COUNT();

    SELECT COUNT(*) INTO v_staged FROM hr_staging.stg_reviews;
    SELECT COUNT(*) INTO v_unique FROM tmp_reviews;
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'load performance_reviews', v_loaded,
         IF(v_rejected = 0, 'SUCCESS', 'WARNING'),
         CONCAT('staged ', v_staged, ', unique ', v_unique, ', loaded ', v_loaded, ', rejected ', v_rejected),
         v_started);
    CALL hr_dw.sp_etl_log_step(p_batch_id, 'fix: review resubmissions removed', v_staged - v_unique,
         'SUCCESS', 'ROW_NUMBER() per employee and review year, latest submission kept', v_started);


    -- 8. Finish ----------------------------------------------------------------------------
    DROP TEMPORARY TABLE IF EXISTS tmp_department_labels, tmp_employees, tmp_job_history, tmp_projects,
                                   tmp_assignments, tmp_surveys, tmp_reviews;
    CALL hr_dw.sp_etl_finish_batch(p_batch_id, NULL);
    COMMIT;
END$$


-- -----------------------------------------------------------------------------
-- sp_change_employee_position
--
-- A job change is never an UPDATE of the current version: the current version is closed on
-- p_effective_date (half-open periods, so its end = the new start) and a new open version is
-- inserted. Business rules checked here, each with a readable error (SQLSTATE 45000):
--   employee exists and is active; effective date after the current version's start and not
--   in the future; the position rules of sp_validate_position; something changed.
-- p_change_reason NULL = derived (refinement E8): Promotion (level up) > Transfer (department)
--   > Role Change > Manager Change > Salary Revision — the same priority the synthesizer used.
-- Does NOT commit. The application calls it and then hr_dw.sp_scd2_apply_employee in the
-- same transaction (decision E3), so OLTP and warehouse change together or not at all.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_change_employee_position(
    IN  p_employee_id        INT UNSIGNED,
    IN  p_department_id      TINYINT UNSIGNED,
    IN  p_job_role_id        TINYINT UNSIGNED,
    IN  p_job_level_id       TINYINT UNSIGNED,
    IN  p_manager_id         INT UNSIGNED,
    IN  p_monthly_income     DECIMAL(10,2),
    IN  p_stock_option_level TINYINT UNSIGNED,
    IN  p_business_travel    VARCHAR(20),
    IN  p_change_reason      VARCHAR(20),
    IN  p_effective_date     DATE,
    OUT p_history_id         INT UNSIGNED)
    COMMENT 'Close the current job version and open a new one; the caller commits'
BEGIN
    DECLARE v_history_id INT UNSIGNED;
    DECLARE v_start      DATE;
    DECLARE v_exit_date  DATE;
    DECLARE v_department TINYINT UNSIGNED;
    DECLARE v_role       TINYINT UNSIGNED;
    DECLARE v_level      TINYINT UNSIGNED;
    DECLARE v_manager    INT UNSIGNED;
    DECLARE v_income     DECIMAL(10,2);
    DECLARE v_stock      TINYINT UNSIGNED;
    DECLARE v_travel     VARCHAR(20);
    DECLARE v_message    VARCHAR(128);

    -- lock the current version, so two simultaneous changes cannot both close it
    SELECT h.history_id, h.effective_start_date, e.exit_date, h.department_id, h.job_role_id,
           h.job_level_id, h.manager_id, h.monthly_income, h.stock_option_level, h.business_travel
    INTO v_history_id, v_start, v_exit_date, v_department, v_role,
         v_level, v_manager, v_income, v_stock, v_travel
    FROM employees e
    JOIN employee_job_history h ON h.employee_id = e.employee_id AND h.effective_end_date IS NULL
    WHERE e.employee_id = p_employee_id
    FOR UPDATE;

    IF v_history_id IS NULL THEN
        SET v_message = CONCAT('Employee ', COALESCE(p_employee_id, 'NULL'), ' does not exist');
    ELSEIF v_exit_date IS NOT NULL THEN
        SET v_message = CONCAT('Employee ', p_employee_id, ' left on ', v_exit_date, ' and can no longer change jobs');
    ELSEIF p_effective_date IS NULL OR p_effective_date <= v_start OR p_effective_date > CURDATE() THEN
        SET v_message = CONCAT('Effective date must be after ', v_start, ' and not in the future');
    END IF;
    IF v_message IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_message;
    END IF;

    CALL sp_validate_position(p_employee_id, p_department_id, p_job_role_id, p_job_level_id,
                              p_manager_id, p_monthly_income);

    IF p_department_id = v_department AND p_job_role_id = v_role AND p_job_level_id = v_level
       AND p_manager_id <=> v_manager AND p_monthly_income = v_income
       AND p_stock_option_level <=> v_stock AND p_business_travel <=> v_travel THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Nothing changed: the new position equals the current one';
    END IF;

    SET p_change_reason = COALESCE(NULLIF(TRIM(p_change_reason), ''),
        CASE WHEN p_job_level_id > v_level              THEN 'Promotion'
             WHEN p_department_id <> v_department       THEN 'Transfer'
             WHEN p_job_role_id <> v_role               THEN 'Role Change'
             WHEN NOT (p_manager_id <=> v_manager)      THEN 'Manager Change'
             ELSE 'Salary Revision' END);

    UPDATE employee_job_history SET effective_end_date = p_effective_date WHERE history_id = v_history_id;

    INSERT INTO employee_job_history (employee_id, department_id, job_role_id, job_level_id, manager_id,
                                      monthly_income, stock_option_level, business_travel, change_reason,
                                      effective_start_date)
    VALUES (p_employee_id, p_department_id, p_job_role_id, p_job_level_id, p_manager_id,
            p_monthly_income, p_stock_option_level, p_business_travel, p_change_reason,
            p_effective_date);
    SET p_history_id = LAST_INSERT_ID();
END$$


-- -----------------------------------------------------------------------------
-- sp_validate_position: the business rules every job position must satisfy — shared by
-- onboarding and job changes, so they are written once (decision B2). They need the reference
-- tables, which is why they live in the database rather than in the Python entity classes.
-- SIGNALs the first broken rule; returns nothing when the position is valid.
-- p_employee_id is NULL for someone not hired yet.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_validate_position(
    IN p_employee_id    INT UNSIGNED,
    IN p_department_id  TINYINT UNSIGNED,
    IN p_job_role_id    TINYINT UNSIGNED,
    IN p_job_level_id   TINYINT UNSIGNED,
    IN p_manager_id     INT UNSIGNED,
    IN p_monthly_income DECIMAL(10,2))
    COMMENT 'Position rules: role offered in department, level in role range, pay in band, active manager'
BEGIN
    DECLARE v_message VARCHAR(128);

    IF NOT EXISTS (SELECT 1 FROM department_job_roles
                   WHERE department_id = p_department_id AND job_role_id = p_job_role_id) THEN
        SET v_message = 'This job role does not exist in the selected department';
    ELSEIF NOT EXISTS (SELECT 1 FROM job_roles
                       WHERE job_role_id = p_job_role_id
                         AND p_job_level_id BETWEEN min_job_level_id AND max_job_level_id) THEN
        SET v_message = 'Job level is outside the allowed range for this role';
    ELSEIF NOT EXISTS (SELECT 1 FROM job_levels
                       WHERE job_level_id = p_job_level_id
                         AND p_monthly_income BETWEEN salary_band_min AND salary_band_max) THEN
        SET v_message = 'Monthly income is outside the salary band of this job level';
    ELSEIF p_manager_id IS NOT NULL
       AND (p_manager_id <=> p_employee_id
            OR NOT EXISTS (SELECT 1 FROM employees WHERE employee_id = p_manager_id AND exit_date IS NULL)) THEN
        SET v_message = 'The manager must be another active employee';
    END IF;

    IF v_message IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_message;
    END IF;
END$$


-- -----------------------------------------------------------------------------
-- sp_onboard_employee: hires a person — the employee row and their first job version
-- ('Hire') in one call. The next employee code is generated here (EMP + 6 digits); the UNIQUE
-- key on employee_code is the guard if two people are onboarded at the same moment.
-- Does NOT commit: the application calls hr_dw.sp_scd2_apply_employee in the same transaction,
-- so the new employee appears in the warehouse at once (dw_design.md 9.6: no late-arriving rows).
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_onboard_employee(
    IN  p_first_name             VARCHAR(50),
    IN  p_last_name              VARCHAR(50),
    IN  p_email                  VARCHAR(120),
    IN  p_phone                  VARCHAR(20),
    IN  p_gender                 VARCHAR(10),
    IN  p_date_of_birth          DATE,
    IN  p_marital_status         VARCHAR(10),
    IN  p_education_level_id     TINYINT UNSIGNED,
    IN  p_education_field_id     TINYINT UNSIGNED,
    IN  p_distance_from_home_km  TINYINT UNSIGNED,
    IN  p_prior_experience_years TINYINT UNSIGNED,
    IN  p_prior_companies_count  TINYINT UNSIGNED,
    IN  p_hire_date              DATE,
    IN  p_department_id          TINYINT UNSIGNED,
    IN  p_job_role_id            TINYINT UNSIGNED,
    IN  p_job_level_id           TINYINT UNSIGNED,
    IN  p_manager_id             INT UNSIGNED,
    IN  p_monthly_income         DECIMAL(10,2),
    IN  p_stock_option_level     TINYINT UNSIGNED,
    IN  p_business_travel        VARCHAR(20),
    OUT p_employee_id            INT UNSIGNED,
    OUT p_employee_code          CHAR(9))
    COMMENT 'Hire a person: employee row + first job version; the caller commits'
BEGIN
    IF p_hire_date IS NULL OR p_hire_date > CURDATE() THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The hire date must be today or earlier';
    END IF;

    CALL sp_validate_position(NULL, p_department_id, p_job_role_id, p_job_level_id,
                              p_manager_id, p_monthly_income);

    -- codes are fixed-width, so the highest code is the last one in the unique index
    SELECT CONCAT('EMP', LPAD(COALESCE(CAST(SUBSTRING(MAX(employee_code), 4) AS UNSIGNED), 0) + 1, 6, '0'))
    INTO p_employee_code
    FROM employees;

    INSERT INTO employees (employee_code, first_name, last_name, email, phone, gender, date_of_birth,
                           marital_status, education_level_id, education_field_id, distance_from_home_km,
                           prior_experience_years, prior_companies_count, hire_date)
    VALUES (p_employee_code, p_first_name, p_last_name, p_email, p_phone, p_gender, p_date_of_birth,
            p_marital_status, p_education_level_id, p_education_field_id, p_distance_from_home_km,
            p_prior_experience_years, p_prior_companies_count, p_hire_date);
    SET p_employee_id = LAST_INSERT_ID();

    INSERT INTO employee_job_history (employee_id, department_id, job_role_id, job_level_id, manager_id,
                                      monthly_income, stock_option_level, business_travel, change_reason,
                                      effective_start_date)
    VALUES (p_employee_id, p_department_id, p_job_role_id, p_job_level_id, p_manager_id,
            p_monthly_income, p_stock_option_level, p_business_travel, 'Hire', p_hire_date);
END$$


-- -----------------------------------------------------------------------------
-- sp_record_exit: an employee leaves. Employees are never deleted (their history and reviews
-- must stay reportable); leaving is recorded as an exit date and reason. Open project
-- assignments end on the exit date, and assignments that would only have started later are
-- removed. The latest job version stays open (the OLTP convention, 03_oltp_views.sql).
-- Does NOT commit: the application applies the warehouse update in the same transaction.
-- -----------------------------------------------------------------------------

CREATE PROCEDURE sp_record_exit(
    IN  p_employee_id        INT UNSIGNED,
    IN  p_exit_date          DATE,
    IN  p_exit_reason_id     TINYINT UNSIGNED,
    OUT p_assignments_closed INT)
    COMMENT 'Record an exit and close the open assignments; the caller commits'
BEGIN
    DECLARE v_found       INT UNSIGNED;
    DECLARE v_exit_date   DATE;
    DECLARE v_start       DATE;
    DECLARE v_last_review DATE;
    DECLARE v_message     VARCHAR(128);

    SELECT e.employee_id, e.exit_date, h.effective_start_date
    INTO v_found, v_exit_date, v_start
    FROM employees e
    JOIN employee_job_history h ON h.employee_id = e.employee_id AND h.effective_end_date IS NULL
    WHERE e.employee_id = p_employee_id
    FOR UPDATE;

    SELECT MAX(review_date) INTO v_last_review FROM performance_reviews WHERE employee_id = p_employee_id;

    IF v_found IS NULL THEN
        SET v_message = CONCAT('Employee ', COALESCE(p_employee_id, 'NULL'), ' does not exist');
    ELSEIF v_exit_date IS NOT NULL THEN
        SET v_message = CONCAT('Employee ', p_employee_id, ' already left on ', v_exit_date);
    ELSEIF p_exit_date IS NULL OR p_exit_date < v_start OR p_exit_date > CURDATE() THEN
        SET v_message = CONCAT('Exit date must be on or after ', v_start, ' (the last job change) and not in the future');
    ELSEIF p_exit_date < v_last_review THEN
        SET v_message = CONCAT('Exit date cannot be before the employee''s last review (', v_last_review, ')');
    ELSEIF NOT EXISTS (SELECT 1 FROM exit_reasons WHERE exit_reason_id = p_exit_reason_id) THEN
        SET v_message = 'Choose a valid exit reason';
    END IF;
    IF v_message IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_message;
    END IF;

    UPDATE employees SET exit_date = p_exit_date, exit_reason_id = p_exit_reason_id
    WHERE employee_id = p_employee_id;

    DELETE FROM project_assignments WHERE employee_id = p_employee_id AND start_date > p_exit_date;
    SET p_assignments_closed = ROW_COUNT();
    UPDATE project_assignments SET end_date = p_exit_date
    WHERE employee_id = p_employee_id AND (end_date IS NULL OR end_date > p_exit_date);
    SET p_assignments_closed = p_assignments_closed + ROW_COUNT();
END$$

DELIMITER ;

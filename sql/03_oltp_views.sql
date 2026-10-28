-- =============================================================================
-- 03_oltp_views.sql  —  read views over hr_oltp
--
-- vw_employee_current  : each employee with their current job version and all lookup
--                        labels resolved — the main read model for the application.
-- vw_employee_snapshot : rebuilds the IBM-style employee row (age, tenure, years in role,
--                        years since promotion, last salary hike, latest survey and review)
--                        from the normalized tables. It proves the decomposition is lossless:
--                        nothing derivable was stored, yet everything can be reconstructed.
--                        The anchor dates (role_since, manager_since, last_promotion_date) are
--                        exposed too, so tenure can be computed as of any date — the ETL
--                        reconciliation uses them with the synthesizer's snapshot date.
--
-- Convention: an employee's latest job version stays open (effective_end_date IS NULL)
-- even after they leave; leaving is recorded in employees.exit_date. Every employee
-- therefore has exactly one current version.
-- =============================================================================

USE hr_oltp;

CREATE OR REPLACE VIEW vw_employee_current AS
SELECT
    e.employee_id,
    e.employee_code,
    e.first_name,
    e.last_name,
    CONCAT(e.first_name, ' ', e.last_name)  AS full_name,
    e.email,
    e.phone,
    e.gender,
    e.date_of_birth,
    e.marital_status,
    el.level_name                           AS education_level,
    ef.field_name                           AS education_field,
    e.distance_from_home_km,
    e.prior_experience_years,
    e.prior_companies_count,
    e.hire_date,
    e.exit_date,
    er.reason_name                          AS exit_reason,
    er.is_voluntary                         AS is_voluntary_exit,
    e.is_active,
    h.history_id                            AS current_history_id,
    d.department_id,
    d.department_code,
    d.department_name,
    r.job_role_id,
    r.role_name                             AS job_role,
    jl.job_level_id                         AS job_level,
    jl.level_title                          AS job_level_title,
    h.manager_id,
    CONCAT(m.first_name, ' ', m.last_name)  AS manager_name,
    h.monthly_income,
    h.stock_option_level,
    h.business_travel,
    h.change_reason                         AS last_change_reason,
    h.effective_start_date                  AS position_since
FROM employees e
JOIN employee_job_history h ON h.employee_id = e.employee_id AND h.effective_end_date IS NULL
JOIN departments d          ON d.department_id = h.department_id
JOIN job_roles r            ON r.job_role_id = h.job_role_id
JOIN job_levels jl          ON jl.job_level_id = h.job_level_id
JOIN education_levels el    ON el.education_level_id = e.education_level_id
JOIN education_fields ef    ON ef.education_field_id = e.education_field_id
LEFT JOIN exit_reasons er   ON er.exit_reason_id = e.exit_reason_id
LEFT JOIN employees m       ON m.employee_id = h.manager_id;


CREATE OR REPLACE VIEW vw_employee_snapshot AS
WITH people AS (
    -- the snapshot is taken on the exit date for leavers, otherwise today
    SELECT e.*, COALESCE(e.exit_date, CURDATE()) AS as_of_date
    FROM employees e
),
versions AS (
    SELECT h.employee_id,
           h.job_role_id,
           h.manager_id,
           h.monthly_income,
           h.change_reason,
           h.effective_start_date,
           ROW_NUMBER()          OVER w AS version_no,
           LAG(h.job_role_id)    OVER w AS prev_role_id,
           LAG(h.manager_id)     OVER w AS prev_manager_id,
           LAG(h.monthly_income) OVER w AS prev_income
    FROM employee_job_history h
    WINDOW w AS (PARTITION BY h.employee_id ORDER BY h.effective_start_date)
),
anchors AS (
    -- the date each current state began: latest role change, latest manager change, latest promotion
    SELECT employee_id,
           MAX(CASE WHEN version_no = 1 OR prev_role_id <> job_role_id
                    THEN effective_start_date END)                   AS role_since,
           MAX(CASE WHEN version_no = 1 OR NOT (prev_manager_id <=> manager_id)
                    THEN effective_start_date END)                   AS manager_since,
           MAX(CASE WHEN change_reason = 'Promotion'
                    THEN effective_start_date END)                   AS last_promotion_date
    FROM versions
    GROUP BY employee_id
),
salary_changes AS (
    SELECT employee_id,
           ROUND((monthly_income / prev_income - 1) * 100)          AS percent_salary_hike,
           ROW_NUMBER() OVER (PARTITION BY employee_id ORDER BY effective_start_date DESC) AS rn
    FROM versions
    WHERE prev_income IS NOT NULL AND monthly_income <> prev_income
),
latest_survey AS (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.employee_id ORDER BY s.survey_date DESC) AS rn
    FROM employee_surveys s
),
latest_review AS (
    SELECT rv.*, ROW_NUMBER() OVER (PARTITION BY rv.employee_id ORDER BY rv.review_year DESC) AS rn
    FROM performance_reviews rv
)
SELECT
    p.employee_id,
    p.employee_code,
    p.as_of_date,
    TIMESTAMPDIFF(YEAR, p.date_of_birth, p.as_of_date)                     AS age,
    IF(p.exit_date IS NULL, 'No', 'Yes')                                   AS attrition,
    h.business_travel,
    d.department_name                                                      AS department,
    p.distance_from_home_km                                                AS distance_from_home,
    p.education_level_id                                                   AS education,
    ef.field_name                                                          AS education_field,
    ls.environment_satisfaction,
    p.gender,
    ls.job_involvement,
    h.job_level_id                                                         AS job_level,
    r.role_name                                                            AS job_role,
    ls.job_satisfaction,
    p.marital_status,
    h.monthly_income,
    p.prior_companies_count,
    IF(lr.overtime_flag, 'Yes', 'No')                                      AS over_time,
    sc.percent_salary_hike,
    lr.rating_id                                                           AS performance_rating,
    ls.relationship_satisfaction,
    h.stock_option_level,
    p.prior_experience_years
        + TIMESTAMPDIFF(YEAR, p.hire_date, p.as_of_date)                   AS total_working_years,
    lr.training_sessions                                                   AS training_times_last_year,
    ls.work_life_balance,
    TIMESTAMPDIFF(YEAR, p.hire_date, p.as_of_date)                         AS years_at_company,
    TIMESTAMPDIFF(YEAR, a.role_since, p.as_of_date)                        AS years_in_current_role,
    TIMESTAMPDIFF(YEAR, COALESCE(a.last_promotion_date, p.hire_date), p.as_of_date)
                                                                           AS years_since_last_promotion,
    TIMESTAMPDIFF(YEAR, a.manager_since, p.as_of_date)                     AS years_with_curr_manager,
    a.role_since,
    a.manager_since,
    a.last_promotion_date
FROM people p
JOIN employee_job_history h  ON h.employee_id = p.employee_id AND h.effective_end_date IS NULL
JOIN departments d           ON d.department_id = h.department_id
JOIN job_roles r             ON r.job_role_id = h.job_role_id
JOIN education_fields ef     ON ef.education_field_id = p.education_field_id
JOIN anchors a               ON a.employee_id = p.employee_id
LEFT JOIN salary_changes sc  ON sc.employee_id = p.employee_id AND sc.rn = 1
LEFT JOIN latest_survey ls   ON ls.employee_id = p.employee_id AND ls.rn = 1
LEFT JOIN latest_review lr   ON lr.employee_id = p.employee_id AND lr.rn = 1;

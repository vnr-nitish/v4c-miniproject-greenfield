-- =============================================================================
-- 06_staging_ddl.sql  —  hr_staging: raw landing zone for the synthesized data
-- Design: docs/synthesizer_design.md
--
-- Staging imitates what a legacy source system would send: text labels instead of ids,
-- business keys (EMP000123), the original IBM columns including their quirks, and a few
-- deliberately planted data-quality issues. There are NO primary-key, unique, foreign-key or
-- check constraints on business columns — staging must accept whatever arrives; cleaning and
-- validation happen in the staging -> OLTP stored procedures.
-- Every table has a surrogate stg_row_id (load order, used to pick the latest duplicate),
-- a batch_id and a loaded_at timestamp. Plain indexes on business keys speed up the ETL joins.
-- WARNING: rebuild script — drops and recreates every table in hr_staging.
-- =============================================================================

USE hr_staging;

DROP TABLE IF EXISTS stg_assignments;
DROP TABLE IF EXISTS stg_projects;
DROP TABLE IF EXISTS stg_reviews;
DROP TABLE IF EXISTS stg_surveys;
DROP TABLE IF EXISTS stg_job_history;
DROP TABLE IF EXISTS stg_employees;

-- The scaled IBM dataset: all 35 IBM columns (snake_case) + synthetic identity and dates
CREATE TABLE stg_employees (
    stg_row_id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_id                    INT          NOT NULL,
    source_employee_number      INT          NULL,      -- IBM EmployeeNumber this row was derived from
    employee_code               VARCHAR(12)  NULL,
    first_name                  VARCHAR(50)  NULL,
    last_name                   VARCHAR(50)  NULL,
    email                       VARCHAR(120) NULL,
    phone                       VARCHAR(25)  NULL,
    date_of_birth               DATE         NULL,
    hire_date                   DATE         NULL,
    exit_date                   DATE         NULL,
    exit_reason                 VARCHAR(60)  NULL,
    -- IBM columns
    age                         INT          NULL,
    attrition                   VARCHAR(5)   NULL,
    business_travel             VARCHAR(25)  NULL,
    daily_rate                  INT          NULL,
    department                  VARCHAR(60)  NULL,
    distance_from_home          INT          NULL,
    education                   INT          NULL,
    education_field             VARCHAR(60)  NULL,
    employee_count              INT          NULL,
    environment_satisfaction    INT          NULL,
    gender                      VARCHAR(10)  NULL,
    hourly_rate                 INT          NULL,
    job_involvement             INT          NULL,
    job_level                   INT          NULL,
    job_role                    VARCHAR(60)  NULL,
    job_satisfaction            INT          NULL,
    marital_status              VARCHAR(15)  NULL,
    monthly_income              DECIMAL(10,2) NULL,
    monthly_rate                INT          NULL,
    num_companies_worked        INT          NULL,
    over18                      VARCHAR(3)   NULL,
    over_time                   VARCHAR(5)   NULL,
    percent_salary_hike         INT          NULL,
    performance_rating          INT          NULL,
    relationship_satisfaction   INT          NULL,
    standard_hours              INT          NULL,
    stock_option_level          INT          NULL,
    total_working_years         INT          NULL,
    training_times_last_year    INT          NULL,
    work_life_balance           INT          NULL,
    years_at_company            INT          NULL,
    years_in_current_role       INT          NULL,
    years_since_last_promotion  INT          NULL,
    years_with_curr_manager     INT          NULL,
    loaded_at                   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stg_row_id),
    INDEX ix_stg_employees_code (employee_code)
) ENGINE = InnoDB COMMENT = 'Scaled IBM dataset (raw, unvalidated)';

CREATE TABLE stg_job_history (
    stg_row_id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    batch_id              INT           NOT NULL,
    employee_code         VARCHAR(12)   NULL,
    version_number        INT           NULL,
    department            VARCHAR(60)   NULL,
    job_role              VARCHAR(60)   NULL,
    job_level             INT           NULL,
    manager_code          VARCHAR(12)   NULL,
    monthly_income        DECIMAL(10,2) NULL,
    stock_option_level    INT           NULL,
    business_travel       VARCHAR(25)   NULL,
    change_reason         VARCHAR(25)   NULL,
    effective_start_date  DATE          NULL,
    effective_end_date    DATE          NULL,
    loaded_at             DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stg_row_id),
    INDEX ix_stg_job_history_code (employee_code, effective_start_date)
) ENGINE = InnoDB COMMENT = 'Job versions (raw)';

CREATE TABLE stg_surveys (
    stg_row_id                 INT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_id                   INT          NOT NULL,
    employee_code              VARCHAR(12)  NULL,
    survey_date                DATE         NULL,
    environment_satisfaction   INT          NULL,
    job_satisfaction           INT          NULL,
    relationship_satisfaction  INT          NULL,
    job_involvement            INT          NULL,
    work_life_balance          INT          NULL,
    loaded_at                  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stg_row_id),
    INDEX ix_stg_surveys_code (employee_code)
) ENGINE = InnoDB COMMENT = 'Engagement surveys (raw)';

CREATE TABLE stg_reviews (
    stg_row_id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    batch_id            INT           NOT NULL,
    employee_code       VARCHAR(12)   NULL,
    review_year         INT           NULL,
    review_date         DATE          NULL,
    reviewer_code       VARCHAR(12)   NULL,
    project_code        VARCHAR(12)   NULL,
    performance_rating  INT           NULL,
    kpi_score           DECIMAL(5,2)  NULL,
    training_sessions   INT           NULL,
    over_time           VARCHAR(5)    NULL,
    comments            VARCHAR(500)  NULL,
    loaded_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stg_row_id),
    INDEX ix_stg_reviews_code_year (employee_code, review_year)
) ENGINE = InnoDB COMMENT = 'Performance reviews (raw)';

CREATE TABLE stg_projects (
    stg_row_id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    batch_id              INT           NOT NULL,
    project_code          VARCHAR(12)   NULL,
    project_name          VARCHAR(120)  NULL,
    description           VARCHAR(500)  NULL,
    department            VARCHAR(60)   NULL,
    project_manager_code  VARCHAR(12)   NULL,
    start_date            DATE          NULL,
    planned_end_date      DATE          NULL,
    actual_end_date       DATE          NULL,
    budget                DECIMAL(14,2) NULL,
    status                VARCHAR(15)   NULL,
    priority              VARCHAR(10)   NULL,
    loaded_at             DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stg_row_id),
    INDEX ix_stg_projects_code (project_code)
) ENGINE = InnoDB COMMENT = 'Projects (raw)';

CREATE TABLE stg_assignments (
    stg_row_id      INT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_id        INT          NOT NULL,
    employee_code   VARCHAR(12)  NULL,
    project_code    VARCHAR(12)  NULL,
    project_role    VARCHAR(30)  NULL,
    allocation_pct  INT          NULL,
    start_date      DATE         NULL,
    end_date        DATE         NULL,
    loaded_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stg_row_id),
    INDEX ix_stg_assignments_code (employee_code)
) ENGINE = InnoDB COMMENT = 'Project assignments (raw)';

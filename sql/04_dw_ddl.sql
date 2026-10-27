-- =============================================================================
-- 04_dw_ddl.sql  —  hr_dw: Kimball star schema
-- Design: docs/dw_design.md   Diagram: diagrams/dw_star_schema.drawio
--
-- Grain of the fact table: one row per performance review (one employee in one review year).
-- Surrogate keys are signed INT so that the special member project_sk = -1 can exist
-- (AUTO_INCREMENT never generates negative values) — see dw_design.md section 9.
-- WARNING: rebuild script — drops and recreates every table in hr_dw.
-- =============================================================================

USE hr_dw;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS fact_performance_reviews;
DROP TABLE IF EXISTS dim_employee;
DROP TABLE IF EXISTS dim_project;
DROP TABLE IF EXISTS dim_department;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS etl_rejected_rows;
DROP TABLE IF EXISTS etl_batch_log;
SET FOREIGN_KEY_CHECKS = 1;


-- -----------------------------------------------------------------------------
-- Dimensions
-- -----------------------------------------------------------------------------

CREATE TABLE dim_date (
    date_sk           INT               NOT NULL,          -- smart key YYYYMMDD
    full_date         DATE              NOT NULL,
    day_of_month      TINYINT UNSIGNED  NOT NULL,
    day_name          VARCHAR(9)        NOT NULL,
    day_of_week       TINYINT UNSIGNED  NOT NULL,          -- ISO: Monday = 1
    is_weekend        BOOLEAN           NOT NULL,
    week_of_year      TINYINT UNSIGNED  NOT NULL,
    month_number      TINYINT UNSIGNED  NOT NULL,
    month_name        VARCHAR(9)        NOT NULL,
    quarter_number    TINYINT UNSIGNED  NOT NULL,
    quarter_label     CHAR(2)           NOT NULL,
    calendar_year     SMALLINT UNSIGNED NOT NULL,
    year_month_label  CHAR(7)           NOT NULL,          -- e.g. 2024-03 (YEAR_MONTH is reserved in MySQL)
    fiscal_year       CHAR(9)           NOT NULL,          -- Indian FY, April-March, e.g. FY2023-24
    fiscal_quarter    CHAR(2)           NOT NULL,
    CONSTRAINT pk_dim_date           PRIMARY KEY (date_sk),
    CONSTRAINT uq_dim_date_full_date UNIQUE (full_date),
    CONSTRAINT ck_dim_date_key       CHECK (date_sk = YEAR(full_date) * 10000 + MONTH(full_date) * 100 + DAY(full_date)),
    INDEX ix_dim_date_year_month (calendar_year, month_number),
    INDEX ix_dim_date_fiscal_year (fiscal_year)
) ENGINE = InnoDB COMMENT = 'Calendar and Indian fiscal-year attributes, 2015-2030';

CREATE TABLE dim_department (
    department_sk    INT              NOT NULL AUTO_INCREMENT,
    department_id    TINYINT UNSIGNED NOT NULL,            -- natural key (hr_oltp)
    department_code  VARCHAR(10)      NOT NULL,
    department_name  VARCHAR(60)      NOT NULL,
    updated_at       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT pk_dim_department    PRIMARY KEY (department_sk),
    CONSTRAINT uq_dim_department_nk UNIQUE (department_id)
) ENGINE = InnoDB COMMENT = 'SCD Type 1';

CREATE TABLE dim_project (
    project_sk              INT               NOT NULL AUTO_INCREMENT,   -- -1 = No Project Assigned
    project_id              SMALLINT UNSIGNED NOT NULL,                  -- natural key; 0 = special member
    project_code            CHAR(8)           NOT NULL,
    project_name            VARCHAR(120)      NOT NULL,
    owning_department_name  VARCHAR(60)       NOT NULL,
    project_manager_name    VARCHAR(101)      NULL,
    start_date              DATE              NULL,
    planned_end_date        DATE              NULL,
    actual_end_date         DATE              NULL,
    planned_duration_days   SMALLINT          NULL,
    delay_days              SMALLINT          NULL,
    is_delayed              BOOLEAN           NULL,
    status                  VARCHAR(15)       NOT NULL,
    priority                VARCHAR(10)       NOT NULL,
    budget                  DECIMAL(14,2)     NULL,
    budget_band             VARCHAR(20)       NOT NULL,
    updated_at              DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT pk_dim_project    PRIMARY KEY (project_sk),
    -- MySQL forbids CHECK constraints on AUTO_INCREMENT columns (error 3818), so the special
    -- member is pinned through the natural key instead: OLTP project ids start at 1, so
    -- project_id = 0 can only be the special row, and UNIQUE allows exactly one of it.
    CONSTRAINT uq_dim_project_nk      UNIQUE (project_id),
    CONSTRAINT ck_dim_project_special CHECK ((project_id = 0) = (project_code = 'N/A'))
) ENGINE = InnoDB COMMENT = 'SCD Type 1, flattened; special member (sk -1, id 0) for reviews without a project';

CREATE TABLE dim_employee (
    employee_sk             INT               NOT NULL AUTO_INCREMENT,
    employee_id             INT UNSIGNED      NOT NULL,          -- natural key (hr_oltp)
    employee_code           CHAR(9)           NOT NULL,
    -- Type 1 attributes (overwritten on every version)
    full_name               VARCHAR(101)      NOT NULL,
    gender                  VARCHAR(10)       NOT NULL,
    date_of_birth           DATE              NOT NULL,
    marital_status          VARCHAR(10)       NOT NULL,
    education_level         VARCHAR(30)       NOT NULL,
    education_field         VARCHAR(60)       NOT NULL,
    distance_from_home_km   TINYINT UNSIGNED  NOT NULL,
    prior_experience_years  TINYINT UNSIGNED  NOT NULL,
    prior_companies_count   TINYINT UNSIGNED  NOT NULL,
    hire_date               DATE              NOT NULL,
    exit_date               DATE              NULL,
    exit_reason             VARCHAR(60)       NULL,
    is_voluntary_exit       BOOLEAN           NULL,
    attrition_flag          BOOLEAN           NOT NULL,
    -- Type 2 attributes (a change creates a new version)
    department_code         VARCHAR(10)       NOT NULL,
    department_name         VARCHAR(60)       NOT NULL,
    job_role                VARCHAR(60)       NOT NULL,
    job_level               TINYINT UNSIGNED  NOT NULL,
    job_level_title         VARCHAR(30)       NOT NULL,
    manager_employee_id     INT UNSIGNED      NULL,
    manager_name            VARCHAR(101)      NULL,
    monthly_income          DECIMAL(10,2)     NOT NULL,
    salary_band             VARCHAR(20)       NOT NULL,
    stock_option_level      TINYINT UNSIGNED  NOT NULL,
    business_travel         VARCHAR(20)       NOT NULL,
    change_reason           VARCHAR(20)       NOT NULL,
    -- SCD Type 2 control columns
    start_date              DATE              NOT NULL,
    end_date                DATE              NOT NULL DEFAULT '9999-12-31',   -- inclusive
    is_current              BOOLEAN           NOT NULL,
    version_number          SMALLINT UNSIGNED NOT NULL,
    row_hash                CHAR(32)          NOT NULL,          -- MD5 of the Type 2 columns
    -- same guarantee as in OLTP: at most one current version per employee
    current_version_key     INT UNSIGNED AS (IF(is_current, employee_id, NULL)) STORED,
    -- audit
    etl_batch_id            INT               NOT NULL,
    loaded_at               DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT pk_dim_employee            PRIMARY KEY (employee_sk),
    CONSTRAINT uq_dim_employee_nk_start   UNIQUE (employee_id, start_date),
    CONSTRAINT uq_dim_employee_nk_version UNIQUE (employee_id, version_number),
    CONSTRAINT uq_dim_employee_current    UNIQUE (current_version_key),
    CONSTRAINT ck_dim_employee_period     CHECK (end_date >= start_date),
    CONSTRAINT ck_dim_employee_current    CHECK (is_current = (end_date = '9999-12-31')),
    CONSTRAINT ck_dim_employee_attrition  CHECK (attrition_flag = (exit_date IS NOT NULL)),
    INDEX ix_dim_employee_nk_current (employee_id, is_current)
) ENGINE = InnoDB COMMENT = 'SCD Type 2 employee dimension';


-- -----------------------------------------------------------------------------
-- Fact
-- -----------------------------------------------------------------------------

CREATE TABLE fact_performance_reviews (
    review_id                  INT UNSIGNED     NOT NULL,    -- degenerate dimension (hr_oltp review id)
    date_sk                    INT              NOT NULL,
    employee_sk                INT              NOT NULL,    -- version valid on the review date
    department_sk              INT              NOT NULL,
    project_sk                 INT              NOT NULL,    -- -1 when no project
    performance_rating         TINYINT UNSIGNED NOT NULL,
    kpi_score                  DECIMAL(5,2)     NOT NULL,
    training_sessions          TINYINT UNSIGNED NOT NULL,
    overtime_flag              TINYINT UNSIGNED NOT NULL,
    monthly_income_at_review   DECIMAL(10,2)    NOT NULL,
    environment_satisfaction   TINYINT UNSIGNED NULL,       -- NULL if no survey that year
    job_satisfaction           TINYINT UNSIGNED NULL,
    relationship_satisfaction  TINYINT UNSIGNED NULL,
    work_life_balance          TINYINT UNSIGNED NULL,
    job_involvement            TINYINT UNSIGNED NULL,
    etl_batch_id               INT              NOT NULL,
    loaded_at                  DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_fact_performance_reviews PRIMARY KEY (review_id),
    CONSTRAINT fk_fact_date FOREIGN KEY (date_sk)
        REFERENCES dim_date (date_sk) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fact_employee FOREIGN KEY (employee_sk)
        REFERENCES dim_employee (employee_sk) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fact_department FOREIGN KEY (department_sk)
        REFERENCES dim_department (department_sk) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fact_project FOREIGN KEY (project_sk)
        REFERENCES dim_project (project_sk) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_fact_rating       CHECK (performance_rating BETWEEN 1 AND 4),
    CONSTRAINT ck_fact_kpi          CHECK (kpi_score BETWEEN 0 AND 100),
    CONSTRAINT ck_fact_overtime     CHECK (overtime_flag IN (0, 1)),
    CONSTRAINT ck_fact_income       CHECK (monthly_income_at_review > 0),
    CONSTRAINT ck_fact_environment  CHECK (environment_satisfaction BETWEEN 1 AND 4),
    CONSTRAINT ck_fact_job_sat      CHECK (job_satisfaction BETWEEN 1 AND 4),
    CONSTRAINT ck_fact_relationship CHECK (relationship_satisfaction BETWEEN 1 AND 4),
    CONSTRAINT ck_fact_wlb          CHECK (work_life_balance BETWEEN 1 AND 4),
    CONSTRAINT ck_fact_involvement  CHECK (job_involvement BETWEEN 1 AND 4),
    INDEX ix_fact_date_department (date_sk, department_sk)
) ENGINE = InnoDB COMMENT = 'Grain: one row per performance review';


-- -----------------------------------------------------------------------------
-- ETL metadata (operational, not part of the star)
-- -----------------------------------------------------------------------------

CREATE TABLE etl_batch_log (
    log_id         INT          NOT NULL AUTO_INCREMENT,
    batch_id       INT          NOT NULL,
    step_name      VARCHAR(60)  NOT NULL,
    rows_affected  INT          NOT NULL DEFAULT 0,
    status         VARCHAR(10)  NOT NULL,
    message        VARCHAR(500) NULL,
    started_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at    DATETIME     NULL,
    CONSTRAINT pk_etl_batch_log     PRIMARY KEY (log_id),
    CONSTRAINT ck_etl_batch_status  CHECK (status IN ('STARTED', 'SUCCESS', 'WARNING', 'FAILED')),
    INDEX ix_etl_batch_log_batch (batch_id)
) ENGINE = InnoDB COMMENT = 'One row per ETL step per run';

CREATE TABLE etl_rejected_rows (
    reject_id     INT          NOT NULL AUTO_INCREMENT,
    batch_id      INT          NOT NULL,
    source_table  VARCHAR(60)  NOT NULL,
    source_key    VARCHAR(60)  NOT NULL,
    reason        VARCHAR(255) NOT NULL,
    rejected_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_etl_rejected_rows PRIMARY KEY (reject_id),
    INDEX ix_etl_rejected_rows_batch (batch_id)
) ENGINE = InnoDB COMMENT = 'Source rows the ETL could not load, with the reason';

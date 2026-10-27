-- =============================================================================
-- 01_oltp_ddl.sql  —  hr_oltp: normalized operational database (3NF / BCNF)
-- Design: docs/oltp_design.md   Diagram: diagrams/oltp_er_diagram.drawio
--
-- 14 tables: 8 reference (lookup) + 6 core.
-- Conventions: fk_ / uq_ / ck_ / ix_ constraint prefixes; every foreign key is
-- ON DELETE RESTRICT (HR history is never cascade-deleted).
-- WARNING: rebuild script — drops and recreates every table in hr_oltp.
-- =============================================================================

USE hr_oltp;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS performance_reviews;
DROP TABLE IF EXISTS project_assignments;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS employee_surveys;
DROP TABLE IF EXISTS employee_job_history;
DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS exit_reasons;
DROP TABLE IF EXISTS performance_rating_levels;
DROP TABLE IF EXISTS education_fields;
DROP TABLE IF EXISTS education_levels;
DROP TABLE IF EXISTS department_job_roles;
DROP TABLE IF EXISTS job_roles;
DROP TABLE IF EXISTS job_levels;
DROP TABLE IF EXISTS departments;
SET FOREIGN_KEY_CHECKS = 1;


-- -----------------------------------------------------------------------------
-- Reference (lookup) tables
-- -----------------------------------------------------------------------------

CREATE TABLE departments (
    department_id    TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    department_code  VARCHAR(10)      NOT NULL,
    department_name  VARCHAR(60)      NOT NULL,
    CONSTRAINT pk_departments      PRIMARY KEY (department_id),
    CONSTRAINT uq_departments_code UNIQUE (department_code),
    CONSTRAINT uq_departments_name UNIQUE (department_name)
) ENGINE = InnoDB COMMENT = 'Organizational departments';

CREATE TABLE job_levels (
    job_level_id     TINYINT UNSIGNED NOT NULL,          -- IBM JobLevel code 1-5
    level_title      VARCHAR(30)      NOT NULL,
    salary_band_min  DECIMAL(10,2)    NOT NULL,
    salary_band_max  DECIMAL(10,2)    NOT NULL,
    CONSTRAINT pk_job_levels       PRIMARY KEY (job_level_id),
    CONSTRAINT uq_job_levels_title UNIQUE (level_title),
    CONSTRAINT ck_job_levels_id    CHECK (job_level_id BETWEEN 1 AND 5),
    CONSTRAINT ck_job_levels_band  CHECK (salary_band_min > 0 AND salary_band_max > salary_band_min)
) ENGINE = InnoDB COMMENT = 'Seniority levels with monthly salary bands (finding F3)';

CREATE TABLE job_roles (
    job_role_id       TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    role_name         VARCHAR(60)      NOT NULL,
    min_job_level_id  TINYINT UNSIGNED NOT NULL,
    max_job_level_id  TINYINT UNSIGNED NOT NULL,
    CONSTRAINT pk_job_roles           PRIMARY KEY (job_role_id),
    CONSTRAINT uq_job_roles_name      UNIQUE (role_name),
    CONSTRAINT fk_job_roles_min_level FOREIGN KEY (min_job_level_id)
        REFERENCES job_levels (job_level_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_job_roles_max_level FOREIGN KEY (max_job_level_id)
        REFERENCES job_levels (job_level_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_job_roles_level_range CHECK (min_job_level_id <= max_job_level_id)
) ENGINE = InnoDB COMMENT = 'Job roles with their allowed level range (finding F2)';

CREATE TABLE department_job_roles (
    department_id  TINYINT UNSIGNED NOT NULL,
    job_role_id    TINYINT UNSIGNED NOT NULL,
    CONSTRAINT pk_department_job_roles PRIMARY KEY (department_id, job_role_id),
    CONSTRAINT fk_djr_department FOREIGN KEY (department_id)
        REFERENCES departments (department_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_djr_job_role FOREIGN KEY (job_role_id)
        REFERENCES job_roles (job_role_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB COMMENT = 'Valid department-role pairs; resolves the M:N relationship (finding F1)';

CREATE TABLE education_levels (
    education_level_id  TINYINT UNSIGNED NOT NULL,       -- IBM Education code 1-5
    level_name          VARCHAR(30)      NOT NULL,
    CONSTRAINT pk_education_levels      PRIMARY KEY (education_level_id),
    CONSTRAINT uq_education_levels_name UNIQUE (level_name),
    CONSTRAINT ck_education_levels_id   CHECK (education_level_id BETWEEN 1 AND 5)
) ENGINE = InnoDB COMMENT = 'Education level (1 Below College ... 5 Doctor)';

CREATE TABLE education_fields (
    education_field_id  TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    field_name          VARCHAR(60)      NOT NULL,
    CONSTRAINT pk_education_fields      PRIMARY KEY (education_field_id),
    CONSTRAINT uq_education_fields_name UNIQUE (field_name)
) ENGINE = InnoDB COMMENT = 'Field of study';

CREATE TABLE performance_rating_levels (
    rating_id     TINYINT UNSIGNED NOT NULL,             -- IBM PerformanceRating scale 1-4
    rating_label  VARCHAR(20)      NOT NULL,
    hike_min_pct  DECIMAL(5,2)     NOT NULL,
    hike_max_pct  DECIMAL(5,2)     NOT NULL,
    CONSTRAINT pk_performance_rating_levels       PRIMARY KEY (rating_id),
    CONSTRAINT uq_performance_rating_levels_label UNIQUE (rating_label),
    CONSTRAINT ck_performance_rating_levels_id    CHECK (rating_id BETWEEN 1 AND 4),
    CONSTRAINT ck_performance_rating_levels_hike  CHECK (hike_min_pct >= 0 AND hike_max_pct >= hike_min_pct)
) ENGINE = InnoDB COMMENT = 'Performance rating scale with salary-hike guideline (findings F4, F12)';

CREATE TABLE exit_reasons (
    exit_reason_id  TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    reason_name     VARCHAR(60)      NOT NULL,
    is_voluntary    BOOLEAN          NOT NULL,
    CONSTRAINT pk_exit_reasons      PRIMARY KEY (exit_reason_id),
    CONSTRAINT uq_exit_reasons_name UNIQUE (reason_name)
) ENGINE = InnoDB COMMENT = 'Why an employee left; separates voluntary from involuntary attrition';


-- -----------------------------------------------------------------------------
-- Core tables
-- -----------------------------------------------------------------------------

CREATE TABLE employees (
    employee_id             INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    employee_code           CHAR(9)          NOT NULL,
    first_name              VARCHAR(50)      NOT NULL,
    last_name               VARCHAR(50)      NOT NULL,
    email                   VARCHAR(120)     NOT NULL,
    phone                   VARCHAR(20)      NOT NULL,
    gender                  VARCHAR(10)      NOT NULL,
    date_of_birth           DATE             NOT NULL,
    marital_status          VARCHAR(10)      NOT NULL,
    education_level_id      TINYINT UNSIGNED NOT NULL,
    education_field_id      TINYINT UNSIGNED NOT NULL,
    distance_from_home_km   TINYINT UNSIGNED NOT NULL,
    prior_experience_years  TINYINT UNSIGNED NOT NULL,
    prior_companies_count   TINYINT UNSIGNED NOT NULL,
    hire_date               DATE             NOT NULL,
    exit_date               DATE             NULL,
    exit_reason_id          TINYINT UNSIGNED NULL,
    is_active               BOOLEAN AS (exit_date IS NULL) VIRTUAL,
    created_at              DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT pk_employees       PRIMARY KEY (employee_id),
    CONSTRAINT uq_employees_code  UNIQUE (employee_code),
    CONSTRAINT uq_employees_email UNIQUE (email),
    CONSTRAINT fk_employees_education_level FOREIGN KEY (education_level_id)
        REFERENCES education_levels (education_level_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_employees_education_field FOREIGN KEY (education_field_id)
        REFERENCES education_fields (education_field_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_employees_exit_reason FOREIGN KEY (exit_reason_id)
        REFERENCES exit_reasons (exit_reason_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_employees_code_format  CHECK (employee_code REGEXP '^EMP[0-9]{6}$'),
    CONSTRAINT ck_employees_gender       CHECK (gender IN ('Male', 'Female')),
    CONSTRAINT ck_employees_marital      CHECK (marital_status IN ('Single', 'Married', 'Divorced')),
    CONSTRAINT ck_employees_distance     CHECK (distance_from_home_km BETWEEN 1 AND 50),
    CONSTRAINT ck_employees_prior_exp    CHECK (prior_experience_years <= 50),
    CONSTRAINT ck_employees_prior_comp   CHECK (prior_companies_count <= 20),
    CONSTRAINT ck_employees_adult_hire   CHECK (hire_date >= date_of_birth + INTERVAL 18 YEAR),
    CONSTRAINT ck_employees_exit_after_hire CHECK (exit_date IS NULL OR exit_date >= hire_date),
    CONSTRAINT ck_employees_exit_pair    CHECK ((exit_date IS NULL) = (exit_reason_id IS NULL)),
    INDEX ix_employees_name (last_name, first_name),
    INDEX ix_employees_exit_date (exit_date)
) ENGINE = InnoDB COMMENT = 'The person. Job data lives in employee_job_history.';

CREATE TABLE employee_job_history (
    history_id            INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    employee_id           INT UNSIGNED     NOT NULL,
    department_id         TINYINT UNSIGNED NOT NULL,
    job_role_id           TINYINT UNSIGNED NOT NULL,
    job_level_id          TINYINT UNSIGNED NOT NULL,
    manager_id            INT UNSIGNED     NULL,
    monthly_income        DECIMAL(10,2)    NOT NULL,
    stock_option_level    TINYINT UNSIGNED NOT NULL,
    business_travel       VARCHAR(20)      NOT NULL,
    change_reason         VARCHAR(20)      NOT NULL,
    effective_start_date  DATE             NOT NULL,
    effective_end_date    DATE             NULL,   -- exclusive (half-open period); NULL = current version
    -- employee_id while the version is open, otherwise NULL. The UNIQUE index below therefore
    -- allows any number of closed versions but at most ONE open (current) version per employee.
    current_version_key   INT UNSIGNED AS (IF(effective_end_date IS NULL, employee_id, NULL)) STORED,
    created_at            DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_employee_job_history PRIMARY KEY (history_id),
    CONSTRAINT uq_ejh_employee_start   UNIQUE (employee_id, effective_start_date),
    CONSTRAINT uq_ejh_current_version  UNIQUE (current_version_key),
    CONSTRAINT fk_ejh_employee FOREIGN KEY (employee_id)
        REFERENCES employees (employee_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ejh_department_role FOREIGN KEY (department_id, job_role_id)
        REFERENCES department_job_roles (department_id, job_role_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ejh_job_level FOREIGN KEY (job_level_id)
        REFERENCES job_levels (job_level_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ejh_manager FOREIGN KEY (manager_id)
        REFERENCES employees (employee_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_ejh_income        CHECK (monthly_income > 0),
    CONSTRAINT ck_ejh_stock_option  CHECK (stock_option_level BETWEEN 0 AND 3),
    CONSTRAINT ck_ejh_travel        CHECK (business_travel IN ('Non-Travel', 'Travel_Rarely', 'Travel_Frequently')),
    CONSTRAINT ck_ejh_change_reason CHECK (change_reason IN
        ('Hire', 'Promotion', 'Transfer', 'Role Change', 'Salary Revision', 'Manager Change')),
    CONSTRAINT ck_ejh_period        CHECK (effective_end_date IS NULL OR effective_end_date > effective_start_date),
    CONSTRAINT ck_ejh_not_own_manager CHECK (manager_id IS NULL OR manager_id <> employee_id),
    INDEX ix_ejh_department_start (department_id, effective_start_date)
) ENGINE = InnoDB COMMENT = 'Effective-dated job versions; single source of truth for SCD Type 2';

CREATE TABLE employee_surveys (
    survey_id                  INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    employee_id                INT UNSIGNED      NOT NULL,
    survey_date                DATE              NOT NULL,
    survey_year                SMALLINT UNSIGNED AS (YEAR(survey_date)) STORED,   -- derived, never typed in
    environment_satisfaction   TINYINT UNSIGNED  NOT NULL,
    job_satisfaction           TINYINT UNSIGNED  NOT NULL,
    relationship_satisfaction  TINYINT UNSIGNED  NOT NULL,
    job_involvement            TINYINT UNSIGNED  NOT NULL,
    work_life_balance          TINYINT UNSIGNED  NOT NULL,
    created_at                 DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_employee_surveys        PRIMARY KEY (survey_id),
    CONSTRAINT uq_surveys_employee_year   UNIQUE (employee_id, survey_year),
    CONSTRAINT fk_surveys_employee FOREIGN KEY (employee_id)
        REFERENCES employees (employee_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_surveys_environment  CHECK (environment_satisfaction BETWEEN 1 AND 4),
    CONSTRAINT ck_surveys_job          CHECK (job_satisfaction BETWEEN 1 AND 4),
    CONSTRAINT ck_surveys_relationship CHECK (relationship_satisfaction BETWEEN 1 AND 4),
    CONSTRAINT ck_surveys_involvement  CHECK (job_involvement BETWEEN 1 AND 4),
    CONSTRAINT ck_surveys_wlb          CHECK (work_life_balance BETWEEN 1 AND 4)
) ENGINE = InnoDB COMMENT = 'Yearly engagement survey (scales 1-4)';

CREATE TABLE projects (
    project_id          SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    project_code        CHAR(8)           NOT NULL,
    project_name        VARCHAR(120)      NOT NULL,
    description         VARCHAR(500)      NULL,
    department_id       TINYINT UNSIGNED  NOT NULL,
    project_manager_id  INT UNSIGNED      NULL,
    start_date          DATE              NOT NULL,
    planned_end_date    DATE              NOT NULL,
    actual_end_date     DATE              NULL,
    budget              DECIMAL(14,2)     NOT NULL,
    status              VARCHAR(15)       NOT NULL,
    priority            VARCHAR(10)       NOT NULL,
    created_at          DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT pk_projects      PRIMARY KEY (project_id),
    CONSTRAINT uq_projects_code UNIQUE (project_code),
    CONSTRAINT fk_projects_department FOREIGN KEY (department_id)
        REFERENCES departments (department_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_projects_manager FOREIGN KEY (project_manager_id)
        REFERENCES employees (employee_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_projects_code_format CHECK (project_code REGEXP '^PRJ[0-9]{5}$'),
    CONSTRAINT ck_projects_budget      CHECK (budget > 0),
    CONSTRAINT ck_projects_status      CHECK (status IN ('Planned', 'Active', 'On Hold', 'Completed', 'Cancelled')),
    CONSTRAINT ck_projects_priority    CHECK (priority IN ('Low', 'Medium', 'High', 'Critical')),
    CONSTRAINT ck_projects_planned_end CHECK (planned_end_date >= start_date),
    CONSTRAINT ck_projects_actual_end  CHECK (actual_end_date IS NULL OR actual_end_date >= start_date),
    -- a project has an actual end date exactly when it is finished
    CONSTRAINT ck_projects_closed      CHECK ((status IN ('Completed', 'Cancelled')) = (actual_end_date IS NOT NULL)),
    INDEX ix_projects_status (status)
) ENGINE = InnoDB COMMENT = 'Projects; planned vs actual end enables delay analysis';

CREATE TABLE project_assignments (
    assignment_id   INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    employee_id     INT UNSIGNED      NOT NULL,
    project_id      SMALLINT UNSIGNED NOT NULL,
    project_role    VARCHAR(30)       NOT NULL,
    allocation_pct  TINYINT UNSIGNED  NOT NULL,
    start_date      DATE              NOT NULL,
    end_date        DATE              NULL,
    created_at      DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_project_assignments PRIMARY KEY (assignment_id),
    CONSTRAINT uq_assignments_emp_proj_start UNIQUE (employee_id, project_id, start_date),
    CONSTRAINT fk_assignments_employee FOREIGN KEY (employee_id)
        REFERENCES employees (employee_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_assignments_project FOREIGN KEY (project_id)
        REFERENCES projects (project_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_assignments_allocation CHECK (allocation_pct BETWEEN 1 AND 100),
    CONSTRAINT ck_assignments_period     CHECK (end_date IS NULL OR end_date >= start_date),
    INDEX ix_assignments_project_start (project_id, start_date)
) ENGINE = InnoDB COMMENT = 'Employee-to-project allocation (resolves M:N)';

CREATE TABLE performance_reviews (
    review_id          INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    employee_id        INT UNSIGNED      NOT NULL,
    review_year        SMALLINT UNSIGNED NOT NULL,   -- performance year being reviewed
    review_date        DATE              NOT NULL,   -- date the review was held (same or next year)
    reviewer_id        INT UNSIGNED      NOT NULL,
    project_id         SMALLINT UNSIGNED NULL,       -- primary project that year
    rating_id          TINYINT UNSIGNED  NOT NULL,
    kpi_score          DECIMAL(5,2)      NOT NULL,
    training_sessions  TINYINT UNSIGNED  NOT NULL,
    overtime_flag      BOOLEAN           NOT NULL,
    comments           VARCHAR(500)      NULL,
    created_at         DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_performance_reviews    PRIMARY KEY (review_id),
    CONSTRAINT uq_reviews_employee_year  UNIQUE (employee_id, review_year),
    CONSTRAINT fk_reviews_employee FOREIGN KEY (employee_id)
        REFERENCES employees (employee_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_reviews_reviewer FOREIGN KEY (reviewer_id)
        REFERENCES employees (employee_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_reviews_project FOREIGN KEY (project_id)
        REFERENCES projects (project_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_reviews_rating FOREIGN KEY (rating_id)
        REFERENCES performance_rating_levels (rating_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_reviews_year          CHECK (review_year BETWEEN 2000 AND 2100),
    CONSTRAINT ck_reviews_date_in_cycle CHECK (YEAR(review_date) BETWEEN review_year AND review_year + 1),
    CONSTRAINT ck_reviews_kpi           CHECK (kpi_score BETWEEN 0 AND 100),
    CONSTRAINT ck_reviews_training      CHECK (training_sessions <= 20),
    CONSTRAINT ck_reviews_not_self      CHECK (reviewer_id <> employee_id),
    INDEX ix_reviews_year_rating (review_year, rating_id)
) ENGINE = InnoDB COMMENT = 'One annual performance review per employee';

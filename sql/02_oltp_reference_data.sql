-- =============================================================================
-- 02_oltp_reference_data.sql  —  contents of the 8 lookup tables in hr_oltp
-- Every value is traceable to a profiling finding in docs/oltp_design.md.
-- Explicit IDs keep codes stable across rebuilds (the synthesizer and tests rely on them).
-- =============================================================================

USE hr_oltp;

-- Departments (IBM Department column)
INSERT INTO departments (department_id, department_code, department_name) VALUES
    (1, 'HR',  'Human Resources'),
    (2, 'RND', 'Research & Development'),
    (3, 'SAL', 'Sales');

-- Job levels: IBM JobLevel codes 1-5 with monthly salary bands (F3, rounded outward)
INSERT INTO job_levels (job_level_id, level_title, salary_band_min, salary_band_max) VALUES
    (1, 'Entry',         1000.00,  5000.00),
    (2, 'Intermediate',  2000.00, 10000.00),
    (3, 'Senior',        5000.00, 14000.00),
    (4, 'Lead',         11000.00, 18000.00),
    (5, 'Executive',    18000.00, 20000.00);

-- Job roles with their observed level range (F2)
INSERT INTO job_roles (job_role_id, role_name, min_job_level_id, max_job_level_id) VALUES
    (1, 'Healthcare Representative', 2, 4),
    (2, 'Human Resources',           1, 3),
    (3, 'Laboratory Technician',     1, 3),
    (4, 'Manager',                   3, 5),
    (5, 'Manufacturing Director',    2, 4),
    (6, 'Research Director',         3, 5),
    (7, 'Research Scientist',        1, 3),
    (8, 'Sales Executive',           2, 4),
    (9, 'Sales Representative',      1, 2);

-- The 11 valid department-role pairs (F1); Manager is the only role in every department
INSERT INTO department_job_roles (department_id, job_role_id) VALUES
    (1, 2), (1, 4),                                   -- Human Resources
    (2, 1), (2, 3), (2, 4), (2, 5), (2, 6), (2, 7),   -- Research & Development
    (3, 4), (3, 8), (3, 9);                           -- Sales

-- Education levels: IBM Education codes 1-5
INSERT INTO education_levels (education_level_id, level_name) VALUES
    (1, 'Below College'),
    (2, 'College'),
    (3, 'Bachelor'),
    (4, 'Master'),
    (5, 'Doctor');

-- Education fields (IBM EducationField column)
INSERT INTO education_fields (education_field_id, field_name) VALUES
    (1, 'Human Resources'),
    (2, 'Life Sciences'),
    (3, 'Marketing'),
    (4, 'Medical'),
    (5, 'Other'),
    (6, 'Technical Degree');

-- Performance rating scale: IBM official 1-4. Hike guideline for 3 and 4 from the data (F4);
-- 1 and 2 never occur in the data and are set below rating 3 (F12).
INSERT INTO performance_rating_levels (rating_id, rating_label, hike_min_pct, hike_max_pct) VALUES
    (1, 'Low',          0.00,  4.00),
    (2, 'Good',         5.00, 10.00),
    (3, 'Excellent',   11.00, 19.00),
    (4, 'Outstanding', 20.00, 25.00);

-- Exit reasons: voluntary vs involuntary attrition
INSERT INTO exit_reasons (exit_reason_id, reason_name, is_voluntary) VALUES
    (1, 'Better Opportunity',      TRUE),
    (2, 'Compensation',            TRUE),
    (3, 'Work-Life Balance',       TRUE),
    (4, 'Career Change',           TRUE),
    (5, 'Relocation',              TRUE),
    (6, 'Retirement',              TRUE),
    (7, 'Performance Termination', FALSE),
    (8, 'Layoff / Restructuring',  FALSE),
    (9, 'Contract End',            FALSE);

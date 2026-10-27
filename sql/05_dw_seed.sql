-- =============================================================================
-- 05_dw_seed.sql  —  special member rows in hr_dw
-- Only dim_project has a special member: reviews of employees with no project in
-- that year point to project_sk = -1 instead of a NULL foreign key.
-- Surrogate key -1 (not 0) because MySQL's AUTO_INCREMENT never generates negative values,
-- so the row is stored correctly regardless of sql_mode (see docs/dw_design.md section 9).
-- Natural key 0 never occurs in hr_oltp (project ids start at 1), so the fact load maps a
-- review without a project with COALESCE(project_id, 0).
-- =============================================================================

USE hr_dw;

INSERT INTO dim_project (
    project_sk, project_id, project_code, project_name, owning_department_name,
    project_manager_name, start_date, planned_end_date, actual_end_date,
    planned_duration_days, delay_days, is_delayed, status, priority, budget, budget_band
) VALUES (
    -1, 0, 'N/A', 'No Project Assigned', 'N/A',
    NULL, NULL, NULL, NULL,
    NULL, NULL, NULL, 'N/A', 'N/A', NULL, 'N/A'
);

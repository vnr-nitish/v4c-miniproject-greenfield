-- =============================================================================
-- 00_create_schemas.sql
-- Creates the three databases of the layered architecture:
--   hr_staging : raw landing zone for the synthesized data (no constraints)
--   hr_oltp    : normalized operational database (3NF / BCNF)
--   hr_dw      : dimensional data warehouse (Kimball star schema)
-- =============================================================================

CREATE DATABASE IF NOT EXISTS hr_staging
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE DATABASE IF NOT EXISTS hr_oltp
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE DATABASE IF NOT EXISTS hr_dw
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

# Project Journal

A chronological record of how the **Enterprise Employee Analytics & Data Warehouse System** was
built: every step, the problems met along the way, how they were resolved, and why each decision
was taken. This journal is the source for the final README and the project Google Doc.

---

## Step 0 — Project kickoff (2026-10-27)

- Created an empty working folder `Downloads/mini project` and opened it in VS Code.
- Studied the full problem statement (*Rising Stars – Greenfield Mini-Project, Version 2*, 5 pages):
  - **Goal:** migrate from disconnected legacy systems to a centralized, cloud-ready data warehouse
    for employee performance, attrition and project allocation.
  - **Users:** HR and Executive teams, through a Streamlit web application.
  - **Technologies:** Python 3 (OOP, pandas, Faker), MySQL (OLTP + OLAP), Streamlit, GitHub,
    Streamlit Community Cloud, draw.io.
  - **Six deliverables:** GitHub repository · data synthesizer script · editable ER and dimensional
    diagrams · SQL scripts (DDL, DML, procedures, CTEs, window functions) · Streamlit app ·
    README + Google Doc.
  - **Five phases:** (1) architecture, modeling, data ingestion · (2) advanced SQL & ETL ·
    (3) OOP Python backend · (4) Streamlit UI & dashboards · (5) version control & cloud.
  - **Evaluation:** data synthesis & scale · data warehousing (surrogate keys, star schema, SCD2) ·
    SQL mastery · Python architecture · presentation.
- Produced a structured plan before writing any code:
  - Three-layer database architecture: `hr_staging` → `hr_oltp` → `hr_dw`.
  - A feature-branch-per-phase Git workflow merged through pull requests.
  - A checkpoint at the end of every step, verified before moving on.
  - **Working principle:** go phase by phase; nothing proceeds until the current step is verified.

## Step 1 — Prerequisites audit

An audit of the machine found:

| Item | Finding | Action |
|---|---|---|
| Project folder | Empty | — |
| Python | 3.11.6 with pandas, streamlit, altair | Use a dedicated virtual environment |
| Faker, plotly, MySQL driver | Missing | Install in the venv (Step 2) |
| MySQL Server | Not installed (Workbench is only a client) | Installed **MySQL Community Server 8.0.46** — Windows service `MySQL80`, port 3306, auto-start |
| Dataset | Not present | Downloaded `WA_Fn-UseC_-HR-Employee-Attrition.csv` from Kaggle |
| GitHub repository | — | Created `vnr-nitish/v4c-miniproject-greenfield` |
| Git | The whole user home folder was already a Git repository | Project needs its own repository (Step 3) |
| GitHub CLI | Not installed | Not required — pull requests are opened on github.com |

**Dataset verification:** 1,470 rows × 35 columns, 0 nulls, 0 duplicate rows, `EmployeeNumber`
unique. Early observations: `EmployeeCount`, `Over18`, `StandardHours` are constant;
`PerformanceRating` only takes values 3 and 4; attrition is 16 % (237 Yes / 1,233 No);
`YearsAtCompany` reaches 40.

## Step 2 — Python virtual environment

```
python -m venv .venv
.venv\Scripts\pip install faker plotly mysql-connector-python python-dotenv pytest streamlit pandas altair
```

Verified versions: Faker 40.43.0 · plotly 7.1.0 · mysql-connector-python 26.10.0 ·
streamlit 1.66.1 · pandas 3.1.0 · numpy 2.4.6 · altair 6.3.0 · python-dotenv 1.2.4 · pytest 9.1.1.
**Why a venv:** isolates project dependencies from the global Python and makes
`requirements.txt` reproducible for Streamlit Community Cloud.

## Step 3 — Git repository

- Initialized a repository inside the project folder (separate from the home-folder repository),
  linked it to GitHub and pulled the existing `main` (initial README).
- Added `.gitignore` excluding `.venv/`, `.env`, `.streamlit/secrets.toml`, generated data and IDE files.
- Added `.env.example` as the credential template.

## Step 4 — Dedicated MySQL application user

**Why not root:** principle of least privilege — the application gets only the rights it needs,
and the root password is never written into the project.

```sql
CREATE USER 'hr_app'@'localhost' IDENTIFIED BY '********';
GRANT ALL PRIVILEGES ON hr_staging.* TO 'hr_app'@'localhost';
GRANT ALL PRIVILEGES ON hr_oltp.*    TO 'hr_app'@'localhost';
GRANT ALL PRIVILEGES ON hr_dw.*      TO 'hr_app'@'localhost';
GRANT CREATE ON *.* TO 'hr_app'@'localhost';
FLUSH PRIVILEGES;
```

**Problem:** re-running `CREATE USER` with a new password failed with
*Error 1396 — Operation CREATE USER failed*.
**Cause:** the first run had already created the user (the following GRANTs succeeded, which is
only possible for an existing user).
**Fix:** `ALTER USER 'hr_app'@'localhost' IDENTIFIED BY '...'`; verified with
`SHOW GRANTS FOR 'hr_app'@'localhost'` (4 grants).
The password is stored only in the local, git-ignored `.env` file.

## Step 5 — Connection test and server settings

A Python test connected as `hr_app` and confirmed:

| Check | Result |
|---|---|
| Connection | MySQL 8.0.46, user `hr_app@localhost` |
| Schemas | `hr_staging`, `hr_oltp`, `hr_dw` created (utf8mb4) |
| Table create + bulk insert | OK |
| Stored procedure using a CTE and `DENSE_RANK()` | Created, executed, correct ranks returned |

Server settings and their consequences for the design:

| Setting | Value | Consequence |
|---|---|---|
| `local_infile` | OFF | Bulk loading uses batched `executemany` inserts — works identically on the cloud database |
| `sql_mode` | strict (incl. `STRICT_TRANS_TABLES`, `ONLY_FULL_GROUP_BY`) | Invalid data raises errors instead of being silently altered |
| `log_bin_trust_function_creators` | OFF | Application user cannot create functions/triggers → all ETL and SCD2 logic in **stored procedures** |
| `max_allowed_packet` | 64 MB | Sufficient for 5,000-row insert batches |

Driver note: mysql-connector-python 26.x deprecates `cursor.stored_results()`; the DB layer
accounts for this.

## Step 6 — Project skeleton and first pull request

- Branch `feature/project-setup`: folder structure (`src/models`, `src/dal`, `src/synthesizer`,
  `sql`, `diagrams`, `pages`, `tests`, `docs`), dataset moved to `data/raw/`, `requirements.txt`
  pinned to the verified versions, Streamlit theme config, initial README.
- **Problem:** push rejected — *GH007: Your push would publish a private email address*.
  **Fix:** the repository uses the GitHub no-reply address
  (`170617683+vnr-nitish@users.noreply.github.com`), which keeps the e-mail private while still
  attributing every commit to the GitHub account.
- Opened **PR #1** → merged into `main`.

**Branching strategy:** one feature branch per phase, merged into `main` through a pull request:
`feature/project-setup` → `feature/database-design` → `feature/data-synthesizer` →
`feature/etl-pipeline` → `feature/oop-backend` → `feature/streamlit-data-entry` →
`feature/streamlit-dashboard` → `feature/deployment-docs`.

## Step 7 — Phase 1: OLTP schema design (branch `feature/database-design`)

1. **First proposal (v1, 10 tables)** — reviewed and **rejected**: it grouped columns together so
   all 35 could not be traced, and it was derived from the column list rather than from the data.
2. **Data profiling** — [`analysis/profile_dataset.py`](../analysis/profile_dataset.py) measured
   domains, functional dependencies and business rules, producing findings F1–F11.
   Key discoveries: Manager is the only role in several departments (M:N), each role has a fixed
   level range, the performance rating fully determines the salary-hike range, the three "rate"
   columns are uncorrelated noise, and `NumCompaniesWorked` is internally inconsistent.
3. **Redesign (v2, 14 tables: 8 reference + 6 core)** — every one of the 35 columns mapped
   (20 stored, 8 derived, 6 dropped, 1 lineage), normalized to BCNF, with effective-dated job
   history as the single source of truth for SCD Type 2.
4. **Decisions approved:**

| ID | Decision |
|---|---|
| D1 | 14-table structure (8 reference + 6 core) |
| D2 | `manager_id` kept inside `employee_job_history` (one timeline) |
| D3 | IBM official performance rating scale 1–4 |
| D4 | Survey scales enforced with CHECK constraints, not lookup tables |
| D5 | Volume option A — 100,000 synthetic employees (~1.5 M OLTP rows) |

Full design: [`docs/oltp_design.md`](oltp_design.md).

## Step 8 — ER diagram

- Editable draw.io diagram: [`diagrams/oltp_er_diagram.drawio`](../diagrams/oltp_er_diagram.drawio)
  — 14 tables, 102 columns, 20 relationships in crow's-foot notation; colour-coded
  (blue = reference, yellow = bridge, green = core); legend and key-integrity-rules panel.
- Verified before use: XML well-formed, no duplicate IDs, every relationship connects existing
  columns, no overlapping tables.
- **Review round 1 (rendered in draw.io):** content correct, three layout issues fixed:
  1. Data types such as `TINYINT UNSIGNED NULL` wrapped and were clipped → type column widened
     from 110 px to 160 px (tables 340 → 410 px, gaps 80 → 120 px).
  2. Relationship labels overlapped table text in the narrow gaps → labels removed; the FK column
     names (`manager_id`, `reviewer_id`, `project_manager_id`) already name each relationship, and
     the composite FK is explained in the *Key integrity rules* panel.
  3. Page-break lines cut through the diagram → page enlarged to 2620 × 1260 to fit everything.
  Note: draw.io's dark appearance inverts colours on screen only; the file uses light colours.
- **Review round 2 (light mode):** the pasted copy came from a stale editor buffer (pre-fix
  version), so the round-1 fixes were not yet visible. One further issue was found: the
  `projects.project_manager_id → employees` line ran diagonally across two tables. It now follows
  an explicit orthogonal path through the empty gaps between tables.
- **Review round 3:** rendered correctly in draw.io — types on one line, no overlapping text, routed
  line clear of all tables. **ER diagram approved.**

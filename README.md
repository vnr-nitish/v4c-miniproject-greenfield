# Enterprise Employee Analytics & Data Warehouse System

An end-to-end, object-oriented HR analytics platform: a Python data synthesizer scales the
IBM HR Attrition dataset to 100,000+ rows, MySQL holds a normalized OLTP database and a
star-schema data warehouse (with SCD Type 2 history), and a Streamlit web app provides 
data-entry forms and interactive dashboards.

**Live app:** https://v4c-miniproject-greenfield-rvuytjcpc39yzy8xnsjus4.streamlit.app
(data entry needs the editor passcode; the dashboards are public).

## Tech stack
Python 3.11 (OOP, pandas, Faker) · MySQL 8 · Streamlit · Plotly · draw.io · GitHub · Streamlit Community Cloud

## Project structure 
```
├── app.py                # Streamlit entry point (navigation, filters, warm-up)
├── views/                # Streamlit pages (data entry + dashboards); src/ui/ holds theme and 3D charts
├── src/
│   ├── db_manager.py     # Singleton DatabaseConnection (shared pool, transactions, error translation)
│   ├── exceptions.py     # application exceptions (validation, business rules, not found, …)
│   ├── models/           # Entity classes: Employee, Position, Project, Assignment, Review
│   ├── dal/              # Data Access Layer managers (employees, projects, reviews, analytics, ETL, schema)
│   └── synthesizer/      # Data synthesizer (pandas + Faker)
├── sql/                  # DDL, DML, stored procedures, CTEs, window functions, validation queries
├── scripts/              # command-line front ends: setup_database.py (SchemaManager), run_etl.py (EtlManager)
├── analysis/             # dataset profiling that drove the schema design
├── diagrams/             # Editable draw.io ER and dimensional-model diagrams
├── data/raw/             # IBM HR Analytics source dataset
├── tests/                # pytest tests (schema, synthesizer, ETL, entities, managers)
└── docs/                 # Design documents and project journal
```

## Local setup
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # then fill in your MySQL credentials
```

## Database setup
Builds `hr_staging`, `hr_oltp` (14 tables + reference data + views) and `hr_dw` (star schema)
by running the numbered scripts in `sql/` in order:
```bash
python scripts/setup_database.py              # refuses to rebuild if business data is loaded
python scripts/setup_database.py --force      # deliberate rebuild (drops and recreates tables)
python scripts/setup_database.py --only 07 08 # (re)install the stored procedures only — keeps all data
```
The scripts can also be run one by one in MySQL Workbench, in numeric order.

## Generate the synthetic dataset
Scales the 1,470-row IBM dataset to 100,000 employees with history, surveys, reviews, projects and
assignments (~1.85 million rows), validates it and loads it into `hr_staging`:
```bash
python -m src.synthesizer.run                          # full build + load (~3 minutes)
python -m src.synthesizer.run --employees 5000 --no-load   # quick build, no database needed
```
The run writes [`docs/synthesis_report.md`](docs/synthesis_report.md) (rule checks, fidelity,
realism comparison, timings) and CSV copies in `data/synthesized/`.

## Run the ETL
Stored procedures move the data `hr_staging → hr_oltp → hr_dw`: de-duplication and cleaning with
CTEs and window functions, SCD Type 2 for employees, point-in-time fact loading; every step is
logged in `hr_dw.etl_batch_log`.
```bash
python scripts/run_etl.py --migrate                # first time: staging -> OLTP, then the warehouse (~6 min)
python scripts/run_etl.py                          # later runs: incremental warehouse load (~50 s)
python scripts/run_etl.py --migrate --full-reload  # rebuild OLTP and warehouse from staging
```
[`sql/validation_queries.sql`](sql/validation_queries.sql) shows in MySQL Workbench that it worked
(reconciliation, SCD Type 2 history, a rolled-back department change).

## Run the web app
```bash
streamlit run app.py
```
Data entry (onboarding, Employee 360 job changes with SCD Type 2, projects, reviews), warehouse
dashboards (treemap, risk heatmap, YoY trends, DENSE_RANK leaderboard, project bubbles, career
timeline) and an ETL page. Set `APP_PASSCODE` to protect data
entry on a public deployment. Design: [`docs/ui_design.md`](docs/ui_design.md).

## Understanding the numbers on the dashboard
Full page-by-page explanation: [`docs/website_guide.md`](docs/website_guide.md). The key definitions:

**Active headcount (83,880)** — people employed today: employees whose current warehouse version has
no exit date. 100,000 people have ever been employed; 16,122 (16.1 %, the IBM dataset's attrition
rate) left between 2019 and June 2026 and stay in the database for history. Every hire made on the
site adds one.

**Review cycle** — once a year every employee gets one review of the previous year: performance year
2025 is reviewed between 15 January and 15 March 2026. Those dates fall in India's fiscal year
FY2025-26 (April 2025 – March 2026), so that cycle is called **FY2025-26**. There are seven cycles,
FY2019-20 to FY2025-26.

**Why the latest cycle has 83,188 reviews but headcount is 83,880** — they count different people:

| | People |
|---|---:|
| Active today **and** reviewed for 2025 | 82,421 |
| + reviewed for 2025, **left afterwards** (April – June 2026) | 767 |
| = reviews in cycle FY2025-26 | **83,188** |
| Active today **and** reviewed for 2025 | 82,421 |
| + active, **hired on or after 1 October 2025** — too new for the 2025 review | 1,459 |
| = active headcount | **83,880** |

The rule (as in real companies): an employee is reviewed for a year only if they **joined by
30 September** of that year (at least a quarter of the year to judge) and are **still employed on
the review date**. The 1,459 newest employees, including hires made on the site, get their first
review in the next cycle.

**Average rating (3.155)** — the mean of the cycle's ratings on the 1–4 scale (1 Low, 2 Good,
3 Excellent, 4 Outstanding). A single review is always a whole number; only the *average* has
decimals. Like the IBM data, the generated reviews use only 3 and 4, so the average is
3 + the share of outstanding reviews (15.5 %) = 3.155.

**KPI score (average 80.12)** — a 0–100 number on every review: how much of their targets the
employee achieved, next to the manager's rating.
- *Reviews entered on the site:* the reviewer types the KPI.
- *Generated reviews (the IBM data has no KPI):* the synthesizer draws it at random from a
  normal distribution **chosen by the review's rating — the only factor used**:

  | Rating | KPI centred on | Typical spread | Always between |
  |---|---:|---:|---|
  | 4 · Outstanding | 92 | ± 4 | 85 – 100 |
  | 3 · Excellent | 78 | ± 7 | 60 – 95 |
  | 2 · Good | 60 | ± 7 | 40 – 75 |
  | 1 · Low | 42 | ± 7 | 20 – 60 |

  A value outside the range is drawn again (not cut off), so no score piles up at a limit.
  Example: an Outstanding review might get 91.37, 95.02 or 88.64 — different people, different
  results, but always 85–100. Tenure, department or pay play no part: KPI depends on the rating
  only, so it agrees with it without being a copy of it. 1,389 reviews were "resubmitted" with a
  corrected KPI (± about 3 points; the ETL keeps the latest), so a few lie just outside their range.
- *The tile:* sum of the cycle's KPI scores ÷ number of reviews. 84.5 % of reviews are rated 3 and
  15.5 % rated 4, so 0.845 × ~78 + 0.155 × ~92 ≈ 80.

**Attrition (2.59 %, FY2025-26)** — leavers in the fiscal year ÷ average headcount × 100:
2,153 ÷ ((82,632 + 83,835) ÷ 2) = 2.59 %. ▼ -0.08 = 0.08 points lower than the year before (good, so green).

**Attrition-risk bands** — each active employee gets 0–14 points from 11 work-related warning signs
(overtime, entry level and no stock options 2 points each; under 2 years at the company, low
environment / job satisfaction, poor work-life balance, low involvement, frequent travel, more than
10 km from home, pay in the lower half of the level 1 point each). Bands: Low 0–2, Medium 3–5,
High 6–7, Very high 8+. Back-tested on history, 0.7 % of Low and 19.5 % of Very high employees left
within a year. Marital status, age and gender are deliberately not used.

**Open (332) and overdue (89) projects** — open = status Planned, Active or On Hold; overdue = open
with a planned end date before today, so this number grows by itself as dates pass.

## Deploy (Streamlit Community Cloud + Aiven MySQL)
1. **Cloud database:** a free [Aiven for MySQL](https://console.aiven.io/signup) service (real MySQL 8 — the
   stored procedures need it). In *Advanced configuration* set `mysql.sql_require_primary_key = off`.
2. **Load it from a laptop:** with the Aiven settings in `.env`, run `python scripts/setup_database.py` and
   `python -m src.synthesizer.run`. The free plan (1 CPU, 32 MB buffer pool) could not finish the hour-long
   migration transaction, so the finished OLTP and warehouse rows were copied from the local database
   (`mysqldump --no-create-info` → `mysql`) and verified table by table — identical data, seed 42.
   Tests against another settings file: `HR_ENV_FILE=.env.local python -m pytest`.
3. **App:** [share.streamlit.io](https://share.streamlit.io) → *Create app* → this repository, branch `main`,
   file `streamlit_app.py` (runs `app.py`), Python 3.11 → *Advanced settings → Secrets*:
   ```toml
   DB_HOST = "mysql-xxxxxxxx.aivencloud.com"
   DB_PORT = "12345"
   DB_USER = "avnadmin"
   DB_PASSWORD = "…"
   APP_PASSCODE = "…"        # unlocks data entry; dashboards stay public
   ```
   Root-level secrets become environment variables, which `DatabaseSettings.from_env()` reads — no code change.

## Python backend
Validated entity classes and one manager per area, all sharing a Singleton connection pool:
```python
from src.dal import AnalyticsFilter, AnalyticsManager, EmployeeManager

change = EmployeeManager().change_position(employee_id, new_position)   # department change = SCD Type 2
change.expired_version, change.new_version                              # the warehouse rows before / after

AnalyticsManager().top_performers(AnalyticsFilter(fiscal_years=("FY2025-26",)), top_n=5)   # DENSE_RANK()
```
Design, OOP principles and decisions: [`docs/backend_design.md`](docs/backend_design.md).

## Tests
```bash
python -m pytest -q                        # everything (needs MySQL with the ETL loaded)
python -m pytest tests/test_models.py -q   # entity rules only — no database needed
```
228 tests, about 5 minutes. Database tests run inside rolled-back transactions — they use the real
data and never change it; they skip themselves when MySQL or the loaded data is missing.

## Design documentation
- [`docs/oltp_design.md`](docs/oltp_design.md) — data dictionary, profiling findings, normalized OLTP design (§14: why more than five tables)
- [`docs/dw_design.md`](docs/dw_design.md) — star schema, SCD Type 2, surrogate keys
- [`docs/synthesizer_design.md`](docs/synthesizer_design.md) — how 1,470 rows become 100,000 realistic employees
- [`docs/etl_design.md`](docs/etl_design.md) — stored-procedure ETL, cleaning rules, SCD Type 2 algorithm, logging
- [`docs/backend_design.md`](docs/backend_design.md) — OOP backend: entities, Singleton, managers, error handling, analytics, attrition-risk score
- [`docs/project_journal.md`](docs/project_journal.md) — step-by-step record of how the project was built

## Dataset
[IBM HR Analytics Employee Attrition & Performance](https://www.kaggle.com/datasets/pavansubhasht/ibm-hr-analytics-attrition-dataset) — 1,470 rows, 35 columns.

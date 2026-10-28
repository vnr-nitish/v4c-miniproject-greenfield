# Enterprise Employee Analytics & Data Warehouse System

An end-to-end, object-oriented HR analytics platform: a Python data synthesizer scales the
IBM HR Attrition dataset to 100,000+ rows, MySQL holds a normalized OLTP database and a
star-schema data warehouse (with SCD Type 2 history), and a Streamlit web app provides
data-entry forms and interactive dashboards.

> 🚧 Work in progress. Full architecture, setup and data-warehousing documentation will be
> added as each phase is completed.

## Tech stack
Python 3.11 (OOP, pandas, Faker) · MySQL 8 · Streamlit · Plotly · draw.io · GitHub · Streamlit Community Cloud

## Project structure
```
├── app.py                # Streamlit entry point
├── pages/                # Streamlit pages (data entry + dashboards)
├── src/
│   ├── db_manager.py     # Singleton DatabaseConnection
│   ├── models/           # Entity classes: Employee, Project, Review
│   ├── dal/              # Data Access Layer managers (CRUD, analytics, ETL)
│   └── synthesizer/      # Data synthesizer (pandas + Faker)
├── sql/                  # DDL, DML, stored procedures, CTEs, window functions, validation queries
├── scripts/              # setup_database.py (builds the schemas), run_etl.py (runs the ETL)
├── analysis/             # dataset profiling that drove the schema design
├── diagrams/             # Editable draw.io ER and dimensional-model diagrams
├── data/raw/             # IBM HR Analytics source dataset
├── tests/                # pytest tests (schema constraints, synthesizer, ETL)
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

## Tests
```bash
python -m pytest -q        # schema constraints + synthesizer rules + ETL reconciliation
```
82 tests, about 3 minutes. The ETL tests need a completed ETL run; they skip themselves otherwise.

## Design documentation
- [`docs/oltp_design.md`](docs/oltp_design.md) — data dictionary, profiling findings, normalized OLTP design (§14: why more than five tables)
- [`docs/dw_design.md`](docs/dw_design.md) — star schema, SCD Type 2, surrogate keys
- [`docs/synthesizer_design.md`](docs/synthesizer_design.md) — how 1,470 rows become 100,000 realistic employees
- [`docs/etl_design.md`](docs/etl_design.md) — stored-procedure ETL, cleaning rules, SCD Type 2 algorithm, logging
- [`docs/project_journal.md`](docs/project_journal.md) — step-by-step record of how the project was built

## Dataset
[IBM HR Analytics Employee Attrition & Performance](https://www.kaggle.com/datasets/pavansubhasht/ibm-hr-analytics-attrition-dataset) — 1,470 rows, 35 columns.

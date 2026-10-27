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
├── sql/                  # DDL, DML, stored procedures, CTEs, window functions
├── scripts/              # setup_database.py — builds the schemas from sql/
├── analysis/             # dataset profiling that drove the schema design
├── diagrams/             # Editable draw.io ER and dimensional-model diagrams
├── data/raw/             # IBM HR Analytics source dataset
├── tests/                # pytest tests (schema constraints, ...)
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
python scripts/setup_database.py          # refuses to rebuild if business data is loaded
python scripts/setup_database.py --force  # deliberate rebuild (drops and recreates tables)
```
The scripts can also be run one by one in MySQL Workbench, in numeric order.

Verify that every integrity rule is enforced:
```bash
python -m pytest tests -q
```

## Design documentation
- [`docs/oltp_design.md`](docs/oltp_design.md) — data dictionary, profiling findings, normalized OLTP design
- [`docs/dw_design.md`](docs/dw_design.md) — star schema, SCD Type 2, surrogate keys
- [`docs/project_journal.md`](docs/project_journal.md) — step-by-step record of how the project was built

## Dataset
[IBM HR Analytics Employee Attrition & Performance](https://www.kaggle.com/datasets/pavansubhasht/ibm-hr-analytics-attrition-dataset) — 1,470 rows, 35 columns.

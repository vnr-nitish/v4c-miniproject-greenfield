"""ETL tests: prove that the stored-procedure ETL (sql/07, sql/08) moved the data correctly.

Reconciliation tests read the loaded data and compare each layer with the one before it:
  staging -> OLTP   every record loaded or explained, planted issues fixed (anomaly manifest),
                    the D19 rule applied, IBM columns reconstructable (lossless decomposition)
  OLTP -> warehouse every job version is one SCD Type 2 version, every review one fact row
                    linked to the employee version valid on its review date
They need a completed run:  python scripts/run_etl.py --migrate
Change-flow tests run the application's SCD Type 2 path inside a transaction that is rolled
back (see conftest.py), so the loaded data is never modified.
""" 
import json
from datetime import date, timedelta

import mysql.connector
import pytest

from src.synthesizer.config import SynthesizerConfig

SIGNAL = 1644                        # MySQL error number for SIGNAL SQLSTATE '45000'
HR, RND, SALES = 1, 2, 3             # sql/02_oltp_reference_data.sql
MANAGER = 4
CONFIG = SynthesizerConfig()


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()[0]


@pytest.fixture(scope="module", autouse=True)
def migration(connection):
    """Step counts of the latest successful staging -> OLTP migration (skips if the ETL never ran)."""
    cur = connection.cursor()
    cur.execute("SELECT (SELECT COUNT(*) FROM hr_oltp.employees), (SELECT COUNT(*) FROM hr_dw.fact_performance_reviews)")
    employees, facts = cur.fetchone()
    cur.execute("SELECT MAX(batch_id) FROM hr_dw.etl_batch_log "
                "WHERE log_id = batch_id AND step_name = 'migrate staging -> oltp' AND status IN ('SUCCESS', 'WARNING')")
    batch_id = cur.fetchone()[0]
    steps = {}
    if batch_id:
        cur.execute("SELECT step_name, rows_affected FROM hr_dw.etl_batch_log WHERE batch_id = %s", (batch_id,))
        steps = dict(cur.fetchall())
    cur.close()
    connection.rollback()
    if not (employees and facts and batch_id):
        pytest.skip("ETL has not been run: python scripts/run_etl.py --migrate")
    return {"batch_id": batch_id, "steps": steps}


# ----------------------------------------------------------------------------- staging -> OLTP

STAGED_RECORDS = [   # staging table, OLTP table, business key (what makes a staged row a duplicate)
    ("stg_employees", "employees", "TRIM(employee_code)"),
    ("stg_job_history", "employee_job_history", "TRIM(employee_code), effective_start_date"),
    ("stg_projects", "projects", "TRIM(project_code)"),
    ("stg_assignments", "project_assignments", "TRIM(employee_code), TRIM(project_code), start_date"),
    ("stg_surveys", "employee_surveys", "TRIM(employee_code), YEAR(survey_date)"),
    ("stg_reviews", "performance_reviews", "TRIM(employee_code), review_year"),
]


@pytest.mark.parametrize("staging, target, key", STAGED_RECORDS)
def test_every_staged_record_is_loaded_or_rejected(cur, migration, staging, target, key):
    unique = scalar(cur, f"SELECT COUNT(*) FROM (SELECT DISTINCT {key} FROM hr_staging.{staging}) k")
    rejected = scalar(cur, "SELECT COUNT(*) FROM hr_dw.etl_rejected_rows WHERE batch_id = %s AND source_table = %s",
                      (migration["batch_id"], f"hr_staging.{staging}"))
    assert migration["steps"][f"load {target}"] == unique - rejected
    assert rejected == 0                         # the synthesized data has no unloadable rows
    assert scalar(cur, f"SELECT COUNT(*) FROM hr_oltp.{target}") >= unique - rejected


def test_planted_issues_were_all_fixed(cur, migration):
    manifest_file = CONFIG.output_dir / "anomaly_manifest.json"
    if not manifest_file.exists():
        pytest.skip("anomaly manifest not found: run python -m src.synthesizer.run")
    manifest = json.loads(manifest_file.read_text())
    steps = migration["steps"]
    assert steps["fix: duplicate employee rows removed"] == manifest["duplicate_employee_rows"]
    assert steps["fix: gender codes standardized"] == manifest["gender_codes"]
    assert steps["fix: department / role labels standardized"] == sum(manifest["label_noise_job_history"].values())
    assert steps["fix: review resubmissions removed"] == manifest["duplicate_review_rows"]
    assert steps["check: end dates match LEAD(start)"] == 0


def test_resubmitted_reviews_keep_the_latest_submission(cur):
    cur.execute("""
        WITH submissions AS (
            SELECT TRIM(employee_code) AS code, review_year, kpi_score,
                   ROW_NUMBER() OVER (PARTITION BY TRIM(employee_code), review_year ORDER BY stg_row_id DESC) AS rn,
                   COUNT(*)     OVER (PARTITION BY TRIM(employee_code), review_year) AS copies
            FROM hr_staging.stg_reviews
        )
        SELECT COUNT(*), COALESCE(SUM(r.kpi_score = s.kpi_score), 0)
        FROM submissions s
        JOIN hr_oltp.employees e           ON e.employee_code = s.code
        JOIN hr_oltp.performance_reviews r ON r.employee_id = e.employee_id AND r.review_year = s.review_year
        WHERE s.rn = 1 AND s.copies > 1""")
    resubmitted, latest_kept = cur.fetchone()
    assert resubmitted > 0 and latest_kept == resubmitted


def test_prior_companies_follow_the_d19_rule(cur):
    # D19: no prior experience <=> no previous employer; with experience there was at least one
    cur.execute("SELECT COALESCE(SUM((prior_experience_years = 0) <> (prior_companies_count = 0)), 0) "
                "FROM hr_oltp.employees")
    assert cur.fetchone()[0] == 0


def test_job_history_is_contiguous_and_starts_at_hire(cur):
    assert scalar(cur, """
        SELECT COUNT(*) FROM (
            SELECT effective_end_date,
                   LEAD(effective_start_date) OVER (PARTITION BY employee_id ORDER BY effective_start_date) AS next_start
            FROM hr_oltp.employee_job_history) v
        WHERE NOT (effective_end_date <=> next_start)""") == 0          # no gaps, no overlaps, one open version
    assert scalar(cur, """
        SELECT COUNT(*) FROM hr_oltp.employees e
        LEFT JOIN (SELECT employee_id, MIN(effective_start_date) AS first_start
                   FROM hr_oltp.employee_job_history GROUP BY employee_id) h ON h.employee_id = e.employee_id
        WHERE NOT (h.first_start <=> e.hire_date)""") == 0


def test_snapshot_view_reconstructs_the_ibm_columns(cur):
    """Lossless decomposition: rebuild the IBM row from the normalized tables, as of the snapshot date."""
    cur.execute("""
        WITH staged AS (
            SELECT s.*, ROW_NUMBER() OVER (PARTITION BY TRIM(s.employee_code) ORDER BY s.stg_row_id DESC) AS rn
            FROM hr_staging.stg_employees s
        ),
        pairs AS (
            SELECT s.*, e.hire_date AS e_hire_date, e.date_of_birth AS e_date_of_birth,
                   e.prior_experience_years, COALESCE(e.exit_date, %(snapshot)s) AS ref,
                   v.attrition AS v_attrition, v.department AS v_department, v.job_role AS v_job_role,
                   v.job_level AS v_job_level, v.monthly_income AS v_income, v.stock_option_level AS v_stock,
                   v.business_travel AS v_travel, v.gender AS v_gender, v.marital_status AS v_marital,
                   v.distance_from_home AS v_distance, v.education AS v_education,
                   v.education_field AS v_field, v.percent_salary_hike AS v_hike,
                   v.environment_satisfaction AS v_env, v.job_satisfaction AS v_job_sat,
                   v.relationship_satisfaction AS v_rel, v.job_involvement AS v_inv, v.work_life_balance AS v_wlb,
                   v.performance_rating AS v_rating, v.over_time AS v_overtime,
                   v.training_times_last_year AS v_training,
                   v.role_since, v.manager_since, v.last_promotion_date
            FROM staged s
            JOIN hr_oltp.employees e             ON e.employee_code = TRIM(s.employee_code)
            JOIN hr_oltp.vw_employee_snapshot v  ON v.employee_id = e.employee_id
            WHERE s.rn = 1
        )
        SELECT COUNT(*)                                                                         AS employees,
          SUM(TIMESTAMPDIFF(YEAR, e_date_of_birth, ref) = age)                                   AS age,
          SUM(v_attrition = attrition)                                                           AS attrition,
          SUM(TIMESTAMPDIFF(YEAR, e_hire_date, ref) = years_at_company)                         AS years_at_company,
          SUM(prior_experience_years + TIMESTAMPDIFF(YEAR, e_hire_date, ref)
              = total_working_years)                                                             AS total_working_years,
          SUM(TIMESTAMPDIFF(YEAR, role_since, ref) = years_in_current_role)                     AS years_in_current_role,
          SUM(TIMESTAMPDIFF(YEAR, COALESCE(last_promotion_date, e_hire_date), ref)
              = years_since_last_promotion)                                                      AS years_since_last_promotion,
          SUM(TIMESTAMPDIFF(YEAR, manager_since, ref) = years_with_curr_manager)                AS years_with_curr_manager,
          SUM(UPPER(v_department) = CASE UPPER(TRIM(department)) WHEN 'R&D' THEN 'RESEARCH & DEVELOPMENT'
                                        WHEN 'HR' THEN 'HUMAN RESOURCES' WHEN 'SALES DEPT' THEN 'SALES'
                                        ELSE UPPER(TRIM(department)) END)                        AS department,
          SUM(UPPER(v_job_role) = UPPER(TRIM(job_role)))                                         AS job_role,
          SUM(v_job_level = job_level)                                                           AS job_level,
          SUM(v_income = monthly_income)                                                         AS monthly_income,
          SUM(v_stock = stock_option_level)                                                      AS stock_option_level,
          SUM(v_travel = business_travel)                                                        AS business_travel,
          SUM(LEFT(v_gender, 1) = LEFT(gender, 1))                                               AS gender,
          SUM(v_marital = marital_status)                                                        AS marital_status,
          SUM(v_distance = distance_from_home)                                                   AS distance_from_home,
          SUM(v_education = education AND v_field = education_field)                             AS education,
          SUM(v_env IS NOT NULL)                                                                 AS surveyed,
          SUM(v_env = environment_satisfaction AND v_job_sat = job_satisfaction
              AND v_rel = relationship_satisfaction AND v_inv = job_involvement
              AND v_wlb = work_life_balance)                                                     AS survey_matches,
          SUM(v_rating IS NOT NULL)                                                              AS reviewed,
          SUM(v_rating = performance_rating AND v_overtime = over_time
              AND v_training = training_times_last_year)                                         AS review_matches,
          SUM(v_hike IS NOT NULL)                                                                AS with_raise,
          SUM(ABS(v_hike - percent_salary_hike) <= 1)                                            AS raise_within_1_point
        FROM pairs""", {"snapshot": CONFIG.snapshot_date})
    r = dict(zip(cur.column_names, (int(v) for v in cur.fetchone())))
    n = r.pop("employees")
    assert n == CONFIG.n_employees
    exact = ["age", "attrition", "years_at_company", "total_working_years", "years_in_current_role",
             "years_since_last_promotion", "department", "job_role", "job_level", "monthly_income",
             "stock_option_level", "business_travel", "gender", "marital_status", "distance_from_home", "education"]
    assert {c: r[c] for c in exact if r[c] != n} == {}
    assert r["years_with_curr_manager"] / n >= 0.95      # synthesizer fidelity 98 % (early years without a manager)
    assert r["survey_matches"] == r["surveyed"] > 0       # the latest survey and review carry the IBM values
    assert r["review_matches"] == r["reviewed"] > 0
    assert r["raise_within_1_point"] / r["with_raise"] >= 0.90   # salaries clipped into level bands (synthesizer: 92 %)


# ----------------------------------------------------------------------------- OLTP -> warehouse

def test_every_job_version_is_one_warehouse_version(cur, migration):
    assert scalar(cur, "SELECT COUNT(*) FROM hr_dw.dim_employee") == \
        scalar(cur, "SELECT COUNT(*) FROM hr_oltp.employee_job_history")
    assert scalar(cur, """
        SELECT COUNT(*) FROM hr_oltp.employee_job_history h
        LEFT JOIN hr_dw.dim_employee d ON d.employee_id = h.employee_id AND d.start_date = h.effective_start_date
        WHERE d.employee_sk IS NULL""") == 0
    assert scalar(cur, "SELECT COUNT(*) FROM hr_dw.dim_employee WHERE is_current") == \
        scalar(cur, "SELECT COUNT(*) FROM hr_oltp.employees")


def test_scd2_periods_are_contiguous_and_numbered(cur, migration):
    cur.execute("""
        SELECT COALESCE(SUM(end_date <> COALESCE(next_start - INTERVAL 1 DAY, DATE '9999-12-31')), 0),
               COALESCE(SUM(version_number <> rn), 0)
        FROM (SELECT end_date, version_number,
                     LEAD(start_date) OVER w AS next_start,
                     ROW_NUMBER()     OVER w AS rn
              FROM hr_dw.dim_employee
              WINDOW w AS (PARTITION BY employee_id ORDER BY start_date)) v""")
    assert cur.fetchone() == (0, 0)          # inclusive end = next start - 1 day: no gaps, no overlaps


def test_warehouse_values_match_oltp(cur, migration):
    cur.execute("""
        SELECT COALESCE(SUM(NOT (d.department_code = dep.department_code AND d.job_role = r.role_name
                                 AND d.job_level = h.job_level_id AND d.monthly_income = h.monthly_income
                                 AND d.manager_employee_id <=> h.manager_id
                                 AND d.change_reason = h.change_reason)), 0)       AS type2_mismatches,
               COALESCE(SUM(NOT (d.gender = e.gender AND d.hire_date = e.hire_date
                                 AND d.exit_date <=> e.exit_date
                                 AND d.prior_companies_count = e.prior_companies_count
                                 AND d.attrition_flag = (e.exit_date IS NOT NULL))), 0) AS type1_mismatches
        FROM hr_dw.dim_employee d
        JOIN hr_oltp.employee_job_history h ON h.employee_id = d.employee_id AND h.effective_start_date = d.start_date
        JOIN hr_oltp.employees e            ON e.employee_id = d.employee_id
        JOIN hr_oltp.departments dep        ON dep.department_id = h.department_id
        JOIN hr_oltp.job_roles r            ON r.job_role_id = h.job_role_id""")
    assert cur.fetchone() == (0, 0)


def test_every_review_is_one_fact_row(cur, migration):
    assert scalar(cur, "SELECT COUNT(*) FROM hr_dw.fact_performance_reviews") == \
        scalar(cur, "SELECT COUNT(*) FROM hr_oltp.performance_reviews")
    assert scalar(cur, "SELECT COUNT(*) FROM hr_dw.etl_rejected_rows "
                       "WHERE source_table = 'hr_oltp.performance_reviews'") == 0


def test_fact_rows_point_to_the_version_valid_on_the_review_date(cur, migration):
    cur.execute("""
        SELECT COALESCE(SUM(de.employee_id <> r.employee_id
                            OR r.review_date NOT BETWEEN de.start_date AND de.end_date), 0) AS wrong_version,
               COALESCE(SUM(f.date_sk <> YEAR(r.review_date) * 10000 + MONTH(r.review_date) * 100
                                         + DAY(r.review_date)), 0)                        AS wrong_date,
               COALESCE(SUM(dd.department_code <> de.department_code), 0)                AS wrong_department,
               COALESCE(SUM(NOT (dp.project_id = COALESCE(r.project_id, 0))), 0)          AS wrong_project,
               COALESCE(SUM(f.monthly_income_at_review <> de.monthly_income), 0)          AS wrong_income,
               COALESCE(SUM(NOT (f.performance_rating = r.rating_id AND f.kpi_score = r.kpi_score
                                 AND f.overtime_flag = r.overtime_flag)), 0)              AS wrong_measures,
               COALESCE(SUM((f.job_satisfaction IS NULL) <> (s.survey_id IS NULL)), 0)    AS wrong_survey
        FROM hr_dw.fact_performance_reviews f
        JOIN hr_oltp.performance_reviews r ON r.review_id = f.review_id
        JOIN hr_dw.dim_employee de         ON de.employee_sk = f.employee_sk
        JOIN hr_dw.dim_department dd       ON dd.department_sk = f.department_sk
        JOIN hr_dw.dim_project dp          ON dp.project_sk = f.project_sk
        LEFT JOIN hr_oltp.employee_surveys s ON s.employee_id = r.employee_id AND s.survey_year = r.review_year""")
    assert dict(zip(cur.column_names, cur.fetchone())) == {
        "wrong_version": 0, "wrong_date": 0, "wrong_department": 0, "wrong_project": 0,
        "wrong_income": 0, "wrong_measures": 0, "wrong_survey": 0}
    # reviews without a project use the special member, never a NULL key
    assert scalar(cur, "SELECT COUNT(*) FROM hr_dw.fact_performance_reviews WHERE project_sk = -1") == \
        scalar(cur, "SELECT COUNT(*) FROM hr_oltp.performance_reviews WHERE project_id IS NULL") > 0


def test_dim_date_has_the_indian_fiscal_year(cur, migration):
    cur.execute("SELECT full_date, fiscal_year, fiscal_quarter, day_of_week, week_of_year FROM hr_dw.dim_date "
                "WHERE date_sk IN (20240331, 20240401, 20241231) ORDER BY date_sk")
    assert cur.fetchall() == [(date(2024, 3, 31), "FY2023-24", "Q4", 7, 13),
                              (date(2024, 4, 1), "FY2024-25", "Q1", 1, 14),
                              (date(2024, 12, 31), "FY2024-25", "Q3", 2, 1)]
    assert scalar(cur, "SELECT COUNT(*) FROM hr_dw.dim_date") == 5844        # 2015-01-01 .. 2030-12-31


def test_rerunning_the_warehouse_load_changes_nothing(cur, migration):
    """Idempotency: with no OLTP change since the last run, every step reports 0 rows."""
    cur.execute("CALL hr_dw.sp_etl_start_batch('test: idempotency', @b)")
    for step in ("sp_load_dim_department(@b)", "sp_load_dim_project(@b)",
                 "sp_load_dim_employee_scd2(@b, NULL)", "sp_load_fact_performance_reviews(@b)"):
        cur.execute(f"CALL hr_dw.{step}")
    cur.execute("SELECT step_name, rows_affected FROM hr_dw.etl_batch_log WHERE batch_id = @b AND log_id <> @b")
    assert dict(cur.fetchall()) == {"load dim_department": 0, "load dim_project": 0,
                                    "load dim_employee (SCD2)": 0, "load fact_performance_reviews": 0}


# ----------------------------------------------------------------------------- application change flow

def current_position(cur, where, params=()):
    cur.execute("SELECT employee_id, department_id, job_role_id, job_level, manager_id, monthly_income, "
                "stock_option_level, business_travel FROM hr_oltp.vw_employee_current "
                f"WHERE is_active AND {where} ORDER BY employee_id LIMIT 1", params)
    return dict(zip(cur.column_names, cur.fetchone()))


def change_position(cur, emp, effective_date=None, change_reason=None, **new):
    p = {**emp, **new}
    cur.execute("CALL hr_oltp.sp_change_employee_position(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "COALESCE(%s, CURDATE()), @history_id)",
                (emp["employee_id"], p["department_id"], p["job_role_id"], p["job_level"], p["manager_id"],
                 p["monthly_income"], p["stock_option_level"], p["business_travel"], change_reason, effective_date))


def test_department_change_creates_a_new_warehouse_version(cur, migration):
    """Phase 4 requirement: changing an employee's department triggers an SCD Type 2 update."""
    emp = current_position(cur, "department_code = 'RND' AND job_role = 'Manager'")
    sales_head = current_position(cur, "department_code = 'SAL' AND job_level = 5")["employee_id"]
    cur.execute("SELECT employee_sk, version_number FROM hr_dw.dim_employee WHERE employee_id = %s AND is_current",
                (emp["employee_id"],))
    old_sk, old_version = cur.fetchone()
    today = scalar(cur, "SELECT CURDATE()")

    change_position(cur, emp, department_id=SALES, job_role_id=MANAGER, manager_id=sales_head)
    cur.execute("CALL hr_dw.sp_scd2_apply_employee(%s, @batch_id)", (emp["employee_id"],))

    cur.execute("SELECT change_reason, effective_start_date FROM hr_oltp.employee_job_history "
                "WHERE history_id = @history_id")
    assert cur.fetchone() == ("Transfer", today)                       # reason derived: department changed
    cur.execute("SELECT end_date, is_current FROM hr_dw.dim_employee WHERE employee_sk = %s", (old_sk,))
    assert cur.fetchone() == (today - timedelta(days=1), 0)           # old version expired yesterday
    cur.execute("SELECT department_name, job_role, manager_employee_id, start_date, end_date, version_number "
                "FROM hr_dw.dim_employee WHERE employee_id = %s AND is_current", (emp["employee_id"],))
    assert cur.fetchone() == ("Sales", "Manager", sales_head, today, date(9999, 12, 31), old_version + 1)
    cur.execute("SELECT message FROM hr_dw.etl_batch_log WHERE batch_id = @batch_id AND log_id <> @batch_id")
    assert "new versions 1, expired 1" in cur.fetchone()[0]


def test_personal_change_overwrites_every_version(cur, migration):
    """Type 1: a correction to the person is applied to all versions, without creating a new one."""
    emp_id = scalar(cur, "SELECT employee_id FROM hr_dw.dim_employee WHERE marital_status <> 'Divorced' "
                         "GROUP BY employee_id HAVING COUNT(*) >= 3 ORDER BY employee_id LIMIT 1")
    versions = scalar(cur, "SELECT COUNT(*) FROM hr_dw.dim_employee WHERE employee_id = %s", (emp_id,))
    cur.execute("UPDATE hr_oltp.employees SET marital_status = 'Divorced' WHERE employee_id = %s", (emp_id,))
    cur.execute("CALL hr_dw.sp_scd2_apply_employee(%s, @batch_id)", (emp_id,))
    cur.execute("SELECT COUNT(*), SUM(marital_status = 'Divorced') FROM hr_dw.dim_employee WHERE employee_id = %s",
                (emp_id,))
    assert cur.fetchone() == (versions, versions)


@pytest.mark.parametrize("change, message", [
    (dict(department_id=HR), "does not exist in the selected department"),       # Laboratory Technician not in HR
    (dict(job_level=4), "outside the allowed range"),                           # role range is 1-3
    (dict(monthly_income=50000), "outside the salary band"),
    (dict(effective_date=date.today() + timedelta(days=30)), "not in the future"),
    (dict(effective_date=date(2000, 1, 1)), "must be after"),
    (dict(manager_id="self"), "another active employee"),
    (dict(), "Nothing changed"),
])
def test_invalid_position_changes_are_refused(cur, migration, change, message):
    emp = current_position(cur, "department_code = 'RND' AND job_role = 'Laboratory Technician' AND job_level = 1")
    if change.get("manager_id") == "self":
        change["manager_id"] = emp["employee_id"]
    with pytest.raises(mysql.connector.Error) as exc:
        change_position(cur, emp, **change)
    assert exc.value.errno == SIGNAL and message in exc.value.msg


def test_leavers_cannot_change_jobs(cur, migration):
    cur.execute("SELECT employee_id, department_id, job_role_id, job_level, manager_id, monthly_income, "
                "stock_option_level, business_travel FROM hr_oltp.vw_employee_current "
                "WHERE NOT is_active ORDER BY employee_id LIMIT 1")
    leaver = dict(zip(cur.column_names, cur.fetchone()))
    with pytest.raises(mysql.connector.Error) as exc:
        change_position(cur, leaver, monthly_income=leaver["monthly_income"] + 1)
    assert exc.value.errno == SIGNAL and "can no longer change jobs" in exc.value.msg

"""Schema tests: prove that the integrity rules of the design are enforced by MySQL itself.

Structure tests check that the schema was built as designed. Positive tests check that valid
data is accepted. Negative tests deliberately insert invalid data and assert that MySQL rejects
it with the expected error code:
    1062 duplicate key · 1451 parent row still referenced · 1452 missing parent row
    3819 CHECK constraint violated
Every test runs inside a transaction that is rolled back (see conftest.py).
"""
import itertools
from datetime import date

import mysql.connector
import pytest

DUPLICATE_KEY, PARENT_REFERENCED, NO_PARENT, CHECK_VIOLATED = 1062, 1451, 1452, 3819

# Reference ids (sql/02_oltp_reference_data.sql)
HR, RND, SALES = 1, 2, 3
LAB_TECHNICIAN, MANAGER, RESEARCH_SCIENTIST, SALES_EXECUTIVE = 3, 4, 7, 8

_seq = itertools.count(1)


# ----------------------------------------------------------------------------- helpers

def expect_error(cur, sql, params, errno):
    with pytest.raises(mysql.connector.Error) as exc:
        cur.execute(sql, params)
    assert exc.value.errno == errno, f"expected MySQL error {errno}, got {exc.value.errno}: {exc.value.msg}"
    return exc.value


EMPLOYEE_SQL = """
INSERT INTO hr_oltp.employees
    (employee_code, first_name, last_name, email, phone, gender, date_of_birth, marital_status,
     education_level_id, education_field_id, distance_from_home_km, prior_experience_years,
     prior_companies_count, hire_date, exit_date, exit_reason_id)
VALUES
    (%(employee_code)s, %(first_name)s, %(last_name)s, %(email)s, %(phone)s, %(gender)s,
     %(date_of_birth)s, %(marital_status)s, %(education_level_id)s, %(education_field_id)s,
     %(distance_from_home_km)s, %(prior_experience_years)s, %(prior_companies_count)s,
     %(hire_date)s, %(exit_date)s, %(exit_reason_id)s)
"""

JOB_SQL = """
INSERT INTO hr_oltp.employee_job_history
    (employee_id, department_id, job_role_id, job_level_id, manager_id, monthly_income,
     stock_option_level, business_travel, change_reason, effective_start_date, effective_end_date)
VALUES
    (%(employee_id)s, %(department_id)s, %(job_role_id)s, %(job_level_id)s, %(manager_id)s,
     %(monthly_income)s, %(stock_option_level)s, %(business_travel)s, %(change_reason)s,
     %(effective_start_date)s, %(effective_end_date)s)
"""

SURVEY_SQL = """
INSERT INTO hr_oltp.employee_surveys
    (employee_id, survey_date, environment_satisfaction, job_satisfaction,
     relationship_satisfaction, job_involvement, work_life_balance)
VALUES (%(employee_id)s, %(survey_date)s, %(environment_satisfaction)s, %(job_satisfaction)s,
        %(relationship_satisfaction)s, %(job_involvement)s, %(work_life_balance)s)
"""

REVIEW_SQL = """
INSERT INTO hr_oltp.performance_reviews
    (employee_id, review_year, review_date, reviewer_id, project_id, rating_id, kpi_score,
     training_sessions, overtime_flag, comments)
VALUES (%(employee_id)s, %(review_year)s, %(review_date)s, %(reviewer_id)s, %(project_id)s,
        %(rating_id)s, %(kpi_score)s, %(training_sessions)s, %(overtime_flag)s, %(comments)s)
"""

PROJECT_SQL = """
INSERT INTO hr_oltp.projects
    (project_code, project_name, department_id, project_manager_id, start_date, planned_end_date,
     actual_end_date, budget, status, priority)
VALUES (%(project_code)s, %(project_name)s, %(department_id)s, %(project_manager_id)s, %(start_date)s,
        %(planned_end_date)s, %(actual_end_date)s, %(budget)s, %(status)s, %(priority)s)
"""

ASSIGNMENT_SQL = """
INSERT INTO hr_oltp.project_assignments
    (employee_id, project_id, project_role, allocation_pct, start_date, end_date)
VALUES (%(employee_id)s, %(project_id)s, %(project_role)s, %(allocation_pct)s, %(start_date)s, %(end_date)s)
"""


def employee_row(**overrides):
    n = next(_seq)
    row = dict(employee_code=f"EMP9{n:05d}", first_name="Test", last_name=f"User{n}",
               email=f"test.user{n}@example.com", phone="+91 90000 00000", gender="Female",
               date_of_birth=date(1990, 6, 1), marital_status="Single", education_level_id=3,
               education_field_id=2, distance_from_home_km=5, prior_experience_years=2,
               prior_companies_count=1, hire_date=date(2015, 1, 1), exit_date=None, exit_reason_id=None)
    row.update(overrides)
    return row


def add_employee(cur, **overrides):
    cur.execute(EMPLOYEE_SQL, employee_row(**overrides))
    return cur.lastrowid


def job_row(employee_id, **overrides):
    row = dict(employee_id=employee_id, department_id=RND, job_role_id=LAB_TECHNICIAN, job_level_id=1,
               manager_id=None, monthly_income=3000, stock_option_level=0, business_travel="Travel_Rarely",
               change_reason="Hire", effective_start_date=date(2015, 1, 1), effective_end_date=None)
    row.update(overrides)
    return row


def add_job(cur, employee_id, **overrides):
    cur.execute(JOB_SQL, job_row(employee_id, **overrides))
    return cur.lastrowid


def survey_row(employee_id, **overrides):
    row = dict(employee_id=employee_id, survey_date=date(2024, 6, 15), environment_satisfaction=3,
               job_satisfaction=4, relationship_satisfaction=2, job_involvement=3, work_life_balance=3)
    row.update(overrides)
    return row


def review_row(employee_id, reviewer_id, **overrides):
    row = dict(employee_id=employee_id, review_year=2024, review_date=date(2025, 1, 20),
               reviewer_id=reviewer_id, project_id=None, rating_id=3, kpi_score=82.5,
               training_sessions=3, overtime_flag=False, comments=None)
    row.update(overrides)
    return row


def project_row(**overrides):
    n = next(_seq)
    row = dict(project_code=f"PRJ9{n:04d}", project_name=f"Test project {n}", department_id=RND,
               project_manager_id=None, start_date=date(2024, 1, 1), planned_end_date=date(2024, 12, 31),
               actual_end_date=None, budget=250000, status="Active", priority="High")
    row.update(overrides)
    return row


def full_years(start, end):
    """Completed years between two dates — same rule as MySQL TIMESTAMPDIFF(YEAR, ...)."""
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))


# ----------------------------------------------------------------------------- structure

def test_oltp_has_14_tables(cur):
    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'hr_oltp' AND table_type = 'BASE TABLE'")
    assert cur.fetchone()[0] == 14


def test_dw_has_7_tables(cur):
    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'hr_dw' AND table_type = 'BASE TABLE'")
    assert cur.fetchone()[0] == 7


@pytest.mark.parametrize("table, expected", [
    ("departments", 3), ("job_levels", 5), ("job_roles", 9), ("department_job_roles", 11),
    ("education_levels", 5), ("education_fields", 6), ("performance_rating_levels", 4), ("exit_reasons", 9),
])
def test_reference_data_row_counts(cur, table, expected):
    cur.execute(f"SELECT COUNT(*) FROM hr_oltp.{table}")
    assert cur.fetchone()[0] == expected


def test_every_foreign_key_is_restrict(cur):
    cur.execute("SELECT constraint_schema, constraint_name, delete_rule, update_rule "
                "FROM information_schema.referential_constraints "
                "WHERE constraint_schema IN ('hr_oltp', 'hr_dw')")
    rows = cur.fetchall()
    assert len(rows) == 24          # 20 in hr_oltp + 4 in hr_dw
    assert all(r[2] == "RESTRICT" and r[3] == "RESTRICT" for r in rows), rows


def test_project_special_member_exists(cur):
    cur.execute("SELECT project_sk, project_id, project_name FROM hr_dw.dim_project WHERE project_sk = -1")
    assert cur.fetchone() == (-1, 0, "No Project Assigned")


# ----------------------------------------------------------------------------- positive

def test_valid_employee_lifecycle_is_accepted(cur):
    manager = add_employee(cur, hire_date=date(2010, 3, 1), date_of_birth=date(1980, 1, 1))
    add_job(cur, manager, department_id=RND, job_role_id=MANAGER, job_level_id=4,
            monthly_income=15000, effective_start_date=date(2010, 3, 1))
    emp = add_employee(cur)
    add_job(cur, emp, effective_start_date=date(2015, 1, 1), effective_end_date=date(2018, 1, 1), manager_id=manager)
    add_job(cur, emp, job_level_id=2, monthly_income=3600, change_reason="Promotion",
            effective_start_date=date(2018, 1, 1), effective_end_date=date(2021, 7, 1), manager_id=manager)
    add_job(cur, emp, job_role_id=RESEARCH_SCIENTIST, job_level_id=2, monthly_income=4000,
            change_reason="Role Change", effective_start_date=date(2021, 7, 1), manager_id=manager)
    cur.execute(SURVEY_SQL, survey_row(emp))
    cur.execute(PROJECT_SQL, project_row(project_manager_id=manager))
    project = cur.lastrowid
    cur.execute(ASSIGNMENT_SQL, dict(employee_id=emp, project_id=project, project_role="Team Member",
                                     allocation_pct=60, start_date=date(2024, 1, 15), end_date=None))
    cur.execute(REVIEW_SQL, review_row(emp, manager, project_id=project))

    cur.execute("SELECT job_role, job_level_title, manager_id, is_active "
                "FROM hr_oltp.vw_employee_current WHERE employee_id = %s", (emp,))
    assert cur.fetchone() == ("Research Scientist", "Intermediate", manager, 1)

    cur.execute("SELECT survey_year FROM hr_oltp.employee_surveys WHERE employee_id = %s", (emp,))
    assert cur.fetchone()[0] == 2024       # generated from survey_date


def test_snapshot_view_rebuilds_ibm_style_columns(cur):
    mgr_a = add_employee(cur, hire_date=date(2010, 1, 1), date_of_birth=date(1975, 1, 1))
    add_job(cur, mgr_a, job_role_id=MANAGER, job_level_id=4, monthly_income=15000,
            effective_start_date=date(2010, 1, 1))
    mgr_b = add_employee(cur, hire_date=date(2010, 1, 1), date_of_birth=date(1976, 1, 1))
    add_job(cur, mgr_b, job_role_id=MANAGER, job_level_id=4, monthly_income=15500,
            effective_start_date=date(2010, 1, 1))

    emp = add_employee(cur, date_of_birth=date(1990, 6, 1), hire_date=date(2015, 1, 1), prior_experience_years=3)
    add_job(cur, emp, manager_id=mgr_a, monthly_income=3000,
            effective_start_date=date(2015, 1, 1), effective_end_date=date(2018, 1, 1))
    add_job(cur, emp, manager_id=mgr_a, job_level_id=2, monthly_income=3600, change_reason="Promotion",
            effective_start_date=date(2018, 1, 1), effective_end_date=date(2021, 7, 1))
    add_job(cur, emp, manager_id=mgr_b, job_role_id=RESEARCH_SCIENTIST, job_level_id=2, monthly_income=4000,
            change_reason="Role Change", effective_start_date=date(2021, 7, 1))
    cur.execute(REVIEW_SQL, review_row(emp, mgr_b, rating_id=4, overtime_flag=True, training_sessions=2))

    cur.execute("SELECT CURDATE()")
    today = cur.fetchone()[0]
    cur.execute("""SELECT age, attrition, job_role, years_at_company, years_in_current_role,
                          years_since_last_promotion, years_with_curr_manager, percent_salary_hike,
                          total_working_years, performance_rating, over_time, training_times_last_year
                   FROM hr_oltp.vw_employee_snapshot WHERE employee_id = %s""", (emp,))
    assert cur.fetchone() == (
        full_years(date(1990, 6, 1), today), "No", "Research Scientist",
        full_years(date(2015, 1, 1), today),        # years at company
        full_years(date(2021, 7, 1), today),        # years in current role (role changed 2021-07-01)
        full_years(date(2018, 1, 1), today),        # years since last promotion
        full_years(date(2021, 7, 1), today),        # years with current manager (changed with the role)
        11,                                         # 4000 / 3600 - 1 = 11.1 %
        3 + full_years(date(2015, 1, 1), today),    # prior experience + tenure
        4, "Yes", 2,
    )
    cur.execute("SELECT role_since, manager_since, last_promotion_date "
                "FROM hr_oltp.vw_employee_snapshot WHERE employee_id = %s", (emp,))
    assert cur.fetchone() == (date(2021, 7, 1), date(2021, 7, 1), date(2018, 1, 1))


# ----------------------------------------------------------------------------- negative: employees

def test_rejects_hire_before_age_18(cur):
    expect_error(cur, EMPLOYEE_SQL, employee_row(date_of_birth=date(2000, 6, 1), hire_date=date(2017, 6, 1)),
                 CHECK_VIOLATED)


def test_rejects_exit_before_hire(cur):
    expect_error(cur, EMPLOYEE_SQL, employee_row(exit_date=date(2014, 12, 31), exit_reason_id=1), CHECK_VIOLATED)


def test_rejects_exit_date_without_reason(cur):
    expect_error(cur, EMPLOYEE_SQL, employee_row(exit_date=date(2020, 5, 1), exit_reason_id=None), CHECK_VIOLATED)


def test_rejects_exit_reason_without_exit_date(cur):
    expect_error(cur, EMPLOYEE_SQL, employee_row(exit_date=None, exit_reason_id=1), CHECK_VIOLATED)


def test_rejects_unknown_gender(cur):
    expect_error(cur, EMPLOYEE_SQL, employee_row(gender="X"), CHECK_VIOLATED)


def test_rejects_malformed_employee_code(cur):
    expect_error(cur, EMPLOYEE_SQL, employee_row(employee_code="E12345"), CHECK_VIOLATED)


def test_rejects_duplicate_email(cur):
    add_employee(cur, email="same@example.com")
    expect_error(cur, EMPLOYEE_SQL, employee_row(email="same@example.com"), DUPLICATE_KEY)


# ----------------------------------------------------------------------------- negative: job history

def test_rejects_invalid_department_role_pair(cur):
    emp = add_employee(cur)
    err = expect_error(cur, JOB_SQL, job_row(emp, department_id=RND, job_role_id=SALES_EXECUTIVE), NO_PARENT)
    assert "fk_ejh_department_role" in err.msg


def test_rejects_second_current_version(cur):
    emp = add_employee(cur)
    add_job(cur, emp, effective_start_date=date(2015, 1, 1))
    err = expect_error(cur, JOB_SQL, job_row(emp, effective_start_date=date(2019, 1, 1)), DUPLICATE_KEY)
    assert "uq_ejh_current_version" in err.msg


def test_accepts_closed_versions_plus_one_current(cur):
    emp = add_employee(cur)
    add_job(cur, emp, effective_start_date=date(2015, 1, 1), effective_end_date=date(2017, 1, 1))
    add_job(cur, emp, effective_start_date=date(2017, 1, 1), effective_end_date=date(2019, 1, 1),
            change_reason="Salary Revision", monthly_income=3300)
    add_job(cur, emp, effective_start_date=date(2019, 1, 1), change_reason="Salary Revision", monthly_income=3600)
    cur.execute("SELECT COUNT(*), SUM(effective_end_date IS NULL) FROM hr_oltp.employee_job_history "
                "WHERE employee_id = %s", (emp,))
    assert cur.fetchone() == (3, 1)


def test_rejects_version_ending_before_it_starts(cur):
    emp = add_employee(cur)
    expect_error(cur, JOB_SQL, job_row(emp, effective_start_date=date(2018, 1, 1),
                                       effective_end_date=date(2017, 1, 1)), CHECK_VIOLATED)


def test_rejects_employee_as_own_manager(cur):
    emp = add_employee(cur)
    expect_error(cur, JOB_SQL, job_row(emp, manager_id=emp), CHECK_VIOLATED)


def test_rejects_unknown_change_reason(cur):
    emp = add_employee(cur)
    expect_error(cur, JOB_SQL, job_row(emp, change_reason="Reorg"), CHECK_VIOLATED)


# ----------------------------------------------------------------------------- negative: surveys, reviews, projects

@pytest.mark.parametrize("column", ["environment_satisfaction", "job_satisfaction", "relationship_satisfaction",
                                    "job_involvement", "work_life_balance"])
def test_rejects_survey_score_outside_1_to_4(cur, column):
    emp = add_employee(cur)
    expect_error(cur, SURVEY_SQL, survey_row(emp, **{column: 5}), CHECK_VIOLATED)


def test_rejects_second_survey_in_same_year(cur):
    emp = add_employee(cur)
    cur.execute(SURVEY_SQL, survey_row(emp, survey_date=date(2024, 3, 1)))
    expect_error(cur, SURVEY_SQL, survey_row(emp, survey_date=date(2024, 9, 1)), DUPLICATE_KEY)


def test_rejects_self_review(cur):
    emp = add_employee(cur)
    expect_error(cur, REVIEW_SQL, review_row(emp, emp), CHECK_VIOLATED)


def test_rejects_kpi_score_above_100(cur):
    emp, reviewer = add_employee(cur), add_employee(cur)
    expect_error(cur, REVIEW_SQL, review_row(emp, reviewer, kpi_score=101), CHECK_VIOLATED)


def test_rejects_second_review_in_same_year(cur):
    emp, reviewer = add_employee(cur), add_employee(cur)
    cur.execute(REVIEW_SQL, review_row(emp, reviewer))
    expect_error(cur, REVIEW_SQL, review_row(emp, reviewer, review_date=date(2025, 2, 1)), DUPLICATE_KEY)


def test_rejects_review_held_outside_its_cycle(cur):
    emp, reviewer = add_employee(cur), add_employee(cur)
    expect_error(cur, REVIEW_SQL, review_row(emp, reviewer, review_year=2022, review_date=date(2025, 1, 10)),
                 CHECK_VIOLATED)


@pytest.mark.parametrize("allocation", [0, 101])
def test_rejects_allocation_outside_1_to_100(cur, allocation):
    emp = add_employee(cur)
    cur.execute(PROJECT_SQL, project_row())
    project = cur.lastrowid
    expect_error(cur, ASSIGNMENT_SQL, dict(employee_id=emp, project_id=project, project_role="Analyst",
                                           allocation_pct=allocation, start_date=date(2024, 2, 1), end_date=None),
                 CHECK_VIOLATED)


def test_rejects_completed_project_without_actual_end_date(cur):
    expect_error(cur, PROJECT_SQL, project_row(status="Completed", actual_end_date=None), CHECK_VIOLATED)


def test_rejects_deleting_department_in_use(cur):
    emp = add_employee(cur)
    add_job(cur, emp, department_id=RND)
    expect_error(cur, "DELETE FROM hr_oltp.departments WHERE department_id = %s", (RND,), PARENT_REFERENCED)


# ----------------------------------------------------------------------------- negative: warehouse

DIM_EMPLOYEE_SQL = """
INSERT INTO hr_dw.dim_employee
    (employee_id, employee_code, full_name, gender, date_of_birth, marital_status, education_level,
     education_field, distance_from_home_km, prior_experience_years, prior_companies_count, hire_date,
     exit_date, exit_reason, is_voluntary_exit, attrition_flag, department_code, department_name, job_role,
     job_level, job_level_title, manager_employee_id, manager_name, monthly_income, salary_band,
     stock_option_level, business_travel, change_reason, start_date, end_date, is_current,
     version_number, row_hash, etl_batch_id)
VALUES
    (%(employee_id)s, 'EMP900001', 'Test User', 'Female', '1990-06-01', 'Single', 'Bachelor', 'Life Sciences',
     5, 2, 1, '2015-01-01', NULL, NULL, NULL, FALSE, 'RND', 'Research & Development', 'Laboratory Technician',
     1, 'Entry', NULL, NULL, 3000, '3K-6K', 0, 'Travel_Rarely', 'Hire', %(start_date)s, %(end_date)s,
     %(is_current)s, %(version_number)s, REPEAT('a', 32), 0)
"""


def test_dw_rejects_two_current_versions(cur):
    cur.execute(DIM_EMPLOYEE_SQL, dict(employee_id=900001, start_date="2015-01-01", end_date="9999-12-31",
                                       is_current=True, version_number=1))
    err = expect_error(cur, DIM_EMPLOYEE_SQL, dict(employee_id=900001, start_date="2018-01-01",
                                                   end_date="9999-12-31", is_current=True, version_number=2),
                       DUPLICATE_KEY)
    assert "uq_dim_employee_current" in err.msg


def test_dw_rejects_current_flag_inconsistent_with_end_date(cur):
    expect_error(cur, DIM_EMPLOYEE_SQL, dict(employee_id=900002, start_date="2015-01-01", end_date="2017-12-31",
                                             is_current=True, version_number=1), CHECK_VIOLATED)


def test_dw_rejects_second_special_project_member(cur):
    expect_error(cur, "INSERT INTO hr_dw.dim_project (project_id, project_code, project_name, "
                      "owning_department_name, status, priority, budget_band) "
                      "VALUES (0, 'N/A', 'Another', 'N/A', 'N/A', 'N/A', 'N/A')", None, DUPLICATE_KEY)


def test_dw_rejects_fact_with_unknown_employee(cur):
    # works on an empty and on a loaded warehouse: the date and department rows may already exist
    cur.execute("INSERT IGNORE INTO hr_dw.dim_date VALUES (20250120, '2025-01-20', 20, 'Monday', 1, FALSE, 4, 1, "
                "'January', 1, 'Q1', 2025, '2025-01', 'FY2024-25', 'Q4')")
    cur.execute("INSERT IGNORE INTO hr_dw.dim_department (department_id, department_code, department_name) "
                "VALUES (2, 'RND', 'Research & Development')")
    cur.execute("SELECT department_sk FROM hr_dw.dim_department WHERE department_id = 2")
    department_sk = cur.fetchone()[0]
    err = expect_error(cur, "INSERT INTO hr_dw.fact_performance_reviews (review_id, date_sk, employee_sk, "
                            "department_sk, project_sk, performance_rating, kpi_score, training_sessions, "
                            "overtime_flag, monthly_income_at_review, etl_batch_id) "
                            "VALUES (4000000000, 20250120, -999, %s, -1, 3, 80, 2, 0, 3000, 0)",
                       (department_sk,), NO_PARENT)
    assert "fk_fact_employee" in err.msg

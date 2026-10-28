"""Data-access manager tests against the loaded database (docs/backend_design.md, decision B6).

Write tests run inside DatabaseConnection.rollback_session(): every manager shares one
connection whose work is rolled back at the end, so the real data is used and never changed
(test_rollback_session_leaves_no_trace proves it). The analytics tests only read, outside a
session, so they also exercise the cache.
They need a completed ETL run:  python scripts/run_etl.py --migrate
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from src.dal import (AnalyticsFilter, AnalyticsManager, EmployeeManager, EtlManager, ProjectManager,
                     ReferenceDataManager, ReviewManager)
from src.dal.analytics_manager import MAX_RISK_SCORE, RISK_BANDS, RISK_FACTORS
from src.db_manager import DatabaseConnection
from src.exceptions import (BusinessRuleError, DuplicateError, HRAppError, NotFoundError, OverAllocationError,
                            ValidationError)
from src.models import Assignment, Employee, Position, Project, Review
from src.models.review import RATING_LABELS

HR, RND, SALES = 1, 2, 3             # sql/02_oltp_reference_data.sql
MANAGERS = (EmployeeManager, ProjectManager, ReviewManager, AnalyticsManager, ReferenceDataManager, EtlManager)


def one(sql, params=None):
    return DatabaseConnection().fetch_one(sql, params)


def value(sql, params=None):
    return next(iter(one(sql, params).values()))


@pytest.fixture(scope="module", autouse=True)
def database():
    try:
        loaded = value("SELECT (SELECT COUNT(*) FROM hr_oltp.employees) "
                       "     * (SELECT COUNT(*) FROM hr_dw.fact_performance_reviews) AS loaded")
    except HRAppError as err:
        pytest.skip(f"MySQL not available: {err}")
    if not loaded:
        pytest.skip("ETL has not been run: python scripts/run_etl.py --migrate")


@pytest.fixture
def session():
    """Everything the test does through the managers is rolled back afterwards."""
    with DatabaseConnection.rollback_session():
        yield


@pytest.fixture(scope="module")
def picks():
    """Reference values and existing people the tests build on (read once)."""
    ref = ReferenceDataManager()
    today = value("SELECT CURDATE() AS today")

    def role(department_id, name):
        roles = ref.roles_for(department_id)
        return int(roles[roles.role_name == name].job_role_id.iloc[0])

    def mid_band(level):
        band = ref.job_levels().set_index("job_level_id").loc[level]
        return Decimal(str((band.salary_band_min + band.salary_band_max) / 2)).quantize(Decimal("1"))

    def senior(department_id):
        return value("SELECT employee_id FROM hr_oltp.vw_employee_current WHERE is_active AND department_id = %s "
                     "AND job_level >= 3 AND hire_date < '2020-01-01' ORDER BY employee_id LIMIT 1", (department_id,))

    return SimpleNamespace(
        today=today, hire_date=date(today.year - 2, 6, 1),
        lab_technician=role(RND, "Laboratory Technician"), sales_rep=role(SALES, "Sales Representative"),
        level1_income=mid_band(1), rnd_manager=senior(RND), sales_manager=senior(SALES),
        education_field_id=int(ref.education_fields().education_field_id.iloc[0]),
        exit_reason_id=int(ref.exit_reasons().exit_reason_id.iloc[0]))


def new_employee(picks, **changes) -> Employee:
    values = dict(first_name="Asha", last_name="Verma", email=f"asha.{uuid4().hex[:12]}@example.com",
                  phone="+91 98765 43210", gender="Female", date_of_birth=date(1995, 5, 5), marital_status="Single",
                  education_level_id=3, education_field_id=picks.education_field_id, distance_from_home_km=8,
                  prior_experience_years=2, prior_companies_count=1, hire_date=picks.hire_date)
    values.update(changes)
    return Employee(**values)


def lab_position(picks, **changes) -> Position:
    return Position(**{**dict(department_id=RND, job_role_id=picks.lab_technician, job_level_id=1,
                              monthly_income=picks.level1_income, manager_id=picks.rnd_manager), **changes})


def hire(picks, **changes) -> Employee:
    return EmployeeManager().onboard(new_employee(picks, **changes), lab_position(picks))


def new_project(picks, **changes) -> Project:
    values = dict(project_name="Test Analytics Platform", department_id=RND, start_date=picks.today - timedelta(30),
                  planned_end_date=picks.today + timedelta(90), budget=Decimal("750000"), status="Active",
                  priority="High", project_manager_id=picks.rnd_manager)
    values.update(changes)
    return ProjectManager().create(Project(**values))


def assignment(employee, project, pct=60, start=None, role="Developer") -> Assignment:
    return Assignment(employee_id=employee.employee_id, project_id=project.project_id, project_role=role,
                      allocation_pct=pct, start_date=start or project.start_date)


# ----------------------------------------------------------------------------- architecture

def test_every_manager_is_a_singleton_on_one_shared_pool():
    for manager in MANAGERS:
        assert issubclass(manager, DatabaseConnection)
        assert manager() is manager()
    assert EmployeeManager() is not ProjectManager()
    assert len({id(manager()._pool) for manager in MANAGERS}) == 1


def test_rollback_session_leaves_no_trace(picks):
    before = value("SELECT COUNT(*) AS n FROM hr_oltp.employees")
    with DatabaseConnection.rollback_session():
        hired = hire(picks)
        assert value("SELECT COUNT(*) AS n FROM hr_oltp.employees") == before + 1     # visible inside
    assert value("SELECT COUNT(*) AS n FROM hr_oltp.employees") == before
    assert one("SELECT 1 AS found FROM hr_oltp.employees WHERE email = %s", (hired.email,)) is None


def test_a_failed_step_undoes_only_itself(session, picks):
    hired = hire(picks)
    with pytest.raises(BusinessRuleError):
        EmployeeManager().change_position(hired.employee_id, lab_position(picks))      # nothing changed
    assert EmployeeManager().get(hired.employee_id) == hired                          # earlier step kept


# ----------------------------------------------------------------------------- reference data

def test_reference_lists(picks):
    ref = ReferenceDataManager()
    assert ref.departments().department_name.tolist() == ["Human Resources", "Research & Development", "Sales"]
    assert "Laboratory Technician" in ref.roles_for(RND).role_name.tolist()
    assert "Laboratory Technician" not in ref.roles_for(SALES).role_name.tolist()
    lab = ref.roles_for(RND).set_index("job_role_id").loc[picks.lab_technician]
    assert ref.levels_for(picks.lab_technician).job_level_id.tolist() == \
        list(range(lab.min_job_level_id, lab.max_job_level_id + 1))
    assert dict(zip(ref.ratings().rating_id, ref.ratings().rating_label)) == RATING_LABELS
    candidates = ref.manager_candidates(SALES, min_level=3)
    assert len(candidates) and candidates.job_level.min() >= 3
    with pytest.raises(NotFoundError):
        ref.levels_for(250)


def test_reference_lists_are_cached_copies():
    ref = ReferenceDataManager()
    first = ref.departments()
    first.loc[0, "department_name"] = "changed by the caller"
    assert ref.departments().department_name.iloc[0] == "Human Resources"


# ----------------------------------------------------------------------------- EmployeeManager

def test_onboarding_creates_the_employee_and_the_first_warehouse_version(session, picks):
    last_code = value("SELECT MAX(employee_code) AS code FROM hr_oltp.employees")
    hired = hire(picks)
    assert hired.employee_code == f"EMP{int(last_code[3:]) + 1:06d}"
    assert EmployeeManager().get(hired.employee_id) == hired
    assert EmployeeManager().get_by_code(hired.employee_code.lower()) == hired
    assert EmployeeManager().current_position(hired.employee_id) == lab_position(picks)
    history = EmployeeManager().history(hired.employee_id)
    assert history[["version_number", "change_reason", "is_current"]].values.tolist() == [[1, "Hire", 1]]
    assert history.start_date.iloc[0] == picks.hire_date
    profile = EmployeeManager().profile(hired.employee_id)
    assert profile["department_name"] == "Research & Development" and profile["is_active"]
    assert EmployeeManager().search(hired.employee_code).employee_id.tolist() == [hired.employee_id]
    assert hired.employee_id in EmployeeManager().search("Asha Verma", department_id=RND, limit=1000).employee_id.tolist()


@pytest.mark.parametrize("position_changes, employee_changes, error, message", [
    (dict(department_id=SALES), {}, BusinessRuleError, "does not exist in the selected department"),
    (dict(job_level_id=5), {}, BusinessRuleError, "outside the allowed range"),
    (dict(monthly_income=Decimal("50000")), {}, BusinessRuleError, "outside the salary band"),
    ({}, dict(hire_date=date.today() + timedelta(days=10)), BusinessRuleError, "today or earlier"),
    ({}, dict(email="existing"), DuplicateError, "already used by another employee"),
])
def test_invalid_onboarding_is_refused_and_leaves_nothing(session, picks, position_changes, employee_changes,
                                                          error, message):
    if employee_changes.get("email") == "existing":
        employee_changes["email"] = value("SELECT email FROM hr_oltp.employees ORDER BY employee_id LIMIT 1")
    employee = new_employee(picks, **employee_changes)
    before = value("SELECT COUNT(*) AS n FROM hr_oltp.employees")
    with pytest.raises(error) as exc:
        EmployeeManager().onboard(employee, lab_position(picks, **position_changes))
    assert message in str(exc.value)
    assert value("SELECT COUNT(*) AS n FROM hr_oltp.employees") == before


def test_onboarding_needs_a_new_person(session, picks):
    with pytest.raises(ValidationError):
        EmployeeManager().onboard(new_employee(picks, employee_id=5), lab_position(picks))


def test_department_change_is_an_scd2_update(session, picks):
    """Phase 4 requirement: changing the department creates a new warehouse version."""
    hired = hire(picks)
    sales = Position(department_id=SALES, job_role_id=picks.sales_rep, job_level_id=1,
                     monthly_income=picks.level1_income, manager_id=picks.sales_manager)
    change = EmployeeManager().change_position(hired.employee_id, sales)
    assert change.change_reason == "Transfer" and change.effective_date == picks.today
    assert len(change.versions) == 2
    assert change.expired_version.department_name == "Research & Development"
    assert change.expired_version.end_date == picks.today - timedelta(days=1) and change.expired_version.is_current == 0
    assert change.new_version.department_name == "Sales" and change.new_version.is_current == 1
    assert change.new_version.start_date == picks.today and change.new_version.end_date == date(9999, 12, 31)
    assert EmployeeManager().current_position(hired.employee_id) == sales
    assert "new versions 1, expired 1" in one(
        "SELECT message FROM hr_dw.etl_batch_log WHERE batch_id = %s AND log_id <> batch_id", (change.batch_id,))["message"]


def test_promotion_reason_is_derived(session, picks):
    hired = hire(picks)
    promoted = lab_position(picks, job_level_id=2, monthly_income=picks.level1_income * 2)
    if not value("SELECT COUNT(*) AS n FROM hr_oltp.job_levels WHERE job_level_id = 2 "
                 "AND %s BETWEEN salary_band_min AND salary_band_max", (promoted.monthly_income,)):
        promoted = promoted.with_changes(monthly_income=value(
            "SELECT salary_band_min AS m FROM hr_oltp.job_levels WHERE job_level_id = 2"))
    assert EmployeeManager().change_position(hired.employee_id, promoted).change_reason == "Promotion"


def test_personal_corrections_overwrite_every_version(session, picks):
    """SCD Type 1: no new version, every version corrected."""
    hired = hire(picks)
    EmployeeManager().change_position(hired.employee_id, lab_position(picks, monthly_income=picks.level1_income + 1))
    updated = EmployeeManager().update_details(hired.with_changes(marital_status="Married", last_name="Verma-Rao"))
    assert updated.marital_status == "Married"
    rows = DatabaseConnection().fetch_all("SELECT marital_status, full_name FROM hr_dw.dim_employee "
                                          "WHERE employee_id = %s", (hired.employee_id,))
    assert [tuple(r.values()) for r in rows] == [("Married", "Asha Verma-Rao")] * 2


def test_exit_closes_assignments_and_updates_the_warehouse(session, picks):
    hired = hire(picks)
    project = new_project(picks)
    ProjectManager().assign(assignment(hired, project))
    assert EmployeeManager().record_exit(hired.employee_id, picks.today, picks.exit_reason_id) == 1
    assert not EmployeeManager().get(hired.employee_id).is_active
    assert ProjectManager().team(project.project_id).end_date.tolist() == [picks.today]
    flags = DatabaseConnection().fetch_all("SELECT attrition_flag, exit_date FROM hr_dw.dim_employee "
                                           "WHERE employee_id = %s", (hired.employee_id,))
    assert [tuple(r.values()) for r in flags] == [(1, picks.today)]
    with pytest.raises(BusinessRuleError, match="can no longer change jobs"):
        EmployeeManager().change_position(hired.employee_id, lab_position(picks, monthly_income=picks.level1_income + 1))
    with pytest.raises(BusinessRuleError, match="already left"):
        EmployeeManager().record_exit(hired.employee_id, picks.today, picks.exit_reason_id)


def test_exit_cannot_predate_the_last_review(session):
    row = one("SELECT e.employee_id, MAX(r.review_date) AS last_review FROM hr_oltp.vw_employee_current e "
              "JOIN hr_oltp.performance_reviews r ON r.employee_id = e.employee_id "
              "WHERE e.is_active AND e.employee_id <= 2000 GROUP BY e.employee_id, e.position_since "
              "HAVING e.position_since < MAX(r.review_date) - INTERVAL 1 DAY ORDER BY e.employee_id LIMIT 1")
    with pytest.raises(BusinessRuleError, match="last review"):
        EmployeeManager().record_exit(row["employee_id"], row["last_review"] - timedelta(days=1), 1)


def test_unknown_employees_are_not_found():
    for call in (lambda: EmployeeManager().get(99_999_999), lambda: EmployeeManager().get_by_code("EMP999999"),
                 lambda: EmployeeManager().current_position(99_999_999), lambda: EmployeeManager().profile(99_999_999)):
        with pytest.raises(NotFoundError):
            call()


# ----------------------------------------------------------------------------- ProjectManager

def test_project_create_read_update(session, picks):
    last_code = value("SELECT MAX(project_code) AS code FROM hr_oltp.projects")
    project = new_project(picks)
    assert project.project_code == f"PRJ{int(last_code[3:]) + 1:05d}"
    assert ProjectManager().get(project.project_id) == project
    assert ProjectManager().search(project.project_code).project_name.tolist() == ["Test Analytics Platform"]
    renamed = ProjectManager().update(project.with_changes(project_name="Renamed Platform", priority="Critical"))
    assert (renamed.project_name, renamed.priority) == ("Renamed Platform", "Critical")


def test_over_allocation_is_refused_unless_confirmed(session, picks):
    hired = hire(picks)
    first, second = new_project(picks), new_project(picks, project_name="Second Project")
    ProjectManager().assign(assignment(hired, first, pct=60))
    with pytest.raises(OverAllocationError) as exc:
        ProjectManager().assign(assignment(hired, second, pct=50))
    assert (exc.value.current_pct, exc.value.requested_pct) == (60, 50) and "makes 110 %" in str(exc.value)
    ProjectManager().assign(assignment(hired, second, pct=50), allow_over_allocation=True)
    assert ProjectManager().allocation_on(hired.employee_id, picks.today) == 110
    assert len(ProjectManager().assignments_for(hired.employee_id)) == 2


@pytest.mark.parametrize("case, message", [
    ("closed project", "can no longer be assigned"),
    ("before the project", "cannot start before the project"),
    ("before joining", "only joined on"),
])
def test_invalid_assignments_are_refused(session, picks, case, message):
    hired = hire(picks)
    if case == "closed project":
        project = new_project(picks, status="Completed", actual_end_date=picks.today - timedelta(5))
        start = None
    elif case == "before the project":
        project, start = new_project(picks), picks.today - timedelta(60)
    else:
        project = new_project(picks, start_date=picks.hire_date - timedelta(100))
        start = picks.hire_date - timedelta(10)
    with pytest.raises(BusinessRuleError, match=message):
        ProjectManager().assign(assignment(hired, project, start=start))


def test_closing_a_project_ends_its_assignments(session, picks):
    hired = hire(picks)
    project = new_project(picks)
    ProjectManager().assign(assignment(hired, project))
    closed = ProjectManager().update(project.close("Completed", picks.today))
    assert closed.is_closed and closed.delay_days < 0                                    # finished early
    assert ProjectManager().team(project.project_id).is_open.tolist() == [0]


def test_assignments_can_be_ended_or_removed(session, picks):
    hired = hire(picks)
    project = new_project(picks)
    first = ProjectManager().assign(assignment(hired, project))
    ProjectManager().end_assignment(first.assignment_id, picks.today)
    assert ProjectManager().allocation_on(hired.employee_id, picks.today + timedelta(1)) == 0
    ProjectManager().remove_assignment(first.assignment_id)
    assert ProjectManager().team(project.project_id).empty
    with pytest.raises(NotFoundError):
        ProjectManager().remove_assignment(first.assignment_id)


def test_only_unused_planned_projects_can_be_deleted(session, picks):
    planned = new_project(picks, status="Planned", start_date=picks.today + timedelta(10))
    ProjectManager().delete(planned.project_id)
    with pytest.raises(NotFoundError):
        ProjectManager().get(planned.project_id)
    with pytest.raises(BusinessRuleError, match="cancel it instead"):
        ProjectManager().delete(new_project(picks).project_id)                          # Active
    staffed = new_project(picks, status="Planned", start_date=picks.today + timedelta(10))
    ProjectManager().assign(assignment(hire(picks), staffed))
    with pytest.raises(BusinessRuleError, match="still has assignments"):
        ProjectManager().delete(staffed.project_id)


def test_deleting_a_warehoused_project_is_a_logged_batch(session):
    project_id = value(
        "SELECT p.project_id FROM hr_oltp.projects p JOIN hr_dw.dim_project dp ON dp.project_id = p.project_id "
        "WHERE p.status = 'Planned' AND NOT EXISTS (SELECT 1 FROM hr_oltp.project_assignments a "
        "                                          WHERE a.project_id = p.project_id) "
        "  AND NOT EXISTS (SELECT 1 FROM hr_oltp.performance_reviews r WHERE r.project_id = p.project_id) "
        "ORDER BY p.project_id LIMIT 1 ") if one(
        "SELECT 1 AS x FROM hr_oltp.projects p WHERE p.status = 'Planned' AND NOT EXISTS "
        "(SELECT 1 FROM hr_oltp.project_assignments a WHERE a.project_id = p.project_id) LIMIT 1") else None
    if project_id is None:
        pytest.skip("no unused planned project in the loaded data")
    version = AnalyticsManager().data_version()
    ProjectManager().delete(project_id)
    assert one("SELECT 1 AS x FROM hr_dw.dim_project WHERE project_id = %s", (project_id,)) is None
    assert AnalyticsManager().data_version() > version                     # the analytics cache sees the change


# ----------------------------------------------------------------------------- ReviewManager

def test_review_submit_read_and_withdraw(session, picks):
    hired = hire(picks)
    year = picks.today.year - 1
    submitted = ReviewManager().submit(Review(employee_id=hired.employee_id, reviewer_id=picks.rnd_manager,
                                              review_year=year, review_date=date(year + 1, 1, 15), rating_id=4,
                                              kpi_score=Decimal("91.5"), training_sessions=2,
                                              comments="Strong first full year"))
    assert ReviewManager().get(submitted.review_id) == submitted
    reviews = ReviewManager().reviews_for(hired.employee_id)
    assert reviews[["review_year", "rating_label"]].values.tolist() == [[year, "Outstanding"]]
    with pytest.raises(DuplicateError, match="already has a review for that year"):
        ReviewManager().submit(replace(submitted, review_id=None, rating_id=3))
    ReviewManager().withdraw(submitted.review_id)                         # not loaded into the warehouse yet
    assert ReviewManager().reviews_for(hired.employee_id).empty


@pytest.mark.parametrize("case, message", [
    ("future date", "cannot be in the future"),
    ("reviewer not employed yet", "The reviewer was not employed"),
    ("joined after the year", "joined after"),
    ("unknown reviewer", "does not exist"),
])
def test_invalid_reviews_are_refused(session, picks, case, message):
    hired = hire(picks)
    year = picks.today.year - 1
    review = Review(employee_id=hired.employee_id, reviewer_id=picks.rnd_manager, review_year=year,
                    review_date=date(year + 1, 1, 15), rating_id=3, kpi_score=Decimal("75"))
    if case == "future date":
        review = review.with_changes(review_year=picks.today.year, review_date=picks.today + timedelta(1))
    elif case == "reviewer not employed yet":
        review = review.with_changes(employee_id=picks.rnd_manager, reviewer_id=hired.employee_id,
                                     review_year=2019, review_date=date(2020, 2, 1))
    elif case == "joined after the year":
        review = review.with_changes(review_year=picks.hire_date.year - 1, review_date=picks.hire_date)
    else:
        review = review.with_changes(reviewer_id=99_999_999)
    with pytest.raises(HRAppError, match=message):
        ReviewManager().submit(review)


def test_reviews_already_in_the_warehouse_are_final(session):
    review_id = value("SELECT MIN(review_id) AS r FROM hr_dw.fact_performance_reviews")
    with pytest.raises(BusinessRuleError, match="already in the warehouse"):
        ReviewManager().withdraw(review_id)


def test_review_form_suggestions(session, picks):
    hired = hire(picks)
    year = picks.today.year - 1
    project = new_project(picks, start_date=date(year, 3, 1))
    ProjectManager().assign(assignment(hired, project, pct=80))
    suggestion = ReviewManager().suggest(hired.employee_id, year)
    assert suggestion["reviewer_id"] == picks.rnd_manager and suggestion["project_id"] == project.project_id


# ----------------------------------------------------------------------------- AnalyticsManager

@pytest.fixture(scope="module")
def analytics():
    return AnalyticsManager()


@pytest.fixture(scope="module")
def facts():
    return value("SELECT COUNT(*) AS n FROM hr_dw.fact_performance_reviews")


def test_filter_options(analytics):
    options = analytics.filter_options()
    assert options["departments"] == ["Human Resources", "Research & Development", "Sales"]
    assert options["fiscal_years"][0] == "FY2019-20" and len(options["fiscal_years"]) == 7


def test_yoy_trends_compare_each_cycle_with_the_previous(analytics, facts):
    trend = analytics.yoy_trends()
    assert trend.reviews.sum() == facts and trend.period.is_monotonic_increasing
    assert pd.isna(trend.rating_change.iloc[0])
    assert ((trend.avg_rating.diff() - trend.rating_change).dropna().abs() <= 0.0011).all()     # LAG
    by_department = analytics.yoy_trends(by_department=True)
    assert by_department.groupby("department_name").rating_change.apply(lambda s: pd.isna(s.iloc[0])).all()
    assert by_department.reviews.sum() == facts


def test_yoy_trends_respect_the_filters(analytics):
    trend = analytics.yoy_trends(AnalyticsFilter(departments=("Sales",), job_levels=(1, 2),
                                                 fiscal_years=("FY2024-25", "FY2025-26")))
    expected = value("SELECT COUNT(*) AS n FROM hr_dw.fact_performance_reviews f "
                     "JOIN hr_dw.dim_department dd ON dd.department_sk = f.department_sk "
                     "JOIN hr_dw.dim_employee de ON de.employee_sk = f.employee_sk "
                     "JOIN hr_dw.dim_date d ON d.date_sk = f.date_sk "
                     "WHERE dd.department_name = 'Sales' AND de.job_level IN (1, 2) "
                     "  AND d.fiscal_year IN ('FY2024-25', 'FY2025-26')")
    assert trend.period.tolist() == ["FY2024-25", "FY2025-26"] and trend.reviews.sum() == expected
    with pytest.raises(ValidationError):
        analytics.yoy_trends(period="weekly")


def test_top_performers_are_dense_ranked_per_department(analytics):
    cycle = AnalyticsFilter(fiscal_years=("FY2023-24", "FY2024-25", "FY2025-26"))
    top = analytics.top_performers(cycle, top_n=5, min_reviews=2)
    for _, department in top.groupby("department_name"):
        ranks = department.department_rank.tolist()
        assert sorted(set(ranks)) == list(range(1, 6))                                 # no gaps
        keys = list(zip(department.avg_rating, department.avg_kpi))
        assert keys == sorted(keys, reverse=True)
        assert (department.groupby("department_rank").size() == department.groupby("department_rank")
                .sharing_rank.first()).all()
    assert (top.reviews >= 2).all() and (top.has_left == 0).all()
    sales = analytics.top_performers(AnalyticsFilter(departments=("Sales",)), top_n=1)
    assert set(sales.department_name) == {"Sales"}
    with pytest.raises(ValidationError):
        analytics.top_performers(top_n=0)


def test_top_performer_matches_a_direct_query(analytics):
    top = analytics.top_performers(AnalyticsFilter(departments=("Human Resources",), fiscal_years=("FY2024-25",)),
                                   top_n=1, active_only=False)
    best = one("SELECT MAX(f.performance_rating * 1000 + f.kpi_score) AS best "
               "FROM hr_dw.fact_performance_reviews f JOIN hr_dw.dim_date d ON d.date_sk = f.date_sk "
               "JOIN hr_dw.dim_department dd ON dd.department_sk = f.department_sk "
               "WHERE d.fiscal_year = 'FY2024-25' AND dd.department_name = 'Human Resources'")["best"]
    assert (top.avg_rating * 1000 + top.avg_kpi).round(2).eq(float(best)).all()
    assert len(top) == top.sharing_rank.iloc[0]


def test_scored_workforce_matches_its_factors(analytics):
    scored = analytics.scored_workforce()
    assert len(scored) == value("SELECT COUNT(*) AS n FROM hr_dw.dim_employee WHERE is_current AND exit_date IS NULL")
    flags = scored[[f"factor_{i}" for i in range(len(RISK_FACTORS))]]
    assert (flags.values @ [f.points for f in RISK_FACTORS] == scored.score).all()     # SQL score = sum of factors
    assert scored.score.between(0, MAX_RISK_SCORE).all()
    for low, band in RISK_BANDS:
        assert (scored[scored.risk_band == band].score >= low).all()
    by_score = scored.groupby("score").risk_percentile.agg(["min", "max"])
    assert (by_score["min"] == by_score["max"]).all() and by_score["min"].is_monotonic_increasing


def test_attrition_risk_views(analytics):
    rnd_level1 = AnalyticsFilter(departments=("Research & Development",), job_levels=(1,))
    risky = analytics.attrition_risk(rnd_level1, limit=50, min_band="High")
    assert len(risky) == 50 and risky.score.is_monotonic_decreasing
    assert set(risky.department_name) == {"Research & Development"} and set(risky.job_level) == {1}
    assert set(risky.risk_band) <= {"High", "Very high"} and risky.risk_factors.str.contains("Entry level").all()
    summary = analytics.risk_summary()
    assert summary.employees.sum() == len(analytics.scored_workforce())
    assert (summary.groupby("department_name").share_pct.sum() - 100).abs().max() < 0.3
    prevalence = analytics.risk_factor_prevalence()
    assert prevalence.factor.tolist()[0] and prevalence.share_pct.is_monotonic_decreasing
    with pytest.raises(ValidationError):
        analytics.attrition_risk(min_band="Extreme")


def test_risk_score_separates_past_leavers(analytics):
    """Back-test: each higher band lost more people within a year; the top band at least 4x the average."""
    backtest = analytics.risk_backtest()
    assert backtest.risk_band.tolist() == ["Low", "Medium", "High", "Very high"]
    assert backtest.left_within_year_pct.is_monotonic_increasing
    assert backtest.lift.iloc[-1] >= 4 and backtest.lift.iloc[0] < 0.5
    assert backtest.share_of_leavers_pct.sum() == pytest.approx(100, abs=0.3)


def test_attrition_trend_uses_point_in_time_departments(analytics):
    trend = analytics.attrition_trend()
    exits = DatabaseConnection().fetch_frame(
        "SELECT d.fiscal_year, COUNT(*) AS leavers FROM hr_dw.dim_employee e "
        "JOIN hr_dw.dim_date d ON d.full_date = e.exit_date WHERE e.is_current GROUP BY d.fiscal_year")
    merged = trend.merge(exits, on="fiscal_year", suffixes=("", "_direct"))
    assert (merged.leavers == merged.leavers_direct).all()
    assert trend.is_complete.iloc[:-1].all() and not trend.is_complete.iloc[-1]          # the running year last
    assert trend.headcount_end.iloc[-1] == len(analytics.scored_workforce())
    by_department = analytics.attrition_trend(by_department=True)
    totals = by_department.groupby("fiscal_year")[["leavers", "headcount_start"]].sum()
    assert (totals.leavers.values == trend.leavers.values).all()
    assert (totals.headcount_start.values == trend.headcount_start.values).all()


def test_overview(analytics):
    overview = analytics.overview()
    trend = analytics.attrition_trend()
    assert overview["headcount"] == len(analytics.scored_workforce())
    assert overview["latest_cycle"] == analytics.filter_options()["fiscal_years"][-1]
    assert overview["attrition_year"] == trend[trend.is_complete == 1].fiscal_year.iloc[-1]
    assert overview["open_projects"] >= overview["overdue_projects"] > 0


def test_project_bottlenecks(analytics):
    completed = value("SELECT COUNT(*) AS n FROM hr_dw.dim_project WHERE status = 'Completed'")
    for by in ("priority", "budget_band", "department", "year"):
        assert analytics.project_delivery(by=by).completed_projects.sum() == completed
    overdue = analytics.bottleneck_projects(limit=500)
    assert (overdue.days_overdue > 0).all() and overdue.days_overdue.is_monotonic_decreasing
    assert set(overdue.status) <= {"Planned", "Active", "On Hold"}
    assert overdue.overdue_rank.iloc[0] == 1
    with pytest.raises(ValidationError):
        analytics.project_delivery(by="colour")


def test_cached_results_are_copies(analytics):
    first = analytics.yoy_trends()
    first.loc[0, "reviews"] = -1
    assert analytics.yoy_trends().reviews.iloc[0] > 0


def test_cache_is_bypassed_in_a_rollback_session(picks, analytics):
    analytics.yoy_trends()
    cached = len(analytics._cache)
    with DatabaseConnection.rollback_session():
        version = analytics.data_version()
        hired = hire(picks)                                    # an SCD Type 2 apply: a new warehouse batch
        assert analytics.data_version() > version
        assert hired.employee_code in analytics.scored_workforce().employee_code.tolist()   # sees uncommitted data
        assert len(analytics._cache) == cached                 # ... but never caches it
    assert hired.employee_code not in analytics.scored_workforce().employee_code.tolist()

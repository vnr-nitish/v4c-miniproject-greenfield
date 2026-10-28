"""Unit tests without a database: the entity classes, the analytics filter and the translation
of MySQL errors into application exceptions (docs/backend_design.md)."""
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import mysql.connector
import numpy as np
import pandas as pd
import pytest

from src.dal.analytics_manager import MAX_RISK_SCORE, RISK_BANDS, RISK_FACTORS, AnalyticsFilter
from src.db_manager import translate_error
from src.exceptions import BusinessRuleError, DatabaseError, DuplicateError, HRAppError, ValidationError
from src.models import Assignment, Employee, Position, Project, Review


def employee(**changes) -> Employee:
    values = dict(first_name="Asha", last_name="Verma", email="asha.verma@example.com", phone="+91 98765 43210",
                  gender="Female", date_of_birth=date(1995, 5, 5), marital_status="Single", education_level_id=3,
                  education_field_id=2, distance_from_home_km=8, prior_experience_years=2, prior_companies_count=1,
                  hire_date=date(2024, 6, 1))
    values.update(changes)
    return Employee(**values)


def position(**changes) -> Position:
    values = dict(department_id=2, job_role_id=3, job_level_id=1, monthly_income=Decimal("2500.00"), manager_id=17)
    values.update(changes)
    return Position(**values)


def project(**changes) -> Project:
    values = dict(project_name="Data Platform", department_id=2, start_date=date(2025, 1, 1),
                  planned_end_date=date(2025, 6, 30), budget=Decimal("500000"), status="Active")
    values.update(changes)
    return Project(**values)


def review(**changes) -> Review:
    values = dict(employee_id=10, reviewer_id=17, review_year=2024, review_date=date(2025, 2, 1), rating_id=3,
                  kpi_score=Decimal("82.5"))
    values.update(changes)
    return Review(**values)


def errors_of(factory, **changes) -> dict:
    with pytest.raises(ValidationError) as exc:
        factory(**changes)
    return exc.value.errors


# ----------------------------------------------------------------------------- Entity behaviour

def test_entities_are_immutable():
    e = employee()
    with pytest.raises(FrozenInstanceError):
        e.email = "other@example.com"


def test_changes_create_a_new_validated_object():
    e = employee()
    moved = e.with_changes(distance_from_home_km=20)
    assert moved.distance_from_home_km == 20 and e.distance_from_home_km == 8      # original unchanged
    with pytest.raises(ValidationError):
        e.with_changes(distance_from_home_km=80)                                     # the copy is validated too


def test_input_is_normalized():
    e = employee(first_name="  Asha ", email=" Asha.Verma@Example.COM ")
    assert e.first_name == "Asha" and e.email == "asha.verma@example.com"
    p = position(monthly_income=2500.456)
    assert p.monthly_income == Decimal("2500.46")
    r = review(overtime_flag=1, kpi_score="91.25", comments="   ")
    assert r.overtime_flag is True and r.kpi_score == Decimal("91.25") and r.comments is None


def test_numpy_values_from_dataframes_become_python_values():
    p = position(department_id=np.int64(2), job_level_id=np.int32(1))
    assert type(p.department_id) is int and type(p.job_level_id) is int


def test_every_broken_rule_is_reported_at_once():
    errors = errors_of(employee, email="not-an-email", phone="12", date_of_birth=date(2010, 1, 1))
    assert {"email", "phone", "hire_date"} <= errors.keys()
    assert errors["hire_date"] == "An employee must be at least 18 on the hire date"


def test_from_row_ignores_extra_columns_and_round_trips():
    e = employee(employee_id=5, employee_code="EMP000005")
    row = {**e.to_record(), "full_name": "ignored", "is_active": 1}
    assert Employee.from_row(row) == e


def test_validation_error_message_and_fields():
    err = ValidationError({"email": "Enter a valid e-mail address", "phone": "Too short"})
    assert str(err) == "email: Enter a valid e-mail address; phone: Too short"
    assert ValidationError("Plain message").errors == {"": "Plain message"}
    assert isinstance(err, HRAppError)


# ----------------------------------------------------------------------------- Employee

@pytest.mark.parametrize("changes, field", [
    (dict(employee_code="E123"), "employee_code"),
    (dict(first_name="Asha2"), "first_name"),
    (dict(last_name=""), "last_name"),
    (dict(email="a@b"), "email"),
    (dict(phone="phone"), "phone"),
    (dict(gender="X"), "gender"),
    (dict(marital_status="Unknown"), "marital_status"),
    (dict(education_level_id=6), "education_level_id"),
    (dict(education_field_id=0), "education_field_id"),
    (dict(distance_from_home_km=0), "distance_from_home_km"),
    (dict(prior_experience_years=51), "prior_experience_years"),
    (dict(prior_experience_years=0, prior_companies_count=2), "prior_companies_count"),   # rule D19
    (dict(prior_experience_years=3, prior_companies_count=0), "prior_companies_count"),
    (dict(hire_date="2024-06-01"), "hire_date"),
    (dict(exit_date=date(2025, 1, 1)), "exit_reason_id"),                                # exit needs a reason
    (dict(exit_date=date(2024, 1, 1), exit_reason_id=1), "exit_date"),                   # before the hire date
])
def test_employee_rules(changes, field):
    assert field in errors_of(employee, **changes)


def test_names_with_apostrophes_and_hyphens_are_valid():
    for last_name in ("D’Alia", "O'Neil", "Sen-Gupta", "de Souza"):
        assert employee(last_name=last_name).last_name == last_name


def test_employee_behaviour():
    e = employee(exit_date=date(2027, 3, 1), exit_reason_id=2)
    assert e.full_name == "Asha Verma" and not e.is_active
    assert e.age_on(date(2025, 5, 4)) == 29 and e.age_on(date(2025, 5, 5)) == 30
    assert e.tenure_on(date(2026, 6, 1)) == 2
    assert e.tenure_on(date(2030, 1, 1)) == 2                  # counted up to the exit date
    assert e.total_experience_on(date(2026, 6, 1)) == 4        # 2 prior + 2 at the company


# ----------------------------------------------------------------------------- Position

@pytest.mark.parametrize("changes, field", [
    (dict(job_level_id=6), "job_level_id"),
    (dict(monthly_income=0), "monthly_income"),
    (dict(monthly_income="a lot"), "monthly_income"),
    (dict(stock_option_level=4), "stock_option_level"),
    (dict(business_travel="Always"), "business_travel"),
    (dict(manager_id=0), "manager_id"),
])
def test_position_rules(changes, field):
    assert field in errors_of(position, **changes)


def test_position_changes():
    old = position()
    new = old.with_changes(department_id=3, job_level_id=2, monthly_income=Decimal("5200"))
    assert new.changes_from(old) == {"department_id": (2, 3), "job_level_id": (1, 2),
                                     "monthly_income": (Decimal("2500.00"), Decimal("5200.00"))}
    assert new.is_promotion_from(old) and new.is_transfer_from(old)
    assert old.changes_from(old) == {} and not old.is_promotion_from(old)


# ----------------------------------------------------------------------------- Project and Assignment

@pytest.mark.parametrize("changes, field", [
    (dict(project_code="P1"), "project_code"),
    (dict(project_name=" "), "project_name"),
    (dict(budget=-5), "budget"),
    (dict(status="Done"), "status"),
    (dict(priority="Urgent"), "priority"),
    (dict(planned_end_date=date(2024, 12, 31)), "planned_end_date"),
    (dict(status="Completed"), "actual_end_date"),                                   # closed needs an end
    (dict(actual_end_date=date(2025, 7, 1)), "actual_end_date"),                     # open has none
    (dict(status="Completed", actual_end_date=date(2024, 12, 1)), "actual_end_date"),
])
def test_project_rules(changes, field):
    assert field in errors_of(project, **changes)


def test_project_delay_and_closing():
    p = project()
    assert p.delay_days is None and p.is_delayed is None and not p.is_closed
    assert p.planned_duration_days == 180
    assert p.is_overdue_on(date(2025, 7, 1)) and not p.is_overdue_on(date(2025, 6, 30))
    late = p.close("Completed", date(2025, 7, 15))
    assert late.delay_days == 15 and late.is_delayed and late.is_closed and not late.is_overdue_on(date(2026, 1, 1))
    assert p.close("Completed", date(2025, 6, 20)).delay_days == -10
    assert p.close("Cancelled", date(2025, 3, 1)).delay_days is None                 # only completed projects


def test_assignment_rules_and_behaviour():
    a = Assignment(employee_id=1, project_id=2, project_role="Developer", allocation_pct=60,
                   start_date=date(2025, 1, 1))
    assert a.is_open and a.active_on(date(2030, 1, 1)) and not a.active_on(date(2024, 12, 31))
    ended = a.end(date(2025, 3, 31))
    assert not ended.is_open and not ended.active_on(date(2025, 4, 1))
    for changes, field in ((dict(allocation_pct=0), "allocation_pct"), (dict(allocation_pct=101), "allocation_pct"),
                           (dict(end_date=date(2024, 12, 1)), "end_date"), (dict(project_role=""), "project_role")):
        assert field in errors_of(a.with_changes, **changes)


# ----------------------------------------------------------------------------- Review

@pytest.mark.parametrize("changes, field", [
    (dict(reviewer_id=10), "reviewer_id"),                       # self-review
    (dict(review_date=date(2026, 1, 1)), "review_date"),         # outside the review year and the next
    (dict(review_date=date(2023, 12, 31)), "review_date"),
    (dict(rating_id=5), "rating_id"),
    (dict(kpi_score=100.5), "kpi_score"),
    (dict(training_sessions=21), "training_sessions"),
    (dict(review_year=1999), "review_year"),
    (dict(comments="x" * 501), "comments"),
])
def test_review_rules(changes, field):
    assert field in errors_of(review, **changes)


def test_review_behaviour():
    r = review(rating_id=4)
    assert r.rating_label == "Outstanding" and r.is_outstanding and r.is_held_in_following_year
    assert not review(review_date=date(2024, 12, 15)).is_held_in_following_year


# ----------------------------------------------------------------------------- MySQL errors -> application errors

@pytest.mark.parametrize("errno, message, expected_type, expected_text", [
    (1644, "This job role does not exist in the selected department", BusinessRuleError,
     "This job role does not exist in the selected department"),
    (1062, "Duplicate entry 'a@b.com' for key 'employees.uq_employees_email'", DuplicateError,
     "This e-mail address is already used by another employee"),
    (1062, "Duplicate entry '7-2025' for key 'performance_reviews.uq_reviews_employee_year'", DuplicateError,
     "The employee already has a review for that year"),
    (1452, "Cannot add or update a child row: a foreign key constraint fails (`hr_oltp`.`performance_reviews`, "
           "CONSTRAINT `fk_reviews_project` FOREIGN KEY ...)", BusinessRuleError, "The selected project does not exist"),
    (1451, "Cannot delete or update a parent row: a foreign key constraint fails (... CONSTRAINT "
           "`fk_assignments_project` ...)", BusinessRuleError, "still has assignments"),
    (3819, "Check constraint 'ck_reviews_not_self' is violated.", ValidationError,
     "An employee cannot review themselves"),
    (1406, "Data too long for column 'first_name' at row 1", ValidationError, "Data too long"),
    (2013, "Lost connection to MySQL server during query", DatabaseError, "Lost connection"),
])
def test_mysql_errors_become_application_errors(errno, message, expected_type, expected_text):
    err = translate_error(mysql.connector.Error(msg=message, errno=errno))
    assert type(err) is expected_type and expected_text in str(err)
    assert isinstance(err, HRAppError)


def test_duplicates_are_business_rule_errors():
    assert issubclass(DuplicateError, BusinessRuleError)


# ----------------------------------------------------------------------------- AnalyticsFilter and the risk score

def test_filter_builds_bound_conditions():
    f = AnalyticsFilter(departments=["Sales", "Human Resources"], job_levels=[np.int64(1)], fiscal_years=["FY2024-25"])
    sql, params = f.where("dd.department_name", "de.job_level", "d.fiscal_year")
    assert sql == (" AND dd.department_name IN (%s, %s) AND de.job_level IN (%s) AND d.fiscal_year IN (%s)")
    assert params == ["Sales", "Human Resources", 1, "FY2024-25"]
    assert f.where("p.owning_department_name") == (" AND p.owning_department_name IN (%s, %s)",
                                                   ["Sales", "Human Resources"])
    assert AnalyticsFilter().where("x", "y", "z") == ("", [])
    assert hash(f) == hash(AnalyticsFilter(("Sales", "Human Resources"), (1,), ("FY2024-25",)))   # a cache key


@pytest.mark.parametrize("changes, field", [
    (dict(job_levels=(0,)), "job_levels"), (dict(job_levels=("two",)), "job_levels"),
    (dict(fiscal_years=("2024",)), "fiscal_years"), (dict(fiscal_years=("FY2024-25; DROP TABLE x",)), "fiscal_years"),
])
def test_invalid_filters_are_refused(changes, field):
    assert field in errors_of(AnalyticsFilter, **changes)


def test_filter_selects_dataframe_rows():
    frame = pd.DataFrame({"department_name": ["Sales", "Sales", "Human Resources"], "job_level": [1, 2, 1]})
    assert AnalyticsFilter(departments=("Sales",), job_levels=(2,)).select(frame).index.tolist() == [1]
    assert len(AnalyticsFilter().select(frame)) == 3


def test_risk_bands_cover_every_score():
    assert MAX_RISK_SCORE == 12 and len(RISK_FACTORS) == 9
    lows = [low for low, _ in RISK_BANDS]
    assert lows == sorted(lows, reverse=True) and lows[-1] == 0
    # personal characteristics are not risk factors (backend_design.md B8)
    assert not any(word in f.condition for f in RISK_FACTORS for word in ("marital", "gender", "birth", "age"))

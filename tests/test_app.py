"""UI smoke tests (Streamlit AppTest): every page renders against the real data without an exception.

Read-only: no test presses a button that writes. The analytics cache is shared with the test
process, so the slow warehouse queries run once.
"""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src.dal import AnalyticsManager
from src.db_manager import DatabaseConnection
from src.exceptions import DatabaseError, HRAppError, PoolBusyError
from src.ui import charts

PAGES = ["overview", "trends", "top_performers", "attrition", "bottlenecks", "operations",
         "onboard", "employee_360", "projects", "reviews"]


@pytest.fixture(scope="module", autouse=True)
def database():
    try:
        DatabaseConnection().fetch_one("SELECT 1 AS ok")
    except HRAppError as err:
        pytest.skip(f"MySQL not available: {err}")


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(f"../views/{page}.py", default_timeout=180).run()
    assert not at.exception, at.exception


def test_app_shell_renders():
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert len(at.sidebar.multiselect) == 3          # department, job level, review cycle filters


def test_long_bar_labels_stay_horizontal_and_are_not_repeated_in_a_legend():
    """Slanted department names used to run into the legend below the chart."""
    frame = pd.DataFrame({"department_name": ["Human Resources", "Research & Development", "Sales"], "employees": [1, 2, 3]})
    named_twice = charts.bars(frame, "department_name", "employees", "department_name")
    assert named_twice.layout.showlegend is False
    assert named_twice.layout.xaxis.tickangle == 0
    assert list(named_twice.layout.xaxis.ticktext) == ["Human<br>Resources", "Research &<br>Development", "Sales"]
    with_bands = charts.bars(frame.assign(band="Low"), "department_name", "employees", "band")
    assert with_bands.layout.showlegend is not False
    numbers = pd.DataFrame({"year": [2024, 2025], "employees": [1, 2]})
    assert charts.bars(numbers, "year", "employees").layout.xaxis.tickvals is None


def raiser(error):
    def fail(*args, **kwargs):
        raise error
    return fail


def test_a_busy_database_keeps_the_pool_everyone_shares(monkeypatch):
    """Decision B22, the crash on the live site: "busy" used to wipe the shared pool, and every
    other visitor's page then failed with AttributeError."""
    resets = []
    monkeypatch.setattr(DatabaseConnection, "reset", lambda: resets.append(1))
    monkeypatch.setattr(AnalyticsManager, "data_version", raiser(PoolBusyError("All 3 connections are busy")))
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert any("busy" in w.value for w in at.warning)
    assert not resets


def test_an_unreachable_database_shows_what_to_check(monkeypatch):
    resets = []
    monkeypatch.setattr(DatabaseConnection, "reset", lambda: resets.append(1))
    monkeypatch.setattr(AnalyticsManager, "data_version", raiser(DatabaseError("Could not connect")))
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert any("not reachable" in e.value for e in at.error)
    assert resets


@pytest.mark.parametrize("error, shown", [(PoolBusyError("connections are busy"), "warning"),
                                          (DatabaseError("connection lost"), "error")])
def test_a_problem_no_page_handled_is_a_message_not_a_crash(error, shown, monkeypatch):
    monkeypatch.setattr("src.ui.common.current_filters", raiser(error))
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert any(str(error) in element.value for element in getattr(at, shown))


@pytest.mark.parametrize("page", ["onboard", "employee_360", "projects", "reviews", "operations"])
def test_data_entry_pages_are_locked_by_the_passcode(page, monkeypatch):
    """With APP_PASSCODE set (the public site), every data-entry page renders its unlock form(s)."""
    monkeypatch.setenv("APP_PASSCODE", "test-passcode")
    at = AppTest.from_file(f"../views/{page}.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert any("protected" in i.value for i in at.info)


def test_invalid_onboarding_shows_field_errors(monkeypatch):
    monkeypatch.delenv("APP_PASSCODE", raising=False)
    at = AppTest.from_file("../views/onboard.py", default_timeout=180)
    at.session_state["editor"] = True                # unlocked, whatever the environment says
    at.run()
    at.text_input[0].input("Asha2").run()            # invalid first name, other fields empty
    at.button[0].click().run()
    assert not at.exception
    assert any("First name" in w.value for w in at.warning)

"""UI smoke tests (Streamlit AppTest): every page renders against the real data without an exception.

Read-only: no test presses a button that writes. The analytics cache is shared with the test
process, so the slow warehouse queries run once.
"""
import pytest
from streamlit.testing.v1 import AppTest

from src.db_manager import DatabaseConnection
from src.exceptions import HRAppError

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

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


def test_invalid_onboarding_shows_field_errors():
    at = AppTest.from_file("../views/onboard.py", default_timeout=180).run()
    at.text_input[0].input("Asha2").run()            # invalid first name, other fields empty
    at.button[0].click().run()
    assert not at.exception
    assert any("First name" in w.value for w in at.warning)

"""UI smoke tests (Streamlit AppTest): every page renders against the real data without an exception.

Read-only: no test presses a button that writes. The analytics cache is shared with the test
process, so the slow warehouse queries run once.
"""
from datetime import date

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src.dal import AnalyticsManager, EmployeeManager, ProjectManager, ReferenceDataManager
from src.dal.employee_manager import PositionChange
from src.db_manager import DatabaseConnection
from src.exceptions import DatabaseError, HRAppError, PoolBusyError
from src.ui import charts
from src.ui.common import fact_cards

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
    at = AppTest.from_file(f"../views/{page}.py", default_timeout=180)
    at.session_state["e360_search"] = long_career().employee_code          # Employee 360 asks after a search
    at.run()
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


def onboarding_form(monkeypatch):
    """The onboarding page, unlocked, with the save replaced: nothing is written to the database."""
    monkeypatch.delenv("APP_PASSCODE", raising=False)
    manager = EmployeeManager()
    existing = manager.get_by_code(manager.example_codes(1)[0])
    saved = []
    monkeypatch.setattr(EmployeeManager, "onboard", lambda self, employee, position: saved.append(position) or existing)
    at = AppTest.from_file("../views/onboard.py", default_timeout=180)
    at.session_state["editor"] = True
    at.run()
    return at, existing, saved


def type_a_person(at) -> None:
    """Last step before pressing Onboard: this test tool forgets a form's unsent text on every run."""
    typed = {"First name": "Asha", "Last name": "Rao", "E-mail": "asha.rao@example.com", "Phone": "+91 98765 43210"}
    for field in at.text_input:
        field.input(typed[field.label])


def test_onboarding_shows_a_pop_up_and_then_an_empty_form(monkeypatch):
    at, existing, saved = onboarding_form(monkeypatch)
    type_a_person(at)
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert not at.warning, [w.value for w in at.warning]
    assert saved and saved[0].stock_option_level == 0
    assert any(existing.employee_code in s.value for s in at.success)      # the pop-up
    assert not hasattr(at, "balloons") or not at.balloons
    assert [field.value for field in at.text_input] == ["", "", "", ""]    # the form behind it is already empty
    at.button(key="announcement_ok").click().run()
    assert not at.exception, at.exception
    assert not at.success                                                  # OK closed the pop-up
    assert [field.value for field in at.text_input] == ["", "", "", ""]


def test_each_position_choice_still_narrows_the_next_fields(monkeypatch):
    """The fields are read before they are drawn, so the choices are taken from the session."""
    at, _, saved = onboarding_form(monkeypatch)
    sales = ReferenceDataManager().roles_for(3)                       # department 3 is Sales
    at.selectbox(key="onboard_0_dept").set_value(3).run()
    assert not at.exception, at.exception
    roles = next(box for box in at.selectbox if box.label == "Job role")
    assert "Sales Executive" in roles.options and "Research Scientist" not in roles.options
    roles.set_value(int(sales[sales.role_name == "Manager"].job_role_id.iloc[0])).run()
    assert not at.exception, at.exception
    levels = next(box for box in at.selectbox if box.label == "Job level")
    assert all(option[0] in "345" for option in levels.options)       # a Manager is level 3 or above
    at.button_group(key="onboard_0_stock").set_value(2).run()
    assert not at.exception, at.exception
    type_a_person(at)
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert not at.warning, [w.value for w in at.warning]
    assert saved and (saved[0].department_id, saved[0].stock_option_level) == (3, 2)


def test_invalid_onboarding_keeps_what_was_typed(monkeypatch):
    at, _, saved = onboarding_form(monkeypatch)
    type_a_person(at)
    next(field for field in at.text_input if field.label == "E-mail").input("not an e-mail address")
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert not saved and not at.success
    assert any("e-mail" in w.value for w in at.warning)
    assert next(field for field in at.text_input if field.label == "First name").value == "Asha"


# ----------------------------------------------------------------------------- Employee 360

TABS = ["Details", "Career timeline", "Update", "Reviews & projects", "Exit"]


def long_career():
    manager = EmployeeManager()
    return manager.get_by_code(manager.example_codes(1)[0])


def employee_page(monkeypatch, query: str) -> AppTest:
    monkeypatch.delenv("APP_PASSCODE", raising=False)
    at = AppTest.from_file("../views/employee_360.py", default_timeout=180)
    at.session_state["e360_search"] = query
    at.run()
    assert not at.exception, at.exception
    return at


def press(at: AppTest, label: str) -> None:
    next(button for button in at.button if button.label == label).click().run()
    assert not at.exception, at.exception


def test_employee_360_starts_with_only_the_search(monkeypatch):
    at = employee_page(monkeypatch, "")
    assert not at.tabs and at.text_input(key="e360_search").value == ""


@pytest.mark.parametrize("field", ["employee_code", "email", "employee_id"])
def test_employee_360_opens_on_an_exact_search(field, monkeypatch):
    employee = long_career()
    at = employee_page(monkeypatch, str(getattr(employee, field)))
    assert [tab.label for tab in at.tabs] == TABS
    assert any(employee.employee_code in m.value for m in at.markdown)
    assert not at.metric and not at.get("plotly_chart")                    # no boxes, and the timeline is a table
    details = " ".join(m.value for m in at.tabs[0].markdown)
    assert employee.email in details and employee.phone in details
    assert len(at.tabs[1].dataframe) == 1
    assert len(at.tabs[3].dataframe) == 2                                  # reviews and projects


def test_employee_360_lists_several_matches_before_opening_one(monkeypatch):
    at = employee_page(monkeypatch, "an")
    assert not at.tabs and len(at.dataframe) == 1
    assert any("matches" in c.value for c in at.caption)


def test_employee_360_lists_everyone_who_shares_a_phone_number(monkeypatch):
    """The generated data has about 5,000 different numbers for 100,000 people."""
    at = employee_page(monkeypatch, long_career().phone)
    assert not at.tabs and len(at.dataframe) == 1


def test_employee_360_says_when_nobody_matches(monkeypatch):
    at = employee_page(monkeypatch, "no such person")
    assert not at.tabs and any("No employee matches" in i.value for i in at.info)


def test_employee_360_updates_personal_details_in_a_pop_up(monkeypatch):
    employee = long_career()
    saved = []
    monkeypatch.setattr(EmployeeManager, "update_details", lambda self, e: saved.append(e) or e)    # nothing is written
    at = employee_page(monkeypatch, employee.employee_code)
    at.button_group(key=f"e360_update_{employee.employee_id}").set_value("Personal details").run()
    assert not at.exception, at.exception
    next(field for field in at.text_input if field.label == "Last name").input("Changed")
    press(at, "Save details")
    assert saved and saved[0].last_name == "Changed" and saved[0].email == employee.email
    assert saved[0].hire_date == employee.hire_date                        # has its own rules: not on this form
    assert any("were saved" in s.value for s in at.success)
    at.button(key="announcement_ok").click().run()
    assert not at.exception, at.exception and not at.success


def test_employee_360_refuses_invalid_personal_details(monkeypatch):
    employee = long_career()
    saved = []
    monkeypatch.setattr(EmployeeManager, "update_details", lambda self, e: saved.append(e) or e)
    at = employee_page(monkeypatch, employee.employee_code)
    at.button_group(key=f"e360_update_{employee.employee_id}").set_value("Personal details").run()
    next(field for field in at.text_input if field.label == "E-mail").input("not an address")
    press(at, "Save details")
    assert not saved and any("e-mail" in w.value for w in at.warning)


def test_employee_360_changes_position_in_a_pop_up(monkeypatch):
    employee = long_career()
    history = EmployeeManager().history(employee.employee_id)
    outcome = PositionChange(employee.employee_id, 1, "Transfer", date.today(), 999, history)
    monkeypatch.setattr(EmployeeManager, "change_position", lambda self, *args: outcome)            # nothing is written
    at = employee_page(monkeypatch, employee.employee_code)
    press(at, "Apply change")
    assert any("Transfer recorded" in s.value for s in at.success)


@pytest.mark.parametrize("condition", ["h.manager_id IS NOT NULL AND h.business_travel = 'Travel_Frequently'",
                                       "h.manager_id IS NULL AND h.business_travel = 'Non-Travel'"])
def test_a_position_change_starts_from_what_is_true_today(condition, monkeypatch):
    """The manager and travel used to start on fixed defaults, so changing anything else changed them too."""
    code = DatabaseConnection().fetch_one(
        "SELECT e.employee_code FROM hr_oltp.employees e JOIN hr_oltp.employee_job_history h "
        f"ON h.employee_id = e.employee_id AND h.effective_end_date IS NULL WHERE e.is_active AND {condition} LIMIT 1"
    )["employee_code"]
    manager = EmployeeManager()
    current = manager.current_position(manager.get_by_code(code).employee_id)
    sent = []

    def record(self, employee_id, position, *args):
        sent.append(position)
        return PositionChange(employee_id, 1, "Transfer", date.today(), 999, self.history(employee_id))

    monkeypatch.setattr(EmployeeManager, "change_position", record)                                 # nothing is written
    at = employee_page(monkeypatch, code)
    press(at, "Apply change")
    assert sent and sent[0].manager_id == current.manager_id
    assert sent[0].business_travel == current.business_travel
    assert (sent[0].department_id, sent[0].job_role_id, sent[0].job_level_id) == (
        current.department_id, current.job_role_id, current.job_level_id)
    assert sent[0].stock_option_level == current.stock_option_level


def test_employee_360_records_an_exit_in_a_pop_up(monkeypatch):
    employee = long_career()
    monkeypatch.setattr(EmployeeManager, "record_exit", lambda self, *args: 2)                      # nothing is written
    at = employee_page(monkeypatch, employee.employee_code)
    press(at, "Record exit")
    assert any("2 assignment(s) closed" in s.value for s in at.success)


def test_employee_360_keeps_a_leaver_read_only(monkeypatch):
    code = DatabaseConnection().fetch_one("SELECT employee_code FROM hr_oltp.employees "
                                          "WHERE exit_date IS NOT NULL LIMIT 1")["employee_code"]
    at = employee_page(monkeypatch, code)
    assert [tab.label for tab in at.tabs] == TABS
    assert any("can no longer change" in i.value for i in at.tabs[2].info)
    assert any("left on" in i.value for i in at.tabs[4].info)
    assert not [button for button in at.button if button.label in ("Apply change", "Record exit")]


@pytest.mark.parametrize("page", PAGES)
def test_page_headings_have_no_line_under_them(page, monkeypatch):
    at = AppTest.from_file(f"../views/{page}.py", default_timeout=180).run()
    heading = next(m.value for m in at.markdown if "class='hero'" in m.value)
    assert "<p>" not in heading


# ----------------------------------------------------------------------------- Projects

def projects_page(monkeypatch) -> AppTest:
    monkeypatch.delenv("APP_PASSCODE", raising=False)
    at = AppTest.from_file("../views/projects.py", default_timeout=180).run()
    assert not at.exception, at.exception
    return at


def look_up(at: AppTest, code: str) -> None:
    next(field for field in at.text_input if field.label == "Project code").input(code)
    press(at, "Submit")


def active_project():
    return ProjectManager().search(status="Active", limit=1).iloc[0]


def test_projects_has_four_tabs_and_a_dashboard_without_row_selection(monkeypatch):
    at = projects_page(monkeypatch)
    assert [tab.label for tab in at.tabs] == ["Dashboard", "Project details", "Create", "Assign people"]
    assert len(at.tabs[0].dataframe) == 1
    assert not any(m.value.startswith("### ") for m in at.tabs[1].markdown)      # nothing until Submit


def test_project_details_appear_after_submit_and_change_with_the_next_code(monkeypatch):
    first, second = ProjectManager().search(status="Active", limit=2).project_code
    at = projects_page(monkeypatch)
    look_up(at, first)
    assert any(first in m.value for m in at.tabs[1].markdown if m.value.startswith("### "))
    assert len(at.tabs[1].dataframe) == 1                                        # the team
    look_up(at, second)
    assert any(second in m.value for m in at.tabs[1].markdown if m.value.startswith("### "))


def test_project_details_say_when_the_code_is_unknown(monkeypatch):
    at = projects_page(monkeypatch)
    look_up(at, "PRJ99999")
    assert any("PRJ99999 not found" in w.value for w in at.warning)


def test_project_is_edited_and_closed_in_a_pop_up(monkeypatch):
    saved = []
    monkeypatch.setattr(ProjectManager, "update", lambda self, project: saved.append(project) or project)  # nothing written
    at = projects_page(monkeypatch)
    look_up(at, active_project().project_code)
    next(field for field in at.text_input if field.label == "Project name").input("Renamed")
    press(at, "Save changes")
    assert saved[-1].project_name == "Renamed" and any("was updated" in s.value for s in at.success)
    at.button(key="announcement_ok").click().run()
    assert not at.exception, at.exception
    next(box for box in at.button_group if box.label == "What do you want to do?").set_value("Close project").run()
    press(at, "Close project")
    assert saved[-1].status == "Completed" and saved[-1].actual_end_date == date.today()
    assert any("was closed" in s.value for s in at.success)


def test_a_new_project_is_created_with_its_department_in_the_same_box(monkeypatch):
    made = []
    monkeypatch.setattr(ProjectManager, "create", lambda self, project: made.append(project) or
                        project.with_changes(project_id=1, project_code="PRJ00001"))                      # nothing written
    at = projects_page(monkeypatch)
    at.selectbox(key="create_department_0").set_value(3).run()                   # Sales: the managers follow
    assert not at.exception, at.exception
    at.text_input(key="create_name_0").input("New platform")
    press(at, "Create project")
    assert made[0].department_id == 3 and made[0].project_name == "New platform" and made[0].priority == "Medium"
    assert any("PRJ00001" in s.value for s in at.success)
    assert at.text_input(key="create_name_1").value == ""                        # the next form is empty


# ----------------------------------------------------------------------------- Trends and top performers

def test_trends_has_one_chart_and_no_heatmap(monkeypatch):
    at = AppTest.from_file("../views/trends.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 1
    assert len(at.dataframe) == 1 and not at.get("pills")


def test_top_performers_has_a_table_per_department_with_plus_and_minus_boxes():
    at = AppTest.from_file("../views/top_performers.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert not at.get("plotly_chart") and not at.toggle and not at.slider and not at.expander
    assert [box.label for box in at.number_input] == ["Ranks per department", "Minimum reviews"]
    assert [tab.label for tab in at.tabs] == ["Human Resources", "Research & Development", "Sales"]
    assert all(len(tab.dataframe) == 1 for tab in at.tabs)


def test_attrition_has_three_charts_and_the_table():
    at = AppTest.from_file("../views/attrition.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 3 and len(at.dataframe) == 1


def test_bottlenecks_has_two_tabs_with_the_completed_projects_in_their_own():
    at = AppTest.from_file("../views/bottlenecks.py", default_timeout=180).run()
    assert not at.exception, at.exception
    assert [tab.label for tab in at.tabs] == ["Overdue projects", "Completed projects"]
    assert len(at.tabs[1].get("plotly_chart")) == 2


@pytest.mark.parametrize("group", ["priority", "budget_band", "department", "year"])
def test_bottlenecks_groups_completed_projects(group):
    at = AppTest.from_file("../views/bottlenecks.py", default_timeout=180).run()
    at.button_group[0].set_value(group).run()
    assert not at.exception, at.exception
    assert len(at.tabs[1].get("plotly_chart")) == 2


# ----------------------------------------------------------------------------- what the public site hides

def locked_employee_page(monkeypatch, query: str) -> AppTest:
    monkeypatch.setenv("APP_PASSCODE", "test-passcode")
    at = AppTest.from_file("../views/employee_360.py", default_timeout=180)
    at.session_state["e360_search"] = query
    at.run()
    assert not at.exception, at.exception
    return at


def test_contact_details_are_hidden_until_the_passcode_is_entered(monkeypatch):
    employee = long_career()
    at = locked_employee_page(monkeypatch, employee.employee_code)
    assert [tab.label for tab in at.tabs] == TABS
    details = " ".join(m.value for m in at.tabs[0].markdown)
    assert employee.employee_code in details
    assert employee.email not in details and employee.phone not in details and str(employee.date_of_birth) not in details


@pytest.mark.parametrize("field", ["email", "phone"])
def test_the_public_site_cannot_search_by_contact_details(field, monkeypatch):
    at = locked_employee_page(monkeypatch, getattr(long_career(), field))
    assert not at.tabs and not at.dataframe
    assert any("needs the passcode" in i.value for i in at.info)


def test_the_list_of_matches_shows_no_contact_details_until_unlocked(monkeypatch):
    at = locked_employee_page(monkeypatch, "an")
    assert "email" not in at.dataframe[0].value.columns or at.dataframe[0].value.empty
    monkeypatch.delenv("APP_PASSCODE")
    unlocked = employee_page(monkeypatch, "an")
    assert "email" in unlocked.dataframe[0].value.columns


def test_the_passcode_brings_contact_details_back(monkeypatch):
    employee = long_career()
    monkeypatch.setenv("APP_PASSCODE", "test-passcode")
    at = AppTest.from_file("../views/employee_360.py", default_timeout=180)
    at.session_state["e360_search"] = employee.employee_code
    at.session_state["editor"] = True
    at.run()
    assert not at.exception, at.exception
    assert employee.email in " ".join(m.value for m in at.tabs[0].markdown)


def test_fact_cards_escape_what_users_typed(monkeypatch):
    shown = []
    monkeypatch.setattr("src.ui.common.st.markdown", lambda html, **kwargs: shown.append(html))
    fact_cards({"Card": [("Name", "<script>x</script>"), ("Empty", None), ("Zero", 0)]})
    assert "&lt;script&gt;" in shown[0] and "<script>" not in shown[0]
    assert "<dd>—</dd>" in shown[0] and "<dd>0</dd>" in shown[0]

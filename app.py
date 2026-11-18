"""HR Analytics — Streamlit entry point: navigation and analytics warm-up.

Run locally:  streamlit run app.py
Pages live in views/; they use only the Phase 3 managers (src/dal) — no SQL in the UI.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import streamlit as st

from src.dal import AnalyticsManager, ReferenceDataManager
from src.exceptions import HRAppError, PoolBusyError
from src.ui.common import attempt
from src.ui.theme import inject_css

# HOW THE APP STARTS: Streamlit runs this file for every visitor (on the cloud, streamlit_app.py runs it).
# It checks the database once, starts the background warm-up, then shows the page the visitor chose.
st.set_page_config(page_title="HR Analytics Command Centre", page_icon="🛰️", layout="wide")
# add the page styles (colours, cards) from src/ui/theme.py
inject_css()

# how often (in seconds) the background thread checks for new warehouse data
WARM_UP_CHECK_SECONDS = 60


# @st.cache_resource makes this run once per server, not once per visitor.
@st.cache_resource(show_spinner=False)
def start_warm_up() -> threading.Thread:
    """When the app starts, bring back the results an earlier run saved: the first visitor after a
    wake-up waits seconds instead of minutes, and everyone else waits here for those seconds. Then
    keep the pages' default results computed in the background: after an ETL, and when the date
    changes, they are ready before anyone asks (results live in AnalyticsManager's cache)."""
    # load the saved dashboard results from the database
    AnalyticsManager().restore_results()

    # Runs forever in the background: every 60 s it reads the forms' lists and recomputes dashboards that changed
    def keep_warm() -> None:
        while True:
            try:
                ReferenceDataManager().preload()       # the forms' lists: read once, then this does nothing
                AnalyticsManager().warm_up()
            except HRAppError as err:                  # the pages report database problems themselves
                logging.getLogger(__name__).warning("Analytics warm-up paused: %s", err)
            time.sleep(WARM_UP_CHECK_SECONDS)
    # start keep_warm() in its own thread (daemon=True: it stops when the app stops)
    thread = threading.Thread(target=keep_warm, name="analytics-warm-up", daemon=True)
    thread.start()
    return thread


try:
    # One cheap query first: if the database can't be reached, show a clear message instead of errors.
    AnalyticsManager().data_version()                 # one cheap query: is the database reachable?
# the database works but every connection is busy: ask the visitor to wait
except PoolBusyError as err:                          # healthy but busy: the pool is shared, so it stays as it is
    st.warning(f"### The app is busy preparing its data\n{err}")
    st.stop()
# the database can't be reached: show what to check, then stop the page
except HRAppError as err:
    st.error("### The database is not reachable\n"
             f"{err}\n\n"
             "**Check:** MySQL is running · `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` in `.env` (local) or in "
             "the app's Secrets (Streamlit Cloud) are correct · the database was loaded "
             "(`python scripts/setup_database.py`, `python -m src.synthesizer.run`, "
             "`python scripts/run_etl.py --migrate`). Then reload this page.")
    from src.db_manager import DatabaseConnection
    # forget the old connection pool, so the next reload tries again
    DatabaseConnection.reset()                         # the next reload tries again with fresh settings
    st.stop()

if os.getenv("HR_WARM_UP", "on") != "off":            # the tests run without the background thread
    start_warm_up()
# The menu. Each entry is a file in views/: data-entry pages write to hr_oltp, analytics pages read hr_dw.
pages = {
    "Overview": [st.Page("views/overview.py", title="Command centre", icon=":material/rocket_launch:", default=True)],
    "Data entry": [
        st.Page("views/onboard.py", title="Onboard employee", icon=":material/person_add:"),
        st.Page("views/employee_360.py", title="Employee 360 · job change", icon=":material/badge:"),
        st.Page("views/projects.py", title="Projects", icon=":material/rocket:"),
        st.Page("views/reviews.py", title="Performance review", icon=":material/rate_review:"),
    ],
    "Analytics (warehouse)": [
        st.Page("views/trends.py", title="Performance trends", icon=":material/trending_up:"),
        st.Page("views/top_performers.py", title="Top performers", icon=":material/emoji_events:"),
        st.Page("views/attrition.py", title="Attrition risk", icon=":material/warning:"),
        st.Page("views/bottlenecks.py", title="Project bottlenecks", icon=":material/hourglass_top:"),
    ],
    "Operations": [st.Page("views/operations.py", title="ETL & data health", icon=":material/sync:")],
}
# build the sidebar menu from the pages above
navigation = st.navigation(pages)
attempt(navigation.run)                               # a problem a page did not handle: a message, not a crash screen

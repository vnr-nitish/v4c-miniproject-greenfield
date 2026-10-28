"""HR Analytics — Streamlit entry point: navigation, global filters, analytics warm-up.

Run locally:  streamlit run app.py
Pages live in views/; they use only the Phase 3 managers (src/dal) — no SQL in the UI.
"""
from __future__ import annotations

import logging
import threading

import streamlit as st

from src.dal import AnalyticsFilter, AnalyticsManager
from src.exceptions import HRAppError
from src.ui.theme import inject_css

st.set_page_config(page_title="HR Analytics Command Centre", page_icon="🛰️", layout="wide")
inject_css()


@st.cache_resource(show_spinner=False)
def start_warm_up() -> threading.Thread:
    """Compute the slow, company-wide analytics once in the background when the app starts, so the
    first visitor of the risk pages does not wait (results live in AnalyticsManager's cache)."""
    def warm() -> None:
        try:
            am = AnalyticsManager()
            for step in (am.overview, am.yoy_trends, am.scored_workforce, am.attrition_trend, am.risk_backtest,
                         lambda: am.role_trends(metric="overtime_pct"), am.bottleneck_projects):
                step()
        except HRAppError as err:                      # the pages report database problems themselves
            logging.getLogger(__name__).warning("Analytics warm-up stopped: %s", err)
    thread = threading.Thread(target=warm, name="analytics-warm-up", daemon=True)
    thread.start()
    return thread


def sidebar_filters() -> None:
    try:
        options = AnalyticsManager().filter_options()
    except HRAppError as err:
        st.sidebar.error(f"Database not reachable: {err}")
        return
    st.sidebar.markdown("### Filters")
    st.sidebar.caption("Apply to every analytics page.")
    departments = st.sidebar.multiselect("Department", options["departments"])
    levels = st.sidebar.multiselect("Job level", options["job_levels"])
    cycles = st.sidebar.multiselect("Review cycle", options["fiscal_years"],
                                    help="FY2024-25 = reviews of performance year 2024")
    st.session_state["filters"] = AnalyticsFilter(tuple(departments), tuple(levels), tuple(cycles))
    st.session_state["cycles"] = options["fiscal_years"]


start_warm_up()
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
navigation = st.navigation(pages)
sidebar_filters()
navigation.run()

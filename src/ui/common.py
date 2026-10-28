"""Shared page helpers: header, KPI tiles, filters, error display and the editor passcode."""
from __future__ import annotations

import hmac
import os
from typing import Any, Callable

import streamlit as st

from src.dal import AnalyticsFilter
from src.exceptions import BusinessRuleError, HRAppError, NotFoundError, ValidationError
from src.ui.theme import inject_css


def page_header(title: str, subtitle: str) -> None:
    inject_css()
    st.markdown(f"<div class='hero'><h1>{title}</h1><p>{subtitle}</p></div>", unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f"<div class='note'>{text}</div>", unsafe_allow_html=True)


def tiles(items: list[tuple[str, str, float | None, bool]]) -> None:
    """KPI tiles: (label, value, change, higher_is_good)."""
    html = []
    for label, value, change, good_up in items:
        delta = ""
        if change is not None and change == change:            # not NaN
            css = "delta-flat" if abs(change) < 1e-9 else (
                "delta-up" if (change > 0) == good_up else "delta-down")
            arrow = "▲" if change > 0 else ("▼" if change < 0 else "■")
            delta = f"<div class='{css}'>{arrow} {change:+,.3g} vs previous year</div>"
        html.append(f"<div class='tile'><div class='tile-inner'><div class='label'>{label}</div>"
                    f"<div class='value'>{value}</div>{delta}</div></div>")
    st.markdown(f"<div class='tiles'>{''.join(html)}</div>", unsafe_allow_html=True)


def current_filters() -> AnalyticsFilter:
    return st.session_state.get("filters", AnalyticsFilter())


def attempt(action: Callable[[], Any]) -> Any:
    """Run a manager call and show its failure as a message the user can act on."""
    try:
        return action()
    except ValidationError as err:
        st.warning("Please correct the following:\n" + "\n".join(
            f"- **{field.replace('_', ' ').capitalize() or 'Input'}**: {message}" for field, message in err.errors.items()))
    except (BusinessRuleError, NotFoundError) as err:
        st.warning(str(err))
    except HRAppError as err:
        st.error(f"The database could not complete this: {err}")
    return None


def _configured_passcode() -> str | None:
    code = os.environ.get("APP_PASSCODE")
    if code:
        return code
    try:
        return st.secrets.get("APP_PASSCODE")
    except Exception:                       # no secrets file locally
        return None


def editor_unlocked() -> bool:
    """Data entry is protected by a passcode when APP_PASSCODE is configured (the public site)."""
    code = _configured_passcode()
    if not code:
        st.caption("🔓 Data entry is open — no APP_PASSCODE is configured (local development).")
        return True
    if st.session_state.get("editor"):
        return True
    st.info("Data entry is protected on the public site; the dashboards are open to everyone.")
    with st.form("unlock"):
        entered = st.text_input("Editor passcode", type="password")
        if st.form_submit_button("Unlock data entry"):
            if hmac.compare_digest(entered.encode(), code.encode()):
                st.session_state.editor = True
                st.rerun()
            st.error("Wrong passcode.")
    return False

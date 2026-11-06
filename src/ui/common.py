"""Shared page helpers: header, KPI tiles, fact cards, page filters, pop-ups and error display."""
from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from src.dal import AnalyticsManager
from src.exceptions import BusinessRuleError, HRAppError, NotFoundError, PoolBusyError, ValidationError
from src.ui.forms import _manager_candidates
from src.ui.theme import TILE_COLORS, inject_css


def page_header(title: str) -> None:
    inject_css()
    st.markdown(f"<div class='hero'><h1>{title}</h1></div>", unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f"<div class='note'>{text}</div>", unsafe_allow_html=True)


def tiles(items: list[tuple[str, str, float | None, bool]]) -> None:
    """KPI tiles: (label, value, change, higher_is_good)."""
    html = []
    for n, (label, value, change, good_up) in enumerate(items):
        delta = ""
        if change is not None and change == change:            # not NaN
            css = "delta-flat" if abs(change) < 1e-9 else (
                "delta-up" if (change > 0) == good_up else "delta-down")
            arrow = "▲" if change > 0 else ("▼" if change < 0 else "■")
            delta = f"<div class='{css}'>{arrow} {change:+,.3g} vs previous year</div>"
        color = TILE_COLORS[n % len(TILE_COLORS)]
        html.append(f"<div class='tile' style='--c:{color}'><div class='label'>{label}</div>"
                    f"<div class='value'>{value}</div>{delta}</div>")
    st.markdown(f"<div class='tiles'>{''.join(html)}</div>", unsafe_allow_html=True)


def fact_cards(cards: dict[str, list[tuple[str, Any]]]) -> None:
    """Groups of label and value, such as an employee's details. Values can come from what users
    typed, so they are escaped; an empty value shows as a dash."""
    html = []
    for title, facts in cards.items():
        rows = "".join(f"<dt>{escape(label)}</dt><dd>{escape('—' if value in (None, '') else str(value))}</dd>"
                       for label, value in facts)
        html.append(f"<div class='fact-card'><h4>{escape(title)}</h4><dl>{rows}</dl></div>")
    st.markdown(f"<div class='fact-cards'>{''.join(html)}</div>", unsafe_allow_html=True)


def announce(title: str, message: str, tables: dict[str, Any] | None = None, caption: str | None = None) -> None:
    """A change was saved: redraw the page with the new data and show this in a pop-up on top."""
    st.session_state["announcement"] = (title, message, tables or {}, caption)
    st.rerun()


def show_announcement() -> None:
    """The pop-up announce() asked for, if any. Only its OK button closes it."""
    if "announcement" not in st.session_state:
        return
    title, message, tables, caption = st.session_state.pop("announcement")

    @st.dialog(title, width="large", dismissible=False)
    def popup() -> None:
        st.success(message)
        for heading, table in tables.items():
            st.markdown(heading)
            st.dataframe(table, hide_index=True, width="stretch")
        if caption:
            st.caption(caption)
        if st.button("OK", type="primary", key="announcement_ok"):
            st.rerun()
    popup()


def data_changed() -> None:
    """This page just changed the warehouse: dashboards re-check its version on the next visit,
    and the manager drop-downs read the people again."""
    AnalyticsManager().refresh_version()
    _manager_candidates.clear()


def department_filter(container: Any) -> tuple[str, ...]:
    """A page's Department drop-down, as AnalyticsFilter wants it: empty means every department."""
    chosen = container.selectbox("Department", ["All", *AnalyticsManager().filter_options()["departments"]])
    return () if chosen == "All" else (chosen,)


def job_level_filter(container: Any) -> tuple[int, ...]:
    """A page's Job level drop-down, as AnalyticsFilter wants it: empty means every level."""
    chosen = container.selectbox("Job level", ["All", *AnalyticsManager().filter_options()["job_levels"]],
                                 format_func=lambda level: level if level == "All" else f"Level {level}")
    return () if chosen == "All" else (chosen,)


def attempt(action: Callable[[], Any]) -> Any:
    """Run a manager call and show its failure as a message the user can act on."""
    try:
        return action()
    except ValidationError as err:
        st.warning("Please correct the following:\n" + "\n".join(
            f"- **{field.replace('_', ' ').capitalize() or 'Input'}**: {message}" for field, message in err.errors.items()))
    except (BusinessRuleError, NotFoundError, PoolBusyError) as err:
        st.warning(str(err))
    except HRAppError as err:
        st.error(f"The database could not complete this: {err}")
    return None

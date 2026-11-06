"""Attrition risk: where the risk is, what drives it, the evidence for the score, and who is at risk."""
import pandas as pd
import streamlit as st

from src.dal import AnalyticsManager
from src.dal.analytics_manager import MAX_RISK_SCORE
from src.ui import charts, theme
from src.ui.common import attempt, current_filters, page_header

page_header("Attrition risk")
am, filters = AnalyticsManager(), current_filters()

with st.spinner("Reading the warehouse…"):
    workforce = attempt(am.scored_workforce)
    backtest = attempt(am.risk_backtest)
if workforce is None:
    st.stop()

left, middle, right = st.columns(3)
left.plotly_chart(charts.bars(am.risk_summary(filters), "department_name", "employees", "risk_band",
                              title="Employees per risk band", colors=theme.BAND_COLORS), width="stretch")
prevalence = am.risk_factor_prevalence(filters)
middle.plotly_chart(charts.bars(prevalence.iloc[::-1], "factor", "share_pct", horizontal=True,
                                title="Factors among High / Very high risk (%)"), width="stretch")
if backtest is not None:
    right.plotly_chart(charts.bars(backtest, "risk_band", "left_within_year_pct", "risk_band",
                                   title="Evidence: % who left within a year", colors=theme.BAND_COLORS,
                                   text="left_within_year_pct"), width="stretch")


@st.dialog("Why this employee is at risk", width="medium", on_dismiss="rerun")
def explain(row: pd.Series) -> None:
    st.markdown(f"### {row.full_name} · {row.employee_code}")
    details, reasons = st.columns(2)
    details.markdown(f"**{row.risk_band} risk** · score {row.score} of {MAX_RISK_SCORE}")
    details.write(f"{row.department_name} · {row.job_role} · level {row.job_level} · {row.tenure_years} years")
    details.caption(f"Riskier than {row.risk_percentile} % of staff")
    reasons.markdown("**Risk factors**")
    reasons.markdown("\n".join(f"- {factor}" for factor in (row.risk_factors or "").split(", ") if factor))


@st.fragment
def at_risk_table() -> None:
    """A fragment: choosing a band or clicking View redraws this table only, not the charts above it."""
    band = st.segmented_control("Minimum band", ["Very high", "High", "Medium", "Low"], default="High", required=True)
    at_risk = am.attrition_risk(filters, limit=500, min_band=band)
    shown = at_risk[["employee_code", "full_name", "department_name", "job_role", "job_level", "tenure_years",
                     "score", "risk_band", "risk_percentile", "risk_factors"]].copy()
    shown.insert(0, "view", "View")
    # A new key after each View leaves no cell selected, so the same row can be opened again
    round_number = st.session_state.setdefault("at_risk_round", 0)
    event = st.dataframe(shown.style.map(lambda _: "color: #8B7CFF; font-weight: 600", subset=["view"]),
                         hide_index=True, width="stretch", on_select="rerun", selection_mode="single-cell",
                         key=f"at_risk_{band}_{round_number}",
                         column_config={"view": st.column_config.TextColumn("Details", width="small"),
                                        "employee_code": "Employee ID", "full_name": "Name", "department_name": "Department",
                                        "job_role": "Job role", "job_level": "Level", "tenure_years": "Years",
                                        "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=MAX_RISK_SCORE,
                                                                                 format="%d"),
                                        "risk_band": "Band", "risk_factors": "Risk factors",
                                        "risk_percentile": st.column_config.NumberColumn("Percentile", format="%.1f")})
    views = [row for row, column in event.selection.cells if column == "view"]
    if views:
        st.session_state["at_risk_round"] = round_number + 1
        explain(at_risk.iloc[views[0]])


st.subheader("Who is at risk")
at_risk_table()

"""Attrition risk: 3D risk terrain, bands, factors, the at-risk list, evidence and the yearly trend."""
import pandas as pd
import streamlit as st

from src.dal import AnalyticsFilter, AnalyticsManager
from src.ui import charts, theme
from src.ui.common import attempt, current_filters, note, page_header

page_header("Attrition risk", "An explainable, back-tested points score from work-related factors HR can act on")
am, filters = AnalyticsManager(), current_filters()

with st.spinner("Scoring the workforce (first visit after a data change takes ~20 s)…"):
    workforce = attempt(am.scored_workforce)
if workforce is None:
    st.stop()
scope = filters.select(workforce)

st.subheader("Risk terrain · job level × tenure")
buckets = pd.cut(scope.tenure_years, [-1, 0, 1, 2, 4, 9, 60], labels=["<1", "1", "2", "3-4", "5-9", "10+"])
cells = scope.assign(tenure=buckets, high=scope.band_order >= 3).groupby(["job_level", "tenure"], observed=False)
grid = (100 * cells.high.mean()).unstack().where(cells.size().unstack() >= 30)       # hide thin cells
st.plotly_chart(charts.surface(grid.rename(index=lambda l: f"Level {l}"), "Tenure (years)", "Job level",
                               "% High / Very high risk", "Inferno"), width="stretch")
note("Peaks = groups where many people are at high risk. Cells with fewer than 30 employees are left out.")

left, right = st.columns(2)
summary = am.risk_summary(filters)
left.plotly_chart(charts.bars(summary, "department_name", "employees", "risk_band", title="Employees per risk band",
                              colors=theme.BAND_COLORS), width="stretch")
prevalence = am.risk_factor_prevalence(filters)
right.plotly_chart(charts.bars(prevalence.iloc[::-1], "factor", "share_pct", horizontal=True,
                               title="Factors among High / Very high risk (%)"), width="stretch")

st.subheader("Who is at risk")
band = st.segmented_control("Minimum band", ["Very high", "High", "Medium", "Low"], default="High") or "High"
at_risk = am.attrition_risk(filters, limit=500, min_band=band)
event = st.dataframe(at_risk[["employee_code", "full_name", "department_name", "job_role", "job_level", "tenure_years",
                              "score", "risk_band", "risk_percentile", "risk_factors"]],
                     hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                     column_config={"score": st.column_config.ProgressColumn("Score", min_value=0, max_value=14,
                                                                             format="%d")})


@st.dialog("Why this employee is at risk")
def explain(row: pd.Series) -> None:
    st.markdown(f"### {row.full_name} · {row.employee_code}")
    st.write(f"{row.department_name} · {row.job_role} · level {row.job_level} · {row.tenure_years} years")
    st.metric("Risk score", f"{row.score} / 14", help=f"{row.risk_band} — riskier than {row.risk_percentile} % of staff")
    for factor in (row.risk_factors or "").split(", "):
        if factor:
            st.markdown(f"- {factor}")


if event.selection.rows:
    explain(at_risk.iloc[event.selection.rows[0]])

st.subheader("Evidence and trend")
left, right = st.columns(2)
backtest = attempt(am.risk_backtest)
if backtest is not None:
    left.plotly_chart(charts.bars(backtest, "risk_band", "left_within_year_pct",
                                  title="Back-test: % who left within a year, by band"), width="stretch")
trend = attempt(lambda: am.attrition_trend(AnalyticsFilter(filters.departments, filters.job_levels)))
if trend is not None:
    right.plotly_chart(charts.lines(trend, "fiscal_year", "attrition_pct",
                                    title="Attrition per fiscal year (%) · point-in-time headcount"), width="stretch")
note("Attrition falls over the years because headcount grows while leavers stay at about 2,150 a year "
     "(deferred item D3). The latest fiscal year is still running.")

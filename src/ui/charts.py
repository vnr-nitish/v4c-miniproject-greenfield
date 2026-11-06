"""Chart builders (Plotly). Plain 2D charts, each answering one question; colours carry meaning:
departments always have the same colour, risk bands go from green to red."""
from __future__ import annotations

import textwrap

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.ui import theme


# Plain words in place of column names, wherever a chart names a value
LABELS = {"priority": "Priority", "budget_band": "Budget band", "department": "Department", "year": "Year",
          "department_name": "Department", "delayed_pct": "Finished late (%)", "average_days": "Average delay (days)",
          "employees": "Employees", "risk_band": "Risk band", "years": "Years at the company", "factor": "Factor",
          "share_pct": "Share (%)", "left_within_year_pct": "Left within a year (%)", "period": "Review cycle",
          "fiscal_year": "Fiscal year", "attrition_pct": "Attrition (%)", "avg_kpi": "Average KPI score",
          "avg_rating": "Average rating", "outstanding_pct": "Outstanding reviews (%)", "overtime_pct": "Overtime (%)",
          "avg_job_satisfaction": "Job satisfaction", "avg_training_sessions": "Training sessions",
          "days_overdue": "Days overdue", "team_overtime_pct": "Team overtime (%)", "project_code": "Code",
          "owning_department_name": "Department", "status": "Status", "budget": "Budget", "team_avg_kpi": "Team avg KPI"}


def _finish(fig: go.Figure, height: int = 380, title: str = "") -> go.Figure:
    fig.update_layout(height=height, title=dict(text=title, x=0.01, font=dict(size=15)))
    return fig


def headcount_treemap(workforce: pd.DataFrame) -> go.Figure:
    """Department -> job role, sized by headcount, coloured by the share of High / Very high risk."""
    data = (workforce.assign(at_risk=workforce.band_order >= 3)
            .groupby(["department_name", "job_role"], as_index=False)
            .agg(employees=("employee_id", "size"), at_risk_pct=("at_risk", "mean")))
    data["at_risk_pct"] = (100 * data.at_risk_pct).round(1)
    fig = px.treemap(data, path=[px.Constant("Company"), "department_name", "job_role"], values="employees",
                     color="at_risk_pct", color_continuous_scale=theme.RISK_SCALE,
                     hover_data={"at_risk_pct": ":.1f"})
    fig.update_traces(texttemplate="<b>%{label}</b><br>%{value:,} people", marker=dict(cornerradius=6))
    fig.update_layout(coloraxis_colorbar=dict(title=dict(text="% at risk")))
    return _finish(fig, 430, "Headcount by department and role · colour = share at high risk")


def donut(frame: pd.DataFrame, names: str, values: str, colors: dict, title: str) -> go.Figure:
    fig = px.pie(frame, names=names, values=values, hole=.62, color=names, color_discrete_map=colors)
    fig.update_traces(textinfo="percent", sort=False)
    return _finish(fig, 430, title)                 # as tall as the treemap beside it


def lines(frame: pd.DataFrame, x: str, y: str, color: str | None = None, title: str = "") -> go.Figure:
    fig = px.line(frame, x=x, y=y, color=color, markers=True, color_discrete_map=theme.DEPARTMENT_COLORS,
                  line_shape="spline", labels=LABELS)
    fig.update_traces(line=dict(width=3), marker=dict(size=8))
    fig.update_layout(hovermode="x unified", legend_title_text="", xaxis_title=None, yaxis_title=None)
    return _finish(fig, 380, title)


def bars(frame: pd.DataFrame, x: str, y: str, color: str | None = None, horizontal: bool = False,
         title: str = "", colors: dict | None = None, text: str | None = None) -> go.Figure:
    kwargs = dict(x=y, y=x, orientation="h") if horizontal else dict(x=x, y=y)
    fig = px.bar(frame, **kwargs, color=color, text=text, labels=LABELS,
                 color_discrete_map=colors or theme.DEPARTMENT_COLORS,
                 color_discrete_sequence=theme.PALETTE)
    fig.update_traces(marker=dict(cornerradius=6), textposition="outside" if text else None)
    fig.update_layout(legend_title_text="", xaxis_title=None, yaxis_title=None, bargap=.25)
    if color == x:                                  # the axis already names every bar
        fig.update_layout(showlegend=False)
    if not horizontal and not pd.api.types.is_numeric_dtype(frame[x]):
        names = list(dict.fromkeys(frame[x]))       # long names on two lines instead of slanted
        fig.update_xaxes(tickmode="array", tickvals=names, tickangle=0,
                         ticktext=[textwrap.fill(str(name), 14).replace("\n", "<br>") for name in names])
    return _finish(fig, 380, title)


def bubbles(projects: pd.DataFrame) -> go.Figure:
    """Overdue projects: how late x team overtime; bubble = budget; colour = priority."""
    fig = px.scatter(projects, x="days_overdue", y="team_overtime_pct", size="budget", color="priority",
                     size_max=42, color_discrete_map=theme.PRIORITY_COLORS,
                     hover_name="project_name",
                     hover_data={"project_code": True, "owning_department_name": True, "status": True,
                                 "budget": ":,.0f", "team_avg_kpi": ":.1f"},
                     labels=LABELS)
    fig.update_traces(marker=dict(line=dict(width=1, color="rgba(255,255,255,.6)"), opacity=.85))
    return _finish(fig, 440, "Overdue projects · bubble size = budget")

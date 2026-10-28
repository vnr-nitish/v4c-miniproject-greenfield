"""Chart builders (Plotly). Plain 2D charts, each answering one question; colours carry meaning:
departments always have the same colour, risk bands go from green to red."""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.ui import theme


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
    return _finish(fig, 340, title)


def heatmap(grid: pd.DataFrame, title: str, scale=None, fmt: str = ".1f") -> go.Figure:
    fig = px.imshow(grid, text_auto=fmt, aspect="auto", color_continuous_scale=scale or theme.SEQ_SCALE)
    fig.update_xaxes(side="top", title=None)
    fig.update_yaxes(title=None)
    return _finish(fig, 60 + 46 * len(grid), title)


def lines(frame: pd.DataFrame, x: str, y: str, color: str | None = None, title: str = "") -> go.Figure:
    fig = px.line(frame, x=x, y=y, color=color, markers=True, color_discrete_map=theme.DEPARTMENT_COLORS,
                  line_shape="spline")
    fig.update_traces(line=dict(width=3), marker=dict(size=8))
    fig.update_layout(hovermode="x unified", legend_title_text="")
    return _finish(fig, 380, title)


def bars(frame: pd.DataFrame, x: str, y: str, color: str | None = None, horizontal: bool = False,
         title: str = "", colors: dict | None = None, text: str | None = None) -> go.Figure:
    kwargs = dict(x=frame[y], y=frame[x], orientation="h") if horizontal else dict(x=frame[x], y=frame[y])
    fig = px.bar(frame, **kwargs, color=frame[color] if color else None, text=frame[text] if text else None,
                 color_discrete_map=colors or theme.DEPARTMENT_COLORS,
                 color_discrete_sequence=theme.PALETTE)
    fig.update_traces(marker=dict(cornerradius=6), textposition="outside" if text else None)
    fig.update_layout(legend_title_text="", xaxis_title=None, yaxis_title=None, bargap=.25)
    return _finish(fig, 380, title)


def leaderboard(top: pd.DataFrame) -> go.Figure:
    """Best people per department: one bar each, labelled with their rank."""
    data = top.sort_values(["department_name", "department_rank", "avg_kpi"], ascending=[True, False, True])
    data = data.assign(label="#" + data.department_rank.astype(str) + "  " + data.full_name)
    fig = px.bar(data, x="avg_kpi", y="label", color="department_name", orientation="h", facet_col="department_name",
                 facet_col_spacing=.08, color_discrete_map=theme.DEPARTMENT_COLORS,
                 hover_data={"avg_rating": ":.2f", "reviews": True, "label": False})
    fig.update_yaxes(matches=None, showticklabels=True, title=None)
    fig.update_xaxes(title=None, range=[max(0, data.avg_kpi.min() - 5), 100])
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    fig.update_traces(marker=dict(cornerradius=5))
    fig.update_layout(showlegend=False)
    return _finish(fig, 120 + 26 * data.groupby("department_name").size().max(), "Average KPI of the top-ranked people")


def bubbles(projects: pd.DataFrame) -> go.Figure:
    """Overdue projects: how late x team overtime; bubble = budget; colour = priority."""
    fig = px.scatter(projects, x="days_overdue", y="team_overtime_pct", size="budget", color="priority",
                     size_max=42, color_discrete_map=theme.PRIORITY_COLORS,
                     hover_name="project_name",
                     hover_data={"project_code": True, "owning_department_name": True, "status": True,
                                 "budget": ":,.0f", "team_avg_kpi": ":.1f"},
                     labels={"days_overdue": "Days overdue", "team_overtime_pct": "Team overtime %"})
    fig.update_traces(marker=dict(line=dict(width=1, color="rgba(255,255,255,.6)"), opacity=.85))
    return _finish(fig, 440, "Overdue projects · bubble size = budget")


def career_timeline(history: pd.DataFrame) -> go.Figure:
    """An employee's SCD Type 2 versions as bars on a timeline, coloured by department."""
    data = history.assign(end=history.end_date.map(lambda d: min(d, date.today())),
                          label="v" + history.version_number.astype(str) + " · " + history.job_role
                          + " (L" + history.job_level.astype(str) + ")")
    fig = px.timeline(data, x_start="start_date", x_end="end", y="label", color="department_name",
                      color_discrete_map=theme.DEPARTMENT_COLORS,
                      hover_data={"change_reason": True, "monthly_income": ":,.0f", "is_current": True, "end": False})
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_traces(marker=dict(cornerradius=6))
    fig.update_layout(legend_title_text="Department")
    return _finish(fig, 120 + 48 * len(data), "Career timeline · one bar per SCD Type 2 version")

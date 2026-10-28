"""Chart builders. The 3D views use Plotly's WebGL scenes (rotate, zoom, hover in the browser)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src.ui import theme

SCENE_CAMERA = dict(eye=dict(x=1.6, y=-1.6, z=0.9))


def _scene(x: str, y: str, z: str, **axes) -> dict:
    scene = dict(xaxis=dict(title=dict(text=x)), yaxis=dict(title=dict(text=y)), zaxis=dict(title=dict(text=z)),
                 camera=SCENE_CAMERA, aspectmode="manual", aspectratio=dict(x=1.3, y=1.1, z=0.75))
    for key, value in axes.items():
        scene[key].update(value)
    return scene


def workforce_galaxy(sample: pd.DataFrame) -> go.Figure:
    """Each employee a star: tenure x monthly pay x risk score, coloured by risk band."""
    rng = np.random.default_rng(7)
    fig = go.Figure()
    for band, color in theme.BAND_COLORS.items():
        part = sample[sample.risk_band == band]
        fig.add_trace(go.Scatter3d(
            x=part.tenure_years + rng.uniform(-.35, .35, len(part)), y=part.monthly_income,
            z=part.score + rng.uniform(-.3, .3, len(part)), mode="markers", name=band,
            marker=dict(size=2.6, color=color, opacity=.75),
            customdata=np.stack([part.full_name, part.employee_code, part.department_name, part.job_role,
                                 part.risk_factors.fillna("")], axis=-1),
            hovertemplate="<b>%{customdata[0]}</b> (%{customdata[1]})<br>%{customdata[2]} · %{customdata[3]}"
                          "<br>tenure %{x:.0f} y · pay %{y:,.0f} · score %{z:.0f}<br>%{customdata[4]}<extra></extra>"))
    fig.update_layout(height=560, scene=_scene("Tenure (years)", "Monthly income", "Risk score"),
                      legend=dict(title=dict(text="Risk band"), itemsizing="constant"))
    return fig


def surface(grid: pd.DataFrame, x_label: str, y_label: str, z_label: str, colorscale: str = "Plasma") -> go.Figure:
    """A 3D surface over a pivot table (rows -> y, columns -> x)."""
    fig = go.Figure(go.Surface(
        z=grid.to_numpy(dtype=float), x=list(range(grid.shape[1])), y=list(range(grid.shape[0])),
        colorscale=colorscale, opacity=.95, contours=dict(z=dict(show=True, usecolormap=True, project_z=True)),
        customdata=np.array([[f"{r} · {c}" for c in grid.columns] for r in grid.index]),
        hovertemplate="%{customdata}<br>" + z_label + ": %{z:.2f}<extra></extra>",
        colorbar=dict(title=dict(text=z_label), thickness=12)))
    fig.update_layout(height=560, scene=_scene(
        x_label, y_label, z_label,
        xaxis=dict(tickvals=list(range(grid.shape[1])), ticktext=[str(c) for c in grid.columns]),
        yaxis=dict(tickvals=list(range(grid.shape[0])), ticktext=[str(r) for r in grid.index])))
    return fig


def _cuboid(x0: float, y0: float, width: float, depth: float, height: float, color: str, name: str,
            hover: str) -> go.Mesh3d:
    x1, y1 = x0 + width, y0 + depth
    return go.Mesh3d(x=[x0, x0, x1, x1, x0, x0, x1, x1], y=[y0, y1, y1, y0, y0, y1, y1, y0],
                     z=[0, 0, 0, 0, height, height, height, height],
                     i=[7, 0, 0, 0, 4, 4, 6, 6, 4, 0, 3, 2], j=[3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 3],
                     k=[0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6], color=color, opacity=.92, flatshading=True,
                     name=name, hovertext=hover, hoverinfo="text", showscale=False)


def podium(top: pd.DataFrame) -> go.Figure:
    """A 3D podium per department: rank 1 in the middle (tallest), rank 2 left, rank 3 right."""
    heights, offsets = {1: 3.0, 2: 2.1, 3: 1.4}, {1: 1.0, 2: 0.0, 3: 2.0}
    fig = go.Figure()
    labels_x, labels_y, labels_z, labels = [], [], [], []
    for d, (department, rows) in enumerate(top.groupby("department_name", sort=True)):
        for rank in (1, 2, 3):
            people = rows[rows.department_rank == rank]
            if people.empty:
                continue
            first = people.iloc[0]
            tied = f" (+{len(people) - 1} tied)" if len(people) > 1 else ""
            hover = (f"<b>{department}</b> · rank {rank}<br>{first.full_name}{tied}<br>"
                     f"rating {first.avg_rating:.2f} · KPI {first.avg_kpi:.2f} · {first.reviews} reviews")
            x0 = d * 4.2 + offsets[rank]
            fig.add_trace(_cuboid(x0, 0, 0.95, 1.2, heights[rank], theme.PALETTE[d % len(theme.PALETTE)],
                                  f"{department} #{rank}", hover))
            labels_x.append(x0 + .47), labels_y.append(.6), labels_z.append(heights[rank] + .35)
            labels.append(f"#{rank} {first.full_name.split()[0]}{'*' if tied else ''}")
        fig.add_trace(go.Scatter3d(x=[d * 4.2 + 1.47], y=[-1.0], z=[0], mode="text", text=[department],
                                   textfont=dict(color=theme.MUTED, size=12), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter3d(x=labels_x, y=labels_y, z=labels_z, mode="text", text=labels,
                               textfont=dict(color=theme.TEXT, size=12), hoverinfo="skip", showlegend=False))
    hidden = dict(showticklabels=False, title=dict(text=""), showgrid=False, showbackground=False, zeroline=False)
    fig.update_layout(height=520, showlegend=False,
                      scene=dict(xaxis=hidden, yaxis=hidden, zaxis=hidden, aspectmode="data",
                                 camera=dict(eye=dict(x=0.2, y=-2.2, z=1.1))))
    return fig


def pressure_cube(projects: pd.DataFrame) -> go.Figure:
    """Overdue projects: days overdue x team overtime x budget; size = reviewed team members."""
    colors = {"Critical": "#F43F5E", "High": "#FBBF24", "Medium": "#60A5FA", "Low": "#2DD4BF"}
    fig = go.Figure()
    for priority, color in colors.items():
        part = projects[projects.priority == priority]
        fig.add_trace(go.Scatter3d(
            x=part.days_overdue, y=part.team_overtime_pct.fillna(0), z=part.budget / 1e6, mode="markers", name=priority,
            marker=dict(size=np.clip(np.sqrt(part.team_reviews.fillna(0)) * 1.3, 4, 26), color=color, opacity=.85,
                        line=dict(width=0)),
            customdata=np.stack([part.project_code, part.project_name, part.owning_department_name,
                                 part.status, part.team_avg_kpi.fillna(0)], axis=-1),
            hovertemplate="<b>%{customdata[0]}</b> %{customdata[1]}<br>%{customdata[2]} · %{customdata[3]}"
                          "<br>%{x} days overdue · team overtime %{y:.1f} % · budget %{z:.2f} M"
                          "<br>team KPI %{customdata[4]:.1f}<extra></extra>"))
    fig.update_layout(height=560, legend=dict(title=dict(text="Priority")),
                      scene=_scene("Days overdue", "Team overtime %", "Budget (M)"))
    return fig


def career_path(history: pd.DataFrame, highlight_last: bool = False) -> go.Figure:
    """An employee's SCD Type 2 versions: time x department x job level."""
    departments = sorted(history.department_name.unique())
    def year(d: date) -> float:
        return d.year + (d.timetuple().tm_yday - 1) / 365.25
    start = history.start_date.map(year)
    end = history.end_date.map(lambda d: year(min(d, date.today())))
    dept = history.department_name.map(departments.index)
    fig = go.Figure()
    for i, row in enumerate(history.itertuples()):
        last = highlight_last and i == len(history) - 1
        color = "#2DD4BF" if row.is_current else ("#F43F5E" if highlight_last and i == len(history) - 2 else theme.ACCENT)
        fig.add_trace(go.Scatter3d(
            x=[start.iloc[i], end.iloc[i]], y=[dept.iloc[i]] * 2, z=[row.job_level] * 2, mode="lines+markers",
            line=dict(width=12 if last else 8, color=color), marker=dict(size=5 if not last else 8, color=color),
            name=f"v{row.version_number} {row.change_reason}",
            hovertext=f"v{row.version_number} · {row.change_reason}<br>{row.department_name} · {row.job_role} "
                      f"(level {row.job_level})<br>{row.start_date} → {row.end_date}<br>pay {row.monthly_income:,.0f}",
            hoverinfo="text"))
        if i:
            fig.add_trace(go.Scatter3d(x=[start.iloc[i]] * 2, y=[dept.iloc[i - 1], dept.iloc[i]],
                                       z=[history.job_level.iloc[i - 1], row.job_level], mode="lines",
                                       line=dict(width=3, color=theme.MUTED, dash="dash"), hoverinfo="skip",
                                       showlegend=False))
    fig.update_layout(height=480, legend=dict(title=dict(text="SCD Type 2 versions")),
                      scene=_scene("Year", "Department", "Job level",
                                   yaxis=dict(tickvals=list(range(len(departments))), ticktext=departments),
                                   zaxis=dict(range=[0.5, 5.5], dtick=1)))
    return fig


def lines(frame: pd.DataFrame, x: str, y: str, color: str | None = None, title: str = "") -> go.Figure:
    fig = go.Figure()
    groups = frame.groupby(color, sort=True) if color else [(y, frame)]
    for name, part in groups:
        fig.add_trace(go.Scatter(x=part[x], y=part[y], mode="lines+markers", name=str(name),
                                 line=dict(width=3, shape="spline"), marker=dict(size=7)))
    fig.update_layout(height=360, title=dict(text=title), hovermode="x unified")
    return fig


def bars(frame: pd.DataFrame, x: str, y: str, color: str | None = None, horizontal: bool = False,
         title: str = "", colors: dict | None = None) -> go.Figure:
    fig = go.Figure()
    groups = frame.groupby(color, sort=False) if color else [(y, frame)]
    for name, part in groups:
        kwargs = dict(x=part[y], y=part[x], orientation="h") if horizontal else dict(x=part[x], y=part[y])
        fig.add_trace(go.Bar(name=str(name), marker=dict(color=(colors or {}).get(name)), **kwargs))
    fig.update_layout(height=360, title=dict(text=title), barmode="stack" if color else "group")
    return fig

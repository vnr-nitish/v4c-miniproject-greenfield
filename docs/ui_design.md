# UI Design — Streamlit Application

**Project:** Enterprise Employee Analytics & Data Warehouse System
**Phase:** 4 — Streamlit UI and interactive dashboards
**Status:** Implemented; smoke-tested (`tests/test_app.py`)
**Run:** `streamlit run app.py`

---

## 1. Requirements → pages

| Phase 4 requirement | Page (`views/`) |
|---|---|
| Forms: onboard employees | `onboard.py` — position first (department → only its roles → only the role's levels → pay slider limited to the level's band → manager), then the person; field-level messages from the entity rules |
| Forms: create projects | `projects.py` — create, assign people (over-allocation check with "assign anyway"), browse, close |
| Forms: submit reviews | `reviews.py` — reviewer and project pre-filled from the employee's history |
| Department change → SCD Type 2 | `employee_360.py` — *Change position* calls `EmployeeManager.change_position()`; the page shows the **expired** and the **new current** `dim_employee` row side by side and the career timeline with the new version |
| Connect directly to the warehouse | every analytics page reads `hr_dw` only, through `AnalyticsManager` |
| Year-over-year trends | `trends.py` — `LAG()` changes per cycle, department comparison, heatmap (job role × cycle) |
| Top performers by department (`DENSE_RANK`) | `top_performers.py` — medal cards per department, leaderboard chart and table with tie counts, the SQL shown on the page |
| Attrition risk / project bottlenecks | both: `attrition.py` (risk heatmap job level × tenure, bands, factors, at-risk list with explanation dialog, back-test, yearly attrition) and `bottlenecks.py` (bubble chart of overdue projects, delivery delays) |
| Replace the command-line interface | `operations.py` — run the warehouse ETL from the browser, batch log and rejected rows |

## 2. Architecture

```
app.py            navigation (sections), global filters (sidebar), background analytics warm-up
views/*.py        one file per page — widgets and charts only, no SQL
src/ui/theme.py   CSS (cards, colour-coded tiles, gradient header), meaning-carrying colours, Plotly template
src/ui/charts.py  chart builders (plotly.express): treemap, donut, heatmaps, lines, bars, leaderboard, bubbles, timeline
src/ui/forms.py   the dependent position picker shared by onboarding and job changes
src/ui/common.py  header, tiles, attempt() (exceptions -> messages), filters, editor passcode
src/dal/…         the Phase 3 managers — the only way the UI reaches MySQL
```

## 3. Decisions (approved)

| ID | Decision | Why |
|---|---|---|
| U1 | Dark "command centre" theme (`.streamlit/config.toml` + CSS), one Plotly template for every chart | a distinctive, consistent product rather than the default look |
| U2 | ~~Plotly 3D views~~ → **2D Plotly charts** (decision U8) | meets "Plotly/Altair", native to Streamlit, deploys unchanged |
| U3 | Data entry and ETL behind a passcode when `APP_PASSCODE` is set (Streamlit secrets); dashboards public | on a public URL, nobody can write without the passcode; locally (no passcode) everything is open |
| U4 | One clear chart per question, plus the exact table where it matters | charts to see the pattern, tables to read values |
| U5 | Colours carry meaning: each department has its own colour, risk bands green → red, one sequential scale for heatmaps, a different accent per tile | replaces the first single-accent design |
| U6 | Flat rating/KPI trends shown honestly, with a note; the surface highlights where data moves | no invented trends (synthesizer D26) |
| U7 | Background warm-up thread computes the slow company-wide analytics at start-up | the ~20 s first computation of the risk score happens before the first visitor needs it |

| U8 | **Team review: the first version's 3D views (galaxy, surfaces, podium, cube) were replaced by 2D charts** — treemap + donut, heatmaps, medal cards + leaderboard, bubble chart, timeline | 3D was hard to read precisely and to explain; every 2D chart answers one question at a glance and is a few lines of `plotly.express` |

## 4. Questions you may be asked

**Q: Where does the UI talk to the database?** Nowhere directly — every page calls a Phase 3 manager
(`EmployeeManager`, `AnalyticsManager`, …). `attempt()` turns the managers' exceptions into a
warning (validation, business rule, not found) or an error (database).

**Q: How does a department change reach the warehouse?** *Employee 360 → Change position* calls
`change_position()`: one transaction closes the OLTP job version, opens the new one and runs
`hr_dw.sp_scd2_apply_employee`. The page then shows the expired and the new warehouse row.

**Q: Why no 3D charts?** The first version had them; the team review found them hard to read and to
explain (decision U8). Each 2D chart answers one question: the risk heatmap shows *where* risk is
(job level × tenure), the bubble chart *which* projects are bottlenecks, the timeline *how* a career
changed version by version.

**Q: Why does a new review not appear on the dashboards at once?** The warehouse is loaded in
batches (decision B5); *Operations → Run warehouse ETL* loads it. Employee changes are immediate
because SCD Type 2 must record them on the day.

**Q: Is the public app safe?** Writes need the editor passcode (`APP_PASSCODE` secret); database
credentials live only in Streamlit secrets; every value is a bound SQL parameter.

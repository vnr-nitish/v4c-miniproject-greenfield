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
| Forms: create projects | `projects.py` — tabs: Dashboard (all projects), Project details (open one by code: details, team, edit, close), Create, Assign people (over-allocation check with "assign anyway") |
| Forms: submit reviews | `reviews.py` — reviewer and project pre-filled from the employee's history |
| Department change → SCD Type 2 | `employee_360.py` — search by name, e-mail, phone or ID; tabs Details, Career timeline, Update, Reviews & projects, Exit. *Update → Position* calls `EmployeeManager.change_position()`; a pop-up shows the **expired** and the **new current** `dim_employee` row |
| Connect directly to the warehouse | every analytics page reads `hr_dw` only, through `AnalyticsManager` |
| Year-over-year trends | `trends.py` — tiles, metric buttons, department comparison and the `LAG()` table |
| Top performers by department (`DENSE_RANK`) | `top_performers.py` — a tab with a table per department; review cycles, job level, ranks and minimum reviews |
| Attrition risk / project bottlenecks | both: `attrition.py` (bands, factors, back-test, at-risk list with a View pop-up) and `bottlenecks.py` (tabs: bubble chart and list of overdue projects; delivery delays of completed projects) |
| Replace the command-line interface | `operations.py` — run the warehouse ETL from the browser, batch log and rejected rows |

## 2. Architecture

```
app.py            navigation (sections), background analytics warm-up
views/*.py        one file per page — widgets and charts only, no SQL
src/ui/theme.py   CSS (cards, colour-coded tiles, gradient header), meaning-carrying colours, Plotly template
src/ui/charts.py  chart builders (plotly.express): treemap, donut, lines, bars, bubbles
src/ui/forms.py   the dependent position picker shared by onboarding and job changes
src/ui/common.py  header, tiles, fact cards, each page's filters, pop-ups, attempt() (exceptions -> messages)
src/dal/…         the Phase 3 managers — the only way the UI reaches MySQL
```

## 3. Decisions (approved)

| ID | Decision | Why |
|---|---|---|
| U1 | Dark "command centre" theme (`.streamlit/config.toml` + CSS), one Plotly template for every chart | a distinctive, consistent product rather than the default look |
| U2 | ~~Plotly 3D views~~ → **2D Plotly charts** (decision U8) | meets "Plotly/Altair", native to Streamlit, deploys unchanged |
| U3 | ~~Data entry and ETL behind a passcode~~ → **no password** (decision U9) | the first design protected writes on the public URL |
| U4 | One clear chart per question, plus the exact table where it matters | charts to see the pattern, tables to read values |
| U5 | Colours carry meaning: each department has its own colour, risk bands green → red, a different accent per tile | replaces the first single-accent design |
| U6 | Flat rating/KPI trends shown honestly | no invented trends (synthesizer D26) |
| U7 | Background warm-up thread keeps the slow default views computed, and saved across restarts (backend B24) | the first visitor after a wake-up gets them in seconds instead of waiting for minutes of queries |

| U8 | **Team review: the first version's 3D views (galaxy, surfaces, podium, cube) were replaced by 2D charts** | 3D was hard to read precisely and to explain; every 2D chart answers one question at a glance and is a few lines of `plotly.express` |
| U9 | **No password anywhere: data entry and the ETL are open to everyone** | the trainer asked for it, so the site can be tried directly; every entry is still validated |
| U10 | **Filters on each analytics page, not one set in the sidebar**: only those that mean something there (Department; Job level on trends, top performers and attrition) | a sidebar set applied filters that did nothing on some pages; on the page they are next to what they change |
| U11 | Fewer charts, chosen as an evaluator would: removed the heatmaps, the yearly attrition trend, medal cards and leaderboard | each remaining view answers a question directly; the trend only fell because headcount grew, and its last point is an unfinished year |
| U12 | Every save ends in the same pop-up, and a click in a table redraws only that table (a fragment) | one clear confirmation; opening a person on the attrition page went from 6–17 s to about 1 s on the live site |

## 4. Questions you may be asked

**Q: Where does the UI talk to the database?** Nowhere directly — every page calls a Phase 3 manager
(`EmployeeManager`, `AnalyticsManager`, …). `attempt()` turns the managers' exceptions into a
warning (validation, business rule, not found) or an error (database).

**Q: How does a department change reach the warehouse?** *Employee 360 → Update → Position* calls
`change_position()`: one transaction closes the OLTP job version, opens the new one and runs
`hr_dw.sp_scd2_apply_employee`. A pop-up then shows the expired and the new warehouse row.

**Q: Why no 3D charts?** The first version had them; the team review found them hard to read and to
explain (decision U8). Each 2D chart answers one question: the treemap shows *where* risk is, the
bubble chart *which* projects are bottlenecks, the back-test *whether* the score works.

**Q: Why does a new review not appear on the dashboards at once?** The warehouse is loaded in
batches (decision B5); *Operations → Run warehouse ETL* loads it. Employee changes are immediate
because SCD Type 2 must record them on the day.

**Q: Why is there no login?** The trainer asked for the site to be open so it can be tried directly
(decision U9). Anyone with the link can read and change the data. Every entry is still validated,
database credentials live only in Streamlit secrets, and every value is a bound SQL parameter.

**Q: Why are the filters on each page?** Decision U10. Each page offers only the filters that mean
something there. Projects have no job level, for example, and on Top performers the tabs already split
by department.

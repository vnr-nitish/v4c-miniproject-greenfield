# Website Guide — HR Analytics Command Centre

**Live app:** https://v4c-miniproject-greenfield-rvuytjcpc39yzy8xnsjus4.streamlit.app
**Run locally:** `streamlit run app.py` → http://localhost:8501
**Code:** `app.py` (shell) · `views/` (one file per page) · `src/ui/` (theme, charts, forms) · `src/dal/` (managers)

This guide explains every page: what you see, what each control does, what happens when you
submit, and how the data travels between the page and the database.

---

## 1. How the website works

```
 Browser ──► Streamlit app (Streamlit Community Cloud, Python)
               app.py         navigation · sidebar filters · health check · background warm-up
               views/*.py     the pages: widgets and charts only, no SQL
               src/dal/*      managers: the only code that talks to MySQL
                    │
                    ▼
             MySQL 8 on Aiven (cloud)
               hr_oltp   operational database — the forms read and write here
               hr_dw     star-schema warehouse — the dashboards read only here
               hr_staging raw synthesized data (used once, by the migration)
```

- **Every click re-runs the page.** Streamlit executes the page's Python file from top to bottom
  on each interaction (choosing a filter, pressing a button). That is why caching (§9) matters.
- **Forms write through managers.** A form builds an entity (`Employee`, `Position`, `Project`,
  `Review`), which checks its own rules at once, then calls a manager method, which runs the
  SQL and stored procedures in **one transaction**: everything is saved, or nothing.
- **Dashboards read the warehouse only** (`AnalyticsManager`), with SQL that uses CTEs and window
  functions (`LAG`, `DENSE_RANK`, `ROW_NUMBER`, `NTILE`, `PERCENT_RANK`, `RANK`).
- **Messages:** a yellow box = something you can fix (a broken rule, a duplicate, not found); a
  red box = the database could not do its job.

## 2. Layout and navigation

The left sidebar has four sections and the **Filters**:

| Section | Pages |
|---|---|
| Overview | Command centre |
| Data entry (OLTP) | Onboard employee · Employee 360 · job change · Projects · Performance review |
| Analytics (warehouse) | Performance trends · Top performers · Attrition risk · Project bottlenecks |
| Operations | ETL & data health |

**Filters** (sidebar) — *Department*, *Job level*, *Review cycle*. Empty = everything. They apply to
every analytics page at once:

| Page | Uses |
|---|---|
| Command centre, Attrition risk | department, job level (the current workforce) |
| Performance trends, Top performers | department, job level, review cycle (department and level *at review time*) |
| Project bottlenecks | department (the project's owning department) |

**Passcode.** On the public site, data-entry pages and the ETL button are locked until you enter
the editor passcode (the `APP_PASSCODE` secret); once unlocked, they stay unlocked for your visit.
Dashboards are open to everyone. Locally (no passcode configured) everything is open.

## 3. Command centre (Overview)

The page answers "how is the company doing right now?". Every number is read from the
**warehouse** (`hr_dw`) through `AnalyticsManager.overview()`, and every one follows the sidebar
filters. Numbers below are from the live site on 30 October 2026.

### How to read the arrows

Some tiles show a line such as **▼ -0.08 vs previous year**:
- **▲ / ▼** = the value went up / down compared with the previous year (computed in SQL with `LAG()`).
- **Green** = the change is good, **red** = the change is bad. Good depends on the measure: a *fall* in
  attrition is green, a *rise* in rating or KPI is green.
- Tiles without an arrow have no year-on-year comparison.

### What a "review cycle" is

Once a year every employee receives **one performance review covering the previous year**:
performance year **2025** is reviewed between **15 January and 15 March 2026**. Those review dates
fall in India's fiscal year **FY2025-26** (1 April 2025 – 31 March 2026), so the cycle is labelled
**FY2025-26**. The database holds seven cycles, FY2019-20 to FY2025-26 (performance years
2019–2025); the latest, FY2025-26, has 83,188 reviews. "Latest cycle" on a tile means FY2025-26.

### The tiles, one by one

**ACTIVE HEADCOUNT — 83,880**
- *Meaning:* how many people work for the company **today** (they have not left).
- *Calculation:* the number of employees whose **current** warehouse version has no exit date:
  `COUNT(*) FROM dim_employee WHERE is_current = 1 AND exit_date IS NULL`.
- *Why not 100,000:* 100,000 people have ever been employed; 16,122 of them (16.1 %, the IBM
  dataset's attrition rate) left between January 2019 and June 2026. They stay in the database
  because their history and reviews are still needed. The number rises by one for every employee
  onboarded through the site (83,878 → 83,880 after two test hires) and falls with every exit recorded.

**ATTRITION FY2025-26 — 2.59 %, ▼ -0.08 vs previous year (green)**
- *Meaning:* the share of the workforce that left during the **latest complete fiscal year**
  (Indian FY, 1 April 2025 – 31 March 2026).
- *Calculation:* leavers ÷ average headcount × 100, where
  - leavers = employees whose exit date falls in the fiscal year = **2,153**;
  - average headcount = (headcount on 1 April 2025 + headcount on 31 March 2026) ÷ 2
    = (82,632 + 83,835) ÷ 2 = **83,233.5**;
  - 2,153 ÷ 83,233.5 × 100 = **2.59 %**.
- Headcounts are **point-in-time** through SCD Type 2: each person is counted with the job version
  that was valid on that day (so a department filter counts people in the department they were in
  *then*).
- *The arrow:* FY2024-25 was 2.67 %, so 2.59 − 2.67 = **-0.08 points** — fewer people left, therefore green.
- Why it declines over the years: the number of leavers stays at about 2,150 a year while the
  company grows (deferred item D3).

**AVG RATING FY2025-26 — 3.155, ▲ +0.003 vs previous year (green)**
- *Meaning:* the average performance rating of the **latest review cycle**. FY2025-26 holds the
  reviews of performance year 2025, held between January and March 2026 (83,188 reviews).
- *Calculation:* `AVG(performance_rating)` over the fact rows (`fact_performance_reviews`) whose
  review date falls in that fiscal year.
- *Scale:* 1 Low, 2 Good, 3 Excellent, 4 Outstanding. Like the IBM data, the generated reviews use
  only 3 and 4, so the average is simply **3 + the share of outstanding ratings**:
  3 + 0.155 = 3.155.
- *The arrow:* FY2024-25 averaged 3.151; the difference (from the unrounded values) is +0.003.

**AVG KPI SCORE — 80.12, ▲ +0.03 vs previous year (green)**
- *Meaning:* the average KPI score (0–100, an objective performance measure recorded with each
  review) in the same latest cycle, FY2025-26.
- *Calculation:* `AVG(kpi_score)` over the same 83,188 reviews.
- *What a KPI score is:* a 0–100 number on every review — how much of their targets the employee
  achieved (objective), next to the rating (the manager's judgement).
- *Where each KPI value comes from:*
  - **Generated reviews** (the 100K history; the IBM data has no KPI): the synthesizer draws a value
    that fits the rating — rating 4 → around **92** (spread ±4, always 85–100); rating 3 → around
    **78** (±7, 60–95); values outside the range are drawn again, not cut off, so none pile up at 100.
  - **Reviews entered on the site:** the reviewer types the KPI in the review form.
- *Why the average is about 80:* 84.5 % of the cycle's reviews are rated 3 and 15.5 % are rated 4,
  so 0.845 × ~78 + 0.155 × ~92 ≈ 80 — the tile's exact value is the sum of all 83,188 KPI scores ÷ 83,188.
- *The arrow:* 80.12 against about 80.09 a year earlier: +0.03.

**OUTSTANDING REVIEWS — 15.5 %**
- *Meaning:* the share of the latest cycle's reviews rated **4 · Outstanding**.
- *Calculation:* outstanding reviews ÷ all reviews in FY2025-26 × 100 = 15.5 %
  (`100 × AVG(performance_rating = 4)`). It is the same information as the rating tile seen from
  the other side: 15.5 % outstanding ⇒ average 3.155.

**OPEN PROJECTS — 332**
- *Meaning:* projects that are not finished: status **Planned, Active or On Hold**.
- *Calculation:* `COUNT` of `dim_project` rows with one of those statuses (the special
  "No Project" row is excluded). The other projects are Completed or Cancelled.
- *When it changes:* projects created or closed on the Projects page are counted after the next
  **Run warehouse ETL** (the warehouse is loaded in batches).

**OVERDUE PROJECTS — 89**
- *Meaning:* open projects whose **planned end date has already passed** — late projects.
- *Calculation:* open projects (as above) with `planned_end_date < today`.
- *Why it grows by itself:* "today" moves every day, so an open project becomes overdue the day
  after its planned end (it was 87 a few days earlier). It falls when a project is closed.
- The Project bottlenecks page shows which projects these are and how late each one is.

### The charts

**Headcount by department and role (treemap)**
- Each rectangle is a group of active employees: the outer blocks are departments, the inner ones
  job roles. **Size** = number of people (e.g. Research Scientist 16,667).
- **Colour** = the share of that group at **High or Very high** attrition risk (scale on the right:
  green ≈ low, yellow, orange, red ≈ high). Research Scientists are red because many are entry
  level without stock options; Manufacturing Directors are yellow-green.
- Click a block to zoom into it, click the top bar ("Company") to zoom out.

**Active employees by years at the company (stacked bars)**
- Each bar is a tenure group (under 1 year, 1, 2, 3–4, 5–9, 10–19, 20+ years); its height is the
  number of active employees in it, split by risk band (green Low → red Very high).
- *Calculation:* each person's years at the company = full years from the hire date to today;
  people are counted per group and band.
- *What it shows:* the red and amber parts are largest in the first two years — new joiners carry
  most of the attrition risk (the "less than 2 years" factor, and entry-level jobs without stock).

**Attrition-risk bands (donut)**
- How the active employees split over the four risk bands: Low (green), Medium (blue), High
  (amber), Very high (red). The percentages are each band's share of the active headcount
  (e.g. 55.6 % Medium, 31.8 % Low).
- The band comes from each person's risk score (0–14 points from 11 work-related factors; see
  the Attrition risk page).

**Headcount by department (bars)**
- Active employees per department, with the number on each bar.

## 4. Onboard employee

**Step 1 · Position** (outside the form, so each choice narrows the next):
1. **Department** → 2. **Job role** — only the roles that department offers → 3. **Job level** —
only the levels that role allows → 4. **Monthly income** — a slider limited to that level's salary
band → 5. **Manager** — active employees of the department at a higher level (or none) →
6. **Business travel**, **Stock option level**.

**Step 2 · Person** (a form): first and last name, e-mail, phone, gender, marital status, date of
birth, hire date (today or earlier), distance from home, education level and field, prior
experience and number of previous employers.

**When you press Onboard:**
1. The `Employee` entity checks its rules and lists *every* problem under the field names (e.g.
   "First name must be 1-50 letters", "An employee must be at least 18 on the hire date",
   "Previous employers must be 0 exactly when there is no prior experience").
2. `EmployeeManager.onboard()` runs, in one transaction: `sp_onboard_employee` (checks the
   position rules again in the database, generates the next code `EMP000…`, inserts the person
   and the first job version "Hire") and `sp_scd2_apply_employee` (the first `dim_employee`
   row in the warehouse). If someone else took the same code at the same moment, it retries
   with the next code.
3. On success: a green message with the new code, **balloons**, and the new warehouse row.

**Balloons appear only here, and only after a successful hire.** They do not appear when the form
shows a warning (nothing was saved), and no other page uses them — they mark the one moment a new
person enters the company.

## 5. Employee 360 · job change

**Find someone:** type an employee code, or click one of the **example** chips (active employees
with the longest history). The header shows department, role and level, income, tenure and the
number of SCD Type 2 versions.

**Tab "Career timeline":** one bar per job version on a time axis, coloured by department, plus
the version table straight from the warehouse (`dim_employee`: version number, department,
role, level, income, change reason, start and end dates, current flag).

**Tab "Change position"** (the Phase 4 SCD Type 2 requirement):
- The same position picker as onboarding, pre-filled with the current job. You can change the
  **department** (only roles that department offers become selectable), **role**, **level**,
  **monthly income**, **manager**, **stock options** and **travel**.
- **Effective date:** today or earlier, after the current version started; at most one change per
  employee per day. **Reason:** pick one or leave "Derive automatically" — then Promotion (level up) >
  Transfer (department) > Role Change > Manager Change > Salary Revision.
- **Refused, with a message:** the employee has left; nothing changed; the role is not offered in
  that department; the level is outside the role's range; the pay is outside the level's band; the
  manager is not an active employee; the date is in the future or before the current version.
- **What happens on "Apply change"** — one transaction:
  - OLTP (`sp_change_employee_position`): the current job version is closed on the effective date
    and a new version opens; the old row is never overwritten.
  - Warehouse (`sp_scd2_apply_employee`): the current `dim_employee` row is **expired**
    (end date = the day before, `is_current = 0`) and a **new current row** is inserted (start =
    effective date, end 9999-12-31, version number + 1).
  - The page shows the **expired** row (red) and the **new** row (green) side by side, and the
    timeline with the new bar.
- **What you get afterwards:** reviews from the effective date on link to the new version (earlier
  reviews keep the old one, so history stays true); department analytics count the person in the
  new department from that day; the attrition-risk score reflects the new level, pay or stock on
  the dashboards' next refresh (immediately, because the page tells the analytics cache).

**Tab "Reviews · projects · exit":** the person's reviews and project assignments; **Record an
exit** (date and reason) — `sp_record_exit` ends their open assignments and the warehouse marks
every version as a leaver (attrition flag, exit date). People who left cannot change jobs.

## 6. Projects

| Tab | What you can do | Rules / result |
|---|---|---|
| Browse & close | Search by code or name, filter by status; click a row to see the team; close an open project as Completed or Cancelled with its actual end date | Closing ends the open assignments on that date |
| Create | Owning department, project manager (level 3+ of that department), name, start and planned end, budget, priority, status (Planned / Active), description | The next code `PRJ0…` is generated by the database (retried if taken); planned end ≥ start |
| Assign people | Active project, employee code, project role, allocation (5–100 %), start date, "assign even if above 100 %" | Refused: closed project, start before the project, person not employed on that date. If the person's total allocation would pass 100 %, a warning names both numbers — tick the box to assign anyway |

New projects reach the warehouse (and the bottleneck page) on the next ETL run (§12).

## 7. Performance review

Type an employee code and pick the **performance year**. The page suggests the **reviewer** (the
person's manager at the end of that year) and the **main project** that year. The form: rating
(1 Low · 2 Good · 3 Excellent · 4 Outstanding), KPI score (0–100), training sessions, overtime,
reviewer and project (pre-filled, editable), the date the review was held, comments.

**Refused, with a message:** a second review for the same employee and year; a review date in the
future or outside the review year and the next; the employee had not joined by the end of that
year; the reviewer was not employed on that date; self-review. Saved reviews appear in the table
at once and reach the dashboards after the next ETL run.

## 8. The analytics pages (all read the warehouse)

### Performance trends
- **Metric** buttons (KPI, rating, outstanding %, overtime %, job satisfaction, training) and
  **Compare departments** (one line per department).
- Tiles: the latest cycle with the change against the previous one; the **Year-over-year table**
  (expander) shows the `LAG()` columns.
- **Heatmap** job role × review cycle for a chosen metric — where the real movement is (e.g.
  overtime per role). Ratings and KPI are steady by design: no trend was invented.

### Top performers
- Controls: review cycles (default the last three), ranks per department (1–20), minimum number of
  reviews, current staff only.
- **Medal cards** 🥇🥈🥉 per department (with "+N tied" when people share a rank), a **leaderboard
  chart**, and a table per department with the rank, "sharing rank" and ranked-employee count.
- The ranking is `DENSE_RANK() OVER (PARTITION BY department ORDER BY avg rating DESC, avg KPI DESC)`
  computed at query time, so it follows the filters; the SQL is shown on the page.

### Attrition risk
- **Heatmap** job level × years at the company: % of employees at High / Very high risk (red =
  concentrated risk; cells with fewer than 30 people are blank).
- **Bands per department** and **factor prevalence** (which factors the at-risk people share).
- **Who is at risk:** choose the minimum band; **click a row** to open a dialog explaining that
  person's score and factors.
- **Evidence:** the back-test bars (share of people who actually left within a year, per band) and
  **attrition per fiscal year** (point-in-time headcounts through SCD Type 2).
- The score: 11 work-related factors, 0–14 points; bands Low 0–2, Medium 3–5, High 6–7, Very high 8+;
  marital status, age and gender are deliberately not used.

### Project bottlenecks
- **Bubble chart** of overdue open projects: days overdue × team overtime, bubble = budget, colour
  = priority; top right = the real bottlenecks. The table below lists them with a days-overdue bar.
- **Completed projects:** % finished late and the average delay, grouped by priority, budget band,
  department or year.

## 9. ETL & data health (Operations)

- The **warehouse version** (latest finished ETL batch) and the number of cached results.
- **Run warehouse ETL** (passcode): runs `hr_dw.sp_run_etl` — dimensions first, then the fact,
  SCD Type 2 included — and lists every step with its row count. This is what makes new reviews
  and projects appear on the dashboards.
- **Recent batches:** click one to see its steps and any rejected rows with the reason.

## 10. When does a change appear on the dashboards?

| You do | OLTP | Warehouse / dashboards |
|---|---|---|
| Onboard an employee | at once | **at once** (warehouse version 1) |
| Change position · record an exit | at once | **at once** (SCD Type 2 step / leaver flag) |
| Create a project · assign · close · submit a review | at once | after **Run warehouse ETL** (the warehouse loads in batches, decision B5) |

## 11. Speed: what is cached

| What | How long | Why |
|---|---|---|
| Dashboard results | until the warehouse changes | `AnalyticsManager` cache, keyed by the warehouse version |
| The warehouse version itself | 60 s, or until this app changes the warehouse | asking costs a 0.45 s round trip to the cloud; cached results now return in under 1 ms instead of ~0.45 s each |
| Drop-down lists (departments, roles, levels, education, exit reasons) | the app's lifetime | reference data hardly changes |
| Manager choices · example employees | 5 min · 1 hour | each is a query over 100,000 employees (~2 s / ~1 s) |
| Company-wide risk score | computed once in the background when the app starts | ~20 s on the first visit after a data change |

## 12. Questions you may be asked

**Why 83,878 and not 100,000 on the headcount tile?** The tile counts active employees; 16,122 left
(the IBM attrition rate) and are kept for history.

**Where is SCD Type 2 visible?** Employee 360 → Change position shows the expired and the new
warehouse row; the career timeline shows every version.

**Why does a new review not show on the dashboards at once?** The warehouse is loaded in batches;
run the ETL from Operations. Employee changes are immediate because SCD Type 2 must record them
on the day.

**What if two people onboard at the same moment?** Both get a valid code: the database refuses the
duplicate and the app retries with the next one.

**Is the data safe on the public site?** Writes need the editor passcode; credentials live only in
Streamlit secrets; every value is a bound SQL parameter.

## 13. The data: from 1,470 IBM rows to 100,000 employees

### 13.1 The source: IBM HR Analytics Employee Attrition & Performance

One CSV file (`data/raw/WA_Fn-UseC_-HR-Employee-Attrition.csv`): **1,470 rows, one per employee, 35
columns**. It's a single **snapshot**, with no dates and no history.

How to read the 1–4 scales:
- **Satisfaction and involvement:** 1 Low · 2 Medium · 3 High · 4 Very high.
- **Work-life balance:** 1 Bad · 2 Good · 3 Better · 4 Best.
- **Performance rating:** 1 Low · 2 Good · 3 Excellent · 4 Outstanding.
- **Education** (1–5): 1 Below college · 2 College · 3 Bachelor · 4 Master · 5 Doctor.

| Column | Values in the file | Meaning | Where it went in this project |
|---|---|---|---|
| Age | 18 – 60 (median 36) | age at the snapshot | date of birth (±1 year variation) |
| Attrition | No 1,233 · Yes 237 (16.1 %) | has the person left? | exit date and exit reason (Yes → left between 2019 and June 2026) |
| BusinessTravel | Travel_Rarely 1,043 · Travel_Frequently 277 · Non-Travel 150 | how often they travel | job version (`business_travel`) |
| DailyRate | 102 – 1,499 | a pay rate unrelated to income (noise) | kept in staging only |
| Department | R&D 961 · Sales 446 · Human Resources 63 | department | `departments`, job version |
| DistanceFromHome | 1 – 29 km | commute distance | `employees.distance_from_home_km` (±2 km) |
| Education | 1 – 5 | education level | `education_levels` |
| EducationField | Life Sciences 606 · Medical 464 · Marketing 159 · Technical Degree 132 · Other 82 · Human Resources 27 | field of study | `education_fields` |
| EmployeeCount | always 1 | constant | dropped (no information) |
| EmployeeNumber | 1 – 2,068 | IBM's id | `source_employee_number` in staging: which IBM row a synthetic employee was modelled on; new codes are EMP000001… |
| EnvironmentSatisfaction | 1 – 4 | survey answer | `employee_surveys` (latest survey = IBM value) |
| Gender | Male 882 · Female 588 | gender | `employees.gender` |
| HourlyRate | 30 – 100 | noise | staging only |
| JobInvolvement | 1 – 4 | survey answer | `employee_surveys` |
| JobLevel | 1 – 5 (median 2) | seniority | job version (`job_levels`; each level has a salary band) |
| JobRole | 9 roles (Sales Executive 326 … Human Resources 52) | role | `job_roles`, job version |
| JobSatisfaction | 1 – 4 | survey answer | `employee_surveys` |
| MaritalStatus | Married 673 · Single 470 · Divorced 327 | marital status | `employees` (deliberately **not** used in the risk score) |
| MonthlyIncome | 1,009 – 19,999 (median 4,919) | monthly pay | job version (±7 %, kept inside the level's band) |
| MonthlyRate | 2,094 – 26,999 | noise | staging only |
| NumCompaniesWorked | 0 – 9 | previous employers | `prior_companies_count` (corrected by rule D19 in the ETL: 16,500 values) |
| Over18 | always Y | constant | dropped |
| OverTime | No 1,054 · Yes 416 | works overtime | review `overtime_flag` (latest review = IBM value) |
| PercentSalaryHike | 11 – 25 % | last pay rise | the last pay change in the job history (92 % within 1 point) |
| PerformanceRating | **only 3 and 4** | last rating | latest review = IBM value |
| RelationshipSatisfaction | 1 – 4 | survey answer | `employee_surveys` |
| StandardHours | always 80 | constant | dropped |
| StockOptionLevel | 0 – 3 | stock options | job version |
| TotalWorkingYears | 0 – 40 | whole career | prior experience = TotalWorkingYears − YearsAtCompany |
| TrainingTimesLastYear | 0 – 6 | trainings last year | review `training_sessions` (latest = IBM value) |
| WorkLifeBalance | 1 – 4 | survey answer | `employee_surveys` |
| YearsAtCompany | 0 – 40 (median 5) | tenure | **hire date** = snapshot (or exit) − years |
| YearsInCurrentRole | 0 – 18 | time in role | date of the **last role change** |
| YearsSinceLastPromotion | 0 – 15 (median 1) | time since promotion | date of the **last promotion** |
| YearsWithCurrManager | 0 – 17 | time with manager | date of the **last manager change** |

The last four columns are what make history possible: they say *when* things happened, so the job
history is built backwards from them (§13.2). Rebuilding the IBM columns from the database gives
back the original values: 100 % for tenure, role, promotion and level, and 98 % for years with the
manager.

### 13.2 What the synthesizer generates (`src/synthesizer`, seed 42, snapshot 30 June 2026)

| Generated table (staging) | Rows | How |
|---|---:|---|
| `stg_employees` | 100,000 (+500 planted duplicates) | **Stratified sampling** of the IBM rows: each group of (attrition, department, role, level) gets its exact share of the 100,000, so the mix matches IBM. Small variations ("jitter") make every person unique: income ±7 % (inside the level's band), age ±1, distance ±2 km. Faker creates new identities: Indian names, `example.com` e-mails, phone numbers. The hire date comes from YearsAtCompany, and leavers get an exit date between 2019 and June 2026 |
| `stg_job_history` | 308,986 versions | Built **backwards** from today's job using the IBM dates: last promotion, last role change, last manager change. 10 % of people get a department transfer, and long careers get earlier promotions. Pay changes follow PercentSalaryHike. Versions per person: 1 → 8,594 people · 2 → 21,095 · 3 → 30,175 · 4 → 34,579 · 5+ → 5,557 |
| `stg_surveys` | 468,646 | One engagement survey per person per year (September–October, 2019–2025) while employed. The **latest** survey holds the IBM answers; earlier years step back with small changes, keeping the IBM mix every year |
| `stg_reviews` | 463,015 (+1,389 planted resubmissions) | One review per person per year, held 15 Jan – 15 Mar of the next year, for people who **joined by 30 September** and are still employed on the review date. The latest review holds the IBM rating, overtime and training; earlier years step back the same way. The KPI comes from the rating (§14, question 5). Reviewer = the manager on the review date; project = the person's main project that year |
| `stg_projects` | 2,000 | Split over departments by headcount, with Faker names. Managers are level-3+ staff of the department. About 40 % of finished projects end late, by 1–120 days |
| `stg_assignments` | 503,240 | Each employment year, 92 % of people are on a project and 25 % of those also on a second one. Allocations are 20–100 %; about 5 % are allowed above 100 % (bottlenecks) |

**Planted data-quality problems.** A real source system is never clean, and the problem statement
asks the ETL to clean data. So the synthesizer deliberately adds problems and records each one in a
manifest. The ETL must fix every one, and a test checks the counts:

| Problem planted in staging | Count | Fixed by the ETL with |
|---|---:|---|
| The same employee sent twice | 500 | `ROW_NUMBER()` per employee code, newest kept |
| Gender written as "M" / "F" | 1,027 | `CASE` → Male / Female |
| Department / role labels with wrong case, extra spaces or aliases ("R&D", "HR", "Sales Dept") | 6,172 job versions · 2,011 employees | `TRIM` + case-insensitive match + alias map |
| A review sent again with a corrected KPI ("resubmission") | 1,389 | `ROW_NUMBER()` per employee and year, latest kept |

### 13.3 From staging to the dashboards

1. **`hr_staging`** holds the raw data: 1.85 million rows.
2. **`sp_migrate_from_staging`** cleans it, removes duplicates and splits it into the normalized
   **`hr_oltp`** (14 tables).
3. **`sp_run_etl`** loads the star schema **`hr_dw`**: first the dimensions, with SCD Type 2 history,
   then the review fact.

The website's forms work on `hr_oltp`; its dashboards read `hr_dw`.

## 14. Your questions answered

**1. Why does Active headcount show 83,880 and not 100,000?**
100,000 people have *ever* been employed. 16,122 of them (16.1 %, the IBM attrition rate) left
between January 2019 and June 2026. They stay in the database because their reviews and job history
are still needed.
- Active headcount = employees with no exit date.
- It grows by one with every hire made on the site (83,878 → 83,880 after two test hires).

**2. Why does the latest review cycle have 83,188 reviews when 83,880 people are active?**
They count different people:

| | People |
|---|---:|
| Active today **and** reviewed for 2025 | 82,421 |
| + reviewed for 2025, **left afterwards** (April – June 2026) | 767 |
| = reviews in cycle FY2025-26 | **83,188** |
| Active today **and** reviewed for 2025 | 82,421 |
| + active, **hired on or after 1 October 2025**: too new for the 2025 review | 1,459 |
| = active headcount | **83,880** |

The rule: an employee is reviewed for a year only if they joined by **30 September** of that year
(at least a quarter of the year to judge), and are still employed on the review date. New joiners get
their first review in the next cycle.

**3. What is a review cycle?**
Once a year, everyone is reviewed for the previous year: performance year 2025 is reviewed between
15 January and 15 March 2026. Those dates fall in India's fiscal year FY2025-26 (April 2025 – March
2026), so the cycle is called FY2025-26. There are seven: FY2019-20 to FY2025-26.

**4. Can a rating be 4.5? How is the average rating 3.155?**
No. A single review's rating is always a whole number: 1 Low, 2 Good, 3 Excellent, 4 Outstanding.
Only averages have decimals. Like IBM's data, the generated reviews use only 3 and 4, so the average
is 3 + the share of 4s: 3 + 0.155 = **3.155**.

**5. How is the KPI score produced? Is it just a random number?**
For reviews entered on the site, the reviewer types it. For the generated reviews (the IBM data has
no KPI), it's drawn at random, but **from a range chosen by the review's rating, the only factor
used**:

| Rating | KPI centred on | Typical spread | Always between |
|---|---:|---:|---|
| 4 · Outstanding | 92 | ± 4 | 85 – 100 |
| 3 · Excellent | 78 | ± 7 | 60 – 95 |
| 2 · Good | 60 | ± 7 | 40 – 75 |
| 1 · Low | 42 | ± 7 | 20 – 60 |

- **"Centred on 92 with spread ± 4"** means most Outstanding reviews score between 88 and 96, with
  fewer further out.
- **Out-of-range values** are drawn again rather than cut off, so scores don't pile up at 100.
- **Example:** three Outstanding reviews might get 91.37, 95.02 and 88.64.
- **Nothing else counts:** tenure, department and pay play no part. So KPI agrees with the rating
  without being a copy of it.
- **The cycle's average (80.12)** follows from this. 84.5 % of reviews are 3s (≈ 78) and 15.5 % are
  4s (≈ 92), so 0.845 × 78 + 0.155 × 92 ≈ 80.

**6. What are the "corrected resubmissions", and why are some KPIs outside their range?**
In real HR systems a manager sometimes submits a review, then corrects it and submits it again, so
the source data contains the same review twice. The synthesizer imitates this on purpose for 1,389
reviews (0.3 %): it adds a **second copy** of the review in staging, with the KPI changed by a small
random amount (typically ± 3 points). A real example from the data, employee **EMP000022**, review
of 2019, rating 3:

| Staging row | KPI | |
|---|---:|---|
| 7 (first submission) | 85.07 | dropped by the ETL |
| 463,016 (resubmission) | **89.64** | **kept**: the latest submission wins |

- The ETL keeps exactly one review per employee and year, the latest, using `ROW_NUMBER()`.
- The correction is applied after the KPI was drawn, so it can push a score slightly past its
  rating's range. **24** of the 463,015 reviews lie just outside, for example an Outstanding review
  at 84.6, just under 85.
- That's realistic (a corrected value) and documented, not an error.

**7. How is attrition 2.59 % calculated, and what does ▼ -0.08 mean?**
Leavers in fiscal year FY2025-26 ÷ average headcount × 100 = 2,153 ÷ ((82,632 + 83,835) ÷ 2) =
**2.59 %**. The year before it was 2.67 %, so it fell by 0.08 points. Fewer people left, so it's
shown in green.

**8. What are the attrition-risk bands in the donut?**
Each active employee gets 0–14 points from 11 work-related warning signs:
- **2 points each:** overtime, entry level, no stock options.
- **1 point each:** under 2 years at the company, low environment satisfaction, low job
  satisfaction, poor work-life balance, low involvement, frequent travel, more than 10 km from home,
  pay in the lower half of the level.

The bands:

| Band | Score | Share of active employees |
|---|---|---:|
| Low | 0–2 | 31.8 % |
| Medium | 3–5 | 55.6 % |
| High | 6–7 | 11.5 % |
| Very high | 8+ | 1.09 % |

Tested on history: 0.7 % of Low and 19.5 % of Very high employees left within a year. The High and
Very high bands hold 19 % of employees but 63 % of the leavers. Marital status, age and gender are
deliberately not used.

**9. Why does the number of overdue projects grow without anyone doing anything?**
Overdue means open with a planned end date before **today**. Every day, an open project whose
planned end was yesterday becomes overdue (87 → 89 in a few days). The number falls when projects
are closed.

**10. When do balloons appear?**
Only after an employee is onboarded successfully. Never after a warning (nothing was saved), and on
no other page.

**11. Why was the first visit slow, and switching pages slow?**
- **The first visit** after the app starts computes the company-wide risk score on the small cloud
  server. That takes about 20 s, once.
- **Page switches** used to ask the database "has anything changed?" 3–5 times per page, at 0.45 s
  each. That answer is now reused for a minute, so cached pages open in well under a second.

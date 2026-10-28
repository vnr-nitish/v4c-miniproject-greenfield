# Synthesis Report

Generated 2026-10-28 14:49 by `python -m src.synthesizer.run` — 100,000 employees, seed 42, snapshot 2026-06-30.
Design and decisions: [`synthesizer_design.md`](synthesizer_design.md).

## 1. Volume

| Staging table | Rows |
|---|---:|
| `stg_employees` | 100,500 |
| `stg_job_history` | 308,986 |
| `stg_surveys` | 468,646 |
| `stg_reviews` | 464,404 |
| `stg_projects` | 2,000 |
| `stg_assignments` | 503,240 |
| **Total** | **1,847,776** |

## 2. Performance

| Step | Seconds |
|---|---:|
| load + validate IBM source | 0.0 |
| employees | 0.6 |
| job history + org chart | 11.2 |
| projects | 1.4 |
| assignments | 3.9 |
| surveys | 0.6 |
| reviews | 1.4 |
| validate | 1.5 |
| write CSV files | 4.1 |
| load hr_staging | 71.9 |
| total | 98.3 |

## 3. Rule checks (every business rule — violations must be 0)

| Area | Rule | Violations |
|---|---|---:|
| employees | employee_code is unique | 0 |
| employees | email is unique | 0 |
| employees | date of birth reproduces Age | 0 |
| employees | hire date reproduces YearsAtCompany | 0 |
| employees | hired at age 18 or older | 0 |
| employees | exit date on or after hire date | 0 |
| employees | income inside the level's salary band | 0 |
| employees | leavers have an exit reason, stayers none | 0 |
| job history | exactly one current version per employee | 0 |
| job history | first version starts on the hire date | 0 |
| job history | versions are contiguous (end = next start) | 0 |
| job history | end date after start date | 0 |
| job history | valid department-role pair | 0 |
| job history | level inside the role's range | 0 |
| job history | income inside the level's band | 0 |
| job history | current version equals the IBM row | 0 |
| job history | nobody is their own manager | 0 |
| job history | manager employed when the version starts | 0 |
| reviews | one review per employee per year | 0 |
| reviews | reviewed only while employed | 0 |
| reviews | has a reviewer who is not the employee | 0 |
| reviews | latest review carries the IBM rating | 0 |
| reviews | earlier reviews use only the ratings IBM uses | 0 |
| reviews | each year's share of outstanding ratings within 3 points of the snapshot | 0 |
| reviews | each year's overtime share within 5 points of the snapshot | 0 |
| reviews | KPI scores do not pile up at a band limit (at most 0.1 % of reviews) | 0 |
| surveys | surveyed only while employed | 0 |
| surveys | latest survey carries the IBM environment_satisfaction | 0 |
| surveys | each year's mean environment_satisfaction within 0.10 of the snapshot | 0 |
| surveys | latest survey carries the IBM job_satisfaction | 0 |
| surveys | each year's mean job_satisfaction within 0.10 of the snapshot | 0 |
| surveys | latest survey carries the IBM relationship_satisfaction | 0 |
| surveys | each year's mean relationship_satisfaction within 0.10 of the snapshot | 0 |
| surveys | latest survey carries the IBM job_involvement | 0 |
| surveys | each year's mean job_involvement within 0.10 of the snapshot | 0 |
| surveys | latest survey carries the IBM work_life_balance | 0 |
| surveys | each year's mean work_life_balance within 0.10 of the snapshot | 0 |
| projects | actual end date exactly when Completed/Cancelled | 0 |
| projects | actual end not before start | 0 |
| assignments | assignment starts after hire | 0 |
| assignments | assignment ends by the exit date | 0 |
| assignments | allocation between 1 and 100 | 0 |
| **All** | **42 rules** | **0** |

## 4. Fidelity — IBM tenure values reconstructed from the generated history

| IBM column | Reproduced exactly |
|---|---:|
| YearsInCurrentRole | 100.00 % |
| YearsSinceLastPromotion | 100.00 % |
| YearsWithCurrManager | 98.09 % |
| PercentSalaryHike (employees with a salary change) | 92.09 % |

PercentSalaryHike counts a match within ±1 percentage point (salaries are whole numbers and earlier salaries are clipped into their level's band).

## 5. Realism — synthetic vs IBM source

**Correlation matrix** (20 numeric columns + attrition): largest absolute difference 0.009, mean absolute difference 0.002.

### Department

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| Human Resources | 4.29 | 4.29 | -0.00 |
| Research & Development | 65.37 | 65.38 | 0.00 |
| Sales | 30.34 | 30.34 | -0.00 |

### JobRole

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| Healthcare Representative | 8.91 | 8.91 | -0.00 |
| Human Resources | 3.54 | 3.54 | -0.00 |
| Laboratory Technician | 17.62 | 17.62 | 0.00 |
| Manager | 6.94 | 6.94 | 0.00 |
| Manufacturing Director | 9.86 | 9.86 | -0.00 |
| Research Director | 5.44 | 5.44 | 0.00 |
| Research Scientist | 19.86 | 19.86 | 0.00 |
| Sales Executive | 22.18 | 22.18 | 0.00 |
| Sales Representative | 5.65 | 5.65 | -0.00 |

### JobLevel

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| 1 | 36.94 | 36.94 | 0.00 |
| 2 | 36.33 | 36.33 | -0.00 |
| 3 | 14.83 | 14.83 | 0.00 |
| 4 | 7.21 | 7.21 | 0.00 |
| 5 | 4.69 | 4.69 | 0.00 |

### Gender

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| Female | 40.00 | 40.19 | 0.19 |
| Male | 60.00 | 59.81 | -0.19 |

### MaritalStatus

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| Divorced | 22.24 | 22.20 | -0.04 |
| Married | 45.78 | 45.73 | -0.05 |
| Single | 31.97 | 32.07 | 0.10 |

### BusinessTravel

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| Non-Travel | 10.20 | 10.12 | -0.08 |
| Travel_Frequently | 18.84 | 18.95 | 0.11 |
| Travel_Rarely | 70.95 | 70.93 | -0.02 |

### OverTime

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| No | 71.70 | 71.55 | -0.15 |
| Yes | 28.30 | 28.45 | 0.15 |

### Attrition

|  | IBM % | Synthetic % | Diff (pp) |
|---|---:|---:|---:|
| No | 83.88 | 83.88 | 0.00 |
| Yes | 16.12 | 16.12 | -0.00 |

### Attrition by OverTime

|  | IBM attrition % | Synthetic attrition % |
|---|---:|---:|
| No | 10.44 | 10.57 |
| Yes | 30.53 | 30.08 |

### Attrition by JobLevel

|  | IBM attrition % | Synthetic attrition % |
|---|---:|---:|
| 1 | 26.34 | 26.34 |
| 2 | 9.74 | 9.74 |
| 3 | 14.68 | 14.68 |
| 4 | 4.72 | 4.72 |
| 5 | 7.25 | 7.24 |

### Attrition by tenure

|  | IBM attrition % | Synthetic attrition % |
|---|---:|---:|
| 0-2 years | 29.82 | 29.70 |
| 3+ years | 11.97 | 11.99 |

### Median monthly income by JobLevel

|  | IBM | Synthetic |
|---|---:|---:|
| 1 | 2,670.00 | 2,658.00 |
| 2 | 5,340.00 | 5,319.00 |
| 3 | 9,980.00 | 9,886.00 |
| 4 | 16,154.00 | 15,923.00 |
| 5 | 19,232.00 | 19,195.50 |

## 6. History, reviews, surveys and projects

| Measure | Value |
|---|---|
| versions per employee (mean) | 3.09 |
| versions per employee (distribution) | {1: 8594, 2: 21095, 3: 30175, 4: 34579, 5: 3981, '6+': 1576} |
| change reasons | {'Hire': 100000, 'Promotion': 96519, 'Role Change': 57723, 'Manager Change': 45479, 'Transfer': 9265} |
| promotions that are pay-grade only (D18) | 54.1 % |
| added career-step promotions (D24) | 8671 |
| current versions with a manager | 98.88 % |
| versions since 2019 with a manager | 99.44 % |
| all versions with a manager | 96.13 % |
| manager segments left unassigned (too early in company history) | 3877 |
| department heads | {'Human Resources': 'EMP000782', 'Research & Development': 'EMP000213', 'Sales': 'EMP000218'} |
| reviews per cycle year | {2019: 46479, 2020: 52298, 2021: 61998, 2022: 67128, 2023: 73052, 2024: 78872, 2025: 83188} |
| rating distribution (all reviews) | {3: 84.7, 4: 15.3} |
| outstanding ratings per cycle year (%) | {2019: 15.1, 2020: 15.3, 2021: 15.2, 2022: 15.3, 2023: 15.3, 2024: 15.1, 2025: 15.5} |
| overtime per cycle year (%) | {2019: 26.7, 2020: 26.5, 2021: 26.2, 2022: 25.9, 2023: 25.5, 2024: 24.8, 2025: 24.1} |
| mean KPI score per cycle year | {2019: 80.09, 2020: 80.1, 2021: 80.07, 2022: 80.11, 2023: 80.14, 2024: 80.09, 2025: 80.12} |
| mean job satisfaction per survey year | {2019: 2.72, 2020: 2.73, 2021: 2.75, 2022: 2.74, 2023: 2.74, 2024: 2.76, 2025: 2.77} |
| KPI scores exactly at a band limit (D27) | 0.00 % |
| surveys per year | {2019: 47275, 2020: 53067, 2021: 62787, 2022: 67958, 2023: 73935, 2024: 79667, 2025: 83957} |
| projects by status | {'Completed': 1563, 'Active': 252, 'Cancelled': 105, 'Planned': 53, 'On Hold': 27} |
| completed projects finishing late | 38.5 % |
| assignments per employee (mean, employees with any) | 5.04 |
| employees ever over-allocated (>100 % concurrently) | 1.8 % |
| reviews without a project (-> 'No Project' member) | 3.7 % |

## 7. Planted data-quality issues (decision D21)

These are deliberately present in staging; the staging → OLTP ETL must fix every one.

| Issue | Count |
|---|---|
| gender_codes | 1027 |
| label_noise_employees | {'upper_case': 476, 'lower_case': 536, 'extra_spaces': 515, 'department_alias': 484} |
| label_noise_job_history | {'upper_case': 1473, 'lower_case': 1571, 'extra_spaces': 1568, 'department_alias': 1560} |
| duplicate_employee_rows | 500 |
| duplicate_review_rows | 1389 |

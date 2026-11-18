"""Profile the IBM HR Attrition dataset before designing the OLTP schema.

Measures column domains, functional dependencies and business rules. Every finding
(F1-F12) in docs/oltp_design.md is reproduced by this script. 

Usage:
    python analysis/profile_dataset.py
"""
from pathlib import Path

import pandas as pd

# Where the IBM file is: from this script go up to the project folder, then into data/raw.
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "raw" / "WA_Fn-UseC_-HR-Employee-Attrition.csv"


# Function 'section': takes a title (text) and prints it as a heading, so the output is easy to read.
# '-> None' means it gives nothing back.
def section(title: str) -> None:
    print(f"\n=== {title} ===")


# Function 'attrition_rate': takes a column of Yes/No values and returns the % of 'Yes',
# i.e. the share of employees who left, rounded to 1 decimal ('-> float' = it returns a decimal number).
def attrition_rate(s: pd.Series) -> float:
    # (s == "Yes") gives True/False per row; the mean of True/False is the share of True; x 100 makes it a %
    return round((s == "Yes").mean() * 100, 1)


# The main program: loads the IBM file and prints every check behind the findings F1-F12.
def main() -> None:
    # print wide tables on one line instead of wrapping them
    pd.set_option("display.width", 200)
    # read the CSV file into a DataFrame (a table in memory): 1,470 rows, 35 columns
    df = pd.read_csv(DATA_FILE)
    # first overview: size, empty cells, duplicate rows, and whether EmployeeNumber is unique
    print(f"Shape: {df.shape} | nulls: {df.isna().sum().sum()} | duplicate rows: {df.duplicated().sum()} "
          f"| EmployeeNumber unique: {df.EmployeeNumber.is_unique}")

    # for every column: numbers show their min and max, text shows its distinct values
    section("Column domains (all 35)")
    # loop over the 35 column names
    for c in df.columns:
        s = df[c]
        # is it a number column?
        if pd.api.types.is_numeric_dtype(s):
            print(f"{c:26s} int   distinct={s.nunique():5d}  min={s.min():6d} max={s.max():6d}")
        else:
            print(f"{c:26s} text  distinct={s.nunique():5d}  values={sorted(s.unique())}")

    # F1: which roles exist in which department (crosstab = a table of counts)
    section("F1  Department x JobRole (M:N because of Manager)")
    print(pd.crosstab(df.JobRole, df.Department))

    # F2: which job levels each role has
    section("F2  JobRole x JobLevel (allowed level range per role)")
    print(pd.crosstab(df.JobRole, df.JobLevel))

    # F3: the salary range of each job level
    section("F3  JobLevel -> MonthlyIncome band")
    print(df.groupby("JobLevel").MonthlyIncome.agg(["count", "min", "median", "max"]))

    # F4: the salary hike that goes with each performance rating
    section("F4  PerformanceRating -> PercentSalaryHike")
    print(df.groupby("PerformanceRating").PercentSalaryHike.agg(["min", "max", "mean", "count"]))

    # F5: do the rate columns follow the income? (a correlation close to 0 means no)
    section("F5  Rate columns vs MonthlyIncome (correlation)")
    print(df[["MonthlyIncome", "DailyRate", "HourlyRate", "MonthlyRate"]].corr().round(3))
    print("DailyRate*20 / MonthlyIncome:", (df.DailyRate * 20 / df.MonthlyIncome).describe().round(2).to_dict())

    # F6-F8: count the rows that break the tenure rules; each rule is a True/False column
    section("F6-F8  Tenure and history rules (violation / occurrence counts)")
    rules = {
        "YearsInCurrentRole > YearsAtCompany": df.YearsInCurrentRole > df.YearsAtCompany,
        "YearsSinceLastPromotion > YearsAtCompany": df.YearsSinceLastPromotion > df.YearsAtCompany,
        "YearsWithCurrManager > YearsAtCompany": df.YearsWithCurrManager > df.YearsAtCompany,
        "YearsAtCompany > TotalWorkingYears": df.YearsAtCompany > df.TotalWorkingYears,
        "Age - TotalWorkingYears < 18": (df.Age - df.TotalWorkingYears) < 18,
        "F7 NumCompaniesWorked=0 with prior experience": (df.NumCompaniesWorked == 0) & (df.TotalWorkingYears > df.YearsAtCompany),
        "F7 NumCompaniesWorked=1 without prior experience": (df.NumCompaniesWorked == 1) & (df.TotalWorkingYears == df.YearsAtCompany),
        "F8 JobLevel=1 but promoted": (df.JobLevel == 1) & (df.YearsSinceLastPromotion < df.YearsAtCompany),
    }
    # mask.sum() counts the True values = the rows that break the rule
    for name, mask in rules.items():
        print(f"  {name:55s} {int(mask.sum())}")

    # F9: stock options per marital status
    section("F9  MaritalStatus x StockOptionLevel")
    print(pd.crosstab(df.MaritalStatus, df.StockOptionLevel))

    # F10: attrition % per overtime and per job level, using attrition_rate() above
    section("F10 Attrition % by OverTime and JobLevel")
    print(df.groupby("OverTime").Attrition.apply(attrition_rate))
    print(df.groupby("JobLevel").Attrition.apply(attrition_rate))

    # F11: business travel per role (it shows no pattern)
    section("F11 JobRole x BusinessTravel (no dependency)")
    print(pd.crosstab(df.JobRole, df.BusinessTravel))

    # F12: job level vs rating (no link) and job level vs income (strong link)
    section("F12 JobLevel vs PerformanceRating (independent) and MonthlyIncome (dependent)")
    print((pd.crosstab(df.JobLevel, df.PerformanceRating, normalize="index") * 100).round(1))
    print("corr(JobLevel, PerformanceRating):", round(df.JobLevel.corr(df.PerformanceRating), 3))
    print("corr(JobLevel, MonthlyIncome):    ", round(df.JobLevel.corr(df.MonthlyIncome), 3))


# run main() only when this file is started directly, not when another file imports it
if __name__ == "__main__":
    main()

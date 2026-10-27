"""Profile the IBM HR Attrition dataset before designing the OLTP schema.

Measures column domains, functional dependencies and business rules. Every finding
(F1-F11) in docs/oltp_design.md is reproduced by this script.

Usage:
    python analysis/profile_dataset.py
"""
from pathlib import Path

import pandas as pd

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "raw" / "WA_Fn-UseC_-HR-Employee-Attrition.csv"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def attrition_rate(s: pd.Series) -> float:
    return round((s == "Yes").mean() * 100, 1)


def main() -> None:
    pd.set_option("display.width", 200)
    df = pd.read_csv(DATA_FILE)
    print(f"Shape: {df.shape} | nulls: {df.isna().sum().sum()} | duplicate rows: {df.duplicated().sum()} "
          f"| EmployeeNumber unique: {df.EmployeeNumber.is_unique}")

    section("Column domains (all 35)")
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            print(f"{c:26s} int   distinct={s.nunique():5d}  min={s.min():6d} max={s.max():6d}")
        else:
            print(f"{c:26s} text  distinct={s.nunique():5d}  values={sorted(s.unique())}")

    section("F1  Department x JobRole (M:N because of Manager)")
    print(pd.crosstab(df.JobRole, df.Department))

    section("F2  JobRole x JobLevel (allowed level range per role)")
    print(pd.crosstab(df.JobRole, df.JobLevel))

    section("F3  JobLevel -> MonthlyIncome band")
    print(df.groupby("JobLevel").MonthlyIncome.agg(["count", "min", "median", "max"]))

    section("F4  PerformanceRating -> PercentSalaryHike")
    print(df.groupby("PerformanceRating").PercentSalaryHike.agg(["min", "max", "mean", "count"]))

    section("F5  Rate columns vs MonthlyIncome (correlation)")
    print(df[["MonthlyIncome", "DailyRate", "HourlyRate", "MonthlyRate"]].corr().round(3))
    print("DailyRate*20 / MonthlyIncome:", (df.DailyRate * 20 / df.MonthlyIncome).describe().round(2).to_dict())

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
    for name, mask in rules.items():
        print(f"  {name:55s} {int(mask.sum())}")

    section("F9  MaritalStatus x StockOptionLevel")
    print(pd.crosstab(df.MaritalStatus, df.StockOptionLevel))

    section("F10 Attrition % by OverTime and JobLevel")
    print(df.groupby("OverTime").Attrition.apply(attrition_rate))
    print(df.groupby("JobLevel").Attrition.apply(attrition_rate))

    section("F11 JobRole x BusinessTravel (no dependency)")
    print(pd.crosstab(df.JobRole, df.BusinessTravel))


if __name__ == "__main__":
    main()

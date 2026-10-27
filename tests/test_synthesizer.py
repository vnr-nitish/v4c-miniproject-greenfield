"""Synthesizer tests: a small build (3,000 employees) must satisfy every rule the full build does.

No database is needed except for the reference-data and Singleton tests, which skip themselves
when MySQL is unavailable.
"""
from dataclasses import replace

import pandas as pd
import pytest

from src.synthesizer import reference as ref
from src.synthesizer.config import SynthesizerConfig
from src.synthesizer.run import SynthesisPipeline

N = 3000


@pytest.fixture(scope="module")
def build():
    config = replace(SynthesizerConfig(), n_employees=N, seed=11)
    pipeline = SynthesisPipeline(config)
    data, validator, source = pipeline.generate()
    return config, data, validator, source


def test_every_business_rule_holds(build):
    _, _, validator, _ = build
    failing = [(area, rule, v) for area, rule, v in validator.checks if v]
    assert not failing, failing


def test_ibm_tenure_columns_are_reproduced(build):
    _, _, validator, _ = build
    fidelity = dict(validator.fidelity)
    assert fidelity["YearsInCurrentRole"] == 100.0
    assert fidelity["YearsSinceLastPromotion"] == 100.0
    assert fidelity["YearsWithCurrManager"] >= 95.0


def test_population_mix_matches_the_source(build):
    _, data, _, source = build
    ibm = (source.Attrition == "Yes").mean()
    synthetic = (data.employees.attrition == "Yes").mean()
    assert abs(ibm - synthetic) < 0.005          # stratified sampling keeps the mix exact
    for col_ibm, col_syn in [("Department", "department"), ("JobLevel", "job_level")]:
        diff = (source[col_ibm].value_counts(normalize=True)
                - data.employees[col_syn].value_counts(normalize=True)).abs().max()
        assert diff < 0.01


def test_correlations_are_preserved(build):
    # Sampling noise shrinks with size: at 3,000 employees the largest difference is ~0.05, at the
    # full 100,000 it is ~0.01 (see docs/synthesis_report.md). The bound here suits the small build.
    _, _, validator, _ = build
    largest, mean = validator.correlation_difference()
    assert largest < 0.08 and mean < 0.02


def test_employee_codes_and_emails_are_unique(build):
    _, data, _, _ = build
    assert data.employees.employee_code.is_unique
    assert data.employees.email.is_unique
    assert data.employees.employee_code.str.fullmatch(r"EMP\d{6}").all()


def test_planted_issues_match_the_manifest(build):
    config, data, _, _ = build
    manifest = data.anomaly_manifest
    stg = data.staging["stg_employees"]
    assert manifest["duplicate_employee_rows"] == round(N * config.duplicate_employee_rate)
    assert stg.employee_code.duplicated().sum() == manifest["duplicate_employee_rows"]
    assert stg.gender.isin(["M", "F"]).sum() >= manifest["gender_codes"] > 0
    reviews = data.staging["stg_reviews"]
    assert reviews.duplicated(["employee_code", "review_year"]).sum() == manifest["duplicate_review_rows"]


def test_same_seed_gives_identical_data():
    config = replace(SynthesizerConfig(), n_employees=300, seed=5)
    first, _, _ = SynthesisPipeline(config).generate()
    second, _, _ = SynthesisPipeline(config).generate()
    for table in first.staging:
        pd.testing.assert_frame_equal(first.staging[table], second.staging[table])


# ----------------------------------------------------------------------------- with the database

def test_reference_mirror_matches_the_database(connection):
    cur = connection.cursor()
    cur.execute("SELECT department_id, department_name FROM hr_oltp.departments")
    assert dict(cur.fetchall()) == ref.DEPARTMENTS
    cur.execute("SELECT role_name, min_job_level_id, max_job_level_id FROM hr_oltp.job_roles")
    assert {r: (lo, hi) for r, lo, hi in cur.fetchall()} == ref.JOB_ROLES
    cur.execute("SELECT d.department_name, r.role_name FROM hr_oltp.department_job_roles djr "
                "JOIN hr_oltp.departments d USING (department_id) JOIN hr_oltp.job_roles r USING (job_role_id)")
    pairs = {(d, r) for d, r in cur.fetchall()}
    assert pairs == {(d, r) for d, roles in ref.DEPARTMENT_ROLES.items() for r in roles}
    cur.execute("SELECT job_level_id, level_title, salary_band_min, salary_band_max FROM hr_oltp.job_levels")
    assert {lv: (t, int(lo), int(hi)) for lv, t, lo, hi in cur.fetchall()} == ref.JOB_LEVELS
    cur.execute("SELECT rating_id, rating_label, hike_min_pct, hike_max_pct FROM hr_oltp.performance_rating_levels")
    assert {r: (lab, int(lo), int(hi)) for r, lab, lo, hi in cur.fetchall()} == ref.PERFORMANCE_RATINGS
    cur.execute("SELECT reason_name, is_voluntary FROM hr_oltp.exit_reasons")
    assert {n: bool(v) for n, v in cur.fetchall()} == ref.EXIT_REASONS
    cur.close()


def test_database_connection_is_a_shared_singleton(connection):
    from src.db_manager import DatabaseConnection
    from src.synthesizer.loader import StagingLoader

    a, b = DatabaseConnection(), DatabaseConnection()
    loader = StagingLoader()
    assert a is b                                   # one instance per class
    assert loader is StagingLoader() and loader is not a
    assert DatabaseConnection._pool is not None     # ... all sharing one pool
    assert a.fetch_one("SELECT 1 AS ok") == {"ok": 1}

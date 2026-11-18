"""Command-line entry point for the data synthesizer.

    python -m src.synthesizer.run                      # 100,000 employees, load into hr_staging
    python -m src.synthesizer.run --employees 5000 --no-load
    python -m src.synthesizer.run --seed 7 --report docs/synthesis_report.md
"""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from faker import Faker

from src.synthesizer.config import PROJECT_ROOT, SynthesizerConfig
from src.synthesizer.dataset import SyntheticDataset
from src.synthesizer.employees import EmployeeGenerator
from src.synthesizer.job_history import JobHistoryGenerator
from src.synthesizer.performance import ReviewGenerator, SurveyGenerator
from src.synthesizer.projects import AssignmentGenerator, ProjectGenerator
from src.synthesizer.quality import AnomalyInjector
from src.synthesizer.source import IBMSourceData
from src.synthesizer.validator import SynthesisValidator

logger = logging.getLogger("synthesizer")


# HOW 100,000 EMPLOYEES ARE MADE: this class runs the generators in order (see generate()). They share ONE
# seeded numpy generator and ONE seeded Faker, so seed 42 always gives exactly the same data.
class SynthesisPipeline:
    """Runs every generator in order and keeps the timings."""

    def __init__(self, config: SynthesizerConfig) -> None:
        self.config = config
        # ONE random number generator for all the data, seeded with 42
        self.rng = np.random.default_rng(config.seed)
        # Faker makes realistic Indian names and phone numbers (locale en_IN); seeding it makes them repeatable.
        self.faker = Faker(config.locale)
        self.faker.seed_instance(config.seed)
        self.timings: dict[str, float] = {}
        self.extras: dict = {}

    # Runs one step and remembers how long it took.
    def _timed(self, label: str, fn, *args):
        started = time.perf_counter()
        result = fn(*args)
        self.timings[label] = time.perf_counter() - started
        logger.info("%-32s %6.1fs", label, self.timings[label])
        return result

    # The order matters: each table needs the ones before it (history needs employees, reviews need the
    # history to find the reviewer, and so on).
    def generate(self) -> tuple[SyntheticDataset, "SynthesisValidator", object]:
        cfg, rng, fk = self.config, self.rng, self.faker
        # read and check the IBM file (1,470 rows)
        source = self._timed("load + validate IBM source", lambda: IBMSourceData(cfg.source_csv).frame)
        # table 1: 1,470 IBM rows -> 100,000 employees
        employees = self._timed("employees", EmployeeGenerator(cfg, rng, fk).generate, source)
        jh = JobHistoryGenerator(cfg, rng, fk)
        # table 2: each employee's job versions (promotions, transfers, manager changes): the SCD Type 2 history
        history = self._timed("job history + org chart", jh.generate, employees)
        self.extras = {"department_heads": jh.department_heads, "unassigned_segments": jh.unassigned_segments}
        # table 3: 2,000 projects, named by Faker
        projects = self._timed("projects", ProjectGenerator(cfg, rng, fk).generate, employees, history)
        ag = AssignmentGenerator(cfg, rng, fk)
        # table 4: who works on which project, when, and how much
        assignments = self._timed("assignments", ag.generate, employees, history, projects)
        # table 5: one engagement survey per employee per year worked
        surveys = self._timed("surveys", SurveyGenerator(cfg, rng, fk).generate, employees)
        # table 6: one performance review per employee per year worked
        reviews = self._timed("reviews", ReviewGenerator(cfg, rng, fk).generate, employees, history,
                              jh.department_heads)
        reviews["project_code"] = AssignmentGenerator.primary_projects(reviews, assignments)

        # put the 6 tables together
        data = SyntheticDataset(employees, history, surveys, reviews, projects, assignments)
        # check every rule (0 violations required) and compare with the IBM data
        validator = self._timed("validate", lambda: SynthesisValidator(source, data, cfg, self.extras).run())
        # rename the columns to the hr_staging layout
        data.build_staging()
        # plant small, counted data-quality problems for the ETL to clean
        data.anomaly_manifest = AnomalyInjector(cfg, rng).apply(data.staging)
        return data, validator, source


# The command 'python -m src.synthesizer.run': read the options, generate, save the CSV files,
# load the database, write the report.
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic HR dataset and load it into hr_staging.")
    parser.add_argument("--employees", type=int, default=SynthesizerConfig.n_employees)
    parser.add_argument("--seed", type=int, default=SynthesizerConfig.seed)
    parser.add_argument("--no-load", action="store_true", help="generate and validate only; skip the database load")
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "docs" / "synthesis_report.md")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    # the settings, with the number of employees and the seed from the command line
    config = replace(SynthesizerConfig(), n_employees=args.employees, seed=args.seed)
    pipeline = SynthesisPipeline(config)
    started = time.perf_counter()
    data, validator, _ = pipeline.generate()
    # save the staging tables as CSV files in data/synthesized
    pipeline._timed("write CSV files", data.to_csv, config.output_dir)

    # load into hr_staging unless --no-load was given
    if not args.no_load:
        from src.synthesizer.loader import StagingLoader      # needs the database only when loading
        batch_id = int(time.time())
        # insert everything into the hr_staging tables; Stage 1 of the ETL starts from there
        stats = pipeline._timed("load hr_staging", StagingLoader().load, data.staging, batch_id)
        for table, s in stats.items():
            logger.info("  %-16s %9d rows  %8.0f rows/s", table, s["rows"], s["rows_per_second"])
    pipeline.timings["total"] = time.perf_counter() - started

    # write docs/synthesis_report.md
    args.report.write_text(validator.to_markdown(data.row_counts(), pipeline.timings, data.anomaly_manifest),
                           encoding="utf-8")
    logger.info("rule violations: %d (must be 0) | report: %s", validator.total_violations, args.report)
    for name, pct in validator.fidelity:
        logger.info("  fidelity %-50s %6.2f %%", name, pct)


if __name__ == "__main__":
    main()

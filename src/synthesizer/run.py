"""Command-line entry point for the data synthesizer.

    python -m src.synthesizer.run                      # 100,000 employees, load into hr_staging
    python -m src.synthesizer.run --employees 5000 --no-load
    python -m src.synthesizer.run --seed 7 --report docs/synthesis_report.md

Pipeline: load IBM source -> employees -> job history -> projects -> assignments -> surveys ->
reviews -> validate -> plant data-quality issues -> CSV files -> load staging -> report.
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


class SynthesisPipeline:
    """Runs every generator in order and keeps the timings."""

    def __init__(self, config: SynthesizerConfig) -> None:
        self.config = config
        self.rng = np.random.default_rng(config.seed)
        self.faker = Faker(config.locale)
        self.faker.seed_instance(config.seed)
        self.timings: dict[str, float] = {}
        self.extras: dict = {}

    def _timed(self, label: str, fn, *args):
        started = time.perf_counter()
        result = fn(*args)
        self.timings[label] = time.perf_counter() - started
        logger.info("%-32s %6.1fs", label, self.timings[label])
        return result

    def generate(self) -> tuple[SyntheticDataset, "SynthesisValidator", object]:
        cfg, rng, fk = self.config, self.rng, self.faker
        source = self._timed("load + validate IBM source", lambda: IBMSourceData(cfg.source_csv).frame)
        employees = self._timed("employees", EmployeeGenerator(cfg, rng, fk).generate, source)
        jh = JobHistoryGenerator(cfg, rng, fk)
        history = self._timed("job history + org chart", jh.generate, employees)
        self.extras = {"department_heads": jh.department_heads, "unassigned_segments": jh.unassigned_segments}
        projects = self._timed("projects", ProjectGenerator(cfg, rng, fk).generate, employees, history)
        ag = AssignmentGenerator(cfg, rng, fk)
        assignments = self._timed("assignments", ag.generate, employees, history, projects)
        surveys = self._timed("surveys", SurveyGenerator(cfg, rng, fk).generate, employees)
        reviews = self._timed("reviews", ReviewGenerator(cfg, rng, fk).generate, employees, history,
                              jh.department_heads)
        reviews["project_code"] = AssignmentGenerator.primary_projects(reviews, assignments)

        data = SyntheticDataset(employees, history, surveys, reviews, projects, assignments)
        validator = self._timed("validate", lambda: SynthesisValidator(source, data, cfg, self.extras).run())
        data.build_staging()
        data.anomaly_manifest = AnomalyInjector(cfg, rng).apply(data.staging)
        return data, validator, source


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic HR dataset and load it into hr_staging.")
    parser.add_argument("--employees", type=int, default=SynthesizerConfig.n_employees)
    parser.add_argument("--seed", type=int, default=SynthesizerConfig.seed)
    parser.add_argument("--no-load", action="store_true", help="generate and validate only; skip the database load")
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "docs" / "synthesis_report.md")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    config = replace(SynthesizerConfig(), n_employees=args.employees, seed=args.seed)
    pipeline = SynthesisPipeline(config)
    started = time.perf_counter()
    data, validator, _ = pipeline.generate()
    pipeline._timed("write CSV files", data.to_csv, config.output_dir)

    if not args.no_load:
        from src.synthesizer.loader import StagingLoader      # needs the database only when loading
        batch_id = int(time.time())
        stats = pipeline._timed("load hr_staging", StagingLoader().load, data.staging, batch_id)
        for table, s in stats.items():
            logger.info("  %-16s %9d rows  %8.0f rows/s", table, s["rows"], s["rows_per_second"])
    pipeline.timings["total"] = time.perf_counter() - started

    args.report.write_text(validator.to_markdown(data.row_counts(), pipeline.timings, data.anomaly_manifest),
                           encoding="utf-8")
    logger.info("rule violations: %d (must be 0) | report: %s", validator.total_violations, args.report)
    for name, pct in validator.fidelity:
        logger.info("  fidelity %-50s %6.2f %%", name, pct)


if __name__ == "__main__":
    main()

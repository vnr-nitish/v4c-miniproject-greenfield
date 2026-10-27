"""AnomalyInjector — plants small, counted data-quality issues in the staging data (decision D21).

The exact counts go into a manifest, so the staging -> OLTP ETL can be tested to have fixed
every planted issue.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.synthesizer.config import SynthesizerConfig
from src.synthesizer.reference import DEPARTMENT_ALIASES


class AnomalyInjector:

    def __init__(self, config: SynthesizerConfig, rng: np.random.Generator) -> None:
        self._config = config
        self._rng = rng

    def apply(self, frames: dict[str, pd.DataFrame]) -> dict:
        cfg = self._config
        manifest = {}
        emp = frames["stg_employees"]
        hist = frames["stg_job_history"]

        manifest["gender_codes"] = self._gender_codes(emp, cfg.gender_code_rate)
        manifest["label_noise_employees"] = self._label_noise(emp, cfg.label_noise_rate)
        manifest["label_noise_job_history"] = self._label_noise(hist, cfg.label_noise_rate)

        # duplicates last, so a duplicate is an exact copy of a (possibly noisy) row
        dup_emp = self._rng.choice(len(emp), size=int(round(len(emp) * cfg.duplicate_employee_rate)), replace=False)
        frames["stg_employees"] = pd.concat([emp, emp.iloc[np.sort(dup_emp)]], ignore_index=True)
        manifest["duplicate_employee_rows"] = int(len(dup_emp))

        reviews = frames["stg_reviews"]
        dup_rev = np.sort(self._rng.choice(len(reviews), size=int(round(len(reviews) * cfg.duplicate_review_rate)),
                                           replace=False))
        resubmitted = reviews.iloc[dup_rev].copy()
        # a corrected resubmission: same employee and year, KPI score revised; the ETL keeps the latest
        resubmitted["kpi_score"] = np.clip(resubmitted.kpi_score + self._rng.normal(0, 3, len(dup_rev)), 0, 100).round(2)
        frames["stg_reviews"] = pd.concat([reviews, resubmitted], ignore_index=True)
        manifest["duplicate_review_rows"] = int(len(dup_rev))
        return manifest

    def _gender_codes(self, emp: pd.DataFrame, rate: float) -> int:
        rows = self._rng.random(len(emp)) < rate
        emp.loc[rows, "gender"] = emp.loc[rows, "gender"].str[0]          # 'Male' -> 'M', 'Female' -> 'F'
        return int(rows.sum())

    def _label_noise(self, frame: pd.DataFrame, rate: float) -> dict:
        """Case / whitespace variants of department and role names, and department aliases."""
        counts = {"upper_case": 0, "lower_case": 0, "extra_spaces": 0, "department_alias": 0}
        rows = np.flatnonzero(self._rng.random(len(frame)) < rate)
        kinds = self._rng.choice(list(counts), size=len(rows))
        for col in ("department", "job_role"):
            values = frame[col].to_numpy(dtype=object).copy()
            for r, kind in zip(rows, kinds):
                if col == "job_role" and kind == "department_alias":
                    continue
                v = values[r]
                if kind == "upper_case":
                    values[r] = v.upper()
                elif kind == "lower_case":
                    values[r] = v.lower()
                elif kind == "extra_spaces":
                    values[r] = f"  {v} "
                else:
                    values[r] = DEPARTMENT_ALIASES[v]
            frame[col] = values
        for kind in kinds:
            counts[kind] += 1
        return counts

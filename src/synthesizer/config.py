"""All synthesizer parameters in one place (docs/synthesizer_design.md)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SynthesizerConfig:
    # scale and reproducibility (D16)
    n_employees: int = 100_000
    seed: int = 42

    # timeline (D17)
    snapshot_date: date = date(2026, 6, 30)
    first_cycle_year: int = 2019
    last_cycle_year: int = 2025
    exit_window_start: date = date(2019, 1, 1)

    # identity (D20)
    locale: str = "en_IN"
    email_domain: str = "example.com"

    # jitter (D16)
    income_jitter: float = 0.07
    rate_jitter: float = 0.05

    # job history (section 5)
    transfer_share: float = 0.10

    # yearly history of reviews and surveys (section 6, D26): the chance per year, going back in
    # time, that a value moves to a neighbouring value (60 % of outstanding ratings stay outstanding,
    # 80 % of overtime stays overtime)
    rating_change: float = 0.40
    overtime_change: float = 0.20
    training_change: float = 0.30
    survey_change: float = 0.30

    # projects and assignments (section 6)
    n_projects: int = 2_000
    project_start_from: date = date(2018, 1, 1)
    late_project_share: float = 0.40

    # planted data-quality issues (D21)
    duplicate_employee_rate: float = 0.005
    label_noise_rate: float = 0.02
    gender_code_rate: float = 0.01
    duplicate_review_rate: float = 0.003

    # files
    source_csv: Path = field(default=PROJECT_ROOT / "data" / "raw" / "WA_Fn-UseC_-HR-Employee-Attrition.csv")
    output_dir: Path = field(default=PROJECT_ROOT / "data" / "synthesized")

    @property
    def cycle_years(self) -> list[int]:
        return list(range(self.first_cycle_year, self.last_cycle_year + 1))

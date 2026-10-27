"""Abstract base class shared by every generator, with vectorized date helpers."""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd
from faker import Faker

from src.synthesizer.config import SynthesizerConfig


class BaseGenerator(ABC):
    """Holds the shared configuration, seeded random generator and Faker instance.

    All generators draw randomness from the same seeded numpy Generator, so a full run is
    reproducible: the same seed always produces the same dataset.
    """

    def __init__(self, config: SynthesizerConfig, rng: np.random.Generator, faker: Faker) -> None:
        self._config = config
        self._rng = rng
        self._faker = faker

    @property
    def config(self) -> SynthesizerConfig:
        return self._config

    @abstractmethod
    def generate(self, *args, **kwargs) -> pd.DataFrame:
        """Produce this generator's table."""

    # ------------------------------------------------------------------ date helpers

    def random_dates(self, start, end) -> np.ndarray:
        """Uniform random dates between start and end inclusive (scalars or equal-length arrays)."""
        start = np.asarray(start, dtype="datetime64[D]")
        end = np.asarray(end, dtype="datetime64[D]")
        start, end = np.broadcast_arrays(start, end)
        span = np.maximum((end - start).astype(np.int64), 0)
        offsets = np.floor(self._rng.random(span.shape) * (span + 1)).astype(np.int64)
        return start + offsets.astype("timedelta64[D]")

    @staticmethod
    def years_before(ref, years, extra_days) -> np.ndarray:
        """ref − `years` calendar years − `extra_days` days.

        With 0 <= extra_days < 360 the completed-year difference back to ref is exactly `years`
        (the same rule as MySQL TIMESTAMPDIFF(YEAR, ...)), which is how IBM tenure values are
        turned into dates without changing them.
        """
        ref = pd.DatetimeIndex(np.asarray(ref, dtype="datetime64[D]"))
        year = ref.year.to_numpy() - np.asarray(years, dtype=np.int64)
        month = ref.month.to_numpy()
        day = ref.day.to_numpy()
        leap = (year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))
        day = np.where((month == 2) & (day == 29) & ~leap, 28, day)
        shifted = pd.to_datetime(pd.DataFrame({"year": year, "month": month, "day": day}))
        result = shifted.to_numpy().astype("datetime64[D]")
        return result - np.asarray(extra_days, dtype=np.int64).astype("timedelta64[D]")

    @staticmethod
    def full_years(start, end) -> np.ndarray:
        """Completed years between two date arrays — same rule as MySQL TIMESTAMPDIFF(YEAR, ...)."""
        s = pd.DatetimeIndex(np.asarray(start, dtype="datetime64[D]"))
        e = pd.DatetimeIndex(np.asarray(end, dtype="datetime64[D]"))
        before = (e.month < s.month) | ((e.month == s.month) & (e.day < s.day))
        return (e.year - s.year).to_numpy() - np.asarray(before, dtype=np.int64)

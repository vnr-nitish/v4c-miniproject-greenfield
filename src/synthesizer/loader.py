"""StagingLoader — bulk-loads the synthetic tables into hr_staging.

Server-side LOAD DATA LOCAL INFILE is disabled on this server (local_infile = OFF, Step 5 of the
journal) and on many managed cloud databases, so rows are sent with batched multi-row INSERTs
(executemany): the same code works locally and in the cloud.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from src.db_manager import DatabaseConnection

logger = logging.getLogger(__name__)


class StagingLoader(DatabaseConnection):
    """Inherits the Singleton connection pool from DatabaseConnection."""

    SCHEMA = "hr_staging"

    def load(self, frames: dict[str, pd.DataFrame], batch_id: int, batch_size: int = 5_000) -> dict[str, dict]:
        stats = {}
        for table, df in frames.items():
            started = time.perf_counter()
            rows = self._rows(df, batch_id)
            columns = ["batch_id", *df.columns]
            sql = (f"INSERT INTO {self.SCHEMA}.{table} ({', '.join(columns)}) "
                   f"VALUES ({', '.join(['%s'] * len(columns))})")
            with self.transaction() as cur:
                cur.execute(f"TRUNCATE TABLE {self.SCHEMA}.{table}")
                for i in range(0, len(rows), batch_size):
                    cur.executemany(sql, rows[i:i + batch_size])
            seconds = time.perf_counter() - started
            stats[table] = {"rows": len(rows), "seconds": seconds, "rows_per_second": len(rows) / max(seconds, 1e-9)}
            logger.info("loaded %-16s %9d rows in %5.1fs", table, len(rows), seconds)
        return stats

    @staticmethod
    def _rows(df: pd.DataFrame, batch_id: int) -> list[tuple]:
        """Convert to plain Python values: NaN/NaT -> None, timestamps -> date, numpy -> int/float."""
        out = df.copy()
        for col in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[col]):
                out[col] = out[col].dt.date
        out = out.astype(object).where(out.notna(), None)
        values = out.to_numpy()
        convert = np.vectorize(lambda v: v.item() if isinstance(v, np.generic) else v, otypes=[object])
        values = convert(values) if values.size else values
        return [(batch_id, *row) for row in values.tolist()]

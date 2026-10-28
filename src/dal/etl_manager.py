"""EtlManager — starts the stored-procedure ETL and reads its audit trail.

The transformation logic lives in the stored procedures (sql/07_oltp_procedures.sql,
sql/08_dw_procedures.sql); this class only starts them and reads hr_dw.etl_batch_log and
hr_dw.etl_rejected_rows, so the command line (scripts/run_etl.py) and the Streamlit app share
one implementation.
"""
from __future__ import annotations

from src.db_manager import DatabaseConnection


class EtlManager(DatabaseConnection):
    """Data-access manager for the two ETL stages and their logs (a Singleton, like every manager)."""

    def migrate_staging(self, full_reload: bool = False) -> int:
        """Stage 1: hr_staging -> hr_oltp. Returns the batch id.

        Refuses to run when hr_oltp already holds employees, unless full_reload is True — which
        also empties the warehouse, because the warehouse is derived from hr_oltp.
        """
        (batch_id,) = self.call_procedure("hr_oltp.sp_migrate_from_staging", (full_reload,), out_params=1)
        return batch_id

    def run_warehouse_etl(self) -> int:
        """Stage 2: hr_oltp -> hr_dw (dimensions, SCD Type 2, fact). Returns the batch id."""
        (batch_id,) = self.call_procedure("hr_dw.sp_run_etl", out_params=1)
        return batch_id

    _HEADER_SQL = ("SELECT batch_id, step_name AS process, status, rows_affected, message, started_at, "
                   "       finished_at, TIMESTAMPDIFF(SECOND, started_at, finished_at) AS seconds "
                   "FROM hr_dw.etl_batch_log ")

    def recent_batches(self, limit: int = 10) -> list[dict]:
        """Batch header rows, newest first (a batch header is the row whose log_id = batch_id)."""
        return self.fetch_all(self._HEADER_SQL + "WHERE log_id = batch_id ORDER BY batch_id DESC LIMIT %s", (limit,))

    def batch(self, batch_id: int) -> dict | None:
        """The header row of one batch: process, final status, rows loaded, duration."""
        return self.fetch_one(self._HEADER_SQL + "WHERE log_id = %s AND batch_id = %s", (batch_id, batch_id))

    def batch_steps(self, batch_id: int) -> list[dict]:
        """The step rows of one batch, in execution order."""
        return self.fetch_all(
            "SELECT step_name, status, rows_affected, message, "
            "       TIMESTAMPDIFF(SECOND, started_at, finished_at) AS seconds "
            "FROM hr_dw.etl_batch_log WHERE batch_id = %s AND log_id <> batch_id ORDER BY log_id", (batch_id,))

    def rejected_rows(self, batch_id: int, limit: int = 100) -> list[dict]:
        """Source rows the batch could not load, with the reason."""
        return self.fetch_all(
            "SELECT source_table, source_key, reason, rejected_at FROM hr_dw.etl_rejected_rows "
            "WHERE batch_id = %s ORDER BY reject_id LIMIT %s", (batch_id, limit))

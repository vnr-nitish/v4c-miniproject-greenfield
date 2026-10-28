"""Run the ETL from the command line and print each batch's log.

Usage:
    python scripts/run_etl.py                          # warehouse ETL: hr_oltp -> hr_dw (incremental)
    python scripts/run_etl.py --migrate                # first time: hr_staging -> hr_oltp, then the warehouse
    python scripts/run_etl.py --migrate --full-reload  # rebuild hr_oltp and hr_dw from hr_staging

Every step is logged in hr_dw.etl_batch_log and rows that could not be loaded go to
hr_dw.etl_rejected_rows; this script prints both. The logic itself lives in the stored
procedures (sql/07_oltp_procedures.sql, sql/08_dw_procedures.sql).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dal.etl_manager import EtlManager  # noqa: E402
from src.exceptions import HRAppError  # noqa: E402


def print_batch(etl: EtlManager, batch_id: int) -> None:
    header = etl.batch(batch_id)
    print(f"\nBatch {batch_id} - {header['process']}: {header['status']} in {header['seconds'] or 0} s "
          f"({header['rows_affected']:,} rows loaded)")
    for step in etl.batch_steps(batch_id):
        print(f"  {step['status']:<8} {step['step_name']:<44} {step['rows_affected']:>10,}  "
              f"{step['seconds']:>4} s  {step['message'] or ''}")
    rejects = etl.rejected_rows(batch_id, limit=20)
    if rejects:
        print(f"  rejected rows (first {len(rejects)}):")
        for r in rejects:
            print(f"    {r['source_table']:<32} {r['source_key']:<28} {r['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--migrate", action="store_true", help="run the staging -> OLTP migration first")
    parser.add_argument("--full-reload", action="store_true",
                        help="with --migrate: empty hr_oltp and hr_dw and load everything again")
    args = parser.parse_args()
    if args.full_reload and not args.migrate:
        parser.error("--full-reload only applies together with --migrate")

    etl = EtlManager()
    try:
        if args.migrate:
            print("Stage 1: migrating hr_staging -> hr_oltp ...")
            print_batch(etl, etl.migrate_staging(full_reload=args.full_reload))
        print("\nStage 2: loading hr_oltp -> hr_dw ...")
        print_batch(etl, etl.run_warehouse_etl())
    except HRAppError as err:
        latest = etl.recent_batches(limit=1)
        print(f"\nETL FAILED: {err}")
        if latest:
            print_batch(etl, latest[0]["batch_id"])
        sys.exit(1)


if __name__ == "__main__":
    main()

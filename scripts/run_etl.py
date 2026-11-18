"""Run the ETL from the command line and print each batch's log.

Usage:
    python scripts/run_etl.py                          # warehouse ETL: hr_oltp -> hr_dw (incremental)
    python scripts/run_etl.py --migrate                # first time: hr_staging -> hr_oltp, then the warehouse
    python scripts/run_etl.py --migrate --full-reload  # rebuild hr_oltp and hr_dw from hr_staging
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# let Python find the 'src' package: add the project folder to the import path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dal.etl_manager import EtlManager  # noqa: E402
from src.exceptions import HRAppError  # noqa: E402


# Prints one ETL batch: its status and time, each step with its row count, and the first rejected rows.
def print_batch(etl: EtlManager, batch_id: int) -> None:
    # the batch's header row: status, duration, rows loaded
    header = etl.batch(batch_id)
    print(f"\nBatch {batch_id} - {header['process']}: {header['status']} in {header['seconds'] or 0} s "
          f"({header['rows_affected']:,} rows loaded)")
    # one line per step of the batch
    for step in etl.batch_steps(batch_id):
        print(f"  {step['status']:<8} {step['step_name']:<44} {step['rows_affected']:>10,}  "
              f"{step['seconds']:>4} s  {step['message'] or ''}")
    # rows the ETL could not load, with the reason
    rejects = etl.rejected_rows(batch_id, limit=20)
    if rejects:
        print(f"  rejected rows (first {len(rejects)}):")
        for r in rejects:
            print(f"    {r['source_table']:<32} {r['source_key']:<28} {r['reason']}")


# The ETL from the command line: --migrate runs Stage 1 (staging -> OLTP) first, then Stage 2 always runs
# (OLTP -> warehouse).
def main() -> None:
    # the options: --migrate (run Stage 1 first) and --full-reload (rebuild everything)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--migrate", action="store_true", help="run the staging -> OLTP migration first")
    parser.add_argument("--full-reload", action="store_true",
                        help="with --migrate: empty hr_oltp and hr_dw and load everything again")
    args = parser.parse_args()
    if args.full_reload and not args.migrate:
        parser.error("--full-reload only applies together with --migrate")

    # the object that starts the ETL procedures and reads their logs
    etl = EtlManager()
    try:
        # Stage 1 only when asked: staging -> OLTP
        if args.migrate:
            print("Stage 1: migrating hr_staging -> hr_oltp ...")
            print_batch(etl, etl.migrate_staging(full_reload=args.full_reload))
        # Stage 2 always: OLTP -> warehouse
        print("\nStage 2: loading hr_oltp -> hr_dw ...")
        print_batch(etl, etl.run_warehouse_etl())
    # the ETL failed: print the message and the failed batch's log, then exit with an error code
    except HRAppError as err:
        latest = etl.recent_batches(limit=1)
        print(f"\nETL FAILED: {err}")
        if latest:
            print_batch(etl, latest[0]["batch_id"])
        sys.exit(1)


if __name__ == "__main__":
    main()

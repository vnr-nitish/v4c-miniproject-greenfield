"""Build the database schemas by running the numbered SQL scripts in sql/ in order.

The same command builds the local database and, later, the cloud database — only the
credentials in .env differ.

Usage:
    python scripts/setup_database.py             # run every sql/NN_*.sql file in order
    python scripts/setup_database.py --force     # allow rebuilding even if data is loaded
    python scripts/setup_database.py --only 03 07 08   # (re)install views and procedures only

Safety: the DDL scripts drop and recreate tables. If any selected script drops tables and a
loaded business table already contains rows, the script stops unless --force is given. View and
procedure scripts never drop tables, so they can be re-run at any time. The work is done by
SchemaManager (src/dal/schema_manager.py); this script only reads the options and prints progress.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dal.schema_manager import SchemaManager, SqlScript  # noqa: E402
from src.exceptions import HRAppError  # noqa: E402


def print_progress(script: SqlScript, statements: int, seconds: float) -> None:
    print(f"  OK  {script.name:<32} {statements:>3} statements  {seconds:5.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild even if business data is loaded")
    parser.add_argument("--only", nargs="+", metavar="NN",
                        help="run only the scripts with these number prefixes, e.g. --only 07 08")
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        schema = SchemaManager()
        scripts = schema.scripts(args.only)
        version, user = schema.server()
        print(f"Connected to MySQL {version} as {user}")
        total = schema.install(scripts, force=args.force, progress=print_progress)
    except HRAppError as err:
        sys.exit(f"\n{err}")
    print(f"Done: {len(scripts)} files, {total} statements in {time.perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()

"""Build the database schemas by running the numbered SQL scripts in sql/ in order.

Usage:
    python scripts/setup_database.py             # run every sql/NN_*.sql file in order
    python scripts/setup_database.py --force     # allow rebuilding even if data is loaded
    python scripts/setup_database.py --only 03 07 08   # (re)install views and procedures only
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# let Python find the 'src' package: add the project folder to the import path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dal.schema_manager import SchemaManager, SqlScript  # noqa: E402
from src.exceptions import HRAppError  # noqa: E402


# Prints one line per SQL file: its name, how many statements ran, and how long it took.
def print_progress(script: SqlScript, statements: int, seconds: float) -> None:
    print(f"  OK  {script.name:<32} {statements:>3} statements  {seconds:5.2f}s")


# Builds the database: python scripts/setup_database.py. With the local settings in .env it built the local
# MySQL database; with the Aiven settings in .env, the same command built the cloud database.
def main() -> None:
    # read the command-line options --force and --only
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild even if business data is loaded")
    parser.add_argument("--only", nargs="+", metavar="NN",
                        help="run only the scripts with these number prefixes, e.g. --only 07 08")
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        # connect (settings from .env) and list the SQL files to run
        schema = SchemaManager()
        scripts = schema.scripts(args.only)
        # show which MySQL server and user we are connected to
        version, user = schema.server()
        print(f"Connected to MySQL {version} as {user}")
        # run the files in order; print_progress() is called after each one
        total = schema.install(scripts, force=args.force, progress=print_progress)
    # a problem (wrong password, a failing statement): print it and stop
    except HRAppError as err:
        sys.exit(f"\n{err}")
    print(f"Done: {len(scripts)} files, {total} statements in {time.perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()

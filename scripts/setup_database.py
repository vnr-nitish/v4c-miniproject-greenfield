"""Build the database schemas by running the numbered SQL scripts in sql/ in order.

The same command builds the local database and, later, the cloud database — only the
credentials in .env differ.

Usage:
    python scripts/setup_database.py             # run every sql/NN_*.sql file in order
    python scripts/setup_database.py --force     # allow rebuilding even if data is loaded

Safety: the DDL scripts drop and recreate tables. If any table listed in PROTECTED_TABLES
already contains rows, the script stops unless --force is given.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"

# Tables holding loaded business data. Reference/lookup tables are not listed:
# rebuilding them is always safe because the scripts repopulate them.
PROTECTED_TABLES = [
    ("hr_staging", "stg_employees"),
    ("hr_oltp", "employees"),
    ("hr_oltp", "projects"),
    ("hr_dw", "dim_employee"),
    ("hr_dw", "fact_performance_reviews"),
]


def split_sql(script: str) -> list[str]:
    """Split a SQL script into statements.

    Honours quoted strings, -- / # / /* */ comments, and the client-side
    DELIMITER directive used around stored-procedure bodies.
    """
    statements: list[str] = []
    buf: list[str] = []
    delimiter = ";"
    quote: str | None = None
    i, n = 0, len(script)

    while i < n:
        ch = script[i]

        if quote:                                   # inside a quoted string / identifier
            buf.append(ch)
            if ch == "\\" and quote != "`" and i + 1 < n:
                buf.append(script[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        line_start = script.rfind("\n", 0, i) + 1
        if not script[line_start:i].strip() and script[i:i + 9].upper() == "DELIMITER" \
                and i + 9 < n and script[i + 9] in " \t":
            end = script.find("\n", i)
            end = n if end == -1 else end
            delimiter = script[i + 9:end].strip()
            i = end + 1
            continue

        if script.startswith("--", i) and (i + 2 >= n or script[i + 2] in " \t\r\n") or ch == "#":
            end = script.find("\n", i)
            i = n if end == -1 else end + 1
            buf.append("\n")
            continue
        if script.startswith("/*", i):
            end = script.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if script.startswith(delimiter, i):
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += len(delimiter)
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def connect():
    load_dotenv(ROOT / ".env")
    try:
        return mysql.connector.connect(
            host=os.environ["DB_HOST"],
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            autocommit=True,
        )
    except KeyError as missing:
        sys.exit(f"Missing setting {missing} in .env (see .env.example).")
    except mysql.connector.Error as err:
        sys.exit(f"Could not connect to MySQL: {err}")


def loaded_tables(cur) -> list[str]:
    """Return the protected tables that exist and contain at least one row."""
    found = []
    for schema, table in PROTECTED_TABLES:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        )
        if not cur.fetchone()[0]:
            continue
        cur.execute(f"SELECT EXISTS (SELECT 1 FROM `{schema}`.`{table}`)")
        if cur.fetchone()[0]:
            found.append(f"{schema}.{table}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild even if business data is loaded")
    args = parser.parse_args()

    files = sorted(SQL_DIR.glob("[0-9][0-9]_*.sql"))
    if not files:
        sys.exit(f"No numbered .sql files found in {SQL_DIR}")

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT VERSION(), CURRENT_USER()")
    version, user = cur.fetchone()
    print(f"Connected to MySQL {version} as {user}")

    data = loaded_tables(cur)
    if data and not args.force:
        sys.exit("Refusing to rebuild: these tables contain data -> " + ", ".join(data)
                 + "\nRe-run with --force if you really want to drop and recreate them.")

    total = 0
    started = time.perf_counter()
    for path in files:
        statements = split_sql(path.read_text(encoding="utf-8"))
        t0 = time.perf_counter()
        for stmt in statements:
            try:
                cur.execute(stmt)
                if cur.with_rows:
                    cur.fetchall()
            except mysql.connector.Error as err:
                first_line = stmt.strip().splitlines()[0][:100]
                sys.exit(f"\nFAILED in {path.name}\n  statement: {first_line} ...\n  error: {err}")
        total += len(statements)
        print(f"  OK  {path.name:<32} {len(statements):>3} statements  {time.perf_counter() - t0:5.2f}s")

    print(f"Done: {len(files)} files, {total} statements in {time.perf_counter() - started:.2f}s")
    conn.close()


if __name__ == "__main__":
    main()

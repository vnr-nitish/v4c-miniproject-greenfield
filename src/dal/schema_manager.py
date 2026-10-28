"""SchemaManager — builds and updates the database schemas from the numbered scripts in sql/.

The same code builds the local database and, later, the cloud database — only the credentials
in .env differ. Like every manager it inherits from DatabaseConnection, so no part of the
application connects to MySQL outside the shared Singleton pool.

Safety: the DDL scripts drop and recreate tables. If any selected script drops tables and a table
in PROTECTED_TABLES already contains rows, install() refuses unless force=True. View and
procedure scripts never drop tables, so they can be re-run at any time.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Callable, Sequence

import mysql.connector

from src.db_manager import PROJECT_ROOT, DatabaseConnection
from src.exceptions import BusinessRuleError, DatabaseError, NotFoundError

SQL_DIR = PROJECT_ROOT / "sql"

# Tables holding loaded business data. Reference / lookup tables are not listed: rebuilding them
# is always safe because the scripts repopulate them.
PROTECTED_TABLES = [
    ("hr_staging", "stg_employees"),
    ("hr_oltp", "employees"),
    ("hr_oltp", "projects"),
    ("hr_dw", "dim_employee"),
    ("hr_dw", "fact_performance_reviews"),
]


@dataclass(frozen=True)
class SqlScript:
    """One numbered SQL file, split into the statements MySQL executes one at a time."""

    path: Path
    text: str

    @classmethod
    def load(cls, path: Path) -> "SqlScript":
        return cls(path, path.read_text(encoding="utf-8"))

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def number(self) -> str:
        return self.path.name[:2]

    @property
    def drops_tables(self) -> bool:
        """True if the script drops tables (the view and procedure scripts do not)."""
        return re.search(r"\bDROP\s+TABLE\b", self.text, re.IGNORECASE) is not None

    @cached_property
    def statements(self) -> list[str]:
        return self.split(self.text)

    @staticmethod
    def split(script: str) -> list[str]:
        """Split a SQL script into statements.

        Honours quoted strings, -- / # / /* */ comments, and the client-side DELIMITER directive
        used around stored-procedure bodies. Comments stay in the statement text, so stored
        procedures keep their comments on the server; a chunk that contains only comments is not
        a statement.
        """
        statements: list[str] = []
        buf: list[str] = []
        has_code = False
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
                end = n if end == -1 else end + 1
                buf.append(script[i:end])
                i = end
                continue
            if script.startswith("/*", i):
                end = script.find("*/", i + 2)
                end = n if end == -1 else end + 2
                buf.append(script[i:end])
                i = end
                continue
            if ch in ("'", '"', "`"):
                quote = ch
                buf.append(ch)
                has_code = True
                i += 1
                continue
            if script.startswith(delimiter, i):
                if has_code:
                    statements.append("".join(buf).strip())
                buf, has_code = [], False
                i += len(delimiter)
                continue

            buf.append(ch)
            has_code = has_code or not ch.isspace()
            i += 1

        if has_code:
            statements.append("".join(buf).strip())
        return statements

    @staticmethod
    def first_code_line(statement: str) -> str:
        """The first line of a statement that is not a comment (for error messages)."""
        for line in statement.splitlines():
            if line.strip() and not line.lstrip().startswith(("--", "#", "/*")):
                return line.strip()
        return statement.strip().splitlines()[0]


class SchemaManager(DatabaseConnection):
    """Runs the numbered SQL scripts: schemas, tables, reference data, views, procedures."""

    def scripts(self, only: Sequence[str] | None = None, directory: Path = SQL_DIR) -> list[SqlScript]:
        """The numbered scripts in order — all of them, or those whose number is in `only`."""
        paths = sorted(directory.glob("[0-9][0-9]_*.sql"))
        if only:
            paths = [p for p in paths if p.name[:2] in only]
        if not paths:
            raise NotFoundError(f"No matching numbered .sql files found in {directory}")
        return [SqlScript.load(p) for p in paths]

    def server(self) -> tuple[str, str]:
        """(MySQL version, the account connected)."""
        row = self.fetch_one("SELECT VERSION(), CURRENT_USER()", dictionary=False)
        return row[0], row[1]

    def loaded_tables(self) -> list[str]:
        """The protected tables that exist and contain at least one row."""
        found = []
        with self.transaction() as cur:
            for schema, table in PROTECTED_TABLES:
                cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_name = %s", (schema, table))
                if not cur.fetchone()[0]:
                    continue
                cur.execute(f"SELECT EXISTS (SELECT 1 FROM `{schema}`.`{table}`)")
                if cur.fetchone()[0]:
                    found.append(f"{schema}.{table}")
        return found

    def install(self, scripts: Sequence[SqlScript], force: bool = False,
                progress: Callable[[SqlScript, int, float], None] | None = None) -> int:
        """Run the scripts in order; returns the number of statements executed.

        Refuses — before running anything — when a script would drop tables that hold data,
        unless force is True. `progress(script, statements, seconds)` is called after each file.
        """
        if not force and any(script.drops_tables for script in scripts):
            data = self.loaded_tables()
            if data:
                raise BusinessRuleError("Refusing to rebuild: these tables contain data -> " + ", ".join(data)
                                        + "\nRe-run with --force if you really want to drop and recreate them.")
        total = 0
        for script in scripts:
            started = time.perf_counter()
            count = self.run_script(script)
            total += count
            if progress:
                progress(script, count, time.perf_counter() - started)
        return total

    def run_script(self, script: SqlScript) -> int:
        """Execute one script's statements on one connection and commit (DDL commits implicitly).
        A failing statement stops the script with its file name, first line and MySQL's error."""
        with self.transaction() as cur:
            for statement in script.statements:
                try:
                    cur.execute(statement)
                    if cur.with_rows:
                        cur.fetchall()
                except mysql.connector.Error as err:
                    raise DatabaseError(f"FAILED in {script.name}\n  statement: "
                                        f"{SqlScript.first_code_line(statement)[:100]} ...\n  error: {err}") from err
        return len(script.statements)

"""Database access foundation: settings, and the DatabaseConnection Singleton.

Design (docs/synthesizer_design.md D22, docs/backend_design.md):
- The whole application shares ONE MySQL connection pool, created on first use.
- DatabaseConnection follows the Singleton pattern per class: every class that inherits from it
  (the staging loader and the data-access managers) has exactly one instance, and all of them
  draw connections from the same shared pool.
- Connections are always returned to the pool and transactions are always committed or rolled
  back, via context managers.
- MySQL errors are translated into the application's exceptions (src/exceptions.py) with a
  message a user can act on, e.g. "This e-mail address is already used by another employee".
- rollback_session() pins every manager to one connection whose work is always rolled back —
  used by the tests, so they can exercise the real data without ever changing it.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Sequence

import mysql.connector
import pandas as pd
from dotenv import load_dotenv
from mysql.connector import pooling

from src.exceptions import (BusinessRuleError, ConfigurationError, DatabaseError, DuplicateError, HRAppError,
                            ValidationError)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_PROCEDURE_NAME = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)?")

# Readable messages for the constraints a user can run into, keyed by (MySQL error, constraint):
# a foreign key means "does not exist" on insert (1452) but "still in use" on delete (1451).
# Constraints not listed fall back to MySQL's own text.
CONSTRAINT_MESSAGES = {
    (1062, "uq_employees_code"): "This employee code already exists — please try again",
    (1062, "uq_employees_email"): "This e-mail address is already used by another employee",
    (1062, "uq_ejh_employee_start"): "The employee already has a job change on that date",
    (1062, "uq_projects_code"): "This project code already exists — please try again",
    (1062, "uq_assignments_emp_proj_start"): "The employee is already assigned to this project from that date",
    (1062, "uq_reviews_employee_year"): "The employee already has a review for that year",
    (1062, "uq_surveys_employee_year"): "The employee already has a survey for that year",
    (1452, "fk_reviews_employee"): "The selected employee does not exist",
    (1452, "fk_reviews_reviewer"): "The selected reviewer does not exist",
    (1452, "fk_reviews_project"): "The selected project does not exist",
    (1452, "fk_projects_department"): "The selected department does not exist",
    (1452, "fk_projects_manager"): "The selected project manager does not exist",
    (1452, "fk_assignments_employee"): "The selected employee does not exist",
    (1452, "fk_assignments_project"): "The selected project does not exist",
    (1451, "fk_assignments_project"): "The project still has assignments — cancel it instead of deleting it",
    (1451, "fk_reviews_project"): "The project is referenced by reviews — cancel it instead of deleting it",
    (3819, "ck_reviews_date_in_cycle"): "The review date must fall in the review year or the year after",
    (3819, "ck_reviews_not_self"): "An employee cannot review themselves",
    (3819, "ck_assignments_period"): "An assignment cannot end before it starts",
    (3819, "ck_projects_closed"): "A project has an actual end date exactly when it is Completed or Cancelled",
    (3819, "ck_employees_adult_hire"): "An employee must be at least 18 on the hire date",
}

# Values MySQL refuses for a column (NULL in NOT NULL, too long, out of range, wrong type/date)
_INPUT_ERRORS = {1048, 1264, 1265, 1292, 1366, 1406}

_pinned = threading.local()           # the connection of an active rollback_session, per thread


def translate_error(err: mysql.connector.Error) -> HRAppError:
    """Turn a MySQL error into the application exception a caller can handle.

    1644 SIGNAL from a procedure -> BusinessRuleError (its message is already written for users)
    1062 duplicate key           -> DuplicateError
    1451 / 1452 foreign key      -> BusinessRuleError
    3819 CHECK constraint        -> ValidationError
    1048, 1264, 1406 … bad value -> ValidationError
    anything else                -> DatabaseError
    """
    message = err.msg or str(err)
    friendly = next((text for (errno, name), text in CONSTRAINT_MESSAGES.items()
                     if errno == err.errno and name in message), message)
    if err.errno == 1644:
        return BusinessRuleError(message)
    if err.errno == 1062:
        return DuplicateError(friendly)
    if err.errno in (1451, 1452):
        return BusinessRuleError(friendly)
    if err.errno == 3819 or err.errno in _INPUT_ERRORS:
        return ValidationError(friendly)
    return DatabaseError(f"{err.errno}: {message}" if err.errno else message)


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection settings. Loaded from the environment (.env locally, secrets in the cloud)."""

    host: str
    port: int
    user: str
    password: str
    pool_size: int = 5

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "DatabaseSettings":
        load_dotenv(env_file or PROJECT_ROOT / ".env")
        try:
            return cls(
                host=os.environ["DB_HOST"],
                port=int(os.getenv("DB_PORT", "3306")),
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
                pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            )
        except KeyError as missing:
            raise ConfigurationError(f"Missing database setting {missing} (see .env.example)") from None
        except ValueError as err:
            raise ConfigurationError(f"Invalid database setting: {err}") from None


class DatabaseConnection:
    """Singleton gateway to MySQL backed by one shared connection pool."""

    _instances: dict[type, "DatabaseConnection"] = {}
    _pool: pooling.MySQLConnectionPool | None = None
    _lock = threading.RLock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "DatabaseConnection":
        with DatabaseConnection._lock:
            if cls not in DatabaseConnection._instances:
                DatabaseConnection._instances[cls] = super().__new__(cls)
            return DatabaseConnection._instances[cls]

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        with DatabaseConnection._lock:
            if DatabaseConnection._pool is None:
                DatabaseConnection._pool = self._create_pool(settings or DatabaseSettings.from_env())

    # ------------------------------------------------------------------ pool management

    @staticmethod
    def _create_pool(settings: DatabaseSettings) -> pooling.MySQLConnectionPool:
        try:
            pool = pooling.MySQLConnectionPool(
                pool_name="hr_app_pool",
                pool_size=settings.pool_size,
                pool_reset_session=True,
                host=settings.host,
                port=settings.port,
                user=settings.user,
                password=settings.password,
                autocommit=False,
            )
        except mysql.connector.Error as err:
            raise DatabaseError(f"Could not connect to MySQL at {settings.host}:{settings.port}: {err}") from err
        logger.info("Connection pool created (%s connections) for %s@%s",
                    settings.pool_size, settings.user, settings.host)
        return pool

    @classmethod
    def reset(cls) -> None:
        """Forget the pool and all instances (used by tests and when settings change)."""
        with DatabaseConnection._lock:
            DatabaseConnection._pool = None
            DatabaseConnection._instances.clear()

    # ------------------------------------------------------------------ connections and transactions

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Borrow a pooled connection; it is always returned to the pool.

        Inside a rollback_session the session's pinned connection is used instead.
        """
        pinned = getattr(_pinned, "connection", None)
        if pinned is not None:
            yield pinned
            return
        try:
            conn = DatabaseConnection._pool.get_connection()
        except mysql.connector.Error as err:
            raise DatabaseError(f"No database connection available: {err}") from err
        try:
            yield conn
        finally:
            conn.close()            # returns the connection to the pool

    @contextmanager
    def transaction(self, dictionary: bool = False) -> Iterator[Any]:
        """Run a unit of work: commit on success, roll back on any error.

        MySQL errors are re-raised as application exceptions (translate_error). Inside a
        rollback_session the unit of work is a SAVEPOINT instead, so a failed step is undone
        without losing the session's earlier steps — and nothing is ever committed.
        """
        with self.connection() as conn:
            pinned = conn is getattr(_pinned, "connection", None)
            cursor = conn.cursor(dictionary=dictionary)
            savepoint = None
            if pinned:
                _pinned.depth += 1
                savepoint = f"dal_unit_{_pinned.depth}"
                cursor.execute(f"SAVEPOINT {savepoint}")
            try:
                yield cursor
                if savepoint:
                    cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    conn.commit()
            except mysql.connector.Error as err:
                self._undo(conn, cursor, savepoint)
                raise translate_error(err) from err
            except Exception:
                self._undo(conn, cursor, savepoint)
                raise
            finally:
                if savepoint:
                    _pinned.depth -= 1
                cursor.close()

    @staticmethod
    def _undo(conn: Any, cursor: Any, savepoint: str | None) -> None:
        try:
            if savepoint:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            else:
                conn.rollback()
        except mysql.connector.Error as err:          # the original error is the one that matters
            logger.warning("Rollback failed: %s", err)

    @staticmethod
    def in_rollback_session() -> bool:
        return getattr(_pinned, "connection", None) is not None

    @classmethod
    @contextmanager
    def rollback_session(cls) -> Iterator[None]:
        """Pin every manager to ONE connection and roll all of its work back at the end.

        Later calls see the earlier, uncommitted changes, but nothing is ever saved — the tests
        use this to exercise the managers against the real data (docs/backend_design.md, B6).
        Do not run the ETL entry points inside it: they commit by design.
        """
        if cls.in_rollback_session():
            raise RuntimeError("A rollback session is already active in this thread")
        with DatabaseConnection().connection() as conn:
            conn.rollback()
            conn.start_transaction()
            _pinned.connection, _pinned.depth = conn, 0
            try:
                yield
            finally:
                _pinned.connection = None
                conn.rollback()

    # ------------------------------------------------------------------ convenience helpers

    def fetch_all(self, sql: str, params: Sequence[Any] | dict | None = None,
                  dictionary: bool = True) -> list:
        with self.transaction(dictionary=dictionary) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def fetch_one(self, sql: str, params: Sequence[Any] | dict | None = None,
                  dictionary: bool = True) -> Any:
        with self.transaction(dictionary=dictionary) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_frame(self, sql: str, params: Sequence[Any] | dict | None = None) -> pd.DataFrame:
        """Run a query and return a DataFrame (DECIMAL columns as floats, ready for charts)."""
        with self.transaction() as cur:
            return self.frame_in(cur, sql, params)

    @staticmethod
    def frame_in(cursor: Any, sql: str, params: Sequence[Any] | dict | None = None) -> pd.DataFrame:
        """Like fetch_frame, on an open cursor (inside the caller's transaction).

        DECIMAL columns become numbers: whole-number DECIMALs (scale 0, e.g. SUM of integers)
        become integers — nullable Int64 when the column has NULLs — and the rest floats.
        """
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        columns = list(cursor.column_names)
        frame = pd.DataFrame([list(r.values()) if isinstance(r, dict) else list(r) for r in rows], columns=columns)
        for column in frame.columns:
            values = frame[column].dropna()
            if not (len(values) and isinstance(values.iloc[0], Decimal)):
                continue
            numbers = pd.to_numeric(frame[column].astype(float))
            if all(v.as_tuple().exponent >= 0 for v in values):
                frame[column] = numbers.astype("Int64" if len(values) < len(frame) else "int64")
            else:
                frame[column] = numbers
        return frame

    def execute(self, sql: str, params: Sequence[Any] | dict | None = None) -> int:
        """Execute one write statement in its own transaction; returns the affected row count."""
        with self.transaction() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    @staticmethod
    def call_in(cursor: Any, name: str, args: Sequence[Any] = (), out_params: int = 0) -> tuple:
        """CALL a stored procedure on an open cursor (inside the caller's transaction).

        IN arguments are bound as query parameters; OUT parameters are read back through
        session variables on the same connection.
        """
        if not _PROCEDURE_NAME.fullmatch(name):
            raise ValueError(f"Invalid procedure name: {name!r}")
        outs = [f"@out{i}" for i in range(out_params)]
        cursor.execute(f"CALL {name}({', '.join(['%s'] * len(args) + outs)})", tuple(args))
        if not outs:
            return ()
        cursor.execute(f"SELECT {', '.join(outs)}")
        row = cursor.fetchone()
        values = row.values() if isinstance(row, dict) else row
        return tuple(v.decode() if isinstance(v, (bytes, bytearray)) else v for v in values)

    def call_procedure(self, name: str, args: Sequence[Any] = (), out_params: int = 0) -> tuple:
        """CALL a stored procedure in its own transaction and return its OUT parameter values.

        The ETL entry points commit themselves; for every other procedure the commit of this
        transaction applies.
        """
        with self.transaction() as cur:
            return self.call_in(cur, name, args, out_params)

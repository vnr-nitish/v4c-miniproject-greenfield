from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence, TypeVar

import mysql.connector
import pandas as pd
from dotenv import load_dotenv
from mysql.connector import pooling

from src.exceptions import (BusinessRuleError, ConfigurationError, DatabaseError, DuplicateError, HRAppError,
                            PoolBusyError, ValidationError)

logger = logging.getLogger(__name__)

# the project folder (one level above src/)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_PROCEDURE_NAME = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)?")

# Readable messages keyed by (MySQL error, constraint): 1452 = "does not exist" on insert, 1451 = "still in
# use" on delete. Constraints not listed fall back to MySQL's own text.
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

# The constraint a MySQL message names: "for key 'employees.uq_employees_email'" (1062),
# "CONSTRAINT `fk_reviews_project` FOREIGN KEY" (1451 / 1452), "Check constraint 'ck_…'" (3819)
_CONSTRAINT_NAME = re.compile(r"(?:for key '(?:\w+\.)?|CONSTRAINT `|[Cc]heck constraint ')(\w+)")

# Connecting: can't reach the server, connection lost, secure handshake failed
_NETWORK_ERRORS = {2003, 2006, 2013, 2026, 2055}
_CONNECT_ATTEMPTS = 4
_CONNECT_PAUSE_SECONDS = 3

_pinned = threading.local()           # the connection of an active rollback_session, per thread
_reads = threading.local()            # the connection of an active read_session, per thread

T = TypeVar("T")


# Error handling: every MySQL error becomes one of the application's exceptions with a message a user
# understands (for example, a duplicate e-mail becomes DuplicateError).
def translate_error(err: mysql.connector.Error) -> HRAppError:
    """Turn a MySQL error into the application exception a caller can handle.

    1644 SIGNAL from a procedure -> BusinessRuleError (its message is already written for users)
    1062 duplicate key           -> DuplicateError
    1451 / 1452 foreign key      -> BusinessRuleError
    3819 CHECK constraint        -> ValidationError
    1048, 1264, 1406 … bad value -> ValidationError
    anything else                -> DatabaseError
    The constraint named in the message, if any, is kept in the exception's `constraint`.
    """
    # MySQL's own error text
    message = err.msg or str(err)
    # find the constraint's name in that text, e.g. uq_employees_email
    match = _CONSTRAINT_NAME.search(message)
    constraint = match.group(1) if match else None
    # our readable message for that constraint, or MySQL's text if we have none
    friendly = CONSTRAINT_MESSAGES.get((err.errno, constraint), message)
    # choose the exception class from MySQL's error number (see the list in the docstring)
    if err.errno == 1644:
        error: HRAppError = BusinessRuleError(message)
    elif err.errno == 1062:
        error = DuplicateError(friendly)
    elif err.errno in (1451, 1452):
        error = BusinessRuleError(friendly)
    elif err.errno == 3819 or err.errno in _INPUT_ERRORS:
        error = ValidationError(friendly)
    else:
        error = DatabaseError(f"{err.errno}: {message}" if err.errno else message)
    error.constraint = constraint
    return error


# Where the database is: read from .env on a laptop (local MySQL or Aiven) and from Streamlit Secrets on
# the live site. The code is the same everywhere; only these settings differ.
@dataclass(frozen=True)
class DatabaseSettings:
    """Connection settings. Loaded from the environment (.env locally, secrets in the cloud)."""

    host: str
    port: int
    user: str
    password: str
    pool_size: int = 5
    pool_wait_seconds: float = 60.0          # how long a request waits when every connection is busy

    # Reads the settings: from the .env file (or the file named in HR_ENV_FILE) and the environment.
    # On Streamlit Cloud they come from the app's Secrets. Missing ones raise ConfigurationError.
    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "DatabaseSettings":
        # HR_ENV_FILE picks another settings file (e.g. .env.local); the file wins over stale shell variables
        load_dotenv(env_file or os.environ.get("HR_ENV_FILE") or PROJECT_ROOT / ".env", override=True)
        try:
            return cls(
                host=os.environ["DB_HOST"],
                port=int(os.getenv("DB_PORT", "3306")),
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
                pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
                pool_wait_seconds=float(os.getenv("DB_POOL_WAIT", "60")),
            )
        except KeyError as missing:
            raise ConfigurationError(f"Missing database setting {missing} (see .env.example)") from None
        except ValueError as err:
            raise ConfigurationError(f"Invalid database setting: {err}") from None


# OOP (Singleton + Inheritance): the ONE gateway to MySQL. Every manager and the StagingLoader inherit from
# this class, so they all share one connection pool and the same transaction and error handling.
class DatabaseConnection:
    """Singleton gateway to MySQL backed by one shared connection pool."""

    # one object per class: the Singleton's store
    _instances: dict[type, "DatabaseConnection"] = {}
    # the one shared connection pool, created on first use
    _pool: pooling.MySQLConnectionPool | None = None
    _pool_wait: float = 60.0
    # a lock, so two visitors starting at the same moment can't create two pools
    _lock = threading.RLock()

    # The Singleton pattern: __new__ runs before __init__. If an object of this class already exists it is
    # returned instead of a new one, so EmployeeManager() is always the same object.
    def __new__(cls, *args: Any, **kwargs: Any) -> "DatabaseConnection":
        with DatabaseConnection._lock:
            # first time for this class: create the object; every later time: reuse it
            if cls not in DatabaseConnection._instances:
                DatabaseConnection._instances[cls] = super().__new__(cls)
            return DatabaseConnection._instances[cls]

    # Every manager's constructor makes sure the shared pool exists.
    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self._shared_pool(settings)

    # ------------------------------------------------------------------ pool management

    @staticmethod
    def _shared_pool(settings: DatabaseSettings | None = None) -> pooling.MySQLConnectionPool:
        """The one pool, created on first use — and again if reset() removed it while another
        visitor's page was still running."""
        with DatabaseConnection._lock:
            if DatabaseConnection._pool is None:
                settings = settings or DatabaseSettings.from_env()
                DatabaseConnection._pool = DatabaseConnection._create_pool(settings)
                DatabaseConnection._pool_wait = settings.pool_wait_seconds
            return DatabaseConnection._pool

    # A connection pool keeps a few connections open and lends them out; opening a new connection to the
    # cloud database for every query would be slow.
    @staticmethod
    def _create_pool(settings: DatabaseSettings) -> pooling.MySQLConnectionPool:
        """Network hiccups while connecting are seen on the cloud database and pass within seconds,
        so they are tried again. Anything else, such as a wrong password, is reported at once."""
        # try to connect up to 4 times
        for attempt in range(1, _CONNECT_ATTEMPTS + 1):
            try:
                pool = pooling.MySQLConnectionPool(
                    pool_name="hr_app_pool",
                    pool_size=settings.pool_size,
                    pool_reset_session=False,        # connection() resets after writes; reads skip the 4 round trips
                    host=settings.host,
                    port=settings.port,
                    user=settings.user,
                    password=settings.password,
                    autocommit=False,
                )
                break
            except mysql.connector.Error as err:
                # not a network hiccup, or the last try: give up with a clear error
                if err.errno not in _NETWORK_ERRORS or attempt == _CONNECT_ATTEMPTS:
                    raise DatabaseError(f"Could not connect to MySQL at {settings.host}:{settings.port}: {err}") from err
                logger.warning("Connecting failed (attempt %s of %s): %s", attempt, _CONNECT_ATTEMPTS, err)
                # wait 3 seconds, then try again
                time.sleep(_CONNECT_PAUSE_SECONDS)
        logger.info("Connection pool created (%s connections) for %s@%s",
                    settings.pool_size, settings.user, settings.host)
        return pool

    # Forgets the pool and every manager object (used by the tests and after a settings change).
    @classmethod
    def reset(cls) -> None:
        """Forget the pool and all instances (used by tests and when settings change)."""
        with DatabaseConnection._lock:
            DatabaseConnection._pool = None
            DatabaseConnection._instances.clear()

    # ------------------------------------------------------------------ connections and transactions

    # Borrow a connection, use it, and always give it back: the 'with' block guarantees that.
    @contextmanager
    def connection(self, read_only: bool = False) -> Iterator[Any]:
        """Borrow a pooled connection; it is always returned to the pool.

        When every connection is in use (several dashboard users at once), the request waits up
        to `pool_wait_seconds` for one to be returned instead of failing at once. A connection
        the server has closed is reconnected by the pool itself. Inside a rollback_session the
        session's pinned connection is used instead; a read inside a read_session uses that one.

        Anything but a read may leave session state behind (a procedure's variables and temporary
        tables, a script's settings), so the session is reset before the connection goes back.
        A read changes nothing there and skips this.
        """
        # inside a test session or a read session, reuse that session's connection
        pinned = getattr(_pinned, "connection", None)
        if pinned is None and read_only:
            pinned = getattr(_reads, "connection", None)
        if pinned is not None:
            yield pinned
            return
        # otherwise borrow a free connection from the pool
        conn = self._borrow()
        try:
            # the caller's 'with' block runs here
            yield conn
        finally:
            try:
                if not read_only:
                    # after a write, clear the session (variables, temporary tables) before giving the connection back
                    conn.reset_session()
            except mysql.connector.Error as err:      # the work is done; the pool reconnects a dead connection
                logger.warning("Session reset failed: %s", err)
            finally:
                conn.close()        # returns the connection to the pool

    # Takes a free connection from the pool; if all are busy it keeps trying for up to 60 s.
    @staticmethod
    def _borrow() -> Any:
        pool = DatabaseConnection._shared_pool()
        deadline = time.monotonic() + DatabaseConnection._pool_wait
        while True:
            try:
                return pool.get_connection()
            except mysql.connector.errors.PoolError as err:          # all connections in use
                if time.monotonic() >= deadline:
                    raise PoolBusyError(f"All {pool.pool_size} database connections are "
                                        "busy — please try again in a moment") from err
                # all busy: wait 50 ms and try again
                time.sleep(0.05)
            except mysql.connector.Error as err:
                raise DatabaseError(f"No database connection available: {err}") from err

    # Two people saving at the same moment could get the same new employee code; the second one is simply
    # tried again with the next code.
    def run_with_retry(self, work: Callable[[], T], unique_key: str, attempts: int = 3) -> T:
        """Run `work` — one transaction that generates a new key, such as the next employee code
        — and run it again if a simultaneous insert took that key first.

        Two users saving at the same moment read the same highest code; the UNIQUE key refuses
        the second insert, its transaction is rolled back, and the next attempt reads the new
        highest code. Any other error, or the same one `attempts` times, is raised.
        """
        # try the work up to 3 times
        for attempt in range(1, attempts + 1):
            try:
                return work()
            except DuplicateError as err:
                # a different duplicate, or the last try: report it
                if err.constraint != unique_key or attempt == attempts:
                    raise
                logger.warning("%s was taken by a simultaneous insert; retrying (attempt %s of %s)",
                               unique_key, attempt + 1, attempts)
        raise AssertionError("unreachable")

    # Every save in the app runs inside this: COMMIT if everything worked, ROLLBACK if anything failed, so the
    # database is never left half-updated.
    @contextmanager
    def transaction(self, dictionary: bool = False, read_only: bool = False) -> Iterator[Any]:
        """Run a unit of work: commit on success, roll back on any error.

        MySQL errors are re-raised as application exceptions (translate_error). Inside a
        rollback_session the unit of work is a SAVEPOINT instead, so a failed step is undone
        without losing the session's earlier steps — and nothing is ever committed.

        read_only marks a unit that only SELECTs (see connection()). The commit still happens: it
        ends the read's snapshot, so the next user of the connection sees new data. In a session
        the session ends it, and a failed read has nothing to undo.
        """
        with self.connection(read_only) as conn:
            pinned = conn is getattr(_pinned, "connection", None)
            shared = pinned or conn is getattr(_reads, "connection", None)
            # a cursor runs SQL; dictionary=True returns each row as {column: value}
            cursor = conn.cursor(dictionary=dictionary)
            savepoint = None
            if pinned and not read_only:
                _pinned.depth += 1
                savepoint = f"dal_unit_{_pinned.depth}"
                cursor.execute(f"SAVEPOINT {savepoint}")
            try:
                # the caller's SQL statements run here
                yield cursor
                if savepoint:
                    cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
                elif not shared:
                    # every step worked: make the changes permanent
                    conn.commit()
            except mysql.connector.Error as err:
                if savepoint or not shared:
                    self._undo(conn, cursor, savepoint)
                # a MySQL error: undo everything, then report it as the application's own exception
                raise translate_error(err) from err
            # any other error (for example a ValidationError): roll back too, then pass the error on
            except Exception:
                if savepoint or not shared:
                    self._undo(conn, cursor, savepoint)
                raise
            finally:
                if savepoint:
                    _pinned.depth -= 1
                cursor.close()

    # Rolls back: the whole transaction, or only back to the savepoint in a test session.
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

    # Used only by the tests: they make real saves and read them back, and everything is undone at the end.
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

    @classmethod
    @contextmanager
    def read_session(cls) -> Iterator[None]:
        """Run several reads on ONE borrowed connection, e.g. everything a page shows about one
        employee: the pool check and the commit happen once instead of for every query.

        The reads see one consistent state of the data. A write inside the session is not part
        of it: it borrows its own connection and commits as usual.
        """
        if cls.in_rollback_session() or getattr(_reads, "connection", None) is not None:
            yield                                     # already on one connection
            return
        # borrow one connection and use it for every read inside the 'with' block
        with DatabaseConnection().connection(read_only=True) as conn:
            _reads.connection = conn
            try:
                yield
            finally:
                _reads.connection = None
                cls._undo(conn, None, None)           # ends the snapshot before the connection goes back

    # ------------------------------------------------------------------ convenience helpers

    # Small helpers the managers use for reads: a list of rows (fetch_all), one row (fetch_one) or a pandas
    # DataFrame for charts (fetch_frame).
    def fetch_all(self, sql: str, params: Sequence[Any] | dict | None = None,
                  dictionary: bool = True) -> list:
        with self.transaction(dictionary=dictionary, read_only=True) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # One row (or None) as a dictionary.
    def fetch_one(self, sql: str, params: Sequence[Any] | dict | None = None,
                  dictionary: bool = True) -> Any:
        with self.transaction(dictionary=dictionary, read_only=True) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_frame(self, sql: str, params: Sequence[Any] | dict | None = None) -> pd.DataFrame:
        """Run a query and return a DataFrame (DECIMAL columns as floats, ready for charts)."""
        with self.transaction(read_only=True) as cur:
            return self.frame_in(cur, sql, params)

    @staticmethod
    def frame_in(cursor: Any, sql: str, params: Sequence[Any] | dict | None = None) -> pd.DataFrame:
        """Like fetch_frame, on an open cursor (inside the caller's transaction).

        DECIMAL columns become numbers: whole-number DECIMALs (scale 0, e.g. SUM of integers)
        become integers — nullable Int64 when the column has NULLs — and the rest floats.
        """
        cursor.execute(sql, params)
        # all the result rows
        rows = cursor.fetchall()
        columns = list(cursor.column_names)
        # turn the rows into a pandas table with the column names
        frame = pd.DataFrame([list(r.values()) if isinstance(r, dict) else list(r) for r in rows], columns=columns)
        # MySQL returns DECIMAL numbers; turn them into plain numbers for the charts
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

    # One INSERT / UPDATE / DELETE in its own transaction; returns how many rows changed.
    def execute(self, sql: str, params: Sequence[Any] | dict | None = None) -> int:
        """Execute one write statement in its own transaction; returns the affected row count."""
        with self.transaction() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    # Runs a stored procedure (CALL sp_...) inside the caller's transaction and reads back its OUT values,
    # such as the new employee id and code from sp_onboard_employee.
    @staticmethod
    def call_in(cursor: Any, name: str, args: Sequence[Any] = (), out_params: int = 0) -> tuple:
        """CALL a stored procedure on an open cursor (inside the caller's transaction).

        IN arguments are bound as query parameters; OUT parameters are read back through
        session variables on the same connection.
        """
        # only real procedure names are allowed, so nothing else can be slipped into the CALL
        if not _PROCEDURE_NAME.fullmatch(name):
            raise ValueError(f"Invalid procedure name: {name!r}")
        # MySQL hands OUT values back through session variables @out0, @out1, ...
        outs = [f"@out{i}" for i in range(out_params)]
        # CALL the procedure: the IN values as %s parameters, the OUT values into the @out variables
        cursor.execute(f"CALL {name}({', '.join(['%s'] * len(args) + outs)})", tuple(args))
        if not outs:
            return ()
        # read the OUT values back
        cursor.execute(f"SELECT {', '.join(outs)}")
        row = cursor.fetchone()
        values = row.values() if isinstance(row, dict) else row
        return tuple(v.decode() if isinstance(v, (bytes, bytearray)) else v for v in values)

    # CALL a procedure in its own transaction (used by the ETL commands).
    def call_procedure(self, name: str, args: Sequence[Any] = (), out_params: int = 0) -> tuple:
        """CALL a stored procedure in its own transaction and return its OUT parameter values.

        The ETL entry points commit themselves; for every other procedure the commit of this
        transaction applies.
        """
        with self.transaction() as cur:
            return self.call_in(cur, name, args, out_params)

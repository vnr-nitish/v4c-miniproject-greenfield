"""Database access foundation: settings, and the DatabaseConnection Singleton.

Design (docs/synthesizer_design.md, decision D22):
- The whole application shares ONE MySQL connection pool, created on first use.
- DatabaseConnection follows the Singleton pattern per class: every class that inherits from it
  (the staging loader now, the data-access managers in Phase 3) has exactly one instance, and all
  of them draw connections from the same shared pool.
- Connections are always returned to the pool and transactions are always committed or rolled
  back, via context managers.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import pooling

from src.exceptions import ConfigurationError, DatabaseError

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
        """Borrow a pooled connection; it is always returned to the pool."""
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
        """Run a unit of work: commit on success, roll back on any error."""
        with self.connection() as conn:
            cursor = conn.cursor(dictionary=dictionary)
            try:
                yield cursor
                conn.commit()
            except mysql.connector.Error as err:
                conn.rollback()
                raise DatabaseError(str(err)) from err
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

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

    def execute(self, sql: str, params: Sequence[Any] | dict | None = None) -> int:
        """Execute one write statement in its own transaction; returns the affected row count."""
        with self.transaction() as cur:
            cur.execute(sql, params)
            return cur.rowcount

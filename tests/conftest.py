"""Shared pytest fixtures: a MySQL connection and a per-test transaction that is rolled back."""
import os
from pathlib import Path

import mysql.connector
import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def connection():
    load_dotenv(os.environ.get("HR_ENV_FILE") or ROOT / ".env", override=True)
    try:
        conn = mysql.connector.connect(
            host=os.environ["DB_HOST"],
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            autocommit=False,
        )
    except (KeyError, mysql.connector.Error) as err:
        pytest.skip(f"MySQL not available: {err}")
    yield conn
    conn.close()


@pytest.fixture
def cur(connection):
    """A cursor inside a transaction that is always rolled back — tests leave no data behind."""
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        connection.rollback()

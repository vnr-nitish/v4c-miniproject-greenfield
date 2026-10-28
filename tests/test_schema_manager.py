"""SchemaManager tests: the SQL splitter (no database) and the safe installation of scripts.

No test here changes a schema: the guard test replaces the execution step with one that fails
the test if it is ever reached, and the execution tests run harmless SELECT scripts.
"""
from pathlib import Path

import pytest

from src.dal.schema_manager import PROTECTED_TABLES, SchemaManager, SqlScript
from src.db_manager import DatabaseConnection
from src.exceptions import BusinessRuleError, DatabaseError, HRAppError, NotFoundError

PROCEDURE = """
USE hr_oltp;  -- select the schema
DELIMITER $$
CREATE PROCEDURE p()
BEGIN
    SELECT 'a;b';          -- a semicolon inside a string and a comment; does not split
    SELECT "c""d";
END$$
DELIMITER ;
/* only a comment; */
# another comment;
SELECT 1;
"""


# ----------------------------------------------------------------------------- the splitter (no database)

def test_statements_are_split_like_the_mysql_client():
    statements = SqlScript.split(PROCEDURE)
    assert len(statements) == 3
    assert statements[0].startswith("USE hr_oltp")
    assert SqlScript.first_code_line(statements[1]) == "CREATE PROCEDURE p()" and statements[1].endswith("END")
    assert "SELECT 'a;b';" in statements[1] and "-- a semicolon" in statements[1]   # comments kept
    assert statements[2].endswith("SELECT 1")                                         # comment-only chunks dropped


def test_script_properties():
    script = SqlScript(Path("04_dw_ddl.sql"), "DROP TABLE IF EXISTS x; CREATE TABLE x (a INT);")
    assert script.number == "04" and script.drops_tables and len(script.statements) == 2
    assert not SqlScript(Path("03_views.sql"), "CREATE OR REPLACE VIEW v AS SELECT 1;").drops_tables
    assert SqlScript.first_code_line("-- comment\n/* note */\n  SELECT 1\nFROM t") == "SELECT 1"


def test_every_project_script_splits():
    for path in sorted((Path(__file__).resolve().parents[1] / "sql").glob("[0-9][0-9]_*.sql")):
        assert SqlScript.load(path).statements, path.name


# ----------------------------------------------------------------------------- the manager (database)

@pytest.fixture(scope="module")
def schema():
    try:
        DatabaseConnection()
    except HRAppError as err:
        pytest.skip(f"MySQL not available: {err}")
    return SchemaManager()


def test_scripts_are_selected_in_order(schema):
    assert [s.number for s in schema.scripts()] == ["00", "01", "02", "03", "04", "05", "06", "07", "08"]
    assert [s.name for s in schema.scripts(["08", "03"])] == ["03_oltp_views.sql", "08_dw_procedures.sql"]
    with pytest.raises(NotFoundError):
        schema.scripts(["99"])


def test_a_rebuild_over_loaded_data_is_refused_before_anything_runs(schema, monkeypatch):
    if not schema.loaded_tables():
        pytest.skip("no business data loaded")

    def must_not_run(script):
        pytest.fail(f"{script.name} was executed although data is loaded")

    monkeypatch.setattr(schema, "run_script", must_not_run)
    with pytest.raises(BusinessRuleError, match="Refusing to rebuild"):
        schema.install(schema.scripts(["01"]))
    assert set(schema.loaded_tables()) <= {f"{s}.{t}" for s, t in PROTECTED_TABLES}


def test_scripts_run_on_the_shared_pool(schema, tmp_path):
    good = tmp_path / "90_select.sql"
    good.write_text("SELECT 1;\nSELECT 'x;y';\n", encoding="utf-8")
    assert schema.install([SqlScript.load(good)]) == 2
    bad = tmp_path / "91_broken.sql"
    bad.write_text("SELECT 1;\nSELECT * FROM hr_oltp.no_such_table;\n", encoding="utf-8")
    with pytest.raises(DatabaseError, match="FAILED in 91_broken.sql"):
        schema.run_script(SqlScript.load(bad))
    version, user = schema.server()
    assert version.startswith("8.") and "@" in user
    assert isinstance(schema, DatabaseConnection) and schema is SchemaManager()

import pytest

from chgraph import schema
from chgraph.store import SchemaVersionError, Store


def test_schema_version_gate(tmp_path):
    s = Store.open(tmp_path / "d")
    s.exec("ALTER TABLE chgraph.meta UPDATE value = '999' WHERE key = 'schema_version'")
    s.close()
    with pytest.raises(SchemaVersionError):
        Store.open(tmp_path / "d")


def test_reopen_matching_version(tmp_path):
    # Daemon-restart path: reopening the same data dir with an unchanged
    # schema version must succeed and must not duplicate the meta row.
    s = Store.open(tmp_path / "d")
    s.close()

    s = Store.open(tmp_path / "d")
    try:
        rows = s.rows("SELECT value FROM chgraph.meta FINAL WHERE key = 'schema_version'")
        assert len(rows) == 1
        assert int(rows[0]["value"]) == schema.SCHEMA_VERSION
    finally:
        s.close()

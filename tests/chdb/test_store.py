import pytest

from chgraph.store import SchemaVersionError, Store


def test_schema_version_gate(tmp_path):
    s = Store.open(tmp_path / "d")
    s.exec("ALTER TABLE chgraph.meta UPDATE value = '999' WHERE key = 'schema_version'")
    s.close()
    with pytest.raises(SchemaVersionError):
        Store.open(tmp_path / "d")

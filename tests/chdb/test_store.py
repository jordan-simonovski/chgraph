import subprocess
import sys

import pytest

from chgraph import schema
from chgraph.store import SchemaVersionError, Store


@pytest.mark.parametrize("checkpoint", [False, True])
def test_durable_restore_without_local_data(tmp_path, checkpoint):
    from chgraph.gitingest import _batch_load

    uri = f"local:{tmp_path / 'durable'}"
    s = Store.open(tmp_path / "first", durable_uri=uri, project="demo")
    try:
        _batch_load(s, "chgraph.edges", [
            {"project": "demo", "source": str(i), "target": "b'\n\\", "type": "CALLS",
             "properties": "{}", "version": 1} for i in range(1001)
        ])
        if checkpoint:
            s.checkpoint()
    finally:
        s.close()

    s = Store.open(tmp_path / "second", durable_uri=uri, project="demo")
    try:
        assert s.rows("SELECT source, target FROM chgraph.edges FINAL WHERE source = '0'") == [
            {"source": "0", "target": "b'\n\\"}
        ]
        assert s.raw("SELECT count() FROM chgraph.edges").strip() == "1001"
    finally:
        s.close()


def test_durable_index_restores_graph_and_git_history(tmp_path, synth_repo):
    from chgraph.indexer import index_repository

    uri = f"local:{tmp_path / 'durable'}"
    s = Store.open(tmp_path / "first", durable_uri=uri, project="demo")
    try:
        result = index_repository(s, "demo", str(synth_repo))
        assert not result.degraded_reasons
        counts = {table: s.rows(f"SELECT count() AS n FROM chgraph.{table}")[0]["n"]
                  for table in ("nodes", "edges", "git_commits", "git_file_changes",
                                "file_evolution")}
        assert all(counts.values())
    finally:
        s.close()

    s = Store.open(tmp_path / "second", durable_uri=uri, project="demo")
    try:
        for table, count in counts.items():
            assert s.rows(f"SELECT count() AS n FROM chgraph.{table}")[0]["n"] == count
    finally:
        s.close()


def test_durable_write_is_committed_before_close_and_excludes_second_writer(tmp_path):
    from chdb.durable.errors import LeaseHeld

    uri = f"local:{tmp_path / 'durable'}"
    s = Store.open(tmp_path / "first", durable_uri=uri, project="demo")
    try:
        s.exec("INSERT INTO chgraph.edges VALUES ('demo','a','b','CALLS','{}',1)")
        with pytest.raises(LeaseHeld):
            Store.open(tmp_path / "second", durable_uri=uri, project="demo")
        out = subprocess.run([
            sys.executable, "-c",
            "from chdb import durable as cd; import sys; "
            "obj = cd.Namespace(sys.argv[1]).open('demo', read_only=True); "
            "print(obj.query('SELECT count() FROM chgraph.edges').data()); obj.close()",
            uri,
        ], check=True, capture_output=True, text=True)
        assert out.stdout.strip() == "1"
    finally:
        s.close()


def test_index_checkpoint_failure_is_not_success(store, synth_repo, monkeypatch):
    from chgraph.indexer import index_repository

    def fail_checkpoint():
        raise OSError("checkpoint failed")

    monkeypatch.setattr(store, "checkpoint", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint failed"):
        index_repository(store, "demo", str(synth_repo))


@pytest.mark.parametrize("durable", [False, True])
def test_schema_version_gate(tmp_path, durable):
    kwargs = {"durable_uri": f"local:{tmp_path / 'durable'}", "project": "demo"} if durable else {}
    s = Store.open(tmp_path / "d", **kwargs)
    s.exec("ALTER TABLE chgraph.meta UPDATE value = '999' WHERE key = 'schema_version'")
    s.close()
    with pytest.raises(SchemaVersionError):
        Store.open(tmp_path / "d", **kwargs)


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

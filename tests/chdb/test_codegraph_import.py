import json
import hashlib
import sqlite3

import pytest

from chgraph.traverse import trace_path


@pytest.fixture
def snapshot_db(tmp_path):
    path = tmp_path / "codegraph.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE schema_versions (version INTEGER);
            INSERT INTO schema_versions VALUES (11);
            CREATE TABLE project_metadata (key TEXT, value TEXT);
            INSERT INTO project_metadata VALUES
                ('indexed_with_version', '1.6.2'),
                ('indexed_with_extraction_version', '27'), ('index_state', 'complete');
            CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, start_line INTEGER, end_line INTEGER, signature TEXT);
            INSERT INTO nodes VALUES
                ('a','function','same','same','a.py',1,3,'()'),
                ('b','function','same','same','b.py',1,2,'()');
            CREATE TABLE edges (id INTEGER, source TEXT, target TEXT, kind TEXT,
                line INTEGER, col INTEGER, metadata TEXT, provenance TEXT);
            INSERT INTO edges VALUES
                (1,'a','b','calls',2,4,'{"resolvedBy":"import"}','tree-sitter'),
                (2,'a','b','calls',3,4,'{"resolvedBy":"import"}',NULL),
                (3,'a','b','references',2,4,NULL,'heuristic');
            CREATE TABLE files (path TEXT, content_hash TEXT, errors TEXT);
            INSERT INTO files VALUES ('a.py','hash-a',NULL), ('b.py','hash-b',NULL);
            CREATE TABLE unresolved_refs (from_node_id TEXT, reference_name TEXT,
                status TEXT);
            INSERT INTO unresolved_refs VALUES ('a','unknown','failed');
        """)
    return path


def test_import_preserves_identity_evidence_and_traversal(store, snapshot_db):
    from chgraph.codegraph_import import import_codegraph_snapshot

    counts = import_codegraph_snapshot(store, "fixture", snapshot_db)
    assert counts == {"nodes": 2, "edges": 3, "relationships": 2, "files": 2,
                      "unresolved_refs": 1}
    nodes = store.rows("SELECT * FROM chgraph.nodes FINAL ORDER BY qualified_name")
    assert [n["qualified_name"] for n in nodes] == ["a", "b"]
    assert [json.loads(n["properties"])["codegraph"]["qualified_name"]
            for n in nodes] == ["same", "same"]
    edges = store.rows("SELECT * FROM chgraph.edges FINAL ORDER BY type")
    evidence = json.loads(edges[0]["properties"])["codegraph"]
    assert [e["line"] for e in evidence] == [2, 3]
    assert evidence[0]["metadata"] == '{"resolvedBy":"import"}'
    assert [e["provenance"] for e in evidence] == ["tree-sitter", None]
    assert edges[1]["type"] == "REFERENCES"
    assert trace_path(store, "fixture", "a", depth=1)[-1]["node"] == "b"
    assert trace_path(store, "fixture", "b", direction="callers", depth=1)[-1]["node"] == "a"
    info = json.loads(store.rows(
        "SELECT value FROM chgraph.meta FINAL WHERE key='codegraph_snapshot:fixture'"
    )[0]["value"])
    assert info["files"][0]["content_hash"] == "hash-a"
    assert info["unresolved_refs"][0]["reference_name"] == "unknown"
    assert info["metadata"]["indexed_with_version"] == "1.6.2"


@pytest.mark.parametrize("sql, message", [
    ("UPDATE schema_versions SET version=12", "schema"),
    ("UPDATE project_metadata SET value='9.0' WHERE key='indexed_with_version'", "version"),
    ("UPDATE project_metadata SET value='28' WHERE key='indexed_with_extraction_version'", "extraction"),
    ("UPDATE project_metadata SET value='indexing' WHERE key='index_state'", "complete"),
    ("ALTER TABLE edges DROP COLUMN provenance", "column"),
    ("UPDATE edges SET target='missing'", "endpoint"),
    ("UPDATE unresolved_refs SET from_node_id='missing'", "reference source"),
    ("UPDATE nodes SET start_line=0", "range"),
    ("UPDATE files SET errors='[\"parse failed\"]'", "errors"),
])
def test_rejects_incompatible_or_incomplete_input_before_writing(store, snapshot_db, sql, message):
    from chgraph.codegraph_import import import_codegraph_snapshot

    with sqlite3.connect(snapshot_db) as db:
        db.execute(sql)
    with pytest.raises(ValueError, match=message):
        import_codegraph_snapshot(store, "fixture", snapshot_db)
    assert store.rows("SELECT count() AS n FROM chgraph.nodes")[0]["n"] == 0
    assert store.rows("SELECT count() AS n FROM chgraph.edges")[0]["n"] == 0


def test_refuses_to_overwrite_existing_graph(store, snapshot_db):
    from chgraph.codegraph_import import import_codegraph_snapshot

    store.exec("INSERT INTO chgraph.nodes VALUES ('old','Function','keep','keep','a',1,1,'{}',1)")
    with pytest.raises(ValueError, match="empty"):
        import_codegraph_snapshot(store, "fixture", snapshot_db)
    assert store.rows("SELECT name FROM chgraph.nodes") == [{"name": "keep"}]


def test_missing_source_is_not_created(store, tmp_path):
    from chgraph.codegraph_import import import_codegraph_snapshot

    missing = tmp_path / "missing.db"
    with pytest.raises((ValueError, sqlite3.OperationalError)):
        import_codegraph_snapshot(store, "fixture", missing)
    assert not missing.exists()


def test_reads_committed_wal_and_leaves_source_unchanged(store, snapshot_db):
    from chgraph.codegraph_import import import_codegraph_snapshot

    with sqlite3.connect(snapshot_db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE files SET content_hash='in-wal' WHERE path='a.py'")
        writer.commit()
        before = snapshot_db.read_bytes()
        import_codegraph_snapshot(store, "fixture", snapshot_db)
        assert snapshot_db.read_bytes() == before
        info = json.loads(store.rows(
            "SELECT value FROM chgraph.meta FINAL WHERE key='codegraph_snapshot:fixture'"
        )[0]["value"])
        assert info["files"][0]["content_hash"] == "in-wal"


def test_preparation_quarantines_shadowed_calls_and_requires_matching_source(snapshot_db, tmp_path):
    from chgraph.codegraph_import import prepare_snapshot

    sources = {"a.py": b"def same(same):\n    same()\n    same()\n",
               "b.py": b"def same():\n    pass\n"}
    with sqlite3.connect(snapshot_db) as db:
        for name, source in sources.items():
            (tmp_path / name).write_bytes(source)
            db.execute("UPDATE files SET content_hash=? WHERE path=?",
                       (hashlib.sha256(source).hexdigest(), name))
    prepared = prepare_snapshot(snapshot_db, tmp_path)
    assert [e["id"] for e in prepared["rejected_edges"]] == [1, 2]
    assert len(prepared["edges"]) == 3  # Original evidence remains intact.
    assert prepared["identities"] == {"a": "a.same", "b": "b.same"}
    (tmp_path / "a.py").write_text("# edited\n")
    with pytest.raises(ValueError, match="stale"):
        prepare_snapshot(snapshot_db, tmp_path)


def test_unmatched_binding_scope_quarantines_calls_without_aborting_import(snapshot_db, tmp_path):
    from chgraph.codegraph_import import prepare_snapshot

    sources = {"a.py": b"def same[T](value: (lambda: int)()):\n    same()\n    same()\n",
               "b.py": b"def same():\n    pass\n"}
    with sqlite3.connect(snapshot_db) as db:
        for name, source in sources.items():
            (tmp_path / name).write_bytes(source)
            db.execute("UPDATE files SET content_hash=? WHERE path=?",
                       (hashlib.sha256(source).hexdigest(), name))
    prepared = prepare_snapshot(snapshot_db, tmp_path)
    assert [e["id"] for e in prepared["rejected_edges"]] == [1, 2]
    assert "binding validation unavailable" in prepared["validation_errors"]["a.py"]
    assert len(prepared["edges"]) == 3

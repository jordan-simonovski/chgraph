import pytest
import importlib.util
from pathlib import Path

from chgraph.generations import active_project, publish_generation


def _node(store, project, name):
    store.exec(f"INSERT INTO chgraph.nodes VALUES ('{project}','Function','{name}','{name}','a.py',1,1,'{{}}',1)")


def test_publish_retains_previous_graph_and_prunes_older_generations(store):
    _node(store, "p", "original")
    _node(store, "p@g1", "first")
    assert active_project(store, "p") == "p"
    assert publish_generation(store, "p", "p@g1", "native") == []
    assert active_project(store, "p") == "p@g1"
    _node(store, "p@g2", "second")
    publish_generation(store, "p", "p@g2", "codegraph")
    assert active_project(store, "p") == "p@g2"
    assert {r["project"] for r in store.rows("SELECT DISTINCT project FROM chgraph.nodes")} == {"p@g1", "p@g2"}


def test_failed_publication_preserves_old_graph(store, monkeypatch):
    _node(store, "p", "original")
    _node(store, "p@failed", "candidate")
    original = store.exec

    def fail_pointer(sql):
        if "INSERT INTO chgraph.meta" in sql and "active_project" in sql:
            raise RuntimeError("publication interrupted")
        return original(sql)

    monkeypatch.setattr(store, "exec", fail_pointer)
    with pytest.raises(RuntimeError, match="interrupted"):
        publish_generation(store, "p", "p@failed", "codegraph")
    assert active_project(store, "p") == "p"
    assert store.rows("SELECT name FROM chgraph.nodes WHERE project='p'") == [{"name": "original"}]


def test_index_sanity_resolves_published_generation(tmp_path):
    from chgraph.store import Store

    data = tmp_path / "data"
    store = Store.open(data)
    try:
        _node(store, "p", "old")
        _node(store, "p@g1", "new")
        publish_generation(store, "p", "p@g1", "native")
    finally:
        store.close()
    script = Path(__file__).resolve().parents[2] / ".claude/skills/chgraph-diagnostics-and-tooling/scripts/index_sanity.py"
    spec = importlib.util.spec_from_file_location("index_sanity", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.count_nodes(str(data), "p") == 1

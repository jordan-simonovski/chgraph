import pytest

from chgraph.indexer import index_repository


def test_explore_is_bounded_and_rejects_stale_source(store, synth_repo):
    from chgraph.context import explore

    index_repository(store, "p", str(synth_repo))
    result = explore(store, "p", str(synth_repo), "handle", max_chars=1000, max_nodes=3)
    assert len(result["context"]) <= 1000
    assert "def handle" in result["context"]
    assert result["truncated"]
    natural = explore(store, "p", str(synth_repo), "How does handle work", max_chars=1000, max_nodes=3)
    assert "def handle" in natural["context"]
    path = synth_repo / "src" / "api.py"
    original = path.read_bytes()
    try:
        path.write_bytes(b"# newly edited source\n" + original)
        result = explore(store, "p", str(synth_repo), "handle")
        assert any("stale" in w for w in result["warnings"])
        assert "newly edited source" not in result["context"]
        assert "def handle" not in result["context"]
    finally:
        path.write_bytes(original)


def test_bounded_walk_limits_fanout_and_keeps_representative_paths(store):
    from chgraph.context import bounded_walk

    store.exec("""INSERT INTO chgraph.edges VALUES
        ('p','a','b','CALLS','{}',1),('p','a','c','CALLS','{}',1),
        ('p','b','d','CALLS','{}',1),('p','c','d','CALLS','{}',1),
        ('p','d','a','CALLS','{}',1)""")
    result = bounded_walk(store, "p", ["a"], direction="callees", depth=10, max_nodes=3)
    assert len(result["paths"]) <= 3
    assert result["truncated"]
    full = bounded_walk(store, "p", ["a"], direction="callees", depth=10, max_nodes=10)
    assert set(full["paths"]) == {"a", "b", "c", "d"}
    assert full["paths"]["d"] in (["a", "b", "d"], ["a", "c", "d"])


def test_impact_separates_static_paths_from_git_cochange(store, synth_repo):
    from chgraph.context import impact

    index_repository(store, "p", str(synth_repo))
    result = impact(store, "p", "src.api.handle")
    assert result["affected"][0]["qualified_name"] == "src.api.handle"
    assert result["affected"][0]["evolution"]["top_author"] == "alice@example.com"
    assert any(row["file_path"] == "tests/test_api.py" for row in result["cochange"])
    assert "tests/test_api.py" not in {r["file_path"] for r in result["affected"]}


def test_native_context_checks_dependencies_outside_selected_nodes(store, synth_repo):
    from chgraph.context import explore, impact

    index_repository(store, "p", str(synth_repo))
    store.exec("INSERT INTO chgraph.edges VALUES ('p','src.api.handle','src.api.handle_v1','CALLS','{}',1)")
    assert "native extraction" in explore(store, "p", str(synth_repo), "src.api.handle")["context"]
    path = synth_repo / "src" / "util.py"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n# changed resolver dependency\n")
        result = explore(store, "p", str(synth_repo), "src.api.handle")
        assert any("stale" in w and "src/util.py" in w for w in result["warnings"])
        assert "native extraction" not in result["context"]
        with pytest.raises(ValueError, match="stale"):
            impact(store, "p", "src.api.handle", repo_root=str(synth_repo))
    finally:
        path.write_bytes(original)


def test_native_impact_reports_unreadable_dependencies(store, synth_repo):
    from chgraph.context import impact

    index_repository(store, "p", str(synth_repo))
    assert not impact(store, "p", "src.api.handle", repo_root=str(synth_repo))["warnings"]
    path = synth_repo / "src" / "util.py"
    original = path.read_bytes()
    try:
        path.unlink()
        result = impact(store, "p", "src.api.handle", repo_root=str(synth_repo))
        assert any("unverified" in w and "src/util.py" in w for w in result["warnings"])
    finally:
        path.write_bytes(original)

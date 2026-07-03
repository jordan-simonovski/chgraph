import subprocess

from chgraph.indexer import index_repository


def _git_repo(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


def _git_commit(repo, msg):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", msg], check=True)


def test_index_synth_repo(store, synth_repo):
    res = index_repository(store, "synth", str(synth_repo))
    assert res.files_total == res.files_done == 4          # 4 .py files in fixture
    assert res.nodes == 25                                 # 4 File nodes + 21 symbols (functions)
    assert res.degraded_reasons == []
    n = store.rows("SELECT count() AS n FROM chgraph.nodes FINAL WHERE project='synth'")[0]["n"]
    assert n == res.nodes
    # git side ran too:
    assert store.rows("SELECT count() AS n FROM chgraph.git_commits")[0]["n"] == 14


def test_reindex_replaces_not_duplicates(store, synth_repo):
    r1 = index_repository(store, "synth", str(synth_repo))
    r2 = index_repository(store, "synth", str(synth_repo))
    assert r2.version == r1.version + 1
    n = store.rows("SELECT count() AS n FROM chgraph.nodes FINAL WHERE project='synth'")[0]["n"]
    assert n == r2.nodes  # FINAL sees exactly one generation


def test_sanity_gate_flags_symbol_collapse(store, tmp_path):
    # A "repo" whose .py files are unparseable garbage should index as degraded, not "indexed".
    repo = _git_repo(tmp_path, "junk")
    (repo / "a.py").write_bytes(b"\x00" * 5000)
    _git_commit(repo, "x")
    res = index_repository(store, "junk", str(repo))
    assert any("nodes-per-KLOC" in r for r in res.degraded_reasons)


def test_reindex_drops_removed_symbols(store, tmp_path):
    # Proves the reindex TRUNCATE-then-reload: without it, deleted files/symbols
    # keep their old-version rows forever (FINAL only collapses same-key rows,
    # and a removed row has no same-key successor to collapse against).
    repo = _git_repo(tmp_path, "shrink")
    (repo / "a.py").write_text("def a():\n    pass\n")
    (repo / "b.py").write_text("def b():\n    pass\n")
    _git_commit(repo, "initial")
    r1 = index_repository(store, "shrink", str(repo))

    subprocess.run(["git", "-C", str(repo), "rm", "-q", "b.py"], check=True)
    _git_commit(repo, "remove b")
    r2 = index_repository(store, "shrink", str(repo))

    assert r2.nodes < r1.nodes
    n = store.rows(
        "SELECT count() AS n FROM chgraph.nodes FINAL WHERE project='shrink'")[0]["n"]
    assert n == r2.nodes
    file_paths = {row["file_path"] for row in store.rows(
        "SELECT file_path FROM chgraph.nodes FINAL WHERE project='shrink'")}
    assert "b.py" not in file_paths
    e = store.rows(
        "SELECT count() AS n FROM chgraph.edges FINAL WHERE project='shrink'")[0]["n"]
    assert e == r2.edges


def test_git_mismatch_reason_propagates(store, synth_repo, monkeypatch):
    # INV-3: the git-ingest count-mismatch reason (not just the density gate)
    # must land in degraded_reasons.
    import chgraph.indexer as indexer_mod
    monkeypatch.setattr(indexer_mod, "verify_git_counts",
                        lambda *a, **k: ["git ingest: fake mismatch"])
    res = index_repository(store, "synth", str(synth_repo))
    assert "git ingest: fake mismatch" in res.degraded_reasons


def test_empty_repo_suppresses_density_gate(store, tmp_path):
    # No .py files at all: the density gate must not fire a false degradation.
    repo = _git_repo(tmp_path, "empty")
    (repo / "README.md").write_text("hello\n")
    _git_commit(repo, "init")
    res = index_repository(store, "empty", str(repo))
    assert res.files_total == 0
    assert res.degraded_reasons == []

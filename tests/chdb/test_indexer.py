from chgraph.indexer import index_repository


def test_index_synth_repo(store, synth_repo):
    res = index_repository(store, "synth", str(synth_repo))
    assert res.files_total == res.files_done == 4          # 4 .py files in fixture
    assert res.nodes >= 4 + 4                              # >= one File node + symbols per file
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
    import subprocess
    repo = tmp_path / "junk"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"\x00" * 5000)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "x"], check=True)
    res = index_repository(store, "junk", str(repo))
    assert any("nodes-per-KLOC" in r for r in res.degraded_reasons)

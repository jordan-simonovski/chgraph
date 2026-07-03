from chgraph.gitingest import ingest_git, verify_git_counts


def test_ingest_counts_match_campaign_gate(store, synth_repo):
    counts = ingest_git(store, "synth", str(synth_repo))
    assert (counts.commits, counts.file_changes, counts.renames) == (14, 24, 1)
    assert verify_git_counts(str(synth_repo), counts) == []


def test_ingest_is_idempotent(store, synth_repo):
    ingest_git(store, "synth", str(synth_repo))
    counts = ingest_git(store, "synth", str(synth_repo))  # second run must NOT double
    assert (counts.commits, counts.file_changes) == (14, 24)


def test_rename_expanded(store, synth_repo):
    ingest_git(store, "synth", str(synth_repo))
    rows = store.rows(
        "SELECT path, old_path FROM chgraph.git_file_changes WHERE is_rename = 1"
    )
    assert rows == [{"path": "src/core/legacy.py", "old_path": "src/legacy.py"}]


def test_count_gate_catches_mismatch(store, synth_repo):
    from chgraph.gitingest import GitIngestCounts
    bad = GitIngestCounts(commits=1, file_changes=1, renames=0)
    reasons = verify_git_counts(str(synth_repo), bad)
    assert len(reasons) == 2  # commits mismatch + file_changes mismatch

import pytest

from chgraph.evolution import churn, coupling, ownership, recency, refresh_file_evolution
from chgraph.gitingest import ingest_git


@pytest.fixture
def synth_store(store, synth_repo):
    ingest_git(store, "synth", str(synth_repo))
    return store


def test_churn_top_is_api(synth_store):
    top = churn(synth_store, "synth")[0]
    assert top["path"] == "src/api.py"
    assert top["commits"] == 8 and top["churn"] == 23


def test_coupling_planted_pair_ranks_first(synth_store):
    rows = coupling(synth_store, "synth")
    # Campaign gate: the planted pair MUST rank #1 with support 7, conf_b_to_a == 1.
    assert rows[0]["file_a"] == "src/api.py" and rows[0]["file_b"] == "tests/test_api.py"
    assert rows[0]["support"] == 7 and rows[0]["conf_b_to_a"] == 1


def test_ownership_alice_dominates_api(synth_store):
    api = next(r for r in ownership(synth_store, "synth") if r["path"] == "src/api.py")
    assert api["top_author"] == "alice@example.com" and api["top_author_share"] == 0.875


def test_recency_fresh_beats_stale(synth_store):
    rows = {r["path"]: r["recency_score"] for r in recency(synth_store, "synth")}
    assert rows["src/api.py"] > 0.9          # touched 1 day ago
    assert rows["src/legacy.py"] < 0.001     # untouched 390 days


def test_file_evolution_refresh(synth_store):
    assert refresh_file_evolution(synth_store, "synth", version=1) == 6
    n = synth_store.rows("SELECT count() AS n FROM chgraph.file_evolution FINAL")[0]["n"]
    assert n == 6

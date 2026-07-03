import pytest

from chgraph.indexer import index_repository
from chgraph.search import search_graph


@pytest.fixture
def indexed(store, synth_repo):
    index_repository(store, "synth", str(synth_repo))
    return store


def test_lexical_hit_ranks_fresh_above_stale(indexed):
    # Fixture: src/api.py touched 1 day ago; src/core/legacy.py stale.
    # Both files define functions; a query hitting both must rank api first.
    page = search_graph(indexed, "synth", query="handle")
    qns = [i["qualified_name"] for i in page.items]
    assert any(q.startswith("src.api.") for q in qns)
    api_pos = min(i for i, q in enumerate(qns) if q.startswith("src.api."))
    legacy_hits = [i for i, q in enumerate(qns) if "legacy" in q]
    assert all(api_pos < i for i in legacy_hits) or not legacy_hits


def test_name_pattern_regex(indexed):
    page = search_graph(indexed, "synth", name_pattern="^handle_v[0-9]$")
    assert page.total >= 1
    assert all(i["label"] == "Function" for i in page.items)


def test_label_filter_and_pagination(indexed):
    all_fns = search_graph(indexed, "synth", label="Function", limit=1000)
    page1 = search_graph(indexed, "synth", label="Function", limit=2, offset=0)
    assert page1.total == all_fns.total and len(page1.items) == 2 and page1.has_more


def test_requires_some_criterion(indexed):
    with pytest.raises(ValueError):
        search_graph(indexed, "synth")

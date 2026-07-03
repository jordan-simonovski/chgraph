"""The campaign thesis end-to-end: fresh code must outrank stale code through the
full stack (index -> evolution -> search), not just in hand-run SQL."""
from chgraph.indexer import index_repository
from chgraph.search import search_graph


def test_fresh_file_symbols_outrank_stale_file_symbols(store, synth_repo):
    res = index_repository(store, "synth", str(synth_repo))
    assert res.degraded_reasons == []
    # 'old_thing' lives in the stale legacy file; 'handle' in the 1-day-old api file.
    fresh = search_graph(store, "synth", query="handle").items[0]
    stale = search_graph(store, "synth", query="old_thing").items[0]
    assert fresh["file_path"] == "src/api.py"
    assert stale["file_path"] == "src/core/legacy.py"
    assert fresh["score"] > stale["score"]   # recency separates them (campaign Phase 5 control)

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

    # Non-vacuous proof: two separate queries, each hitting only fresh or
    # only stale code, both with lex=1.0 (exact name match), so any score
    # gap must come from recency (+ centrality) rather than lexical match.
    fresh = search_graph(indexed, "synth", query="handle").items[0]
    stale = search_graph(indexed, "synth", query="old_thing").items[0]
    assert fresh["file_path"] == "src/api.py"
    assert stale["file_path"] == "src/core/legacy.py"
    assert fresh["score"] > stale["score"]


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


def test_over_pagination_reports_full_total(indexed):
    # Fixture has N=21 Function nodes. Paging past the end must still
    # report the true total (not 0), with no items and has_more False.
    all_fns = search_graph(indexed, "synth", label="Function", limit=1000)
    n = all_fns.total
    page = search_graph(indexed, "synth", label="Function", limit=2, offset=n + 5)
    assert page.total == n
    assert page.items == []
    assert page.has_more is False


def test_last_page_has_more_false(indexed):
    all_fns = search_graph(indexed, "synth", label="Function", limit=1000)
    n = all_fns.total
    page = search_graph(indexed, "synth", label="Function", limit=2, offset=n - 1)
    assert page.total == n
    assert page.has_more is False


def _insert_node(store, qn, name, props='{}'):
    store.exec(
        "INSERT INTO chgraph.nodes "
        "(project, label, name, qualified_name, file_path, start_line, end_line, properties, version) "
        f"VALUES ('p', 'Class', '{name}', '{qn}', 'f.py', 1, 2, '{props}', 1)")


def test_dep_signal_surfaces_and_flag_demotes(store, monkeypatch):
    # two same-name classes tying on lexical match; one is deprecated at parse time
    _insert_node(store, "pkg.Widget", "Widget")
    _insert_node(store, "old.Widget", "Widget", props='{"deprecated": true}')

    monkeypatch.delenv("CHGRAPH_RANK_DEPRECATION_WEIGHT", raising=False)
    items = {i["qualified_name"]: i for i in search_graph(store, "p", query="Widget").items}
    assert items["old.Widget"]["dep"] == 1 and items["pkg.Widget"]["dep"] == 0
    # default weight -0.20 (ADR-0002) -> the deprecated twin is demoted below the live one
    ranked = [i["qualified_name"] for i in search_graph(store, "p", query="Widget").items]
    assert ranked.index("pkg.Widget") < ranked.index("old.Widget")

    # disable flag -> both tie on lex (no git signals), deprecation no longer changes score
    monkeypatch.setenv("CHGRAPH_RANK_DEPRECATION_WEIGHT", "0.0")
    items = {i["qualified_name"]: i for i in search_graph(store, "p", query="Widget").items}
    assert items["old.Widget"]["score"] == items["pkg.Widget"]["score"]


def test_subtokens_splits_identifiers():
    from chgraph.search import _subtokens
    assert _subtokens("HttpResponseRedirect") == ["http", "response", "redirect"]
    assert _subtokens("get_user_by_id") == ["get", "user", "by", "id"]
    assert _subtokens("XMLHttpRequest2") == ["xml", "http", "request2"]  # trailing digit attaches


def test_jaccard_lexical_ranks_exact_above_partial(store, monkeypatch):
    # same query token appears in an exact-name symbol and a longer partial-match symbol
    _insert_node(store, "a.MetaData", "MetaData")
    _insert_node(store, "b.MetaDataFactoryBuilder", "MetaDataFactoryBuilder")
    monkeypatch.setenv("CHGRAPH_RANK_LEXICAL", "jaccard")
    ranked = [i["qualified_name"] for i in search_graph(store, "p", query="MetaData").items]
    assert ranked.index("a.MetaData") < ranked.index("b.MetaDataFactoryBuilder")
    items = {i["qualified_name"]: i for i in search_graph(store, "p", query="MetaData").items}
    assert items["a.MetaData"]["lex"] == 1.0                    # {meta,data} == {meta,data}
    assert items["b.MetaDataFactoryBuilder"]["lex"] < 1.0        # extra subtokens -> lower Jaccard

    # binary escape hatch: both are full substring matches -> tie at 1.0 (the old placeholder)
    monkeypatch.setenv("CHGRAPH_RANK_LEXICAL", "binary")
    items = {i["qualified_name"]: i for i in search_graph(store, "p", query="MetaData").items}
    assert items["a.MetaData"]["lex"] == items["b.MetaDataFactoryBuilder"]["lex"] == 1.0


def _insert_embedding(store, qn, vec):
    lit = "[" + ",".join(str(x) for x in vec) + "]"
    store.exec("INSERT INTO chgraph.embeddings (project, qualified_name, vec, version) "
               f"VALUES ('p', '{qn}', {lit}, 1)")


def test_vector_signal_surfaces_non_lexical_match(store, monkeypatch):
    from chgraph import embeddings, search
    # two symbols; the query lexically matches NEITHER name
    _insert_node(store, "pkg.render", "render")
    _insert_node(store, "pkg.template", "template")
    e_render = [1.0] + [0.0] * (embeddings.EMBED_DIM - 1)
    e_template = [0.0, 1.0] + [0.0] * (embeddings.EMBED_DIM - 2)
    _insert_embedding(store, "pkg.render", e_render)
    _insert_embedding(store, "pkg.template", e_template)
    # stub the model: query embeds close to render, far from template
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_query", lambda q: e_render)
    monkeypatch.setenv("CHGRAPH_RANK_VECTOR", "on")

    items = search_graph(store, "p", query="draw output to the page").items
    qns = [i["qualified_name"] for i in items]
    assert "pkg.render" in qns                      # pulled in by vector despite zero lexical match
    top = items[0]
    assert top["qualified_name"] == "pkg.render" and top["vec"] == 1.0

    # flag off -> no lexical match -> the vector candidate is gone
    monkeypatch.setenv("CHGRAPH_RANK_VECTOR", "off")
    assert search_graph(store, "p", query="draw output to the page").items == []

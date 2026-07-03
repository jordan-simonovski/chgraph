"""Ranking eval (git-evolution campaign Phase 6): does hybrid ranking beat a
recency-blind baseline? Re-rank the same candidate pool two ways, score MRR@10.
Pure logic (rerank + MRR); the search call is injected."""
from chgraph.eval.ranking import rerank, mrr_at_k, evaluate, RankGolden

HYBRID = {"lex": 0.35, "rec": 0.20, "cen": 0.15}
BLIND = {"lex": 0.35, "rec": 0.0, "cen": 0.0}   # recency+centrality zeroed (Phase 6 baseline)


def test_rerank_orders_by_weighted_signals():
    # two lexical ties; one fresh (rec high), one stale (rec low)
    cands = [
        {"qualified_name": "a.stale", "lex": 1.0, "rec": 0.01, "cen": 0.0},
        {"qualified_name": "a.live", "lex": 1.0, "rec": 0.98, "cen": 1.0},
    ]
    assert rerank(cands, HYBRID)[0] == "a.live"          # recency+centrality float live to top
    # recency-blind: pure lexical tie -> stable sort keeps pool order; blind can't tell them apart
    assert rerank(cands, BLIND) == ["a.stale", "a.live"]  # stale stays ahead => the failure mode


def test_mrr_at_k():
    assert mrr_at_k(["x", "y", "target", "z"], "target", k=10) == 1 / 3
    assert mrr_at_k(["target", "y"], "target", k=10) == 1.0
    assert mrr_at_k(["a", "b", "c"], "target", k=2) == 0.0      # beyond cutoff
    assert mrr_at_k([], "target", k=10) == 0.0


def test_evaluate_reports_hybrid_vs_blind_by_slice():
    goldens = [
        RankGolden(query="handle", expected="a.live", repo="synth", slice="staleness"),
        RankGolden(query="helper", expected="u.helper", repo="synth", slice="general"),
    ]
    pool = {
        "handle": [  # stale near-duplicate ranks above live unless recency helps
            {"qualified_name": "a.stale", "lex": 1.0, "rec": 0.01, "cen": 0.0},
            {"qualified_name": "a.live", "lex": 1.0, "rec": 0.98, "cen": 1.0},
        ],
        "helper": [  # no staleness angle; both rankings find it at rank 1
            {"qualified_name": "u.helper", "lex": 1.0, "rec": 0.5, "cen": 0.2},
        ],
    }
    res = evaluate(goldens, lambda q, repo: pool[q], HYBRID, BLIND, k=10)
    # staleness slice: hybrid floats a.live to rank1 (1.0); blind leaves stale ahead -> a.live
    # at rank2 -> 0.5. This +0.50 gap is the campaign's whole thesis.
    assert res["staleness"]["hybrid_mrr"] == 1.0
    assert res["staleness"]["blind_mrr"] == 0.5
    assert res["general"]["hybrid_mrr"] == 1.0 and res["general"]["blind_mrr"] == 1.0

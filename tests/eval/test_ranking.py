"""Ranking eval (git-evolution campaign Phase 6): does hybrid ranking beat a
recency-blind baseline? Re-rank the same candidate pool two ways, score MRR@10.
Pure logic (rerank + MRR); the search call is injected."""
from chgraph.eval.ranking import (rerank, mrr_at_k, evaluate, RankGolden,
                                   is_deprecated_body, annotate_deprecation)

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


def test_is_deprecated_body_detects_markers():
    assert is_deprecated_body("class X:\n    warnings.warn('RemovedInDjango60Warning')")
    assert is_deprecated_body("# this is deprecated, use Y instead")
    assert is_deprecated_body("@deprecated\ndef old(): ...")
    assert not is_deprecated_body("def live():\n    return compute()")


def test_annotate_deprecation_flags_and_demotes():
    cands = [
        {"qualified_name": "pg.StringAgg", "lex": 1.0, "rec": 0.169, "cen": 0.0},
        {"qualified_name": "db.StringAgg", "lex": 1.0, "rec": 0.169, "cen": 0.0},
    ]
    bodies = {"pg.StringAgg": "raise RemovedInDjango70Warning", "db.StringAgg": "def ok(): pass"}
    annotate_deprecation(cands, lambda qn: bodies[qn])
    assert [c["dep"] for c in cands] == [1, 0]
    # with a demotion weight the live symbol now wins the lexical tie the recency signal couldn't break
    W = {"lex": 0.35, "rec": 0.20, "cen": 0.15, "dep": -1.0}
    assert rerank(cands, W)[0] == "db.StringAgg"


def test_dep_weight_is_a_signal_not_a_veto():
    """Phase-6 fix: dep must be a moderate weight, not a -1.0 veto. A veto ejects a
    false-positive live symbol (one that deprecates a PARAMETER, so its body carries a
    marker) out of top-k; a moderate weight demotes true stale twins (which tie on real
    signals) while a false positive's lexical margin keeps it #1. Same body-mention
    detector for both — the weight is what separates the failure modes."""
    stale, live = ({"qualified_name": "pg.X", "lex": 1.0, "rec": 0.5, "cen": 0.0, "dep": 1},
                   {"qualified_name": "db.X", "lex": 1.0, "rec": 0.5, "cen": 0.0, "dep": 0})
    # false positive: canonical live symbol flagged dep=1 but leads the pool on lexical match
    fp = {"qualified_name": "http.JsonResponse", "lex": 1.0, "rec": 0.3, "cen": 0.2, "dep": 1}
    others = [{"qualified_name": f"m{i}", "lex": 0.5, "rec": 0.3, "cen": 0.2, "dep": 0}
              for i in range(12)]

    moderate = {"lex": 0.35, "rec": 0.20, "cen": 0.15, "dep": -0.05}
    veto = {"lex": 0.35, "rec": 0.20, "cen": 0.15, "dep": -1.0}

    # true stale twin: demoted below its live twin under either weight (they tie otherwise)
    assert rerank([stale, live], moderate)[0] == "db.X"
    # false positive: survives at #1 under the moderate weight, but the veto ejects it
    assert rerank([fp, *others], moderate)[0] == "http.JsonResponse"
    assert mrr_at_k(rerank([fp, *others], veto), "http.JsonResponse", k=10) == 0.0


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

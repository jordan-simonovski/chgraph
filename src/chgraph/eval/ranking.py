"""Ranking eval for the git-evolution flagship (campaign Phase 6).

Proves the thesis "live code beats stale code" numerically: re-rank the same
lexical candidate pool with the hybrid weights vs a recency-blind baseline
(w_rec = w_cen = 0) and compare MRR@10. Success bar (campaign Phase 6): hybrid
beats blind by >= +0.10 MRR@10 on the staleness slice, <= 0.02 regression on the
general slice.

Pure re-rank + MRR; the search that produces the candidate pool is injected so
this is deterministic and needs no API spend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

SIGNALS = ("lex", "rec", "cen")


@dataclass(frozen=True)
class RankGolden:
    query: str
    expected: str          # exact qualified_name that should rank #1
    repo: str
    slice: str             # "staleness" | "general"


def rerank(candidates: list[dict], weights: dict[str, float]) -> list[str]:
    """Order candidate qualified_names by weighted signal score, descending.
    Stable: candidates that tie keep pool order — so a recency-blind ranking
    genuinely cannot float a fresh duplicate above its stale twin."""
    def score(c: dict) -> float:
        return sum(weights.get(s, 0.0) * float(c.get(s, 0.0)) for s in weights)
    return [c["qualified_name"] for c in sorted(candidates, key=score, reverse=True)]


def mrr_at_k(ranked: list[str], expected: str, k: int = 10) -> float:
    for i, qn in enumerate(ranked[:k], start=1):
        if qn == expected:
            return 1.0 / i
    return 0.0


SearchFn = Callable[[str, str], list[dict]]  # (query, repo) -> candidate pool


def evaluate(goldens: list[RankGolden], search_fn: SearchFn,
             hybrid_w: dict[str, float], blind_w: dict[str, float],
             k: int = 10) -> dict:
    by_slice: dict[str, dict] = {}
    for g in goldens:
        pool = search_fn(g.query, g.repo)
        h = mrr_at_k(rerank(pool, hybrid_w), g.expected, k)
        b = mrr_at_k(rerank(pool, blind_w), g.expected, k)
        s = by_slice.setdefault(g.slice, {"_h": [], "_b": []})
        s["_h"].append(h)
        s["_b"].append(b)
    out = {}
    for name, s in by_slice.items():
        hm = sum(s["_h"]) / len(s["_h"])
        bm = sum(s["_b"]) / len(s["_b"])
        out[name] = {"n": len(s["_h"]), "hybrid_mrr": hm, "blind_mrr": bm,
                     "delta": hm - bm}
    return out


def daemon_search(repo_checkout: str, query: str, limit: int = 50) -> list[dict]:
    """Default candidate-pool source: the daemon's ranked search. Fetches a wide
    pool (limit) with its lex/rec/cen components so we can re-rank it any way.
    Lazy import so the pure core above needs no chdb/daemon."""
    import os
    from chgraph.client import DaemonClient
    from chgraph.paths import ProjectPaths
    sock = ProjectPaths.for_repo(os.path.realpath(repo_checkout)).socket
    res = DaemonClient(sock).call("search", query=query, limit=limit)
    return res.get("items", []) if isinstance(res, dict) else res

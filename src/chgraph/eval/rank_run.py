"""Run the ranking eval (git-evolution campaign Phase 6) over a goldens file.

For each golden: fetch the daemon's candidate pool, annotate deprecation, then
score MRR@10 under three configs — blind (lexical only), hybrid (lex+rec+cen),
and hybrid+dep (adds the deprecation-demotion signal). Reports per-slice means
against the Phase-6 bar and writes an artifact.

    python -m chgraph.eval.rank_run

Deterministic, no API spend — reads the already-indexed corpus daemons.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from chgraph.client import DaemonClient
from chgraph.paths import ProjectPaths
from chgraph.eval.ranking import (RankGolden, annotate_deprecation, daemon_search,
                                  mrr_at_k, rerank)

REPO_ROOT = Path(__file__).resolve().parents[3]
K = 10
CONFIGS = {
    "blind":      {"lex": 0.35, "rec": 0.0,  "cen": 0.0,  "dep": 0.0},   # recency-blind baseline
    "hybrid":     {"lex": 0.35, "rec": 0.20, "cen": 0.15, "dep": 0.0},   # current v0.1 ranking
    "hybrid+dep": {"lex": 0.35, "rec": 0.20, "cen": 0.15, "dep": -1.0},  # + deprecation demotion
}


def load_rank_goldens(path: str | Path) -> list[RankGolden]:
    return [RankGolden(**g) for g in yaml.safe_load(Path(path).read_text())]


def _snippet_fn(checkout: str):
    sock = ProjectPaths.for_repo(os.path.realpath(checkout)).socket
    client = DaemonClient(sock)
    return lambda qn: (client.call("snippet", qualified_name=qn) or {}).get("text", "")


def run(goldens: list[RankGolden]) -> dict:
    # per-slice, per-config list of reciprocal ranks
    scores: dict[str, dict[str, list]] = defaultdict(lambda: {c: [] for c in CONFIGS})
    for g in goldens:
        checkout = str(REPO_ROOT / "evals" / ".cache" / g.repo)
        pool = daemon_search(checkout, g.query, limit=50)
        annotate_deprecation(pool, _snippet_fn(checkout))
        for cfg, w in CONFIGS.items():
            scores[g.slice][cfg].append(mrr_at_k(rerank(pool, w), g.expected, K))
    report = {"k": K, "slices": {}}
    for sl, per_cfg in scores.items():
        report["slices"][sl] = {"n": len(next(iter(per_cfg.values())))}
        for cfg, rr in per_cfg.items():
            report["slices"][sl][cfg] = sum(rr) / len(rr)
    return report


def main(argv=None) -> int:
    path = (argv or sys.argv[1:] or [str(REPO_ROOT / "evals" / "ranking_goldens.yaml")])[0]
    report = run(load_rank_goldens(path))
    for sl, r in report["slices"].items():
        print(f"[{sl}] n={r['n']}  blind={r['blind']:.3f}  hybrid={r['hybrid']:.3f}  "
              f"hybrid+dep={r['hybrid+dep']:.3f}", file=sys.stderr)
    st = report["slices"].get("staleness", {})
    gen = report["slices"].get("general", {})
    if st:
        gain = st["hybrid+dep"] - st["blind"]
        print(f"staleness gain (hybrid+dep - blind) = {gain:+.3f}  "
              f"[Phase-6 bar: >= +0.10]", file=sys.stderr)
    if gen:
        reg = gen["blind"] - gen["hybrid+dep"]
        print(f"general regression = {reg:+.3f}  [Phase-6 bar: <= 0.02]", file=sys.stderr)
    day = datetime.date.today().isoformat()
    out = REPO_ROOT / "evals" / "runs" / f"rank-{day}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"-> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

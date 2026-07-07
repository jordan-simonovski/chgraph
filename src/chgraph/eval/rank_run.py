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
# dep=-0.05: Phase-6 step-3 sweep on django@318a316a (see run provenance) found the
# passing window dep in [-0.02,-0.08] — both gates clear (staleness gain +0.112,
# general regression 0.000). Staleness gain saturates at any negative (same-commit
# deprecate+replace twins tie exactly on lex/rec/cen, so any nudge swaps them);
# general regresses only past ~-0.10, where the demotion exceeds the lexical margin of
# a false-positive live symbol (JsonResponse: flagged because its __init__ deprecates
# the `safe` PARAMETER, not the class). -0.05 sits mid-window with margin from both
# edges. The prototype's -1.0 was a veto, not a signal — it ejected that false positive
# from top-10. Detector precision (whole-symbol vs per-parameter deprecation) is known
# debt; the durable fix is a parse-time node property, routing through change-control.
CONFIGS = {
    "blind":      {"lex": 0.35, "rec": 0.0,  "cen": 0.0,  "dep": 0.0},    # recency-blind baseline
    "hybrid":     {"lex": 0.35, "rec": 0.20, "cen": 0.15, "dep": 0.0},    # current v0.1 ranking
    "hybrid+dep": {"lex": 0.35, "rec": 0.20, "cen": 0.15, "dep": -0.05},  # + deprecation demotion
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


STALENESS_GAIN_BAR = 0.10   # hybrid+dep must beat blind by >= this on the staleness slice
GENERAL_REG_BAR = 0.02      # ... at <= this regression on the general slice (campaign Phase 6)


def _corpus_shas(goldens: list[RankGolden]) -> dict:
    """SHA per repo the goldens reference — provenance so the numbers aren't an anecdote
    (validation-and-qa §4). A number without its corpus SHA is not comparable."""
    corpus = yaml.safe_load((REPO_ROOT / "evals" / "corpus.yaml").read_text())["repos"]
    by_name = {r["name"]: r["sha"] for r in corpus}
    return {repo: by_name.get(repo, "UNKNOWN") for repo in sorted({g.repo for g in goldens})}


def main(argv=None) -> int:
    path = (argv or sys.argv[1:] or [str(REPO_ROOT / "evals" / "ranking_goldens.yaml")])[0]
    goldens = load_rank_goldens(path)
    report = run(goldens)
    for sl, r in report["slices"].items():
        print(f"[{sl}] n={r['n']}  blind={r['blind']:.3f}  hybrid={r['hybrid']:.3f}  "
              f"hybrid+dep={r['hybrid+dep']:.3f}", file=sys.stderr)
    st = report["slices"].get("staleness", {})
    gen = report["slices"].get("general", {})
    gates = {}
    if st:
        gain = st["hybrid+dep"] - st["blind"]
        gates["staleness_gain"] = {"value": gain, "bar": STALENESS_GAIN_BAR,
                                   "pass": gain >= STALENESS_GAIN_BAR}
        print(f"staleness gain (hybrid+dep - blind) = {gain:+.3f}  "
              f"[Phase-6 bar: >= +{STALENESS_GAIN_BAR:.2f}]  "
              f"{'PASS' if gates['staleness_gain']['pass'] else 'FAIL'}", file=sys.stderr)
    if gen:
        reg = gen["blind"] - gen["hybrid+dep"]
        gates["general_regression"] = {"value": reg, "bar": GENERAL_REG_BAR,
                                       "pass": reg <= GENERAL_REG_BAR}
        print(f"general regression = {reg:+.3f}  [Phase-6 bar: <= {GENERAL_REG_BAR:.2f}]  "
              f"{'PASS' if gates['general_regression']['pass'] else 'FAIL'}", file=sys.stderr)

    # provenance so a reader can reproduce and compare (validation-and-qa §4)
    report["provenance"] = {
        "run_id": f"rank-{datetime.date.today().isoformat()}",
        "goldens_file": os.path.relpath(path, REPO_ROOT),
        "corpus_shas": _corpus_shas(goldens),
        "configs": CONFIGS,
        "gates": gates,
    }
    day = datetime.date.today().isoformat()
    out = REPO_ROOT / "evals" / "runs" / f"rank-{day}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"-> {out}", file=sys.stderr)
    return 0 if all(g["pass"] for g in gates.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

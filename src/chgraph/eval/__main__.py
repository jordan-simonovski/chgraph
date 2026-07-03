"""Eval harness CLI.

    python -m chgraph.eval --condition A                    # baseline (files only)
    python -m chgraph.eval --condition C                    # chgraph MCP
    python -m chgraph.eval --condition A --repo click       # one corpus repo (cheap)

Live runs need ANTHROPIC_API_KEY and the `claude` CLI (claude-agent-sdk driver).
Condition C also needs the checkout indexed (--reindex to force). Writes a report
to evals/runs/ — see validation-and-qa §2/§3.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from chgraph.paths import ProjectPaths
from chgraph.status import read_status
from chgraph.eval.goldens import CorpusRepo, load_corpus, load_goldens
from chgraph.eval.judge import RUBRIC_VERSION
from chgraph.eval.report import build_report, noise_band, write_report
from chgraph.eval.runner import run_eval

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCAFFOLD_MODEL = "claude-sonnet-5"
DEFAULT_JUDGE_MODEL = "claude-opus-4-8"


def _checkout(repo: CorpusRepo, cache: Path) -> str:
    """Full clone pinned to the corpus SHA. Idempotent.
    Full (not blobless): chgraph's git-evolution ingestion runs `git log --numstat`
    over all history, which needs historical blobs present locally — a blobless clone
    forces fragile per-blob lazy refetch and fails indexing.
    """
    dest = cache / repo.name
    if _is_partial_clone(dest):
        shutil.rmtree(dest)                      # stale blobless clone breaks git-ingest; re-clone full
    if not (dest / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet",
                        f"https://github.com/{repo.repo}.git", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", repo.sha], check=True)
    return str(dest)


def _is_partial_clone(dest: Path) -> bool:
    if not (dest / ".git").exists():
        return False
    r = subprocess.run(["git", "-C", str(dest), "config", "--get",
                        "remote.origin.partialclonefilter"], capture_output=True)
    return r.returncode == 0  # config present => a --filter clone


def _ensure_indexed(chgraph_bin: str, checkout: str, reindex: bool,
                    timeout_s: int = 600) -> None:
    """Index the checkout and BLOCK until the graph is ready. `chgraph index` is
    async (queues a job, returns immediately), so we poll status to a terminal state
    or the agent would query an empty graph.
    ponytail: verified by the live condition-C run, not a unit test (pure shell+fs glue).
    """
    paths = ProjectPaths.for_repo(os.path.realpath(checkout))
    if not reindex and read_status(paths.status_json).get("state") == "indexed":
        return                                   # pinned SHA already indexed; idempotent skip
    subprocess.run([chgraph_bin, "index", checkout], check=True)  # auto-starts daemon + queues
    for _ in range(timeout_s * 2):               # poll every 0.5s
        state = read_status(paths.status_json).get("state")
        if state in ("indexed", "degraded"):     # degraded still has a queryable graph
            return
        if state == "failed":
            raise RuntimeError(f"chgraph index failed for {checkout}: "
                               f"{read_status(paths.status_json).get('error')}")
        time.sleep(0.5)
    raise TimeoutError(f"indexing {checkout} did not reach a terminal state in {timeout_s}s")


def _run_id(condition: str) -> str:
    day = datetime.date.today().isoformat()
    return f"run-{day}-{condition}-{os.urandom(2).hex()}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="chgraph.eval")
    ap.add_argument("--condition", required=True, choices=["A", "B", "C"])
    ap.add_argument("--goldens", type=Path, default=REPO_ROOT / "evals" / "goldens")
    ap.add_argument("--corpus", type=Path, default=REPO_ROOT / "evals" / "corpus.yaml")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "runs")
    ap.add_argument("--cache", type=Path, default=REPO_ROOT / "evals" / ".cache")
    ap.add_argument("--model", default=DEFAULT_SCAFFOLD_MODEL, help="scaffold agent model")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--repo", help="only goldens for this corpus repo (cheaper)")
    ap.add_argument("--run-id")
    ap.add_argument("--reindex", action="store_true", help="condition C: force reindex")
    ap.add_argument("--max-budget-usd", type=float, default=0.75,
                    help="hard per-question spend ceiling (0 to disable)")
    ap.add_argument("--runs", type=int, default=1,
                    help="repeat the eval N times to measure the noise band (§2: N>=3)")
    args = ap.parse_args(argv)

    corpus = load_corpus(args.corpus)
    goldens = [g for g in load_goldens(args.goldens) if g.repo in corpus]
    if args.repo:
        goldens = [g for g in goldens if g.repo == args.repo]
    if not goldens:
        print("no goldens match (check --repo / corpus)", file=sys.stderr)
        return 1

    chgraph_cmd = [str(REPO_ROOT / ".venv" / "bin" / "chgraph"), "mcp"]

    def checkout_for(name: str) -> str:
        path = _checkout(corpus[name], args.cache)
        if args.condition == "C":
            _ensure_indexed(chgraph_cmd[0], path, args.reindex)
        return path

    base_id = args.run_id or _run_id(args.condition)
    corpus_shas = {name: corpus[name].sha for name in {g.repo for g in goldens}}
    print(f"{base_id}: condition {args.condition}, {len(goldens)} goldens x {args.runs} run(s), "
          f"scaffold={args.model} judge={args.judge_model}", file=sys.stderr)

    reports = []
    for i in range(args.runs):
        run_id = base_id if args.runs == 1 else f"{base_id}-r{i+1}"
        pairs = run_eval(goldens, condition=args.condition, checkout_for=checkout_for,
                         corpus=corpus, model=args.model, judge_model=args.judge_model,
                         chgraph_cmd=chgraph_cmd, max_budget_usd=args.max_budget_usd or None)
        report = build_report(
            run_id=run_id, condition=args.condition, pairs=pairs, corpus=corpus_shas,
            scaffold_model=args.model, judge_model=args.judge_model,
            rubric_version=RUBRIC_VERSION,
            golden_set_version=max(g.golden_set_version for g in goldens),
        )
        path = write_report(report, args.out)
        reports.append(report)
        s = report["summary"]
        print(f"  run {i+1}/{args.runs}: quality {s['quality']:.0%} ({s['passed']}/{s['n']}), "
              f"{s['tokens_total']:,} tokens -> {path}", file=sys.stderr)

    if args.runs > 1:
        band = noise_band(reports)
        (args.out / f"{base_id}-band.json").write_text(json.dumps(band, indent=2))
        q, t = band["quality"], band["tokens_per_q"]
        print(f"noise band ({band['n_runs']} runs): quality {q['mean']:.1%} "
              f"±{q['stdev']:.1%} (min {q['min']:.0%}, max {q['max']:.0%}); "
              f"{t['mean']:,.0f} tokens/q ±{t['stdev']:,.0f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

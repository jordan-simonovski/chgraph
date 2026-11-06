"""Compare HEAD native, working-tree native, and CodeGraph on existing ranking goldens.

Uses disposable corpus clones/stores; no paid agent calls or live daemon changes.
    uv run python evals/codegraph_corpus.py --output /tmp/codegraph-corpus.json
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]


def measure(repo, source, data):
    from chgraph.evolution import refresh_file_evolution
    from chgraph.gitingest import ingest_git
    from chgraph.indexer import index_repository
    from chgraph.search import search_graph
    from chgraph.store import Store

    store = Store.open(data)
    started = time.monotonic()
    try:
        if source == "codegraph":
            from chgraph.codegraph_import import prepare_codegraph, write_snapshot
            snapshot = prepare_codegraph(str(repo))
            counts = write_snapshot(store, "eval", snapshot)
            ingest_git(store, "eval", str(repo))
            refresh_file_evolution(store, "eval", 1)
            counts["rejected_edges"] = len(snapshot["rejected_edges"])
        else:
            counts = vars(index_repository(store, "eval", str(repo)))
        queries = []
        for golden in yaml.safe_load((ROOT / "evals" / "ranking_goldens.yaml").read_text()):
            if golden["repo"] != repo.name:
                continue
            pool = search_graph(store, "eval", query=golden["query"], limit=50).items
            ranked = [n["qualified_name"] for n in pool]
            rank = ranked.index(golden["expected"]) + 1 if golden["expected"] in ranked else None
            queries.append({**golden, "rank": rank, "rr_at_10": 1 / rank if rank and rank <= 10 else 0,
                            "top10": ranked[:10]})
        return {"counts": counts, "queries": queries, "seconds": time.monotonic() - started}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    goldens = ROOT / "evals" / "ranking_goldens.yaml"
    names = sorted({g["repo"] for g in yaml.safe_load(goldens.read_text())})
    report = {"baseline_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "goldens_sha256": hashlib.sha256(goldens.read_bytes()).hexdigest(),
              "judge": "deterministic exact-symbol MRR@10; no agent answer-quality evaluation",
              "runs": {}}
    with tempfile.TemporaryDirectory(prefix="chgraph-corpus-") as work:
        work = Path(work)
        baseline = work / "baseline"
        archive = subprocess.check_output(["git", "archive", "HEAD", "src/chgraph"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(baseline, filter="data")
        repos = work / "repos"
        repos.mkdir()
        for name in names:
            subprocess.run(["git", "clone", "--shared", "--quiet", str(ROOT / "evals" / ".cache" / name),
                            str(repos / name)], check=True, capture_output=True)
        report["corpus_shas"] = {name: subprocess.check_output(
            ["git", "-C", str(repos / name), "rev-parse", "HEAD"], text=True).strip() for name in names}
        for phase, package, source in (("baseline_native", baseline / "src", "native"),
                                        ("current_native", ROOT / "src", "native"),
                                        ("codegraph", ROOT / "src", "codegraph")):
            report["runs"][phase] = {}
            for name in names:
                env = {**os.environ, "PYTHONPATH": str(package), "CHGRAPH_RANK_VECTOR": "off"}
                started = time.monotonic()
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child",
                    str(repos / name), source, str(work / f"{phase}-{name}")], env=env,
                    capture_output=True, text=True, timeout=600)
                data = json.loads(result.stdout) if result.returncode == 0 else {"error": result.stderr[-6000:]}
                report["runs"][phase][name] = data
                print(f"{phase} {name}: {'ERROR' if 'error' in data else 'complete'} ({time.monotonic() - started:.1f}s)", flush=True)
                args.output.write_text(json.dumps(report, indent=2) + "\n")
    report["native_ranks_unchanged"] = all(
        "queries" in report["runs"]["baseline_native"][name]
        and "queries" in report["runs"]["current_native"][name]
        and report["runs"]["baseline_native"][name]["queries"] == report["runs"]["current_native"][name]["queries"]
        for name in names)
    report["mrr_at_10"] = {phase: {name: sum(q["rr_at_10"] for q in run["queries"]) / len(run["queries"])
        for name, run in runs.items() if run.get("queries")} for phase, runs in report["runs"].items()}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    assert report["native_ranks_unchanged"], "native golden ranking changed; inspect report"
    return 1 if any("error" in run for run in report["runs"]["codegraph"].values()) else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        print(json.dumps(measure(Path(sys.argv[2]), sys.argv[3], sys.argv[4])))
    else:
        raise SystemExit(main())

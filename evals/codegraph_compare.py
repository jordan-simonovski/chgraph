"""Run the disposable CodeGraph → ClickHouse extraction experiment.

    .venv/bin/python evals/codegraph_compare.py --output /tmp/codegraph-comparison.json

Exit 1 means a graph-quality or sync-convergence gate failed, not a broken importer.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from chgraph.codegraph_import import prepare_snapshot, write_snapshot
from chgraph.parse_python import _module_name, parse_file
from chgraph.search import search_graph
from chgraph.store import Store
from chgraph.traverse import trace_path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)


def _quality(edges: set[tuple[str, str, str]], expected: dict) -> dict:
    missing = [pair for pair in expected["calls"] if (*pair, "calls") not in edges]
    false_calls = [pair for pair in expected["not_calls"] if (*pair, "calls") in edges]
    unexpected = sorted((s, t) for s, t, kind in edges
                        if kind == "calls" and [s, t] not in expected["calls"])
    return {"expected_calls": len(expected["calls"]), "missing_calls": missing,
            "false_calls": false_calls, "unexpected_calls": unexpected,
            "passed": not missing and not unexpected}


def _native(repo: Path, expected: dict) -> dict:
    nodes, edges = {}, []
    for path in sorted(repo.rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        parsed, refs = parse_file(rel, path.read_bytes())
        for node in parsed:
            name = node["qualified_name"].removeprefix(_module_name(rel) + ".")
            nodes[node["qualified_name"]] = f"{rel}::{name}"
        edges.extend(refs)
    calls = {(nodes[e["source"]], nodes[e["target"]], "calls") for e in edges
             if e["type"] == "CALLS" and e["source"] in nodes and e["target"] in nodes}
    result = _quality(calls, expected)
    result["unresolved_call_targets"] = sorted({e["target"] for e in edges
        if e["type"] == "CALLS" and e["target"] not in nodes})
    return result


def _semantic_state(snapshot: dict, edges: set) -> dict:
    state = {"edges": edges}
    for table, volatile in (("nodes", {"updated_at"}),
                            ("files", {"indexed_at", "modified_at"}),
                            ("unresolved_refs", {"id"})):
        state[table] = {json.dumps({k: v for k, v in row.items() if k not in volatile}, sort_keys=True)
                        for row in snapshot[table]}
    return state


def _import_and_check(repo: Path, data: Path, expected: dict) -> tuple[dict, dict]:
    path = repo / ".codegraph" / "codegraph.db"
    snapshot = prepare_snapshot(path, repo)
    store = Store.open(data)
    try:
        counts = write_snapshot(store, "fixture", snapshot)
        loaded_nodes = store.rows("SELECT * FROM chgraph.nodes FINAL")
        nodes = {json.loads(n["properties"])["codegraph"]["id"]: json.loads(n["properties"])["codegraph"]
                 for n in loaded_nodes}
        identities = {json.loads(n["properties"])["codegraph"]["id"]: n["qualified_name"] for n in loaded_nodes}
        assert sorted(nodes.values(), key=lambda n: n["id"]) == sorted(
            snapshot["nodes"], key=lambda n: n["id"])
        records = [e for row in store.rows("SELECT properties FROM chgraph.edges FINAL")
                   for e in json.loads(row["properties"])["codegraph"]]
        assert sorted([*records, *snapshot["rejected_edges"]], key=lambda e: e["id"]) == sorted(snapshot["edges"], key=lambda e: e["id"])
        calls = {(identities[e["source"]], identities[e["target"]]) for e in records if e["kind"] == "calls"}
        for source in {s for s, _ in calls}:
            actual = {r["node"] for r in trace_path(store, "fixture", source, depth=1) if r["depth"]}
            assert actual == {t for s, t in calls if s == source}
        for target in {t for _, t in calls}:
            actual = {r["node"] for r in trace_path(
                store, "fixture", target, direction="callers", depth=1) if r["depth"]}
            assert actual == {s for s, t in calls if t == target}
        names = {id: f"{n['file_path']}::{n['qualified_name'].replace('::', '.')}"
                 for id, n in nodes.items()}
        normalized = {(names[e["source"]], names[e["target"]], e["kind"]) for e in records}
        quality = _quality(normalized, expected)
        quality["missing_references"] = [p for p in expected["references"]
                                         if (*p, "references") not in normalized]
        quality["passed"] &= not quality["missing_references"]
        raw_quality = _quality({(names[e["source"]], names[e["target"]], e["kind"])
                                for e in snapshot["edges"]}, expected)
        overload_lookup = [n["qualified_name"] for n in search_graph(
            store, "fixture", query="convert").items if n["name"] == "convert"]
        # Compare callsite evidence too; row IDs and timestamps depend on insertion order.
        semantic = {(names[e["source"]], names[e["target"]], e["kind"], e["line"], e["col"],
                     e["metadata"], e["provenance"]) for e in records}
        return {"counts": counts, "quality": quality, "raw_quality": raw_quality,
                "rejected_edges": len(snapshot["rejected_edges"]), "lossless_import": True,
                "overload_lookup": overload_lookup,
                "traversal_matches": True, "producer": snapshot["metadata"]}, _semantic_state(snapshot, semantic)
    finally:
        store.close()


def compare() -> dict:
    if shutil.which("codegraph") is None:
        raise RuntimeError("CodeGraph CLI 1.6.2 is required for this experiment")
    expected = json.loads((FIXTURES / "codegraph_expected.json").read_text())
    with tempfile.TemporaryDirectory(prefix="chgraph-codegraph-") as work:
        root = Path(work)
        repo = root / "repo"
        shutil.copytree(FIXTURES / "codegraph", repo)
        _run("git", "init", "-q", str(repo))
        _run("git", "-C", str(repo), "add", ".")
        _run("git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-qm", "fixture")
        baseline = _native(repo, expected)
        _run("codegraph", "init", "--yes", str(repo))
        initial, _ = _import_and_check(repo, root / "initial", expected)

        # Keep consumer.py untouched while its re-exported definition changes identity.
        package = repo / "src" / "fixture_pkg"
        helpers = package / "helpers.py"
        helpers.write_text(helpers.read_text().replace("target", "renamed"))
        api = package / "api.py"
        api.write_text(api.read_text()
            .replace("import target as aliased", "import renamed as aliased")
            .replace("from .helpers import target", "from .helpers import renamed as target")
            .replace("helpers.target()", "helpers.renamed()"))
        (package / "__init__.py").write_text("from .helpers import renamed as target\n")
        edited_expected = json.loads(json.dumps(expected).replace("target", "renamed"))
        _run("codegraph", "sync", str(repo))
        synced, sync_state = _import_and_check(repo, root / "synced", edited_expected)
        _run("codegraph", "index", str(repo))
        rebuilt, rebuild_state = _import_and_check(repo, root / "rebuilt", edited_expected)
        converged = sync_state == rebuild_state
        return {"native": baseline, "codegraph": initial, "after_sync": synced,
                "after_rebuild": rebuilt, "sync_matches_rebuild": converged,
                "sync_only": {key: sorted(sync_state[key] - rebuild_state[key], key=str) for key in sync_state},
                "rebuild_only": {key: sorted(rebuild_state[key] - sync_state[key], key=str) for key in sync_state},
                "fixture_gate_passed": bool(initial["quality"]["passed"]
                    and synced["quality"]["passed"] and rebuilt["quality"]["passed"] and converged)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compare()
    except subprocess.CalledProcessError as exc:
        parser.exit(2, f"command failed: {exc.cmd}\n{exc.stderr or exc.stdout}\n")
    except (RuntimeError, ValueError, AssertionError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"experiment failed: {exc}\n")
    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(output)
    print(output, end="")
    return 0 if result["fixture_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

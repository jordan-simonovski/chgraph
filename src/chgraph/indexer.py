"""Repo -> graph. Batch writes only (INV-5); honest degradation reporting (INV-3)."""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from chgraph.evolution import refresh_file_evolution
from chgraph.gitingest import ingest_git, verify_git_counts
from chgraph.parse_python import parse_file
from chgraph.store import Store

# Candidate threshold, label OPEN (validation-and-qa §5): calibrate before trusting.
# Plain Python code sits well above this; near-zero means the parser silently failed.
MIN_NODES_PER_KLOC = 5.0


@dataclass
class IndexResult:
    version: int
    files_total: int
    files_done: int
    nodes: int
    edges: int
    degraded_reasons: list[str] = field(default_factory=list)


def _py_files(repo_root: str) -> list[str]:
    out = subprocess.run(["git", "-C", repo_root, "ls-files", "*.py"],
                         check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def _batch_insert(store: Store, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        tmp = f.name
    try:
        store.exec(f"INSERT INTO {table} SELECT * FROM file('{tmp}', 'JSONEachRow')")
    finally:
        os.unlink(tmp)


def index_repository(store: Store, project: str, repo_root: str,
                     on_progress: Callable[[int, int], None] | None = None) -> IndexResult:
    version = store.rows(
        f"SELECT coalesce(max(version), 0) + 1 AS v FROM chgraph.nodes WHERE project = '{project}'"
    )[0]["v"]

    files = _py_files(repo_root)
    node_rows: list[dict] = []
    edge_rows: list[dict] = []
    total_lines = 0
    done = 0
    for rel in files:
        src = open(os.path.join(repo_root, rel), "rb").read()
        total_lines += src.count(b"\n") + 1
        nodes, edges = parse_file(rel, src)   # pure Python — never raises on bad syntax,
        for n in nodes:                       # tree-sitter yields a partial tree instead
            node_rows.append({**n, "project": project, "version": version})
        for e in edges:
            edge_rows.append({**e, "project": project, "version": version})
        done += 1
        if on_progress:
            on_progress(done, len(files))

    _batch_insert(store, "chgraph.nodes", node_rows)
    _batch_insert(store, "chgraph.edges", edge_rows)
    store.exec("OPTIMIZE TABLE chgraph.nodes FINAL")
    store.exec("OPTIMIZE TABLE chgraph.edges FINAL")

    reasons: list[str] = []
    reasons += verify_git_counts(repo_root, ingest_git(store, project, repo_root))
    refresh_file_evolution(store, project, version)

    symbol_nodes = sum(1 for n in node_rows if n["label"] != "File")
    kloc = max(total_lines / 1000.0, 0.001)
    density = symbol_nodes / kloc
    if files and density < MIN_NODES_PER_KLOC:
        reasons.append(
            f"nodes-per-KLOC sanity: {density:.1f} < {MIN_NODES_PER_KLOC} "
            f"({symbol_nodes} symbols over {total_lines} lines) — parser likely failed silently"
        )

    return IndexResult(version=version, files_total=len(files), files_done=done,
                       nodes=len(node_rows), edges=len(edge_rows), degraded_reasons=reasons)

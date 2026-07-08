"""Repo -> graph. Batch writes only (INV-5); honest degradation reporting (INV-3)."""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from chgraph import embeddings
from chgraph.evolution import _q, refresh_file_evolution
from chgraph.gitingest import ingest_git, verify_git_counts
from chgraph.parse_python import parse_file
from chgraph.store import Store
from chgraph.text import subtokens

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
        f"SELECT coalesce(max(version), 0) + 1 AS v FROM chgraph.nodes WHERE project = {_q(project)}"
    )[0]["v"]

    # ponytail: TRUNCATE-then-reload is safe because one data dir == one project
    # (mirrors gitingest.ingest_git). Without it, symbols/files deleted or renamed
    # since the last index keep their old-version rows forever: FINAL only collapses
    # rows sharing the same ORDER BY key, so a removed row has no same-key successor
    # to collapse against and lingers as a ghost node/edge.
    store.exec("TRUNCATE TABLE chgraph.nodes")
    store.exec("TRUNCATE TABLE chgraph.edges")
    store.exec("TRUNCATE TABLE chgraph.embeddings")

    files = _py_files(repo_root)
    node_rows: list[dict] = []
    edge_rows: list[dict] = []
    embed_texts: list[str] = []       # parallel to embed_qns: symbol embed-text (name + docstring)
    embed_qns: list[str] = []
    total_lines = 0
    done = 0
    for rel in files:
        src = open(os.path.join(repo_root, rel), "rb").read()
        total_lines += src.count(b"\n") + 1
        nodes, edges = parse_file(rel, src)   # pure Python — never raises on bad syntax,
        for n in nodes:                       # tree-sitter yields a partial tree instead
            doc = n.pop("doc", "")            # transient: embed-text input, not a nodes column
            if n["label"] != "File":
                embed_qns.append(n["qualified_name"])
                embed_texts.append((" ".join(subtokens(n["name"])) + ". " + doc).strip())
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

    # Optional vector signal (ADR-0004): embed symbols if fastembed is installed, else skip
    # silently — core indexing is unaffected and the vector signal stays inert.
    if embeddings.available() and embed_qns:
        vecs = embeddings.embed(embed_texts)
        _batch_insert(store, "chgraph.embeddings", [
            {"project": project, "qualified_name": qn, "vec": v, "version": version}
            for qn, v in zip(embed_qns, vecs)
        ])
        store.exec("OPTIMIZE TABLE chgraph.embeddings FINAL")

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

"""Repo -> graph. Batch writes only (INV-5); honest degradation reporting (INV-3)."""
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from chgraph import embeddings
from chgraph.evolution import _q, refresh_file_evolution
from chgraph.gitingest import _batch_load, ingest_git, verify_git_counts
from chgraph.parse_python import parse_file
from chgraph.paths import read_source
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


def index_repository(store: Store, project: str, repo_root: str,
                     on_progress: Callable[[int, int], None] | None = None) -> IndexResult:
    version = store.rows(
        f"SELECT coalesce(max(version), 0) + 1 AS v FROM chgraph.nodes WHERE project = {_q(project)}"
    )[0]["v"]

    # Limit replacement to this identity so a staged rebuild preserves the active graph.
    for table in ("nodes", "edges", "embeddings"):
        store.delete_rows(f"chgraph.{table}", f"project={_q(project)}")

    files = _py_files(repo_root)
    node_rows: list[dict] = []
    edge_rows: list[dict] = []
    embed_texts: list[str] = []       # parallel to embed_qns: symbol embed-text (name + docstring)
    embed_qns: list[str] = []
    total_lines = 0
    done = 0
    for rel in files:
        src = read_source(repo_root, rel)
        total_lines += src.count(b"\n") + 1
        nodes, edges = parse_file(rel, src)   # pure Python — never raises on bad syntax,
        content_hash = hashlib.sha256(src).hexdigest()
        for n in nodes:                       # tree-sitter yields a partial tree instead
            n["properties"] = json.dumps({**json.loads(n["properties"]),
                                           "content_hash": content_hash})
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

    _batch_load(store, "chgraph.nodes", node_rows)
    _batch_load(store, "chgraph.edges", edge_rows)
    store.exec("OPTIMIZE TABLE chgraph.nodes FINAL")
    store.exec("OPTIMIZE TABLE chgraph.edges FINAL")

    # Optional vector signal (ADR-0004): embed symbols if fastembed is installed, else skip
    # silently — core indexing is unaffected and the vector signal stays inert.
    if embeddings.available() and embed_qns:
        vecs = embeddings.embed(embed_texts)
        _batch_load(store, "chgraph.embeddings", [
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

    store.checkpoint()
    return IndexResult(version=version, files_total=len(files), files_done=done,
                       nodes=len(node_rows), edges=len(edge_rows), degraded_reasons=reasons)

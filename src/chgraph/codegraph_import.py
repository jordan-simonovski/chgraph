"""CodeGraph extraction, source validation and ClickHouse snapshot ingestion."""
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from chgraph.gitingest import _batch_load
from chgraph.parse_python import _module_name, _overload_lines, _shadowed_parameter_calls, parse_file
from chgraph.paths import read_source
from chgraph.store import Store

CODEGRAPH_VERSION = "1.6.2"
CODEGRAPH_SCHEMA_VERSION = 11
CODEGRAPH_EXTRACTION_VERSION = "27"

_COLUMNS = {
    "nodes": {"id", "kind", "name", "qualified_name", "file_path", "start_line", "end_line"},
    "edges": {"id", "source", "target", "kind", "metadata", "line", "col", "provenance"},
    "files": {"path", "content_hash", "errors"},
    "unresolved_refs": {"from_node_id", "reference_name", "status"},
    "project_metadata": {"key", "value"},
    "schema_versions": {"version"},
}


def _read_snapshot(path: str | Path) -> dict:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # One SQLite read snapshot, including committed WAL rows.
        for table, required in _COLUMNS.items():
            columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
            if not required <= columns:
                raise ValueError(f"unsupported CodeGraph schema: {table} missing columns {required - columns}")
        version = db.execute("SELECT max(version) FROM schema_versions").fetchone()[0]
        if version != CODEGRAPH_SCHEMA_VERSION:
            raise ValueError(f"unsupported CodeGraph schema version {version!r}; expected {CODEGRAPH_SCHEMA_VERSION}")
        metadata = dict(db.execute("SELECT key, value FROM project_metadata"))
        if metadata.get("indexed_with_version") != CODEGRAPH_VERSION:
            raise ValueError(f"unsupported CodeGraph producer version; expected {CODEGRAPH_VERSION}")
        if metadata.get("indexed_with_extraction_version") != CODEGRAPH_EXTRACTION_VERSION:
            raise ValueError(f"unsupported CodeGraph extraction version; expected {CODEGRAPH_EXTRACTION_VERSION}")
        if metadata.get("index_state") != "complete":
            raise ValueError("CodeGraph index must be complete before import")
        snapshot = {table: [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
                    for table in _COLUMNS}
    ids = {n["id"] for n in snapshot["nodes"]}
    if len(ids) != len(snapshot["nodes"]) or any(not i for i in ids):
        raise ValueError("duplicate or empty CodeGraph node identity")
    for node in snapshot["nodes"]:
        if not 1 <= node["start_line"] <= node["end_line"] <= 2**32 - 1:
            raise ValueError(f"invalid source range for {node['id']}")
    for edge in snapshot["edges"]:
        if edge["source"] not in ids or edge["target"] not in ids:
            raise ValueError(f"missing CodeGraph edge endpoint: {edge['id']}")
    if any(ref["from_node_id"] not in ids for ref in snapshot["unresolved_refs"]):
        raise ValueError("missing CodeGraph unresolved reference source node")
    if any(json.loads(f["errors"] or "[]") for f in snapshot["files"]):
        raise ValueError("CodeGraph files contain extraction errors")
    snapshot["metadata"] = metadata
    return snapshot


def prepare_snapshot(path: str | Path, repo_root: str | Path) -> dict:
    snapshot = _read_snapshot(path)
    files = {f["path"]: f for f in snapshot["files"]}
    shadowed, native_properties, validation_errors, overloads = {}, {}, {}, set()
    for relative, record in files.items():
        source = read_source(repo_root, relative)
        if hashlib.sha256(source).hexdigest() != record["content_hash"]:
            raise ValueError(f"stale CodeGraph source: {relative}; reindex before importing")
        if relative.endswith(".py"):
            try:
                shadowed[relative] = _shadowed_parameter_calls(source)
                overloads.update((relative, line) for line in _overload_lines(source))
            except (SyntaxError, ValueError) as exc:
                shadowed[relative] = None
                validation_errors[relative] = f"Python binding validation unavailable: {exc}"
            parsed, _ = parse_file(relative, source)
            native_properties.update({n["qualified_name"]: json.loads(n["properties"]) for n in parsed})

    identities = {}
    for node in snapshot["nodes"]:
        relative = node["file_path"]
        if relative not in files:
            raise ValueError(f"missing file manifest for {relative}")
        if node["kind"] == "file":
            identity = relative
        elif relative.endswith(".py"):
            identity = f"{_module_name(relative)}.{node['qualified_name'].replace('::', '.')}"
        else:
            identity = f"{relative}::{node['qualified_name']}"
        identities[node["id"]] = identity
    representatives = {}
    for node in snapshot["nodes"]:
        qn = identities[node["id"]]
        previous = representatives.get(qn)
        if previous is None or node["start_line"] > previous["start_line"]:
            representatives[qn] = node
    snapshot["identities"] = {id: qn if representatives[qn]["id"] == id else id for id, qn in identities.items()}
    snapshot["node_properties"] = {
        n["id"]: {**native_properties.get(identities[n["id"]], {}), "codegraph": n,
                  "declaration_only": (n["file_path"], n["start_line"]) in overloads,
                  "content_hash": files[n["file_path"]]["content_hash"]}
        for n in snapshot["nodes"]}
    nodes = {n["id"]: n for n in snapshot["nodes"]}
    snapshot["rejected_edges"] = []
    for edge in snapshot["edges"]:
        sites = shadowed.get(nodes[edge["source"]]["file_path"], set())
        if edge["kind"] == "calls" and (sites is None or (edge["line"], edge["col"]) in sites):
            snapshot["rejected_edges"].append(edge)
    snapshot["validation_errors"] = validation_errors
    return snapshot


def _codegraph_command(command: list[str], cancel, timeout: int) -> str:
    if cancel is not None and cancel.is_set():
        raise ValueError("CodeGraph indexing cancelled")
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise ValueError("CodeGraph indexing cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    out, err = process.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command, out, err)
            return out
        finally:
            # The launcher may exit while descendants still hold its pipes open.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()


def prepare_codegraph(repo_root: str, *, cancel=None) -> dict:
    try:
        version = _codegraph_command(["codegraph", "--version"], cancel, 10).strip()
        if version != CODEGRAPH_VERSION:
            raise ValueError(f"CodeGraph {CODEGRAPH_VERSION} required; found {version}")
        path = Path(repo_root) / ".codegraph" / "codegraph.db"
        command = ["codegraph", "index", repo_root] if path.exists() else ["codegraph", "init", "--yes", repo_root]
        _codegraph_command(command, cancel, 600)
    except FileNotFoundError as exc:
        raise ValueError(f"CodeGraph CLI {CODEGRAPH_VERSION} is required for --source codegraph") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"CodeGraph failed: {exc.stderr or exc.stdout}") from exc
    return prepare_snapshot(path, repo_root)


def import_codegraph_snapshot(store: Store, project: str, path: str | Path) -> dict:
    # shortcut: buffers a whole snapshot in an empty store; add streaming/publication before production use.
    snapshot = _read_snapshot(path)
    for table in ("nodes", "edges", "embeddings", "git_commits", "git_file_changes", "file_evolution"):
        if store.rows(f"SELECT count() AS n FROM chgraph.{table}")[0]["n"]:
            raise ValueError("CodeGraph experiment requires an empty store; refusing to overwrite data")

    return write_snapshot(store, project, snapshot)


def write_snapshot(store: Store, project: str, snapshot: dict) -> dict:
    from chgraph.evolution import _q

    for table in ("nodes", "edges"):
        if store.rows(f"SELECT count() AS n FROM chgraph.{table} WHERE project={_q(project)}")[0]["n"]:
            raise ValueError("snapshot destination project must be empty")
    identities = snapshot.get("identities", {n["id"]: n["id"] for n in snapshot["nodes"]})
    nodes = [{
        "project": project, "label": "Function" if n["kind"] == "method" else n["kind"].title(), "name": n["name"],
        # Upstream qualified names collide across files; retain its ID as graph identity.
        "qualified_name": identities[n["id"]], "file_path": n["file_path"],
        "start_line": n["start_line"], "end_line": n["end_line"],
        "properties": json.dumps(snapshot.get("node_properties", {}).get(n["id"], {"codegraph": n})), "version": 1,
    } for n in snapshot["nodes"]]
    evidence = defaultdict(list)
    rejected = {e["id"] for e in snapshot.get("rejected_edges", [])}
    for edge in snapshot["edges"]:
        if edge["id"] not in rejected:
            evidence[(identities[edge["source"]], identities[edge["target"]], edge["kind"])].append(edge)
    edges = [{
        "project": project, "source": source, "target": target, "type": kind.upper(),
        "properties": json.dumps({"codegraph": records}), "version": 1,
    } for (source, target, kind), records in evidence.items()]
    _batch_load(store, "chgraph.nodes", nodes)
    _batch_load(store, "chgraph.edges", edges)
    _batch_load(store, "chgraph.meta", [{
        "key": f"codegraph_snapshot:{project}", "version": 1,
        "value": json.dumps({"project": project, "schema_version": CODEGRAPH_SCHEMA_VERSION,
                             "metadata": snapshot["metadata"], "files": snapshot["files"],
                             "unresolved_refs": snapshot["unresolved_refs"],
                             "validation_errors": snapshot.get("validation_errors", {}),
                             "rejected_edges": snapshot.get("rejected_edges", [])}),
    }])
    store.checkpoint()
    return {"nodes": len(nodes), "edges": len(snapshot["edges"]),
            "relationships": len(edges), "files": len(snapshot["files"]),
            "unresolved_refs": len(snapshot["unresolved_refs"])}

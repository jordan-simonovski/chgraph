"""Bounded graph context and change impact, combining structure with Git evidence."""
import hashlib
import json
import time

from chgraph.paths import MAX_SOURCE_BYTES, read_source
from chgraph.evolution import _q
from chgraph.search import QUERY_BUDGET, search_graph
from chgraph.traverse import MAX_DEPTH

_BUDGET = QUERY_BUDGET


def graph_freshness(store, project, repo_root):
    rows = store.rows("SELECT JSONExtractRaw(value, 'files') AS files FROM chgraph.meta FINAL WHERE key="
                      + _q("codegraph_snapshot:" + project) + " " + _BUDGET)
    if rows:
        files = json.loads(rows[0]["files"])
    else:
        files = store.rows("SELECT file_path AS path, JSONExtractString(properties, 'content_hash') AS content_hash "
                           "FROM chgraph.nodes FINAL WHERE project=" + _q(project)
                           + " AND label='File' ORDER BY file_path LIMIT 10001 " + _BUDGET)
        if not files or len(files) > 10000:
            return "unverified graph dependencies: native file manifest unavailable or exceeds budget"
    deadline, remaining = time.monotonic() + 0.5, 16 * 1024 * 1024
    for file in files:
        if not file["content_hash"]:
            return f"unverified graph dependency {file['path']}: missing indexed source hash"
        if time.monotonic() >= deadline or remaining <= 0:
            return "unverified graph dependencies: freshness check reached its time/read budget"
        try:
            content = read_source(repo_root, file["path"], max_bytes=min(MAX_SOURCE_BYTES, remaining))
        except (OSError, ValueError) as exc:
            return f"unverified graph dependency {file['path']}: {exc}"
        remaining -= len(content)
        if hashlib.sha256(content).hexdigest() != file["content_hash"]:
            return f"stale graph dependency {file['path']}; reindex before using CALLS evidence"
    return None


def bounded_walk(store, project, roots, *, direction="both", depth=2, max_nodes=30):
    if direction not in ("both", "callers", "callees"):
        raise ValueError("direction must be both|callers|callees")
    max_nodes = max(1, min(int(max_nodes), 200))
    paths = {root: [root] for root in roots[:max_nodes]}
    frontier, edges = list(paths), {}
    truncated = len(roots) > max_nodes
    for _ in range(max(1, min(int(depth), MAX_DEPTH))):
        if not frontier:
            break
        literals = ",".join(_q(n) for n in frontier)
        conditions = []
        if direction in ("both", "callees"):
            conditions.append(f"source IN ({literals})")
        if direction in ("both", "callers"):
            conditions.append(f"target IN ({literals})")
        remaining = max_nodes * 2 - len(edges)
        rows = store.rows(f"""SELECT source, target, properties FROM chgraph.edges FINAL
            WHERE project={_q(project)} AND type='CALLS' AND ({' OR '.join(conditions)})
            ORDER BY source, target LIMIT {remaining + 1} {_BUDGET}""")
        if len(rows) > remaining:
            truncated = True
        following = []
        for edge in rows[:remaining]:
            source, target = edge["source"], edge["target"]
            parent, child = (source, target) if source in frontier else (target, source)
            if child not in paths:
                if len(paths) >= max_nodes:
                    truncated = True
                    continue
                paths[child] = [*paths[parent], child]
                following.append(child)
            edges[(source, target)] = edge
        frontier = following
        if len(edges) >= max_nodes * 2:
            truncated = True
            break
    return {"paths": paths, "edges": list(edges.values()), "truncated": truncated}


def _nodes(store, project, identities):
    if not identities:
        return []
    literals = ",".join(_q(n) for n in identities)
    return store.rows(f"SELECT * FROM chgraph.nodes FINAL WHERE project={_q(project)} "
                      f"AND qualified_name IN ({literals}) ORDER BY file_path, start_line, qualified_name {_BUDGET}")


def explore(store, project, repo_root, query, *, max_chars=12000, max_nodes=30, depth=2):
    max_nodes = max(1, min(int(max_nodes), 200))
    page = search_graph(store, project, query=query, limit=min(max_nodes, 5), bounded=True, match_tokens=True)
    roots = [n["qualified_name"] for n in page.items]
    graph = bounded_walk(store, project, roots, depth=depth, max_nodes=max_nodes)
    nodes = _nodes(store, project, graph["paths"])
    dependency_warning = graph_freshness(store, project, repo_root)
    warnings, files, valid = ([dependency_warning] if dependency_warning else []), {}, []
    for node in nodes:
        relative = node["file_path"]
        if relative not in files:
            try:
                files[relative] = read_source(repo_root, relative)
            except (OSError, ValueError) as exc:
                files[relative] = None
                warnings.append(f"unreadable source {relative}: {exc}")
        content = files[relative]
        expected = json.loads(node["properties"]).get("content_hash")
        if content is None:
            continue
        if not expected or hashlib.sha256(content).hexdigest() != expected:
            warning = f"stale or unverified source {relative}; reindex before using its graph evidence"
            if warning not in warnings:
                warnings.append(warning)
            continue
        valid.append(node)
    known = {n["qualified_name"] for n in valid}
    truncated = bool(dependency_warning) or graph["truncated"] or page.has_more or len(valid) < len(graph["paths"])
    limit = max(1, min(int(max_chars), 50000))
    parts, used = [], 0

    def add(text):
        nonlocal used, truncated
        if used + len(text) > limit:
            truncated = True
            return False
        parts.append(text)
        used += len(text)
        return True

    add(f"Graph generation: {project}\nEntry points:\n")
    if dependency_warning:
        add(dependency_warning + "\n")
    for root in roots:
        if root in known:
            add(f"  {root}\n")
    add("CALLS evidence:\n")
    for edge in graph["edges"]:
        if dependency_warning:
            break
        if edge["source"] not in known or edge["target"] not in known:
            continue
        records = json.loads(edge["properties"]).get("codegraph", [])
        evidence = []
        for record in records:
            metadata = json.loads(record["metadata"] or "{}")
            evidence.append(f"line {record['line']}: {metadata.get('resolvedBy', 'unclassified')}")
        add(f"  {edge['source']} -> {edge['target']} ({'; '.join(evidence) or 'native extraction'})\n")
    spans = {}
    for node in valid:
        spans.setdefault(node["file_path"], []).append((node["start_line"], node["end_line"]))
    for relative, ranges in spans.items():
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        lines = files[relative].decode(errors="replace").splitlines()
        for start, end in merged:
            if not add(f"\nSource {relative}:{start}-{end}\n"):
                break
            for number in range(start, min(end, len(lines)) + 1):
                if not add(f"{number}: {lines[number - 1]}\n"):
                    break
    if truncated:
        warnings.append("context omitted candidates, graph expansion, or source because of limits or freshness")
    return {"context": "".join(parts), "generation": project, "truncated": truncated, "warnings": warnings}


def impact(store, project, qualified_name, *, depth=5, max_nodes=100, repo_root=None):
    warning = None
    if repo_root:
        warning = graph_freshness(store, project, repo_root)
        if warning and warning.startswith("stale"):
            raise ValueError(warning)
    graph = bounded_walk(store, project, [qualified_name], direction="callers", depth=depth, max_nodes=max_nodes)
    nodes = _nodes(store, project, graph["paths"])
    by_name = {n["qualified_name"]: n for n in nodes}
    if qualified_name not in by_name:
        raise ValueError(f"unknown symbol: {qualified_name}")
    paths = ",".join(_q(n["file_path"]) for n in nodes)
    metrics = {r["path"]: r for r in store.rows(f"""SELECT path, commit_count, churn,
        last_commit_at, top_author, top_author_share FROM chgraph.file_evolution FINAL
        WHERE project={_q(project)} AND path IN ({paths}) {_BUDGET}""")}
    affected = [{"qualified_name": name, "file_path": by_name[name]["file_path"], "path": path,
                 "evolution": metrics.get(by_name[name]["file_path"], {})}
                for name, path in graph["paths"].items() if name in by_name]
    changed_file = _q(by_name[qualified_name]["file_path"])
    cochange = store.rows(f"""SELECT b.path AS file_path, uniqExact(a.hash) AS support,
        round(support / greatest((SELECT uniqExact(hash) FROM chgraph.git_file_changes
            WHERE project={_q(project)} AND path={changed_file}), 1), 3) AS confidence
        FROM chgraph.git_file_changes AS a INNER JOIN chgraph.git_file_changes AS b
        ON a.hash=b.hash AND a.project=b.project
        WHERE a.project={_q(project)} AND a.path={changed_file} AND b.path!={changed_file}
        GROUP BY b.path HAVING support >= 2 ORDER BY support DESC, file_path LIMIT 10 {_BUDGET}""")
    return {"affected": affected, "cochange": cochange, "generation": project,
            "truncated": graph["truncated"],
            "warnings": [warning] if warning else [],
            "evidence_note": "affected follows indexed CALLS; cochange is historical correlation"}

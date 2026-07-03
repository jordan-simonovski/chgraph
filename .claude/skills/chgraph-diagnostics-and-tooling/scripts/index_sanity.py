#!/usr/bin/env python3
"""index_sanity.py — nodes-per-KLOC sanity check for an indexed repo.

The reference tool's worst documented failure mode is SILENT index
degradation: a 72k-LOC repo indexed to ~500 nodes (~7 nodes/KLOC) while
reporting status "indexed" (DeusData/codebase-memory-mcp issue #333).
This script makes that failure loud: it counts source lines in the repo,
counts graph nodes for the project, and PASS/WARNs on the ratio.

Usage:
    .venv/bin/python scripts/index_sanity.py <repo-path> <data-dir> [--project NAME]

--project defaults to the repo directory's basename.
Exit: 0 = PASS, 1 = WARN, 2 = error.

Thresholds (OPEN — candidates, uncalibrated until chgraph has indexed real
repos; recalibrate via chgraph-validation-and-qa's eval harness and change
them only through chgraph-change-control):
    WARN if ratio < 10 nodes/KLOC   (degradation symptom; issue #333 sat at ~7)
    WARN if ratio > 500 nodes/KLOC  (duplicate versions or over-indexing)
    PASS otherwise

Requires: Python 3.10+ stdlib + chdb. Opens the data dir directly — run
scripts/check_lock.sh first (chdb lock is exclusive).
"""

import argparse
import json
import os
import sys

# OPEN: candidate thresholds, not calibrated (2026-07-03).
WARN_LOW_NODES_PER_KLOC = 10.0
WARN_HIGH_NODES_PER_KLOC = 500.0

SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".rb", ".php", ".swift",
    ".scala", ".m", ".mm", ".sh", ".sql",
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "vendor",
    "dist", "build", "target", "__pycache__", ".tox", ".mypy_cache",
}


def count_loc(repo):
    """Total line count of recognized source files, skipping vendored dirs."""
    total_lines = 0
    total_files = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in SOURCE_EXTENSIONS:
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, "rb") as fh:
                    total_lines += sum(1 for _ in fh)
                total_files += 1
            except OSError:
                continue
    return total_files, total_lines


def count_nodes(data_dir, project):
    try:
        from chdb import session
    except ImportError:
        sys.exit("chdb is not importable. Use the project venv: .venv/bin/python")
    try:
        sess = session.Session(data_dir)
    except Exception as exc:  # noqa: BLE001
        # VERIFIED chdb 26.5.0: lock contention surfaces to Python as
        # "Failed to create connection: ... Error initializing EmbeddedServer".
        if "Error initializing EmbeddedServer" in str(exc) or "Cannot lock file" in str(exc):
            sys.exit(f"LOCKED (probably): could not open {data_dir}. "
                     "Run scripts/check_lock.sh; stop the owner or copy the dir.")
        raise
    p = project.replace("'", "\\'")
    res = sess.query(
        f"SELECT count() AS c FROM chgraph.nodes FINAL WHERE project = '{p}'",
        "JSON")
    if res.has_error():
        sys.exit(f"query failed: {res.error_message()}")
    n = int(json.loads(res.data())["data"][0]["c"])
    sess.close()
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", help="path to the source repository")
    ap.add_argument("data_dir", help="chdb data directory path")
    ap.add_argument("--project", default=None,
                    help="project name in the graph (default: repo basename)")
    args = ap.parse_args()

    if not os.path.isdir(args.repo):
        sys.exit(f"repo path is not a directory: {args.repo}")
    project = args.project or os.path.basename(os.path.abspath(args.repo))

    files, loc = count_loc(args.repo)
    nodes = count_nodes(args.data_dir, project)

    print(f"repo: {os.path.abspath(args.repo)}")
    print(f"project: {project}")
    print(f"source files counted: {files}")
    print(f"source lines counted: {loc} ({loc / 1000:.2f} KLOC)")
    print(f"graph nodes (FINAL): {nodes}")

    if files == 0 or loc == 0:
        print("WARN: no recognized source files in repo — extension list may "
              "not cover this repo's languages, or the path is wrong")
        sys.exit(1)
    if nodes == 0:
        print(f"WARN: 0 nodes for project '{project}' — wrong project name, "
              "or indexing never ran / fully failed")
        sys.exit(1)

    ratio = nodes / (loc / 1000)
    print(f"nodes per KLOC: {ratio:.1f}")
    print(f"thresholds (OPEN, uncalibrated): WARN if < {WARN_LOW_NODES_PER_KLOC} "
          f"or > {WARN_HIGH_NODES_PER_KLOC}")

    if ratio < WARN_LOW_NODES_PER_KLOC:
        print(f"WARN: {ratio:.1f} nodes/KLOC is below {WARN_LOW_NODES_PER_KLOC} — "
              "possible silent index degradation (cf. reference tool issue #333)")
        sys.exit(1)
    if ratio > WARN_HIGH_NODES_PER_KLOC:
        print(f"WARN: {ratio:.1f} nodes/KLOC is above {WARN_HIGH_NODES_PER_KLOC} — "
              "possible duplicate rows (check graph_stats.py pending_dupes) or "
              "over-indexing")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()

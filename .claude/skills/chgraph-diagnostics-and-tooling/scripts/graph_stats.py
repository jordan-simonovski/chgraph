#!/usr/bin/env python3
"""graph_stats.py — structural health snapshot of a chgraph data directory.

Prints, for each project in the graph:
  - node counts by label and edge counts by type (FINAL, i.e. deduplicated)
  - raw-vs-FINAL row divergence (pending ReplacingMergeTree merges)
  - top-N nodes by degree (in + out, over all edge types)
  - orphan-node count (nodes with no edge in either direction)

Usage:
    .venv/bin/python scripts/graph_stats.py <data-dir> [--top N]

Requires: Python 3.10+ stdlib + chdb (pin `chdb==4.2.0`, which installs
chdb-core 26.5.0 — see chgraph-build-and-env for the pinned install).
IMPORTANT: opens the data dir directly — the chdb lock is exclusive.
Run scripts/check_lock.sh first; if a daemon owns the dir, stop it or
run this against a copy of the dir.

Schema assumption (the canonical core DDL is owned by
chgraph-architecture-contract, Decision 5; if the contract diverges, update
this script through chgraph-change-control):
    chgraph.nodes(project, label, name, qualified_name, file_path,
                  start_line, end_line, properties, version)
        ENGINE ReplacingMergeTree(version) ORDER BY (project, qualified_name)
    chgraph.edges(project, source, target, type, properties, version)
        ENGINE ReplacingMergeTree(version) ORDER BY (project, type, source, target)
"""

import argparse
import json
import sys


def open_session(data_dir):
    try:
        from chdb import session
    except ImportError:
        sys.exit("chdb is not importable. Use the project venv: .venv/bin/python")
    try:
        return session.Session(data_dir)
    except Exception as exc:  # noqa: BLE001 — chdb raises generic RuntimeError
        # VERIFIED chdb 26.5.0: when another process holds the dir, the Python
        # exception says "Failed to create connection: Code: 36 ... Error
        # initializing EmbeddedServer"; the underlying "Cannot lock file
        # <dir>/status ... (CANNOT_OPEN_FILE)" line goes to stderr only.
        msg = str(exc)
        if "Error initializing EmbeddedServer" in msg or "Cannot lock file" in msg:
            sys.exit(
                f"LOCKED (probably): could not open {data_dir} — chdb's lock is "
                "exclusive and another process likely owns the dir.\n"
                "Run scripts/check_lock.sh to identify the owner; stop it or "
                "run against a copy of the dir."
            )
        raise


def q(sess, sql):
    """Run sql, return list of dict rows (ClickHouse JSON format)."""
    res = sess.query(sql, "JSON")
    if res.has_error():
        sys.exit(f"query failed: {res.error_message()}\nSQL: {sql}")
    payload = res.data()
    return json.loads(payload)["data"] if payload else []


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data_dir", help="chdb data directory path")
    ap.add_argument("--top", type=int, default=10, help="top-N for degree table")
    args = ap.parse_args()

    sess = open_session(args.data_dir)

    tables = {r["name"] for r in q(
        sess, "SELECT name FROM system.tables WHERE database = 'chgraph'")}
    missing = {"nodes", "edges"} - tables
    if missing:
        sys.exit(f"missing table(s) in database chgraph: {sorted(missing)} — "
                 "is this a chgraph data dir?")

    # Raw vs FINAL: divergence = duplicate-key rows awaiting merge. Harmless
    # unless it grows without bound (see SKILL.md interpretation guide).
    for tbl in ("nodes", "edges"):
        raw = q(sess, f"SELECT count() AS c FROM chgraph.{tbl}")[0]["c"]
        fin = q(sess, f"SELECT count() AS c FROM chgraph.{tbl} FINAL")[0]["c"]
        print(f"{tbl}: raw_rows={raw} final_rows={fin} pending_dupes={int(raw)-int(fin)}")

    projects = [r["project"] for r in q(
        sess, "SELECT DISTINCT project FROM chgraph.nodes FINAL ORDER BY project")]
    if not projects:
        print("no projects found in chgraph.nodes")
        return

    for proj in projects:
        p = proj.replace("'", "\\'")
        print(f"\n=== project: {proj} ===")

        print("nodes by label:")
        for r in q(sess, f"""
            SELECT label, count() AS n FROM chgraph.nodes FINAL
            WHERE project = '{p}' GROUP BY label ORDER BY n DESC"""):
            print(f"  {r['label']:<12} {r['n']}")

        print("edges by type:")
        for r in q(sess, f"""
            SELECT type, count() AS n FROM chgraph.edges FINAL
            WHERE project = '{p}' GROUP BY type ORDER BY n DESC"""):
            print(f"  {r['type']:<12} {r['n']}")

        print(f"top {args.top} nodes by degree (in+out, all edge types):")
        for r in q(sess, f"""
            SELECT qn, sum(d) AS degree FROM (
                SELECT source AS qn, count() AS d
                FROM chgraph.edges FINAL WHERE project = '{p}' GROUP BY qn
                UNION ALL
                SELECT target AS qn, count() AS d
                FROM chgraph.edges FINAL WHERE project = '{p}' GROUP BY qn
            ) GROUP BY qn ORDER BY degree DESC, qn ASC LIMIT {args.top}"""):
            print(f"  {r['degree']:>4}  {r['qn']}")

        orphans = q(sess, f"""
            SELECT count() AS n, groupArray(10)(qualified_name) AS sample
            FROM chgraph.nodes FINAL
            WHERE project = '{p}'
              AND qualified_name NOT IN (
                SELECT source FROM chgraph.edges FINAL
                WHERE project = '{p}')
              AND qualified_name NOT IN (
                SELECT target FROM chgraph.edges FINAL
                WHERE project = '{p}')""")[0]
        print(f"orphan nodes (no edges in or out): {orphans['n']}")
        for qn in orphans["sample"]:
            print(f"  orphan: {qn}")

    sess.close()


if __name__ == "__main__":
    main()

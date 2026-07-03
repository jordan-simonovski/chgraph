#!/usr/bin/env python3
"""query_profile.py — wall-clock timing + row counts for a query against a
chgraph data directory. The tool behind the rule: no performance claim
without a number.

Usage:
    .venv/bin/python scripts/query_profile.py <data-dir> --sql "SELECT ..." [--repeat N]
    .venv/bin/python scripts/query_profile.py <data-dir> --sql-file q.sql [--repeat N]

Reports per run:
    wall_ms   — wall-clock time around sess.query() (what an MCP caller feels)
    engine_ms — chdb's own elapsed() (query execution inside the engine)
    rows_ret  — rows RETURNED to the caller
    rows_read — rows SCANNED by the engine (rows_read())
then min / median / max of wall_ms over all runs. First run is reported
separately as "cold" (includes first-touch costs); stats cover warm runs
when --repeat > 1.

Requires: Python 3.10+ stdlib + chdb. Opens the data dir directly — run
scripts/check_lock.sh first (chdb lock is exclusive).
"""

import argparse
import json
import statistics
import sys
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data_dir", help="chdb data directory path")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sql", help="SQL text to profile")
    src.add_argument("--sql-file", help="file containing SQL text to profile")
    ap.add_argument("--repeat", type=int, default=5, help="number of runs (default 5)")
    args = ap.parse_args()

    sql = args.sql
    if args.sql_file:
        with open(args.sql_file) as fh:
            sql = fh.read()

    try:
        from chdb import session
    except ImportError:
        sys.exit("chdb is not importable. Use the project venv: .venv/bin/python")
    try:
        sess = session.Session(args.data_dir)
    except Exception as exc:  # noqa: BLE001
        # VERIFIED chdb 26.5.0: lock contention surfaces to Python as
        # "Failed to create connection: ... Error initializing EmbeddedServer".
        if "Error initializing EmbeddedServer" in str(exc) or "Cannot lock file" in str(exc):
            sys.exit(f"LOCKED (probably): could not open {args.data_dir}. "
                     "Run scripts/check_lock.sh; stop the owner or copy the dir.")
        raise

    runs = []
    for i in range(max(1, args.repeat)):
        t0 = time.perf_counter()
        res = sess.query(sql, "JSON")
        wall_ms = (time.perf_counter() - t0) * 1000
        if res.has_error():
            sys.exit(f"query failed: {res.error_message()}\nSQL: {sql}")
        payload = res.data()
        rows_ret = json.loads(payload).get("rows", 0) if payload else 0
        engine_ms = res.elapsed() * 1000
        rows_read = res.rows_read()
        runs.append(wall_ms)
        tag = "cold" if i == 0 else "warm"
        print(f"run {i + 1} ({tag}): wall_ms={wall_ms:.2f} engine_ms={engine_ms:.2f} "
              f"rows_ret={rows_ret} rows_read={rows_read}")

    warm = runs[1:] if len(runs) > 1 else runs
    print(f"\nsql: {' '.join(sql.split())[:120]}")
    print(f"runs: {len(runs)} (stats over {len(warm)} warm run(s))")
    print(f"wall_ms min/median/max: {min(warm):.2f} / "
          f"{statistics.median(warm):.2f} / {max(warm):.2f}")
    print("baseline: ~13 ms/query historical session overhead — REPORTED, "
          "chdb-io/chdb#391 (closed); see SKILL.md for what 'slow' means")
    sess.close()


if __name__ == "__main__":
    main()

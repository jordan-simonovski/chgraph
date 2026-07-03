---
name: chgraph-diagnostics-and-tooling
description: Use when a chgraph claim needs a number — query latency, node/edge counts by label/type, degree distribution, orphan nodes, nodes-per-KLOC index sanity, or whether a chdb data dir is locked and by which pid. Trigger on "seems faster", "index looks small", "who holds the lock", "how big is the graph", raw-vs-FINAL row drift, or before writing any performance or quality statement in a PR. Ships runnable measurement scripts under scripts/.
---

# chgraph-diagnostics-and-tooling: measure, don't eyeball

This skill ships four self-contained diagnostic scripts (stdlib + chdb only) and
tells you how to read their output. They exist to enforce one rule:

> **No performance or quality claim in a PR without a number from one of these
> scripts or from the eval harness (see chgraph-validation-and-qa).**
> "Feels faster", "the index looks fine", "traversal seems slow" are not
> reviewable statements. `wall_ms median 3.69 over 4 warm runs` is.

All sample outputs below are REAL, pasted from runs on 2026-07-03 against a
synthetic toy fixture: one project ("toyproj"), 13 nodes (3 File, 9 Function,
1 Class), 19 edges (8 CALLS, 8 DEFINES, 3 IMPORTS), 2 deliberate orphan nodes,
and 1 deliberate duplicate-key row, in a throwaway chdb 26.5.0 data dir.
chgraph itself has no code yet (2026-07-03) — these scripts are the founding
instruments and already run against any data dir that follows the schema below.

## Prerequisites and the one hazard

- Run scripts with the project venv per the env convention (see
  chgraph-build-and-env): `.venv/bin/python scripts/<script>.py ...`.
  System python3 (3.9.6 on macOS) is too old and has no chdb.
- **Hazard (VERIFIED chdb 26.5.0, 2026-07-03):** chdb takes an exclusive lock
  on its data directory — a second process opening the same dir fails hard,
  even read-only. Three of the four scripts open the data dir directly, so
  **run `scripts/check_lock.sh <data-dir>` first**. If a daemon owns the dir,
  stop it (see chgraph-run-and-operate) or run against a `cp -R` copy.

## Script index

| Script | Question it answers | Opens the data dir? |
|---|---|---|
| `scripts/check_lock.sh` | Is this chdb data dir owned by a live process? By whom? | No (filesystem + lsof only) |
| `scripts/graph_stats.py` | How big/healthy is the graph? Counts, degrees, orphans, pending dupes | Yes |
| `scripts/index_sanity.py` | Did indexing plausibly cover the repo? (nodes per KLOC) | Yes |
| `scripts/query_profile.py` | How fast is this query, really? (wall clock + rows) | Yes |

Schema assumption shared by the Python scripts (the canonical core DDL is OWNED
by chgraph-architecture-contract, Decision 5 — this is its shape restated for
quick reference; if the contract diverges, update the scripts through
chgraph-change-control):

```sql
chgraph.nodes(project, label, name, qualified_name, file_path,
              start_line, end_line, properties, version)
    ENGINE ReplacingMergeTree(version) ORDER BY (project, qualified_name)
chgraph.edges(project, source, target, type, properties, version)
    ENGINE ReplacingMergeTree(version) ORDER BY (project, type, source, target)
```

---

## 1. check_lock.sh — who owns this data dir?

```sh
scripts/check_lock.sh <data-dir>
```

Always ends with exactly one `VERDICT:` line. Exit 0 = safe to open,
1 = locked, 2 = not a chdb dir / bad args. Requires `lsof` (present on macOS
and most Linux distros).

Lock-file lifecycle, all VERIFIED on chdb 26.5.0 (2026-07-03):

| Event | `<dir>/status` file | OS lock |
|---|---|---|
| Session open | created; contains `PID: <pid>`, start time, revision | held |
| Clean close | **removed** | released |
| Crash / `kill -9` | **remains (stale)** | released — a new session opens fine over it |

So presence of `status` alone proves nothing; only a live process holding it
open (found via lsof) means LOCKED. The four verdicts, real output:

```
$ scripts/check_lock.sh /path/to/data        # no owner, chdb layout present
VERDICT: UNLOCKED (/path/to/data is a chdb data dir with no owner; safe to open)

$ scripts/check_lock.sh /path/to/data        # a live session holds it
status file: /path/to/data/status
status file contents:
  | PID: 46702
  | Started at: 2026-07-03 15:22:15
  | Revision: 54510
holder: pid=46702 command=Python
VERDICT: LOCKED by pid 46702 (Python) — opening this dir from another process will fail with CANNOT_OPEN_FILE

$ scripts/check_lock.sh /path/to/data        # owner was killed -9
VERDICT: STALE-LOCK (status file left by dead pid 46978; owner crashed or was killed; safe to open — chdb reclaims it)

$ scripts/check_lock.sh /some/random/dir
VERDICT: NOT-A-CHDB-DIR (/some/random/dir has no status file and no chdb layout (metadata/, store/))
```

What a second opener actually sees while LOCKED (VERIFIED 26.5.0): stderr gets
`Code: 76. DB::Exception: Cannot lock file <dir>/status. Another server
instance in same directory is already running. (CANNOT_OPEN_FILE)`, while the
Python exception itself reads `Failed to create connection: Code: 36. ...
Error initializing EmbeddedServer`. The Python scripts below match on the
latter and print a friendly LOCKED message instead of a stack trace.

Interpretation: LOCKED by the chgraph daemon is the NORMAL state for a live
project (single-daemon architecture — see chgraph-architecture-contract).
STALE-LOCK after a crash is expected and harmless for opening, but see
chgraph-run-and-operate for daemon crash recovery.

## 2. graph_stats.py — structural health snapshot

```sh
.venv/bin/python scripts/graph_stats.py <data-dir> [--top N]
```

Real output against the toy fixture:

```
nodes: raw_rows=14 final_rows=13 pending_dupes=1
edges: raw_rows=19 final_rows=19 pending_dupes=0

=== project: toyproj ===
nodes by label:
  Function     9
  File         3
  Class        1
edges by type:
  CALLS        8
  DEFINES      8
  IMPORTS      3
top 5 nodes by degree (in+out, all edge types):
     6  pkg/core.py
     5  pkg.core.process
     4  pkg.core.persist
     4  pkg.util.log
     4  pkg/api.py
orphan nodes (no edges in or out): 2
  orphan: pkg.util.dead_helper
  orphan: pkg.util.old_shim
```

How to read it:

| Line | Good looks like | Bad looks like |
|---|---|---|
| `pending_dupes` (raw minus FINAL rows; FINAL = ClickHouse's read-time deduplication for ReplacingMergeTree) | Small and shrinking after merges | Growing without bound across re-indexes → merges not keeping up; consider `OPTIMIZE TABLE ... FINAL` (see chdb-reference). NOTE (VERIFIED 26.5.0): duplicates inside a single INSERT are collapsed at insert time, so dupes only appear across separate inserts — exactly the re-index case. |
| nodes by label | Function ≫ Class ≫ File, all labels you expect for the languages indexed | A label at 0 that the parser should emit (e.g. 0 Functions in a Python repo) = extraction bug |
| edges by type | CALLS and DEFINES both present; DEFINES roughly tracks definition-node count | 0 CALLS with nonzero Functions = call resolution silently failed (the reference tool's classic precision bug class, e.g. DeusData/codebase-memory-mcp#480) |
| degree top-N | Util/log functions and core files dominating is normal | One node with degree in the thousands on a small repo = duplicate edge explosion |
| orphan nodes (no edge in OR out, not even DEFINES) | ~0 — a healthy index gives every symbol at least a DEFINES edge | A large orphan count = the edge pass dropped rows or ran on a different node set than the node pass |

## 3. index_sanity.py — did indexing plausibly cover the repo?

```sh
.venv/bin/python scripts/index_sanity.py <repo-path> <data-dir> [--project NAME]
```

Counts source lines (recognized extensions, skipping .git/node_modules/.venv/
vendor/etc.), counts FINAL nodes for the project, prints the ratio and a
PASS/WARN verdict. KLOC = thousand lines of code. Exit 0 PASS, 1 WARN.

Why this exists: the reference tool's worst documented failure is SILENT
degradation — a 72k-LOC repo indexed to ~500 nodes (~7 nodes/KLOC) while
reporting "indexed" (REPORTED: github.com/DeusData/codebase-memory-mcp/issues/333).
Status honesty is a chgraph differentiator; this script is the cheap loud check.

Real output (PASS case, toy fixture):

```
repo: /path/to/toyrepo
project: toyproj
source files counted: 3
source lines counted: 212 (0.21 KLOC)
graph nodes (FINAL): 13
nodes per KLOC: 61.3
thresholds (OPEN, uncalibrated): WARN if < 10.0 or > 500.0
PASS
```

Real output (WARN case — wrong/missing project):

```
graph nodes (FINAL): 0
WARN: 0 nodes for project 'nosuch' — wrong project name, or indexing never ran / fully failed
```

Thresholds are **OPEN** (candidates, 2026-07-03, uncalibrated — chgraph has
never indexed a real repo yet):

| Condition | Verdict | Rationale |
|---|---|---|
| ratio < 10 nodes/KLOC | WARN | the issue-#333 degraded index sat at ~7 |
| ratio > 500 nodes/KLOC | WARN | suggests duplicate rows (check `pending_dupes` in graph_stats.py) or over-indexing |
| 0 source files or 0 nodes | WARN | wrong path, wrong project name, or total failure |
| otherwise | PASS | |

Calibrate against real indexed repos via chgraph-validation-and-qa's harness;
change the constants at the top of the script only through
chgraph-change-control (they are a quality gate).

## 4. query_profile.py — how fast is this query, really?

```sh
.venv/bin/python scripts/query_profile.py <data-dir> --sql "SELECT ..." [--repeat N]
.venv/bin/python scripts/query_profile.py <data-dir> --sql-file q.sql [--repeat N]
```

Real output — a 3-hop-capped WITH RECURSIVE CALLS traversal with cycle guard,
on the toy fixture (macOS arm64, chdb 26.5.0, 2026-07-03):

```
run 1 (cold): wall_ms=14.73 engine_ms=14.58 rows_ret=8 rows_read=8
run 2 (warm): wall_ms=4.02 engine_ms=3.87 rows_ret=8 rows_read=8
run 3 (warm): wall_ms=3.48 engine_ms=3.36 rows_ret=8 rows_read=8
run 4 (warm): wall_ms=3.24 engine_ms=3.13 rows_ret=8 rows_read=8
run 5 (warm): wall_ms=3.90 engine_ms=3.76 rows_ret=8 rows_read=8

runs: 5 (stats over 4 warm run(s))
wall_ms min/median/max: 3.24 / 3.69 / 4.02
```

For scale: a warm single-row point lookup on `(project, qualified_name)` (the
ORDER BY key) measured wall_ms 0.85–0.98 (VERIFIED same setup).

Field guide:

| Field | Meaning | What to watch |
|---|---|---|
| `wall_ms` | wall clock around `sess.query()` — what an MCP caller feels | The number to quote in PRs |
| `engine_ms` | chdb's own `elapsed()` — execution inside the engine | wall minus engine = Python/session overhead. VERIFIED 26.5.0: sub-ms on this machine. chdb once regressed to ~13 ms/query session overhead (REPORTED: github.com/chdb-io/chdb/issues/391, closed) — if the gap is tens of ms per query, that class of bug is back; pin it before blaming your SQL. |
| `rows_ret` | rows returned to the caller | what the MCP tool will serialize |
| `rows_read` | rows scanned by the engine | `rows_read ≫ rows_ret` on a "point" query = the primary key isn't being used; check the WHERE clause against the table's ORDER BY |
| cold vs warm | run 1 includes first-touch costs; stats cover warm runs | quote warm medians; mention cold separately if it matters for UX |

What counts as "slow" (candidates, OPEN until calibrated on real-size graphs —
today's numbers are from a 13-node toy): a warm point lookup > 10 ms, or a
warm depth-capped traversal > 100 ms, on a graph under ~1M rows is worth
investigating before shipping. Interactive MCP tools should stay well under a
second wall-clock end to end. For rigorous A/B methodology (baselines,
refutation, how many runs), see chgraph-research-methodology — this script is
its measuring instrument, not a substitute for its discipline.

---

## When NOT to use this

- **Judging retrieval/answer QUALITY or setting acceptance gates** — that is
  the eval harness and golden sets: use **chgraph-validation-and-qa** (it also
  owns calibrating the OPEN thresholds defined here).
- **Diagnosing an error message or misbehavior** (CANNOT_OPEN_FILE, hangs,
  empty search results) — use **chgraph-debugging-playbook**; check_lock.sh is
  one of its inputs, not the playbook itself.
- **Starting/stopping the daemon, crash recovery, where data dirs live** —
  use **chgraph-run-and-operate**.
- **chdb API details** (Session vs connect, formats, OPTIMIZE semantics) —
  use **chdb-reference**.
- **Designing an experiment or benchmark protocol** — use
  **chgraph-research-methodology**.
- **Changing the schema these scripts assume, or their thresholds** — route
  through **chgraph-change-control**; the schema itself is owned by
  **chgraph-architecture-contract**.

## Provenance and maintenance

Grounded 2026-07-03 on macOS arm64, Python 3.12, chdb 26.5.0 (pip). Every
script in scripts/ was executed against a synthetic toy data dir (13 nodes /
19 edges, ReplacingMergeTree per the locked schema decision); every output
block above is pasted from those runs, including the LOCKED / STALE-LOCK /
UNLOCKED / NOT-A-CHDB-DIR verdicts, the friendly LOCKED error path in the
Python scripts, and the lock-file lifecycle table. Reference-tool issue
numbers and the 13 ms overhead figure are REPORTED from public trackers
(github.com/DeusData/codebase-memory-mcp, github.com/chdb-io/chdb).

Re-verify on drift (each is one command):

| What may drift | Re-verification |
|---|---|
| chdb version | `.venv/bin/python -c "import chdb; print(chdb.__version__)"` — if not 26.5.0, re-run everything below |
| status-file lifecycle (created on open / removed on clean close / stale after kill -9) | open a Session on a scratch dir, `ls <dir>/status`; close, `ls` again; repeat with `kill -9` |
| lock exception text the scripts match on ("Error initializing EmbeddedServer") | hold a Session open, attempt a second `Session(<same dir>)`, read the exception string |
| lsof availability (check_lock.sh dependency) | `command -v lsof` |
| status-file format (`PID:` line parsed by STALE-LOCK verdict) | `cat <dir>/status` while a session is open |
| per-query overhead baseline | profile a trivial SELECT with query_profile.py; compare wall−engine gap; history at github.com/chdb-io/chdb/issues/391 |
| schema the scripts assume | diff against chgraph-architecture-contract's tables; on divergence, update scripts via chgraph-change-control |
| OPEN thresholds (nodes/KLOC 10–500; "slow" = >10 ms lookup / >100 ms traversal warm) | calibrate via chgraph-validation-and-qa once real repos are indexed |

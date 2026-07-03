---
name: chdb-reference
description: Use when writing or debugging chdb code in chgraph - install/import, chdb.query() vs Session vs connect(), output formats (CSV/JSON/DataFrame/Arrow), errors like "Cannot lock file .../status", "EmbeddedServer already initialized", "Unknown Index type 'vector_similarity'", WITH RECURSIVE traversal, ReplacingMergeTree/OPTIMIZE FINAL upserts, text index + hasToken, cosineDistance vectors, version confusion (4.2.0 vs 26.5.0), fork hangs, or query overhead.
---

# chdb Reference: the engine knowledge pack for chgraph

chdb is an in-process (embedded) ClickHouse engine you `pip install` and drive from Python — no server to run. chgraph uses it as the storage/query engine for its codebase knowledge graph. Every snippet below was executed on 2026-07-03 against the exact versions in the next section, with the real observed output pasted. There is no chgraph code yet (repo is empty as of 2026-07-03) — this documents the engine you will build on.

## 1. Version ground truth (read this before pinning anything)

**VERIFIED 2026-07-03, macOS arm64.** chdb changed versioning schemes mid-2026 and the numbers are genuinely confusing. Three different version strings are all "the version":

| What | Value | How to see it |
|---|---|---|
| PyPI distribution `chdb` (pure-Python wrapper) | **4.2.0** (newest on PyPI) | `uv pip show chdb` → `Version: 4.2.0`, `Requires: chdb-core, pandas, pyarrow` |
| PyPI distribution `chdb-core` (the engine binary) | **26.5.0** | `uv pip list \| grep chdb` |
| `chdb.__version__` in Python | **'26.5.0'** (tracks chdb-core, NOT the wrapper) | `python -c "import chdb; print(chdb.__version__)"` |
| `chdb.engine_version` (ClickHouse engine inside) | **'26.5.1.1'** | same import |

Consequences, all VERIFIED by running them:

- `uv pip install "chdb==26.5.0"` **FAILS**: `No solution found ... there is no version of chdb==26.5.0`. The correct pin is `chdb==4.2.0`, which resolves `chdb-core==26.5.0`.
- Research/docs citing "chdb v4.2.0" and this skill citing "chdb 26.5.0" describe the **same install**. When reporting a bug upstream, give both numbers.
- REPORTED: the official docs page still describes chdb as powered by ClickHouse 25.8 — docs lag the shipped engine (https://clickhouse.com/docs/chdb). Trust `chdb.engine_version` over docs.

## 2. Install and import

Convention (DECIDED, see chgraph-build-and-env for the full env doctrine): uv-managed Python 3.12 venv at `.venv`. System python3 on this class of Mac is 3.9.6 — old enough to be a trap; chdb needs 3.9+ but the project standardizes on 3.12. chdb Python is **macOS/Linux only, no Windows** (REPORTED: https://github.com/chdb-io/chdb README).

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV="$PWD/.venv" uv pip install "chdb==4.2.0"
.venv/bin/python -c "import chdb; print(chdb.__version__, chdb.engine_version)"
```

VERIFIED output of that last line: `26.5.0 26.5.1.1`.

Footprint (VERIFIED 2026-07-03 with `du -sh` and `/usr/bin/time`):

- `site-packages/chdb` alone: **330 MB**; a venv with chdb + its pandas/pyarrow deps: **~573 MB**. (Compare: DuckDB ~10 MB, SQLite <1 MB — distribution weight is a known cost of this backend.)
- Cold start `import chdb; chdb.query('SELECT 1')`: **real 0.14s** on Apple Silicon. Fast enough that startup is never the bottleneck for a daemon.
- REPORTED: chdb has been run in a 64 MB RAM container (https://antonz.org/trying-chdb/) — memory floor is small; working-set memory scales with query, not with engine.

## 3. The three query APIs

### 3a. Stateless `chdb.query()` — one-shot, no persistent state

VERIFIED signature on 4.2.0: `chdb.query(sql, output_format='CSV', path='', udf_path='', params=None, options=None)`.

```python
import chdb
r = chdb.query("SELECT 1 AS x, 'hello' AS s")
str(r)   # -> '1,"hello"\n'   (default format is CSV)
```

The result object exposes stats (VERIFIED): `r.rows_read()`, `r.bytes_read()`, `r.elapsed()`, plus `bytes`, `data`, `has_error`, `error_message`, `show`, `get_memview`, `storage_rows_read`, `storage_bytes_read`.

Each stateless call opens and closes an engine connection — fine for scripts, wrong for a long-lived daemon (see per-query overhead in section 9).

### 3b. `chdb.session.Session(path)` — stateful, persistent

A Session owns a data directory in real ClickHouse on-disk layout (`metadata/`, `store/`, `data/`). State survives close/reopen. VERIFIED end-to-end:

```python
from chdb import session

s = session.Session("/path/to/datadir")          # created if missing
s.query("CREATE DATABASE IF NOT EXISTS kg")
s.query("CREATE TABLE kg.demo (id UInt32, name String) ENGINE = MergeTree ORDER BY id")
s.query("INSERT INTO kg.demo VALUES (1, 'alpha'), (2, 'beta')")
print(s.query("SELECT * FROM kg.demo ORDER BY id", "JSONEachRow"))
s.close()

s2 = session.Session("/path/to/datadir")          # reopen: data survives
print(s2.query("SELECT count() FROM kg.demo", "CSV"))
s2.close()
```

VERIFIED output: `{"id":1,"name":"alpha"}` / `{"id":2,"name":"beta"}`, then `2`.

`Session(":memory:")` gives a throwaway in-memory session — VERIFIED working with `ENGINE = Memory` and MergeTree tables alike. Use it for tests and scratch work; it holds no data-dir lock on disk you care about.

Session query signature (VERIFIED): `Session.query(sql, fmt='CSV', udf_path='', params=None)`.

### 3c. `chdb.connect()` — DB-API-style cursor

VERIFIED:

```python
import chdb
conn = chdb.connect(":memory:")     # or a path
cur = conn.cursor()
cur.execute("SELECT 1 AS x, 'hi' AS s")
cur.fetchall()        # -> ((1, 'hi'),)
cur.column_names()    # -> ['x', 's']
cur.column_types()    # -> ['UInt8', 'String']
cur.close(); conn.close()
```

Use `connect()` when you want Python-native typed rows instead of a formatted string blob. Same underlying engine, same locking rules as Session.

## 4. Output formats

`output_format` / `fmt` accepts any ClickHouse format name plus two Python-special values. All VERIFIED:

| Format string | Returns | Verified sample |
|---|---|---|
| `"CSV"` (default) | result object; `str()` → CSV text | `'1,"hello"\n'` |
| `"JSONEachRow"` | one JSON object per line | `{"x":1,"s":"hello"}` |
| `"JSON"` | full document with `meta` (name+type per column), `data`, `rows`, `statistics` (elapsed/rows_read/bytes_read) | see below |
| `"Pretty"` | box-drawing table (good for humans/logs) | tables shown throughout this skill |
| `"DataFrame"` | `pandas.DataFrame` (real object, not text) | `type(df)` → `<class 'pandas.DataFrame'>` |
| `"ArrowTable"` | `pyarrow.Table` (real object) | `type(tbl)` → `<class 'pyarrow.lib.Table'>` |
| `"TSVRaw"` | tab-separated, no escaping (good for EXPLAIN output) | used in section 7 |

`"JSON"` verified shape — useful because `meta` gives you column types and `statistics` gives per-query cost for free:

```json
{ "meta": [{"name": "x", "type": "UInt8"}],
  "data": [{"x": 1}],
  "rows": 1,
  "statistics": {"elapsed": 0.000421958, "rows_read": 1, "bytes_read": 1} }
```

For chgraph MCP tool responses, `"JSONEachRow"` or `"JSON"` are the workhorses; `"DataFrame"`/`"ArrowTable"` are for in-process analytics. (Which format each MCP tool emits is owned by mcp-server-reference.)

### Parameterized queries (use these, never f-strings)

`params` performs server-side parameter binding via `{name:Type}` placeholders. VERIFIED including a hostile value:

```python
r = chdb.query(
    "SELECT {name:String} AS who, {n:UInt32} * 2 AS dbl",
    "JSONEachRow",
    params={"name": "o'brien; DROP TABLE x", "n": 21},
)
# VERIFIED output: {"who":"o'brien; DROP TABLE x","dbl":42}
```

The injection attempt comes back as an inert string. chgraph's templated tool queries MUST use this, not string interpolation. (The tool-surface contract itself is owned by mcp-server-reference; changing it goes through chgraph-change-control.)

## 5. The exclusive data-directory lock (the constraint that shaped chgraph)

**VERIFIED 2026-07-03 on chdb 4.2.0/26.5.0** (and previously on the older 4.2.0-scheme build): a second **process** opening a data dir that another live process holds fails immediately. Read-only mode does **not** bypass it. There are two layers of error text and you will see both:

What the second process's Python exception says (vague — this is all you get in `except`):

```
RuntimeError: Failed to create connection: Code: 36. DB::Exception: Error initializing EmbeddedServer: . (BAD_ARGUMENTS)
```

What the engine prints to **stderr** at the same moment (the real cause):

```
Code: 76. DB::Exception: Cannot lock file /path/to/datadir/status. Another server instance in same directory is already running. (CANNOT_OPEN_FILE)
```

VERIFIED details:

- Appending `?mode=ro` to the path fails identically — there is no read-only escape hatch.
- The lock is released on `close()` (or process exit): after the first process closed, the second opened the same dir successfully.
- Debugging rule: if you only see the useless `Code: 36 ... Error initializing EmbeddedServer: .` message, **look at stderr** for the `Code: 76 ... Cannot lock file .../status` line before concluding anything.

This is why chgraph is a single daemon owning each data dir, with MCP stdio shims connecting over a unix socket (DECIDED — rationale and architecture owned by **chgraph-architecture-contract**). Operational recovery from lock errors (stale daemon, crashed process holding `status`) is owned by **chgraph-run-and-operate**.

## 6. One active session per process

**VERIFIED 2026-07-03**: within a single process, creating a second `Session` on a *different* path while one is open fails:

```
RuntimeError: Failed to create connection: Code: 36. DB::Exception: EmbeddedServer already initialized with path '/tmp/.../sessA', cannot connect with different path '/tmp/.../sessB'. (BAD_ARGUMENTS)
```

VERIFIED: after `s1.close()`, opening a Session on a new path in the same process succeeds. So one process = one data dir at a time, period. REPORTED corroboration: one-active-session-per-process is documented behavior in chdb's troubleshooting guide (https://github.com/chdb-io/chdb/blob/main/docs/troubleshooting.rst).

Implication for the chgraph daemon: one daemon process per project data dir; serving two projects means two daemon processes. Thread-safety of a single shared long-lived Session under concurrent requests is **OPEN** (undocumented upstream; per-issue evidence pending) — until proven, serialize all queries through one daemon-side executor.

## 7. Runnable engine patterns for the graph workload

Everything in this section ran end-to-end in a chdb 26.5.0 session on 2026-07-03; outputs shown are pasted from the runs. These are engine capabilities — the actual chgraph schema and any change to it are owned by chgraph-architecture-contract and gated by chgraph-change-control.

### 7a. WITH RECURSIVE traversal — depth cap + visited array are MANDATORY

ClickHouse recursive CTEs (24.4+) use PostgreSQL append-only semantics with **no built-in cycle detection** (REPORTED: open RFC for keyed recursive CTEs, https://github.com/ClickHouse/ClickHouse/issues/107067). On a call graph — which always has cycles — an unguarded recursive CTE runs away. Both guards below are non-negotiable:

```sql
WITH RECURSIVE walk AS (
    SELECT
        e.src AS start,
        e.dst AS node,
        1 AS depth,
        [e.src, e.dst] AS path
    FROM edges e
    WHERE e.src = 'main'
    UNION ALL
    SELECT
        w.start,
        e.dst AS node,
        w.depth + 1 AS depth,
        arrayPushBack(w.path, e.dst) AS path
    FROM walk w
    JOIN edges e ON e.src = w.node
    WHERE w.depth < 10            -- mandatory depth cap
      AND NOT has(w.path, e.dst)  -- visited-array cycle guard
)
SELECT node, depth, arrayStringConcat(path, ' -> ') AS route
FROM walk
ORDER BY depth, node
```

VERIFIED against a 7-edge graph containing a deliberate `parse <-> lex` cycle (`edges(src String, dst String)`, seeded with main→parse, main→render, parse→lex, lex→parse, render→draw, draw→util, parse→util). Observed output — terminates, cycle not revisited, both routes to `util` found:

```
   ┏━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
   ┃ node   ┃ depth ┃ route                          ┃
   ┡━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
1. │ parse  │     1 │ main -> parse                  │
2. │ render │     1 │ main -> render                 │
3. │ draw   │     2 │ main -> render -> draw         │
4. │ lex    │     2 │ main -> parse -> lex           │
5. │ util   │     2 │ main -> parse -> util          │
6. │ util   │     3 │ main -> render -> draw -> util │
   └────────┴───────┴────────────────────────────────┘
```

DECIDED (owned by chgraph-architecture-contract): hot edge types (CALLS) additionally get a precomputed transitive-closure table refreshed at index time, so agent-facing traversals don't pay recursive-CTE cost per query.

### 7b. ReplacingMergeTree + FINAL — the upsert-ish pattern

MergeTree engines are append/merge-oriented; there is no in-place UPDATE worth using at row granularity. The chgraph pattern (DECIDED): `ReplacingMergeTree(version)` keyed on the logical identity, batch per-file replaces, never row-by-row upserts. VERIFIED end-to-end demo — two versions of the same symbol, then dedup:

```sql
CREATE TABLE nodes (
    project        String,
    qualified_name String,
    version        UInt64,          -- e.g. index-run counter or commit timestamp
    file_path      String,
    start_line     UInt32
) ENGINE = ReplacingMergeTree(version)
ORDER BY (project, qualified_name);

INSERT INTO nodes VALUES ('demo', 'pkg.mod.f', 1, 'src/mod.py', 10);
INSERT INTO nodes VALUES ('demo', 'pkg.mod.f', 2, 'src/mod.py', 42);
```

VERIFIED observed behavior, in order:

1. `SELECT * FROM nodes` (plain) → **both rows** (`version` 1 at line 10 and `version` 2 at line 42). Replacement is lazy; until a merge happens, duplicates coexist. Never trust a plain SELECT for current-state reads.
2. `SELECT * FROM nodes FINAL` → **one row**, version 2, line 42. `FINAL` deduplicates at query time (costs merge work per query).
3. `OPTIMIZE TABLE nodes FINAL` then plain `SELECT * FROM nodes` → **one row**, version 2. OPTIMIZE physically merges parts; afterwards plain reads are clean until the next inserts.

Operating rule: reads that must be exact use `FINAL`; the daemon runs periodic `OPTIMIZE TABLE ... FINAL` after index batches so the steady state stays merged. Whether this holds up under frequent small re-index deltas at real repo scale is **OPEN** (unbenchmarked — flagged in Phase-1 research; benchmark belongs to chgraph-validation-and-qa).

### 7c. Text index + hasToken — works, is a filter not a ranker

VERIFIED end-to-end on 26.5.0. Flag note (VERIFIED 2026-07-03): on engine 26.5.1.1 the `text` index **no longer requires** `allow_experimental_full_text_index=1` — CREATE succeeds with and without it (the setting is still accepted, so passing it is harmless). Older chdb builds (4.x scheme, engine 25.x) did require it; if a CREATE fails with an experimental-index error you are on an old build. This resolves the Phase-1 research contradiction ("FTS GA on server vs experimental in chdb") — the 26.5 engine ships the GA generation.

```sql
CREATE TABLE docs (
    qualified_name String,
    body String,
    INDEX idx_body body TYPE text(tokenizer = 'splitByNonAlpha') GRANULARITY 1
) ENGINE = MergeTree ORDER BY qualified_name;

INSERT INTO docs VALUES
  ('pkg.auth.login',  'def login(user, password): validate credentials and issue session token'),
  ('pkg.auth.logout', 'def logout(session): revoke the session token'),
  ('pkg.db.connect',  'def connect(dsn): open a database connection pool');

SELECT qualified_name FROM docs WHERE hasToken(body, 'token') ORDER BY qualified_name;
```

VERIFIED output — the two token-bearing rows, and `EXPLAIN indexes = 1` proving the skip index actually pruned:

```
1. │ pkg.auth.login  │
2. │ pkg.auth.logout │
```

```
      Skip
        Name: idx_body
        Description: text GRANULARITY 100000000
        Condition: (mode: All; tokens: ["token"])
```

There is **no native BM25/TF-IDF scoring** in ClickHouse (REPORTED: ClickHouse positions the text index as an acceleration engine, not a relevance engine — https://clickhouse.com/blog/clickhouse-full-text-search). DECIDED: chgraph uses the text index as a candidate filter and layers custom SQL hybrid scoring on top; the scoring design is owned by chgraph-architecture-contract / chgraph-git-evolution-campaign, and changes to retrieval behavior go through chgraph-change-control.

### 7d. Vector search: brute-force cosineDistance only — HNSW is compiled out

VERIFIED on 26.5.0: creating a `vector_similarity` (HNSW approximate-nearest-neighbor) index fails even with `allow_experimental_vector_similarity_index = 1`:

```
Code: 80. DB::Exception: Unknown Index type 'vector_similarity'. Available index types: hypothesis, text, bloom_filter, sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax: When validating secondary index `idx`. (INCORRECT_QUERY)
```

The index is GA in ClickHouse *server* but not compiled into the chdb binary. The working pattern is brute force over `Array(Float32)` — VERIFIED end-to-end:

```sql
CREATE TABLE embeddings (
    qualified_name String,
    vec Array(Float32)
) ENGINE = MergeTree ORDER BY qualified_name;

INSERT INTO embeddings VALUES
  ('pkg.auth.login',  [0.9, 0.1, 0.0]),
  ('pkg.auth.logout', [0.8, 0.2, 0.1]),
  ('pkg.db.connect',  [0.0, 0.1, 0.9]);

WITH [0.85, 0.15, 0.05]::Array(Float32) AS query_vec
SELECT
    qualified_name,
    round(cosineDistance(vec, query_vec), 4) AS dist
FROM embeddings
ORDER BY dist ASC
LIMIT 2;
```

VERIFIED output (lower distance = more similar):

```
1. │ pkg.auth.login  │ 0.0037 │
2. │ pkg.auth.logout │ 0.0044 │
```

DECIDED: brute force is the plan of record — adequate at codebase scale (10⁴–10⁵ vectors). Whether upstream will compile `vector_similarity` into chdb is **OPEN** (worth filing/watching a chdb-io issue before any HNSW-dependent design).

## 8. Fork/subprocess safety

REPORTED (https://github.com/chdb-io/chdb/issues/355): querying chdb inside a **forked child** after importing chdb in the parent has hung historically (fork-safety). Note the distinction, which I verified in passing: plain `subprocess.run([...python..., "-c", ...])` (fork+exec of a fresh interpreter) from a chdb-holding parent worked fine in the lock experiments above. The danger zone is `multiprocessing` with the fork start method and any fork-without-exec. Rules for chgraph: the daemon never queries chdb from a forked worker; if worker processes are ever needed, use the `spawn` start method. Re-check #355 status before relying on anything subtler.

## 9. Sharp edges quick table

| Edge | Status | Detail / source |
|---|---|---|
| Exclusive data-dir lock, even read-only | VERIFIED (sec. 5) | forces the daemon architecture — chgraph-architecture-contract |
| One session per process; second path fails until `close()` | VERIFIED (sec. 6) | plus https://github.com/chdb-io/chdb/blob/main/docs/troubleshooting.rst |
| Real lock error (`Code: 76`) only on stderr; Python gets vague `Code: 36` | VERIFIED (sec. 5) | always capture stderr in the daemon |
| Version-scheme change: PyPI `chdb 4.2.0` wraps `chdb-core 26.5.0`; `__version__` says 26.5.0; `chdb==26.5.0` pin fails | VERIFIED (sec. 1) | pin `chdb==4.2.0` |
| Fork-safety hang in forked children | REPORTED | https://github.com/chdb-io/chdb/issues/355 |
| Long-running session degradation (historical, fixed) | REPORTED | https://github.com/chdb-io/chdb/issues/363 |
| ~13ms per-query overhead (historical issue; motivated keeping one hot session, not per-call `chdb.query`) | REPORTED | https://github.com/chdb-io/chdb/issues/391 |
| Pre-2.0.2 sessions lost state / "Directory for table data already exists"; session mode reimplemented | REPORTED | https://github.com/chdb-io/chdb/issues/197, PR #283 |
| No cycle detection in recursive CTEs | VERIFIED guards work (sec. 7a) | RFC https://github.com/ClickHouse/ClickHouse/issues/107067 |
| HNSW `vector_similarity` compiled out | VERIFIED (sec. 7d) | brute-force cosineDistance only |
| No native BM25 scoring | REPORTED (sec. 7c) | text index is filter-only |
| 330 MB package / ~573 MB venv | VERIFIED (sec. 2) | distribution weight vs single-binary competitors |
| Docs cite ClickHouse 25.8; binary is 26.5.1.1 | VERIFIED locally vs REPORTED docs | trust `chdb.engine_version` |

## When NOT to use this

- **Starting/stopping the daemon, socket/pidfile locations, recovering from a live "Cannot lock file .../status" in operation** → **chgraph-run-and-operate**. This skill explains what the lock *is*; that one tells you what to *do* at 2am.
- **Why chgraph is a daemon + shim, schema shape, closure tables, hybrid-ranking design** → **chgraph-architecture-contract**. Engine capabilities live here; design decisions live there.
- **MCP tool definitions, stdio transport, tool names/compatibility with codebase-memory-mcp** → **mcp-server-reference**.
- **Setting up the repo venv, Python version policy, lockfiles** → **chgraph-build-and-env** (this skill only shows the minimal install to get chdb importable).
- **Benchmarking ReplacingMergeTree under real re-index load, retrieval quality evals** → **chgraph-validation-and-qa**.
- **A live bug you can't attribute yet** → **chgraph-debugging-playbook** first; come back here for engine semantics.
- **Changing chgraph's schema, retrieval behavior, or tool surface** based on anything here → route through **chgraph-change-control**; this skill never authorizes a change by itself.

## Provenance and maintenance

Grounded 2026-07-03: every SQL statement, Python snippet, and shell command above was executed in a Python 3.12.13 venv with PyPI `chdb 4.2.0` / `chdb-core 26.5.0` (`chdb.__version__` = 26.5.0, `chdb.engine_version` = 26.5.1.1) on macOS arm64 (Darwin 25.5.0); outputs are pasted verbatim from those runs. REPORTED items come from the Phase-1 research corpus with public URLs inline. OPEN items: session thread-safety, ReplacingMergeTree performance under frequent small deltas, upstream HNSW-in-chdb, current status of #355/#363/#391.

Re-verify on any chdb upgrade (one-liners, run from a venv with chdb installed):

```bash
# version triple (wrapper vs core vs engine) — note: uv venvs ship no pip binary, use `uv pip`
python -c "import chdb; print(chdb.__version__, chdb.engine_version)"
VIRTUAL_ENV="$PWD/.venv" uv pip show chdb | grep -E 'Version|Requires'
# exclusive lock + ro bypass (expect both child opens to FAIL while parent holds the dir)
python - <<'EOF'
import subprocess, sys, shutil
from chdb import session
d = "/tmp/chdb-lockcheck"; shutil.rmtree(d, ignore_errors=True)
s = session.Session(d); s.query("SELECT 1")
for p in (d, d + "?mode=ro"):
    r = subprocess.run([sys.executable, "-c", f"from chdb import session; session.Session({p!r})"], capture_output=True, text=True)
    print(p, "->", "STILL LOCKED" if r.returncode else "LOCK GONE (skill stale!)", "|", r.stderr.strip()[:120])
s.close()
EOF
# HNSW still compiled out? (expect "Unknown Index type"; if it succeeds, update section 7d and notify chgraph-architecture-contract via chgraph-change-control)
python -c "import chdb; chdb.query(\"CREATE TABLE v (id UInt32, vec Array(Float32), INDEX i vec TYPE vector_similarity('hnsw','cosineDistance',3)) ENGINE=MergeTree ORDER BY id SETTINGS allow_experimental_vector_similarity_index=1\")" 2>&1 | tail -1
# text index without experimental flag (expect silence = success on >=26.5; an experimental-index error means an old build)
python -c "import chdb; chdb.query('CREATE TABLE t (s String, INDEX i s TYPE text(tokenizer=\'splitByNonAlpha\')) ENGINE=MergeTree ORDER BY s')" 2>&1 | tail -1
# issue status drift
# open in browser: github.com/chdb-io/chdb/issues/355, /363, /391 and ClickHouse/ClickHouse#107067
```

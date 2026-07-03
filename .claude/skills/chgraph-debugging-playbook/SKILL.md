---
name: chgraph-debugging-playbook
description: "Use when chgraph or chdb misbehaves: errors like \"Code: 76 Cannot lock file .../status\", \"CANNOT_OPEN_FILE\", \"Error initializing EmbeddedServer\", \"EmbeddedServer already initialized\", \"TOO_DEEP_RECURSION\", \"Unknown Index type 'vector_similarity'\", \"SUPPORT_IS_DISABLED\"; a graph query that hangs or explodes; text search returning nothing for symbols that exist; an index reporting success with an absurdly small graph; or a long-running daemon degrading until restart."
---

# chgraph debugging playbook

Symptom → triage for chgraph's known failure modes. Every row in the triage table is either **VERIFIED** (reproduced locally on chdb 26.5.0 / engine 26.5.1.1, macOS arm64, 2026-07-03 — observed output pasted in the detail sections below) or explicitly labeled **REPORTED** / inherited. Trust the exact error strings; they are pasted from real runs, not paraphrased.

Context you need once: **chdb** is an in-process ClickHouse engine (a Python library, not a server); a **Session** is a chdb handle bound to one on-disk data directory; the **daemon** is the single chgraph process that owns a data directory, and the **shim** is the thin MCP-stdio process that connects to the daemon over a unix socket (architecture is DECIDED — see chgraph-architecture-contract). The repo has no code yet (2026-07-03), so rows about chgraph-level behavior are marked DECIDED/OPEN; the chdb-level rows are real today and reproducible with any chdb 26.5.0 venv (`.venv/bin/python` — see chgraph-build-and-env; the system python3 3.9.6 trap lives there too).

## Triage table

| # | Symptom (exact text where possible) | First check (one command) | Root cause | Fix | The trap (what people waste time on instead) |
|---|---|---|---|---|---|
| 1 | `Code: 76. DB::Exception: Cannot lock file <dir>/status. Another server instance in same directory is already running. (CANNOT_OPEN_FILE)` — Python raises `RuntimeError: Failed to create connection: Code: 36 ... Error initializing EmbeddedServer` | `lsof <dir>/status` | Another live process holds the chdb data dir. The lock is exclusive and read-only mode does NOT bypass it (VERIFIED). | Don't open the dir yourself — connect to the owning daemon via the shim (procedure: chgraph-run-and-operate). If the holder is a stray script, stop it. | Retrying with `?mode=ro`, sleeping-and-retrying, or copying the whole data dir. RO fails identically (VERIFIED below). |
| 2 | Same Code: 76 message expected after a crash — but `lsof <dir>/status` shows **no process** | `lsof <dir>/status` (empty output, exit 1 → no holder) | Stale `status` file after unclean death. VERIFIED: the file survives `kill -9` but does NOT block reopening — the lock is a kernel file lock released on process death, not the file's existence. | Nothing to remove. Just reopen (restart the daemon — chgraph-run-and-operate). If `lsof` shows a live-but-wedged holder, kill that PID first. | Deleting the `status` file or the whole data dir "to clear the lock". Unnecessary (VERIFIED) and deleting the dir destroys the graph. |
| 3 | Graph query hangs, memory climbs, or: `Code: 306. DB::Exception: Maximum recursive CTE evaluation depth (N) exceeded ... (TOO_DEEP_RECURSION)` | Grep the SQL for `NOT has(` — is there a visited-path guard in the recursive step? | `WITH RECURSIVE` on a cyclic graph without a cycle guard. ClickHouse has no built-in cycle detection; the depth setting (default 1000, VERIFIED) only bounds the crash, and rows grow exponentially with branching before it fires (3069 vs 3 rows in the repro below). | Add both guards to the recursive step: `WHERE NOT has(w.path, e.dst) AND w.depth < <cap>`. Never raise `max_recursive_cte_evaluation_depth` as a "fix". | Raising the depth setting (the error message itself suggests it — it makes the explosion bigger), or blaming chdb performance. |
| 4 | `Code: 80. DB::Exception: Unknown Index type 'vector_similarity'. Available index types: hypothesis, text, bloom_filter, sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax ... (INCORRECT_QUERY)` | Re-run the CREATE — the error lists available types; `vector_similarity` absent = compiled out | HNSW is compiled out of chdb 26.5.0 (VERIFIED, even with `allow_experimental_vector_similarity_index=1`). Expected, not a bug in your SQL. | Use brute-force `cosineDistance` + `ORDER BY ... LIMIT` (patterns: chdb-reference). Adequate at codebase scale (10⁴–10⁵ vectors). | Fiddling with experimental-flag combinations or upgrading/downgrading chdb hunting for HNSW. It isn't in the build. |
| 5a | `CREATE TABLE` with a text index fails: `Code: 344. DB::Exception: The text index feature is disabled. Enable the setting 'enable_full_text_index' to use it. (SUPPORT_IS_DISABLED)` | `SELECT name, value FROM system.settings WHERE name IN ('enable_full_text_index','allow_experimental_full_text_index')` | Text-index flags disabled in THIS session. As of 2026-07-03 both default to **1** in chdb 26.5.0 (VERIFIED — a drift from earlier builds that required the flag), so seeing this means something set them to 0, or you're on an older chdb. | `SET enable_full_text_index=1` (session) or add `SETTINGS allow_experimental_full_text_index=1` to the CREATE. Pin chdb 26.5.0. | Rewriting the index DDL / tokenizer arguments when the DDL was fine. |
| 5b | Text search returns nothing for a symbol you can see in the source | `SELECT ... WHERE hasToken(body, '<exact-identifier>')` — full identifier hits, sub-word misses | Tokenizer mismatch, not the flag: `splitByNonAlpha`/`hasToken` treat `parseCallGraph` as ONE token, so searching `graph` misses camelCase symbols (VERIFIED: matched `parse_call_graph` only). Note `hasToken` still returns correct results with the index disabled — the index is acceleration, not semantics (VERIFIED). | Search full identifiers, or tokenize identifiers at index time (camelCase/snake_case splitting is chgraph's ingest job — DECIDED, see chgraph-architecture-contract). | Blaming the experimental flag or "broken index" and rebuilding it. The flag changes CREATE-time behavior and speed, never result emptiness. |
| 6 | `EmbeddedServer already initialized with path '<dirA>', cannot connect with different path '<dirB>'. (BAD_ARGUMENTS)` (Code: 36) | Search the process's code path for a second `Session(...)`/`connect(...)` | One active chdb session per process (VERIFIED). A second Session to a *different* dir in the same process fails with exactly this. | One process = one data dir. Serving multiple projects means multiple daemons (chgraph-run-and-operate). | Hunting for a "close the old session properly" bug when the design itself assumes two concurrent dirs in one process. |
| 7 | **Inherited trap** (reference tool issue #333): index reports success/"indexed" but node count is absurdly low vs repo size (~500 nodes for a 72k-LOC repo) | Sanity ratio: `SELECT count() FROM nodes WHERE project = '<p>'` vs repo KLOC — nodes-per-KLOC far below the gate threshold = degraded index | Silent index degradation. REPORTED against codebase-memory-mcp (https://github.com/DeusData/codebase-memory-mcp/issues/333); this is WHY chgraph gates `index_status` on index sanity and surfaces "degraded" explicitly (DECIDED). Thresholds and the gate live in chgraph-validation-and-qa. | Treat "indexed" + tiny graph as FAILED, re-index, and check parser/language coverage for the repo's main language. | Debugging retrieval quality ("search is bad") for hours when the graph under it is 1% populated. Check the ratio first, always. |
| 8 | Long-running daemon slowly degrades: queries slow down or session state misbehaves over hours/days | Restart the daemon; if the symptom vanishes → this row | REPORTED: chdb has a closed issue for long-running session degradation (https://github.com/chdb-io/chdb/issues/363, fixed in a past release) — NOT reproduced on 26.5.0 (OPEN whether any variant persists). | Restart is the discriminator and the mitigation (chgraph-run-and-operate). If reproducible on 26.5.0, capture it (escalation below) and consider scheduled daemon recycling (OPEN). | Tuning queries or schema to chase a leak that lives in session lifetime. Always run the restart discriminator before optimizing anything. |

## Detail and reproductions

All repros below were executed 2026-07-03 on chdb 26.5.0 in throwaway scratch directories; substitute your own paths. Output blocks are pasted verbatim from real runs.

### Row 1 — lock contention (VERIFIED)

With process A holding `Session("<dir>")`, process B doing the same gets, on stderr then as the Python exception:

```
Code: 76. DB::Exception: Cannot lock file /tmp/.../data1/status. Another server instance in same directory is already running. (CANNOT_OPEN_FILE)
RuntimeError: Failed to create connection: Code: 36. DB::Exception: Error initializing EmbeddedServer: . (BAD_ARGUMENTS)
```

Gotcha: the Python-visible exception is the generic **Code: 36** wrapper; the diagnostic **Code: 76** line goes to stderr. If you only log exceptions, you'll miss the real message — capture stderr.

Read-only does not help — `Session("<dir>?mode=ro")` fails with the identical pair of messages (VERIFIED, re-run 2026-07-03).

First check — find the holder:

```bash
lsof <dir>/status
# COMMAND   PID             USER   FD   TYPE DEVICE SIZE/OFF     NODE NAME
# Python  41291 jordanclickhouse    5w   REG   1,15       59 21826683 /private/tmp/.../data1/status
```

If the PID is the chgraph daemon: you're on the wrong path — connect through the shim (chgraph-run-and-operate). If it's a stray notebook/script: stop it, then start the daemon.

### Row 2 — "stale lock" after a crash (VERIFIED, and the observation contradicts folklore)

Reproduced: started a session holder, confirmed it held `<dir>/status`, then `kill -9 <pid>`. Observed:

- The `status` file **still exists** after `kill -9` (`ls -la` showed it, 59 bytes).
- `lsof <dir>/status` prints nothing and exits 1 — no holder.
- A fresh `Session("<dir>")` **opened successfully with the stale file in place** — no cleanup of any kind — and previously inserted data was intact (`SELECT count()` returned the expected rows).
- After a **clean** `session.close()`, the `status` file is removed by chdb itself.

Conclusion: the lock is the kernel-level file lock, not the file's existence. The safe-recovery protocol is therefore: `lsof <dir>/status` → no holder → just restart the daemon. Do not delete `status`; do not delete the data dir. (Caveat: verified on macOS arm64 / local filesystem. Network filesystems have different lock semantics — OPEN, and chgraph does not support data dirs on NFS anyway per chgraph-architecture-contract.)

### Row 3 — runaway recursive CTE (VERIFIED)

Discriminating check: does the recursive step contain a visited-path guard? Bounded repro on a 3-node cycle (a→b→c→a), run via `chdb.query(...)`:

Guarded — terminates correctly:

```sql
WITH RECURSIVE walk AS (
    SELECT 'a' AS node, ['a'] AS path, 1 AS depth
    UNION ALL
    SELECT e.dst, arrayPushBack(w.path, e.dst), w.depth + 1
    FROM walk w JOIN edges e ON e.src = w.node
    WHERE NOT has(w.path, e.dst) AND w.depth < 10
)
SELECT node, path, depth FROM walk ORDER BY depth
```

```
   ┌─node─┬─path──────────┬─depth─┐
1. │ a    │ ['a']         │     1 │
2. │ b    │ ['a','b']     │     2 │
3. │ c    │ ['a','b','c'] │     3 │
   └──────┴───────────────┴───────┘
```

Unguarded on the same cycle — runs until the engine kills it:

```
Code: 306. DB::Exception: Maximum recursive CTE evaluation depth (50) exceeded, during evaluation of  walk. Consider raising max_recursive_cte_evaluation_depth setting: While executing RecursiveCTESource. (TOO_DEEP_RECURSION)
```

(Depth 50 was set for the repro; the default is 1000 — VERIFIED via `system.settings`.) Do NOT follow the error message's suggestion to raise the setting: on a branching cyclic graph (a→{b,c}, b→a, c→a) a depth-cap-only walk to depth 20 emitted **3069 rows** where the guarded version emitted **3** (VERIFIED). Growth is exponential in depth; raising the cap converts an error into an OOM.

chgraph rule (DECIDED, chgraph-architecture-contract): every recursive traversal ships with BOTH `NOT has(path, next)` and a depth cap; hot edge types use the precomputed closure table instead.

### Row 4 — vector_similarity (VERIFIED, expected)

```
Code: 80. DB::Exception: Unknown Index type 'vector_similarity'. Available index types: hypothesis, text, bloom_filter, sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax: When validating secondary index `iv`. (INCORRECT_QUERY)
```

Reproduced with `allow_experimental_vector_similarity_index=1` set — the flag does not help; HNSW is compiled out of the chdb 26.5.0 binary. The brute-force fallback works (VERIFIED):

```sql
SELECT id, cosineDistance(v, [1.0, 0.0]) AS d FROM emb ORDER BY d LIMIT 2
-- returned: 1,0 / 3,0.293
```

Query patterns, costs, and when brute force stops being adequate: chdb-reference (it owns the embedding-search facts).

### Rows 5a/5b — text index (VERIFIED, with a version drift to know about)

As of 2026-07-03 on chdb 26.5.0, `enable_full_text_index` and `allow_experimental_full_text_index` both **default to 1** (VERIFIED via `system.settings`; a text-index CREATE succeeded with no flags set). Phase-1 notes saying the flag is required date from the chdb 4.2.0-era build — if you hit flag errors, first suspect you're not on the pinned version.

With the feature explicitly disabled, CREATE fails clearly (not silently):

```
Code: 344. DB::Exception: The text index feature is disabled. Enable the setting 'enable_full_text_index' to use it. (SUPPORT_IS_DISABLED)
```

The "query returns nothing" symptom is almost never the flag. VERIFIED discriminating experiment — two rows, `text(tokenizer='splitByNonAlpha')` index:

| body | `hasToken(body,'graph')` | `hasToken(body,'parseCallGraph')` |
|---|---|---|
| `def parseCallGraph(repo):` | **miss** | hit |
| `def parse_call_graph(repo):` | hit | — |

`splitByNonAlpha` keeps `parseCallGraph` as one token, so sub-word queries miss camelCase identifiers. Also VERIFIED: `hasToken` returns correct results even with `enable_full_text_index=0` at query time — the index only accelerates; it cannot change which rows match. So: empty results = token/query mismatch; slow results = index not being used. Fixing this properly (identifier-aware tokenization at ingest) is a schema/retrieval change — route through chgraph-change-control.

### Row 6 — one session per process (VERIFIED)

Second `Session` to a different dir in the same process:

```
RuntimeError: Failed to create connection: Code: 36. DB::Exception: EmbeddedServer already initialized with path '<dirA>', cannot connect with different path '<dirB>'. (BAD_ARGUMENTS)
```

This is documented chdb behavior (one active session per process). It is why one daemon serves one data dir — multi-project setups run multiple daemons (chgraph-run-and-operate).

### Row 7 — the silent-degradation inherited trap (REPORTED)

codebase-memory-mcp #333: a 72k-LOC Rust repo indexed to ~500 nodes with status "indexed" (https://github.com/DeusData/codebase-memory-mcp/issues/333); the maintainer's reactive fix was a `CBM_DUMP_VERIFY_MIN_RATIO` knob. chgraph's stance (DECIDED): `index_status` must return "degraded" when the nodes-per-KLOC sanity ratio fails — status honesty over optimistic status. The ratio thresholds, how they were derived, and the gate tests are owned by chgraph-validation-and-qa; this playbook's job is only: **tiny graph + "success" status = degraded index until proven otherwise.**

### Row 8 — long-running degradation (REPORTED)

chdb #363 (https://github.com/chdb-io/chdb/issues/363) reported long-running session degradation, closed as fixed pre-26.5.0. Not reproduced locally (OPEN — no long-run soak test has been performed on 26.5.0). The restart discriminator costs one minute and cleanly splits "session-lifetime bug" from "query/schema problem": if a daemon restart fixes it, do not touch queries or schema — capture the repro and escalate.

## Escalation: a failure mode not in this table

1. Capture verbatim: full error text (including stderr — see Row 1 gotcha), chdb version (`python -c "import chdb; print(chdb.__version__, chdb.engine_version)"`), OS/arch, and the minimal SQL/Python that triggers it.
2. Run the two cheap discriminators first: `lsof <dir>/status` (lock/process class) and a daemon restart (state-lifetime class).
3. File it via the entry protocol in **chgraph-failure-archaeology** — that skill owns the archive format and the decision of whether a new triage row gets added here.
4. If the fix you're considering changes schema, retrieval behavior, or the MCP tool surface, it goes through **chgraph-change-control** — no playbook fix routes around its gates.

## When NOT to use this

- Starting/stopping/registering the daemon and shim, socket/pidfile locations, or step-by-step crash recovery → **chgraph-run-and-operate** (this playbook only identifies which failure you have).
- chdb API usage, SQL patterns, index/embedding-search reference facts → **chdb-reference**.
- Building or interpreting the index-sanity gates, eval harness, golden sets → **chgraph-validation-and-qa**.
- Writing custom diagnostics/inspection tooling → **chgraph-diagnostics-and-tooling**.
- Environment/install problems (wrong python, venv, chdb won't import) → **chgraph-build-and-env**.
- Recording a post-mortem of a failure you already diagnosed → **chgraph-failure-archaeology**.
- Design rationale for the daemon/lock architecture itself → **chgraph-architecture-contract**.

## Provenance and maintenance

Grounded 2026-07-03 by executing every VERIFIED claim above against chdb 26.5.0 (engine 26.5.1.1), pip-installed in a Python 3.12 venv on macOS arm64, using throwaway data directories. REPORTED rows cite public issue URLs (DeusData/codebase-memory-mcp#333, chdb-io/chdb#363) from the Phase-1 research corpus. The chgraph repo contained no code at writing time; daemon/shim behavior rows are DECIDED design, not tested software.

Re-verification one-liners (run on any chdb upgrade; `PY=.venv/bin/python`):

| What may drift | Command | Expect (as of 26.5.0) |
|---|---|---|
| chdb version pin | `$PY -c "import chdb; print(chdb.__version__, chdb.engine_version)"` | `26.5.0 26.5.1.1` |
| Exclusive lock + RO bypass | hold a `Session(dir)` in one process; in another: `$PY -c "import chdb.session as s; s.Session('<dir>?mode=ro')"` | Code: 76 CANNOT_OPEN_FILE, RO included |
| Stale-lock recovery | `kill -9` the holder; `lsof <dir>/status`; reopen | file remains, no holder, reopen succeeds without cleanup |
| HNSW availability | CREATE TABLE with `INDEX iv v TYPE vector_similarity('hnsw','cosineDistance',8)` | Code: 80 Unknown Index type (update Row 4 if it ever succeeds) |
| Text-index flag defaults | `$PY -c "import chdb; print(chdb.query(\"SELECT name,value FROM system.settings WHERE name LIKE '%full_text%'\",'CSV'))"` | both flags = 1 |
| Recursion depth default | `SELECT value FROM system.settings WHERE name='max_recursive_cte_evaluation_depth'` | `1000` |
| One-session-per-process | open two `Session`s with different dirs in one process | Code: 36 "EmbeddedServer already initialized" |
| Reference-tool trap status | https://github.com/DeusData/codebase-memory-mcp/issues/333 | check if resolved; keep Row 7 labeled inherited |

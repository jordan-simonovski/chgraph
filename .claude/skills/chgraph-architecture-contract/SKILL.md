---
name: chgraph-architecture-contract
description: Use when making, questioning, or reviewing any chgraph design choice — backend or runtime selection, daemon vs per-session processes, "Cannot lock file .../status" errors, schema shape, ReplacingMergeTree vs upserts, recursive traversal safety, requests for HNSW/vector index, BM25, or Windows support, or any "why is chgraph built this way" question. Holds the locked design decisions, the system invariants, and the honest weak-points register that every other skill assumes.
---

# chgraph Architecture Contract

This is the constitution of chgraph: a chdb-backed MCP server for codebase knowledge graphs, positioned as an alternative to DeusData/codebase-memory-mcp (called "the reference tool" below). As of 2026-07-03 the repo contains **no code** — this contract was written before the first commit, and nothing here implies otherwise.

Definitions used throughout:

- **chdb** — an in-process (embedded, no server) ClickHouse engine, consumed as a Python package. Pinned via the pip wrapper dist **`chdb` 4.2.0**, which pulls **`chdb-core` 26.5.0** (engine-tracking; `chdb.__version__` reports this core version) wrapping ClickHouse engine 26.5.1.1 (VERIFIED locally 2026-07-03). The three-layer version story (wrapper / core / engine) is owned by **chdb-reference** §1 and FA-003 in **chgraph-failure-archaeology**; "chdb 26.5.0" in this contract always means the core/`__version__`.
- **MCP** — Model Context Protocol, the stdio/JSON-RPC protocol agents use to call tools.
- **The reference tool** — [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp), a pure-C, single-static-binary, SQLite-backed code graph MCP server (v0.8.1, 2026-06-12). chgraph keeps a compatible core tool surface with it. Details live in the **code-graph-reference** sibling skill.

## Evidence labels

Every claim in this contract that could be doubted carries one of these labels. Overselling is the cardinal sin here; a wrong runbook is worse than none.

| Label | Meaning |
|---|---|
| **VERIFIED** | Executed locally by the skill authors and the output observed (dates given) |
| **REPORTED** | From research, with a public source URL |
| **DECIDED** | A design decision — rationale given, could have gone another way |
| **OPEN** | Unproven candidate or unbenchmarked assumption; do not build on it as fact |

## The forcing fact: the exclusive data-directory lock

One VERIFIED fact shapes the whole system, so it comes first. A chdb data directory can be opened by **exactly one process**. A second process — even read-only (`?mode=ro`) — fails at open. Re-verified on chdb 26.5.0, macOS arm64, 2026-07-03, with two processes opening the same session directory; the second one got:

```
Failed to create connection: Code: 36. DB::Exception: Error initializing EmbeddedServer: . (BAD_ARGUMENTS)
Code: 76. DB::Exception: Cannot lock file <dir>/status. Another server instance in same directory
is already running. (CANNOT_OPEN_FILE)
```

(The read-only bypass failure was verified in Phase-1 research on chdb 4.2.0; the lock itself was re-verified on 26.5.0.)

Additionally, one process cannot even open **two different** data directories (VERIFIED 2026-07-03 on 26.5.0): the second `Session` in the same process fails with:

```
Failed to create connection: Code: 36. DB::Exception: EmbeddedServer already initialized with path
'<dir1>', cannot connect with different path '<dir2>'. (BAD_ARGUMENTS)
```

Consequences: multiple Claude Code sessions each spawning their own MCP server against one project graph will hard-fail, and one daemon process serves at most one data directory. This is why chgraph is a daemon, not a library (Decision 2), and why serving multiple projects from one data dir vs one daemon-per-project is a design axis owned by **chgraph-run-and-operate**.

## The locked design decisions

All ten decisions below were user-confirmed and locked on 2026-07-03. They are defaults, not dogma — but changing any of them goes through **chgraph-change-control** (see "How to challenge a decision"). ("Phase-1" throughout this library means the pre-code research pass of early July 2026; its primary sources are the public URLs cited inline — the internal research reports are not part of the repo and are never citable evidence on their own. Definition owned by **chgraph-change-control** §6.)

| # | Decision | Options considered | Locked choice | Rationale | Evidence |
|---|---|---|---|---|---|
| 1 | Runtime | Python / TypeScript / Go | **Python 3.12** | Only first-class chdb binding (Session, streaming, Arrow); chdb-node v2 is thinner with unverified session parity. Cost: no Windows — chdb Python is macOS/Linux only. Acceptable: the reference tool's Windows story is also its weakest platform ([tracking issue #394](https://github.com/DeusData/codebase-memory-mcp/issues/394)). | REPORTED: binding maturity from [chdb-io/chdb](https://github.com/chdb-io/chdb) (repo is ~97% Python). DECIDED: dropping Windows. |
| 2 | Process architecture | daemon+socket / lock-wait-retry / per-instance dir copies | **Single daemon process owns each chdb data dir; MCP stdio shims connect to it over a local unix socket** | Forced by the VERIFIED exclusive lock above — naive per-session servers hard-fail on open, and read-only mode does not bypass it. The daemon also serializes writes, which answers the multi-agent write-contention question (concurrent unprotected writes to shared agent memory are a documented corruption source — [arXiv:2603.10062](https://arxiv.org/html/2603.10062)). | VERIFIED (lock, 26.5.0, 2026-07-03). DECIDED (daemon over the two alternatives). |
| 3 | MCP tool surface | raw SQL / own Cypher subset / reference-compatible fixed tools | **Compatible core with the reference tool's proven names/semantics (search_graph, trace_path, query_graph, index_status, …) plus chgraph-unique analytical extensions; raw SQL only behind an opt-in flag** | Agents already know this surface (the reference tool installs a skill teaching it); raw SQL as a default tool carries injection/correctness risk. The full 14-tool list was re-extracted from the live source on 2026-07-03: index_repository, search_graph, query_graph, trace_path, get_code_snippet, get_graph_schema, get_architecture, search_code, list_projects, delete_project, index_status, detect_changes, manage_adr, ingest_traces. | VERIFIED (tool list fetched from [src/mcp/mcp.c](https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c), 2026-07-03). DECIDED (compat-first). The per-tool compatibility contract (which tools are tier-1/tier-2/not-planned, extension namespace) is owned by **mcp-server-reference**; the reference tool's exact semantics by **code-graph-reference**. |
| 4 | Flagship differentiation | language breadth / indexing speed / distribution / evolution analytics | **Git-evolution graph: ingest full git history into ClickHouse tables joined onto the symbol graph (churn, co-change coupling, ownership, recency) + hybrid ranking** | Attacks the documented "stale code ranks as well as live code" failure mode ([redis.io KG-RAG blog](https://redis.io/blog/knowledge-graph-rag-structured-retrieval-ai-agents/)); `clickhouse git-import` ingests full history at scale (Linux ~12.5 min — [ClickHouse github dataset docs](https://clickhouse.com/docs/getting-started/example-datasets/github)); no Neo4j/Kuzu-backed competitor attempts it. Anti-angles (do NOT compete): zero-dep distribution, indexing speed, 158-language breadth, native Cypher. | REPORTED (both sources above). DECIDED. Whether hybrid ranking closes the retrieval-quality gap is OPEN (see weak points). Campaign detail owned by **chgraph-git-evolution-campaign**. |
| 5 | Schema | nodes+edges+JSON (reference-compatible) / wide typed tables | **Reference-compatible nodes/edges core + typed columnar side tables (complexity metrics, git history, embeddings as `Array(Float32)`); `ReplacingMergeTree(version)` with `ORDER BY (project, qualified_name)` — `version` is the replacing column, NOT part of the sort key (canonical DDL below); batch per-file replace, never row-by-row upserts; periodic `OPTIMIZE`** | Keeps drop-in familiarity for agents; typed side tables unlock the analytical differentiation. MergeTree is append/merge-oriented — frequent single-row upserts fit poorly (REPORTED: [chdb docs/ecosystem](https://github.com/chdb-io/chdb)). Batch-replace semantics VERIFIED 2026-07-03: after inserting version 2 of a row, `SELECT ... FINAL` returns only the latest version pre-merge, and `OPTIMIZE TABLE ... FINAL` collapses the parts (see Invariant INV-5). Keeping `version` OUT of the sort key is load-bearing: with `version` inside the ORDER BY key, every version is a distinct sort-key value and `FINAL` does NOT deduplicate — VERIFIED 2026-07-03 (both rows survive; counter-example test kept in **chgraph-validation-and-qa** §7). | DECIDED. VERIFIED (ReplacingMergeTree FINAL/OPTIMIZE behavior on 26.5.0). Performance at scale is OPEN (unbenchmarked). |
| 6 | Graph traversal | recursive CTE / precomputed closure / agent-driven hops | **`WITH RECURSIVE` with mandatory depth cap + visited-path array; precomputed transitive-closure table for hot edge types (CALLS) refreshed at index time** | `WITH RECURSIVE` with `arrayPushBack` path tracking and `has(path, x)` cycle guards VERIFIED working on a cyclic graph (26.5.0, 2026-07-03 — see INV-2 for the pattern). ClickHouse recursive CTEs have PostgreSQL append-only semantics with **no built-in cycle detection** (REPORTED: [ClickHouse#107067](https://github.com/ClickHouse/ClickHouse/issues/107067)) — an unguarded query on a cyclic call graph runs away. Closure tables are cheap in columnar storage and dodge the reference tool's row-cap UX problems. | VERIFIED (guarded traversal works; no built-in guard). DECIDED (closure table for CALLS). |
| 7 | Indexing mode | block the MCP call / background job | **Async with `index_status` polling and explicit "degraded" status surfacing** | The reference tool's silent-degradation bugs (status "indexed" with ~500 nodes on a 72k-LOC repo — [#333](https://github.com/DeusData/codebase-memory-mcp/issues/333); never-finishing indexes [#524](https://github.com/DeusData/codebase-memory-mcp/issues/524), [#563](https://github.com/DeusData/codebase-memory-mcp/issues/563); a v0.8.1 data-loss bug [#557](https://github.com/DeusData/codebase-memory-mcp/issues/557)) show status honesty is a differentiator. MCP spec 2025-11-25 supports long-running operations. | REPORTED (issue numbers). DECIDED. Enforced as INV-3. |
| 8 | Embeddings & text relevance | chdb HNSW / brute force / external engine; text index alone / +custom scoring | **Brute-force `cosineDistance` for vectors; `text` index (default-enabled on 26.5, previously experimental) as candidate filter + custom SQL hybrid scoring for text** | The HNSW `vector_similarity` index is **compiled out of chdb**: VERIFIED 2026-07-03 on 26.5.0 — `Unknown Index type 'vector_similarity'. Available index types: hypothesis, text, bloom_filter, sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax`. Brute force is adequate at codebase scale (10⁴–10⁵ vectors). The `text` index works — default-enabled on 26.5 (the experimental flag defaults to 1 and a flagless CREATE succeeds; passing the flag is harmless — VERIFIED 26.5.0, fact home **chdb-reference** §7c). ClickHouse has **no native BM25/TF-IDF** — "an acceleration engine, not a relevance engine" (REPORTED: [ClickHouse FTS GA blog](https://clickhouse.com/blog/full-text-search-ga-release)), so chgraph must compensate with hybrid signals (recency, centrality, identifier-aware tokenization). | VERIFIED (both index facts, 26.5.0). DECIDED (hybrid scoring). Scoring quality is OPEN. |
| 9 | Parsing frontend | build own resolver / reuse tree-sitter | **tree-sitter via py-tree-sitter, ~10 top languages first; no "Hybrid LSP" clone in v1** | 158-language breadth is not the differentiation axis, and the reference tool's precision issues (false-positive PHP CALLS edges [#606](https://github.com/DeusData/codebase-memory-mcp/issues/606), unresolved TS path aliases [#730](https://github.com/DeusData/codebase-memory-mcp/issues/730)) show breadth ≠ quality. | REPORTED (issue numbers). DECIDED. |
| 10 | Environment | system python / conda / uv | **uv-managed Python 3.12 venv at `.venv` in the repo** | Known trap: system python3 on the dev machine is 3.9.6 — too old for the toolchain (VERIFIED as environment fact 2026-07-03). chdb requires Python 3.9+ but the project standardizes on 3.12. Setup mechanics owned by **chgraph-build-and-env**. | DECIDED. |

### Canonical core DDL (Decision 5, normative — this is the one home for the nodes/edges table shape)

Sibling skills (**chgraph-diagnostics-and-tooling**'s scripts, **chgraph-git-evolution-campaign** Phase 1) quote campaign cuts of exactly this shape; if a script and this block disagree, this block wins and the script is fixed through **chgraph-change-control**. VERIFIED 2026-07-03 (chdb 26.5.0): both CREATEs execute, and `FINAL` deduplicates duplicate-key inserts down to the highest `version`.

```sql
CREATE TABLE chgraph.nodes (
    project String,
    label LowCardinality(String),          -- Function, Class, File, ...
    name String,
    qualified_name String,                 -- unique symbol id within project
    file_path String,
    start_line UInt32,
    end_line UInt32,
    properties String,                     -- JSON blob, reference-compatible
    version UInt64                         -- index-generation counter
) ENGINE = ReplacingMergeTree(version)
ORDER BY (project, qualified_name);

CREATE TABLE chgraph.edges (
    project String,
    source String,                         -- qualified_name of source node
    target String,
    type LowCardinality(String),           -- CALLS, IMPORTS, DEFINES, ...
    properties String,
    version UInt64
) ENGINE = ReplacingMergeTree(version)
ORDER BY (project, type, source, target);
```

## Invariants — these MUST hold

Violating any of these is a bug regardless of what any other document says. Each names its evidence and, where relevant, the sibling skill that operationalizes it.

**INV-1: Exactly one process owns a chdb data directory, and all access goes through the daemon.**
No tool, script, debug session, or test may open a live data dir directly — it will either fail on the lock (VERIFIED, see forcing fact) or, worse, steal the lock from a crashed daemon and confuse recovery. Debug access patterns are owned by **chgraph-debugging-playbook**; daemon lifecycle by **chgraph-run-and-operate**.

**INV-2: Every recursive traversal carries a depth cap AND a visited-path cycle guard.**
ClickHouse `WITH RECURSIVE` has no built-in cycle detection (REPORTED: [ClickHouse#107067](https://github.com/ClickHouse/ClickHouse/issues/107067)); call graphs are cyclic. The canonical guarded pattern, VERIFIED on chdb 26.5.0 (2026-07-03) against a deliberately cyclic edge set `a→b→c→a, c→d`:

```sql
WITH RECURSIVE walk AS (
    SELECT 'a' AS node, ['a'] AS path, 0 AS depth
    UNION ALL
    SELECT e.dst, arrayPushBack(w.path, e.dst), w.depth + 1
    FROM walk AS w
    JOIN g.edges AS e ON e.src = w.node
    WHERE w.depth < 10           -- mandatory depth cap
      AND NOT has(w.path, e.dst) -- mandatory cycle guard
)
SELECT node, path, depth FROM walk ORDER BY depth, node
```

Observed output (terminates cleanly despite the cycle):

```
a	['a']	0
b	['a','b']	1
c	['a','b','c']	2
d	['a','b','c','d']	3
```

**INV-3: `index_status` reflects reality — no silent degradation, ever.**
If the index is partial, stale, failed, or mid-rebuild, the status says so explicitly ("degraded", with counts). This is a direct response to the reference tool's silent-degradation record (Decision 7 evidence) and is a product differentiator, not a nicety. Status semantics and QA checks owned by **chgraph-validation-and-qa**.

**INV-4: Every retrieval-behavior change passes the eval gate before it ships.**
Retrieval behavior = anything that changes what results a query/search/traversal returns or how they are ranked. The gate itself is defined in **chgraph-change-control**; the eval harness in **chgraph-validation-and-qa**. Rationale: the reference tool's own eval showed graph-first retrieval *losing* to plain file exploration on answer quality (83% vs 92% — REPORTED: [arXiv:2603.27277](https://arxiv.org/abs/2603.27277), self-reported by that project); chgraph's whole bet is closing that gap, which is impossible to know without measuring every change.

**INV-5: Batch writes only; queries over ReplacingMergeTree must be dedup-correct.**
Never row-by-row upserts (Decision 5). And because ReplacingMergeTree deduplicates only at merge time, duplicate versions of a row **coexist on disk until OPTIMIZE/merge** — VERIFIED 2026-07-03 on 26.5.0: after a batch re-insert with a bumped version, a plain `SELECT` returned both `('p','mod.f',1,...)` and `('p','mod.f',2,...)`; `SELECT ... FINAL` and post-`OPTIMIZE TABLE ... FINAL` returned only version 2. Therefore every read path must use `FINAL`, `argMax`-style version-aware aggregation, or run against post-OPTIMIZE state. A query that forgets this silently returns ghost rows of old code — exactly the staleness failure chgraph exists to kill.

**INV-6: Schema changes ship with a migration plus a schema-version bump, through chgraph-change-control.**
No exceptions for "additive" changes; the version bump is what lets the daemon refuse to open a data dir it doesn't understand instead of corrupting or misreading it (the reference tool's corruption-triggered data-loss bug [#557](https://github.com/DeusData/codebase-memory-mcp/issues/557) is the cautionary tale — REPORTED).

**INV-7: The chdb version is pinned; lock, session, and index-availability findings are re-verified on every upgrade.**
The engine version churns (docs lagged the shipped engine during Phase-1 research; the versioning scheme itself changed from 4.x to engine-tracked 26.x). Re-verification one-liners are in "Provenance and maintenance" below. Upgrade procedure owned by **chgraph-change-control**.

## Weak-points register — stated plainly

These are real. Do not paper over them in docs, marketing, or tool descriptions.

| Weak point | Status / evidence | Consequence & stance |
|---|---|---|
| No native BM25/TF-IDF relevance scoring in ClickHouse | REPORTED: [FTS GA blog](https://clickhouse.com/blog/full-text-search-ga-release). The `text` index accelerates matching, it does not rank. | The reference tool has SQLite FTS5 BM25. chgraph's custom hybrid SQL scoring must compensate — whether it matches BM25 quality is **OPEN**. |
| No HNSW vector index — brute-force `cosineDistance` only | VERIFIED 2026-07-03 (chdb 26.5.0): `Unknown Index type 'vector_similarity'`. | Fine at 10⁴–10⁵ vectors; a real gap vs the reference tool's bundled semantic search on multi-million-node graphs. Revisit if chdb ships the index (re-check one-liner below). |
| ~330MB install pulling pandas+pyarrow, vs the reference tool's single static binary | VERIFIED 2026-07-03: `du -sh` on the chdb package = 330M; startup import + `SELECT 1` = ~0.14s (fast start does not fix the download). | Distribution story is materially worse. Accepted cost of Decision 1; do not compete on distribution (Decision 4 anti-angles). |
| Incremental update performance (ReplacingMergeTree batch-replace + periodic OPTIMIZE at real repo scale) | **OPEN** — semantics verified (INV-5), performance never benchmarked. | Do not claim incremental-indexing superiority anywhere until **chgraph-validation-and-qa** has numbers. |
| One chdb Session per process, one data dir per process | VERIFIED 2026-07-03 (26.5.0): second Session in the same process fails with `EmbeddedServer already initialized with path '<dir1>', cannot connect with different path '<dir2>'`. Thread-safety of one shared long-lived Session under concurrent MCP requests is **OPEN** (undocumented). | The daemon must serialize or carefully gate query execution; multi-project = multiple daemons or one shared data dir (design owned by **chgraph-run-and-operate**). |
| Daemon is a single point of failure | DECIDED consequence of Decision 2. | Crash recovery, stale-socket, and stale-lock handling must exist; owned by **chgraph-run-and-operate** / **chgraph-debugging-playbook**. |
| The compatibility target is a single-maintainer project | REPORTED: 955 of ~1,060 commits by one maintainer ([GitHub API](https://api.github.com/repos/DeusData/codebase-memory-mcp)); very rapid star growth of contested provenance (star counts from different public sources disagreed during Phase-1 research — the discrepancy is OPEN, no audited number exists; re-check the GitHub API before quoting any figure). | The reference tool's surface may drift or die; treat compat as "familiar names and semantics", not slavish tracking. Its self-reported benchmarks are not a trusted baseline. |
| Retrieval-quality gap of the whole product category | REPORTED: graph-first retrieval scored 83% vs 92% for plain file exploration ([arXiv:2603.27277](https://arxiv.org/abs/2603.27277), self-reported). | chgraph's hybrid-ranking bet that git signals close this gap is **OPEN** and is the point of the eval gate (INV-4). |
| No Windows | DECIDED (Decision 1); chdb Python is macOS/Linux only (REPORTED: [chdb-io/chdb](https://github.com/chdb-io/chdb)). | State it up front in README/docs; do not accept Windows issues as bugs. |
| Backend choice itself was contestable | DECIDED over named contenders: Kuzu (embedded graph DB, native Cypher — arguably the best semantic fit) and SQLite (the boring-reliable choice) were the serious alternatives at founding. That ranking is a founding-team assessment with no public benchmark behind it (OPEN). | chdb was DECIDED on ClickHouse SQL/analytics reach (git-evolution flagship) and ecosystem alignment. If the flagship fails its evals, this decision is the one to re-litigate first — via **chgraph-change-control**. |

## How to challenge a decision

Any of the ten decisions or seven invariants can be challenged — none of them is sacred, all of them are load-bearing. The path is:

1. Open a change proposal per **chgraph-change-control** (it owns the gates, templates, and who decides).
2. Bring evidence at the same or better label than what the decision rests on: a VERIFIED fact is only overturned by a new VERIFIED fact (e.g., "chdb now ships `vector_similarity`" must be a locally observed `CREATE TABLE` success, not a changelog line).
3. Changes to schema, retrieval behavior, or the tool surface additionally hit the specific gates in **chgraph-change-control** (migration+version bump per INV-6; eval gate per INV-4). Nothing routes around that skill.

## When NOT to use this

| If you need… | Use instead |
|---|---|
| chdb API mechanics, SQL syntax, session usage, sharp-edge details | **chdb-reference** |
| The reference tool's exact tool schemas, node labels, edge types, issue history | **code-graph-reference** |
| MCP protocol details (stdio framing, tool schemas, long-running ops) | **mcp-server-reference** |
| Setting up the venv, installing chdb, build tooling | **chgraph-build-and-env** |
| Starting/stopping/monitoring the daemon, socket and lock operations | **chgraph-run-and-operate** |
| Diagnosing a live failure (lock errors, hangs, bad results) | **chgraph-debugging-playbook** |
| Proposing or executing a change to schema/retrieval/tools | **chgraph-change-control** |
| Git-evolution ingestion and hybrid-ranking implementation detail | **chgraph-git-evolution-campaign** |
| Benchmarks, eval harness, QA procedures | **chgraph-validation-and-qa** |

This skill answers "what is decided, why, what must hold, and what is honestly weak" — nothing operational.

## Provenance and maintenance

Grounding: written 2026-07-03 against an empty repo, from (a) Phase-1 research on codebase-memory-mcp, chdb, and the SOTA landscape (public sources linked inline), and (b) local experiments on chdb 26.5.0 / engine 26.5.1.1, Python 3.12, macOS arm64, executed by the author on 2026-07-03: two-process lock test, two-sessions-one-process test, cyclic-graph `WITH RECURSIVE` with guards, `vector_similarity` failure + available-index-type list, `cosineDistance` brute force, experimental `text` index create/insert/query, ReplacingMergeTree batch-replace + `FINAL` + `OPTIMIZE`, package `du`, and startup timing. All observed outputs shown above are pasted, not paraphrased.

Re-verify on any chdb upgrade or when a decision is challenged (run with the project venv's python — see **chgraph-build-and-env**; all executed successfully by the author on 2026-07-03):

```bash
# 1. Pinned version still what this contract states (expect: 26.5.0 26.5.1.1)
python -c "import chdb; print(chdb.__version__, chdb.engine_version)"

# 2. Exclusive data-dir lock still holds (expect: LOCK STILL EXCLUSIVE)
D=$(mktemp -d); python -c "
import subprocess, sys
h = subprocess.Popen([sys.executable,'-c','import chdb.session,time; s=chdb.session.Session(\"$D\"); s.query(\"SELECT 1\"); print(1,flush=True); time.sleep(10)'], stdout=subprocess.PIPE); h.stdout.readline()
r = subprocess.run([sys.executable,'-c','import chdb.session; chdb.session.Session(\"$D\").query(\"SELECT 1\")'], capture_output=True, text=True)
print('LOCK STILL EXCLUSIVE' if 'Cannot lock file' in r.stderr else 'LOCK BEHAVIOR CHANGED — re-verify architecture'); h.terminate()"

# 3. HNSW still compiled out (expect: still compiled out; anything else -> revisit Decision 8)
python -c "
import chdb
try:
    chdb.query(\"CREATE TABLE t (id UInt64, e Array(Float32), INDEX v e TYPE vector_similarity('hnsw','cosineDistance',8)) ENGINE=MergeTree ORDER BY id\")
    print('vector_similarity NOW AVAILABLE — revisit embeddings decision')
except Exception as ex:
    print('still compiled out' if 'Unknown Index type' in str(ex) else f'new error: {ex}')"

# 4. Reference tool surface still the 14 names Decision 3 lists
curl -s https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c | grep -oE '^\s*\{"[a-z_]+", "[A-Z]' | grep -oE '"[a-z_]+"' | tr -d '"' | sort -u

# 5. Install footprint claim (expect ~330M; adjust register if it moves)
du -sh "$(python -c 'import chdb, os; print(os.path.dirname(chdb.__file__))')"
```

If any re-verification result diverges from what this contract states, do not silently edit this file — open a change proposal via **chgraph-change-control** so downstream skills that inherited the fact get updated too.

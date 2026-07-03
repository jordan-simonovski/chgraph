---
name: chgraph-research-frontier
description: Use when deciding what chgraph should research or attempt next, when drafting any external claim (README, blog post, benchmark, "beats X", "first to Y", competitor comparison), when someone asks how chgraph compares to codebase-memory-mcp, GitNexus, CodeGraphContext, Cursor, or Aider repo-map, when assessing whether an idea is novel or known art, or when defining what evidence a milestone or public number requires before publication.
---

# chgraph Research Frontier: Open Problems and Claim Discipline

This skill is the honest map of where chgraph could advance the state of the art (SOTA — the best published results and tools in the field), and the rules for what chgraph may say about itself externally. As of 2026-07-03 the repo has **zero code**: every milestone below is unhit, every external claim is forbidden until its evidence exists.

**Evidence labels used throughout** (every doubtable fact carries one):

| Label | Meaning |
|---|---|
| **VERIFIED** | Executed locally (2026-07-03, chdb 26.5.0 / engine 26.5.1.1, macOS arm64, Python 3.12); observed output shown |
| **REPORTED** | From research with a public source URL |
| **DECIDED** | Design decision with rationale; not yet proven in code |
| **OPEN** | Unproven candidate; treat as a hypothesis |

## When NOT to use this

| You actually want | Use instead |
|---|---|
| Execute the git-evolution flagship campaign (schemas, ingest pipeline, runbook) | **chgraph-git-evolution-campaign** |
| Change schema, tool surface, or retrieval behavior | **chgraph-change-control** (nothing routes around its gates, including "first steps" below) |
| chdb API mechanics, lock behavior details, SQL recipes | **chdb-reference** |
| How to run evals / QA acceptance criteria mechanics | **chgraph-validation-and-qa** |
| How to conduct research itself (search protocol, source grading) | **chgraph-research-methodology** |
| Reference-tool tool semantics and schema compatibility details | **code-graph-reference** |
| Set up the environment to reproduce the snippets here | **chgraph-build-and-env** |

## The frontier at a glance

| # | Problem | Why SOTA fails | chgraph's asset | Status |
|---|---|---|---|---|
| 1 | Retrieval-quality gap (83→92 at low tokens) | Graph-first retrieval trades accuracy for cost | Hybrid multi-signal SQL ranking | OPEN — north star |
| 2 | Evolution-aware ranking | Stale code ranks as well as live code | Full git history joined onto symbol graph in one engine | OPEN — defensible novelty candidate |
| 3 | Incremental-index freshness at scale | Silent degradation, hours-stale graphs | ReplacingMergeTree batch replace | OPEN — unbenchmarked |
| 4 | Agent-written memory fusion | Static graphs and agent memory live in separate stores | One queryable store, provenance columns | OPEN |
| 5 | Multi-repo / org-scale graphs | Row-limit ceilings; embeddings broke at >100k repos | Columnar aggregation | OPEN |
| 6 | vector_similarity in chdb builds | HNSW compiled out of chdb | User works at ClickHouse; exact repro in hand | OPEN — ecosystem play |

---

## Problem 1 — Closing the retrieval-quality gap (the north star)

**Why SOTA fails.** The reference tool's own paper reports **83% answer quality vs 92% for a plain file-exploring agent**, at 10x fewer tokens and 2.1x fewer tool calls across 31 repos (**REPORTED**, https://arxiv.org/abs/2603.27277). Graph-first retrieval currently buys token savings by paying accuracy. Compounding it: agents under-use the graph tools even when installed (**REPORTED**, open adoption issue https://github.com/DeusData/codebase-memory-mcp/issues/509). Nobody has published a graph-first system that matches file-exploration quality at graph-level token cost.

**Caution:** the 83/92 numbers are self-reported by the competitor and its rapid star growth is itself an open credibility question (**REPORTED**, report cross-check of https://api.github.com/repos/DeusData/codebase-memory-mcp vs secondary sources). Never cite them as an established baseline without independent reproduction.

**chgraph's asset.** Every ranking signal — text match, vector cosine, git recency, churn, graph degree, complexity — lives in one SQL engine, so hybrid scoring is a single statement, tunable per query, not a hardcoded pipeline (**DECIDED**, schema decision 5; hybrid-scoring shape **VERIFIED** in Problem 2 below). Whether hybrid ranking actually closes the gap is **OPEN** — this is the falsifiable bet the whole project rides on.

**First three steps in this repo:**
1. Create `evals/` with a pinned corpus manifest (public repo URLs + exact commit SHAs) and a question set with held-out answer keys — before any retrieval code exists. Eval design mechanics: see **chgraph-validation-and-qa**.
2. Independently reproduce the file-exploration baseline on our corpus (our own "92"), so the gap we chase is our measurement, not the competitor's.
3. Score the first trivial retrieval prototype (name match + text index) on the same harness to establish the floor. Any retrieval-behavior iteration after that goes through **chgraph-change-control**.

**You have a result when:** a re-runnable harness in this repo shows chgraph answer quality ≥ our measured file-exploration baseline at ≤ 1/5 of its tokens, on the pinned corpus, reproducible by a third party from repo artifacts alone. Anything less is not a result. **OPEN.**

---

## Problem 2 — Evolution-aware ranking (the flagship novelty candidate)

**Why SOTA fails.** Documented failure mode: embeddings and graph indexes don't rank recency, so "deprecated code retrieves as well as current code, and agents patch the wrong target" (**REPORTED**, https://redis.io/blog/knowledge-graph-rag-structured-retrieval-ai-agents/). Prior-art check (be harsh, this is the novelty claim):

| Tool | What it does with history | Source (**REPORTED**) |
|---|---|---|
| codebase-memory-mcp | Thin `githistory` pass producing FILE_CHANGES_WITH co-change edges only; no churn/ownership/recency analytics or ranking | https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md |
| CodeGraphContext | tree-sitter structure into FalkorDB/Kuzu/Neo4j; no git-evolution dimension | https://github.com/CodeGraphContext/CodeGraphContext |
| GitNexus | Kuzu + tree-sitter structural graph over MCP; despite the name, no published evolution analytics | https://arxiv.org/html/2504.10046 (survey context) |
| ClickHouse itself | `clickhouse git-import` ingests full commit/file/line history at scale (Linux ~12.5 min, Chromium ~67 min) — but as a demo dataset, not joined to a symbol graph | https://clickhouse.com/docs/getting-started/example-datasets/github |

No surveyed tool joins full git history onto a symbol graph in one queryable engine. That is the defensible gap — but "we did not find it" ≠ "it does not exist": the novelty claim stays **OPEN** until a dated prior-art scan is committed to this repo (see POSITIONING).

**chgraph's asset — VERIFIED prototype shape.** Note first: chdb ships only `_chdb.abi3.so`, no `clickhouse` CLI, so `clickhouse git-import` is NOT available through chdb (**VERIFIED**: `ls`/`find` over the installed package found no binary). chgraph must extract history itself; `git log --numstat` parsing is sufficient and was **VERIFIED** end-to-end on a scratch repo (one stale file touched once in 2024, one live file touched 3x in June 2026), loaded into chdb, ranked in one SQL statement combining assumed-equal text relevance with a 90-day exponential recency decay and commit-count signal:

```
   ┌─qualified_name─────────────┬─commits_touching─┬─churn─┬─recency_signal─┬─hybrid_score─┐
1. │ config_v2.parse_config_v2  │                3 │     5 │         0.9826 │       0.9448 │
2. │ legacy_parser.parse_config │                1 │     1 │              0 │         0.55 │
   └────────────────────────────┴──────────────────┴───────┴────────────────┴──────────────┘
```

The live symbol beats the stale one purely on evolution signals — the exact inversion of the documented failure mode. This is a toy; the weights, decay constant, and scale behavior are all **OPEN**.

**First three steps in this repo** (execution runbook lives in **chgraph-git-evolution-campaign**; these are the entry points):
1. Commit the history extractor (`git log --numstat` → commits/file_changes tables) as the first ingest module, with the schema proposed through **chgraph-change-control**.
2. Build a staleness pair set: repos with known deprecated-vs-live symbol pairs (deprecation markers, CHANGELOG evidence) as ground truth.
3. Measure the inversion rate (stale ranked above live) with and without evolution signals on the Problem-1 harness.

**You have a result when:** on the staleness pair set, evolution-aware ranking eliminates a stated, pre-registered fraction of stale-over-live inversions with no regression on overall Problem-1 quality — both numbers from the pinned harness. **OPEN.**

---

## Problem 3 — Incremental-index freshness at scale

**Why SOTA fails.** The reference tool's weakest documented area: silent index degradation (72k LOC repo → ~500 nodes with status "indexed", https://github.com/DeusData/codebase-memory-mcp/issues/333), never-finishing indexes (#524, #563), and a v0.8.1 data-loss bug deleting project DBs (#557) (all **REPORTED**). Batch indexers industry-wide leave graphs hours stale without surfacing errors (**REPORTED**, redis.io post above).

**chgraph's asset.** Batch per-file replace into ReplacingMergeTree — a ClickHouse table engine that deduplicates rows sharing a sort key, keeping the highest `version` — keyed on `(project, qualified_name)` with a version column per index generation (**DECIDED**, decision 5), plus explicit "degraded" status surfacing (**DECIDED**, decision 7). Mechanics **VERIFIED** on chdb 26.5.0: without `FINAL` (the query modifier that forces merge-time dedup at read) both versions of a re-indexed symbol are visible; with `FINAL` the latest wins; after `OPTIMIZE TABLE ... FINAL` dedup is physical. Observed output also exposed a real gap:

```
-- after OPTIMIZE FINAL:
"a.bar",20,1     <- a.bar was deleted from the file in generation 2 but its v1 row survives
"a.foo",15,2
```

**VERIFIED finding: per-file batch replace does not delete removed symbols.** Deletion needs tombstone rows or a `(file_path, max_version)` filter join — design goes through **chgraph-change-control**. Performance at scale (FINAL overhead, merge lag, freshness p95) is entirely **OPEN** — flagged as unbenchmarked at project founding (2026-07-03), no numbers exist anywhere.

**First three steps in this repo:**
1. `benchmarks/bench_incremental.py`: synthetic graphs at 10k/100k/1M nodes; measure single-file-change → queryable-latest latency, with and without `FINAL`, before and after `OPTIMIZE`.
2. Write the delete-correctness test first (the VERIFIED gap above is the failing case), then design the tombstone strategy against it.
3. Wire `index_status` to report real persisted-row counts vs expected — the anti-#333 check — per **chgraph-architecture-contract**.

**You have a result when:** the benchmark produces a freshness/overhead curve at all three scales, the delete-correctness test passes, and a forced-degradation test shows `index_status` reporting "degraded" instead of "indexed". **OPEN.**

---

## Problem 4 — Agent-written memory fusion

**Why SOTA fails.** The two camps don't meet: static symbol graphs carry no agent-accumulated knowledge (the reference tool's only agent-written channel is `manage_adr`, **REPORTED**, https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/store/store.c), while generic memory servers (@modelcontextprotocol/server-memory) are code-blind JSONL files unsafe under concurrent writers (**REPORTED**, https://github.com/modelcontextprotocol/servers/tree/main/src/memory). Memory hallucination dominates agent-memory failures (~58.9% of non-timeout failures) and stale-fact context poisoning is a recognized failure class — arguing for provenance and freshness metadata on every stored observation (**REPORTED**, https://arxiv.org/pdf/2606.04315).

**chgraph's asset.** One engine already holds the symbols; an `observations` side table keyed by `(project, qualified_name)` with `observed_at`, `author`, and provenance columns fuses in a single join. **VERIFIED** shape on chdb 26.5.0 — static nodes LEFT JOIN agent observations:

```
1. │ config_v2.parse_config_v2  │ Function │                                                        │ 1970-01-01 10:00:00 │
2. │ legacy_parser.parse_config │ Function │ DEPRECATED: replaced by parse_config_v2; do not extend │ 2026-06-28 09:00:00 │
```

Write serialization comes free from the daemon architecture (**DECIDED**, decision 2 — single process owns the data dir; the exclusive lock is **VERIFIED**, owned by **chdb-reference**). Graphiti-style bi-temporal validity intervals `(t_valid, t_invalid)` on observation rows (**REPORTED**, https://arxiv.org/abs/2501.13956) map naturally onto versioned rows but are **OPEN**.

**First three steps in this repo:**
1. Propose the observations schema (provenance + freshness columns mandatory) through **chgraph-change-control** — this adds MCP tool surface, so it is gated twice.
2. Prototype the daemon write path append-only; never mutate observation rows, invalidate them (bi-temporal candidate).
3. Add a cross-session eval: an observation written in session A must change the answer in session B; a contradicted-by-fresh-code observation must not outrank the static fact.

**You have a result when:** both cross-session tests pass on the pinned harness, and a concurrent-writer test (two agents writing observations through the daemon simultaneously) shows zero lost or corrupted rows. **OPEN.**

---

## Problem 5 — Multi-repo / org-scale graphs

**Why SOTA fails.** Sourcegraph deprecated embeddings for Cody Enterprise because keeping vectors fresh across >100k repos was operationally untenable (**REPORTED**, https://sourcegraph.com/blog/how-cody-understands-your-codebase). The reference tool's cross-repo edges returned 0 for byte-identical pairs (#523) and its Cypher has a 100k-row ceiling — with its own docs disagreeing whether the cap is 100k or 200 rows (**REPORTED**, doc drift between mcp.c schema and installed skill; both https://github.com/DeusData/codebase-memory-mcp). Multi-million-node graphs (Linux kernel: 4.81M nodes / 7.72M edges, **REPORTED**, reference README) make row-capped query surfaces structurally unfit for org-wide questions.

**chgraph's asset.** Whole-graph aggregation is ClickHouse's home turf; the `project` column is already in the core schema (**DECIDED**, decision 5). Aggregation shape **VERIFIED** on chdb 26.5.0 (`GROUP BY project` over a symbols-with-complexity relation returns per-repo rollups correctly). Scale behavior on real multi-million-node corpora is **OPEN**. Multi-repo symbol identity (same symbol across forks/monorepo packages) has no published answer anywhere beyond Sourcegraph's SCIP monikers (**OPEN** — no design or benchmark found at project founding, 2026-07-03; the Sourcegraph blog linked above is the nearest public prior art) — **OPEN** here too.

**First three steps in this repo:**
1. Multi-repo ingest smoke test: 3 small public repos at pinned SHAs into one store, distinct `project` values, cross-project query pack (top churn × complexity across repos).
2. Scale probe: generate a synthetic 5M-node/8M-edge graph, time the query pack; this doubles as Problem-3 infrastructure.
3. Write the identity-problem design note (SCIP moniker study) — a research task per **chgraph-research-methodology**, not code.

**You have a result when:** a documented org-scale query (e.g., top-50 highest-churn × highest-complexity functions across ≥20 repos) returns correct results on a corpus whose equivalent answer would exceed the reference tool's documented row ceiling, with timings published from the benchmark script. **OPEN.**

---

## Problem 6 — Upstreaming vector_similarity into chdb (ecosystem play)

**Why SOTA (here: chdb itself) fails.** HNSW vector indexing is GA in ClickHouse server 25.8+ but compiled out of chdb. **VERIFIED** on chdb 26.5.0 (2026-07-03), exact observed error:

```
Code: 80. DB::Exception: Unknown Index type 'vector_similarity'. Available index types:
hypothesis, text, bloom_filter, sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax:
When validating secondary index `idx`. (INCORRECT_QUERY)
```

Brute-force `cosineDistance` works and orders correctly (**VERIFIED**: 3-vector query returned distances 0 / 0.2929 / 1 in correct order) and is adequate at codebase scale (10⁴–10⁵ vectors, **DECIDED**, decision 8) — but the ceiling is unmeasured and every chdb user doing vector search hits the same wall.

**chgraph's asset.** The maintainer works at ClickHouse (jordan.simonovski@clickhouse.com): a credible position to file the issue with a minimal repro, quantify demand, and shepherd a build-flag change through chdb-io. This benefits the ecosystem regardless of chgraph's fate.

**First three steps in this repo:**
1. Commit the repro script (the CREATE TABLE above) under `experiments/` and file/locate the upstream issue at https://github.com/chdb-io/chdb with it — check for existing issues first.
2. Benchmark brute-force cosineDistance latency at 10⁴/10⁵/10⁶ vectors to quantify exactly when HNSW becomes necessary — this number is the argument in the upstream issue.
3. Attempt a local chdb build with the vector index enabled to identify the build flag and binary-size cost (this is why it's compiled out — confirm, don't assume; **OPEN**).

**You have a result when:** a chdb build (local first, released wheel for the full result) accepts `CREATE ... TYPE vector_similarity` and returns kNN results matching brute-force ground truth on a pinned vector set. **OPEN.**

---

## POSITIONING: claim discipline

**chgraph may claim NOTHING today (2026-07-03).** Zero code exists. The table below is the contract for every future external statement — README, blog, benchmark, conference, tweet. If the evidence column is not satisfied by artifacts in this repo, the claim is forbidden.

| Claim | Allowed today? | Required evidence before claiming |
|---|---|---|
| "chgraph works" | NO | Indexer + daemon + MCP surface passing CI on a pinned corpus |
| "Better retrieval quality than codebase-memory-mcp" | NO | Both tools run by us on the same pinned corpus, same grader, raw per-question outputs published |
| "Closes the 83→92 gap" | NO | Our own independent measurement of both baseline numbers first — the published figures are competitor-self-reported (https://arxiv.org/abs/2603.27277) |
| "First to join full git history onto a symbol graph in one engine" | NO | A dated prior-art scan committed to this repo (tools + academic; at minimum GitNexus, CodeGraphContext, codebase-memory-mcp githistory pass, Sourcegraph, CodexGraph/RepoGraph line); then phrase as "we found no prior system that..." — never "first" |
| "Fresher / more robust incremental indexing" | NO | Problem-3 benchmark curve + delete-correctness + degradation-honesty tests, plus the same scenario run against the reference tool's watcher |
| "Org-scale graph analytics" | NO | Problem-5 corpus, timings, and scripts published |
| "X% fewer tokens" | NO | Token accounting in the harness, per-question, published raw |

**Novel vs known art (be harsh — the graph-MCP space is crowded):**

| Idea | Verdict |
|---|---|
| A code knowledge-graph MCP server | **Known art**, crowded: codebase-memory-mcp, CodeGraphContext, GitNexus, CodexGraph, RepoGraph (**REPORTED**, sources in Problems 2 and 5) |
| tree-sitter symbol extraction | Commodity |
| Recursive-CTE graph traversal in SQL | Known technique; Kuzu does native traversal better (**REPORTED**, chdb research comparison) |
| Hybrid text+vector scoring | Known art in every RAG stack |
| Status-honest degraded indexing | A quality bar, not a novelty — never market it as research |
| Bi-temporal validity on symbols | Graphiti/Zep did bi-temporal KGs (https://arxiv.org/abs/2501.13956); applying it to code structure is incremental at best |
| **Full git-evolution analytics joined onto the symbol graph in one engine, driving retrieval ranking** | **The one defensible novelty candidate** — no prior art found (Problem 2 table), but the claim stays OPEN until the dated prior-art scan is in the repo |

**Reproducibility standard for any public benchmark** (all items mandatory; partial compliance = no publication):

- [ ] Corpus pinned: repo URLs + exact commit SHAs, committed as a manifest
- [ ] Environment pinned: chdb version (26.5.0 as of 2026-07-03), Python version, uv lockfile (see **chgraph-build-and-env**)
- [ ] All sampling seeded; seeds committed
- [ ] Grader/answer-key definition committed before results are generated
- [ ] One-command rerun script in the repo
- [ ] Raw per-question outputs published, not just aggregates
- [ ] Competitor tool version pinned and its config published, when comparing
- [ ] Hardware and OS stated
- [ ] A third party can reproduce from repo artifacts alone — dry-run this on a clean machine before publishing

Any experiment that changes schema, retrieval behavior, or tool surface on its way to a result goes through **chgraph-change-control** — research urgency is not an exemption.

## Provenance and maintenance

Grounded 2026-07-03: SOTA failure modes and prior art from the Phase-1 research corpus (public URLs cited inline); all SQL/Python snippets and observed outputs executed against chdb 26.5.0 (engine 26.5.1.1) in a Python 3.12 uv venv on macOS arm64; scratch experiments used throwaway data dirs and a synthetic 4-commit git repo. The repo contained zero code at writing time — every milestone is OPEN by construction.

Re-verify on drift (run from the project venv, `.venv/bin/python` once it exists — see **chgraph-build-and-env**):

| What may drift | One-line re-check |
|---|---|
| chdb version | `python -c "import chdb; print(chdb.__version__, chdb.engine_version)"` (was: `26.5.0 26.5.1.1`) |
| vector_similarity still compiled out | `python -c "import chdb; chdb.query(\"CREATE TABLE t (id UInt32, e Array(Float32), INDEX i e TYPE vector_similarity('hnsw','cosineDistance',8)) ENGINE=MergeTree ORDER BY id\")"` — expect `Unknown Index type` |
| chdb ships no clickhouse CLI (git-import unavailable) | `find $(python -c "import chdb,os;print(os.path.dirname(chdb.__file__))") -name "clickhouse*" -type f` — expect no CLI binary |
| Reference tool surface / issue state | `curl -s https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c \| grep -c '"name"'` (coarse drift signal — was 18 on 2026-07-03; counts all `"name"` keys, not just the 14 tools) and re-read issues #333/#509/#557 |
| Reference eval numbers (83/92) | Re-check https://arxiv.org/abs/2603.27277 for revisions before citing |
| Prior-art table (Problem 2) | Re-scan GitNexus / CodeGraphContext / reference README for new git-evolution features before repeating the novelty argument |
| Upstream vector issue | Search https://github.com/chdb-io/chdb/issues for `vector_similarity` |

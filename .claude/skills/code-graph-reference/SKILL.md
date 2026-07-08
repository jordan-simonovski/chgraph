---
name: code-graph-reference
description: Use when you need domain theory for codebase knowledge graphs in chgraph - property-graph model for code, node labels and edge types (CALLS, IMPORTS, DEFINES, HTTP_CALLS, DATA_FLOWS, SIMILAR_TO), why qualified_name is the identity key, tree-sitter symbol/edge extraction, call-edge resolution precision, file-hash incremental indexing, hybrid retrieval ranking (camelCase/BM25 gap, recency, centrality), exact definitions of churn, co-change coupling, ownership, hotspot decay, or the 83%-vs-92% retrieval eval.
---

# code-graph-reference: domain theory for chgraph's codebase knowledge graph

This is the concept pack for chgraph — an as-yet-unbuilt (repo is empty as of 2026-07-03)
chdb-backed MCP server that indexes codebases into a knowledge graph. It defines the graph
model, taxonomy, extraction/indexing theory, ranking theory, and git-evolution metrics
precisely enough to implement from. It is **not** a runbook for building or operating chgraph.

Evidence labels used throughout:

| Label | Meaning |
|---|---|
| **VERIFIED** | Executed locally against chdb 26.5.0 on 2026-07-03; real output shown |
| **REPORTED** | From research with a public source URL |
| **DECIDED** | chgraph design decision (user-confirmed 2026-07-03), rationale given |
| **OPEN** | Unproven candidate; do not treat as fact |

"The reference tool" below always means **DeusData/codebase-memory-mcp** (pure-C,
SQLite-backed, 14 MCP tools, v0.8.1), the SOTA baseline chgraph is compatible with and
aims to beat. Its full tool surface and MCP semantics are owned by `mcp-server-reference`;
this skill owns the *graph domain model* it embodies.

## When NOT to use this

| You actually need | Go to sibling skill |
|---|---|
| chgraph's concrete DDL, table engines, daemon/socket architecture | `chgraph-architecture-contract` |
| chdb quirks: exclusive dir lock, HNSW compiled out, session limits, WITH RECURSIVE mechanics | `chdb-reference` |
| MCP tool names/schemas, protocol behavior, stdio shim design | `mcp-server-reference` |
| Setting up the venv, installing chdb, running anything | `chgraph-build-and-env`, `chgraph-run-and-operate` |
| The git-history ingestion pipeline and campaign plan (git-import, joins onto symbols) | `chgraph-git-evolution-campaign` (it *uses* the metric definitions below; this skill *defines* them) |
| Building the eval harness that tests the 83%-vs-92% thesis | `chgraph-validation-and-qa` |
| Changing anything defined here (taxonomy, formulas, ranking signals) | **`chgraph-change-control` — mandatory gate.** The definitions in this file are contract-grade; edits to node labels, edge types, metric formulas, or scoring-signal sets change retrieval behavior and must go through change control. |

---

## 1. The property-graph model for code

A **property graph** is: typed **nodes** (each with a label, e.g. `Function`), typed
directed **edges** (each with a type, e.g. `CALLS`), and free-form **properties**
(key-value bags) on both. It is the model used by the reference tool and by every
surveyed code-graph system (CodeGraphContext, GitNexus, CodexGraph) — REPORTED,
https://github.com/DeusData/codebase-memory-mcp, https://github.com/CodeGraphContext/CodeGraphContext.

The reference tool's minimal relational encoding (REPORTED, src/store/store.c via
https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/store/store.c):

```
nodes(project, label, name, qualified_name UNIQUE per project,
      file_path, start_line, end_line, properties JSON)
edges(source_id, target_id, type, properties JSON)
```

chgraph DECIDED to keep this two-table core reference-compatible and add typed columnar
side tables (complexity metrics, git history, embeddings as `Array(Float32)`) — the
concrete DDL lives in `chgraph-architecture-contract`.

### Why `qualified_name` is the identity key

**Definition.** A qualified name is the fully scoped path to a symbol:
`package.module.Class.method` (Python), `pkg/mod.Type.Method` (Go), etc. — the name a
compiler/linker would use to disambiguate, minus build specifics.

Reasons it is the identity key (DECIDED, following the reference tool's
`qualified_name UNIQUE per project` — REPORTED, store.c source above):

1. **Stable under edits.** Line numbers shift on every edit; file paths change on
   moves; `qualified_name` survives both. Node identity must not churn when a comment
   is added above a function.
2. **Enables name-first edge resolution.** During extraction, a call site knows the
   *name* it targets long before any database row ID exists. Edges are resolved
   symbolically (qualified name → node) then stored; identity-by-name makes the
   multi-pass pipeline order-independent.
3. **It is the natural dedup/version key for chgraph's storage.** chgraph DECIDED
   `ReplacingMergeTree(version)` keyed on `(project, qualified_name)` — `version` is the
   replacing column, not part of the sort key (canonical DDL owned by
   `chgraph-architecture-contract`, Decision 5): re-indexing a file inserts new versions
   of its symbols and the engine collapses duplicates to the highest version. An
   auto-increment ID cannot do this; a content hash changes on every body edit.
4. **Agents can read it.** Tool responses keyed by `app.auth.get_user_by_id` are
   directly actionable; opaque IDs force an extra lookup round-trip.

Known weaknesses of name-identity (all OPEN — disambiguation conventions to be fixed in
`chgraph-architecture-contract` before v1):
- Overloads (C++/Java same-name-different-signature) need a signature suffix.
- Anonymous functions/closures need a synthetic name (e.g. `parent.<lambda:line>` — but
  then line-stability is lost for exactly those symbols).
- A symbol rename is a delete+add, severing history continuity (interacts with the
  git-evolution join; see §6 caveat).

## 2. Node-label taxonomy (inherited, reference-compatible)

The 13 node labels chgraph inherits (REPORTED,
https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md):

| Label | Meaning |
|---|---|
| `Project` | The indexed repository root |
| `Package` | Distribution/import unit (npm package, Python package, Go module) |
| `Folder` | Directory |
| `File` | Source file |
| `Module` | Compilation/import unit inside a file (file-level scope for most languages) |
| `Class` | Class/struct definition |
| `Function` | Free function |
| `Method` | Function owned by a Class/Interface |
| `Interface` | Interface/trait/protocol |
| `Enum` | Enumeration |
| `Type` | Named type alias / typedef / type declaration |
| `Route` | HTTP/API route handler binding (e.g. `GET /users/:id`) |
| `Resource` | Infra resource (K8s objects, config-declared services) |

In the reference tool, `Function`/`Method` nodes additionally carry queryable complexity
properties: `cyclomatic`, `cognitive`, `loop_depth`, `transitive_loop_depth` (propagated
along CALLS edges), `linear_scan_in_loop`, `alloc_in_loop`, `unguarded_recursion`,
`param_count` (REPORTED, src/mcp/mcp.c via
https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c).
chgraph DECIDED to store these in a typed side table rather than JSON, so they are
directly usable as ranking/aggregation columns.

## 3. Edge-type taxonomy (inherited, reference-compatible)

All REPORTED from the reference tool's README and site
(https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md,
https://deusdata.github.io/codebase-memory-mcp/):

| Edge type | Semantics (source → target) |
|---|---|
| `CONTAINS_*` | Structural containment (Folder→File, File→Module, …) |
| `DEFINES` / `DEFINES_METHOD` | Scope defines symbol (Module→Function, Class→Method) |
| `MEMBER_OF` | Symbol belongs to enclosing type |
| `IMPORTS` | Module imports module/symbol |
| `CALLS` | Call site in source symbol targets callee. **The precision hotspot — see §4** |
| `RESOLVED_CALLS` | CALLS refined by type resolution (higher confidence tier) |
| `ASYNC_CALLS` | Call through async boundary (await/spawn/callback) |
| `HTTP_CALLS` | Code makes HTTP request to a `Route` (validated by `ingest_traces` runtime traces in the reference tool) |
| `DATA_FLOWS` | Value produced at source flows to target |
| `USAGE` / `USES_TYPE` | Symbol references symbol / type annotation usage |
| `IMPLEMENTS` | Class implements Interface |
| `HANDLES` | Handler bound to Route/event |
| `EMITS` / `LISTENS_ON` | Event emission / subscription |
| `WRITES` / `CONFIGURES` | Code writes to resource / config configures resource |
| `TESTS` | Test symbol exercises production symbol |
| `FILE_CHANGES_WITH` | Files that co-change in git history (the reference tool's thin evolution pass — chgraph's git-evolution tables in §6 supersede this) |
| `SIMILAR_TO` | Near-duplicate code, MinHash+LSH |
| `SEMANTICALLY_RELATED` | Embedding cosine similarity ≥ 0.80 (REPORTED threshold, deusdata.github.io) |
| `CROSS_*` | Cross-repository edges |

Two edge classes to keep distinct when implementing:
- **Deterministic structural edges** (`DEFINES`, `CONTAINS_*`, `IMPORTS`, `MEMBER_OF`):
  derivable from a single file's parse tree; near-100% precision.
- **Resolved semantic edges** (`CALLS`, `USAGE`, `DATA_FLOWS`, `HTTP_CALLS`): require
  cross-file name/type resolution; precision is the product differentiator and the
  failure mode (§4).

Traversal over these edges (recursive CTE mechanics, depth caps, closure tables) is
owned by `chdb-reference` and `chgraph-architecture-contract`.

## 4. Tree-sitter extraction: parse tree → symbols → edges

**tree-sitter** is an incremental parsing library producing a concrete syntax tree
("parse tree") per file, with error recovery (parses broken code). chgraph DECIDED to
use it via `py-tree-sitter` for ~10 top languages.

The canonical multi-pass pipeline (mirrors the reference tool's `pass_*.c` stages —
REPORTED, README source above):

1. **Definitions pass** (per file, embarrassingly parallel): walk the parse tree; every
   definition node (function_definition, class_definition, …) becomes a graph node with
   `qualified_name` built from the enclosing scope chain. Emit structural edges
   (`DEFINES`, `CONTAINS_*`).
2. **Reference collection pass** (per file): collect call sites, imports, type usages
   as *unresolved references* — (source qualified_name, target name-as-written,
   kind, location).
3. **Resolution pass** (global): resolve each reference's written name to a defined
   symbol's qualified_name using import maps and scope rules. Emit `CALLS`, `IMPORTS`,
   `USAGE`, … Unresolvable references are dropped or kept with a low-confidence marker
   (chgraph choice: OPEN).
4. **Enrichment passes**: complexity metrics, routes, git history (§6), similarity and
   embedding edges.

### Call-edge resolution is the precision hotspot

Steps 1–2 are syntax and rarely wrong. Step 3 requires *semantics* (which `get()` is
this?), and a bare parse tree does not carry types. Naive resolution — "link the call
to any same-named symbol in the project" — produces false edges, and false CALLS edges
poison everything downstream: `trace_path` returns wrong impact sets, centrality
signals (§5) inflate the wrong symbols, and agents patch the wrong target.

Evidence from the reference tool's tracker (REPORTED,
https://github.com/DeusData/codebase-memory-mcp/issues) — despite it shipping a
"Hybrid LSP" C reimplementation of type resolution for 10 languages:

| Issue | Failure |
|---|---|
| #606 | PHP framework method calls create **false-positive CALLS edges** to same-named project methods |
| #730 | TypeScript path aliases (`@packages/shared`) not resolved → missing/wrong edges |
| #554 | C++ out-of-line method definitions attach CALLS edges to the file-level Module instead of the enclosing Method |
| #480 | `trace_path` returns empty despite CALLS edges existing (resolution/traversal disconnect) |

Design consequences for chgraph (DECIDED direction, details in
`chgraph-architecture-contract`):
- Prefer **precision over recall** for CALLS: an unresolved call is recoverable via
  lexical search; a false edge silently corrupts analysis. Store resolution confidence
  (mirroring the reference tool's CALLS vs RESOLVED_CALLS split) rather than emitting
  everything at one tier.
- No Hybrid-LSP clone in v1 (breadth ≠ quality — the issues above are the proof);
  scope-and-import resolution done carefully for ~10 languages beats 158 languages
  done shallowly.
- Method calls through receivers of unknown type are the hard case in dynamic
  languages; candidate strategies (type inference lite, arity filtering,
  import-graph-constrained candidates) are OPEN pending eval data.

## 5. Incremental indexing theory (file-hash invalidation)

**The model.** The unit of invalidation is the file. Store per file: content hash
(reference tool: sha256 + mtime in a `file_hashes` table — REPORTED, store.c) and
index-time metadata. On re-index:

1. Hash every file (mtime as a cheap pre-filter, hash as truth — mtime alone lies
   under git checkout/branch switches).
2. Unchanged hash → skip entirely.
3. Changed/new file → re-run passes 1–2 for that file, then **replace all nodes and
   outgoing edges whose source file is that file** as one batch.
4. Deleted file → tombstone its nodes/edges.

chgraph DECIDED: step 3's replace is a batch per-file insert into ReplacingMergeTree
with a bumped `version`, never row-by-row upserts (MergeTree is append/merge-oriented;
mechanics owned by `chgraph-architecture-contract` / `chdb-reference`).

**The correctness traps** (theory — these are exactly where incremental indexes rot):

- **The edge-invalidation frontier.** If file B changes, edges *from unchanged file A
  into B* may now dangle or mis-resolve (A calls a function B renamed). File-hash
  invalidation alone misses this. Correct handling: after replacing B, re-run
  resolution (pass 3) for every file whose unresolved references named symbols
  defined in B — i.e. keep a name→dependent-files inverted index, or re-resolve
  edges lazily at query time. chgraph choice: OPEN (needs benchmarking).
- **Renames/moves** appear as delete+add of qualified_names. Structural graph is fine;
  continuity of history/metrics is not (§6 caveat).
- **Silent partial indexes are worse than failed indexes.** The reference tool
  returned status "indexed" for a 72k-LOC repo that produced ~500 nodes (#333,
  REPORTED), plus never-finishing indexes (#524, #563) and a v0.8.1 data-loss bug
  (#557). chgraph DECIDED: async indexing with `index_status` polling and an explicit
  "degraded" state — status honesty is a differentiator. Operational handling is
  owned by `chgraph-run-and-operate`; failure catalog by `chgraph-failure-archaeology`.

## 6. Retrieval ranking theory for code

### Why lexical search alone fails on code

Code identifiers are agglutinated: `getUserById`, `fetchHTTPResponse`. A tokenizer that
splits only on non-alphanumerics treats each identifier as one opaque token, so the
query "get user by id" cannot match `getUserById`.

**VERIFIED** (chdb 26.5.0, 2026-07-03) — this is chgraph's actual gap, not hypothetical.
chdb's `splitByNonAlpha` tokenizer (the one usable with the experimental `text` index —
see `chdb-reference`):

```sql
SELECT tokens('getUserById fetchHTTPResponse snake_case_name', 'splitByNonAlpha')
-- observed: ['getUserById','fetchHTTPResponse','snake','case','name']
```

`snake_case` splits; camelCase does not. The reference tool solved this with
camelCase-split tokenization feeding SQLite FTS5 (REPORTED, store.c). chgraph must
implement its own **subtoken splitting** at index time. VERIFIED RE2-safe splitter
(single-alternation regexes with lookahead fail — RE2 has no `(?!)`; and plain
alternation mangles acronyms: observed `'httpr','esponse'`). The working two-pass form:

```sql
WITH replaceRegexpAll(
       replaceRegexpAll(name, '([A-Z]+)([A-Z][a-z])', '\1 \2'),
       '([a-z0-9])([A-Z])', '\1 \2') AS boundary_split
SELECT arrayMap(x -> lower(x), splitByRegexp('[^A-Za-z0-9]+', boundary_split))
-- input:    'getUserById fetchHTTPResponse snake_case_name XMLHttpRequest2'
-- observed: ['get','user','by','id','fetch','http','response','snake','case','name','xml','http','request2']
```

(Trailing digits stay attached to the last subtoken — acceptable; note it in tests.)

### What BM25 is and why chgraph doesn't have it

**BM25** is the standard lexical relevance function: it scores a document for a query by
term frequency (saturating — the 10th occurrence adds little), inverse document
frequency (rare terms weigh more), and document-length normalization. SQLite FTS5 ships
it, so the reference tool's `search_graph` gets ranked lexical results for free.
ClickHouse/chdb has **no native BM25** — the text index is "an acceleration engine, not
a relevance engine" (REPORTED, https://clickhouse.com/blog/full-text-search-ga-release).
Consequence (DECIDED): the text index serves as a *candidate filter*; relevance comes
from chgraph's own hybrid score. (Whether hand-rolled SQL BM25 over subtoken stats is
worth it vs the simpler overlap score below: OPEN.)

### The hybrid scoring formula (shape DECIDED)

The documented SOTA failure mode: "embeddings don't prioritize recency, so deprecated
code can outrank current code and agents patch the wrong target" (REPORTED,
https://redis.io/blog/knowledge-graph-rag-structured-retrieval-ai-agents/). chgraph's
flagship answer is ranking with evolution signals.

**DECIDED shape**: a weighted linear combination of per-signal scores, each normalized
to [0,1], computed in one SQL statement:

```
score(symbol, query) = Σᵢ wᵢ · sᵢ      with Σ wᵢ = 1
```

| Signal | Definition | Normalization |
|---|---|---|
| `s_lex` | Subtoken **Jaccard**: \|query_subtokens ∩ symbol_subtokens\| / \|query_subtokens ∪ symbol_subtokens\| (VERIFIED 2026-07-08 to beat query-coverage \|q∩s\|/\|q\|, which barely improved on the binary placeholder — Jaccard penalizes extra symbol tokens so exact names win; ADR-0003). qn-only matches keep a 0.15 floor. | already [0,1] |
| `s_vec` | `1 - cosineDistance(embedding, query_embedding)` (brute force; HNSW is compiled out of chdb — see `chdb-reference`) | already [0,1] for non-negative embeddings; clamp otherwise |
| `s_recency` | `exp(-ln(2)/H · days_since_last_touch)`, half-life H days (from git history, §7) | already [0,1] |
| `s_central` | `log1p(in_degree) / log1p(max_in_degree)` over CALLS edges (log damping: hub symbols shouldn't drown everything) | [0,1] by construction |
| `s_complexity` | Normalized complexity (e.g. `1/(1+cyclomatic)` as a simplicity prior, or raw as a hotspot boost depending on tool intent) | DECIDED as a signal; direction and weight OPEN |

**VERIFIED** end-to-end in chdb 26.5.0 (toy 3-symbol table; `embedding Array(Float32)`,
weights 0.30 lex / 0.35 vec / 0.20 recency / 0.15 centrality):

```
qualified_name                 s_lex  s_vec   s_recency  s_central  score
app.auth.get_user_by_id        1      0.9983  0.9772     1.0        0.9948
app.legacy.get_user_by_id_v1   1      0.9990  0.0010     0.2958     0.6942
app.billing.charge_card        0      0.2706  0.9047     0.7466     0.3876
```

Note the demonstration in the data: the **stale legacy symbol has the *highest* vector
similarity** (0.9990 > 0.9983) and identical lexical score — pure embedding or lexical
ranking returns the deprecated target first. Recency + centrality demote it. This is
the thesis in one query.

- The weights above are illustrative for this toy only, NOT the decided defaults — the
  DECIDED starting default weight vector (and the initial recency half-life) is owned by
  `chgraph-git-evolution-campaign` (Phase 5); tuning is OPEN pending the eval harness
  (`chgraph-validation-and-qa`). Half-life H: 90 days used in the toy; the decided
  initial value and its sweep live in the campaign.
- Any change to the signal set, formula shape, or default weights changes retrieval
  behavior → **must go through `chgraph-change-control`**.
- OPEN candidates deliberately *not* in v1: PageRank-style centrality (Aider's
  repo-map shows it degrades on weak module boundaries — REPORTED,
  https://aider.chat/docs/repomap.html), learned weights, per-query-type weight
  profiles.

## 7. Git-evolution metrics — canonical definitions

This section is the **single home** for these definitions. The ingestion pipeline,
`git-import` mechanics, and campaign strategy live in `chgraph-git-evolution-campaign`.
All SQL below is VERIFIED in chdb 26.5.0 (2026-07-03) against a toy `file_changes`
table `(commit_hash, file_path, author, commit_time, lines_added, lines_deleted)` —
table names/shapes are illustrative; contracted schema is `chgraph-architecture-contract`'s.
"Now" is parameterized as `toDateTime('2026-07-03 00:00:00')` in the verified runs;
production uses `now()`.

### 7.1 Churn

**Definition.** For entity f (file or symbol) over window W:
`churn(f, W) = Σ over commits c in W touching f of (lines_added(c,f) + lines_deleted(c,f))`.
Report `commit_count` alongside — 1×200-line commit and 40×5-line commits have equal
churn but very different meaning.

```sql
SELECT file_path,
  sum(lines_added + lines_deleted) AS churn,
  count() AS commit_count
FROM file_changes
WHERE commit_time >= now() - INTERVAL 90 DAY
GROUP BY file_path ORDER BY churn DESC
-- VERIFIED toy output: a.py churn=156 commits=4; b.py 27/2; c.py 1/1
```

### 7.2 Co-change coupling

**Definition.** Let C_A = set of commits touching entity A, C_B likewise. Then:

- `support(A,B) = |C_A ∩ C_B|` — raw co-occurrence count.
- `confidence(A→B) = support / |C_A|` — P(B changes | A changes). **Asymmetric.**
- `jaccard(A,B) = support / |C_A ∪ C_B| = support / (|C_A| + |C_B| − support)` —
  symmetric coupling strength.

Use confidence for "if you edit A, also look at B" prompts (directional); jaccard for
symmetric coupling edges (the reference tool's `FILE_CHANGES_WITH`, which stores no
formula-defined strength — chgraph's improvement is the scored version). Guard with a
minimum-support threshold (the DECIDED initial floor is owned by
`chgraph-git-evolution-campaign`, Phase 3b) — jaccard=1.0 from a
single shared commit is noise. Also cap commit size: a 500-file formatting commit
couples everything to everything; exclude commits touching more than N files (N≈50,
OPEN) before computing.

```sql
WITH per_file AS (
  SELECT file_path, groupUniqArray(commit_hash) AS commits
  FROM file_changes GROUP BY file_path
)
SELECT a.file_path AS file_a, b.file_path AS file_b,
  length(arrayIntersect(a.commits, b.commits)) AS support,
  support / length(a.commits) AS confidence_a_to_b,
  support / (length(a.commits) + length(b.commits) - support) AS jaccard
FROM per_file a CROSS JOIN per_file b
WHERE a.file_path < b.file_path AND support >= 1
ORDER BY jaccard DESC
-- VERIFIED toy output: (a.py,b.py) support=2 confidence=0.5 jaccard=0.5;
--                      (a.py,c.py) support=1 confidence=0.25 jaccard=0.2
```

(`groupUniqArray` + `arrayIntersect` is the verified pattern; the CROSS JOIN is
quadratic in distinct files — production needs the min-support pre-filter pushed down,
strategy owned by `chgraph-git-evolution-campaign`.)

### 7.3 Ownership concentration

**Definition.** Per entity f, let `lines(a, f)` = total churn attributed to author a.
With `total(f) = Σₐ lines(a,f)` and shares `p_a = lines(a,f)/total(f)`:

- `top_owner_share(f) = max_a p_a` — the dominant author's fraction.
- `hhi(f) = Σₐ p_a²` — Herfindahl–Hirschman index. 1.0 = single author;
  → 1/n as ownership spreads over n equal authors. Higher HHI = knowledge
  concentration (bus-factor risk); very low HHI on a hotspot = no owner at all.

```sql
WITH per_author AS (
  SELECT file_path, author, sum(lines_added + lines_deleted) AS lines
  FROM file_changes GROUP BY file_path, author
),
totals AS (
  SELECT file_path, sum(lines) AS total FROM per_author GROUP BY file_path
)
SELECT p.file_path,
  max(p.lines) / any(t.total) AS top_owner_share,
  sum(pow(p.lines / t.total, 2)) AS hhi
FROM per_author p JOIN totals t ON p.file_path = t.file_path
GROUP BY p.file_path
-- VERIFIED toy output: a.py top_share=0.744 hhi=0.619; c.py 0.997/0.993
```

(Gotcha, VERIFIED the hard way: a window function inside an aggregate —
`sum(pow(lines / sum(lines) OVER (...), 2))` — throws `ILLEGAL_AGGREGATION` in
chdb 26.5.0; use the two-level aggregation above.)

### 7.4 Hotspot decay

**Definition.** Exponentially time-decayed churn — recent change counts, old change
fades, with a tunable half-life H (days):

`hotspot(f) = Σ over commits c touching f of (lines_added + lines_deleted) · exp(−ln(2)/H · age_days(c))`

A commit exactly H days old contributes half its churn; 2H days, a quarter. This is the
`s_recency`-weighted analogue of churn and the primary "is this code alive" input to
hybrid ranking (§6 uses the same kernel on last-touch date as `s_recency`; hotspot is
the mass version, recency the freshness version — keep both, they answer different
questions).

```sql
SELECT file_path,
  sum((lines_added + lines_deleted)
      * exp(-ln(2) / 30.0 * dateDiff('day', commit_time, now()))) AS hotspot_score
FROM file_changes
GROUP BY file_path ORDER BY hotspot_score DESC
-- VERIFIED (H=30, fixed 'now'=2026-07-03): a.py 85.05; c.py 35.94; b.py 13.66
-- note: c.py (one huge old commit, 300 lines at age ~93d) decays below a.py's
-- steady recent activity — exactly the intended behavior
```

Default H: the DECIDED initial value and its sweep grid are owned by
`chgraph-git-evolution-campaign` (Phase 3d/6); the final production value is OPEN
(likely per-repo tunable; decide via eval harness).

### Symbol-level caveat (applies to all four metrics)

git history is native at **file** granularity. Attributing line changes to *symbols*
requires joining line ranges from git against symbol line spans at each historical
commit — expensive and rename-fragile. DECIDED starting point: compute metrics at file
granularity and propagate to symbols contained in the file (via `DEFINES`/`CONTAINS`
edges); true symbol-level attribution is OPEN. Renames (file or symbol, §1/§5) break
metric continuity unless rename-following is implemented — also OPEN, owned by
`chgraph-git-evolution-campaign`.

## 8. The retrieval-eval framing: what chgraph is actually trying to beat

The reference tool's own preprint (REPORTED, https://arxiv.org/abs/2603.27277, across
31 repositories, self-reported):

- Graph-first retrieval: **83% answer quality**, at **10× fewer tokens** and 2.1× fewer
  tool calls.
- Plain file-exploration agent (grep/read): **92% answer quality**.
- (Its README separately claims 99.2% token reduction on 5 structural queries —
  ~3,400 vs ~412,000 tokens.)

**Read this correctly:** graph-first retrieval today is a *cost win, not a quality
win*. It trades ~9 points of answer quality for an order of magnitude in tokens. The
tool that "replaces grep" currently loses to grep on accuracy. Contributing causes, per
§4 and §5: false/missing CALLS edges, silent index staleness, and ranking that cannot
distinguish live from dead code.

**chgraph's thesis (DECIDED as the flagship bet):** the quality gap is largely a
*ranking and freshness* problem, and evolution signals (§6–§7) attack exactly that —
demote stale symbols, boost hotspots and central live paths, surface degraded indexes
instead of serving them. The §6 toy result (legacy symbol winning on pure
embedding similarity, demoted by recency) is the mechanism in miniature.

Honesty constraints:
- The 83/92 numbers are self-reported by the reference tool's author; treat as
  directional, not gospel (single dominant maintainer; benchmark not independently
  replicated — REPORTED caveat).
- Whether hybrid ranking closes the gap is **OPEN — the central open question of the
  project**. It is falsifiable and must be tested early; the eval harness design is
  owned by `chgraph-validation-and-qa`.
- Do not claim chgraph "beats" anything until that harness says so.

## Provenance and maintenance

**How this was grounded (2026-07-03):**
- Taxonomy, schema encoding, complexity properties, issue evidence (#333, #480, #524,
  #554, #557, #563, #606, #730), and eval numbers: research corpus on
  DeusData/codebase-memory-mcp (sources: repo README/`src/store/store.c`/`src/mcp/mcp.c`
  raw URLs, github.com issues, https://deusdata.github.io/codebase-memory-mcp/,
  https://arxiv.org/abs/2603.27277) — all REPORTED; the reference repo itself was not
  cloned or executed here.
- Every SQL statement shown with output was executed against chdb 26.5.0
  (engine 26.5.1.1), Python 3.12, macOS arm64, on 2026-07-03, in a throwaway session
  directory. Toy inputs, real outputs.
- Design decisions (identity key, taxonomy inheritance, formula shape, file-granularity
  start) were user-confirmed 2026-07-03 and are labeled DECIDED.

**Re-verification one-liners** (assumes a venv with chdb; see `chgraph-build-and-env`):

| Drift risk | Check |
|---|---|
| chdb/engine version | `python -c "import chdb; print(chdb.__version__, chdb.engine_version)"` (was: `26.5.0 26.5.1.1`) |
| Tokenizer still doesn't split camelCase | `python -c "import chdb; print(chdb.query(\"SELECT tokens('getUserById','splitByNonAlpha')\"))"` (was: one token) |
| Subtoken splitter still works | rerun the §6 two-pass `replaceRegexpAll` query; expect `['get','user','by','id',...]` |
| Window-in-aggregate still illegal | rerun §7.3 naive form; expect `ILLEGAL_AGGREGATION`, keep two-level form |
| Reference tool taxonomy drift | diff node labels/edge types against `https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md` (was v0.8.1, 13 labels) |
| Reference issue statuses | `gh issue view 606 --repo DeusData/codebase-memory-mcp` (also 730, 554, 480, 333) — if fixed upstream, §4 evidence needs re-dating |
| Eval baseline superseded | check arXiv:2603.27277 for revisions and citing papers |

Changing any definition, label set, edge type, formula, or signal in this file:
**`chgraph-change-control` first.**

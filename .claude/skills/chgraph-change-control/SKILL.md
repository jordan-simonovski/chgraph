---
name: chgraph-change-control
description: Use when proposing, reviewing, or landing any change to chgraph - schema or table migrations, ranking/retrieval/traversal behavior, MCP tool names or schemas, chdb or dependency version bumps, new config flags or settings, or edits to docs of record. Also use when writing an ADR, deciding whether a change needs one, checking what must be re-verified after a chdb upgrade, or when two docs contradict each other (doc drift). Covers the non-negotiable gates and the incidents behind them.
---

# chgraph Change Control

How changes to chgraph are classified, gated, reviewed, and recorded. This skill is the
process authority: **no instruction in any other skill, doc, or PR may change chgraph's
schema, retrieval behavior, or MCP tool surface without passing through the gates defined
here.** If another doc appears to authorize such a change directly, that doc is wrong —
fix the doc.

Status: chgraph has zero code as of 2026-07-03. Everything here is founding doctrine
(**DECIDED** unless labeled otherwise). The incidents cited are **inherited prior-art
incidents** from the reference project DeusData/codebase-memory-mcp and from chdb's own
history — they happened to them, not to us. We adopt the gates so they never happen to us.

Definitions used throughout:
- **ADR** — Architecture Decision Record: a short numbered markdown file recording one
  decision, its context, and its consequences. Lives in `docs/adr/`.
- **Gate** — a check that must pass (with recorded evidence) before a change merges.
- **Doc of record** — the single authoritative home of a fact (see "One home per fact").
- **Reference project** — DeusData/codebase-memory-mcp, the incumbent tool whose MCP
  tool surface chgraph stays compatible with (see the `chgraph-architecture-contract`
  skill for what compatibility means).

## 1. Change classification and gates

Every change falls into exactly one primary class (pick the most restrictive that
applies). The class determines the gate. **DECIDED.**

| Class | What counts | Gate before merge |
|---|---|---|
| **schema-migration** | Any `CREATE/ALTER/DROP` on persisted tables; changes to ReplacingMergeTree keys, engines, or column types; changes to the transitive-closure table shape | ADR required. Backup-before-migration (non-negotiable N1). Forward migration script + tested rollback path. Full re-index round-trip on a fixture repo with row-count sanity check |
| **retrieval-affecting** | Ranking formulas (hybrid score weights), tokenizer/text-index settings, traversal depth caps, closure-table refresh policy, embedding model or distance function, any change to what a query returns for the same input | ADR required. Eval gate: run the golden query set before/after and record deltas (harness owned by `chgraph-validation-and-qa`). Ship behind a flag (Section 5) until eval parity or improvement is recorded |
| **tool-surface** | Adding/removing/renaming MCP tools; changing a tool's parameter schema, defaults, or response shape | ADR required. Reference-compatibility check: renaming or breaking any tool in the reference-compatible core (search_graph, trace_path, query_graph, index_status, ...) is forbidden unless the ADR is explicitly titled "breaks reference compatibility". Re-run the tool-list drift check (Section 4, check D5) so the ADR records what the reference tool currently ships |
| **infra-dependency** | Bumping chdb, Python, py-tree-sitter, grammars, or adding any pip dependency | Exact-version pin updated in one place. chdb bumps additionally require the full drift-check suite (Section 4) with outputs pasted into the PR. ADR only if observed behavior changed |
| **docs** | Any edit to docs of record, skills, ADR statuses | House-style check (Section 6): evidence labels present, volatile facts date-stamped, one-home-per-fact respected (non-negotiable N3). No board/approval, but violations block merge |

Rules of thumb:
- A change that touches two classes takes both gates (e.g. a new ranking signal stored in
  a new column = schema-migration + retrieval-affecting).
- "It's just a default change" is retrieval-affecting if any query result can differ.
- Reverting a change takes the same gate as the change itself, minus the ADR (update the
  original ADR's status instead).

## 2. Non-negotiables: rule, rationale, incident

Each rule below is **DECIDED**. Each incident is an **inherited prior-art incident**,
not chgraph history.

### N1 — Backup before migration; never destructively "auto-repair"

**Rule.** Any schema-migration gate begins by snapshotting the chdb data directory
(copy or hard-link tree) before the first DDL statement runs. Corruption detection must
*quarantine* (rename dir aside, surface `degraded` in index_status) — code that deletes
user data on a heuristic is an automatic review rejection, no exceptions.

**Rationale.** A codebase graph is hours of indexing work and, once agents write ADRs
into the store, irreplaceable knowledge. A wrong "repair" is strictly worse than a
crash: the crash is recoverable, the deletion is not.

**Inherited incident.** codebase-memory-mcp v0.8.1 silently deleted project databases
when its corruption heuristic fired — "data loss with no recovery" (**VERIFIED** the
issue exists and is open as of 2026-07-03: issue #557, titled `cbm v0.8.1 silently
deletes project DBs on "corrupt" detection — data loss with no recovery`,
https://github.com/DeusData/codebase-memory-mcp/issues/557).

### N2 — No silent success: status honesty + eval gate

**Rule.** Every state-changing operation reports what actually happened: index_status
must expose `degraded` (with counts) whenever persisted rows fall short of expected
rows, and no retrieval-affecting change merges without a before/after run of the golden
query eval set. "Tests pass" is not evidence for retrieval quality; the eval delta is.

**Rationale.** Silent degradation is the worst failure mode for a retrieval tool:
agents keep trusting a graph that quietly stopped representing the code, and every
downstream answer is confidently wrong. Status honesty is an explicit chgraph
differentiator (see `chgraph-architecture-contract`).

**Inherited incident.** codebase-memory-mcp returned status `"indexed"` while
persisting ~500 nodes for a 72k-LOC Rust repo (**VERIFIED** the issue exists and is
open as of 2026-07-03: issue #333, titled `Silent index degradation — status:"indexed"
but only ~500 nodes for 72k LOC Rust codebase`,
https://github.com/DeusData/codebase-memory-mcp/issues/333). The maintainer's fix was a
reactive knob (`CBM_DUMP_VERIFY_MIN_RATIO`) rather than a design guarantee
(**REPORTED**: https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md).

### N3 — One home per fact (docs anti-drift)

**Rule.** Every fact — a numeric limit, a default, a version pin, a table name — has
exactly one doc of record. Every other doc, skill, or tool description that needs the
fact links to that home instead of restating the value. When you change the fact, you
change one file. A grep for the old value across the repo is part of the docs gate.

**Rationale.** Restated facts fork. The fork that an agent reads first wins, and agents
act on wrong limits confidently.

**Inherited incident.** The reference project's own installed agent skill says
`query_graph` has a "200-row cap" while its MCP tool schema documents a "hard 100k row
ceiling". **VERIFIED** directly against upstream source on 2026-07-03: `src/cli/cli.c`
contains `"query_graph has a 200-row cap"` and `src/mcp/mcp.c` contains `"There is a
hard 100k row ceiling"` (fetch both from
https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/cli/cli.c and
`.../src/mcp/mcp.c` and grep). Two homes, two answers, and agents were taught the
wrong one.

### N4 — Pin exact versions; every chdb bump re-runs the drift suite

**Rule.** chdb is pinned to an exact version in one place. The pin is the pip *wrapper*
dist — `chdb==4.2.0` as of 2026-07-03, which resolves `chdb-core==26.5.0` (the
engine-tracking layer that `chdb.__version__` reports); pinning mechanics and the pin's
home are `chgraph-build-and-env` (trap T1), and the full three-layer version story is
owned by `chdb-reference` §1 / FA-003 in `chgraph-failure-archaeology` — link there,
don't restate the numbers. Any bump — patch included — re-runs all drift checks in
Section 4 and pastes the outputs into the PR. Behavior differences become ADRs before
the bump lands.

**Rationale.** chdb ships a whole ClickHouse engine; "patch" bumps can recompile
features in or out. chgraph's architecture is load-bearing on specific verified
behaviors (the exclusive lock forces the daemon design; vector_similarity being absent
forces brute-force embeddings).

**Inherited incident.** chdb's own docs described the engine as ClickHouse 25.8 while
the shipped package wrapped 26.5.1.1, and the module's version reporting changed scheme:
the pip wrapper dist stayed on 4.x (`chdb` 4.2.0) while `chdb.__version__` moved to
engine-tracking 26.5.0 (the `chdb-core` version) — so "chdb v4.2.0" and "chdb 26.5.0"
describe the *same install* (**VERIFIED** locally 2026-07-03, see Section 4 check D1;
docs lag **REPORTED**: https://clickhouse.com/docs/chdb). If you trust the docs' version
instead of the verified one, you design against features you don't have.

## 3. ADR discipline

**DECIDED.** ADRs live at `docs/adr/NNNN-short-kebab-title.md`, `NNNN` zero-padded and
strictly increasing (`0001-...`, `0002-...`). Never renumber; never delete — supersede.
Precedent that this is agent-workable: the reference project's `manage_adr` MCP tool has
agents reading and writing ADRs as its only agent-written knowledge (**REPORTED**:
https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c).

Template (copy verbatim for every new ADR):

```markdown
# NNNN — <Title: the decision as a sentence>

- Status: Proposed | Accepted | Superseded by NNNN | Retired
- Date: YYYY-MM-DD
- Class: schema-migration | retrieval-affecting | tool-surface | infra-dependency | docs
- Owner: <person or agent responsible for follow-through>

## Context
What forced a decision. Link evidence with labels (VERIFIED/REPORTED/DECIDED/OPEN).

## Decision
The decision, in one paragraph, imperative mood.

## Alternatives rejected
Each alternative with the one reason it lost.

## Consequences
What gets easier, what gets harder, what must now be maintained.

## Verification
The commands run to validate this decision and their observed output (or the gate
runs recorded, with dates). Anything unproven is labeled OPEN with a follow-up.

## Rollback
Concrete steps to undo, or "irreversible because <reason>".
```

ADR rules:
- One decision per ADR. If the Context section argues for two decisions, split it.
- An ADR with an empty Verification section cannot move from Proposed to Accepted.
- Changing your mind = new ADR that supersedes the old one; edit only the old ADR's
  Status line.
- The locked founding decisions (runtime, daemon architecture, schema shape, etc.) are
  owned by `chgraph-architecture-contract`; changing one of those requires an ADR here
  *and* an update to that skill in the same PR.

## 4. chdb upgrade protocol (the drift-check suite)

Run all five checks on ANY chdb version change. All commands below were executed on
2026-07-03 against chdb 26.5.0 (engine 26.5.1.1), macOS arm64, Python 3.12 — the pasted
output is real observed output. `$PY` is the project venv interpreter
(`.venv/bin/python` by convention — venv setup is owned by `chgraph-build-and-env`).
For deeper explanation of each chdb behavior, see the `chdb-reference` skill; this
section owns only the upgrade ritual.

### D1 — Version identity

```bash
$PY -c "import chdb; print(chdb.__version__, chdb.engine_version)"
```

Observed (2026-07-03): `26.5.0 26.5.1.1`. If either number moved, the whole suite runs.

### D2 — Exclusive data-directory lock (architecture-critical)

The single-daemon design exists *because* of this lock. If a bump ever relaxes it,
that is an ADR-worthy architecture event, not a free win.

```bash
$PY - <<'EOF'
import subprocess, sys, time
DIR = "/tmp/chgraph-driftcheck-lockdir"
holder = subprocess.Popen([sys.executable, "-c",
    f"import chdb.session, time; s = chdb.session.Session({DIR!r}); s.query('SELECT 1'); time.sleep(15)"])
time.sleep(5)
second = subprocess.run([sys.executable, "-c",
    f"import chdb.session; s = chdb.session.Session({DIR!r}); s.query('SELECT 1')"],
    capture_output=True, text=True)
holder.terminate()
print("rc:", second.returncode)
print(second.stderr)
EOF
```

Observed (2026-07-03, chdb 26.5.0): `rc: 1`, stderr contains both the engine error
`Code: 76. DB::Exception: Cannot lock file /tmp/chgraph-driftcheck-lockdir/status.
Another server instance in same directory is already running. (CANNOT_OPEN_FILE)` and
the Python-level exception `RuntimeError: Failed to create connection: Code: 36.
DB::Exception: Error initializing EmbeddedServer: . (BAD_ARGUMENTS)`. Note the trap:
the *exception object* carries only Code 36; Code 76 appears on stderr. Read-only mode
does not bypass the lock (**VERIFIED** Phase 1 on the 4.2.0 scheme; lock itself
re-verified on 26.5.0). Expected: rc 1 with those errors. rc 0 = lock relaxed = stop
and write an ADR.

### D3 — WITH RECURSIVE with cycle guard

```bash
$PY - <<'EOF'
import chdb
print(chdb.query("""
WITH RECURSIVE walk AS (
    SELECT 'a' AS node, ['a'] AS path, 0 AS depth
    UNION ALL
    SELECT e.dst, arrayPushBack(walk.path, e.dst), walk.depth + 1
    FROM (SELECT 'a' AS src, 'b' AS dst UNION ALL SELECT 'b','c' UNION ALL SELECT 'c','a') AS e
    JOIN walk ON e.src = walk.node
    WHERE NOT has(walk.path, e.dst) AND walk.depth < 10
)
SELECT node, path FROM walk ORDER BY depth
""", "CSV"))
EOF
```

Observed (2026-07-03): three rows, terminating despite the a→b→c→a cycle:

```
"a","['a']"
"b","['a','b']"
"c","['a','b','c']"
```

A hang or unbounded output means recursive-CTE semantics changed — traversal queries
are unsafe until re-reviewed.

### D4 — text index still works; vector_similarity still absent

```bash
$PY - <<'EOF'
import chdb.session
s = chdb.session.Session("/tmp/chgraph-driftcheck-idxdir")
print(s.query("""
SET allow_experimental_full_text_index = 1;
CREATE TABLE t_text (s String, INDEX idx s TYPE text(tokenizer='splitByNonAlpha')) ENGINE = MergeTree ORDER BY s;
SELECT 'text index OK';
""", "CSV"))
try:
    s.query("""
SET allow_experimental_vector_similarity_index = 1;
CREATE TABLE t_vec (v Array(Float32), INDEX vi v TYPE vector_similarity('hnsw','cosineDistance', 8)) ENGINE = MergeTree ORDER BY tuple();
""")
    print("vector_similarity WORKS NOW - ADR required")
except Exception as e:
    print("vector_similarity absent as expected:", str(e)[:200])
s.close()
EOF
```

Observed (2026-07-03, chdb 26.5.0): `"text index OK"` then
`vector_similarity absent as expected: Code: 80. DB::Exception: Unknown Index type
'vector_similarity'. Available index types: hypothesis, text, bloom_filter,
sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax`. Note: as of 26.5.0 the text-index
flags (`allow_experimental_full_text_index`, `enable_full_text_index`) both default
to 1 and the CREATE succeeds with no SET at all (**VERIFIED** 2026-07-03; fact home
`chdb-reference` §7c) — the script keeps the SET because passing it is harmless on 26.5
and it still exercises old builds. Two possible drift events: (a) the text-index flag
defaults flip back to 0, or the index syntax changes — retrieval-affecting;
(b) vector_similarity starts working — write an ADR before switching from brute-force
cosineDistance, since embedding search behavior would change (retrieval-affecting gate).

### D5 — Reference tool surface (run on tool-surface changes and quarterly)

```bash
curl -sf https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c \
  | grep -oE '^\s*\{"[a-z_]+",' | grep -oE '[a-z_]+'
```

Observed (2026-07-03): exactly 14 names — `index_repository search_graph query_graph
trace_path get_code_snippet get_graph_schema get_architecture search_code
list_projects delete_project index_status detect_changes manage_adr ingest_traces`.
A diff against the 14-tool compatibility contract recorded in `mcp-server-reference`
(its home) means the compatibility target moved — file an OPEN item there.

After the suite: paste all outputs in the PR, update the pin, and record any behavioral
delta as an ADR *before* merging the bump.

## 5. Config/flag addition checklist

**DECIDED.** Every new config option or feature flag ships with all six fields filled
in, in the flag's registration comment/doc — a flag missing any field fails review:

| Field | Requirement |
|---|---|
| Name | Namespaced, lowercase snake_case, e.g. `chgraph_rank_recency_weight`. No abbreviations that need explaining |
| Default | Stated explicitly, and the default must be the *current* behavior (adding a flag never changes behavior by itself) |
| Label | `prod` (safe, supported) or `experimental` (may change/vanish; surfaced as experimental in any status output). Nothing ships unlabeled |
| Owner | A named person/agent who answers for it |
| Re-verification command | One command that demonstrates the flag doing its job (goes into the PR with observed output — same evidence bar as Section 4) |
| Retirement criteria | The condition under which the flag is removed or hardened to `prod`, with a target date or trigger event. "Someday" fails review |

Cautionary pattern to avoid (**REPORTED**): the reference project's
`CBM_DUMP_VERIFY_MIN_RATIO` knob was bolted on reactively after the silent-degradation
incident (#333) with no labeled status or retirement plan — a flag as apology, not
design (https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md).
Flipping any flag's *default* later is a retrieval-affecting or tool-surface change and
takes that gate.

## 6. Docs of record: conventions and house style

**DECIDED.** These rules apply to every doc, skill, ADR, and tool description in chgraph.

1. **Evidence labels.** Any statement a reader could doubt carries one of:
   **VERIFIED** (someone ran it and saw the output — say when), **REPORTED** (external
   source — cite a public URL, never a local path), **DECIDED** (design choice —
   rationale given or ADR linked), **OPEN** (unproven candidate). Overselling is the
   cardinal sin; a wrong runbook is worse than none.
2. **Date-stamp volatile facts.** Versions, upstream issue states, benchmark numbers:
   "as of 2026-07-03, chdb 26.5.0". Undated volatile facts fail the docs gate.
3. **One home per fact** (non-negotiable N3). Before writing a limit, default, or
   version anywhere, grep for it; if it already has a home, link, don't restate.
4. **Paste real output.** Where a doc shows expected output, it is pasted observed
   output, not typed-from-memory output.
5. **Never imply code exists when it doesn't.** Until chgraph ships, future CLI/tool
   behavior is written as DECIDED/OPEN projections, not as runnable instructions.
6. **Imperative runbook voice, jargon defined at first use, tables/checklists over
   prose.** Skills follow the sibling-skill format (frontmatter, "When NOT to use
   this", "Provenance and maintenance").
7. **ADR statuses are the only mutable history.** Everything else is edited forward;
   superseded guidance is replaced in its home, not annotated around.
8. **"Phase-1" means the pre-code research pass of early July 2026.** Its primary
   sources are the public URLs cited inline wherever a Phase-1 claim appears; the
   internal research reports/briefs behind it are not part of the repo and are never
   citable evidence on their own (rule 1: REPORTED needs a public URL). A Phase-1
   claim with no public URL must be relabeled DECIDED (with rationale) or OPEN.

## When NOT to use this

- You want to know what the locked architecture decisions *are* (daemon design, schema
  shape, tool compatibility contract) → `chgraph-architecture-contract`. This skill
  only governs how those decisions get *changed*.
- You need chdb behavior details (session semantics, lock mechanics, index types,
  sharp edges) beyond the upgrade ritual → `chdb-reference`.
- You need the reference tool's tool semantics or graph model → `code-graph-reference`;
  the 14-tool compatibility contract and MCP protocol mechanics → `mcp-server-reference`.
- You're setting up the venv, pinning installs, or fighting the Python-3.9 trap →
  `chgraph-build-and-env`.
- You're actually running the eval/golden-query harness a gate demands →
  `chgraph-validation-and-qa` (this skill only says *when* an eval is required).
- You want the full catalog of inherited incidents and their lessons →
  `chgraph-failure-archaeology`. This skill cites only the incidents that anchor its
  gates.
- Something is broken right now → `chgraph-debugging-playbook` /
  `chgraph-run-and-operate`. Change control is for deliberate changes, not firefights.

## Provenance and maintenance

Grounding: written 2026-07-03 against an empty repo, before any chgraph code exists.
All chdb commands in Section 4 were executed that day on macOS arm64 (Python 3.12,
chdb 26.5.0 / engine 26.5.1.1) and outputs pasted verbatim. The reference project's
tool list, the 200-vs-100k doc drift strings, and the existence/titles/open-state of
issues #557 and #333 were fetched live from GitHub the same day. Process rules
(classification, gates, ADR template, flag checklist, house style) are DECIDED founding
doctrine with rationale inline; none have been exercised on a real chgraph change yet —
expect the first few ADRs to refine them (via a docs-class change to this skill).

Re-verification one-liners for everything that can drift:

| What may drift | Re-check |
|---|---|
| chdb pin / engine version | `$PY -c "import chdb; print(chdb.__version__, chdb.engine_version)"` (expect `26.5.0 26.5.1.1` as of 2026-07-03) |
| Exclusive dir lock | Section 4 D2 snippet (expect rc 1, Code 76 on stderr) |
| WITH RECURSIVE + cycle guard | Section 4 D3 snippet (expect 3 rows, terminates) |
| text index / vector_similarity | Section 4 D4 snippet (expect `text index OK` + `Unknown Index type 'vector_similarity'`) |
| Reference tool list (14 tools) | Section 4 D5 curl+grep one-liner |
| Doc-drift incident still live upstream | `curl -sf https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/cli/cli.c \| grep -o '200-row cap'` and same for `mcp.c` with `grep -o '100k row ceiling'` |
| Incident issues #557 / #333 state | `curl -sf https://api.github.com/repos/DeusData/codebase-memory-mcp/issues/557 \| grep -o '"state": *"[a-z]*"'` (both open as of 2026-07-03) |

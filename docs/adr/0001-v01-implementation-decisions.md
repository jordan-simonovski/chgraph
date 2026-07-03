# 0001 — v0.1 implementation decisions and deviations from founding skills

- Status: Accepted (per-item status noted below; three items are `needs-review`)
- Date: 2026-07-03
- Class: mixed — schema-migration, retrieval-affecting, tool-surface, infra-dependency (each item classified individually below)
- Owner: chgraph-v01-core implementation (Tasks 1–12)

## Context

Tasks 1–12 built chgraph v0.1 from the founding skills (`chgraph-architecture-contract`,
`chgraph-run-and-operate`, `mcp-server-reference`, `code-graph-reference`,
`chgraph-git-evolution-campaign`, `chgraph-build-and-env`) plus the implementation plan.
During that work a number of deviations, refinements, and API-drift adaptations from the
plan's verbatim guidance were made, each individually justified at the time and logged
live in `.superpowers/sdd/adr-items.md`. This ADR is the durable, change-control-format
record of every one of those items, per Task 13 Step 3.

**Note on ADR granularity.** `chgraph-change-control` §3 states "one decision per ADR."
This document intentionally departs from that rule: it bundles every v0.1 implementation
deviation into a single retrospective ADR-0001, per the Task 13 brief's explicit
instruction to produce one file covering items (a)–(f) plus the additional lettered
items observed during implementation. Going forward (ADR-0002+), each new decision
should get its own ADR file as the skill specifies; this bundling is a one-time,
brief-directed exception for the v0.1 baseline, not a precedent.

Each item below follows Context → Decision → Consequences → Status, per the
`chgraph-change-control` §3 template, letter-keyed to match
`.superpowers/sdd/adr-items.md` so the two can be cross-checked entry-for-entry.

---

### (a) Git tables: TRUNCATE-then-reload idempotency

**Class:** retrieval-affecting.

**Context.** `gitingest.ingest_git` re-runs on every `index_repository` call. Plan
guidance did not specify how repeat ingests avoid duplicate/stale rows in
`chgraph.git_commits` / `chgraph.git_file_changes`.

**Decision.** `ingest_git` TRUNCATEs the git tables before reloading from `git log
--numstat`, rather than attempting incremental append or upsert-by-commit-hash.

**Consequences.** Safe under chgraph's one-project-per-data-dir invariant (no
cross-project data ever coexists in these tables, so a full TRUNCATE cannot destroy
another project's history). Every reindex re-parses full git history, which is fine at
v0.1 scale (a synthetic and small real repos) but does not scale ingestion cost with
repo history length — deferred: incremental append keyed on commit hash, tracked as a
v0.2 concern, not built here. Mirrors the same pattern independently applied to
`chgraph.nodes`/`chgraph.edges` in item (k).

**Status:** accepted.

---

### (b) `uninitialized` status state added to the `index_status` enum

**Class:** tool-surface (extends a documented enum in `mcp-server-reference` §4).

**Context.** `mcp-server-reference` §4 defines the index-status state machine as
`queued → running → indexed | degraded | failed`. That enum has no state for "daemon is
up, project dir exists, but `index_repository` has never been called" — the daemon
needed one so `index_status`/CLI/shim callers get an honest answer instead of an error
or a misleading `queued`.

**Decision.** Add `uninitialized` as the state reported before the first
`index_repository` call completes (Task 10, daemon status machine).

**Consequences.** Callers must handle a fifth state value; this is additive (no existing
state's meaning changes) but is a literal extension of a doc-of-record enum owned by
`mcp-server-reference`, which per `chgraph-change-control` §1 tool-surface class requires
sign-off before it is fully final. Status honesty (INV-3) is preserved — no state is
folded into another.

**Status:** needs-review — the enum extension is functionally in place and exercised by
tests (Task 10), but `mcp-server-reference` §4 has not yet been amended to document
`uninitialized` alongside the other four states; that doc update is the outstanding
action before this can move to accepted.

---

### (c) `MIN_NODES_PER_KLOC = 5.0` as an OPEN placeholder threshold

**Class:** operational threshold (owned in principle by `chgraph-validation-and-qa` §5).

**Context.** `chgraph-run-and-operate` §5 and `chgraph-architecture-contract` (INV-3)
require `index_status` to detect and report degradation via a node-count plausibility
check, but no calibrated threshold existed pre-implementation.

**Decision.** `src/chgraph/indexer.py` hardcodes `MIN_NODES_PER_KLOC = 5.0` (symbols per
1,000 source lines) as the degradation trigger, commented in-code as an uncalibrated
placeholder.

**Consequences.** The synthetic fixture repo passes comfortably above this threshold, so
the campaign's staleness thesis test is not blocked by it, but the number has no
empirical backing against real-world repos of varying density (comment-heavy code,
generated code, etc.) and could both false-positive (flag healthy sparse repos as
degraded) and false-negative (miss a genuinely broken parse that still clears 5.0/KLOC
on a large-enough file set). Calibration is explicitly deferred to
`chgraph-validation-and-qa`'s eval-harness milestone.

**Status:** needs-review — functional and tested, but the constant itself is
uncalibrated and must not be read as a quality claim.

---

### (d) `query_graph` and tier-2 tools (`search_code`, etc.) absent from v0.1

**Class:** tool-surface.

**Context.** `mcp-server-reference` §5 lists `query_graph` as "Tier-1 name, OPEN
semantics" (Cypher-to-SQL translation vs. templates vs. raw SQL is explicitly an
undecided, change-control-gated question) and lists `search_code`/`get_architecture`/
`detect_changes`/`manage_adr`/`ingest_traces` as Tier-2 or not-planned.

**Decision.** v0.1 ships only the Tier-1 tools whose semantics were already decided:
`index_repository`, `search_graph`, `trace_path`, `get_code_snippet`,
`get_graph_schema`, `index_status`, `list_projects`, `delete_project`. `query_graph` and
all Tier-2 tools are not implemented.

**Consequences.** chgraph v0.1 is not a drop-in 14-tool replacement for the reference
tool; any agent workflow depending on Cypher queries or `search_code` has no chgraph
equivalent yet. This is consistent with the plan's explicit "Explicitly OUT of v0.1"
table and avoids building `query_graph` semantics without the change-control decision
`mcp-server-reference` §5 says is required first.

**Status:** accepted.

---

### (e) Vector ranking weight structurally present, contributes 0

**Class:** retrieval-affecting.

**Context.** `search.py`'s hybrid score formula (`W = {"lex": 0.35, "vec": 0.30, "rec":
0.20, "cen": 0.15}`) was specified by the campaign with a vector/semantic-similarity
term, but no embedding model has been chosen and no embeddings are computed in v0.1.

**Decision.** The `vec` weight (0.30) is retained in the formula's shape/constant table
for forward-compatibility, but no `vec` signal is computed or joined into the query
today — effectively `vec` contributes 0 to every row's score (the formula only sums
`lex`, `rec`, `cen` terms currently wired up; see `src/chgraph/search.py`).

**Consequences.** Scores are not comparable to a future version once embeddings land
(adding a real `vec` signal will change every ranking — that future change is itself
retrieval-affecting and needs its own eval-gated ADR per `chgraph-change-control` §1).
Until then, ranking is effectively a 3-signal (lexical/recency/centrality) formula
wearing a 4-signal weight table; this is documented here so nobody mistakes the `W`
dict for a currently-active semantic signal.

**Status:** accepted.

---

### (f) API drift adaptations from Tasks 6/8/12 (summary; see (g)–(q) for specifics)

**Class:** umbrella — each underlying item is separately classified below.

**Context.** The plan's verbatim code samples for symbol-graph construction (Task 6),
`search_graph` (Task 8), and the MCP shim (Task 12) needed small, deliberate departures
from their literal text to satisfy the plan's own stated invariants (precision-over-
breadth for CALLS edges, "never silently ignore" for unsupported params, INV-3 status
honesty) or to fix bugs the verbatim samples would otherwise have shipped.

**Decision.** Each concrete instance is recorded as its own item below: (i) and (j) for
Task 6 (`parse_python.py`), (k), (l), (m) for Tasks 7/8 (`indexer.py`/`search.py`), (q)
for Task 12 (`shim.py`). This entry exists only so the plan's required minimum-(f) is
traceable to its specifics rather than left as a vague catch-all.

**Consequences.** None beyond those recorded per-item.

**Status:** accepted (as a pointer entry; see referenced items for their own status).

---

### (g) `.python-version` pinned to 3.12 despite `uv` resolving 3.14.6

**Class:** infra-dependency.

**Context.** `pyproject.toml`'s `requires-python = ">=3.12"` let `uv sync` resolve
Python 3.14.6 on this machine. `chgraph-build-and-env` verifies chdb's embedded engine
behavior specifically on Python 3.12; 3.14 was untested by that skill's grounding work.

**Decision.** Pin `.python-version = 3.12` so `uv` always provisions the
team-verified interpreter, rather than trusting `>=3.12` to land on a safe version.

**Consequences.** All dependencies have 3.14 wheels and the full test suite passes on
3.14 in ad hoc checks, so this is a conservative choice, not a fix for an observed
failure — but chdb's embedded-engine behavior on 3.14 is simply unverified territory,
and `chgraph-build-and-env` (infra-dependency's doc of record) should be updated if a
future task deliberately moves off 3.12.

**Status:** accepted.

---

### (h) `-c core.quotePath=false` added to the git invocation in `gitingest._git`

**Class:** retrieval-affecting (graph correctness — path join integrity).

**Context.** The plan's verbatim campaign Phase-2b git-log parser did not pass
`core.quotePath=false`. Without it, git octal-escapes non-ASCII bytes in paths in its
plumbing output, which would silently corrupt any `path`/`old_path` join used to link
git history rows to symbol-graph file nodes.

**Decision.** Add `-c core.quotePath=false` to every git invocation in
`gitingest._git`, applied symmetrically to both the parsing pass and
`verify_git_counts`, so counts stay consistent between the two.

**Consequences.** Non-ASCII filenames now round-trip correctly through
`git_file_changes`/`git_commits` and join cleanly against `chgraph.nodes.file_path`.
Two residual risks were logged and remain open, both `needs-review`: (1) rename counts
are not gate-checked by `verify_git_counts` (which intentionally gates only
commits+file_changes per plan); (2) binary-file `numstat '-'` handling is implemented
but unexercised, because the synthetic fixture repo is byte-locked to the skill and
contains no binary files, so there is no regression test covering that path today.

**Status:** accepted (core fix); residual risks needs-review — revisit when a real
repo containing binaries or prefix-less renames is indexed.

---

### (i) `parse_python.walk_calls` suppresses CALLS edges to locally-shadowed names

**Class:** retrieval-affecting.

**Context.** The plan's verbatim `walk_calls` example resolves every call expression
`f(...)` against module-level `def`s by name alone. If a parameter, a local `x = ...`
target, or a nested `def`/`class` shadows a module-level definition's name, the verbatim
algorithm emits a CALLS edge to the wrong (module-level) target — a false edge.
`code-graph-reference` names false CALLS edges as chgraph's own cautionary tale for
precision-over-breadth.

**Decision.** Track locally-bound names (parameters, `=` assignment targets, nested
`def`/`class` names) per lexical scope, and suppress CALLS resolution for any callee
name that is locally bound in the enclosing scope, rather than resolving it against the
module-level symbol table.

**Consequences.** Strictly removes false edges; the plan's own 4 Task-6 tests still pass
unmodified (the change only prevents edges the verbatim algorithm would have added
incorrectly — it adds no new edges). Decided autonomously mid-task (user unavailable for
~60s); open to revisiting if a different resolution strategy (e.g., type-aware
resolution) is preferred later.

**Status:** accepted.

---

### (j) File-node `end_line` convention change + `from . import x` fix

**Class:** retrieval-affecting (minor; bugfix).

**Context.** The plan's verbatim File-node `end_line` was `source.count(b"\n") + 1`.
Function/Class node spans use `tree_sitter` node `end_point` line numbers, a different
convention that happens to be numerically identical for well-formed files but is
inconsistent in principle. Separately, the verbatim relative-import handling produced a
malformed qualified target for `from . import x` (yielding `..x` instead of `.x`).

**Decision.** Switch File-node `end_line` to `tree.root_node.end_point[0] + 1` for
convention consistency with Function/Class spans, and fix the relative-import target
construction so `from . import x` yields `.x`.

**Consequences.** No behavior change in practice for `end_line` (values are numerically
identical); the import-target fix corrects a real bug the verbatim sample would have
shipped. No tree-sitter API drift was involved — tree-sitter 0.26.0 matched the brief
exactly.

**Status:** accepted.

---

### (k) `index_repository` TRUNCATEs `chgraph.nodes`/`chgraph.edges` before reload

**Class:** retrieval-affecting.

**Context.** The plan's verbatim reindex strategy bumps `version` and re-inserts rows,
relying on `ReplacingMergeTree(version) FINAL` to collapse old vs. new versions of the
same row. But FINAL only collapses rows that share the same ORDER BY key — a symbol or
file that was deleted or renamed since the last index has no same-key successor row to
collapse against, so its old-version node/edge rows linger forever as ghosts. That
silently drifts the graph away from the true repo state, which conflicts with INV-3's
spirit (status/graph must reflect reality).

**Decision.** `index_repository` reads the next `version` first (so it still
increments monotonically), then TRUNCATEs `chgraph.nodes` and `chgraph.edges` before the
batch reload — mirroring the same TRUNCATE-then-reload pattern independently applied to
git tables in item (a).

**Consequences.** Deleted/renamed symbols no longer leave ghost nodes/edges. Safe only
because of the one-project-per-data-dir invariant (a TRUNCATE cannot affect another
project's rows). Does not attempt rename-*chain* tracking (a symbol renamed across
commits is still seen as a delete + an add) — that remains deferred to v0.2 per the
plan's own scope.

**Status:** accepted.

---

### (l) `search_graph` v0.1 parameter scope and no-criterion behavior

**Class:** tool-surface.

**Context.** `mcp-server-reference` §5 row 2 specifies `search_graph`'s full
tier-1-compatible parameter set: `query`, `name_pattern`, `semantic_query`, `label`,
`qn_pattern`, `file_pattern`, `min_degree`, `max_degree`, `exclude_entry_points`,
`limit`/`offset`, with only `project` required (i.e., a criterion-less call should list
the whole graph, paginated).

**Decision.** v0.1's `search_graph(store, project, query=None, name_pattern=None,
label=None, limit=200, offset=0)` implements only `query`/`name_pattern`/`label`/
`limit`/`offset`. The vector signal is structurally present in the ranking formula but
always scores 0 per item (e). Additionally, calling `search_graph` with none of
`query`/`name_pattern`/`label` set raises `ValueError`, rather than falling back to a
bare list-all as the reference contract's "only `project` required" implies — this is
directed by the plan's own test (`test_requires_some_criterion`), reasoned as avoiding
an accidental full-graph dump.

**Consequences.** Not yet full tier-1 parameter parity with the reference contract; any
caller relying on `semantic_query`/`qn_pattern`/`file_pattern`/degree filters or a
criterion-less browse call gets no chgraph equivalent yet (the shim explicitly rejects
those params with a clear error — see item (q) — rather than silently ignoring them).

**Status:** accepted as the deliberate v0.1 scope; needs-review at the next MCP
tool-surface task, where full parameter parity and criterion-less list/browse behavior
should be reconciled against `mcp-server-reference` §5 or that section amended to
reflect the narrower v0.1 contract.

---

### (m) `search_graph` computes `total` via a dedicated `COUNT` query

**Class:** tool-surface (response-metadata correctness).

**Context.** The plan's Step 4 sanctioned a fallback of a separate `SELECT count()`
query if `count() OVER ()` combined with `FINAL` on `chgraph.nodes AS n FINAL` errored
on chdb 26.5.0. In practice `count() OVER ()` did **not** error — it worked as written,
so the documented fallback trigger never fired. A different, unanticipated problem did
surface: `count() OVER ()` only annotates *returned* rows, so once `offset` is past the
last matching row, zero rows return and the window-function total is unavailable,
making pagination metadata (`total`, `has_more`) wrong for over-shot offsets.

**Decision.** Always run a dedicated `SELECT count() ... WHERE {where}` query for
`total`, rather than relying on `count() OVER ()` inline with the main result set.

**Consequences.** One extra query per `search_graph` call, but `total`/`has_more` are
correct for every offset, including past-the-end pagination. Documented here per the
brief's Step 4 instruction to note the fallback path's (non-)use even though its stated
trigger condition (a `count() OVER ()` + `FINAL` error) was never observed.

**Status:** accepted.

---

### (n) `SessionWorker` owns opening *and* closing the chdb `Session` on its own thread

**Class:** daemon-internal / concurrency-safety (INV-1).

**Context.** The plan's original daemon design opened the chdb `Session` in
`Daemon.__init__` on the main thread and passed the handle into the worker. chdb
`Session` thread-safety is an OPEN question (owned by `chdb-reference`/
`chgraph-architecture-contract`), so handing a main-thread-opened Session to a worker
thread risked violating whatever single-thread assumption chdb makes internally.

**Decision.** Refactor so the `SessionWorker` thread itself opens the `Session` at
startup and closes it at shutdown — the handle never crosses threads at creation time.
Errors opening the Session (e.g., the Code 36/76 lock-conflict pair) are surfaced back
to `run_daemon` via `worker.wait_ready()`, preserving the existing lock-conflict error
messaging.

**Consequences.** Correctly realizes INV-1 ("exactly one process, and now provably one
thread, opens the data dir") given the open thread-safety question, at the cost of one
extra layer of indirection (`wait_ready()`) between daemon startup and confirmed Session
readiness.

**Status:** accepted.

---

### (o) Test-only: short `CHGRAPH_DATA_DIR` to stay under the macOS `AF_UNIX` socket-path limit

**Class:** infra-dependency (test infrastructure only; also hardens production error
messaging).

**Context.** The plan's daemon test fixture pointed `CHGRAPH_DATA_DIR` at pytest's deep
`tmp_path` (nested several directories under the pytest run's temp root). macOS's
`sockaddr_un` path limit is ~104 bytes; `daemon.sock` under a deep `tmp_path` overflows
it, causing socket binds to fail in a way unrelated to the code under test.

**Decision.** The daemon test fixture now creates a short, unique directory directly
under `/tmp` for `CHGRAPH_DATA_DIR` instead of using the deep pytest `tmp_path`.
Separately, `run_daemon` now catches socket-bind `OSError` and emits a clear "path too
long?" hint rather than a bare traceback.

**Consequences.** Tests are decoupled from pytest's temp-dir nesting depth. Production
default (`~/.local/share/chgraph/<slug>/daemon.sock`) stays comfortably under the limit,
but a user who sets a deep custom `$CHGRAPH_DATA_DIR` could still hit this — that
residual risk is now at least surfaced with a clear error instead of a raw traceback,
per the improved `run_daemon` error handling, but is not otherwise mitigated (e.g., no
automatic shortening or relocation of the socket path).

**Status:** accepted.

---

### (p) `SessionWorker` has no per-operation timeout or cancellation

**Class:** architecture weak point (daemon-internal), deferred.

**Context.** `chgraph-architecture-contract` leaves chdb `Session` concurrency
semantics OPEN. The daemon's `SessionWorker` serializes all chdb operations through one
worker thread with no per-call timeout.

**Decision.** Ship v0.1 without per-op timeout/cancellation. The client-side unix-socket
call has a 120s timeout, which surfaces a hang to the *caller* but does not free the
worker — a truly hung chdb call (e.g., pathological query) wedges all subsequent DB
operations for every client, even though `ping`/`status` (handled without touching
the Session) still respond.

**Consequences.** A single pathological query can degrade the whole daemon for every
shim/client until the daemon is restarted; `daemon status` remains truthful throughout
(it doesn't touch the Session), so the failure mode is detectable, but not
self-healing.

**Status:** needs-review for v0.2 — tracked as a known residual risk, not a v0.1 defect,
since it matches the architecture contract's own stated OPEN item on Session
concurrency.

---

### (q) `shim.search_graph` explicitly rejects reference-only parameters

**Class:** tool-surface.

**Context.** The brief's Task 12 code sample for `shim.search_graph` omitted the
reference-contract-only parameters (`semantic_query`, `qn_pattern`, `file_pattern`,
`min_degree`, `max_degree`, `exclude_entry_points` — see item (l)) entirely from the
tool's signature. Simply omitting them from the schema would let a caller who reads the
reference tool's contract pass them and have chgraph silently ignore them — a "no
silent success" (N2) violation at the tool-surface layer.

**Decision.** `shim.search_graph` declares those reference-only parameters in its
signature, defaulted to `None`, and raises a clear error ("search_graph param(s) not
supported in v0.1") if any is set to a non-default value, rather than omitting them or
accepting-and-ignoring them.

**Consequences.** Callers get an explicit, actionable error instead of confusing silent
no-op behavior when they use a reference-contract parameter chgraph v0.1 doesn't yet
implement. This is a deliberate broadening of the brief's own code sample to satisfy the
global "never silently ignore" constraint.

**Status:** accepted.

---

### (r) `anyio` dependency — informational, not a deviation

**Class:** infra-dependency (informational only).

**Context.** Task 12's test setup calls for an `anyio_backend` fixture (present in
`tests/conftest.py`) to support `pytest-anyio`-style async tests against the MCP shim.

**Decision/observation.** No `uv add anyio` was necessary — `anyio` was already present
transitively via the `mcp` package's own dependency tree. Only the `anyio_backend`
fixture itself needed to be added, per the brief.

**Consequences.** None — no new direct dependency was introduced; this entry exists
purely so the dependency surface is documented, not because any deviation occurred.

**Status:** accepted (informational).

## Verification

- `uv run pytest tests/chdb/test_e2e_staleness.py -v` — 1 passed (2026-07-03): fresh
  (`src/api.py::handle`, score 0.5454) outranks stale (`src/core/legacy.py::old_thing`,
  score 0.4).
- `uv run pytest tests/ -v` — full suite green (see commit message / CI output for the
  exact count as of this ADR's date).
- Items (a), (k) were verified by the passing indexer/gitingest test suites (Tasks 4, 7)
  plus the new e2e test above, which depends on both TRUNCATE-then-reload paths
  executing correctly across a repeat `index_repository` call.
- Items (n), (o) were verified by the Task 10 daemon test suite (`tests/test_daemon.py`),
  including the lock-conflict and socket-path-length paths.
- Items (c), (p) are explicitly unverified/OPEN by design (calibration and load-testing
  are future work, not v0.1 claims).

## Rollback

Each item is independently reversible in its own file/commit:
- (a), (k): revert to version-bump-only reindex (reintroduces the ghost-row bug this ADR
  documents as the reason for the change).
- (b): remove `uninitialized` from the status enum (reintroduces the pre-first-index gap
  in `index_status`).
- (h), (i), (j), (m), (q): each is a small, isolated diff in a single function; revert
  the specific commit.
- (n): revert to main-thread Session open (reintroduces the open thread-safety question
  this ADR avoids).
- (d), (l), (p): not applicable — these record scope *not* built; "rollback" is simply
  not building the deferred surface, which is already the current state.

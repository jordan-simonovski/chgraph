---
name: chgraph-failure-archaeology
description: Use when asking "didn't we already try this?", proposing to re-test a settled question (chdb read-only multi-process access, HNSW/vector_similarity in chdb), closing out an investigation or dead end, writing a postmortem, or wondering why chgraph is daemon-only, brute-force-vector, or precision-over-breadth. Also when chdb version numbers look contradictory (4.2.0 vs 26.5.0), or when someone cites the reference tool's data-loss, silent-degradation, or 83%-vs-92% eval history.
---

# chgraph Failure Archaeology

The chronicle of investigations, dead ends, and settled battles — so nobody re-fights one.

**Greenfield honesty (as of 2026-07-03):** chgraph has zero lines of production code and therefore **zero native incidents**. Every entry below is labeled **inherited / pre-code**: it comes either from Phase-1 empirical verification of chdb (run locally, output pasted), or from the documented failure history of the reference project (DeusData/codebase-memory-mcp), which shaped chgraph's founding design. When chgraph's first native incident happens, it becomes FA-008.

**Evidence labels used throughout** (project-wide convention): **VERIFIED** = ran locally, output observed. **REPORTED** = from research, source URL given. **DECIDED** = design decision, rationale given. **OPEN** = unproven candidate.

## Rule zero: check the chronicle before investigating

Before starting any investigation, diagnosing a "weird" chdb behavior, or proposing to revisit a design constraint, search this file:

```bash
grep -n -i '<your symptom keyword>' /path/to/repo/.claude/skills/chgraph-failure-archaeology/SKILL.md
```

If a settled entry matches, do not re-run the investigation from scratch. Either accept the settled answer, or run that entry's **re-verification one-liner** (every settled entry has one) and — only if the output has changed — open a supersession per the protocol below.

## Entry format

Every entry has exactly these fields, in this order:

| Field | Meaning |
|---|---|
| **ID** | `FA-NNN`, monotonically increasing, never reused |
| **Title** | One line, symptom-first |
| **Origin** | `inherited / pre-code` or `native` + date |
| **Symptom** | What was observed, verbatim where possible (paste the actual error) |
| **Root cause** | Why it happens, to the depth actually established (say "not established" if so) |
| **Evidence** | Labeled VERIFIED/REPORTED with pasted output or source URLs. An entry with no evidence is not an entry |
| **Status** | `settled` (answered, stop re-testing), `open` (unresolved, revisit trigger named), or `superseded by FA-NNN` |
| **Design consequence** | What chgraph does differently because of this, and which sibling skill owns the living rule |
| **Re-verification** | The one command that re-tests the finding (settled entries only; required because "settled" facts about dependencies can drift on upgrades) |

## Protocol for adding entries

1. **When an investigation ends — success, failure, or abandonment — an entry is required before the branch merges.** This is enforced through chgraph-change-control: an investigation-closing PR without its FA entry does not pass that skill's gates. No exceptions for "it turned out to be nothing" — that IS the entry.
2. **Dead ends get recorded WITH their evidence.** "We tried X and it didn't work" is worthless without the exact command, version, and output that showed it didn't work. Future readers must be able to re-run the failure.
3. **Never delete or edit the substance of an old entry.** If new evidence overturns one, add a new entry and mark the old one `superseded by FA-NNN`. The wrong turn is part of the record.
4. **One home per fact.** The entry records the historical finding and its design consequence; the *living* operational rule lives in the sibling skill named in the entry. Don't grow runbooks here.
5. Any entry whose design consequence would change schema, retrieval behavior, or the MCP tool surface points through **chgraph-change-control** — this chronicle records decisions, it never authorizes them.

---

## The chronicle

### FA-001 — chdb read-only mode does NOT allow a second process on the same data dir

- **Origin:** inherited / pre-code (Phase-1, re-verified 2026-07-03 on chdb 26.5.0 engine / pip dist 4.2.0)
- **Symptom:** Second process opening a chdb session dir — **including with `?mode=ro`** — fails immediately. Engine prints to stderr:
  ```
  Code: 76. DB::Exception: Cannot lock file <dir>/status. Another server instance in same directory is already running. (CANNOT_OPEN_FILE)
  ```
  and the Python caller sees:
  ```
  RuntimeError: Failed to create connection: Code: 36. DB::Exception: Error initializing EmbeddedServer: . (BAD_ARGUMENTS)
  ```
  Note the trap: the Python exception (Code 36) carries an *empty* detail; the real reason (Code 76) only appears on stderr.
- **Root cause:** chdb embeds a full ClickHouse server, which takes an exclusive lock on `<dir>/status` at init. Read-only mode does not skip lock acquisition.
- **Evidence:** VERIFIED — two-process test executed 2026-07-03 (snippet below), second process exit code 1 with exactly the output above. Phase-1 had verified the same on the earlier 4.2.0-scheme build.
- **Status:** settled.
- **Design consequence:** DECIDED — single daemon process owns each chdb data directory; MCP stdio shims connect to it over a local unix socket. This is chgraph's single biggest architectural constraint. Architecture: see **chgraph-architecture-contract**. Operating/recovering the daemon and diagnosing this error in the field: see **chgraph-run-and-operate**.
- **Re-verification** (do NOT argue "maybe ro works now" without running this on the new chdb; expected on 26.5.0: exit code 1 + the Code 76 line):
  ```bash
  python - <<'EOF'
  # Re-verify FA-001: does a second process (even read-only) still hit the chdb dir lock?
  import subprocess, sys, tempfile, time
  d = tempfile.mkdtemp(prefix="chdb-locktest-")
  holder = subprocess.Popen([sys.executable, "-c",
      f"import chdb.session, time; s = chdb.session.Session({d!r}); time.sleep(20)"])
  time.sleep(6)  # let the holder finish engine init and take the lock
  r = subprocess.run([sys.executable, "-c",
      f"import chdb.session; chdb.session.Session({d!r} + '?mode=ro').query('SELECT 1')"],
      capture_output=True, text=True)
  holder.terminate(); holder.wait()
  print("second process exit code:", r.returncode)
  print(r.stderr.splitlines()[0] if r.stderr else "NO ERROR — lock behavior has CHANGED, update this entry")
  EOF
  ```
  (Run with a Python 3.12 venv that has chdb installed — the repo's `.venv/bin/python` once **chgraph-build-and-env** setup is done. System python3 is 3.9.6 and too old.)
  Observed 2026-07-03:
  ```
  second process exit code: 1
  Code: 76. DB::Exception: Cannot lock file /var/folders/.../chdb-locktest-p5ztgd78/status. Another server instance in same directory is already running. (CANNOT_OPEN_FILE)
  ```

### FA-002 — HNSW vector index (`vector_similarity`) is compiled out of chdb

- **Origin:** inherited / pre-code (verified 2026-07-03 on chdb 26.5.0)
- **Symptom:** Creating a table with `INDEX v e TYPE vector_similarity(...)` fails even though the index is GA in ClickHouse server 25.8+:
  ```
  Code: 80. DB::Exception: Unknown Index type 'vector_similarity'. Available index types: hypothesis, text, bloom_filter, sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax: When validating secondary index `v`. (INCORRECT_QUERY)
  ```
  The `allow_experimental_vector_similarity_index=1` setting does not change the outcome (also VERIFIED).
- **Root cause:** The chdb binary is built without the vector-similarity index code. Server-vs-chdb build divergence; GA status of the feature refers to the server (REPORTED: https://github.com/ClickHouse/ClickHouse/pull/85888).
- **Evidence:** VERIFIED — one-liner below executed 2026-07-03, error pasted above.
- **Status:** **open** upstream (settled for chdb ≤ 26.5.0). Revisit trigger: every chdb upgrade — run the one-liner before touching embedding code. Upstream tracking: check https://github.com/chdb-io/chdb/issues for a vector_similarity build request before filing a new one (OPEN — no chgraph-filed issue exists yet).
- **Design consequence:** DECIDED — embeddings use brute-force `cosineDistance` over `Array(Float32)` columns; adequate at codebase scale (10⁴–10⁵ vectors). Schema and query patterns: see **chdb-reference** and **chgraph-architecture-contract**. If a future chdb ships the index, switching is a retrieval-behavior change → **chgraph-change-control**.
- **Re-verification:**
  ```bash
  python -c "import chdb; chdb.query(\"CREATE TABLE t (id UInt64, e Array(Float32), INDEX v e TYPE vector_similarity('hnsw','cosineDistance',8)) ENGINE=MergeTree ORDER BY id\")"
  # chdb 26.5.0: RuntimeError Code: 80 "Unknown Index type 'vector_similarity'". If it CREATES, reopen this entry.
  ```

### FA-003 — chdb version numbers are three-layered; "4.2.0" and "26.5.0" are the same install

- **Origin:** inherited / pre-code (verified 2026-07-03)
- **Symptom:** Phase-1 research reports cite "chdb v4.2.0"; the imported module reports `26.5.0`. These look contradictory and have already caused confusion once.
- **Root cause:** `pip install chdb` installs **two** distributions: the wrapper `chdb` (dist-info `chdb-4.2.0`, and PyPI's latest `info.version` is `4.2.0`) plus `chdb-core` (dist-info `chdb_core-26.5.0`, versioned to track the engine). `chdb.__version__` reports the *core* version `26.5.0`; `chdb.engine_version` reports the wrapped ClickHouse `26.5.1.1`. Official docs lag further, still describing 25.8.
- **Evidence:** VERIFIED 2026-07-03 —
  ```
  $ python -c "import chdb; print(chdb.__version__, chdb.engine_version)"
  26.5.0 26.5.1.1
  $ ls .venv/lib/python3.12/site-packages | grep -E '^chdb.*dist-info'
  chdb-4.2.0.dist-info
  chdb_core-26.5.0.dist-info
  ```
  PyPI latest also VERIFIED: `curl -s https://pypi.org/pypi/chdb/json` → `info.version` = `4.2.0`.
- **Status:** settled.
- **Design consequence:** DECIDED — always pin the pip requirement (`chdb==4.2.0` as of 2026-07-03) AND state all three numbers (wrapper / core / engine) in any bug report, benchmark, or doc. Any FA entry or benchmark that says only "chdb X.Y" without the layer name is underspecified. Pinning mechanics: see **chgraph-build-and-env**.
- **Re-verification:**
  ```bash
  python -c "import chdb; print(chdb.__version__, chdb.engine_version)"   # core + engine of the installed build
  curl -s https://pypi.org/pypi/chdb/json | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"   # latest wrapper on PyPI
  ```

### FA-004 — Reference tool deleted project databases on corruption detection (data loss)

- **Origin:** inherited / pre-code (reference project codebase-memory-mcp, v0.8.1)
- **Symptom:** Users' indexed project DBs were silently deleted when the tool's corruption detection triggered — hours of indexing gone, plus any agent-written ADR knowledge stored in the DB.
- **Root cause (as reported):** Corruption *detection* was wired to destructive cleanup with no backup step.
- **Evidence:** REPORTED — https://github.com/DeusData/codebase-memory-mcp/issues/557 (issue live as of 2026-07-03; HTTP 200 spot-checked).
- **Status:** settled (as a lesson for chgraph; upstream fix status is the reference project's business).
- **Design consequence:** DECIDED — chgraph has a **backup-before-migration gate**: no schema migration, corruption-recovery path, or destructive maintenance operation may run without first producing a verified backup of the data directory. The gate itself is owned and enforced by **chgraph-change-control**; backup mechanics by **chgraph-run-and-operate**. Never implement "detect corruption → delete" logic; implement "detect corruption → quarantine (rename aside) → report degraded via index_status".

### FA-005 — Reference tool: silent index degradation and never-finishing indexes on large repos

- **Origin:** inherited / pre-code (reference project)
- **Symptom:** (a) A 72k-LOC Rust repo indexed to only ~500 nodes yet reported status "indexed" — silently useless graph. (b) Indexing never finishes on large Python projects. (c) Index failures on large repos generally. Upstream reacted by adding a `CBM_DUMP_VERIFY_MIN_RATIO` knob that flips status to "degraded" when persisted node count falls below a fraction of in-memory count.
- **Root cause (as reported):** Persistence/pipeline failures on large inputs were not surfaced in tool status; success was claimed on partial output.
- **Evidence:** REPORTED — https://github.com/DeusData/codebase-memory-mcp/issues/333 (silent degradation), https://github.com/DeusData/codebase-memory-mcp/issues/524 (never-finishing), https://github.com/DeusData/codebase-memory-mcp/issues/563 (large-repo failures). All live 2026-07-03.
- **Status:** settled (as design input).
- **Design consequence:** DECIDED — the **status-honesty invariant**: chgraph's async indexing must never report success it cannot prove. `index_status` exposes an explicit `degraded` state, and index-sanity ratios (nodes-per-KLOC, orphan ratio, etc.) gate what "indexed" may claim. This is a founding differentiator, not a nice-to-have. The concrete ratios and their thresholds are owned by **chgraph-validation-and-qa**; the index_status tool contract by **mcp-server-reference**.

### FA-006 — Reference tool: call-edge precision bugs across languages

- **Origin:** inherited / pre-code (reference project, which parses 158 languages)
- **Symptom:** False-positive PHP CALLS edges to same-named project methods; TypeScript path aliases (`@packages/shared`) unresolved so edges are missing; C++ out-of-line method definitions attach CALLS edges to the file-level Module instead of the enclosing Method; trace_path returns empty despite CALLS edges existing.
- **Root cause (as reported):** Breadth-first language support with heuristic ("Hybrid LSP") type resolution produces both false positives and false negatives; only ~19 of 158 languages are benchmarked "Excellent" by the project's own README tiers.
- **Evidence:** REPORTED — https://github.com/DeusData/codebase-memory-mcp/issues/606 (PHP false positives), https://github.com/DeusData/codebase-memory-mcp/issues/730 (TS path aliases), https://github.com/DeusData/codebase-memory-mcp/issues/554 (C++ mis-attachment), https://github.com/DeusData/codebase-memory-mcp/issues/480 (empty trace_path). All live 2026-07-03.
- **Status:** settled (as design input).
- **Design consequence:** DECIDED — **precision over breadth**: chgraph ships ~10 top languages via py-tree-sitter, not 158. An edge chgraph asserts should be an edge an agent can trust; language breadth is explicitly an anti-axis (the reference tool already wins it). Parser architecture: see **chgraph-architecture-contract**; per-language precision acceptance: **chgraph-validation-and-qa**.

### FA-007 — Graph-first retrieval currently LOSES to plain file exploration on answer quality (83% vs 92%)

- **Origin:** inherited / pre-code (reference project's own evaluation)
- **Symptom:** The reference project's self-reported eval across 31 repositories: graph-first retrieval scores **83%** answer quality vs **92%** for a plain file-exploring agent — at 10x fewer tokens and 2.1x fewer tool calls. Graph retrieval today trades accuracy for cost; it is not simply "better".
- **Root cause:** Open research question. Candidate explanation (OPEN, untested): graph results lack the ranking signals (recency, ownership, churn) needed to prefer live code over stale code — the failure mode chgraph's git-evolution campaign attacks.
- **Evidence:** REPORTED — arXiv preprint https://arxiv.org/abs/2603.27277 (self-reported by the reference project; treat the absolute numbers with caution, the *direction* of the gap is the lesson).
- **Status:** **open** — this is the whole campaign thesis, and it is unproven in both directions for chgraph.
- **Design consequence:** DECIDED — **nobody may claim chgraph "improves retrieval" (in code comments, README, blog posts, or anywhere) without numbers from the eval harness** defined in **chgraph-validation-and-qa**. The hybrid-ranking attempt to close the gap is the git-evolution campaign: see **chgraph-git-evolution-campaign**. External-claim discipline: **chgraph-research-frontier**.

---

## When NOT to use this

- **Diagnosing a live problem right now** (daemon won't start, query hangs, index stuck): use **chgraph-debugging-playbook**; come back here only to check whether the battle is already settled, and to file the entry when your investigation closes.
- **Operating the daemon / recovering from the lock error in the field**: **chgraph-run-and-operate** owns the runbook; FA-001 only records why the architecture is what it is.
- **chdb API facts, SQL patterns, sharp edges as a reference**: **chdb-reference**.
- **Deciding whether a change is allowed / running the gates** (including the backup-before-migration gate and the merge-time entry requirement): **chgraph-change-control** owns enforcement; this skill only defines what an entry looks like.
- **Proving a retrieval or quality claim**: **chgraph-validation-and-qa** (FA-007 tells you why you must).
- **Choosing what to research next or drafting competitor comparisons**: **chgraph-research-frontier**.

## Provenance and maintenance

**Grounding.** Written 2026-07-03 against an empty repo (zero commits — every entry is inherited/pre-code by construction). VERIFIED entries (FA-001, FA-002, FA-003) were executed by the author on 2026-07-03 on macOS arm64, Python 3.12, chdb pip dist 4.2.0 / core 26.5.0 / engine 26.5.1.1; all pasted output is real observed output, not projected. REPORTED entries (FA-004..FA-007) come from Phase-1 research into DeusData/codebase-memory-mcp; every issue URL was spot-checked HTTP 200 on 2026-07-03.

**Drift re-verification one-liners** (run in a venv with chdb installed):

| What may drift | Command | Expected as of 2026-07-03 |
|---|---|---|
| chdb versions (all 3 layers) | `python -c "import chdb; print(chdb.__version__, chdb.engine_version)"` + `curl -s https://pypi.org/pypi/chdb/json \| python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"` | `26.5.0 26.5.1.1` / `4.2.0` |
| Dir-lock behavior (FA-001) | The FA-001 snippet above | exit 1 + `Code: 76 ... Cannot lock file .../status` |
| vector_similarity availability (FA-002) | The FA-002 one-liner above | `Code: 80 ... Unknown Index type 'vector_similarity'` |
| Reference tool surface (context for FA-004..007) | `curl -s https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c \| grep -oE '^    \{"[a-z_]+"' \| tr -d ' {"'` | 14 names, index_repository … ingest_traces |
| Reference issue links still resolve | `for n in 557 333 524 563 606 730 554 480; do curl -s -o /dev/null -w "#$n %{http_code}\n" https://github.com/DeusData/codebase-memory-mcp/issues/$n; done` | all `200` |

**Maintenance duties.** On every chdb upgrade: re-run FA-001 and FA-002 re-verifications and update their entries (supersede, don't edit) if behavior changed. On chgraph's first native incident: open FA-008 following the entry format, and route its design consequence through chgraph-change-control.

---
name: chgraph-validation-and-qa
description: Use when judging whether a chgraph change is actually proven — claims like "retrieval looks better" or "seems faster", adding or running pytest tests, defining or updating golden question/answer pairs, designing or running the retrieval eval harness, checking answer-quality or token-budget numbers before a merge or campaign promotion, or setting index-sanity gates (nodes-per-KLOC, orphan ratio). Keywords - evidence, eval, golden set, acceptance threshold, regression, QA, test layout, baselines.
---

# chgraph validation and QA: what counts as evidence

This skill defines the evidence bar for chgraph: what a claim must be backed by, the design of the retrieval eval harness, the golden inventory protocol, acceptance thresholds for merging, index-sanity gates, and pytest conventions. The git-evolution campaign's promotion protocol (see `chgraph-git-evolution-campaign`) routes through the bar defined here.

**Status caveat (2026-07-03):** the repo has no code yet. Everything marked DECIDED/OPEN below is founding doctrine, not a description of existing artifacts. Everything marked VERIFIED was actually executed on 2026-07-03 against chdb 26.5.0 (python 3.12, macOS arm64) — real output is pasted where shown.

## 1. The evidence hierarchy (DECIDED)

When two sources of evidence disagree, the higher one wins. When you make a claim, state which rung it sits on.

| Rank | Evidence type | What it proves | Example |
|---|---|---|---|
| 1 | **Eval-harness number** | End-to-end retrieval quality and cost, vs baselines | "Answer quality 89% at 41k tokens/question, run `eval-2026-07-15-a`" |
| 2 | **Diagnostic-script number** | A measurable property of a real index | "nodes-per-KLOC = 62 on corpus repo X at SHA abc123" |
| 3 | **Unit/integration test** | A component behaves as specified in isolation | The schema round-trip test in §7 |
| 4 | **Anecdote** | Almost nothing; at best, a lead worth converting upward | "I asked it about the parser and the answer seemed good" |

Hard rules (DECIDED):

- **"Looks right" is not evidence.** An anecdote may motivate work; it may never justify shipping a retrieval-affecting change or claiming a capability.
- A number without its provenance (corpus SHA, harness version, judge version, run ID) is an anecdote wearing a costume. Record all four.
- Negative results are evidence too and get recorded the same way. A wrong runbook is worse than none; the same holds for a wrong benchmark claim.
- This hierarchy mirrors the project's four evidence labels (VERIFIED / REPORTED / DECIDED / OPEN — defined in `chgraph-architecture-contract`): rungs 1–3 produce VERIFIED facts; rung 4 never does.

Why this matters concretely: the reference tool (DeusData/codebase-memory-mcp) shipped silent index degradation — a 72k-LOC repo indexed to ~500 nodes with status "indexed" (REPORTED, issue #333, https://github.com/DeusData/codebase-memory-mcp). "It indexed and answers look plausible" was the evidence standard that let that through.

## 2. The retrieval eval harness (design — nothing built yet)

**The falsifiable target (DECIDED):** chgraph must **match or beat 92% answer quality at ≤1/5 the tokens of plain file exploration** on the golden set. Baseline for calibration: the reference tool self-reports **83% answer quality at ~10x fewer tokens** (and 2.1x fewer tool calls) across 31 repos, vs **92% for a plain file-exploring agent** (REPORTED, arXiv preprint https://arxiv.org/abs/2603.27277 — self-reported by the reference project, not independently replicated). If chgraph cannot beat 83% quality, the graph-first premise is failing and the campaign does not promote.

### Harness anatomy (DECIDED unless marked OPEN)

| Component | Design |
|---|---|
| **Corpus** | A small set of real public repos, each **pinned to a commit SHA** recorded in the golden files. OPEN: which repos; selection criteria are DECIDED — cover the ~10 launch languages, include at least one repo >50k LOC (to exercise the reference tool's documented weak zone), and at least one with rich multi-year git history (the evolution campaign needs it). |
| **Golden question set** | Question/answer pairs over the pinned corpus (protocol in §4). Categories (DECIDED): symbol lookup, caller/callee tracing, impact ("what breaks if I change X"), architectural overview, cross-file data flow, and evolution questions (churn/ownership/staleness — chgraph-unique, the reference tool cannot answer these; they are scored for chgraph but excluded from head-to-head quality comparisons to keep those fair). |
| **Conditions compared** | (A) plain file exploration — same agent scaffold, no MCP graph tools; (B) reference tool codebase-memory-mcp; (C) chgraph. Same model, same scaffold, same questions, same corpus SHA for all three. Any run missing a condition says so in its report. |
| **Answer-quality scoring** | LLM-as-judge against the golden answer's required key points, rubric versioned in-repo; a fixed human-audited subset (≥20% of questions) checks judge agreement each time the judge model or rubric changes. OPEN: judge model choice and rubric wording. DECIDED: judge model ID + rubric version are recorded in every run report — a score is not comparable across judge versions. |
| **Token accounting** | Per question, count **all** tokens through the agent loop: prompts, tool results, and completions, summed over every turn until answer. Tool-result tokens count fully — that is where graph retrieval saves or spends. Report per-question and aggregate. OPEN: exact tokenizer/counting mechanism (candidate: the provider's usage fields from the agent scaffold). |
| **Noise band** | Before any threshold is enforced, run each condition N≥3 on the same commit to measure run-to-run variance; the non-regression band in §3 is set from that measurement. OPEN until measured. |
| **Run artifact** | Every run emits a report (run ID, date, corpus SHAs, golden-set version, judge version, per-condition quality %, token totals, failures listed) checked into the repo. Numbers that exist only in a terminal scrollback do not exist. |

## 3. Acceptance thresholds discipline (DECIDED)

`chgraph-change-control` classifies changes; this section defines what each class must show. Nothing routes around change-control — if you are unsure whether a change is retrieval-affecting, that classification question belongs there, not here.

| Change class (per chgraph-change-control) | Required evidence to merge |
|---|---|
| **Retrieval-affecting** (schema, ranking, traversal, indexing semantics, tool-surface behavior) | A full eval-harness run on the change, **non-regressing** vs the last recorded baseline on both answer quality and token budget (within the measured noise band), **and the numbers pasted in the PR description** with run ID. No number in the PR = not mergeable. |
| **Index-integrity-affecting** (parser, ingestion, dedup) | Index-sanity gates (§5) pass on the corpus repos + relevant pytest suites green. |
| **Everything else** | Relevant pytest suites green. |

Additional rules:

- **Regressions ship only as explicit trades**, stated in the PR ("quality −1.2pt for −38% tokens, accepted because …") and approved through change-control. Silent regressions are the failure mode this whole skill exists to prevent.
- Until the harness exists, retrieval-affecting changes carry an honest "**eval: not yet run — harness not built**" line in the PR. Do not fake the ritual; do note the debt. Building the harness (§6) is therefore on the critical path of the first retrieval-affecting change.

## 4. The golden inventory protocol (DECIDED)

A "golden" is a question/answer pair the harness treats as ground truth. Goldens live in the repo (planned location: `evals/goldens/`, versioned files; format OPEN — candidate: one YAML file per category).

**Each golden records:** stable ID, question text, corpus repo + pinned SHA, required answer key points (what the judge checks), category, provenance (who added it, why, date), and golden-set version it entered at.

**How a pair becomes golden:**

1. It originates from either a designed capability (coverage of a tool/category) or a real observed failure (the best goldens are regression traps — see `chgraph-failure-archaeology` for where failures are recorded).
2. A human verifies the answer key points against the pinned corpus by hand. LLM-drafted goldens are fine; LLM-verified goldens are not.
3. It enters via PR review like code. The PR states the category balance impact.

**When goldens may change — never silently:**

- Editing, relaxing, or deleting a golden is a retrieval-affecting change (it moves the bar) and goes through `chgraph-change-control` with rationale in the PR.
- **Banned:** editing a golden to make a failing run pass, in the same PR as the change that failed it. If the golden is genuinely wrong, fix it in its own PR with independent justification.
- Bumping a corpus SHA invalidates every golden on that repo until each is re-verified against the new SHA; the golden-set version increments and run reports across versions are not directly comparable.

## 5. Index-sanity gates (candidates — thresholds OPEN)

Cheap invariants checked after every index run, designed to catch silent degradation (the reference tool's #333 failure class) before any eval spends money. The **scripts** that compute these are owned by `chgraph-diagnostics-and-tooling`; this skill owns their status as gates and the thresholds once measured.

| Gate (candidate) | Signal | Threshold |
|---|---|---|
| **nodes-per-KLOC** | Parsed symbol density per language; a collapse (e.g. 72k LOC → ~500 nodes) means the parser silently failed | OPEN — measure per-language bands on the corpus repos first; hardcode nothing before measurement |
| **zero-orphan-ratio** | Fraction of non-File nodes with zero edges; a spike means edge resolution broke while node extraction kept going | OPEN — same: measure, then gate |
| **file coverage** | Indexed-file count vs files the language matcher selected | OPEN |

DECIDED: when a gate fails, `index_status` must surface **degraded**, not "indexed" — the surfacing mechanics belong to `chgraph-architecture-contract` (decision 7); the gate definition and threshold live here.

## 6. First three steps to build the harness (DECIDED sequence)

1. **Pin the corpus and write the starter golden set.** Pick the corpus repos (criteria in §2), pin SHAs, author ~20 goldens (2–4 per category), human-verify each per §4. No code needed — this is authorship plus review.
2. **Build the condition-A runner first** (plain file exploration + token accounting + judge scoring) and run it N≥3 to establish the 92%-class baseline and the noise band **on our goldens** — the arXiv numbers are REPORTED calibration, not our baseline. This runner is useful before a single line of chgraph exists.
3. **Add condition B (reference tool), then C (chgraph) behind the same interface**, and check the first full comparison report into the repo. Only after that report exists do §3's thresholds start being enforced mechanically.

## 7. Test conventions (pytest)

DECIDED layout (greenfield):

```
tests/
  unit/          # pure-Python, no chdb import
  chdb/          # needs a chdb Session; every test uses tmp_path data dirs
  eval/          # harness code's own tests (NOT the eval runs themselves)
conftest.py      # shared fixtures, incl. the session fixture below
```

Rules (DECIDED):

- Runner is pytest, executed from the project venv (`.venv/bin/python -m pytest tests/` — see `chgraph-build-and-env` for the venv itself; system python3 is 3.9.6 and will not work).
- Every chdb test gets a **fresh `tmp_path` data dir** and **closes its Session**. chdb allows only one active Session per process (REPORTED, chdb troubleshooting docs; the fixture-close pattern below is VERIFIED to let sequential tests in one process each open a session) — never share a data dir or a Session across tests, and never point a test at a real project data dir (the exclusive lock, owned by `chdb-reference`, will bite).
- Eval runs are not part of the default test pass; they run on demand and on retrieval-affecting PRs (§3).

### Runnable example: schema round-trip (VERIFIED 2026-07-03, chdb 26.5.0, pytest 9.1.1)

`tests/chdb/test_schema_roundtrip.py` — exercises the DECIDED nodes-table shape (`chgraph-architecture-contract` decision 5: `ReplacingMergeTree(version)` keyed on `(project, qualified_name)` — `version` is the replacing column, NOT in the sort key — batch per-file replace), plus a counter-example test pinning why the key must exclude `version`:

```python
"""Schema round-trip: prove the DECIDED nodes-table shape survives a chdb write/read cycle."""
import chdb.session as chs
import pytest


@pytest.fixture
def db(tmp_path):
    # chdb allows only ONE active Session per process.
    # A function-scoped fixture that closes the session keeps tests isolated.
    sess = chs.Session(str(tmp_path / "data"))
    yield sess
    sess.close()


def test_nodes_roundtrip_and_replace(db):
    db.query("""
        CREATE TABLE nodes (
            project        String,
            label          LowCardinality(String),
            name           String,
            qualified_name String,
            file_path      String,
            start_line     UInt32,
            end_line       UInt32,
            version        UInt64
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY (project, qualified_name)
    """)
    # Batch insert: index run v1 sees two symbols in one file.
    db.query("""
        INSERT INTO nodes VALUES
        ('demo', 'Function', 'parse', 'pkg.mod.parse', 'pkg/mod.py', 10, 42, 1),
        ('demo', 'Function', 'emit',  'pkg.mod.emit',  'pkg/mod.py', 44, 60, 1)
    """)
    # Re-index run v2 replaces the whole file's rows (batch per-file replace).
    db.query("""
        INSERT INTO nodes VALUES
        ('demo', 'Function', 'parse', 'pkg.mod.parse', 'pkg/mod.py', 12, 50, 2)
    """)
    out = db.query(
        "SELECT qualified_name, start_line, version FROM nodes FINAL "
        "WHERE qualified_name = 'pkg.mod.parse'",
        "CSV",
    )
    # FINAL deduplicates on the sort key and keeps the highest version.
    assert out.data().strip() == '"pkg.mod.parse",12,2'


def test_final_dedups_on_correct_key(db):
    db.query("""
        CREATE TABLE nodes (
            project String, qualified_name String, version UInt64
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY (project, qualified_name)
    """)
    db.query("INSERT INTO nodes VALUES ('demo', 'a.b.c', 1), ('demo', 'a.b.c', 2)")
    n = db.query("SELECT count() FROM nodes FINAL", "CSV").data().strip()
    assert n == "1"


def test_version_in_key_breaks_dedup(db):
    # COUNTER-EXAMPLE (a rejected schema variant, kept as a regression trap):
    # with `version` inside the ORDER BY key, every version is a distinct
    # sort-key value, so FINAL does NOT collapse the two rows.
    db.query("""
        CREATE TABLE nodes (
            project String, qualified_name String, version UInt64
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY (project, qualified_name, version)
    """)
    db.query("INSERT INTO nodes VALUES ('demo', 'a.b.c', 1), ('demo', 'a.b.c', 2)")
    n = db.query("SELECT count() FROM nodes FINAL", "CSV").data().strip()
    assert n == "2"
```

Observed output (real run, 2026-07-03, throwaway lab venv):

```
$ python -m pytest tests/ -v
============================= test session starts ==============================
platform darwin -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0 -- .../bin/python
collected 3 items

tests/test_schema_roundtrip.py::test_nodes_roundtrip_and_replace PASSED  [ 33%]
tests/test_schema_roundtrip.py::test_final_dedups_on_correct_key PASSED  [ 66%]
tests/test_schema_roundtrip.py::test_version_in_key_breaks_dedup PASSED  [100%]

============================== 3 passed in 0.11s ===============================
```

**VERIFIED trap the third test documents:** with `version` inside the ORDER BY key, ReplacingMergeTree's `FINAL` does **not** deduplicate versions — both rows survive. An early draft of the schema decision was worded that way; the counter-example test stays as the regression trap that keeps the mistake from coming back. The locked key (decision 5, `chgraph-architecture-contract`) excludes `version` from the sort key for exactly this reason. If the schema key ever changes, that is a `chgraph-change-control` matter, and the tests above change with it — in the same gated PR.

## When NOT to use this

- **Classifying whether a change is retrieval-affecting, or approving a threshold trade** → `chgraph-change-control` (it classifies and gates; this skill only supplies the evidence bar it enforces).
- **A failing or weird index right now** → `chgraph-debugging-playbook`; **writing/running the sanity-metric scripts** → `chgraph-diagnostics-and-tooling`.
- **Campaign promotion mechanics and evolution-signal design** → `chgraph-git-evolution-campaign` (it routes through §3's bar but owns its own protocol).
- **chdb SQL semantics, lock behavior, engine quirks** → `chdb-reference`. **Venv/setup to run tests at all** → `chgraph-build-and-env`.
- **Recording a postmortem of a failure** → `chgraph-failure-archaeology` (then convert it into a golden here).

## Provenance and maintenance

Grounded 2026-07-03 by: running the §7 pytest example live against chdb 26.5.0 (python 3.12, pytest 9.1.1, macOS arm64) with the pasted output; reading the Phase-1 research corpus for the reference tool's eval claims (arXiv:2603.27277, confirmed reachable HTTP 200 on 2026-07-03) and issue history (#333 et al.). The harness itself (§2, §6) is a greenfield design — no part of it exists or has been run.

Re-verify when things drift (run from the project `.venv` once it exists; all were executed 2026-07-03 in an equivalent chdb 26.5.0 venv):

| What may drift | One-liner |
|---|---|
| chdb version (pin: 26.5.0 as of 2026-07-03) | `.venv/bin/python -c "import chdb; print(chdb.__version__)"` |
| Schema round-trip + FINAL-non-dedup behavior | `.venv/bin/python -m pytest tests/chdb/test_schema_roundtrip.py -v` |
| Reference eval claim source still up | `curl -sI https://arxiv.org/abs/2603.27277 \| head -1` (got `HTTP/2 200`) |
| Reference tool's baseline numbers (they re-benchmark) | re-read the arXiv abstract + repo README at https://github.com/DeusData/codebase-memory-mcp before quoting 83%/92% |
| Noise band / sanity thresholds (currently OPEN) | once measured, record values + measurement run ID here and flip labels to VERIFIED |

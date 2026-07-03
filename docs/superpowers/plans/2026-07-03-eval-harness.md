# Implementation plan: retrieval eval harness

Status: PLAN (2026-07-03). Authority for the *what*: `chgraph-validation-and-qa`
§2/§6 (DECIDED design + build sequence). This doc concretizes the *how* after two
decisions: scaffold = **Claude Agent SDK (headless)**, judge = **Opus**.

## 1. Goal & falsifiable target

Match or beat **92% answer quality at ≤1/5 the tokens** of plain file exploration on
our golden set. Reference tool (`codebase-memory-mcp`) self-reports 83% at ~10× fewer
tokens (REPORTED, arXiv:2603.27277 — calibration only, not our baseline). If chgraph
can't beat 83% on our goldens, the graph-first premise is failing.

The reference project is **not cloned locally**, so "base on the reference's harness"
means its documented methodology (3 conditions, LLM-as-judge, token accounting), not
a code port. Condition B still stands up their actual MCP server so the head-to-head is
real.

## 2. Architecture

One agent scaffold, three conditions that differ *only* in which tools the agent may call:

| Cond | Tools available to the agent | Purpose |
|---|---|---|
| **A** | File-read only (Read/Glob/Grep), no MCP | the 92%-class baseline |
| **B** | File tools + `codebase-memory-mcp` MCP server | the thing we must beat |
| **C** | File tools + `chgraph` MCP server (from `.mcp.json`) | us |

Same model, same system prompt, same questions, same corpus checkout at the same SHA
for all three. chgraph's condition-C surface already exists: `search_graph`,
`get_code_snippet`, `trace_path`, `get_graph_schema`, etc. (MCP tools in `shim.py`,
backed by daemon RPC ops).

The scaffold is the Claude Agent SDK run headless per question. MCP servers wire in
natively; per-message `usage` fields give token accounting for free.

### Layout

```
src/chgraph/eval/          # harness CODE (installed with venv → SDK dep available, importable)
  agent.py                 # SDK wrapper: run_question(q, condition, checkout, model) -> AnswerResult
  conditions.py            # A/B/C → SDK options (allowed tools + mcp_servers)
  judge.py                 # Opus LLM-as-judge vs golden key points -> Verdict
  tokens.py                # sum usage across turns; record raw (incl. cache-read) + computed totals
  goldens.py               # load + validate golden YAML
  report.py                # emit run artifact (JSON + markdown)
  __main__.py              # `python -m chgraph.eval --condition C --goldens evals/goldens --out evals/runs`
evals/                     # DATA + artifacts (checked in)
  corpus.yaml              # repo -> {sha, language, loc}
  rubric.md                # judge rubric, versioned; ID referenced in every run
  goldens/                 # one YAML per category (§4)
  runs/                    # run-<date>-<letter>.{json,md} artifacts
tests/eval/                # unit tests for the harness code itself (mock SDK/judge, no live calls)
```

Rationale: code under `src/chgraph/eval/` rides the existing package + venv (no PYTHONPATH
fiddling, SDK dep resolves); data/artifacts under `evals/` per the skill.

### Core interface

```python
# agent.py
def run_question(question: str, condition: Condition, checkout: Path, model: str) -> AnswerResult:
    """One headless Agent SDK run. Returns answer text + full token/tool accounting."""

@dataclass
class AnswerResult:
    answer: str
    tokens_total: int          # input+output summed over every turn (tool results = next-turn input)
    tokens_raw: dict           # per-turn usage incl. cache-read/cache-write, for auditing
    tool_calls: int
    condition: str
    corpus_sha: str
```

Conditions collapse to SDK option differences (allowed tools + `mcp_servers`), nothing else.

### Golden YAML (proposed concrete schema — format was OPEN)

```yaml
- id: symlookup-001
  question: "Where is the daemon's per-file replace logic and what engine backs it?"
  repo: chgraph            # key into corpus.yaml
  category: symbol_lookup  # symbol_lookup|caller_callee|impact|architecture|data_flow|evolution
  key_points:              # what the judge checks; human-verified against the pinned SHA
    - "ReplacingMergeTree(version) on nodes"
    - "batch per-file replace in the indexer"
  provenance: {by: jordan, why: "tool coverage: search+snippet", date: 2026-07-03}
  golden_set_version: 1
```

`evolution` category is chgraph-only → scored for us, excluded from A/B/C head-to-head.

## 3. Build sequence (concretizes §6)

**Step 0 — prereqs (authorship + one gated dep bump).**
- Add Agent SDK + Anthropic SDK to a `dev`/`eval` optional group in `pyproject.toml`.
  Dependency bump → routes through `chgraph-change-control`.
- Pick corpus repos, pin SHAs → `evals/corpus.yaml`. **Constraint:** only `parse_python.py`
  exists, so condition C can only answer Python-repo questions today — the head-to-head is
  **Python-only** until more parsers land. Corpus for step 1/2 must be Python; include ≥1
  repo >50k LOC (reference tool's documented weak zone) and ≥1 with rich git history
  (evolution goldens).
- Author ~20 goldens (2–4/category), **human-verify each** key-point set against the SHA.
  No runner code needed yet.

**Step 1 — condition-A runner end-to-end + baseline.**
- Build `agent.py` (tools=file-only), `tokens.py`, `judge.py` (Opus), `goldens.py`,
  `report.py`, `__main__`, and `tests/eval/` (mocked SDK/judge — no live calls in CI).
- Run A on the goldens **N≥3 on the same SHA** → establish the 92%-class quality baseline
  and the **noise band** (§3 non-regression band derives from this). Check in the first
  run artifact.

**Step 2 — condition C (chgraph).**
- Index each corpus repo (daemon + `index`), then run `index_sanity.py`
  (`chgraph-diagnostics-and-tooling`) as a **precondition gate** — no eval spend on a
  silently degraded index (the #333 failure class). Wire chgraph MCP into the SDK options.
- Run C, produce the A-vs-C report.

**Step 3 — condition B (reference tool).**
- Clone + install `codebase-memory-mcp`, wire its MCP server. Produce the full A/B/C report.
- Only after this report exists do §3's acceptance thresholds enforce mechanically.

(§6's DECIDED order is A-first; I put **C before B** since C is what we're validating and B
requires standing up an external tool — both land before thresholds enforce. Flagging as a
minor deviation to confirm.)

## 4. Scoring, token accounting, artifacts

- **Judge:** one Opus Messages call per (question, answer) → structured verdict (per-key-point
  covered? + overall pass + score). Rubric in `evals/rubric.md`, version ID in every run.
  Re-check the ≥20% human-audited subset whenever judge model or rubric changes.
- **Tokens:** sum input+output over all turns; tool-result tokens count (they're next-turn
  input). Record **raw** usage incl. cache-read/write separately — prompt caching can flatter
  input counts; report both raw and cache-discounted so comparisons stay honest.
- **Run artifact (every run):** run ID, date, per-condition quality % + token totals, corpus
  SHAs, golden-set version, judge model ID + rubric version, failures listed. Checked into
  `evals/runs/`. A number without those four provenance fields is an anecdote.

## 5. Open items & risks

- **Corpus repos**: RESOLVED → `evals/corpus.yaml`. Reuse the reference tools' own Python
  corpora: **django/django** (head-to-head; in both codebase-memory-mcp's and codegraph's
  corpora; >50K LOC + rich history), plus **flask**/**click** (codegraph's Python tier) for
  cheap runner iteration. SHAs pinned 2026-07-03.
- **Parser coverage caps the head-to-head** to Python until more languages land.
- **Reference tool (B)** may not install cleanly or may degrade on >50k LOC; if it can't run a
  condition, the report says so (no faked condition).
- **Cost**: N≥3 × conditions × ~20 questions × Opus judge is real $ per run — eval runs are
  on-demand, not in the default `pytest` pass.
- **Nondeterminism**: agent runs vary; the N≥3 noise band exists precisely for this.
- **Confirm during build**: exact Agent SDK `usage` field names and how it surfaces MCP
  tool-result tokens.
- **Langfuse (deferred 2026-07-03)**: considered for judge/annotation/run-comparison UI.
  Dropped for now to get the harness going; `report.py` + plain Opus `judge.py` stay the
  source of truth. Revisit as an *optional* annotation/comparison layer, never as the sole
  home for numbers (§2 requires checked-in artifacts). Self-host needs a full ClickHouse
  *server* — it does not reuse chgraph's embedded chdb.

## 6. First actions

1. Change-control note for the SDK/Anthropic dev-dep bump.
2. `evals/corpus.yaml` with 1–2 pinned Python repos meeting the criteria.
3. ~20 human-verified goldens across the six categories.
4. Then Step 1 (condition-A runner + baseline).

## 7. Build status — 2026-07-03

**Landed (code + TDD, all mocked so no live calls / no spend):**
- `eval` dep group added (`claude-agent-sdk`, `anthropic`, `pyyaml`) — gated dep bump, isolated from the shipped `chgraph` runtime.
- `evals/corpus.yaml` — django/flask/click pinned to real SHAs.
- Harness under `src/chgraph/eval/`: `goldens` (strict load/validate), `conditions` (A/C wired, B stubbed), `agent` (SDK behind injectable boundary + token accounting), `judge` (Opus, rubric v1, JSON verdict), `report` (provenance-carrying artifact + markdown), `runner` (orchestration, agent-error → recorded failure), `__main__` (CLI: clone-at-SHA, index for C, write report).
- `tests/eval/` — 21 tests green (SDK + judge mocked at their boundaries).
- Starter goldens (4, click) grounded in real symbols — **drafts, `verified: false`**.

**Gated / not yet done (the honest remainder of M1):**
- **Live baseline run** — needs `ANTHROPIC_API_KEY` + the `claude` CLI + real spend. Not runnable autonomously.
- **Human-verify the goldens** (§4: LLM-drafted is fine, LLM-verified is not) and expand to ~20 (2–4/category).
- **N≥3 condition-A run** to set the 92%-class baseline + noise band; check the artifact into `evals/runs/`.
- Condition C: run against a chgraph-indexed checkout. Condition B: stand up the reference MCP server (Step 3).

Run when ready: `python -m chgraph.eval --condition A --repo click` (cheapest smoke).

## 8. Condition-A baseline — VERIFIED 2026-07-03 (run `run-2026-07-03-A-2a3e`)

First N=3 condition-A run on the 14-golden set (click+flask), Sonnet-5 scaffold / Opus-4.8 judge (rubric v1). Artifacts `-r1/-r2/-r3.json` + `-band.json` checked in.

- **Quality: 100.0% ±0.0%** (14/14 all three runs).
- **Token noise band: 188,858 tokens/q ±14,595 (±7.7%)** (min 174,470, max 203,650). ← usable non-regression band for token budget (§3).
- **Cost: ~$3–4 total** — measured, not estimated: ~84% of tokens are 0.1× cache-reads (the `tokens_raw` split), so real cost is ~$1/run, far below the token-count estimate.

**Caveats (why the quality band is not yet the bar):**
- Goldens are still `verified: false` and easy — condition A (plain exploration) answers all 14, so quality is pinned at 100% and doesn't discriminate. The **token band is the meaningful output today**; the quality band needs harder + human-verified goldens (esp. a >50K-LOC repo and cross-file/impact questions plain exploration struggles with) before it means anything.
- Known small gaps: CLI prints nothing per-question within a run (looks frozen); `--runs>1` band-write path is not unit-tested (the missing-`json`-import bug slipped through — fixed, but the argparse glue has no injection test).

## 9. First A-vs-C head-to-head — VERIFIED 2026-07-04 (run `run-2026-07-04-C-35c8`)

Same 14-golden set (click+flask), N=3, Sonnet-5 scaffold / Opus-4.8 judge.

| Condition | Quality | Tokens/question |
|---|---|---|
| A (plain file exploration) | 100% ±0% | 188,858 ±14,595 |
| C (chgraph graph tools) | 98% ±4% | 198,316 ±10,941 |

**Token delta: C +5.0% vs A — bands overlap heavily, so C ≈ A. NOT the predicted ~1/5 reduction.** Per-question splits are mixed (C fewer turns/tokens on some, more on others); cache-reads dominate both (~80–90%).

**Honest reading:** this proves the **plumbing** (harness + both conditions + judge + band, end-to-end, after fixing 3 real integration bugs), not the **thesis**. The current corpus *cannot* discriminate:
- click/flask are small — graph tools save nothing when file exploration is already cheap; the graph's advantage is expected only on large repos (django, >50K LOC) where plain exploration explodes.
- Goldens are easy + `verified: false`, so quality pins near 100% for both.

**To get a real head-to-head:** add django to the active corpus, author human-verified goldens with hard cross-file/impact questions, then A-vs-C on that. Until then, no graph-advantage claim is supported (and none may be made — research-frontier claim discipline).

3 integration bugs found & fixed while wiring C: async index (now blocks to terminal state), blobless clone breaks git-ingest (full clone), stale partial clone survives idempotent skip (re-clone guard).

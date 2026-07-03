---
name: chgraph-research-methodology
description: Use when someone has a hunch, hypothesis, or "what if" about chgraph — a ranking signal, a chdb capability question, a performance claim, a proposed experiment — and needs to test it so the result counts. Also for reviewing an experiment writeup, assigning a refutation pass, estimating if an approach is fast enough before building, timing a query, or explaining a result after the fact. Keywords: hypothesis, prediction, benchmark, probe, back-of-envelope, refutation, experiment lifecycle.
---

# chgraph Research Methodology

The discipline that turns a hunch into an accepted result. chgraph's founding bet (hybrid git-evolution ranking, see `chgraph-git-evolution-campaign`) lives or dies on whether we can distinguish real improvements from wishful thinking. This skill defines the evidence bar, the idea lifecycle, and the first-principles analysis toolkit — each toolkit method demonstrated with a real experiment from this project's own Phase-1/Phase-2 history.

**Context for the zero-context reader (as of 2026-07-03):** chgraph is a chdb-backed (chdb = in-process ClickHouse for Python) MCP server for codebase knowledge graphs, an alternative to DeusData/codebase-memory-mcp ("the reference tool"). The repo has no code yet; this doctrine precedes it. All chdb facts below were verified on chdb 26.5.0 (engine 26.5.1.1), macOS arm64.

## Evidence labels (project-wide convention)

Every claim in chgraph docs, skills, and experiment writeups carries one of four labels:

| Label | Meaning | Minimum backing |
|---|---|---|
| **VERIFIED** | We ran it locally and saw the output | The command + observed output, date-stamped, with versions |
| **REPORTED** | From external research | A public source URL |
| **DECIDED** | A design decision | Written rationale + what would reverse it |
| **OPEN** | Unproven candidate | A falsifiable statement of what would prove/disprove it |

Overselling is the cardinal sin. A wrong runbook is worse than no runbook. If you cannot label a claim, you do not get to state it.

## The evidence bar

A result is **accepted** in chgraph only when all three hold:

1. **One mechanism explains ALL observations — including the negatives.** A mechanism is the causal story ("the second process fails because chdb takes an exclusive flock on the `status` file at server init, before the read-only flag is consulted"), not a restatement ("read-only mode doesn't work"). If your mechanism explains why the fast case is fast but not why the slow case is slow, it is not yet the mechanism. Negative results and non-effects are observations too and must be covered.
2. **The prediction preceded the run** (see next section).
3. **It survived an assigned adversarial refutation pass.**

### How refutation gets assigned (DECIDED)

A *refutation pass* is a deliberate attempt to break the result, performed by someone who did not produce it. Assignment rules:

- The moment an experiment's author marks a result "candidate-accepted" in its experiment doc, a refutation pass is assigned. No result enters `chgraph-change-control` without a completed one.
- The refuter must be **context-fresh**: a second engineer, or (solo-dev reality) a fresh agent session that receives ONLY (a) the hypothesis doc with its pre-registered predictions, (b) the raw commands and outputs — never the author's interpretation or conclusion section.
- The refuter's standing charge, verbatim: *"Produce either an alternative mechanism that explains all the observations, or a new cheap observation that the claimed mechanism cannot explain. Run at least one such observation."*
- Outcomes: **survives** (refuter found nothing, or found something and the mechanism absorbed it after re-testing) or **broken** (result returns to experiment state or is retired). The refuter's transcript is appended to the experiment doc either way.

Rationale: the author's session is contaminated by the effort already spent; confirmation bias is a mechanism, and the countermeasure must be structural, not aspirational.

## Predictions come before runs

Write the predicted numbers down **before** executing. A prediction is a specific quantity or band ("warm top-10 query over 10⁵×768 vectors: 30–300 ms"), not a direction ("should be fast"). Bands wider than one order of magnitude mean you don't have a mechanism yet — do the arithmetic first (Toolkit method 3).

A post-hoc explanation is **archaeology, not evidence**. It tells you what story fits; it cannot tell you whether you understand the system, because any observation has infinitely many fitting stories. Prediction-first is the only cheap test of understanding we have. When a prediction misses, that miss is a finding — record it, don't rewrite it (see anti-patterns: this project's own cold-cache prediction missed, documented below, and taught us something real about page cache).

## The idea lifecycle

```
hunch → written hypothesis (with predicted numbers)
      → experiment (behind a flag or branch)
      → eval-harness verdict
      → ADOPTED via chgraph-change-control
        — or —
        RETIRED with a chgraph-failure-archaeology entry
```

There is **no third terminal state**. Every idea that reaches the experiment stage ends up either adopted or retired-with-writeup.

| Stage | Artifact | Exit criterion |
|---|---|---|
| Hunch | A sentence anywhere | Someone writes the hypothesis doc, or it evaporates (fine — hunches are free) |
| Hypothesis | `docs/experiments/EXP-NNNN-<slug>.md` (DECIDED location; directory doesn't exist yet — create on first use) containing: mechanism, predicted numbers, kill criterion | Experiment implemented behind a flag/branch |
| Experiment | Flag/branch + commands + raw outputs appended to the doc | Eval-harness verdict (harness and thresholds are owned by `chgraph-validation-and-qa`) + refutation pass |
| Adopted | Change lands through `chgraph-change-control` gates — nothing routes around it; any schema, retrieval-behavior, or tool-surface change goes through its gates even if the experiment "obviously" won | — |
| Retired | Entry written per `chgraph-failure-archaeology` | — |

**Zombie experiments are forbidden (DECIDED):** an experiment flag or branch with no verdict after 14 days is force-retired with an archaeology entry saying so. A zombie flag is worse than a retired idea: it costs maintenance, poisons benchmarks, and its author's memory of "we tried that" decays into folklore.

The hypothesis doc's **kill criterion** is written at hypothesis time: the specific number below/above which the idea is retired. Deciding it later is threshold-moving (see anti-patterns).

## Where good ideas come from

Three proven quarries for this project (prioritization across them — the research portfolio and any external claims — belongs to `chgraph-research-frontier`):

1. **The reference tool's issue tracker** (REPORTED: https://github.com/DeusData/codebase-memory-mcp/issues). Each recurring complaint is a pre-validated user need with a built-in eval case: #333 (silent index degradation — 72k LOC repo, ~500 nodes, status "indexed") → status-honesty hypotheses; #480 (empty trace_path despite CALLS edges) → traversal-correctness golden cases; #509 (agents under-use the tools) → tool-description/naming experiments; #557 (data loss on corruption detection) → durability invariants.
2. **ClickHouse capabilities unexplored for code.** The engine ships machinery no code-graph tool uses yet: full git-history ingestion at scale (REPORTED: https://clickhouse.com/docs/getting-started/example-datasets/github), window functions for churn decay, the `sparse_grams` skip index for identifier search (VERIFIED present in chdb 26.5.0's index list — see Toolkit method 2 output). Each is an OPEN candidate until probed and eval'd.
3. **Eval-harness failure analysis.** Every golden question the harness fails is a hypothesis generator: *why* did ranking put the stale symbol first? The harness itself belongs to `chgraph-validation-and-qa`; mining its failures for mechanisms belongs here. The flagship OPEN question of the whole project lives in this quarry: the reference tool self-reports 83% answer quality vs 92% for plain file exploration (REPORTED: https://arxiv.org/abs/2603.27277) — can hybrid git-evolution ranking close that gap?

---

# The first-principles toolkit

Four methods. Each recipe is followed by a worked example from this project's real history — commands actually run, outputs actually observed.

## Method 1: The discriminating experiment

A *discriminating experiment* is one whose two possible outcomes force two different designs. If both outcomes lead to the same next step, the experiment is entertainment — don't run it.

**Recipe:**
1. State the design fork ("if X, design A is viable; if not-X, we must do B").
2. Write the hypothesis and the predicted observation for each branch.
3. Build the smallest setup that separates the branches. Two processes, one table, ten lines.
4. Run, record verbatim output, name the mechanism, commit to the branch the data chose.

**Worked example (Phase 1, re-verified 2026-07-03 on chdb 26.5.0): does chdb read-only mode bypass the data-dir lock?**

- **Fork:** if `?mode=ro` bypasses the lock, many MCP server processes can share one data dir (read-mostly workload) and no daemon is needed. If not, a single daemon must own the dir.
- **Hypothesis:** read-only mode bypasses the lock (plausible: SQLite readers don't block on writers under WAL).
- **Predicted observation if true:** contender process opens and `SELECT 1` succeeds. **If false:** contender raises a lock error.
- **Setup** — two scripts, run with the project's verification Python (a uv venv with chdb 26.5.0; see `chgraph-build-and-env` for creating one; `$PY` below is its `bin/python`):

`holder.py`:
```python
import sys, time
from chdb import session
s = session.Session(sys.argv[1])
s.query("CREATE TABLE IF NOT EXISTS t (x UInt8) ENGINE = MergeTree ORDER BY x")
print("HOLDER: session open, holding lock", flush=True)
time.sleep(float(sys.argv[2]))
s.close()
print("HOLDER: closed", flush=True)
```

`contender.py`:
```python
import sys
from chdb import session
try:
    s = session.Session(sys.argv[1] + "?mode=ro")
    print("CONTENDER: OPENED READ-ONLY (lock bypassed)", s.query("SELECT 1").bytes())
    s.close()
except Exception as e:
    print("CONTENDER: FAILED:", str(e)[:400])
```

Run (from a directory containing both scripts):
```bash
export SCRATCH=$(mktemp -d)
("$PY" holder.py "$SCRATCH/lockdir" 12 &) && sleep 5 && "$PY" contender.py "$SCRATCH/lockdir"
```

- **Observed (VERIFIED 2026-07-03, chdb 26.5.0, verbatim):**
```
HOLDER: session open, holding lock
Code: 76. DB::Exception: Cannot lock file .../lockdir/status. Another server instance in same directory is already running. (CANNOT_OPEN_FILE)
CONTENDER: FAILED: Failed to create connection: Code: 36. DB::Exception: Error initializing EmbeddedServer: . (BAD_ARGUMENTS)
HOLDER: closed
```
Note the Python exception surfaces as Code 36; the true Code 76 lock error is printed to stderr by the engine. Both must be covered by the mechanism: the exclusive lock on `<dir>/status` is taken during embedded-server init, before any read-only flag matters — which also explains the negative observation that the contender succeeds *after* the holder closes.
- **Design consequence:** hypothesis refuted → **single daemon owns each data dir; MCP stdio shims connect over a local unix socket** (DECIDED; architecture in `chgraph-architecture-contract`, operational symptoms in `chgraph-run-and-operate`). One ten-line experiment fixed the process architecture of the entire project. That is what "discriminating" buys.

## Method 2: Capability probing before designing

Never design against documentation. Docs describe the upstream server; chdb is a differently-compiled embedded build, and features get compiled out. A *capability probe* is a minimal statement that succeeds or fails fast, run before any design assumes the capability.

**Recipe:**
1. Find the claimed capability and its source (blog, docs, changelog). Record it as REPORTED.
2. Write the prediction ("this CREATE will succeed because the feature is GA per <source>").
3. Run the smallest statement that exercises it. Capture the full error — ClickHouse errors often enumerate what IS available, which is free reconnaissance.
4. Record the delta between REPORTED and VERIFIED. That delta is a design input and often an upstream question worth filing.

**Worked example (Phase 1, re-verified 2026-07-03 on chdb 26.5.0): is the HNSW vector index available?**

- **REPORTED:** `vector_similarity` (HNSW approximate-nearest-neighbor index) is GA in ClickHouse server 25.8 (clickhouse.com blog/docs).
- **Prediction:** the CREATE succeeds, chgraph gets indexed vector search.
- **Probe** (in a session on a scratch dir; skip indices require a MergeTree-family engine — a `TEMPORARY TABLE` probe fails earlier, on the Memory engine, VERIFIED):
```python
from chdb import session
s = session.Session("<scratch-dir>")
s.query("""
    CREATE TABLE probe_vec (id UInt32, v Array(Float32),
        INDEX iv v TYPE vector_similarity('hnsw', 'cosineDistance', 768))
    ENGINE = MergeTree ORDER BY id
    SETTINGS allow_experimental_vector_similarity_index = 1
""")
```
- **Observed (VERIFIED 2026-07-03, chdb 26.5.0, verbatim):**
```
Code: 80. DB::Exception: Unknown Index type 'vector_similarity'. Available index types: hypothesis, text, bloom_filter, sparse_grams, tokenbf_v1, ngrambf_v1, set, minmax: When validating secondary index `iv`. (INCORRECT_QUERY)
```
- **Consequences:** (a) DECIDED — embeddings use brute-force `cosineDistance` (capacity-checked in Method 3); (b) an upstream question to file/track with chdb-io (https://github.com/chdb-io/chdb) before ever promising HNSW; (c) free reconnaissance — the error enumerates the available index types, which is where the `sparse_grams` and `text` candidates in "Where good ideas come from" came from. Full chdb capability matrix lives in `chdb-reference`.

## Method 3: Back-of-envelope capacity math

Before building anything performance-sensitive, do the arithmetic; then verify the arithmetic with a real measurement. If measurement and arithmetic disagree by more than ~10×, you don't understand the mechanism — stop and find out why before designing on top of it.

**Recipe:**
1. Quantify the work: rows × bytes, FLOPs (floating-point operations), seeks.
2. Estimate the hardware rate (memory bandwidth, GFLOP/s) — order of magnitude is fine.
3. **Write the predicted band down** (≤1 order of magnitude wide).
4. Build a toy corpus at the real scale, **sanity-check the corpus**, measure.
5. Compare prediction vs observation; record both, especially the misses.

**Worked example (run 2026-07-03): can brute-force cosineDistance carry chgraph's embedding search?**

Scale: N = 10⁵ symbol embeddings × 768 dims (typical code-embedding dimensionality), Float32.

**The arithmetic (written before running):**
- Data: 100,000 × 768 × 4 B = **307.2 MB**.
- Work per query: cosineDistance ≈ dot product + two norms ≈ 3 × (2 × 768) ≈ 4,600 FLOPs/row → **~4.6×10⁸ FLOPs** per full scan.
- Hardware: Apple M5 Max; ClickHouse vectorizes and parallelizes, hundreds of GB/s bandwidth, tens of GFLOP/s effective. **Predicted: warm top-10 query 30–300 ms; cold (first query after open) 2–5× slower.**

**Build the corpus** (VERIFIED; in a session on a scratch dir):
```sql
CREATE TABLE emb (id UInt32, v Array(Float32)) ENGINE = MergeTree ORDER BY id;

INSERT INTO emb
SELECT number,
       CAST(arrayMap(j -> sipHash64(number, j) / 18446744073709551615.0, range(768)) AS Array(Float32))
FROM numbers(100000);

-- sanity-check the corpus before timing anything:
SELECT uniqExact(cityHash64(toString(v))), formatReadableSize(sum(length(v))*4) FROM emb;
-- observed: 100000, "292.97 MiB"   (292.97 MiB = 307.2 MB ✓ matches the arithmetic)
```

**Why sipHash64 and not randCanonical() — a real trap hit while building this example (VERIFIED, both variants run 2026-07-03):** `arrayMap(x -> randCanonical(), range(768))` was constant-folded so all 768 elements within each row were identical (`uniqExact(v[1])` = `uniqExact(hash(v))` = 49,959) and timed at 23 ms; `randCanonical(x)` produced ONE distinct vector in the whole table and timed at 16 ms. Three corpora, three different "results" for the same benchmark. Degenerate data compresses better, branches differently, and lies. Always run a distinctness probe before timing.

**Measure** (VERIFIED; fresh session, then 7 repeats):
```sql
WITH (SELECT v FROM emb WHERE id = 42) AS q
SELECT id, cosineDistance(v, q) AS d
FROM emb ORDER BY d ASC LIMIT 10
```
Observed (Apple M5 Max, 64 GB RAM, macOS 26.5.2, chdb 26.5.0, Python 3.12):
```
cold: 36 ms
warm runs ms: 35, 36, 35, 35, 36, 36, 35
warm median: 35 ms
top-3: id 42 d=0 (the query vector itself ✓), then d=0.2088, 0.2111
```

**Prediction vs observation:**
- Warm median 35 ms — **inside the predicted 30–300 ms band**, at the fast edge. Arithmetic understood. Brute force at 10⁵ vectors costs ~35 ms/query on this machine: acceptable for an MCP tool call (DECIDED input to the embeddings design).
- Cold prediction **missed**: cold was 36 ms, 1.03× warm, not 2–5×. Mechanism found on investigation: the table had just been written, so its parts were in the OS page cache (RAM-cached disk blocks); "fresh session" ≠ "cold cache". Recorded as a miss, not rewritten — and it produced a rule in Method 4 (define "cold" honestly).
- Extrapolation, arithmetic only (OPEN, not measured): 10⁶ vectors → ~350 ms/query — usable but near the comfort ceiling; re-run this method before promising million-symbol semantic search.

## Method 4: Benchmark protocol

Any number that will be compared — against a baseline, a competitor, or a threshold — must be produced under this protocol, or it is an anecdote.

| Rule | What it means | Why (each learned the hard way somewhere) |
|---|---|---|
| Fixed corpus | Pinned real repositories at pinned commit SHAs. The corpus manifest and acceptance thresholds live with `chgraph-validation-and-qa`. Synthetic corpora get a distinctness/shape sanity probe first (see Method 3's randCanonical trap). | Unpinned corpora drift; degenerate corpora lie |
| Never the demo repo | The tiny repo used in docs/tests is cache-resident, unrepresentative, and every code path is warm | Reference tool's failures are *large-repo* failures (#333, #524); a demo-repo benchmark cannot see them |
| Warm/cold separated | Warm = repeated in-process queries. Cold = fresh process AND honestly stated cache state — on macOS you cannot easily evict the page cache, so label such runs "fresh-process, cache-warm", not "cold" (VERIFIED miss in Method 3) | Mixing them produces bimodal garbage |
| Medians, not means; report all runs | ≥5 warm runs, print every number (e.g. `35, 36, 35, 35, 36, 36, 35`), take the median, discard nothing silently | Means launder outliers; hidden discards are threshold-moving |
| Report the machine | One line, every result: `Apple M5 Max, 64 GB, macOS 26.5.2, chdb 26.5.0, Python 3.12` | A number without hardware+version context cannot be compared or re-verified |
| Baseline and candidate in the same harness | Same process pattern, same corpus, same run count; interleave A/B runs if thermal or background drift is suspected | Different harnesses measure the harness |

Public-facing numbers additionally go through `chgraph-research-frontier` (external-claim standards) before publication.

## Anti-patterns

| Anti-pattern | Smell | Correction |
|---|---|---|
| Mechanism-free fix | "Adding this setting made it work" with no story of why | No mechanism → not accepted; run the discriminating experiment (Method 1) |
| "It seems faster" | Vibes, single runs, no baseline | Method 4 protocol; verdicts on such claims belong to `chgraph-validation-and-qa` |
| Moving thresholds after seeing results | Kill criterion edited post-run; "80% was close enough" | Kill criterion is written in the hypothesis doc before the run; a miss retires the idea or spawns a NEW hypothesis doc |
| Post-hoc prediction | "As expected, ..." written after the numbers existed | Archaeology, not evidence. Prediction text must predate the run in the experiment doc |
| Benchmarking on the demo repo | All numbers from the tiny test fixture | Fixed real corpus (Method 4) |
| Degenerate synthetic corpus | Generated data never sanity-checked; suspicious ties in results | Distinctness/shape probe before timing (this project hit it twice in one afternoon — Method 3) |
| Zombie experiment | A flag or branch with no verdict, weeks old, "we'll get back to it" | 14-day force-retirement into `chgraph-failure-archaeology` |
| Confirmation-only testing | Only the happy-path observation gathered | The evidence bar requires negatives explained + an assigned refutation pass |
| Trusting docs over probes | Designing against a blog post ("HNSW is GA") | Capability probe first (Method 2); docs describe a different binary |

## When NOT to use this

- **Landing an already-accepted change** (schema, retrieval behavior, tool surface) → `chgraph-change-control`. This skill produces evidence; that skill governs adoption.
- **Writing up a retired/failed experiment** → `chgraph-failure-archaeology` owns the writeup format and the ledger.
- **Building or running the eval harness, golden sets, acceptance thresholds, pytest layout** → `chgraph-validation-and-qa`. This skill tells you a verdict is required; that one produces it.
- **Deciding what to research next, novelty checks, or any externally published claim/comparison** → `chgraph-research-frontier`.
- **Looking up established chdb behavior/limits** (lock, sessions, index types, recursive CTEs) → `chdb-reference`; re-probe only on version bumps.
- **Debugging a defect in existing behavior** (not testing a hypothesis about a new idea) → `chgraph-debugging-playbook`.

## Provenance and maintenance

Grounded 2026-07-03 by live execution in a uv-managed Python 3.12 venv with chdb 26.5.0 (engine 26.5.1.1) on Apple M5 Max, 64 GB, macOS 26.5.2: the two-process lock test (Method 1 output verbatim), the vector_similarity probe (Method 2 output verbatim), the 100k×768 brute-force build+timing including both degenerate-corpus failures (Method 3, all numbers as observed). Reference-tool facts (issue numbers, 83%-vs-92% eval) are REPORTED from https://github.com/DeusData/codebase-memory-mcp and https://arxiv.org/abs/2603.27277. The refutation-assignment scheme, EXP-doc location, and 14-day zombie rule are DECIDED here and have not yet been exercised in anger.

Re-verify on drift (all with the project venv's python):

| What may drift | One-liner |
|---|---|
| chdb version | `python -c "import chdb; print(chdb.__version__, chdb.query('SELECT version()').bytes())"` |
| Exclusive lock / ro bypass | Re-run Method 1's holder/contender pair; expect Code 76 on stderr, Code 36 in the exception |
| Index availability (vector_similarity, sparse_grams, text) | Re-run Method 2's probe; read the "Available index types" list in the error |
| Brute-force timing envelope | Re-run Method 3's build+measure block; compare to 35 ms warm median (this machine) |
| Reference tool surface / issues | `curl -s https://api.github.com/repos/DeusData/codebase-memory-mcp/releases/latest` and re-scan the issue links above |
| 83%/92% eval numbers | Check https://arxiv.org/abs/2603.27277 for revisions |

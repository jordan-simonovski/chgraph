---
name: chgraph-git-evolution-campaign
description: Use when building, extending, or debugging chgraph's flagship git-evolution graph — ingesting git history (git log --numstat) into ClickHouse tables, computing churn, co-change coupling, ownership, or recency decay, joining git metrics onto the symbol graph, or ranking that demotes stale code. Triggers - "deprecated code ranks as well as live code", recency ranking, hotspots, logical coupling, hybrid scoring weights, rename history, evolution metrics, or "where does the flagship campaign stand".
---

# chgraph git-evolution campaign

The executable runbook for chgraph's flagship differentiator: ingest full git history into ClickHouse tables, join it onto the symbol graph, and rank retrieval results so **live code beats stale code**. This attacks the documented SOTA failure mode: "deprecated code retrieves as well as current code, so agents patch the wrong target" (REPORTED: https://redis.io/blog — KG-RAG staleness discussion; also the SOTA survey's universal-staleness finding). No Neo4j/Kuzu-backed competitor (CodeGraphContext, GitNexus) attempts evolution analytics; the reference tool codebase-memory-mcp has only a thin `githistory` pass producing FILE_CHANGES_WITH edges (REPORTED: https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md).

**Status as of 2026-07-03**: the repo is empty. Every phase below was prototyped end-to-end against chdb 26.5.0 on a synthetic repo; all "Expected" blocks are pasted real output (VERIFIED), not projections. Commands referencing a future `chgraph` CLI are labeled DECIDED/OPEN.

**Definitions** (used throughout):
- **Churn**: sum of lines added + deleted per file across history.
- **Co-change coupling** (a.k.a. logical coupling): files that repeatedly change in the same commits. Formula owned by **code-graph-reference**; used here as: `support(A,B)` = number of commits touching both A and B; `confidence(A→B) = support / commits(A)`.
- **Recency decay**: `exp(-ln(2)/half_life_days × age_days)` — score 1.0 for code touched today, 0.5 at one half-life.
- **Hybrid ranking**: one SQL statement combining lexical, vector, recency, and centrality signals into a single score.

## How to run this campaign

Work through phases in order. Each phase has a **Gate** — exact numbers you must observe before proceeding. Prototype in a scratch directory of your choosing (`$SCRATCH` below, e.g. `mktemp -d`); nothing lands in the repo until Phase 6's gates say so. `$PY` is the repo's uv-managed Python 3.12 venv interpreter (`.venv/bin/python`) with chdb installed per **chgraph-build-and-env** (pip pin `chdb==4.2.0`, which resolves chdb-core 26.5.0; `chdb.__version__` reports `26.5.0`).

---

## Phase 0 — Environment

Owned by **chgraph-build-and-env** — follow it, don't improvise. The two traps that abort this campaign at step one:

| Check | Command | Must see |
|---|---|---|
| Python ≥3.12 (system python3 is 3.9.6 — too old) | `$PY --version` | `Python 3.12.x` |
| chdb importable + pinned | `$PY -c "import chdb; print(chdb.__version__)"` | `26.5.0` (VERIFIED 2026-07-03) |

If chdb version differs → stop, re-verify the Provenance section's drift checks before trusting any Expected block below.

## Phase 1 — Walking skeleton: schema exists in a persistent session

Create the minimal campaign cut of the schema. Authoritative schema is owned by **chgraph-architecture-contract** — the `nodes`/`edges` DDL below quotes its canonical Decision-5 DDL verbatim; the `git_*`/`file_evolution` tables are campaign-owned side tables. Changing anything here later routes through **chgraph-change-control**. Save as `$SCRATCH/phase1_skeleton.py` and run `$PY $SCRATCH/phase1_skeleton.py $SCRATCH/chdb-data`:

```python
import sys
import chdb.session as chs

sess = chs.Session(sys.argv[1])
sess.query("CREATE DATABASE IF NOT EXISTS chgraph")

sess.query("""
CREATE TABLE IF NOT EXISTS chgraph.nodes (
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
ORDER BY (project, qualified_name)""")

sess.query("""
CREATE TABLE IF NOT EXISTS chgraph.edges (
    project String,
    source String,                         -- qualified_name of source node
    target String,
    type LowCardinality(String),           -- CALLS, IMPORTS, DEFINES, ...
    properties String,
    version UInt64
) ENGINE = ReplacingMergeTree(version)
ORDER BY (project, type, source, target)""")

sess.query("""
CREATE TABLE IF NOT EXISTS chgraph.git_commits (
    project String, hash FixedString(40), author_name String, author_email String,
    committed_at DateTime, message String
) ENGINE = MergeTree ORDER BY (project, committed_at, hash)""")

sess.query("""
CREATE TABLE IF NOT EXISTS chgraph.git_file_changes (
    project String, hash FixedString(40), committed_at DateTime, author_email String,
    path String, old_path String, additions UInt32, deletions UInt32, is_rename UInt8
) ENGINE = MergeTree ORDER BY (project, path, committed_at)""")

sess.query("""
CREATE TABLE IF NOT EXISTS chgraph.file_evolution (
    project String, path String,
    commit_count UInt32, churn UInt64,
    last_commit_at DateTime, top_author String, top_author_share Float32,
    recency_score Float32,
    version UInt64
) ENGINE = ReplacingMergeTree(version) ORDER BY (project, path)""")

print(sess.query("SHOW TABLES FROM chgraph", "TSV"))
sess.close()
```

**Gate — Expected output (VERIFIED, chdb 26.5.0):**

```
edges
file_evolution
git_commits
git_file_changes
nodes
```

Branches:
- `Cannot lock file .../status. Another server instance in same directory is already running` → another process holds the data dir (exclusive lock, even read-only — fact owned by **chdb-reference**; daemon architecture owned by **chgraph-run-and-operate**). Kill the other process or use a fresh dir. Never work around by sharing the dir.
- Session opens but tables missing on a second run → you passed a different path; `Session(path)` persists per-directory, verify the argument.

## Phase 2 — Git-history ingestion

### 2a. Synthetic fixture repo with planted patterns

You need a repo where you *know* the right answers. This script plants: a co-change pair (`src/api.py` + `tests/test_api.py`, together in 7 commits), a pure rename (`git mv src/legacy.py src/core/legacy.py`), alice-dominant ownership of `api.py`, and a recency spread (api touched 1 day ago, legacy content untouched for 390 days). Dates are relative to run time, so metric outputs below reproduce on any date; commit hashes will differ. Save as `$SCRATCH/make_synth_repo.sh`, run `bash $SCRATCH/make_synth_repo.sh $SCRATCH/synth-repo`:

```bash
#!/bin/bash
set -euo pipefail
REPO=${1:?usage: make_synth_repo.sh <dir>}
rm -rf "$REPO"; mkdir -p "$REPO"; cd "$REPO"
git init -q -b main
git config user.name alice; git config user.email alice@example.com

commit() { # commit <days_ago> <author> <email> <msg>
  local days=$1 name=$2 email=$3 msg=$4
  local d
  d=$(date -u -v-"${days}"d '+%Y-%m-%dT12:00:00' 2>/dev/null || date -u -d "-${days} days" '+%Y-%m-%dT12:00:00')
  GIT_AUTHOR_NAME=$name GIT_AUTHOR_EMAIL=$email GIT_AUTHOR_DATE=$d \
  GIT_COMMITTER_NAME=$name GIT_COMMITTER_EMAIL=$email GIT_COMMITTER_DATE=$d \
  git commit -q -m "$msg"
}

mkdir -p src tests
printf 'def handle():\n    pass\n' > src/api.py
printf 'def helper():\n    pass\n' > src/util.py
printf 'def old_thing():\n    pass\n' > src/legacy.py
printf 'def test_handle():\n    pass\n' > tests/test_api.py
git add -A; commit 400 alice alice@example.com "initial skeleton"

printf '\ndef old_thing2():\n    pass\n' >> src/legacy.py
git add -A; commit 390 bob bob@example.com "extend legacy"

for i in 1 2 3 4 5 6; do
  days=$((200 - i * 20))
  printf '\ndef handle_v%s():\n    pass\n' "$i" >> src/api.py
  printf '\ndef test_handle_v%s():\n    pass\n' "$i" >> tests/test_api.py
  if [ "$i" -eq 4 ]; then author=bob; email=bob@example.com; else author=alice; email=alice@example.com; fi
  git add -A; commit "$days" "$author" "$email" "api feature v$i + test"
done

printf '\ndef helper2():\n    pass\n' >> src/util.py
git add -A; commit 90 bob bob@example.com "util helper2"
printf '\ndef helper3():\n    pass\n' >> src/util.py
git add -A; commit 80 bob bob@example.com "util helper3"

mkdir -p src/core
git mv src/legacy.py src/core/legacy.py
commit 60 alice alice@example.com "move legacy into core/"

printf 'debug: false\n' > config.yaml
git add -A; commit 30 alice alice@example.com "add config"

printf '\ndef helper4():\n    pass\n' >> src/util.py
printf 'verbose: true\n' >> config.yaml
git add -A; commit 10 bob bob@example.com "util helper4 + config"

printf '\ndef handle_hotfix():\n    pass\n' >> src/api.py
git add -A; commit 1 alice alice@example.com "api hotfix"

echo "TOTAL_COMMITS=$(git rev-list --count HEAD)"
```

**Expected (VERIFIED):** `TOTAL_COMMITS=14`.

### 2b. Parse `git log --numstat` into chdb

The ingestion format is `git log --no-merges -M --pretty=format:'C%x09%H%x09%an%x09%ae%x09%at%x09%s' --numstat` — one tab-separated `C`-prefixed header line per commit, then one `additions<TAB>deletions<TAB>path` line per changed file. Two observed (VERIFIED, git 2.50.1) quirks you MUST handle:
- **Renames** appear as `src/{ => core}/legacy.py` (or bare `old => new`) with `0	0` counts — the `-M` flag is required or the rename shows as delete+add and file history fractures silently.
- **Binary files** print `-	-	path` (not observed in the fixture; handle defensively).

(DECIDED: hand-rolled `--numstat` parsing over `clickhouse git-import` for v1 — git-import produces richer line-level tables and is proven at Linux/Chromium scale (REPORTED: https://clickhouse.com/docs/getting-started/example-datasets/github), but it's an external binary dependency and its schema is not project-keyed; revisit via **chgraph-change-control** if line-level blame is needed.)

Save as `$SCRATCH/ingest_git.py`, run `$PY $SCRATCH/ingest_git.py $SCRATCH/synth-repo $SCRATCH/chdb-data synth`:

```python
import json, re, subprocess, sys, tempfile, os

repo, data_dir, project = sys.argv[1], sys.argv[2], sys.argv[3]

fmt = "C%x09%H%x09%an%x09%ae%x09%at%x09%s"
out = subprocess.run(
    ["git", "-C", repo, "log", "--no-merges", "-M", f"--pretty=format:{fmt}", "--numstat"],
    check=True, capture_output=True, text=True).stdout

BRACE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")

def expand_rename(path):
    """'src/{ => core}/legacy.py' -> ('src/legacy.py', 'src/core/legacy.py')."""
    m = BRACE.match(path)
    if m:
        pre, old_mid, new_mid, post = m.groups()
        return (pre + old_mid + post).replace("//", "/"), (pre + new_mid + post).replace("//", "/")
    if " => " in path:
        old, new = path.split(" => ", 1)
        return old, new
    return None, path

commits, changes = [], []
cur = None
for line in out.splitlines():
    if line.startswith("C\t"):
        _, h, an, ae, at, msg = line.split("\t", 5)
        cur = {"project": project, "hash": h, "author_name": an,
               "author_email": ae, "committed_at": int(at), "message": msg}
        commits.append(cur)
    elif line.strip():
        add, dele, path = line.split("\t", 2)
        old, new = expand_rename(path)
        changes.append({
            "project": project, "hash": cur["hash"],
            "committed_at": cur["committed_at"], "author_email": cur["author_email"],
            "path": new, "old_path": old or "",
            "additions": 0 if add == "-" else int(add),   # numstat prints '-' for binary
            "deletions": 0 if dele == "-" else int(dele),
            "is_rename": 1 if old else 0,
        })

import chdb.session as chs
sess = chs.Session(data_dir)

# Batch load via file() — never row-by-row INSERTs (see fenced wrong paths).
def load(table, rows):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        tmp = f.name
    sess.query(f"INSERT INTO {table} SELECT * FROM file('{tmp}', 'JSONEachRow')")
    os.unlink(tmp)

load("chgraph.git_commits", commits)
load("chgraph.git_file_changes", changes)

print(sess.query("""
SELECT (SELECT count() FROM chgraph.git_commits) AS commits,
       (SELECT count() FROM chgraph.git_file_changes) AS file_changes,
       (SELECT count() FROM chgraph.git_file_changes WHERE is_rename = 1) AS renames
""", "TSVWithNames"))
sess.close()
```

**Expected (VERIFIED):**

```
commits	file_changes	renames
14	24	1
```

### 2c. Gate — counts must match git ground truth

Run the discriminating check (VERIFIED — these are the exact ground-truth commands):

```bash
cd $SCRATCH/synth-repo
git rev-list --no-merges --count HEAD                          # -> 14
git log --no-merges -M --pretty=format: --numstat | grep -c .  # -> 24
```

Gate: chdb `commits` == first number, `file_changes` == second number, `renames` == 1. Branches:
- **Counts exactly doubled (28/48/2)** → you ran ingestion twice into the same dir. MergeTree INSERTs append; there is no implicit dedupe. (VERIFIED: a second run produced exactly 28/48/2.) Wipe the data dir or `TRUNCATE` both tables and re-ingest. Production ingestion must be idempotent by design — batch replace per **chgraph-architecture-contract**, gated by **chgraph-change-control**.
- `renames = 0` → you dropped `-M` from `git log`, or the brace parser regressed.
- `file_changes` off by a few → check binary-file `-` handling and merge commits (`--no-merges` on BOTH sides of the comparison).

## Phase 3 — Evolution metrics

Run each metric against the ingested fixture. All four queries and outputs below are VERIFIED (chdb 26.5.0, run 2026-07-03; recency/age values reproduce on any date because fixture dates are relative).

### 3a. Churn per file

```sql
SELECT path,
       count() AS commits,
       sum(additions + deletions) AS churn,
       max(committed_at) AS last_touched
FROM chgraph.git_file_changes
WHERE project = 'synth'
GROUP BY path
ORDER BY churn DESC, path
```

```
   ┌─path───────────────┬─commits─┬─churn─┬────────last_touched─┐
1. │ src/api.py         │       8 │    23 │ 2026-07-02 12:00:00 │
2. │ tests/test_api.py  │       7 │    20 │ 2026-04-14 12:00:00 │
3. │ src/util.py        │       4 │    11 │ 2026-06-23 12:00:00 │
4. │ src/legacy.py      │       2 │     5 │ 2025-06-08 12:00:00 │
5. │ config.yaml        │       2 │     2 │ 2026-06-23 12:00:00 │
6. │ src/core/legacy.py │       1 │     0 │ 2026-05-04 12:00:00 │
   └────────────────────┴─────────┴───────┴─────────────────────┘
```

Note rows 4 and 6: **the rename split one file's history in two**. This is deliberate fixture design — it is Phase 4's open problem, made visible.

### 3b. Co-change coupling

Formula owned by **code-graph-reference** (support/confidence). The `support >= 2` floor is DECIDED: one shared commit is noise, not coupling.

```sql
WITH pairs AS (
    SELECT a.path AS file_a, b.path AS file_b, count() AS support
    FROM chgraph.git_file_changes AS a
    INNER JOIN chgraph.git_file_changes AS b
        ON a.hash = b.hash AND a.project = b.project
    WHERE a.project = 'synth' AND a.path < b.path
    GROUP BY file_a, file_b
),
totals AS (
    SELECT path, uniqExact(hash) AS n_commits
    FROM chgraph.git_file_changes WHERE project = 'synth' GROUP BY path
)
SELECT file_a, file_b, support,
       round(support / ta.n_commits, 3) AS conf_a_to_b,
       round(support / tb.n_commits, 3) AS conf_b_to_a
FROM pairs
INNER JOIN totals AS ta ON pairs.file_a = ta.path
INNER JOIN totals AS tb ON pairs.file_b = tb.path
WHERE support >= 2
ORDER BY support DESC, greatest(conf_a_to_b, conf_b_to_a) DESC
```

```
   ┌─file_a─────┬─file_b────────────┬─support─┬─conf_a_to_b─┬─conf_b_to_a─┐
1. │ src/api.py │ tests/test_api.py │       7 │       0.875 │           1 │
   └────────────┴───────────────────┴─────────┴─────────────┴─────────────┘
```

**Gate: the planted pair (`src/api.py`, `tests/test_api.py`) MUST rank #1 with support 7** (6 planted feature commits + the initial commit) and `conf_b_to_a = 1` (every test_api commit also touched api). You planted this pair — if it does not rank #1, the SQL is wrong, not the data. Debug the join (self-join on `hash` with `path <` dedup) before touching anything else. The `util.py`+`config.yaml` single shared commit correctly falls below the noise floor.

Scale warning (OPEN): the pairwise self-join is O(k²) per commit for k files changed. Fine at fixture scale; on monorepos with 1000-file commits it explodes — cap per-commit file count (git-of-theseus and Code Maat both do this) before running on large repos. Unbenchmarked.

### 3c. Ownership concentration

```sql
SELECT path,
       argMax(author_email, cnt) AS top_author,
       round(max(cnt) / sum(cnt), 3) AS top_author_share,
       sum(cnt) AS total_commits
FROM (
    SELECT path, author_email, count() AS cnt
    FROM chgraph.git_file_changes
    WHERE project = 'synth'
    GROUP BY path, author_email
)
GROUP BY path
ORDER BY top_author_share DESC, path
```

```
   ┌─path───────────────┬─top_author────────┬─top_author_share─┬─total_commits─┐
1. │ src/core/legacy.py │ alice@example.com │                1 │             1 │
2. │ src/api.py         │ alice@example.com │            0.875 │             8 │
3. │ tests/test_api.py  │ alice@example.com │            0.857 │             7 │
4. │ src/util.py        │ bob@example.com   │             0.75 │             4 │
5. │ config.yaml        │ bob@example.com   │              0.5 │             2 │
6. │ src/legacy.py      │ bob@example.com   │              0.5 │             2 │
   └────────────────────┴───────────────────┴──────────────────┴───────────────┘
```

Gate: alice owns `src/api.py` at 0.875 (7 of 8 commits — planted). Note row 1 is a lesson, not a bug: a 1-commit file trivially has share 1.0 — ownership needs a minimum-commit floor before it means anything.

### 3d. Recency decay (half-life 30 days — DECIDED initial value, swept in Phase 6)

```sql
SELECT path,
       max(committed_at) AS last_touched,
       dateDiff('day', max(committed_at), now()) AS age_days,
       round(exp(-log(2) / 30 * dateDiff('day', max(committed_at), now())), 4) AS recency_score
FROM chgraph.git_file_changes
WHERE project = 'synth'
GROUP BY path
ORDER BY recency_score DESC
```

```
   ┌─path───────────────┬────────last_touched─┬─age_days─┬─recency_score─┐
1. │ src/api.py         │ 2026-07-02 12:00:00 │        1 │        0.9772 │
2. │ config.yaml        │ 2026-06-23 12:00:00 │       10 │        0.7937 │
3. │ src/util.py        │ 2026-06-23 12:00:00 │       10 │        0.7937 │
4. │ src/core/legacy.py │ 2026-05-04 12:00:00 │       60 │          0.25 │
5. │ tests/test_api.py  │ 2026-04-14 12:00:00 │       80 │        0.1575 │
6. │ src/legacy.py      │ 2025-06-08 12:00:00 │      390 │        0.0001 │
   └────────────────────┴─────────────────────┴──────────┴───────────────┘
```

Gate: `src/api.py` ≈ 0.977 (1 day old), pre-rename `src/legacy.py` ≈ 0.0001. Sanity anchor: 60 days = 2 half-lives = exactly 0.25. **Trap made visible**: `src/core/legacy.py` scores 0.25 because the *rename commit* counts as a touch — a pure `git mv` makes dead code look 60 days fresh. Mitigation candidates in Phase 4; a cheap partial fix is excluding `is_rename = 1` rows from `max(committed_at)` (OPEN — not yet evaluated for side effects on legitimately-renamed live code).

### 3e. Materialize into `file_evolution`

Index-time refresh (this is what the future indexer pass will run; VERIFIED as SQL):

```sql
INSERT INTO chgraph.file_evolution
SELECT project, path,
       count()                          AS commit_count,
       sum(additions + deletions)       AS churn,
       max(committed_at)                AS last_commit_at,
       argMax(author_email, cnt_by_author) AS top_author,
       max(cnt_by_author) / count()     AS top_author_share,
       exp(-log(2)/30 * dateDiff('day', max(committed_at), now())) AS recency_score,
       1 AS version
FROM (
    SELECT project, path, committed_at, additions, deletions, author_email,
           count() OVER (PARTITION BY project, path, author_email) AS cnt_by_author
    FROM chgraph.git_file_changes WHERE project = 'synth'
)
GROUP BY project, path
```

Expected: `SELECT count() FROM chgraph.file_evolution` → `6` (VERIFIED). The `version` column must become the index-generation counter in production (ReplacingMergeTree replace-on-refresh, per **chgraph-architecture-contract**). Storing a *computed* `recency_score` means it goes stale between refreshes — DECIDED: always recompute recency from `last_commit_at` at query time in ranking (Phase 5 does this); the stored column is for cheap browsing only.

## Phase 4 — Joining git metrics onto the symbol graph

**Join strategy (DECIDED):** symbols join to git history via `nodes.file_path = git_file_changes.path` (equivalently `file_evolution.path`), both repo-relative. File-level granularity for v1 — every symbol in a file inherits the file's evolution metrics. Symbol-level attribution (mapping hunks to line ranges) is OPEN, below.

VERIFIED join check (after inserting three fixture symbol nodes — see Phase 5 setup):

```sql
SELECT n.qualified_name, n.file_path, f.commit_count, f.churn,
       round(f.recency_score, 3) AS recency
FROM chgraph.nodes AS n
LEFT JOIN chgraph.file_evolution AS f
    ON n.project = f.project AND n.file_path = f.path
WHERE n.project = 'synth'
ORDER BY n.qualified_name
```

```
   ┌─qualified_name─────────┬─file_path──────────┬─commit_count─┬─churn─┬─recency─┐
1. │ api.handle             │ src/api.py         │            8 │    23 │   0.977 │
2. │ core.legacy.old_handle │ src/core/legacy.py │            1 │     0 │   0.25  │
3. │ util.helper            │ src/util.py        │            4 │    11 │   0.794 │
   └────────────────────────┴────────────────────┴──────────────┴───────┴─────────┘
```

Row 2 is the smoking gun: `core.legacy.old_handle` lives in a file with *3 commits of real history* (initial + extend + move), but the join sees only 1 commit and churn 0 — everything before the rename is stranded under `src/legacy.py`.

### Rename handling — OPEN, the known hard part

Do not silently pick one. Candidates, ranked, each with its theory obligation:

| Rank | Approach | Sketch | Theory obligation before adopting |
|---|---|---|---|
| 1 | **Rename-chain closure at ingest** | `is_rename` rows carry `(old_path → path)`; walk chains at ingest time and rewrite all historical rows to the *current* path (or maintain a `path_identity` mapping table) | Prove chain-walking terminates and is unambiguous under: rename A→B while a new A is created; B→A→B flip-flops; case-only renames on case-insensitive filesystems. Directory renames arrive as N per-file renames — verify. |
| 2 | **`git log --follow` per surviving file** | VERIFIED on fixture: `git log --follow --oneline -- src/core/legacy.py` returns all 3 commits, and `--name-status -M` shows `R100 src/legacy.py src/core/legacy.py` | `--follow` is per-file (one git invocation per file — O(files) subprocess cost, unbenchmarked) and officially "supports only one path argument"; measure on a real repo before committing. |
| 3 | **Content-hash identity** | Track file identity by blob-similarity across commits (what `-M` does internally, done once, stored) | Requires defining a similarity threshold and handling split/merge of files — substantially the hardest to get right; only pursue if 1–2 fail. |

Whichever is chosen: it changes ingestion semantics and therefore retrieval behavior → **must go through chgraph-change-control**, with a fixture gate: after the fix, the Phase 4 join for `core.legacy.old_handle` must show `commit_count = 3` (or 2 if rename-touches are excluded) and the pre-rename churn of 5.

## Phase 5 — Hybrid ranking

### Solution menu — candidate signals, ranked, with obligations

| Rank | Signal | v1 form | Theory/derivation obligation |
|---|---|---|---|
| 1 | Lexical | Name/token match; later: experimental `text` index as candidate filter + custom scoring (no native BM25 in ClickHouse — facts owned by **chdb-reference**) | Define identifier-aware tokenization (camelCase splitting — the reference tool does this in FTS5); scoring function must be specified since the text index only accelerates, it does not rank. |
| 2 | Vector | Brute-force `cosineDistance` over `Array(Float32)` (HNSW `vector_similarity` is compiled out of chdb — VERIFIED, owned by **chdb-reference**) | Pick embedding model; map cosine similarity into [0,1]; benchmark brute-force latency at 10⁴–10⁵ vectors (OPEN — expected fine at codebase scale, but unbenchmarked as of 2026-07-03). |
| 3 | Recency | `exp(-ln2/30 × age_days)` from `git_file_changes`, computed at query time | Half-life 30d is DECIDED-as-initial only; must be swept against the Phase 6 eval, and rename inflation (Phase 4) must be resolved or bounded. |
| 4 | Centrality | Normalized in-degree over CALLS edges | Degree is a crude PageRank proxy; PageRank proper is OPEN (needs iterative computation; Aider repo-map precedent, with its known failure of clustering everything at the top on weak module boundaries — REPORTED, SOTA survey). |
| 5 | Churn/hotspot | not in v1 score | OPEN: high churn correlates with defect density (Tornhill, "Your Code as a Crime Scene"), but churn ≠ query relevance; needs eval evidence before earning a weight. |
| 6 | Co-change expansion | not a score term | OPEN: use coupling as *result expansion* (return coupled files alongside hits), not ranking; needs UX decision in **mcp-server-reference** tool shapes. |
| — | Fusion method | Weighted sum of [0,1]-normalized signals (DECIDED for v1) | Alternative: reciprocal-rank fusion (RRF) — scale-free, no normalization obligation, but requires a well-defined per-signal ranking; OPEN, compare in Phase 6. |

**DECIDED initial weights** (sum = 1.00; initial values chosen so lexical+vector dominate but recency alone can break lexical ties — they are *starting points for the Phase 6 sweep*, nothing more). This campaign is the one home of the decided starting defaults (weight vector, recency half-life, co-change support floor); **code-graph-reference** §6 owns the formula shapes and labels its toy weights illustrative:

```
score = 0.35·lexical + 0.30·vector + 0.20·recency + 0.15·centrality
```

### Expected-shape sanity check (VERIFIED end-to-end)

Setup: insert two functions that tie on lexical AND vector signals — `api.handle` (fresh, called twice) and `core.legacy.old_handle` (stale, uncalled) — plus a distractor. Toy 4-dim embeddings, identical for the twins on purpose:

```sql
INSERT INTO chgraph.nodes VALUES
('synth','Function','handle','api.handle','src/api.py',1,3,'{}',1),
('synth','Function','old_handle','core.legacy.old_handle','src/core/legacy.py',1,3,'{}',1),
('synth','Function','helper','util.helper','src/util.py',1,3,'{}',1);

INSERT INTO chgraph.edges VALUES
('synth','util.helper','api.handle','CALLS','{}',1),
('synth','tests.test_handle','api.handle','CALLS','{}',1);

CREATE TABLE IF NOT EXISTS chgraph.embeddings (
    project String, qualified_name String, vec Array(Float32), version UInt64
) ENGINE = ReplacingMergeTree(version) ORDER BY (project, qualified_name);

INSERT INTO chgraph.embeddings VALUES
('synth','api.handle',[0.5,0.5,0.0,0.0],1),
('synth','core.legacy.old_handle',[0.5,0.5,0.0,0.0],1),
('synth','util.helper',[0.0,0.0,0.9,0.1],1);
```

The one-statement hybrid ranker (VERIFIED; weights interpolated as literals):

```sql
WITH
    [0.5, 0.5, 0.0, 0.0]::Array(Float32) AS qvec,
    'handle' AS qtext,
    recency AS (
        SELECT path,
               exp(-log(2) / 30 * dateDiff('day', max(committed_at), now())) AS r
        FROM chgraph.git_file_changes WHERE project = 'synth' GROUP BY path
    ),
    degree AS (
        SELECT target AS qn, count() AS deg
        FROM chgraph.edges WHERE project = 'synth' AND type = 'CALLS' GROUP BY qn
    ),
    maxdeg AS (SELECT max(deg) AS m FROM degree)
SELECT
    n.qualified_name,
    round(if(positionCaseInsensitive(n.name, qtext) > 0, 1.0, 0.0), 3) AS lex,
    round(1 - cosineDistance(e.vec, qvec), 3)                          AS vec,
    round(coalesce(r.r, 0), 3)                                         AS rec,
    round(coalesce(d.deg, 0) / (SELECT m FROM maxdeg), 3)              AS cen,
    round(0.35 * lex + 0.30 * vec + 0.20 * rec + 0.15 * cen, 4)        AS score
FROM chgraph.nodes AS n
LEFT JOIN chgraph.embeddings AS e ON n.qualified_name = e.qualified_name AND n.project = e.project
LEFT JOIN recency AS r ON n.file_path = r.path
LEFT JOIN degree AS d ON n.qualified_name = d.qn
WHERE n.project = 'synth'
ORDER BY score DESC
```

**Gate — Expected (VERIFIED, chdb 26.5.0):**

```
   ┌─n.qualified_name───────┬─lex─┬─vec─┬───rec─┬─cen─┬──score─┐
1. │ api.handle             │   1 │   1 │ 0.977 │   1 │ 0.9954 │
2. │ core.legacy.old_handle │   1 │   1 │  0.25 │   0 │    0.7 │
3. │ util.helper            │   0 │   0 │ 0.794 │   0 │ 0.1588 │
   └────────────────────────┴─────┴─────┴───────┴─────┴────────┘
```

And the control — the same query with recency and centrality weights zeroed (i.e., what every recency-blind competitor computes) MUST tie the twins (VERIFIED: both score exactly 0.65). That tie *is* the staleness failure mode; the 0.9954-vs-0.7000 separation above is the campaign's entire thesis in one row. If your hybrid query does NOT separate the twins → your recency join is broken (typical cause: joining on stored `file_evolution.recency_score` that was materialized long ago, instead of computing from `committed_at` at query time).

Note the lexical signal here is a placeholder binary match — good enough to prove the fusion mechanics, nowhere near a real relevance function (see menu rank 1 obligations).

## Phase 6 — Validation and promotion

Nothing from Phases 1–5 lands in the repo, and no weight/half-life/formula becomes "the" value, except through this phase.

1. **Eval harness**: owned by **chgraph-validation-and-qa**. Required slices: a *staleness slice* (golden queries whose correct answer is live code that has a stale near-duplicate — generalizations of the Phase 5 twins) and a *general slice* (queries with no staleness angle, to catch recency over-weighting burying stable-but-correct old code — the known failure of naive recency boosts).
2. **Success is a number** (DECIDED initial targets, adjustable only via **chgraph-change-control**): on the staleness slice, hybrid ranking must beat the recency-blind baseline (same query, `w_rec = w_cen = 0`) by **≥ +0.10 absolute MRR@10**, with **≤ 0.02 absolute regression** on the general slice. "It looks better" is not a result. The eventual outer benchmark — closing the reference tool's self-reported 83%-vs-92% answer-quality gap (REPORTED: arXiv:2603.27277) — is owned by **chgraph-research-frontier**; do not cite it as achieved.
3. **Weight sweep**: grid over `w_rec ∈ {0.1, 0.2, 0.3}` × half-life `∈ {14, 30, 90}` days against both slices; promote the winning cell with its numbers attached.
4. **Promotion**: schema (git tables, file_evolution, embeddings), the ranking formula, and any new MCP tool exposing evolution data (`evolution_*` namespace — owned by **mcp-server-reference**) all route through **chgraph-change-control** gates. This skill has no authority to change them; it only proves candidates.

## Fenced wrong paths — do not go here

| Wrong path | Why it's fenced |
|---|---|
| HNSW / `vector_similarity` index for embeddings | Compiled out of chdb — "Unknown Index type 'vector_similarity'" (VERIFIED 2026-07-03, chdb 26.5.0; fact owned by **chdb-reference**). Brute-force `cosineDistance` only. |
| Row-by-row INSERT/upsert during ingestion | Every MergeTree INSERT creates an on-disk part; per-row inserts of 10⁵ file-change rows means 10⁵ parts and merge collapse. Batch via `file()`/bulk INSERT as in Phase 2 (DECIDED at founding, 2026-07-03; batch-write rule owned by **chgraph-architecture-contract**, INV-5). |
| Unguarded `WITH RECURSIVE` for graph traversal on top of these tables | ClickHouse recursive CTEs have no cycle detection; depth cap + `has(visited, x)` guards are mandatory (VERIFIED behavior owned by **chdb-reference**). This campaign's queries deliberately use only joins/aggregates — keep it that way. |
| Sharing a chdb data dir across processes ("just open it read-only from the eval script") | Exclusive `status`-file lock, read-only does NOT bypass (VERIFIED; ownership: **chdb-reference** for the fact, **chgraph-run-and-operate** for the daemon answer). |
| Trusting ingestion "success" without the count gate | Silent wrongness is the reference tool's documented disease (silent index degradation, issue #333 — REPORTED: https://github.com/DeusData/codebase-memory-mcp/issues). The doubled-counts trap (Phase 2c) was real and observed. Always run the discriminating check. |
| `git log` without `-M` | Renames become delete+add pairs; file histories fracture silently and recency/churn are wrong with no error (format VERIFIED in Phase 2b). |
| Declaring ranking quality by eyeballing Phase 5 output | Phase 5 proves *mechanics* on planted data only. Quality claims require Phase 6 numbers through **chgraph-validation-and-qa** — no exceptions. |

## When NOT to use this

- Setting up Python/uv/chdb or fixing "chdb won't import" → **chgraph-build-and-env**.
- Daemon lifecycle, socket wiring, `Cannot lock file .../status` in operation → **chgraph-run-and-operate**.
- chdb SQL capabilities, recursive CTE mechanics, index-type availability → **chdb-reference**.
- Graph-theory formulas (coupling, centrality definitions), node/edge modeling doctrine → **code-graph-reference**.
- MCP tool naming/schemas for exposing evolution data → **mcp-server-reference**.
- Authoritative schema definitions → **chgraph-architecture-contract**; changing them → **chgraph-change-control**.
- Building or running the eval harness itself → **chgraph-validation-and-qa**.
- Something broke while executing this runbook and the branch hints here don't cover it → **chgraph-debugging-playbook**.
- Deciding whether this campaign is still the right bet vs competitors → **chgraph-research-frontier**.

## Provenance and maintenance

Grounded 2026-07-03 by executing every phase against chdb 26.5.0 (pip, macOS arm64, Python 3.12) with git 2.50.1: synthetic 14-commit repo built, ingestion parser run, all Phase 3/4/5 SQL executed, all Expected blocks pasted from real output. External claims carry REPORTED + URL; design choices carry DECIDED; unproven candidates carry OPEN. The fixture's relative dates make metric outputs date-stable; commit hashes are not.

Drift re-verification one-liners (run before trusting this skill after any environment change):

```bash
$PY -c "import chdb; print(chdb.__version__)"            # expect 26.5.0; if newer, re-run all phases
# HNSW still compiled out? Expect: "Unknown Index type 'vector_similarity'". If it succeeds, reopen Phase 5 via chgraph-change-control.
$PY -c "import chdb; chdb.query(\"CREATE TABLE t (v Array(Float32), INDEX vi v TYPE vector_similarity('hnsw','L2Distance',4)) ENGINE=MergeTree ORDER BY tuple() SETTINGS allow_experimental_vector_similarity_index=1\")"
git -C <fixture> log -M --numstat | grep '=>' | head -1  # rename format still brace-style? Expect: 0	0	src/{ => core}/legacy.py
git -C <fixture> rev-list --no-merges --count HEAD       # fixture ground truth still 14?
```

Also re-check: the reference tool's tool list and githistory pass (https://github.com/DeusData/codebase-memory-mcp — last checked at v0.8.1, 2026-06-12) and whether chdb has shipped `vector_similarity` (would reopen the Phase 5 vector-signal design via **chgraph-change-control**).

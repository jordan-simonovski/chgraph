# chgraph

A [chdb](https://github.com/chdb-io/chdb)-backed codebase knowledge-graph MCP
server. It indexes your repo's Python symbols (tree-sitter) and full git history
(churn, co-change coupling, ownership, recency) into an embedded ClickHouse
engine, and serves reference-compatible MCP tools whose ranking demotes stale
code.

**Status: v0.1.** macOS/Linux only (chdb ships no Windows wheels). Retrieval
quality is **unproven** — the eval harness is the next milestone, and no quality,
accuracy, or "better than X" claims are made or permitted until it reports
numbers (see `.claude/skills/chgraph-validation-and-qa/`).

## Requirements

- macOS or Linux (no Windows — chdb is macOS/Linux only)
- [`uv`](https://docs.astral.sh/uv/) (manages the Python toolchain)
- `git` on `PATH` (history ingestion shells out to it)
- ~554 MB of disk for the environment (chdb embeds ClickHouse)

Python 3.12 is pinned via `.python-version`; `uv` fetches it — no system Python
is used (system `python3` is often too old).

## Installation

```sh
git clone <this-repo> chgraph
cd chgraph
uv sync                 # creates .venv on Python 3.12, ~554 MB (first run is slow)
uv run pytest           # verify: 51 passing
```

`uv sync` installs a `chgraph` console script at `.venv/bin/chgraph`. Invoke it
with `uv run chgraph …`, or use the absolute path `…/chgraph/.venv/bin/chgraph`
(that path is what you register in `.mcp.json`). The first `import chdb` in a
fresh process takes ~5 s — this is normal.

## How it works

```
  agent session ── stdio ──► chgraph mcp (shim)
                              │  (relays JSON over a unix socket;
                              │   NEVER imports chdb — stays lock-free & cheap)
                              ▼
                         chgraph daemon  ── owns the chdb data dir ──►  ~/.local/share/chgraph/<slug>/chdb
                              │            (exclusive lock: exactly one
                              │             process may open it)
             ┌────────────────┼────────────────┐
        tree-sitter        git log          ClickHouse (embedded)
        symbols/edges      --numstat        nodes/edges + git side tables
                                             + evolution metrics
```

- **One engine, in-process.** chdb is ClickHouse compiled as a Python library —
  no database server to run. The symbol graph, full git history, and the
  analytics that join them all live in one embedded columnar engine, queried
  with SQL (recursive CTEs for traversal, `cosineDistance` for vectors, a `text`
  index for candidate lexical filtering).
- **Daemon, not a library.** A chdb data directory can be opened by exactly one
  process — even read-only opens fail on the lock (verified). So a single daemon
  per project owns the data dir and serializes all engine access on one worker
  thread; MCP stdio shims relay tool calls to it over a unix socket and never
  import chdb themselves.
- **Honest indexing.** Indexing runs as a background job you poll via
  `index_status`. `degraded` is a first-class state with machine-readable
  reasons (e.g. a nodes-per-KLOC sanity gate) — it is never folded into
  `indexed`.
- **Storage.** Nodes and edges are `ReplacingMergeTree(version)`; writes are
  batched and every read is `FINAL`-correct (no row-by-row upserts). A reindex
  replaces the prior generation rather than accreting ghost rows.
- **Ranking.** `search_graph` combines a lexical signal, git **recency**
  (recomputed at query time so it never goes stale), and call-graph centrality.
  A vector slot is wired in but contributes 0 in v0.1 (no embeddings yet).
- **Traversal.** `trace_path` walks CALLS edges with a `WITH RECURSIVE` query
  that always carries a depth cap **and** a visited-path cycle guard — ClickHouse
  recursive CTEs have no built-in cycle detection.

Full design rationale, invariants, and the honest weak-points register live in
`.claude/skills/` (start with `chgraph-architecture-contract` and
`chgraph-run-and-operate`).

## Use with Claude Code

Add to `.mcp.json` in your project (use an **absolute** path — MCP clients spawn
servers with an unpredictable working directory):

```json
{
  "mcpServers": {
    "chgraph": {
      "command": "/absolute/path/to/chgraph/.venv/bin/chgraph",
      "args": ["mcp"]
    }
  }
}
```

The shim auto-starts the daemon on first use. Then, from any agent session:
`index_repository` → poll `index_status` until `indexed` → `search_graph` /
`trace_path` / `get_code_snippet` / `get_graph_schema` / `list_projects` /
`delete_project`.

## CLI

For terminal use, indexing without an agent, or debugging a stuck daemon. `repo`
is optional and defaults to the top level of the current git repo
(`git rev-parse --show-toplevel`).

```sh
uv run chgraph daemon start   [repo]   # start the daemon (idempotent: "already running")
uv run chgraph daemon status  [repo]   # exit 0 = running, 1 = stopped, 2 = stale/crashed
uv run chgraph daemon stop    [repo]   # graceful shutdown, escalates to SIGTERM/SIGKILL
uv run chgraph daemon restart [repo]

uv run chgraph index          [repo]   # start daemon if needed, enqueue an index, print the job id
uv run chgraph status         [repo]   # print status.json (state, files, nodes, degraded reasons)

uv run chgraph mcp [--repo PATH]       # run the stdio MCP shim (what .mcp.json invokes)
```

Typical terminal flow: `chgraph index` → `chgraph status` (repeat until
`indexed` or `degraded`). The daemon keeps running in the background; stop it
with `chgraph daemon stop`. Logs land in
`~/.local/share/chgraph/<slug>/logs/`.

## Data & layout

Each project's graph lives under `~/.local/share/chgraph/<project-slug>/`
(override the root with `$CHGRAPH_DATA_DIR`), containing the `chdb/` data dir,
the `daemon.sock` socket, `daemon.pid`, `status.json`, and `logs/`. One daemon
per project — everything routes through it because chdb's data-dir lock is
exclusive. To hand a graph to a teammate, ship that directory (the daemon must
be stopped first).

## What sets it apart

These are **architectural and design** differences, not quality claims — chgraph
makes no retrieval-quality or "beats X" claim until its eval harness runs.

- **Embedded analytical engine instead of an external graph DB.** Codebase graph
  tools commonly back onto a separate graph database (Neo4j / Kuzu / FalkorDB)
  or a vector store, and index code *structure*. chgraph instead keeps
  everything in one in-process ClickHouse engine, which lets it treat the graph
  as columnar data and run analytical SQL over it — including joining git history
  directly onto the symbol graph — with zero server to operate.
- **Evolution-aware ranking is the design focus.** A documented failure mode of
  structure-only graphs is that deprecated code retrieves as well as live code.
  chgraph's differentiation axis is to ingest full git history (churn,
  co-change coupling, ownership, recency) and let those signals demote stale
  code in `search_graph`. Whether this measurably improves retrieval is an
  **open question** pending the eval harness — it is a bet, not a proven result,
  and not claimed as novel.
- **Precision over breadth in the graph.** Call (CALLS) edges are emitted only
  when a callee resolves to a same-module definition or an explicit import, and
  a call shadowed by a local/parameter is suppressed — unresolvable calls are
  dropped rather than guessed. chgraph deliberately does **not** compete on
  language breadth, indexing speed, zero-dependency distribution, or native
  Cypher.
- **Status honesty as a hard invariant.** Degraded/failed indexing is surfaced
  explicitly with reasons, never reported as a healthy `indexed`.

For context: the comparable reference tool's own paper self-reports 83% answer
quality vs 92% for a plain file-exploring agent (at far fewer tokens). chgraph
makes **no** such claim in either direction — those numbers are a north star to
independently measure once the harness exists, not a baseline chgraph has met.

# 0005 — Import source-validated CodeGraph references behind an experimental selector

- Status: Proposed
- Date: 2026-11-06
- Class: retrieval-affecting | tool-surface
- Owner: Codex

## Context
**VERIFIED:** the fixture finds missing native method/import paths and a false
upstream shadowed-parameter call. [The guide](../experiments/codegraph-import.md)
owns measured results. CLI context JSON omits evidence required for ingestion.

## Decision
**DECIDED:** expose `chgraph_index_source` as CLI `--source` and an additive MCP
indexing parameter. Default: `native`. Label: `experimental` for `codegraph`,
surfaced in status. Owner: Codex. Re-verify with
`uv run python evals/codegraph_compare.py`. Retire the experimental label only
after corpus edge-quality, ranking and agent quality/context-cost gates pass or
an explicit trade is accepted. Keep the current default until then.

Validate completed compatible snapshots, source hashes and Python parameter
bindings; retain rejected evidence. Keep canonical implementation identities
while retaining distinct overload and alias nodes for explicit queries.

## Alternatives rejected
- Expand every native resolver first: duplicates a reference builder before reuse is tested.
- Import context JSON: loses metadata and provides only a bounded subgraph.
- Patch the installed CLI: creates an untracked dependency change outside this repository.

## Consequences
The internal SQLite schema is a pinned dependency. Unsupported Python syntax
causes explicit degradation with calls quarantined. Full snapshots use memory;
this path currently omits embeddings. Native extraction remains independent.

## Verification
**VERIFIED, 2026-11-06:** fixture and CLI/daemon/MCP tests cover validation,
local/durable restart, failed rebuilds and cancellation. Ranking evidence lives
in the [corpus artifact](../../evals/runs/codegraph-corpus-2026-11-06.json).
**OPEN:** full agent quality/token evaluation; no default promotion follows.

## Rollback
Reindex with `--source native`. No pip dependency or table migration was added.

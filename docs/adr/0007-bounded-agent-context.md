# 0007 — Expose bounded context and separate structural and historical impact

- Status: Proposed
- Date: 2026-11-06
- Class: retrieval-affecting | tool-surface
- Owner: Codex

## Context
**VERIFIED:** agents previously needed separate search/trace/snippet requests.
Depth/cycle guards alone cannot bound every path in a high-fanout DAG.

## Decision
**DECIDED:** add experimental `explore` and `change_impact` tools. Compose
lexical search, graph and Git data with bounded frontiers and representative
paths. Bound SQL work and source text. Verify source/dependency freshness and
report unchecked states. Keep co-change separate from structural callers;
neither proves runtime reachability or test coverage.

## Alternatives rejected
- Copy all upstream ranking machinery: unnecessary before measuring the small composition.
- Enumerate every path: output/memory can grow exponentially.
- Infer affected tests from co-change: association is insufficient evidence.

## Consequences
Results can be incomplete, with explicit truncation/freshness information.
Conservative checking can leave large graphs unverified. Existing core names
remain available; additive tools provide typed structured responses.

## Verification
**VERIFIED, 2026-11-06:** tests exercise token candidates, budgets, fanout/cycles,
stale source/re-exports, FIFO/size limits, Git evidence separation and real MCP.
The drift check finds existing core names still present in
[upstream](https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c),
plus `check_index_coverage`, `compare_graphs` and `get_file_outline` (not implemented here).
**OPEN:** full agent answer-quality/context-cost evaluation.

## Rollback
Use existing search/trace/snippet tools. Removing additive tools requires the
tool-surface gate but no data migration.

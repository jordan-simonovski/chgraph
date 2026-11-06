# 0006 — Publish complete graph generations through one metadata pointer

- Status: Proposed
- Date: 2026-11-06
- Class: retrieval-affecting
- Owner: Codex

## Context
**VERIFIED:** truncation can remove the usable graph before rebuilding finishes.
ReplacingMergeTree versions do not preserve predecessor identities after merges.

## Decision
**DECIDED:** stage under distinct physical project identities, retaining the
public logical project ID and existing schema. On the sole database worker,
checkpoint complete graph/Git data, publish a versioned pointer, checkpoint it,
then prune older owned data. Retain current and previous healthy generations.
Resolve the pointer inside each database operation so joins use one generation.

## Alternatives rejected
- Truncate/reload: loses the prior usable graph on failure.
- Filter replacement versions: merges can erase old rows.
- Exchange several tables: does not supply a cross-table transaction.
- Add generation columns everywhere: requires a migration without a demonstrated need.

## Consequences
Retention costs disk. Cleanup failures report degradation after publication;
they do not undo a usable new graph. Failed staging data remains unreachable
and is eligible for later cleanup. Diagnostics must resolve/display the active
identity. This is an application publication protocol, not a database transaction.

## Verification
**VERIFIED, 2026-11-06:** tests cover retention, pruning, failed publication,
project-scoped resets, diagnostics, local/durable restart and failed rebuilds.
Durable mutation SQL avoids unsupported query settings and waits for completion.
**OPEN:** power-loss fault injection at every WAL boundary and scale/load testing.

## Rollback
Before reverting to an older build, stop the daemon and back up its directory:
old builds ignore the pointer and may truncate retained data. Reindexing with
the desired source under the new build requires no destructive schema repair.

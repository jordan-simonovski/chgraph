# 0003 — Lexical signal: identifier-aware subtoken Jaccard (replaces the binary placeholder)

- Status: Accepted
- Date: 2026-07-08
- Class: retrieval-affecting
- Owner: git-evolution campaign

## Context

The shipped `s_lex` was a placeholder binary: `1.0` if the query is a substring of the symbol
name, `0.5` if only of the qualified_name. It does not discriminate among name-matchers — every
symbol containing the query token scores `1.0`, so on a collision-heavy corpus the canonical symbol
is buried under dozens of ties broken only by alphabetical `qualified_name`. VERIFIED on
SQLAlchemy@12f32306: `MetaData` ranked 44, `Session` 12, `Table` fell outside the LIMIT entirely.

This also caps the flagship deprecation signal (ADR-0002): the third-corpus audit found the
staleness *benefit* was gated by lexical quality — twins buried among ties can't be surfaced by a
small demotion. Fixing lexical was the identified unblock.

`code-graph-reference` DECIDED `s_lex = |q∩s|/|q|` (subtoken query-coverage) with a VERIFIED
RE2-safe two-pass subtoken splitter, but query-coverage alone still scores `1.0` for any symbol
containing all query tokens (it ignores extra symbol tokens), so it barely helps (measured below).

## Decision

`s_lex` = **subtoken Jaccard** `|q∩s| / |q∪s|` over identifier subtokens, computed at query time in
SQL. Subtokens come from the VERIFIED two-pass boundary splitter (acronym→word, lower→Upper, then
non-alphanumeric split, lowercased). A qualified_name-only match (no shared name subtoken) keeps a
`0.15` floor so it ranks below real hits. Behind flag `chgraph_rank_lexical`
(env `CHGRAPH_RANK_LEXICAL` = `jaccard` | `binary`), **default `jaccard`**; `binary` restores the
prior placeholder as an escape hatch. Computed at query time over the already-filtered candidate
rows — no schema change, no re-index.

## Alternatives rejected

- **Keep binary.** Buries canonicals; caps the deprecation signal. It is the thing being fixed.
- **Query-coverage `|q∩s|/|q|` (the prior DECIDED formula).** Measured MRR@10 0.570 vs binary 0.550
  — barely moves (scores 1.0 for any symbol containing the query tokens; doesn't penalize extra
  symbol tokens). Jaccard scored 0.783.
- **Symbol-coverage `|q∩s|/|s|`.** Ties Jaccard on the cases here but a short symbol matching a long
  query games it (covers few query tokens yet scores 1.0). Jaccard is symmetric — the safe pick.
- **Index-time subtoken storage** (a subtokens column). Rejected for now: triggers the
  schema-migration gate for no measured latency need at candidate-set sizes. Query-time is enough.

## Consequences

- Easier: canonical symbols surface; the deprecation signal pays off (staleness gain more than
  doubled). One escape-hatch flag.
- Harder / maintain: the two-pass splitter is duplicated in Python (`_subtokens`) and SQL
  (`_lex_expr`) — they must stay in sync (a unit test pins the Python side; the eval pins the SQL).
  Trailing digits stay attached to the last subtoken (e.g. `request2`), as the splitter documents.
- This changes a `code-graph-reference`-owned DECIDED formula, so that skill is updated in the same
  PR (change-control §3).

## Verification

Offline lexical-only MRR@10 over buried-canonical queries across django/flask/sqlalchemy
(VERIFIED 2026-07-08): binary 0.550, query-coverage 0.570, **Jaccard 0.783**.

Live, daemon restarted under each mode (VERIFIED 2026-07-08):

```
django ranking gate:        binary staleness gain +0.112 | jaccard +0.250   (general reg 0.000 both)
sqlalchemy canonical ranks: MetaData 44->1, Session 12->1, Table (cut)->4, create_engine 2->1
```

The `binary` flag reproduces the prior numbers exactly (escape hatch works). Full suite green.

## Rollback

Reversible with no re-index: set `CHGRAPH_RANK_LEXICAL=binary` to restore the placeholder. To fully
remove, delete `_lex_expr`'s jaccard branch and `_subtokens`; revert the `s_lex` line in
`code-graph-reference`.

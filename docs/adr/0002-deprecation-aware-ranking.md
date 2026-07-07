# 0002 — Deprecation-aware ranking: parse-time whole-symbol detection + flagged demotion signal

- Status: Proposed
- Date: 2026-07-08
- Class: retrieval-affecting + index-integrity-affecting (parser). No schema-migration: the flag
  is stored in the existing `nodes.properties` JSON blob — no DDL.
- Owner: git-evolution campaign (Phase-6 step-4 promotion)

## Context

The flagship git-evolution thesis is "live code beats stale code." Phase-6 validation
(`evals/runs/rank-2026-07-08.json`, PR #4) established on django@318a316a that **recency
cannot separate same-commit deprecate+replace twins** — the live/stale twins tie exactly on
recency and centrality (e.g. `StringAgg`: both rec=0.154, cen=0), so plain `hybrid`
(lex+rec+cen) yields **zero** staleness gain. A deprecation signal is the only discriminator.

The Phase-6 prototype detected deprecation with a body-text regex in the eval
(`is_deprecated_body`). That is too coarse: it flags any symbol whose body *mentions*
deprecation, not one that *is* deprecated. Verified false positives on django: `JsonResponse`,
`QuerySet`, `EmailMultiAlternatives` — all live symbols that merely deprecate a parameter/method.
`QuerySet` is the proof the regex is unshippable: flagged, ranks 5th under blind amid ~50
name-collisions, and a demotion ejects it from top-10 (VERIFIED, PR #4 goldens header).

## Decision

Detect **whole-symbol** deprecation at parse time and store it as a node property; expose it as
a ranking signal behind a default-off flag.

1. **Parser** (`chgraph.parse_python`): a Function/Class node is `deprecated` iff it
   unconditionally issues a deprecation warning in its own body (a `warnings.warn`/`warn` with a
   `*Deprecation*Warning`/`RemovedIn*Warning` category, or a `raise` of one, as a **direct** body
   statement — not nested in `if`/`try`/…, and not a comment), OR carries an `@deprecated`
   decorator, OR a `.. deprecated::` docstring. For a class, "its body" means its `__init__`.
   Stored as `nodes.properties = {"deprecated": true}`.
2. **Search** (`chgraph.search`): surface `dep = JSONExtractBool(properties,'deprecated')` in the
   `search_graph` response and add `w_dep · dep` to the hybrid score, where `w_dep` is the flag
   `chgraph_rank_deprecation_weight` (env `CHGRAPH_RANK_DEPRECATION_WEIGHT`), **default 0.0** —
   adding this changes no query result until an operator sets it.
3. **Eval** (`chgraph.eval`): the ranking eval reads `dep` from the real property (the body regex
   is retired — one home for "is this deprecated").

## Alternatives rejected

- **Keep the body-text regex.** Rejected: unshippable false-positive rate (QuerySet), and it
  would be a second, disagreeing home for deprecation detection (change-control N3).
- **New `deprecated UInt8` column on `nodes`.** Rejected: triggers the schema-migration gate
  (ADR + backup + rollback + re-index round-trip) for a boolean the reference-compatible
  `properties` JSON already accommodates. Reuse the column; dodge the migration.
- **Flip the shipped ranking default to demote in this PR.** Rejected for now: default stays
  current behavior (flag checklist); flipping the default is its own retrieval-affecting step
  after a second corpus confirms (see Consequences / retirement).

## Consequences

- Easier: the flagship staleness demotion is now driven by a precise, recency-independent signal
  computed once at index time; the eval tests the real pipeline, not a regex proxy.
- Harder / to maintain: the detector is a heuristic over django-style patterns
  (`warnings.warn`+category, `@deprecated`, `.. deprecated::`). Other ecosystems' conventions
  (e.g. `typing_extensions.deprecated`, framework-specific warnings) may need additions —
  tracked as its follow-on. Re-indexing is required for the property to appear (old indexes read
  `dep=0`).
- New flag `chgraph_rank_deprecation_weight` must be carried per the §5 checklist (below).

## Flag (change-control §5)

| Field | Value |
|---|---|
| Name | `chgraph_rank_deprecation_weight` (env `CHGRAPH_RANK_DEPRECATION_WEIGHT`) |
| Default | `0.0` — current behavior; adding the flag changes no result by itself |
| Label | `experimental` |
| Owner | git-evolution campaign |
| Re-verification | `CHGRAPH_RANK_DEPRECATION_WEIGHT=-0.20 python -m chgraph.eval.rank_run` → both gates PASS |
| Retirement | harden to a `-0.20` default once a second corpus (beyond django) confirms both gates; then this flag is removed |

## Verification

Detector on real django@318a316a (parse-time, VERIFIED 2026-07-08):
`BitAnd`, `StringAgg` (stale twins) → deprecated=True; `JsonResponse`, `HttpResponseRedirect`,
`QuerySet`, `EmailMultiAlternatives` (live) → False. The prior regex false positives are gone.

Phase-6 eval gate on the real node property (`evals/runs/rank-2026-07-08.json`, VERIFIED
2026-07-08), `dep=-0.20`, django@318a316a re-indexed with the new parser:

```
[staleness] n=4   blind=0.271  hybrid=0.271  hybrid+dep=0.383   gain +0.112  [bar >= +0.10] PASS
[general]   n=10  blind=1.000  hybrid=1.000  hybrid+dep=1.000   reg  +0.000  [bar <= 0.02]  PASS
```

`hybrid` (lex+rec+cen, no dep) == `blind` on staleness → recency contributes zero; the `dep`
signal is the sole discriminator. `-0.20` is 4× the body-regex prototype's fragile `-0.05` and
still holds because precise detection leaves every general-slice live symbol unflagged.

Index-sanity on the re-indexed corpus (daemon status, VERIFIED 2026-07-08): 2924 files,
46,356 nodes, `degraded_reasons: []` — density ~15.8 nodes/file, nowhere near the #333 collapse.

Unit + integration: parser detection tests (synthetic + real django BitAnd=True /
JsonResponse=QuerySet=False) + a chdb end-to-end test (`dep` surfaces; default 0.0 is a no-op;
flag demotes the stale twin). Full suite green.

**Second-corpus precision audit** (`evals/precision_audit.py`, `evals/runs/precision-2026-07-08.json`,
VERIFIED 2026-07-08) — the risk a default flip must retire is a false-positive demotion of live
code on some other codebase:

| repo | symbols | flagged | precision |
|---|---|---|---|
| django | 43,432 | 15 (0.03%) | 15/15 genuine whole-symbol deprecations (postgres aggregates, `savepoint`, `sanitize_address`, `forbid_multi_line_headers`, `SQLCompiler.quote_name_unless_alias`, `Action.__iter__/__getitem__`, `OrderableAggMixin.__init_subclass__`) — each hand-verified to carry an unconditional deprecation warn |
| flask  | 1,620  | 0 | zero false positives; all 4 of flask's deprecation sites are correctly excluded (guarded `if`, a `.. deprecated::` on a class *attribute* which isn't a symbol node, guarded `__getattr__`) |

On flask, `dep=-0.20` changes **no** ranking (0 flagged → the signal is a provable no-op across all
1,620 symbols) — an exhaustive no-regression result, not a sampled one. Caveat: flask has no
deprecation twins, so the *staleness benefit* remains django-only evidence; the second corpus
confirms precision / no-regression, which is the flip's risk.

The audit caught and fixed a real detector bug: `_has_deprecated_decorator` matched the word
"deprecated" anywhere in a decorator's text, so django's `@ignore_warnings(message="…is
deprecated")` (which SUPPRESSES a warning) false-flagged 3 live test classes. Fixed to match the
decorator's callable *name* (`deprecated`), never its arguments; regression test added.

## Rollback

Reversible. Set/leave `CHGRAPH_RANK_DEPRECATION_WEIGHT=0.0` (or unset) → ranking is byte-for-byte
the pre-ADR behavior with no re-index. To fully remove: drop the `dep` term + `JSONExtractBool`
from `search.py` and the detector from `parse_python.py`; existing `{"deprecated": true}`
properties become inert (ignored). No data migration needed either direction.

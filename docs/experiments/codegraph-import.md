# CodeGraph extraction and ClickHouse retrieval

**DECIDED:** use CodeGraph as an opt-in extraction frontend; keep publication,
queries and Git analytics in ClickHouse. [ADR-0005](../adr/0005-codegraph-extraction.md)
owns the source selector's defaults and promotion criteria.

## Use it

Install the supported CodeGraph CLI separately. Producer, schema and extraction
pins are registered in [`codegraph_import.py`](../../src/chgraph/codegraph_import.py).
After updating this checkout, restart any existing daemon with
`uv run chgraph daemon restart /path/to/repo`. An older daemon is rejected for
CodeGraph indexing so it cannot silently ignore the selected source.

```sh
uv run chgraph index /path/to/repo --source codegraph
uv run chgraph status /path/to/repo
```

The selected CLI initializes/rebuilds its own `.codegraph` index. chgraph reads
one completed SQLite snapshot, checks source hashes and Python parameter
bindings, then stages graph and Git data in ClickHouse. It publishes after
loading completes. Extraction runs outside the database worker, allowing reads
of the previous generation. Shutdown cancels the owned extractor process group.

Through MCP:

- `index_repository(source="codegraph")` queues the same operation.
- `explore(query="How does Service run?", max_chars=12000)` returns entry points,
  bounded CALLS evidence and fresh line-numbered source in one request.
- `change_impact(qualified_name="...")` returns bounded callers with ownership
  and churn, plus separate historical co-change. Co-change does not prove
  runtime impact or identify tests that must run.

`max_chars` caps context text, with metadata/warnings returned separately.
Identifier-token candidates use the existing lexical ranker; overlapping source
spans are merged. SQL time/memory/scan and graph node/edge limits bound work.
Freshness checks use native file hashes or the imported manifest, including
resolver dependencies such as re-exports.
Known-stale dependencies suppress CALLS evidence. Reaching a freshness budget
reports unverified dependencies; it does not certify freshness. Source reads
reject nonregular/oversized files and paths escaping the repository.

## Evidence and identity

Raw node fields, metadata, callsites, nullable provenance, file hashes and
unresolved references remain available. Multiple callsites are grouped in a
relationship's `properties.codegraph` array because the ClickHouse key identifies
source/target/kind, not callsite.

Python identities follow module-qualified names. For repeated definitions, the
final definition keeps the canonical name; earlier definitions keep distinct
upstream IDs. Overload declarations and noncallable aliases remain available
through explicit labels/names, while ordinary lookup prefers definitions.

Parameter calls incorrectly bound to imported functions are quarantined.
Unsupported Python syntax or binding scopes also quarantine that file's CALLS. Raw evidence and
validation reasons remain in snapshot metadata. Status reports rejected counts
and degraded reasons. This does not establish corpus-wide edge precision/recall.

Publication retains current and previous healthy generations without changing
table schema. [ADR-0006](../adr/0006-published-graph-generations.md) records recovery
semantics. Diagnostics resolve/display the published identity.

## Verify

```sh
uv run python evals/codegraph_compare.py --output /tmp/codegraph-fixture.json
uv run python evals/codegraph_corpus.py --output /tmp/codegraph-corpus.json
uv run pytest
```

The fixture runs in a disposable Git repo, checks independent positive/negative
expectations and ClickHouse traversal, then compares semantic state after
rename/sync versus rebuild. Exit 0 means its gates pass; 1 reports a quality or
convergence failure; 2 means it could not run. CLI tests skip when the supported
CLI is absent; SQLite/ClickHouse tests always run.

**VERIFIED, 2026-11-06:** the expanded fixture imports 27 nodes and retains 45
raw edge records. One false parameter call is quarantined, leaving 38 served
relationships. All seven expected calls are present, with no forbidden calls;
native extraction finds two. Sync/rebuild state matches and overload lookup
returns the implementation by its canonical name. These are component results.

The [corpus artifact](../../evals/runs/codegraph-corpus-2026-11-06.json) records
HEAD-native, working-tree-native and imported ranking results with pinned corpus
SHAs and per-query ranks. It measures exact-symbol MRR, not agent answer quality
or tokens. Initial integration exposed alias and overload lookup regressions;
current mapping/lookup rules address those cases. Broad agent quality, context
cost and performance superiority remain **OPEN**.

## Limits and rollback

The internal SQLite format is a pinned dependency without a full-graph export
API. Snapshots are buffered. This path performs explicit full upstream rebuilds;
automatic watching and incremental ClickHouse ingestion are not implemented.
Imported embeddings are not generated. Freshness budgets can leave large
repository dependencies unverified.
Checks cover indexed files; newly added source files require reindexing.
The component gates cover Python; other-language lookup remains unvalidated.

Reindex with `--source native` to switch back. Failed preparation/loading leaves
the prior published graph active. Explicit project deletion removes retained
generations and manifests. Back up before reverting to older builds that ignore
the publication pointer.

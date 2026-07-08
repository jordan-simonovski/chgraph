# 0004 — Vector semantic signal: fastembed bge-small over name+docstring

- Status: Accepted
- Date: 2026-07-08
- Class: retrieval-affecting + infra-dependency (optional). The parser docstring extraction and
  embeddings population ride under retrieval-affecting (they change what a query returns) and pass
  index-sanity. No schema-migration — the `chgraph.embeddings` table already exists (campaign
  Phase-5 DDL).
- Owner: git-evolution campaign (Phase-5 vector signal)

## Context

Lexical (ADR-0003) and deprecation (ADR-0002) ranking are landed, but the candidate filter is
lexical-substring-gated: a natural-language query an agent actually asks ("abort the request with
an http error") substring-matches no symbol name, so the pool is empty and nothing is returned.
The campaign's rank-2 signal is vector similarity for exactly this semantic recall. `s_vec` and the
`embeddings` table are DECIDED in code-graph-reference; this ADR picks the model, the embed-text,
and the candidate-set change, and validates them.

## Decision

1. **Model:** `fastembed` + `BAAI/bge-small-en-v1.5` (384-dim, ONNX — no torch, no API key, CPU).
   OPTIONAL dependency (`embeddings` group). `chgraph/embeddings.py` loads it lazily once per
   process; `available()` gates everything so core chgraph runs unchanged when it is absent.
2. **Embed-text:** `subtokens(name) + ". " + first-paragraph docstring`, built at index time. The
   parser extracts the docstring (`parse_python._docstring`, transient `doc` field the indexer pops
   — never persisted to `nodes`). Embeddings populated into `chgraph.embeddings` on index,
   TRUNCATE-on-reindex like nodes/edges.
3. **Search:** when a query is present and the project is embedded, the candidate set becomes
   `lexical-substring-match UNION vec_top` (nearest `VEC_CANDIDATES=100` by `cosineDistance`), and
   `s_vec = 1 - cosineDistance(vec, qvec)` enters the hybrid score at `W['vec']=0.30`. Query embedded
   at search time. Brute force — no ANN (36ms/46k×384, VERIFIED; HNSW is compiled out of chdb).
   Flag `chgraph_rank_vector` (env `CHGRAPH_RANK_VECTOR` = `on`|`off`, default `on`).

## Alternatives rejected

- **model2vec static embeddings (potion-base).** Lightest, but a bake-off on flask showed it is
  soft-lexical (bag-of-words average) — "build a url for an endpoint" ranked `Request.endpoint` over
  `url_for`. bge-small (real transformer) put `url_for` at rank 1. Semantics is the whole point.
- **sentence-transformers.** Pulls torch (~GB); fastembed's ONNX runtime is far lighter for the same
  bge model.
- **Embedding API (OpenAI/Voyage/Anthropic).** Needs a key + per-call $ + network on every query;
  a local CPU model has none of those.
- **HNSW / vector_similarity index.** Compiled out of chdb (VERIFIED, chdb-reference); and brute
  force is already 36ms at django scale, so unneeded.
- **Identifier-only embed-text (no docstring).** Bake-off: marginal (url_for rank 4). The docstring
  is the NL description that makes semantic matching work; extracting it at parse time is cheap.

## Consequences

- Easier: NL/semantic queries the lexical filter misses entirely now return the right symbol.
- Harder / maintain: an optional heavy-ish model download (~130MB, first run); ~1-2s model load in
  the daemon; re-index required to populate embeddings (old indexes read `s_vec=0`, signal inert).
  The two-pass subtoken splitter is shared with search (`_subtokens`).
- The `embeddings` dep is optional — CI without it still passes (vector inert, `available()` false).

## Verification (VERIFIED 2026-07-08)

- Model bake-off (flask, name+docstring): bge-small put `url_for`/`send_file`/`register_blueprint`
  at rank 1 for NL queries; model2vec did not.
- Brute-force `cosineDistance` over 46,000×384 → top-10 in **36ms** (django scale).
- Full pipeline (in-process, flask): index → 1,578 embeddings, 0 degraded; NL queries return the
  right symbol at rank 1.
- **Semantic goldens** (`evals/semantic_goldens.yaml`, n=4, flask), MRR@10 with the daemon restarted
  under each mode: `CHGRAPH_RANK_VECTOR=off` → **0.000** (lexical finds none), default `on` → **1.000**.
- Unit + integration: a deterministic chdb test (stubbed model) proves the vector candidate-union
  surfaces a non-lexical match and the flag disables it. Full suite 88 green.

## Rollback

Reversible, no re-index: `CHGRAPH_RANK_VECTOR=off` restores the pre-ADR lexical-gated search
(`s_vec=0`, candidate set = lexical only). To fully remove: drop the vector CTE/join/`s_vec` from
`search.py`, the embedding step from `indexer.py`, and `embeddings.py`; the `embeddings` table
becomes unused. Uninstall the optional `embeddings` dep group. No data migration either direction.

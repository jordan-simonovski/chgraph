"""Optional symbol embeddings (campaign Phase-5 vector signal).

fastembed + BAAI/bge-small-en-v1.5 (384-dim, ONNX — no torch, no API key, CPU). Chosen over
static model2vec (too soft-lexical) after a flask semantic-ranking bake-off (ADR-0004). The
dependency is OPTIONAL: if fastembed isn't installed the indexer skips embeddings and the vector
signal stays inert — core chgraph runs unchanged. Model loads lazily and once per process
(~1-2s); brute-force cosineDistance over 46k×384 is ~36ms (VERIFIED 2026-07-08), so no ANN index
is needed (HNSW is compiled out of chdb anyway — chdb-reference).
"""
from __future__ import annotations

MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384

_model = None


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of documents. Returns unit-normalized 384-float vectors.
    fastembed's bge output is already L2-normalized; cosineDistance handles the rest."""
    if not texts:
        return []
    return [v.tolist() for v in _get_model().embed(texts)]


def embed_query(text: str) -> list[float]:
    """Embed a single query string -> 384 floats."""
    return next(_get_model().query_embed([text])).tolist()

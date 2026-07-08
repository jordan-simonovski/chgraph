"""search_graph: candidate filter + hybrid ranking. Three scored signals (weights are the
campaign's DECIDED starting defaults, W below): identifier-aware subtoken-Jaccard lexical
(ADR-0003, flag chgraph_rank_lexical), vector semantic similarity over name+docstring
embeddings (ADR-0004, flag chgraph_rank_vector), and a parse-time `dep` demotion (ADR-0002,
flag chgraph_rank_deprecation_weight), plus recency + centrality. All retrieval-affecting
(eval gate, INV-4)."""
import os
from dataclasses import dataclass

from chgraph import embeddings
from chgraph.evolution import DEFAULT_HALF_LIFE_DAYS, _q
from chgraph.store import Store
from chgraph.text import subtokens

VEC_CANDIDATES = 100   # how many nearest-neighbour symbols the vector path adds to the pool

W = {"lex": 0.35, "vec": 0.30, "rec": 0.20, "cen": 0.15}  # one code home; doc home: campaign Phase 5


def _lexical_mode() -> str:
    """Lexical relevance signal.

    Flag: chgraph_rank_lexical (env CHGRAPH_RANK_LEXICAL) = "jaccard" | "binary"
    Default: "jaccard" — subtoken Jaccard |q∩s|/|q∪s| (identifier-aware). "binary" restores
      the pre-ADR-0003 placeholder (1.0 name-substring / 0.5 qn-only), an escape hatch.
    Label: prod. Owner: git-evolution campaign.
    Validated (ADR-0003, run rank-2026-07-08): lifts buried canonicals (MetaData rank 25->1,
      Session 29->3) and raises django staleness 0.383->0.625, general flat 1.000.
    Re-verify: python -m chgraph.eval.rank_run ; CHGRAPH_RANK_LEXICAL=binary python -m chgraph.eval.rank_run
    """
    mode = os.environ.get("CHGRAPH_RANK_LEXICAL", "jaccard").lower()
    return mode if mode in ("jaccard", "binary") else "jaccard"


def _vector_on(store: Store, project: str, query: str | None) -> bool:
    """Vector signal active? Needs a text query, fastembed installed, the flag on, and this
    project actually embedded.

    Flag: chgraph_rank_vector (env CHGRAPH_RANK_VECTOR) = on | off
    Default: on — but inert until the optional `embeddings` dep is installed AND the repo is
      re-indexed (both deliberate acts), so merging changes no query result by itself.
    Label: prod. Owner: git-evolution campaign.
    Validated (ADR-0004, semantic goldens flask): MRR@10 0.000 (off) -> 1.000 (on).
    Re-verify: CHGRAPH_RANK_VECTOR=off vs on over evals/semantic_goldens.yaml (ADR-0004 §Verification).
    Retire: flag retained as an `off` escape hatch (not removed) — reversible disable, no re-index.
    """
    if not query or os.environ.get("CHGRAPH_RANK_VECTOR", "on").lower() == "off":
        return False
    if not embeddings.available():
        return False
    return bool(store.rows(
        f"SELECT 1 FROM chgraph.embeddings WHERE project = {_q(project)} LIMIT 1"))


def _dep_weight() -> float:
    """Weight applied to the parse-time `deprecated` node property in the hybrid score.

    Flag: chgraph_rank_deprecation_weight (env CHGRAPH_RANK_DEPRECATION_WEIGHT)
    Default: -0.20 — demote deprecated symbols (ADR-0002, hardened after the 2-corpus staleness
      confirmation). Set to 0.0 to disable.
    Label: prod. Owner: git-evolution campaign.
    Validated (run rank-2026-07-08, subtoken-Jaccard lexical): combined django+sqlalchemy
      staleness gain +0.361, general regression 0.000; precision 3-corpus clean (0 false positives).
    Re-verify: python -m chgraph.eval.rank_run ; CHGRAPH_RANK_DEPRECATION_WEIGHT=0.0 python -m chgraph.eval.rank_run
    """
    try:
        return float(os.environ.get("CHGRAPH_RANK_DEPRECATION_WEIGHT", "-0.20"))
    except ValueError:
        return -0.20


def _lex_expr(query: str | None) -> str:
    """SQL for the lexical signal over each candidate row (see _lexical_mode)."""
    if not query:
        return "0.0"
    if _lexical_mode() == "binary":
        return f"if(positionCaseInsensitive(n.name, {_q(query)}) > 0, 1.0, 0.5)"
    qtoks = subtokens(query)
    if not qtoks:                       # query had no alphanumerics — fall back to substring
        return f"if(positionCaseInsensitive(n.name, {_q(query)}) > 0, 1.0, 0.5)"
    qarr = "[" + ",".join(_q(t) for t in qtoks) + "]"
    # per-row name subtokens via the two-pass boundary split (mirrors text.subtokens)
    nst = ("arrayFilter(x -> x != '', splitByRegexp('[^A-Za-z0-9]+', lower("
           "replaceRegexpAll(replaceRegexpAll(n.name, '([A-Z]+)([A-Z][a-z])', '\\1 \\2'), "
           "'([a-z0-9])([A-Z])', '\\1 \\2'))))")
    jaccard = (f"length(arrayIntersect({nst}, {qarr})) / "
               f"greatest(length(arrayDistinct(arrayConcat({nst}, {qarr}))), 1)")
    # qn-only matches (share no name subtoken) keep a low floor so they rank below real hits
    return (f"greatest({jaccard}, "
            f"if(positionCaseInsensitive(n.qualified_name, {_q(query)}) > 0, 0.15, 0.0))")


@dataclass
class SearchPage:
    items: list[dict]
    total: int
    has_more: bool


def search_graph(store: Store, project: str, query: str | None = None,
                 name_pattern: str | None = None, label: str | None = None,
                 limit: int = 200, offset: int = 0) -> SearchPage:
    if not (query or name_pattern or label):
        raise ValueError("search_graph needs at least one of: query, name_pattern, label")

    vec_on = _vector_on(store, project, query)
    qlit = ""                          # query-embedding literal, reused by the CTE, join, and score
    if vec_on:
        qlit = "[" + ",".join(str(x) for x in embeddings.embed_query(query)) + "]::Array(Float32)"

    conds = [f"n.project = {_q(project)}"]
    if query:
        lex_match = (f"positionCaseInsensitive(n.name, {_q(query)}) > 0"
                     f" OR positionCaseInsensitive(n.qualified_name, {_q(query)}) > 0")
        # vector path widens the candidate set to nearest-neighbour symbols the query never
        # lexically matches — that is the whole point of the vector signal.
        if vec_on:
            lex_match += " OR n.qualified_name IN (SELECT qualified_name FROM vec_top)"
        conds.append(f"({lex_match})")
    if name_pattern:
        conds.append(f"match(n.name, {_q(name_pattern)})")
    if label:
        conds.append(f"n.label = {_q(label)}")
    where = " AND ".join(conds)
    lex_expr = _lex_expr(query)
    w_dep = _dep_weight()

    # FINAL on the embeddings reads mirrors the nodes/edges reads: TRUNCATE-on-reindex keeps one
    # row per (project, qualified_name) today, but FINAL keeps s_vec correct if a future
    # incremental path ever writes a bumped version without a full OPTIMIZE (INV-5 consistency).
    # vec_cte_body has no trailing punctuation; the two WITH sites add their own separator.
    vec_cte_body = (f"vec_top AS (SELECT qualified_name FROM chgraph.embeddings FINAL "
                    f"WHERE project = {_q(project)} ORDER BY cosineDistance(vec, {qlit}) ASC "
                    f"LIMIT {VEC_CANDIDATES})" if vec_on else "")
    vec_top_cte = f"{vec_cte_body},\n        " if vec_on else ""   # chained ahead of `recency`
    # arrayResize makes the vector always EMBED_DIM long so cosineDistance never sees mismatched
    # sizes — ClickHouse's if() does NOT short-circuit it, so a LEFT-JOIN-missing empty vec would
    # otherwise raise SIZES_OF_ARRAYS_DONT_MATCH. The if() still zeroes s_vec for those rows.
    vec_col = (f"round(if(length(e.vec) = {embeddings.EMBED_DIM}, "
               f"1 - cosineDistance(arrayResize(e.vec, {embeddings.EMBED_DIM}), {qlit}), 0.0), 3)"
               if vec_on else "0.0")
    vec_join = ("LEFT JOIN chgraph.embeddings AS e FINAL "
                "ON n.qualified_name = e.qualified_name AND n.project = e.project" if vec_on else "")

    sql = f"""
    WITH
        {vec_top_cte}recency AS (
            SELECT path,
                   exp(-log(2) / {float(DEFAULT_HALF_LIFE_DAYS)} *
                       dateDiff('day', max(committed_at), now())) AS r
            FROM chgraph.git_file_changes WHERE project = {_q(project)} GROUP BY path
        ),
        degree AS (
            SELECT target AS qn, count() AS deg
            FROM chgraph.edges FINAL
            WHERE project = {_q(project)} AND type = 'CALLS' GROUP BY qn
        ),
        maxdeg AS (SELECT greatest(max(deg), 1) AS m FROM degree)
    SELECT
        n.qualified_name AS qualified_name, n.label AS label, n.name AS name,
        n.file_path AS file_path, n.start_line AS start_line, n.end_line AS end_line,
        round({lex_expr}, 3)                                    AS lex,
        {vec_col}                                               AS vec,
        round(coalesce(r.r, 0), 3)                              AS rec,
        round(coalesce(d.deg, 0) / (SELECT m FROM maxdeg), 3)   AS cen,
        toUInt8(JSONExtractBool(n.properties, 'deprecated'))    AS dep,
        round({W['lex']} * lex + {W['vec']} * vec + {W['rec']} * rec + {W['cen']} * cen
              + {w_dep} * dep, 4)                               AS score
    FROM chgraph.nodes AS n FINAL
    {vec_join}
    LEFT JOIN recency AS r ON n.file_path = r.path
    LEFT JOIN degree AS d ON n.qualified_name = d.qn
    WHERE {where}
    ORDER BY score DESC, qualified_name
    LIMIT {int(limit)} OFFSET {int(offset)}
    """
    count_cte = f"WITH {vec_cte_body}\n" if vec_on else ""
    count_sql = f"{count_cte}SELECT count() AS n FROM chgraph.nodes AS n FINAL WHERE {where}"
    rows = store.rows(sql)
    total = store.rows(count_sql)[0]["n"]
    return SearchPage(items=rows, total=total, has_more=offset + len(rows) < total)

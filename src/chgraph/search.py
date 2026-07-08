"""search_graph: candidate filter + hybrid-lite ranking.
Ranking shape verified in chgraph-git-evolution-campaign Phase 5; weights are the
campaign's DECIDED starting defaults. Lexical is identifier-aware subtoken Jaccard
(ADR-0003, flag chgraph_rank_lexical); the deprecated `dep` signal comes from the
parse-time node property (ADR-0002). Both are retrieval-affecting (eval gate, INV-4)."""
import os
import re
from dataclasses import dataclass

from chgraph.evolution import DEFAULT_HALF_LIFE_DAYS, _q
from chgraph.store import Store

W = {"lex": 0.35, "vec": 0.30, "rec": 0.20, "cen": 0.15}  # one code home; doc home: campaign Phase 5

# Identifier subtoken splitter — the VERIFIED RE2-safe two-pass form (code-graph-reference):
# split acronym->word boundaries then lower->Upper boundaries, then on non-alphanumerics.
_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")


def _subtokens(s: str) -> list[str]:
    s = _CAMEL.sub(r"\1 \2", _ACRONYM.sub(r"\1 \2", s))
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


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


def _dep_weight() -> float:
    """Weight applied to the parse-time `deprecated` node property in the hybrid score.

    Flag: chgraph_rank_deprecation_weight (env CHGRAPH_RANK_DEPRECATION_WEIGHT)
    Default: 0.0 — current behavior; adding this flag changes no query result by itself.
    Label: experimental. Owner: git-evolution campaign.
    Validated: eval run rank-2026-07-08 (staleness +0.112, general reg 0.000) at -0.05 on the
      body-regex prototype; the parse-time detector is at least as clean (no false positives).
    Re-verify: CHGRAPH_RANK_DEPRECATION_WEIGHT=-0.05 python -m chgraph.eval.rank_run
    Retire: harden to a -0.05 default once a second corpus confirms (campaign Phase-6 step-4).
    """
    try:
        return float(os.environ.get("CHGRAPH_RANK_DEPRECATION_WEIGHT", "0.0"))
    except ValueError:
        return 0.0


def _lex_expr(query: str | None) -> str:
    """SQL for the lexical signal over each candidate row (see _lexical_mode)."""
    if not query:
        return "0.0"
    if _lexical_mode() == "binary":
        return f"if(positionCaseInsensitive(n.name, {_q(query)}) > 0, 1.0, 0.5)"
    qtoks = _subtokens(query)
    if not qtoks:                       # query had no alphanumerics — fall back to substring
        return f"if(positionCaseInsensitive(n.name, {_q(query)}) > 0, 1.0, 0.5)"
    qarr = "[" + ",".join(_q(t) for t in qtoks) + "]"
    # per-row name subtokens via the two-pass boundary split (mirrors _subtokens)
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

    conds = [f"n.project = {_q(project)}"]
    if query:
        conds.append(f"(positionCaseInsensitive(n.name, {_q(query)}) > 0"
                     f" OR positionCaseInsensitive(n.qualified_name, {_q(query)}) > 0)")
    if name_pattern:
        conds.append(f"match(n.name, {_q(name_pattern)})")
    if label:
        conds.append(f"n.label = {_q(label)}")
    where = " AND ".join(conds)
    lex_expr = _lex_expr(query)
    w_dep = _dep_weight()

    sql = f"""
    WITH
        recency AS (
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
        round(coalesce(r.r, 0), 3)                              AS rec,
        round(coalesce(d.deg, 0) / (SELECT m FROM maxdeg), 3)   AS cen,
        toUInt8(JSONExtractBool(n.properties, 'deprecated'))    AS dep,
        round({W['lex']} * lex + {W['rec']} * rec + {W['cen']} * cen
              + {w_dep} * dep, 4)                               AS score
    FROM chgraph.nodes AS n FINAL
    LEFT JOIN recency AS r ON n.file_path = r.path
    LEFT JOIN degree AS d ON n.qualified_name = d.qn
    WHERE {where}
    ORDER BY score DESC, qualified_name
    LIMIT {int(limit)} OFFSET {int(offset)}
    """
    count_sql = f"""
    SELECT count() AS n FROM chgraph.nodes AS n FINAL WHERE {where}
    """
    rows = store.rows(sql)
    total = store.rows(count_sql)[0]["n"]
    return SearchPage(items=rows, total=total, has_more=offset + len(rows) < total)

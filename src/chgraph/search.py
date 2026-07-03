"""search_graph: candidate filter + hybrid-lite ranking.
Ranking shape verified in chgraph-git-evolution-campaign Phase 5; weights are the
campaign's DECIDED starting defaults. Lexical is the placeholder binary signal —
upgrading it is retrieval-affecting (eval gate, INV-4).
eval: not yet run — harness not built."""
from dataclasses import dataclass

from chgraph.evolution import DEFAULT_HALF_LIFE_DAYS, _q
from chgraph.store import Store

W = {"lex": 0.35, "vec": 0.30, "rec": 0.20, "cen": 0.15}  # one code home; doc home: campaign Phase 5


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
    lex_expr = (f"if(positionCaseInsensitive(n.name, {_q(query)}) > 0, 1.0, 0.5)"
                if query else "0.0")

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
        round({W['lex']} * lex + {W['rec']} * rec + {W['cen']} * cen, 4) AS score,
        count() OVER () AS _total
    FROM chgraph.nodes AS n FINAL
    LEFT JOIN recency AS r ON n.file_path = r.path
    LEFT JOIN degree AS d ON n.qualified_name = d.qn
    WHERE {where}
    ORDER BY score DESC, qualified_name
    LIMIT {int(limit)} OFFSET {int(offset)}
    """
    rows = store.rows(sql)
    total = rows[0]["_total"] if rows else 0
    for r in rows:
        r.pop("_total", None)
    return SearchPage(items=rows, total=total, has_more=offset + len(rows) < total)

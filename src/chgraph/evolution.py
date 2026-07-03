"""Evolution metrics. SQL verified in chgraph-git-evolution-campaign Phase 3.
DECIDED starting defaults (campaign Phase 5 is their one doc home): half-life 30d, support floor 2."""
from chgraph.store import Store

DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_MIN_SUPPORT = 2


def _q(s: str) -> str:
    """Escape a string literal for ClickHouse SQL."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def churn(store: Store, project: str) -> list[dict]:
    return store.rows(f"""
        SELECT path, count() AS commits,
               sum(additions + deletions) AS churn,
               max(committed_at) AS last_touched
        FROM chgraph.git_file_changes
        WHERE project = {_q(project)}
        GROUP BY path
        ORDER BY churn DESC, path""")


def coupling(store: Store, project: str, min_support: int = DEFAULT_MIN_SUPPORT) -> list[dict]:
    return store.rows(f"""
        WITH pairs AS (
            SELECT a.path AS file_a, b.path AS file_b, count() AS support
            FROM chgraph.git_file_changes AS a
            INNER JOIN chgraph.git_file_changes AS b
                ON a.hash = b.hash AND a.project = b.project
            WHERE a.project = {_q(project)} AND a.path < b.path
            GROUP BY file_a, file_b
        ),
        totals AS (
            SELECT path, uniqExact(hash) AS n_commits
            FROM chgraph.git_file_changes WHERE project = {_q(project)} GROUP BY path
        )
        SELECT file_a, file_b, support,
               round(support / ta.n_commits, 3) AS conf_a_to_b,
               round(support / tb.n_commits, 3) AS conf_b_to_a
        FROM pairs
        INNER JOIN totals AS ta ON pairs.file_a = ta.path
        INNER JOIN totals AS tb ON pairs.file_b = tb.path
        WHERE support >= {int(min_support)}
        ORDER BY support DESC, greatest(conf_a_to_b, conf_b_to_a) DESC""")


def ownership(store: Store, project: str) -> list[dict]:
    return store.rows(f"""
        SELECT path,
               argMax(author_email, cnt) AS top_author,
               round(max(cnt) / sum(cnt), 3) AS top_author_share,
               sum(cnt) AS total_commits
        FROM (
            SELECT path, author_email, count() AS cnt
            FROM chgraph.git_file_changes
            WHERE project = {_q(project)}
            GROUP BY path, author_email
        )
        GROUP BY path
        ORDER BY top_author_share DESC, path""")


def recency(store: Store, project: str, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> list[dict]:
    return store.rows(f"""
        SELECT path,
               max(committed_at) AS last_touched,
               dateDiff('day', max(committed_at), now()) AS age_days,
               round(exp(-log(2) / {float(half_life_days)} *
                     dateDiff('day', max(committed_at), now())), 4) AS recency_score
        FROM chgraph.git_file_changes
        WHERE project = {_q(project)}
        GROUP BY path
        ORDER BY recency_score DESC""")


def refresh_file_evolution(store: Store, project: str, version: int) -> int:
    store.exec(f"""
        INSERT INTO chgraph.file_evolution
        SELECT project, path,
               count()                          AS commit_count,
               sum(additions + deletions)       AS churn,
               max(committed_at)                AS last_commit_at,
               argMax(author_email, cnt_by_author) AS top_author,
               max(cnt_by_author) / count()     AS top_author_share,
               exp(-log(2)/{float(DEFAULT_HALF_LIFE_DAYS)} *
                   dateDiff('day', max(committed_at), now())) AS recency_score,
               {int(version)} AS version
        FROM (
            SELECT project, path, committed_at, additions, deletions, author_email,
                   count() OVER (PARTITION BY project, path, author_email) AS cnt_by_author
            FROM chgraph.git_file_changes WHERE project = {_q(project)}
        )
        GROUP BY project, path""")
    return store.rows(
        f"SELECT count() AS n FROM chgraph.file_evolution FINAL WHERE project = {_q(project)}"
    )[0]["n"]

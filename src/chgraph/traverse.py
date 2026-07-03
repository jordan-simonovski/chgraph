"""Guarded traversal. INV-2: depth cap AND visited-path guard on EVERY recursive query —
ClickHouse recursive CTEs have no built-in cycle detection."""
from chgraph.evolution import _q
from chgraph.store import Store

MAX_DEPTH = 10


def trace_path(store: Store, project: str, qualified_name: str,
               direction: str = "callees", depth: int = 5) -> list[dict]:
    if direction not in ("callees", "callers"):
        raise ValueError(f"direction must be callees|callers, got {direction!r}")
    depth = max(1, min(int(depth), MAX_DEPTH))
    src, dst = ("source", "target") if direction == "callees" else ("target", "source")
    return store.rows(f"""
        WITH RECURSIVE walk AS (
            SELECT {_q(qualified_name)} AS node,
                   [{_q(qualified_name)}] AS path, 0 AS depth
            UNION ALL
            SELECT e.{dst}, arrayPushBack(w.path, e.{dst}), w.depth + 1
            FROM walk AS w
            JOIN (SELECT source, target FROM chgraph.edges FINAL
                  WHERE project = {_q(project)} AND type = 'CALLS') AS e
                 ON e.{src} = w.node
            WHERE w.depth < {depth}            -- mandatory depth cap (INV-2)
              AND NOT has(w.path, e.{dst})     -- mandatory cycle guard (INV-2)
        )
        SELECT node, path, depth FROM walk ORDER BY depth, node""")

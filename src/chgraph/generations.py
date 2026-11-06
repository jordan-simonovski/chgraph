"""Publish a complete graph by switching its project identity on the sole DB worker."""
import json

from chgraph.evolution import _q
from chgraph.gitingest import _batch_load
from chgraph.store import Store

TABLES = ("nodes", "edges", "embeddings", "git_commits", "git_file_changes", "file_evolution")


def active_project(store: Store, project: str) -> str:
    rows = store.rows("SELECT value FROM chgraph.meta FINAL WHERE key='active_project'")
    if not rows:
        return project
    pointer = json.loads(rows[0]["value"])
    if pointer["logical_project"] == project:
        return pointer["project"]
    if project in (pointer["project"], pointer.get("previous")):
        return project
    raise ValueError("published graph belongs to another repository")


def publish_generation(store: Store, project: str, generation: str, source: str) -> list[str]:
    previous = active_project(store, project)
    store.checkpoint()  # Persist every table before publishing a durable pointer.
    version = store.rows(
        "SELECT coalesce(max(version), 0) + 1 AS v FROM chgraph.meta WHERE key='active_project'"
    )[0]["v"]
    _batch_load(store, "chgraph.meta", [{
        "key": "active_project", "version": version,
        "value": json.dumps({"logical_project": project, "project": generation,
                             "previous": previous, "source": source}),
    }])
    store.checkpoint()
    # Keep one healthy predecessor. Failed staging generations are safe to discard.
    owned = f"(project={_q(project)} OR startsWith(project, {_q(project + '@')}))"
    obsolete = f"{owned} AND project NOT IN ({_q(generation)}, {_q(previous)})"
    try:
        for table in TABLES:
            store.delete_rows(f"chgraph.{table}", obsolete)
        store.delete_rows("chgraph.meta", f"""
            (key={_q('codegraph_snapshot:' + project)} OR startsWith(key, {_q('codegraph_snapshot:' + project + '@')}))
            AND key NOT IN ({_q('codegraph_snapshot:' + generation)}, {_q('codegraph_snapshot:' + previous)})
            """)
    except Exception as exc:
        return [f"graph published; old-generation cleanup failed: {exc}"]
    return []

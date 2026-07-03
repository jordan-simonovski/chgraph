"""git log --numstat -> chgraph.git_commits / git_file_changes.
Prototype verified in chgraph-git-evolution-campaign Phase 2. Batch loads only (INV-5)."""
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from chgraph.store import Store

_FMT = "C%x09%H%x09%an%x09%ae%x09%at%x09%s"
_BRACE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


def _expand_rename(path: str):
    """'src/{ => core}/legacy.py' -> ('src/legacy.py', 'src/core/legacy.py')."""
    m = _BRACE.match(path)
    if m:
        pre, old_mid, new_mid, post = m.groups()
        return (pre + old_mid + post).replace("//", "/"), (pre + new_mid + post).replace("//", "/")
    if " => " in path:
        old, new = path.split(" => ", 1)
        return old, new
    return None, path


@dataclass(frozen=True)
class GitIngestCounts:
    commits: int
    file_changes: int
    renames: int


def _git(repo_root: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo_root, "-c", "core.quotePath=false", *args],
        check=True, capture_output=True, text=True
    ).stdout


def parse_git_log(repo_root: str, project: str) -> tuple[list[dict], list[dict]]:
    out = _git(repo_root, "log", "--no-merges", "-M", f"--pretty=format:{_FMT}", "--numstat")
    commits, changes, cur = [], [], None
    for line in out.splitlines():
        if line.startswith("C\t"):
            _, h, an, ae, at, msg = line.split("\t", 5)
            cur = {"project": project, "hash": h, "author_name": an,
                   "author_email": ae, "committed_at": int(at), "message": msg}
            commits.append(cur)
        elif line.strip():
            add, dele, path = line.split("\t", 2)
            old, new = _expand_rename(path)
            changes.append({
                "project": project, "hash": cur["hash"],
                "committed_at": cur["committed_at"], "author_email": cur["author_email"],
                "path": new, "old_path": old or "",
                "additions": 0 if add == "-" else int(add),   # numstat prints '-' for binary
                "deletions": 0 if dele == "-" else int(dele),
                "is_rename": 1 if old else 0,
            })
    return commits, changes


def _batch_load(store: Store, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        tmp = f.name
    try:
        store.exec(f"INSERT INTO {table} SELECT * FROM file('{tmp}', 'JSONEachRow')")
    finally:
        os.unlink(tmp)


def ingest_git(store: Store, project: str, repo_root: str) -> GitIngestCounts:
    commits, changes = parse_git_log(repo_root, project)
    # ponytail: TRUNCATE-then-reload is safe because one data dir == one project
    # (chgraph-run-and-operate §1); incremental append is a later, benchmarked change.
    store.exec("TRUNCATE TABLE chgraph.git_commits")
    store.exec("TRUNCATE TABLE chgraph.git_file_changes")
    _batch_load(store, "chgraph.git_commits", commits)
    _batch_load(store, "chgraph.git_file_changes", changes)
    return GitIngestCounts(
        commits=len(commits),
        file_changes=len(changes),
        renames=sum(c["is_rename"] for c in changes),
    )


def verify_git_counts(repo_root: str, counts: GitIngestCounts) -> list[str]:
    """The campaign Phase-2c discriminating gate. Empty list == pass."""
    reasons = []
    truth_commits = int(_git(repo_root, "rev-list", "--no-merges", "--count", "HEAD").strip())
    numstat = _git(repo_root, "log", "--no-merges", "-M", "--pretty=format:", "--numstat")
    truth_changes = sum(1 for line in numstat.splitlines() if line.strip())
    if counts.commits != truth_commits:
        reasons.append(f"git ingest: {counts.commits} commits ingested, git says {truth_commits}")
    if counts.file_changes != truth_changes:
        reasons.append(f"git ingest: {counts.file_changes} file changes ingested, git says {truth_changes}")
    return reasons

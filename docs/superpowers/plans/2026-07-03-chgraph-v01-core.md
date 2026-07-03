# chgraph v0.1 Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **REQUIRED READING per task:** each task lists project skills (in `.claude/skills/`) that are the authoritative source for its facts. Read them before coding. When this plan and a skill disagree, the skill wins — stop and report the conflict instead of improvising.

**Goal:** A working chgraph v0.1: a Python daemon that owns a chdb data directory, indexes a repo's Python symbols and full git history into a knowledge graph, and serves reference-compatible MCP tools (with recency-aware ranking) to any number of concurrent agent sessions via a thin stdio shim.

**Architecture:** Single daemon per project owns the chdb Session (chdb's data-dir lock is exclusive, even read-only — verified); MCP stdio shims relay tool calls over a unix socket and never import chdb. Storage is the canonical ReplacingMergeTree(version) nodes/edges schema plus git-evolution side tables; all reads are FINAL-correct, all writes are batch.

**Tech Stack:** Python 3.12 (uv-managed `.venv`), chdb==4.2.0 (resolves chdb-core 26.5.0), mcp (FastMCP) ≥1.28, tree-sitter + tree-sitter-python, pydantic v2, pytest.

## Global Constraints

Copied from the project skills — every task's requirements implicitly include these:

- **Pins:** `chdb==4.2.0` exactly (`chdb==26.5.0` does not exist on PyPI; `chdb.__version__` will report `26.5.0` — that is correct, see build-and-env trap T1). Python `>=3.12` (system python3 is 3.9.6 — never use it).
- **INV-1:** Exactly one process opens a chdb data dir, always through the daemon. Tests use fresh `tmp_path` dirs and close their Session. One Session per process — a second `Session()` in the same process fails even with a different path.
- **INV-2:** Every recursive traversal has a depth cap AND a `has(path, x)` visited guard. No exceptions.
- **INV-3:** `index_status` reflects reality. `degraded` is a first-class state with machine-readable `reasons[]`, never folded into `indexed`.
- **INV-5:** Batch writes only (JSONEachRow via `file()` or multi-row INSERT). Every read of a ReplacingMergeTree table uses `FINAL` (or version-aware aggregation). Never row-by-row upserts.
- **INV-6:** The schema ships with a schema-version gate; the daemon refuses data dirs it doesn't understand.
- **Ranking changes are retrieval-affecting:** the eval harness does not exist yet, so every PR touching ranking carries the literal line `eval: not yet run — harness not built` (validation-and-qa §3). Do not claim quality.
- **MCP stdout is protocol:** no `print()` anywhere in shim or daemon-spawned-by-shim code paths; log to stderr/files only.
- **Fork-safety:** the daemon may exec external binaries (`git`) via `subprocess`, but must never spawn a Python child that imports/queries chdb (build-and-env trap T5).
- **The shim never imports chdb** (enforced by test in Task 12).
- **Commits:** commit after each task with the message given. If `git commit` fails on GPG signing, STOP and report — do not disable signing, do not use `--no-gpg-sign`.
- **Writes:** code in `src/chgraph/`, tests in `tests/`, fixtures in `tests/fixtures/`, docs in `docs/`. Never write into `~/.local/share/chgraph` from tests (use `CHGRAPH_DATA_DIR` + tmp dirs).
- **No Windows.** macOS/Linux only.

## File Structure

```
pyproject.toml
src/chgraph/
  __init__.py        # version string only
  paths.py           # project slug + ProjectPaths (data-dir layout)
  schema.py          # SCHEMA_VERSION, canonical DDL, create/check
  store.py           # Store: owns the chdb Session, FINAL-correct helpers
  gitingest.py       # git log --numstat -> git_commits/git_file_changes + count gate
  evolution.py       # churn/coupling/ownership/recency + file_evolution refresh
  parse_python.py    # tree-sitter extraction: one file -> node/edge rows
  indexer.py         # repo walk, batch insert, OPTIMIZE, sanity gates
  search.py          # search_graph: candidates + hybrid-lite ranking
  traverse.py        # trace_path: guarded recursive CTE
  status.py          # status.json read/write (atomic)
  daemon.py          # SessionWorker + asyncio unix-socket server + index job
  client.py          # DaemonClient (used by CLI and shim)
  cli.py             # chgraph daemon start/stop/status/restart, index, mcp
  shim.py            # FastMCP stdio server, tier-1 tools, never imports chdb
tests/
  conftest.py        # store fixture, synth_repo fixture
  fixtures/make_synth_repo.sh   # verbatim from chgraph-git-evolution-campaign
  unit/    chdb/     # per validation-and-qa §7 layout
docs/adr/0001-v01-implementation-decisions.md
```

---

### Task 1: Project scaffolding

**Skills to read first:** `chgraph-build-and-env`, `chgraph-change-control` (§ ADR template).

**Files:**
- Create: `pyproject.toml`, `src/chgraph/__init__.py`, `tests/unit/test_scaffold.py`, `.gitignore`

**Interfaces:**
- Produces: importable `chgraph` package with `chgraph.__version__ == "0.1.0"`; `uv run pytest` works; `chgraph` console script (wired fully in Task 11).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "chgraph"
version = "0.1.0"
description = "chdb-backed codebase knowledge graph MCP server"
requires-python = ">=3.12"
dependencies = [
    "chdb==4.2.0",          # resolves chdb-core 26.5.0; chdb.__version__ reports 26.5.0
    "mcp>=1.28,<2",
    "tree-sitter>=0.26,<0.27",
    "tree-sitter-python>=0.25",
    "pydantic>=2,<3",
]

[dependency-groups]
dev = ["pytest>=9"]

[project.scripts]
chgraph = "chgraph.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/chgraph"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `src/chgraph/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Write `.gitignore`**

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 4: Write the failing test** — `tests/unit/test_scaffold.py`

```python
import chgraph


def test_version():
    assert chgraph.__version__ == "0.1.0"


def test_chdb_pin():
    import chdb
    assert chdb.__version__ == "26.5.0"  # wrapper pin is 4.2.0; __version__ reports core
```

- [ ] **Step 5: Create env and run**

Run: `uv sync && uv run pytest tests/unit/test_scaffold.py -v`
Expected: 2 passed. (First `import chdb` takes ~5s — normal, trap T4. `uv sync` pulls ~554MB — normal, trap T3.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ tests/ .gitignore uv.lock
git commit -m "feat: project scaffolding with pinned toolchain (chdb==4.2.0)"
```

---

### Task 2: Paths and project slug

**Skills to read first:** `chgraph-run-and-operate` §1.

**Files:**
- Create: `src/chgraph/paths.py`, `tests/unit/test_paths.py`

**Interfaces:**
- Produces: `project_slug(repo_root: str) -> str`; `ProjectPaths` dataclass with fields `root, chdb_dir, socket, pidfile, status_json, log_dir` (all `Path`) and classmethod `ProjectPaths.for_repo(repo_root: str) -> ProjectPaths`; `ProjectPaths.ensure()` creates dirs. Respects `$CHGRAPH_DATA_DIR` then `$XDG_DATA_HOME`, defaults `~/.local/share/chgraph`.

- [ ] **Step 1: Write the failing test** — `tests/unit/test_paths.py`

```python
import hashlib
import os

from chgraph.paths import ProjectPaths, project_slug


def test_slug_is_basename_plus_hash(tmp_path):
    repo = tmp_path / "my-repo"
    repo.mkdir()
    real = os.path.realpath(str(repo))
    want = "my-repo-" + hashlib.sha256(real.encode()).hexdigest()[:8]
    assert project_slug(str(repo)) == want


def test_slug_sanitizes_weird_names(tmp_path):
    repo = tmp_path / "a b@c"
    repo.mkdir()
    assert project_slug(str(repo)).startswith("a-b-c-")


def test_paths_layout_respects_chgraph_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHGRAPH_DATA_DIR", str(tmp_path / "root"))
    repo = tmp_path / "repo"
    repo.mkdir()
    p = ProjectPaths.for_repo(str(repo))
    slug = project_slug(str(repo))
    assert p.root == tmp_path / "root" / slug
    assert p.chdb_dir == p.root / "chdb"
    assert p.socket == p.root / "daemon.sock"
    assert p.pidfile == p.root / "daemon.pid"
    assert p.status_json == p.root / "status.json"
    assert p.log_dir == p.root / "logs"
    p.ensure()
    assert p.log_dir.is_dir()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_paths.py -v` — Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement** — `src/chgraph/paths.py`

```python
"""Data-directory layout. Contract: chgraph-run-and-operate §1 (DECIDED 2026-07-03)."""
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path


def project_slug(repo_root: str) -> str:
    real = os.path.realpath(repo_root)
    base = re.sub(r"[^A-Za-z0-9_-]", "-", os.path.basename(real)) or "repo"
    return f"{base}-{hashlib.sha256(real.encode()).hexdigest()[:8]}"


def _data_root() -> Path:
    if os.environ.get("CHGRAPH_DATA_DIR"):
        return Path(os.environ["CHGRAPH_DATA_DIR"])
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(xdg) / "chgraph"


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    chdb_dir: Path
    socket: Path
    pidfile: Path
    status_json: Path
    log_dir: Path

    @classmethod
    def for_repo(cls, repo_root: str) -> "ProjectPaths":
        root = _data_root() / project_slug(repo_root)
        return cls(
            root=root,
            chdb_dir=root / "chdb",
            socket=root / "daemon.sock",
            pidfile=root / "daemon.pid",
            status_json=root / "status.json",
            log_dir=root / "logs",
        )

    def ensure(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_paths.py -v` — Expected: 3 passed

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: project slug and XDG data-dir layout"`

---

### Task 3: Schema and Store

**Skills to read first:** `chgraph-architecture-contract` (canonical DDL + INV-5/INV-6), `chgraph-validation-and-qa` §7, `chdb-reference`.

**Files:**
- Create: `src/chgraph/schema.py`, `src/chgraph/store.py`, `tests/conftest.py`, `tests/chdb/test_schema_roundtrip.py`, `tests/chdb/test_store.py`

**Interfaces:**
- Produces: `schema.SCHEMA_VERSION: int = 1`; `schema.DDL: list[str]` (CREATE statements); `schema.create_all(sess) -> None`; `Store.open(chdb_dir: str | Path) -> Store` (creates schema on first open, raises `SchemaVersionError` on mismatch); `Store.exec(sql: str) -> None`; `Store.rows(sql: str) -> list[dict]` (JSONEachRow-parsed); `Store.raw(sql: str, fmt: str = "CSV") -> str`; `Store.close()`.
- Consumes: nothing (foundation).

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import pytest

from chgraph.store import Store


@pytest.fixture
def store(tmp_path):
    # One Session per process; fresh tmp dir per test; always close (INV-1).
    s = Store.open(tmp_path / "data")
    yield s
    s.close()
```

`tests/chdb/test_schema_roundtrip.py` — port of the VERIFIED validation-and-qa §7 suite onto the real schema:

```python
def test_nodes_roundtrip_and_replace(store):
    store.exec("""
        INSERT INTO chgraph.nodes VALUES
        ('demo','Function','parse','pkg.mod.parse','pkg/mod.py',10,42,'{}',1),
        ('demo','Function','emit','pkg.mod.emit','pkg/mod.py',44,60,'{}',1)
    """)
    store.exec("""
        INSERT INTO chgraph.nodes VALUES
        ('demo','Function','parse','pkg.mod.parse','pkg/mod.py',12,50,'{}',2)
    """)
    rows = store.rows(
        "SELECT qualified_name, start_line, version FROM chgraph.nodes FINAL "
        "WHERE qualified_name = 'pkg.mod.parse'"
    )
    assert rows == [{"qualified_name": "pkg.mod.parse", "start_line": 12, "version": 2}]


def test_all_tables_exist(store):
    names = {r["name"] for r in store.rows("SELECT name FROM system.tables WHERE database='chgraph'")}
    assert {"nodes", "edges", "git_commits", "git_file_changes",
            "file_evolution", "embeddings", "meta"} <= names


def test_edges_dedup_on_final(store):
    store.exec("INSERT INTO chgraph.edges VALUES ('p','a','b','CALLS','{}',1),('p','a','b','CALLS','{}',2)")
    assert store.rows("SELECT count() AS n FROM chgraph.edges FINAL")[0]["n"] == 1
```

`tests/chdb/test_store.py`:

```python
import pytest

from chgraph.store import SchemaVersionError, Store


def test_schema_version_gate(tmp_path):
    s = Store.open(tmp_path / "d")
    s.exec("ALTER TABLE chgraph.meta UPDATE value = '999' WHERE key = 'schema_version'")
    s.close()
    with pytest.raises(SchemaVersionError):
        Store.open(tmp_path / "d")
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/chdb -v` — Expected: FAIL (import error)

- [ ] **Step 3: Implement** — `src/chgraph/schema.py`

The nodes/edges DDL below is the canonical Decision-5 DDL from `chgraph-architecture-contract`, verbatim; the side tables are the campaign-owned shapes from `chgraph-git-evolution-campaign` Phase 1. Do not edit any column or ORDER BY — that is a change-control matter.

```python
"""Canonical schema. One home: chgraph-architecture-contract Decision 5 (nodes/edges)
and chgraph-git-evolution-campaign Phase 1 (git side tables). Changes -> chgraph-change-control."""

SCHEMA_VERSION = 1

DDL = [
    "CREATE DATABASE IF NOT EXISTS chgraph",
    """CREATE TABLE IF NOT EXISTS chgraph.nodes (
        project String,
        label LowCardinality(String),
        name String,
        qualified_name String,
        file_path String,
        start_line UInt32,
        end_line UInt32,
        properties String,
        version UInt64
    ) ENGINE = ReplacingMergeTree(version)
    ORDER BY (project, qualified_name)""",
    """CREATE TABLE IF NOT EXISTS chgraph.edges (
        project String,
        source String,
        target String,
        type LowCardinality(String),
        properties String,
        version UInt64
    ) ENGINE = ReplacingMergeTree(version)
    ORDER BY (project, type, source, target)""",
    """CREATE TABLE IF NOT EXISTS chgraph.git_commits (
        project String, hash FixedString(40), author_name String, author_email String,
        committed_at DateTime, message String
    ) ENGINE = MergeTree ORDER BY (project, committed_at, hash)""",
    """CREATE TABLE IF NOT EXISTS chgraph.git_file_changes (
        project String, hash FixedString(40), committed_at DateTime, author_email String,
        path String, old_path String, additions UInt32, deletions UInt32, is_rename UInt8
    ) ENGINE = MergeTree ORDER BY (project, path, committed_at)""",
    """CREATE TABLE IF NOT EXISTS chgraph.file_evolution (
        project String, path String,
        commit_count UInt32, churn UInt64,
        last_commit_at DateTime, top_author String, top_author_share Float32,
        recency_score Float32,
        version UInt64
    ) ENGINE = ReplacingMergeTree(version) ORDER BY (project, path)""",
    """CREATE TABLE IF NOT EXISTS chgraph.embeddings (
        project String, qualified_name String, vec Array(Float32), version UInt64
    ) ENGINE = ReplacingMergeTree(version) ORDER BY (project, qualified_name)""",
    """CREATE TABLE IF NOT EXISTS chgraph.meta (
        key String, value String, version UInt64
    ) ENGINE = ReplacingMergeTree(version) ORDER BY (key)""",
]


def create_all(sess) -> None:
    for stmt in DDL:
        sess.query(stmt)
```

`src/chgraph/store.py`:

```python
"""Store: sole owner of the chdb Session in a process (INV-1)."""
import json
from pathlib import Path

import chdb.session as chs

from chgraph import schema


class SchemaVersionError(RuntimeError):
    pass


class Store:
    def __init__(self, sess):
        self._sess = sess

    @classmethod
    def open(cls, chdb_dir: str | Path) -> "Store":
        sess = chs.Session(str(chdb_dir))
        store = cls(sess)
        schema.create_all(sess)
        rows = store.rows("SELECT value FROM chgraph.meta FINAL WHERE key = 'schema_version'")
        if not rows:
            store.exec(
                f"INSERT INTO chgraph.meta VALUES ('schema_version', '{schema.SCHEMA_VERSION}', 1)"
            )
        elif int(rows[0]["value"]) != schema.SCHEMA_VERSION:
            sess.close()
            raise SchemaVersionError(
                f"data dir has schema_version={rows[0]['value']}, "
                f"this build understands {schema.SCHEMA_VERSION} (INV-6: refusing to open)"
            )
        return store

    def exec(self, sql: str) -> None:
        self._sess.query(sql)

    def rows(self, sql: str) -> list[dict]:
        out = self._sess.query(sql, "JSONEachRow").bytes().decode()
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def raw(self, sql: str, fmt: str = "CSV") -> str:
        return self._sess.query(sql, fmt).bytes().decode()

    def close(self) -> None:
        self._sess.close()
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/chdb -v` — Expected: 4 passed. (If `ALTER TABLE ... UPDATE` is rejected in the version-gate test, replace that line with `s.exec("INSERT INTO chgraph.meta VALUES ('schema_version','999',2)")` — RMT + FINAL makes the newer row win; the gate must still raise.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: canonical schema with version gate and FINAL-correct store"`

---

### Task 4: Git ingestion with count gate

**Skills to read first:** `chgraph-git-evolution-campaign` Phase 2 (the parser below is its VERIFIED prototype, productionized), fenced wrong paths table.

**Files:**
- Create: `src/chgraph/gitingest.py`, `tests/fixtures/make_synth_repo.sh`, `tests/chdb/test_gitingest.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `GitIngestCounts` dataclass (`commits, file_changes, renames: int`); `ingest_git(store: Store, project: str, repo_root: str) -> GitIngestCounts` (idempotent: truncates both git tables first — one project per data dir makes that safe); `verify_git_counts(repo_root: str, counts: GitIngestCounts) -> list[str]` (empty list = gate passed; entries are degraded-reasons strings).
- Consumes: `Store` from Task 3.

- [ ] **Step 1: Copy the fixture script** — `tests/fixtures/make_synth_repo.sh` must be byte-identical to the script in `chgraph-git-evolution-campaign` §2a (it plants known co-change/rename/ownership/recency patterns; TOTAL_COMMITS=14). Copy it from the skill, `chmod +x` it.

- [ ] **Step 2: Add the fixture** — append to `tests/conftest.py`:

```python
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def synth_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("synth") / "repo"
    out = subprocess.run(
        ["bash", str(FIXTURES / "make_synth_repo.sh"), str(repo)],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "TOTAL_COMMITS=14" in out
    return repo
```

- [ ] **Step 3: Write the failing test** — `tests/chdb/test_gitingest.py`

```python
from chgraph.gitingest import ingest_git, verify_git_counts


def test_ingest_counts_match_campaign_gate(store, synth_repo):
    counts = ingest_git(store, "synth", str(synth_repo))
    assert (counts.commits, counts.file_changes, counts.renames) == (14, 24, 1)
    assert verify_git_counts(str(synth_repo), counts) == []


def test_ingest_is_idempotent(store, synth_repo):
    ingest_git(store, "synth", str(synth_repo))
    counts = ingest_git(store, "synth", str(synth_repo))  # second run must NOT double
    assert (counts.commits, counts.file_changes) == (14, 24)


def test_rename_expanded(store, synth_repo):
    ingest_git(store, "synth", str(synth_repo))
    rows = store.rows(
        "SELECT path, old_path FROM chgraph.git_file_changes WHERE is_rename = 1"
    )
    assert rows == [{"path": "src/core/legacy.py", "old_path": "src/legacy.py"}]


def test_count_gate_catches_mismatch(store, synth_repo):
    from chgraph.gitingest import GitIngestCounts
    bad = GitIngestCounts(commits=1, file_changes=1, renames=0)
    reasons = verify_git_counts(str(synth_repo), bad)
    assert len(reasons) == 2  # commits mismatch + file_changes mismatch
```

- [ ] **Step 4: Run to verify failure** — `uv run pytest tests/chdb/test_gitingest.py -v` — Expected: FAIL

- [ ] **Step 5: Implement** — `src/chgraph/gitingest.py` (parser logic is the campaign's VERIFIED Phase-2b code; keep `-M`, brace-rename expansion, and binary `-` handling exactly)

```python
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
        ["git", "-C", repo_root, *args], check=True, capture_output=True, text=True
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
```

- [ ] **Step 6: Run to verify pass** — `uv run pytest tests/chdb/test_gitingest.py -v` — Expected: 4 passed

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: git history ingestion with campaign count gate"`

---

### Task 5: Evolution metrics

**Skills to read first:** `chgraph-git-evolution-campaign` Phase 3 (all SQL below is its VERIFIED SQL, parameterized), `code-graph-reference` §7 (formula definitions).

**Files:**
- Create: `src/chgraph/evolution.py`, `tests/chdb/test_evolution.py`

**Interfaces:**
- Produces: `refresh_file_evolution(store, project: str, version: int) -> int` (row count written); `churn(store, project) -> list[dict]`; `coupling(store, project, min_support: int = 2) -> list[dict]`; `ownership(store, project) -> list[dict]`; `recency(store, project, half_life_days: float = 30.0) -> list[dict]`. Half-life default 30.0 and min_support default 2 are the campaign's DECIDED starting values — named constants, single home here in code.
- Consumes: `Store`, ingested git tables (Task 4).

- [ ] **Step 1: Write the failing test** — `tests/chdb/test_evolution.py`

```python
import pytest

from chgraph.evolution import churn, coupling, ownership, recency, refresh_file_evolution
from chgraph.gitingest import ingest_git


@pytest.fixture
def synth_store(store, synth_repo):
    ingest_git(store, "synth", str(synth_repo))
    return store


def test_churn_top_is_api(synth_store):
    top = churn(synth_store, "synth")[0]
    assert top["path"] == "src/api.py"
    assert top["commits"] == 8 and top["churn"] == 23


def test_coupling_planted_pair_ranks_first(synth_store):
    rows = coupling(synth_store, "synth")
    # Campaign gate: the planted pair MUST rank #1 with support 7, conf_b_to_a == 1.
    assert rows[0]["file_a"] == "src/api.py" and rows[0]["file_b"] == "tests/test_api.py"
    assert rows[0]["support"] == 7 and rows[0]["conf_b_to_a"] == 1


def test_ownership_alice_dominates_api(synth_store):
    api = next(r for r in ownership(synth_store, "synth") if r["path"] == "src/api.py")
    assert api["top_author"] == "alice@example.com" and api["top_author_share"] == 0.875


def test_recency_fresh_beats_stale(synth_store):
    rows = {r["path"]: r["recency_score"] for r in recency(synth_store, "synth")}
    assert rows["src/api.py"] > 0.9          # touched 1 day ago
    assert rows["src/legacy.py"] < 0.001     # untouched 390 days


def test_file_evolution_refresh(synth_store):
    assert refresh_file_evolution(synth_store, "synth", version=1) == 6
    n = synth_store.rows("SELECT count() AS n FROM chgraph.file_evolution FINAL")[0]["n"]
    assert n == 6
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/chdb/test_evolution.py -v` — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/evolution.py` (SQL bodies copied from campaign Phase 3; only `{project}` parameterization and named constants added; project strings are internal slugs, not user input — still escape via the helper)

```python
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
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/chdb/test_evolution.py -v` — Expected: 5 passed. (Note: the stored `recency_score` goes stale between refreshes — DECIDED: ranking always recomputes recency at query time; the stored column is for browsing only. Task 8 honors this.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: evolution metrics (churn, coupling, ownership, recency)"`

---

### Task 6: Python parser (tree-sitter)

**Skills to read first:** `code-graph-reference` (node labels, edge types, qualified_name identity, why call-edge precision beats breadth), `chgraph-architecture-contract` Decision 9.

**Files:**
- Create: `src/chgraph/parse_python.py`, `tests/unit/test_parse_python.py`

**Interfaces:**
- Produces: `parse_file(rel_path: str, source: bytes) -> tuple[list[dict], list[dict]]` — node dicts with keys `label, name, qualified_name, file_path, start_line, end_line, properties` and edge dicts with keys `source, target, type, properties` (no `project`/`version` — the indexer adds those). Conventions: File node `label="File"`, `qualified_name=rel_path`; module dotted name = rel_path minus `.py`, `/`→`.` (`__init__.py` → package name); Function/Class qualified names nest (`pkg.mod.Class.method`); edges: `DEFINES` (File→symbol), `IMPORTS` (File→module string), `CALLS` (precision-first: only calls resolvable to a same-module def or an explicit `from X import y` name).
- Consumes: nothing chdb-related (pure function; tests live in `tests/unit/`).

- [ ] **Step 1: Write the failing test** — `tests/unit/test_parse_python.py`

```python
from chgraph.parse_python import parse_file

SRC = b'''\
from os import path
import json

def top(x):
    return helper(x)

def helper(x):
    return path.join("a", x)

class Greeter:
    def greet(self):
        return top(1)

def uses_import():
    return json.dumps({})
'''


def _nodes_by_qn(nodes):
    return {n["qualified_name"]: n for n in nodes}


def test_nodes_extracted():
    nodes, _ = parse_file("src/demo.py", SRC)
    qns = _nodes_by_qn(nodes)
    assert qns["src/demo.py"]["label"] == "File"
    assert qns["src.demo.top"]["label"] == "Function"
    assert qns["src.demo.Greeter"]["label"] == "Class"
    assert qns["src.demo.Greeter.greet"]["label"] == "Function"
    assert qns["src.demo.top"]["start_line"] == 4  # 1-based


def test_defines_and_imports_edges():
    _, edges = parse_file("src/demo.py", SRC)
    defines = {(e["source"], e["target"]) for e in edges if e["type"] == "DEFINES"}
    assert ("src/demo.py", "src.demo.top") in defines
    assert ("src/demo.py", "src.demo.Greeter") in defines
    imports = {e["target"] for e in edges if e["type"] == "IMPORTS"}
    assert {"os.path", "json"} <= imports


def test_calls_resolved_precision_first():
    _, edges = parse_file("src/demo.py", SRC)
    calls = {(e["source"], e["target"]) for e in edges if e["type"] == "CALLS"}
    assert ("src.demo.top", "src.demo.helper") in calls          # same-module def
    assert ("src.demo.Greeter.greet", "src.demo.top") in calls   # method -> module def
    # precision-first: attribute calls on imported modules are NOT guessed into CALLS
    assert not any(t == "json.dumps" for _, t in calls)


def test_init_py_module_name():
    nodes, _ = parse_file("pkg/__init__.py", b"def f():\n    pass\n")
    assert "pkg.f" in _nodes_by_qn(nodes)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/unit/test_parse_python.py -v` — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/parse_python.py`

```python
"""Python symbol/edge extraction via tree-sitter (Decision 9: precision over breadth).
CALLS edges are emitted ONLY when the callee resolves to a same-module def or an
explicit `from X import name` binding — unresolvable calls are dropped, not guessed
(the reference tool's false-CALLS bugs are the cautionary tale, code-graph-reference)."""
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

_PY = Language(tspython.language())
_parser = Parser(_PY)


def _module_name(rel_path: str) -> str:
    p = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def parse_file(rel_path: str, source: bytes) -> tuple[list[dict], list[dict]]:
    tree = _parser.parse(source)
    module = _module_name(rel_path)
    nodes: list[dict] = [{
        "label": "File", "name": rel_path.rsplit("/", 1)[-1],
        "qualified_name": rel_path, "file_path": rel_path,
        "start_line": 1, "end_line": source.count(b"\n") + 1, "properties": "{}",
    }]
    edges: list[dict] = []
    module_defs: dict[str, str] = {}     # local name -> qualified_name (module-level defs)
    imported: dict[str, str] = {}        # local name -> dotted module path

    def text(n) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="replace")

    def add_symbol(node, kind: str, scope: str) -> str:
        name_node = node.child_by_field_name("name")
        name = text(name_node)
        qn = f"{scope}.{name}"
        nodes.append({
            "label": kind, "name": name, "qualified_name": qn, "file_path": rel_path,
            "start_line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1,
            "properties": "{}",
        })
        edges.append({"source": rel_path, "target": qn, "type": "DEFINES", "properties": "{}"})
        return qn

    def collect_imports(node) -> None:
        if node.type == "import_statement":            # import json, os.path
            for child in node.named_children:
                dotted = text(child.child_by_field_name("name") or child)
                alias = text(child.child_by_field_name("alias")) if child.type == "aliased_import" else dotted.split(".")[0]
                imported[alias] = dotted
                edges.append({"source": rel_path, "target": dotted, "type": "IMPORTS", "properties": "{}"})
        elif node.type == "import_from_statement":     # from os import path [as p]
            mod = text(node.child_by_field_name("module_name"))
            for child in node.named_children[1:]:
                if child.type in ("dotted_name", "aliased_import"):
                    name_node = child.child_by_field_name("name") or child
                    name = text(name_node)
                    alias = text(child.child_by_field_name("alias")) if child.type == "aliased_import" else name
                    imported[alias] = f"{mod}.{name}"
                    edges.append({"source": rel_path, "target": f"{mod}.{name}", "type": "IMPORTS", "properties": "{}"})

    # Pass 1: symbols + imports (so calls can resolve forward references).
    def walk_defs(node, scope: str) -> None:
        for child in node.named_children:
            if child.type == "function_definition":
                qn = add_symbol(child, "Function", scope)
                if scope == module:
                    module_defs[text(child.child_by_field_name("name"))] = qn
                walk_defs(child.child_by_field_name("body"), qn)
            elif child.type == "class_definition":
                qn = add_symbol(child, "Class", scope)
                if scope == module:
                    module_defs[text(child.child_by_field_name("name"))] = qn
                walk_defs(child.child_by_field_name("body"), qn)
            elif child.type in ("import_statement", "import_from_statement"):
                collect_imports(child)
            else:
                walk_defs(child, scope)

    walk_defs(tree.root_node, module)

    # Pass 2: CALLS. Track the enclosing function scope while walking.
    def walk_calls(node, scope: str) -> None:
        for child in node.named_children:
            if child.type in ("function_definition", "class_definition"):
                walk_calls(child.child_by_field_name("body"),
                           f"{scope}.{text(child.child_by_field_name('name'))}")
            elif child.type == "call":
                fn = child.child_by_field_name("function")
                if fn.type == "identifier":
                    callee = text(fn)
                    target = module_defs.get(callee) or imported.get(callee)
                    if target:
                        edges.append({"source": scope, "target": target,
                                      "type": "CALLS", "properties": "{}"})
                walk_calls(child, scope)
            else:
                walk_calls(child, scope)

    walk_calls(tree.root_node, module)
    return nodes, edges
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/unit/test_parse_python.py -v` — Expected: 4 passed.
**If the tree-sitter API errors on `Parser(_PY)` or field names:** the installed tree-sitter version's API drifted — check `uv run python -c "import tree_sitter; print(tree_sitter.__version__)"` and the py-tree-sitter changelog, adapt ONLY the binding calls (never the output contract), and note the drift in ADR-0001 (Task 13). The tests are the spec — make them pass unmodified.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: precision-first Python parser (tree-sitter)"`

---

### Task 7: Indexer with sanity gates

**Skills to read first:** `chgraph-architecture-contract` (INV-3, INV-5), `chgraph-validation-and-qa` §5 (gates are candidates, thresholds OPEN), `chgraph-diagnostics-and-tooling` (nodes-per-KLOC concept).

**Files:**
- Create: `src/chgraph/indexer.py`, `tests/chdb/test_indexer.py`

**Interfaces:**
- Produces: `IndexResult` dataclass (`version: int, files_total: int, files_done: int, nodes: int, edges: int, degraded_reasons: list[str]`); `index_repository(store: Store, project: str, repo_root: str, on_progress: Callable[[int, int], None] | None = None) -> IndexResult`. Behavior: enumerate `git ls-files '*.py'`; parse all files (pure Python); ONE batch insert per table with `version = max(existing)+1`; `OPTIMIZE TABLE ... FINAL` both tables; run `ingest_git` + `verify_git_counts` + `refresh_file_evolution`; compute sanity gates and fill `degraded_reasons` (never raise for degradation — INV-3 reports, the daemon surfaces).
- Consumes: `parse_file` (Task 6), `ingest_git`/`verify_git_counts` (Task 4), `refresh_file_evolution` (Task 5), `Store` (Task 3).

- [ ] **Step 1: Write the failing test** — `tests/chdb/test_indexer.py`

```python
from chgraph.indexer import index_repository


def test_index_synth_repo(store, synth_repo):
    res = index_repository(store, "synth", str(synth_repo))
    assert res.files_total == res.files_done == 4          # 4 .py files in fixture
    assert res.nodes >= 4 + 4                              # >= one File node + symbols per file
    assert res.degraded_reasons == []
    n = store.rows("SELECT count() AS n FROM chgraph.nodes FINAL WHERE project='synth'")[0]["n"]
    assert n == res.nodes
    # git side ran too:
    assert store.rows("SELECT count() AS n FROM chgraph.git_commits")[0]["n"] == 14


def test_reindex_replaces_not_duplicates(store, synth_repo):
    r1 = index_repository(store, "synth", str(synth_repo))
    r2 = index_repository(store, "synth", str(synth_repo))
    assert r2.version == r1.version + 1
    n = store.rows("SELECT count() AS n FROM chgraph.nodes FINAL WHERE project='synth'")[0]["n"]
    assert n == r2.nodes  # FINAL sees exactly one generation


def test_sanity_gate_flags_symbol_collapse(store, tmp_path):
    # A "repo" whose .py files are unparseable garbage should index as degraded, not "indexed".
    import subprocess
    repo = tmp_path / "junk"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"\x00" * 5000)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "x"], check=True)
    res = index_repository(store, "junk", str(repo))
    assert any("nodes-per-KLOC" in r for r in res.degraded_reasons)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/chdb/test_indexer.py -v` — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/indexer.py`

```python
"""Repo -> graph. Batch writes only (INV-5); honest degradation reporting (INV-3)."""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from chgraph.evolution import refresh_file_evolution
from chgraph.gitingest import ingest_git, verify_git_counts
from chgraph.parse_python import parse_file
from chgraph.store import Store

# Candidate threshold, label OPEN (validation-and-qa §5): calibrate before trusting.
# Plain Python code sits well above this; near-zero means the parser silently failed.
MIN_NODES_PER_KLOC = 5.0


@dataclass
class IndexResult:
    version: int
    files_total: int
    files_done: int
    nodes: int
    edges: int
    degraded_reasons: list[str] = field(default_factory=list)


def _py_files(repo_root: str) -> list[str]:
    out = subprocess.run(["git", "-C", repo_root, "ls-files", "*.py"],
                         check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def _batch_insert(store: Store, table: str, rows: list[dict]) -> None:
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


def index_repository(store: Store, project: str, repo_root: str,
                     on_progress: Callable[[int, int], None] | None = None) -> IndexResult:
    version = store.rows(
        f"SELECT coalesce(max(version), 0) + 1 AS v FROM chgraph.nodes WHERE project = '{project}'"
    )[0]["v"]

    files = _py_files(repo_root)
    node_rows: list[dict] = []
    edge_rows: list[dict] = []
    total_lines = 0
    done = 0
    for rel in files:
        src = open(os.path.join(repo_root, rel), "rb").read()
        total_lines += src.count(b"\n") + 1
        nodes, edges = parse_file(rel, src)   # pure Python — never raises on bad syntax,
        for n in nodes:                       # tree-sitter yields a partial tree instead
            node_rows.append({**n, "project": project, "version": version})
        for e in edges:
            edge_rows.append({**e, "project": project, "version": version})
        done += 1
        if on_progress:
            on_progress(done, len(files))

    _batch_insert(store, "chgraph.nodes", node_rows)
    _batch_insert(store, "chgraph.edges", edge_rows)
    store.exec("OPTIMIZE TABLE chgraph.nodes FINAL")
    store.exec("OPTIMIZE TABLE chgraph.edges FINAL")

    reasons: list[str] = []
    reasons += verify_git_counts(repo_root, ingest_git(store, project, repo_root))
    refresh_file_evolution(store, project, version)

    symbol_nodes = sum(1 for n in node_rows if n["label"] != "File")
    kloc = max(total_lines / 1000.0, 0.001)
    density = symbol_nodes / kloc
    if files and density < MIN_NODES_PER_KLOC:
        reasons.append(
            f"nodes-per-KLOC sanity: {density:.1f} < {MIN_NODES_PER_KLOC} "
            f"({symbol_nodes} symbols over {total_lines} lines) — parser likely failed silently"
        )

    return IndexResult(version=version, files_total=len(files), files_done=done,
                       nodes=len(node_rows), edges=len(edge_rows), degraded_reasons=reasons)
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/chdb/test_indexer.py -v` — Expected: 3 passed

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: indexer with batch writes, OPTIMIZE, and honest sanity gates"`

---

### Task 8: search_graph with hybrid-lite ranking

**Skills to read first:** `chgraph-git-evolution-campaign` Phase 5 (the ranking SQL shape below is its VERIFIED query; weights are its DECIDED defaults), `mcp-server-reference` §5 row 2 (param contract).

**Files:**
- Create: `src/chgraph/search.py`, `tests/chdb/test_search.py`

**Interfaces:**
- Produces: `SearchPage` dataclass (`items: list[dict], total: int, has_more: bool`); `search_graph(store, project, query: str | None = None, name_pattern: str | None = None, label: str | None = None, limit: int = 200, offset: int = 0) -> SearchPage`. Items carry `qualified_name, label, name, file_path, start_line, end_line, lex, rec, cen, score`. Weights: module constants `W = {"lex": 0.35, "vec": 0.30, "rec": 0.20, "cen": 0.15}`; the vector signal is structurally present but contributes 0 in v0.1 (no embeddings yet) — do NOT renormalize (ranking is monotonic; renormalizing would be an undocumented weights change). Recency is computed from `git_file_changes` at query time, never from the stored `file_evolution.recency_score`.
- Consumes: `Store`, indexed graph + git tables (Tasks 4–7).
- **This task is retrieval-affecting:** the PR body must contain the line `eval: not yet run — harness not built`.

- [ ] **Step 1: Write the failing test** — `tests/chdb/test_search.py`

```python
import pytest

from chgraph.indexer import index_repository
from chgraph.search import search_graph


@pytest.fixture
def indexed(store, synth_repo):
    index_repository(store, "synth", str(synth_repo))
    return store


def test_lexical_hit_ranks_fresh_above_stale(indexed):
    # Fixture: src/api.py touched 1 day ago; src/core/legacy.py stale.
    # Both files define functions; a query hitting both must rank api first.
    page = search_graph(indexed, "synth", query="handle")
    qns = [i["qualified_name"] for i in page.items]
    assert any(q.startswith("src.api.") for q in qns)
    api_pos = min(i for i, q in enumerate(qns) if q.startswith("src.api."))
    legacy_hits = [i for i, q in enumerate(qns) if "legacy" in q]
    assert all(api_pos < i for i in legacy_hits) or not legacy_hits


def test_name_pattern_regex(indexed):
    page = search_graph(indexed, "synth", name_pattern="^handle_v[0-9]$")
    assert page.total >= 1
    assert all(i["label"] == "Function" for i in page.items)


def test_label_filter_and_pagination(indexed):
    all_fns = search_graph(indexed, "synth", label="Function", limit=1000)
    page1 = search_graph(indexed, "synth", label="Function", limit=2, offset=0)
    assert page1.total == all_fns.total and len(page1.items) == 2 and page1.has_more


def test_requires_some_criterion(indexed):
    with pytest.raises(ValueError):
        search_graph(indexed, "synth")
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/chdb/test_search.py -v` — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/search.py`

```python
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
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/chdb/test_search.py -v` — Expected: 4 passed. (If `count() OVER ()` combined with `FINAL` errors, fall back to a second `SELECT count()` query with the same WHERE — correctness over cleverness; note it in ADR-0001.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: search_graph with recency-aware hybrid-lite ranking" -m "eval: not yet run — harness not built"`

---

### Task 9: trace_path (guarded traversal)

**Skills to read first:** `chgraph-architecture-contract` INV-2 (the CTE below is its VERIFIED pattern), `chdb-reference` (recursive CTE mechanics).

**Files:**
- Create: `src/chgraph/traverse.py`, `tests/chdb/test_traverse.py`

**Interfaces:**
- Produces: `trace_path(store, project, qualified_name: str, direction: str = "callees", depth: int = 5) -> list[dict]` — rows `{node, path: list[str], depth}`; `direction` in `{"callees", "callers"}`; `depth` clamped to `[1, 10]`. Cycles must terminate (INV-2).
- Consumes: `Store`, edges table.

- [ ] **Step 1: Write the failing test** — `tests/chdb/test_traverse.py`

```python
import pytest

from chgraph.traverse import trace_path


@pytest.fixture
def cyclic(store):
    # Deliberate cycle a->b->c->a plus branch c->d (the INV-2 verification shape).
    store.exec("""
        INSERT INTO chgraph.edges VALUES
        ('p','a','b','CALLS','{}',1), ('p','b','c','CALLS','{}',1),
        ('p','c','a','CALLS','{}',1), ('p','c','d','CALLS','{}',1)
    """)
    return store


def test_terminates_on_cycle_and_finds_all(cyclic):
    rows = trace_path(cyclic, "p", "a", direction="callees", depth=10)
    reached = {r["node"] for r in rows}
    assert reached == {"a", "b", "c", "d"}
    d = next(r for r in rows if r["node"] == "d")
    assert d["path"] == ["a", "b", "c", "d"] and d["depth"] == 3


def test_callers_direction(cyclic):
    rows = trace_path(cyclic, "p", "d", direction="callers", depth=10)
    assert {r["node"] for r in rows} == {"d", "c", "b", "a"}


def test_depth_clamped(cyclic):
    rows = trace_path(cyclic, "p", "a", depth=99)   # silently clamped to 10, must not error
    assert rows
    with pytest.raises(ValueError):
        trace_path(cyclic, "p", "a", direction="sideways")
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/chdb/test_traverse.py -v` — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/traverse.py`

```python
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
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/chdb/test_traverse.py -v` — Expected: 3 passed

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: trace_path with mandatory depth cap and cycle guard (INV-2)"`

---

### Task 10: Status file, daemon, and client

**Skills to read first:** `chgraph-run-and-operate` §§1–3, 5, 7 (lifecycle spec — this task implements it), `mcp-server-reference` §4 (status schema: states `queued|running|indexed|degraded|failed`), `chgraph-build-and-env` trap T5.

**Files:**
- Create: `src/chgraph/status.py`, `src/chgraph/daemon.py`, `src/chgraph/client.py`, `tests/chdb/test_daemon.py`

**Interfaces:**
- Produces:
  - `status.read_status(path: Path) -> dict` / `status.write_status(path: Path, **fields) -> None` (atomic tmp+rename; fields: `state, repo_root, job_id, files_total, files_done, nodes_persisted, degraded_reasons, error, updated_at`). NOTE: this plan adds one pre-first-index state `uninitialized` on top of the owned enum — record that extension in ADR-0001 (Task 13) as a tool-surface addition needing change-control sign-off.
  - `daemon.run_daemon(repo_root: str) -> None` — blocking; owns the Store; binds `ProjectPaths.socket`; writes pidfile/status; newline-delimited JSON protocol: request `{"op": str, "params": {...}}` → response `{"ok": true, "data": ...}` or `{"ok": false, "error": str}`. Ops: `ping, index, status, search, snippet, trace, schema_info, list_projects, delete_project, shutdown`.
  - `client.DaemonClient(socket_path: Path)` with `.call(op: str, **params) -> dict` (raises `DaemonError(str)` on `ok: false` or connection failure).
- Concurrency contract: ALL chdb access happens on ONE dedicated worker thread (`SessionWorker`) — chdb Session thread-safety is OPEN, so serialize. Parsing runs in the index job thread (pure Python); only the batched DB calls are submitted to the worker. The `status` op reads `status.json` from disk and never touches chdb (stays responsive mid-index).
- Fork-safety (T5): the daemon may `subprocess.run(["git", ...])` (exec of an external binary) but must never spawn a Python child that touches chdb.
- Consumes: everything from Tasks 2–9.

- [ ] **Step 1: Write the failing test** — `tests/chdb/test_daemon.py` (spawns the daemon as a real subprocess — one Session per process makes in-process testing impossible)

```python
import os
import subprocess
import sys
import time

import pytest

from chgraph.client import DaemonClient, DaemonError
from chgraph.paths import ProjectPaths


@pytest.fixture
def daemon(tmp_path, synth_repo):
    env = {**os.environ, "CHGRAPH_DATA_DIR": str(tmp_path / "cg")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "chgraph.daemon", str(synth_repo)],
        env=env, stderr=subprocess.PIPE,
    )
    paths = ProjectPaths.for_repo(str(synth_repo))
    # Recompute under the overridden env:
    os.environ["CHGRAPH_DATA_DIR"] = str(tmp_path / "cg")
    paths = ProjectPaths.for_repo(str(synth_repo))
    client = DaemonClient(paths.socket)
    for _ in range(100):                      # wait for socket, up to 10s
        try:
            client.call("ping")
            break
        except DaemonError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("daemon never answered ping: " + proc.stderr.read().decode()[-2000:])
    yield client, paths
    try:
        client.call("shutdown")
    except DaemonError:
        pass
    proc.wait(timeout=10)
    del os.environ["CHGRAPH_DATA_DIR"]


def _wait_indexed(client, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.call("status")
        if st["state"] in ("indexed", "degraded", "failed"):
            return st
        time.sleep(0.2)
    raise TimeoutError(st)


def test_index_then_query_roundtrip(daemon):
    client, paths = daemon
    job = client.call("index")
    assert job["state"] == "queued" and job["job_id"]
    st = _wait_indexed(client)
    assert st["state"] == "indexed"
    assert st["nodes_persisted"] > 0

    page = client.call("search", query="handle")
    assert page["total"] >= 1

    snip = client.call("snippet", qualified_name=page["items"][0]["qualified_name"])
    assert "def" in snip["text"]

    trace = client.call("trace", qualified_name=page["items"][0]["qualified_name"])
    assert isinstance(trace["paths"], list)

    info = client.call("schema_info")
    assert "Function" in info["labels"]


def test_unknown_op_is_error_not_crash(daemon):
    client, _ = daemon
    with pytest.raises(DaemonError):
        client.call("frobnicate")
    assert client.call("ping")["pong"] is True   # still alive
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/chdb/test_daemon.py -v` — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/status.py`

```python
"""status.json — atomic read/write. State machine home: mcp-server-reference §4."""
import json
import os
import time
from pathlib import Path

STATES = ("uninitialized", "queued", "running", "indexed", "degraded", "failed")


def write_status(path: Path, **fields) -> None:
    assert fields.get("state") in STATES, fields.get("state")
    fields["updated_at"] = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(fields))
    os.replace(tmp, path)


def read_status(path: Path) -> dict:
    if not path.exists():
        return {"state": "uninitialized"}
    return json.loads(path.read_text())
```

- [ ] **Step 4: Implement** — `src/chgraph/client.py`

```python
"""Tiny blocking JSON-lines client for the daemon socket. Used by CLI and shim.
MUST NOT import chdb (the shim imports this)."""
import json
import socket
from pathlib import Path


class DaemonError(RuntimeError):
    pass


class DaemonClient:
    def __init__(self, socket_path: Path):
        self.socket_path = Path(socket_path)

    def call(self, op: str, **params) -> dict:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(120)
                s.connect(str(self.socket_path))
                s.sendall((json.dumps({"op": op, "params": params}) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            raise DaemonError(f"cannot reach daemon at {self.socket_path}: {e}") from e
        resp = json.loads(buf.decode())
        if not resp.get("ok"):
            raise DaemonError(resp.get("error", "unknown daemon error"))
        return resp["data"]
```

- [ ] **Step 5: Implement** — `src/chgraph/daemon.py`

```python
"""The daemon: sole owner of the chdb Session (INV-1). One project per daemon.
Protocol: newline-delimited JSON over a unix socket. Lifecycle spec: chgraph-run-and-operate §2."""
import asyncio
import dataclasses
import json
import logging
import os
import queue
import sys
import threading
import uuid
from concurrent.futures import Future
from pathlib import Path

from chgraph import search as search_mod
from chgraph import traverse as traverse_mod
from chgraph.evolution import _q
from chgraph.indexer import index_repository
from chgraph.paths import ProjectPaths, project_slug
from chgraph.status import read_status, write_status
from chgraph.store import Store

log = logging.getLogger("chgraph.daemon")


class SessionWorker:
    """ALL chdb access goes through this single thread.
    ponytail: global serialization; per-op scheduling only if throughput ever demands it
    (Session thread-safety is OPEN — chgraph-architecture-contract weak points)."""

    def __init__(self, store: Store):
        self._store = store
        self._q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while True:
            fn, fut = self._q.get()
            if fn is None:
                fut.set_result(None)
                return
            try:
                fut.set_result(fn(self._store))
            except Exception as e:                     # noqa: BLE001 — daemon must not die on a bad query
                fut.set_exception(e)

    def submit(self, fn) -> Future:
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut

    def stop(self):
        fut: Future = Future()
        self._q.put((None, fut))
        fut.result(timeout=30)
        self._store.close()


class Daemon:
    def __init__(self, repo_root: str):
        self.repo_root = os.path.realpath(repo_root)
        self.project = project_slug(self.repo_root)
        self.paths = ProjectPaths.for_repo(self.repo_root)
        self.paths.ensure()
        self.worker = SessionWorker(Store.open(self.paths.chdb_dir))
        self._index_thread: threading.Thread | None = None
        self._stop = asyncio.Event()

    # ---- index job (runs in its own thread; only DB calls go through the worker) ----
    def _index_job(self, job_id: str):
        sp = self.paths.status_json
        try:
            write_status(sp, state="running", repo_root=self.repo_root, job_id=job_id,
                         files_total=0, files_done=0, nodes_persisted=0,
                         degraded_reasons=[], error=None)

            def progress(done, total):
                write_status(sp, state="running", repo_root=self.repo_root, job_id=job_id,
                             files_total=total, files_done=done, nodes_persisted=0,
                             degraded_reasons=[], error=None)

            res = self.worker.submit(
                lambda st: index_repository(st, self.project, self.repo_root, progress)
            ).result()
            state = "degraded" if res.degraded_reasons else "indexed"
            write_status(sp, state=state, repo_root=self.repo_root, job_id=job_id,
                         files_total=res.files_total, files_done=res.files_done,
                         nodes_persisted=res.nodes, degraded_reasons=res.degraded_reasons,
                         error=None)
        except Exception as e:                          # noqa: BLE001
            log.exception("index job failed")
            write_status(sp, state="failed", repo_root=self.repo_root, job_id=job_id,
                         files_total=0, files_done=0, nodes_persisted=0,
                         degraded_reasons=[], error=str(e))

    # ---- ops ----
    def op_ping(self, **_):
        return {"pong": True, "project": self.project, "pid": os.getpid()}

    def op_index(self, **_):
        if self._index_thread and self._index_thread.is_alive():
            return {"job_id": read_status(self.paths.status_json).get("job_id"),
                    "state": "running"}
        job_id = f"idx-{uuid.uuid4().hex[:8]}"
        write_status(self.paths.status_json, state="queued", repo_root=self.repo_root,
                     job_id=job_id, files_total=0, files_done=0, nodes_persisted=0,
                     degraded_reasons=[], error=None)
        self._index_thread = threading.Thread(target=self._index_job, args=(job_id,), daemon=True)
        self._index_thread.start()
        return {"job_id": job_id, "state": "queued"}

    def op_status(self, **_):
        return read_status(self.paths.status_json)     # never touches chdb — stays responsive

    def op_search(self, **params):
        page = self.worker.submit(
            lambda st: search_mod.search_graph(st, self.project, **params)).result()
        return dataclasses.asdict(page)

    def op_snippet(self, qualified_name: str, **_):
        row = self.worker.submit(lambda st: st.rows(
            f"SELECT file_path, start_line, end_line FROM chgraph.nodes FINAL "
            f"WHERE project = {_q(self.project)} AND qualified_name = {_q(qualified_name)}"
        )).result()
        if not row:
            raise ValueError(f"unknown symbol: {qualified_name}")
        r = row[0]
        lines = open(os.path.join(self.repo_root, r["file_path"]), errors="replace").read().splitlines()
        text = "\n".join(lines[r["start_line"] - 1:r["end_line"]])
        return {"qualified_name": qualified_name, "file_path": r["file_path"],
                "start_line": r["start_line"], "end_line": r["end_line"], "text": text}

    def op_trace(self, qualified_name: str, direction: str = "callees", depth: int = 5, **_):
        rows = self.worker.submit(lambda st: traverse_mod.trace_path(
            st, self.project, qualified_name, direction=direction, depth=depth)).result()
        return {"paths": rows}

    def op_schema_info(self, **_):
        def q(st):
            labels = [r["label"] for r in st.rows(
                f"SELECT DISTINCT label FROM chgraph.nodes FINAL WHERE project = {_q(self.project)} ORDER BY label")]
            etypes = [r["type"] for r in st.rows(
                f"SELECT DISTINCT type FROM chgraph.edges FINAL WHERE project = {_q(self.project)} ORDER BY type")]
            return {"labels": labels, "edge_types": etypes}
        return self.worker.submit(q).result()

    def op_list_projects(self, **_):
        return {"projects": [{"project": self.project, "repo_root": self.repo_root,
                              "state": read_status(self.paths.status_json)["state"]}]}

    def op_delete_project(self, **_):
        def wipe(st):
            for t in ("nodes", "edges", "git_commits", "git_file_changes",
                      "file_evolution", "embeddings"):
                st.exec(f"TRUNCATE TABLE chgraph.{t}")
        self.worker.submit(wipe).result()
        write_status(self.paths.status_json, state="uninitialized", repo_root=self.repo_root,
                     job_id=None, files_total=0, files_done=0, nodes_persisted=0,
                     degraded_reasons=[], error=None)
        return {"deleted": self.project}

    # ---- server ----
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode())
            op = req.get("op", "")
            handler = getattr(self, f"op_{op}", None)
            if op == "shutdown":
                writer.write(b'{"ok": true, "data": {"bye": true}}\n')
                await writer.drain()
                self._stop.set()
                return
            if handler is None:
                resp = {"ok": False, "error": f"unknown op: {op}"}
            else:
                loop = asyncio.get_running_loop()
                try:
                    data = await loop.run_in_executor(
                        None, lambda: handler(**req.get("params", {})))
                    resp = {"ok": True, "data": data}
                except Exception as e:                  # noqa: BLE001
                    resp = {"ok": False, "error": str(e)}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    async def serve(self):
        if self.paths.socket.exists():
            self.paths.socket.unlink()                 # stale socket from a crash
        server = await asyncio.start_unix_server(self.handle, path=str(self.paths.socket))
        self.paths.pidfile.write_text(f"{os.getpid()}\n")
        if not self.paths.status_json.exists():
            write_status(self.paths.status_json, state="uninitialized",
                         repo_root=self.repo_root, job_id=None, files_total=0,
                         files_done=0, nodes_persisted=0, degraded_reasons=[], error=None)
        log.info("chgraph daemon up: project=%s socket=%s", self.project, self.paths.socket)
        async with server:
            await self._stop.wait()
        self.worker.stop()
        for p in (self.paths.socket, self.paths.pidfile):
            p.unlink(missing_ok=True)


def run_daemon(repo_root: str) -> None:
    paths = ProjectPaths.for_repo(repo_root)
    paths.ensure()
    logging.basicConfig(
        filename=paths.log_dir / "daemon.log", level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        d = Daemon(repo_root)
    except Exception as e:                              # noqa: BLE001
        # Code 36/76 pair == another process owns the chdb dir (run-and-operate §2).
        msg = str(e)
        if "EmbeddedServer" in msg or "Cannot lock file" in msg:
            print(f"another chgraph daemon (or stray process) owns {paths.chdb_dir}; "
                  f"run `chgraph daemon status`", file=sys.stderr)
            raise SystemExit(1)
        raise
    asyncio.run(d.serve())


if __name__ == "__main__":
    run_daemon(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
```

- [ ] **Step 6: Run to verify pass** — `uv run pytest tests/chdb/test_daemon.py -v` — Expected: 2 passed (allow ~60s: the fixture repo indexes fast, but first chdb import in the subprocess costs ~5s)

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: daemon with serialized chdb worker, async index job, honest status"`

---

### Task 11: CLI

**Skills to read first:** `chgraph-run-and-operate` §2 (command semantics + exit codes: 0 running, 1 stopped, 2 stale/crashed).

**Files:**
- Create: `src/chgraph/cli.py`, `tests/chdb/test_cli.py`

**Interfaces:**
- Produces: console entrypoint `chgraph` with subcommands: `daemon start|stop|status|restart [repo]` (repo defaults to `git rev-parse --show-toplevel` of cwd), `index [repo]` (starts daemon if needed, enqueues index, prints job id), `status [repo]` (prints status.json), `mcp [--repo PATH]` (runs the shim — wired in Task 12; until then prints "shim not implemented" to stderr and exits 1). `daemon start` is idempotent (exit 0 "already running"); spawns `python -m chgraph.daemon <repo>` detached (`start_new_session=True`, stdout/stderr → `logs/daemon.err`).
- Consumes: `DaemonClient`, `ProjectPaths`, `read_status`.

- [ ] **Step 1: Write the failing test** — `tests/chdb/test_cli.py`

```python
import os
import subprocess
import sys
import time


def run_cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "chgraph.cli", *args],
                          capture_output=True, text=True, env=env, timeout=120)


def test_daemon_lifecycle_via_cli(tmp_path, synth_repo):
    env = {**os.environ, "CHGRAPH_DATA_DIR": str(tmp_path / "cg")}

    r = run_cli("daemon", "status", str(synth_repo), env=env)
    assert r.returncode == 1                                   # stopped

    assert run_cli("daemon", "start", str(synth_repo), env=env).returncode == 0
    for _ in range(100):                                        # wait for ready
        if run_cli("daemon", "status", str(synth_repo), env=env).returncode == 0:
            break
        time.sleep(0.1)
    assert run_cli("daemon", "start", str(synth_repo), env=env).returncode == 0  # idempotent

    r = run_cli("index", str(synth_repo), env=env)
    assert r.returncode == 0 and "idx-" in r.stdout

    assert run_cli("daemon", "stop", str(synth_repo), env=env).returncode == 0
    assert run_cli("daemon", "status", str(synth_repo), env=env).returncode == 1
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/cli.py`

```python
"""chgraph CLI. Command semantics: chgraph-run-and-operate §2."""
import argparse
import os
import signal
import subprocess
import sys
import time

from chgraph.client import DaemonClient, DaemonError
from chgraph.paths import ProjectPaths
from chgraph.status import read_status


def _repo(arg: str | None) -> str:
    if arg:
        return os.path.realpath(arg)
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else os.getcwd()


def _pid_alive(paths: ProjectPaths) -> int | None:
    try:
        pid = int(paths.pidfile.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def daemon_start(repo: str) -> int:
    paths = ProjectPaths.for_repo(repo)
    paths.ensure()
    try:
        DaemonClient(paths.socket).call("ping")
        print("already running")
        return 0
    except DaemonError:
        pass
    errlog = open(paths.log_dir / "daemon.err", "ab")
    subprocess.Popen([sys.executable, "-m", "chgraph.daemon", repo],
                     start_new_session=True, stdout=errlog, stderr=errlog)
    for _ in range(100):
        try:
            DaemonClient(paths.socket).call("ping")
            print(f"started (socket {paths.socket})")
            return 0
        except DaemonError:
            time.sleep(0.1)
    print("daemon did not come up; see logs/daemon.err", file=sys.stderr)
    return 1


def daemon_status(repo: str) -> int:
    paths = ProjectPaths.for_repo(repo)
    try:
        info = DaemonClient(paths.socket).call("ping")
        st = read_status(paths.status_json)
        print(f"running pid={info['pid']} project={info['project']} index={st['state']}")
        return 0
    except DaemonError:
        if _pid_alive(paths) or (paths.chdb_dir / "status").exists() and paths.pidfile.exists():
            print("stale — crashed (socket dead but pid/lock artifacts present)")
            return 2
        print("stopped")
        return 1


def daemon_stop(repo: str) -> int:
    paths = ProjectPaths.for_repo(repo)
    try:
        DaemonClient(paths.socket).call("shutdown")
    except DaemonError:
        pid = _pid_alive(paths)
        if pid is None:
            print("not running")
            return 0
        os.kill(pid, signal.SIGTERM)                    # escalation per run-and-operate §2
    for _ in range(100):
        if _pid_alive(paths) is None:
            print("stopped")
            return 0
        time.sleep(0.1)
    os.kill(_pid_alive(paths), signal.SIGKILL)          # safe for data (run-and-operate §3)
    print("killed")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="chgraph")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pd = sub.add_parser("daemon")
    pd.add_argument("action", choices=["start", "stop", "status", "restart"])
    pd.add_argument("repo", nargs="?")
    pi = sub.add_parser("index")
    pi.add_argument("repo", nargs="?")
    ps = sub.add_parser("status")
    ps.add_argument("repo", nargs="?")
    pm = sub.add_parser("mcp")
    pm.add_argument("--repo")
    args = ap.parse_args()

    if args.cmd == "daemon":
        repo = _repo(args.repo)
        if args.action == "restart":
            daemon_stop(repo)
            raise SystemExit(daemon_start(repo))
        raise SystemExit({"start": daemon_start, "stop": daemon_stop,
                          "status": daemon_status}[args.action](repo))
    if args.cmd == "index":
        repo = _repo(args.repo)
        if daemon_start(repo) != 0:
            raise SystemExit(1)
        job = DaemonClient(ProjectPaths.for_repo(repo).socket).call("index")
        print(f"queued {job['job_id']}; poll with `chgraph status`")
        raise SystemExit(0)
    if args.cmd == "status":
        print(read_status(ProjectPaths.for_repo(_repo(args.repo)).status_json))
        raise SystemExit(0)
    if args.cmd == "mcp":
        from chgraph import shim                        # deferred: Task 12
        shim.main(repo_root=_repo(args.repo))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/chdb/test_cli.py -v` — Expected: 1 passed

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: chgraph CLI (daemon lifecycle, index, status)"`

---

### Task 12: MCP shim (tier-1 tools)

**Skills to read first:** `mcp-server-reference` §§2–7 (topology, FastMCP recipe with VERIFIED output, tier table, registration snippet — this task implements them exactly).

**Files:**
- Create: `src/chgraph/shim.py`, `tests/unit/test_shim_imports.py`, `tests/chdb/test_shim_e2e.py`

**Interfaces:**
- Produces: `shim.main(repo_root: str) -> None` — FastMCP stdio server named `chgraph`. Tier-1 tools with reference-compatible names: `index_repository`, `index_status`, `search_graph`, `get_code_snippet`, `trace_path`, `get_graph_schema`, `list_projects`, `delete_project`. (`query_graph` is tier-1-name-OPEN-semantics per mcp-server-reference §5 — NOT in v0.1; do not stub it.) Every tool returns a pydantic model (bare dicts yield `structuredContent: None` — verified in the skill). `search_graph`'s unsupported reference params (`semantic_query` etc.) raise a clear "not supported in v0.1" error — never silently ignore. Shim auto-starts the daemon via `subprocess` of the CLI when the socket is dead, then retries; if still unreachable, tools raise (FastMCP converts to tool-error results, not protocol crashes).
- Hard constraints: `chgraph.shim` must be importable WITHOUT chdb entering `sys.modules`; no `print()` (stdout is the MCP channel).
- Consumes: `DaemonClient` (Task 10), CLI start behavior (Task 11).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_shim_imports.py`:

```python
import subprocess
import sys


def test_shim_never_imports_chdb():
    # Run in a clean interpreter: importing the shim must not pull in chdb (INV-1: only
    # the daemon touches chdb; the shim must stay cheap and lock-free).
    code = "import sys; import chgraph.shim; sys.exit(1 if 'chdb' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
```

`tests/chdb/test_shim_e2e.py` (uses the mcp client — the VERIFIED smoke pattern from mcp-server-reference §3):

```python
import os
import sys

import pytest


@pytest.mark.anyio
async def test_shim_lists_tier1_tools_and_indexes(tmp_path, synth_repo, monkeypatch):
    monkeypatch.setenv("CHGRAPH_DATA_DIR", str(tmp_path / "cg"))
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "chgraph.cli", "mcp", "--repo", str(synth_repo)],
        env={**os.environ, "CHGRAPH_DATA_DIR": str(tmp_path / "cg")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            assert {"index_repository", "index_status", "search_graph", "get_code_snippet",
                    "trace_path", "get_graph_schema", "list_projects", "delete_project"} <= tools
            assert "query_graph" not in tools          # OPEN semantics — not stubbed

            r = await session.call_tool("index_repository", {})
            assert r.structuredContent["state"] in ("queued", "running")

            import anyio
            for _ in range(300):
                st = await session.call_tool("index_status", {})
                if st.structuredContent["state"] in ("indexed", "degraded", "failed"):
                    break
                await anyio.sleep(0.2)
            assert st.structuredContent["state"] == "indexed"

            res = await session.call_tool("search_graph", {"query": "handle"})
            assert res.structuredContent["total"] >= 1
```

Add `anyio` marker support: append to `pyproject.toml` `[tool.pytest.ini_options]`: `addopts = "-p anyio"` is NOT needed — instead add fixture in `tests/conftest.py`:

```python
@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/unit/test_shim_imports.py tests/chdb/test_shim_e2e.py -v` — Expected: FAIL

- [ ] **Step 3: Implement** — `src/chgraph/shim.py`

```python
"""MCP stdio shim. Topology: mcp-server-reference §2 — the shim NEVER imports chdb;
it relays to the daemon over the unix socket. stdout is the MCP channel: no print()."""
import os
import subprocess
import sys
import time

from pydantic import BaseModel

from chgraph.client import DaemonClient, DaemonError
from chgraph.paths import ProjectPaths


class IndexJob(BaseModel):
    job_id: str | None
    state: str


class IndexStatus(BaseModel):
    state: str
    files_total: int = 0
    files_done: int = 0
    nodes_persisted: int = 0
    degraded_reasons: list[str] = []
    error: str | None = None


class SearchItem(BaseModel):
    qualified_name: str
    label: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    score: float


class SearchPage(BaseModel):
    items: list[SearchItem]
    total: int
    has_more: bool


class Snippet(BaseModel):
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    text: str


class TraceStep(BaseModel):
    node: str
    path: list[str]
    depth: int


class TracePaths(BaseModel):
    paths: list[TraceStep]


class GraphSchema(BaseModel):
    labels: list[str]
    edge_types: list[str]


class ProjectInfo(BaseModel):
    project: str
    repo_root: str
    state: str


class ProjectList(BaseModel):
    projects: list[ProjectInfo]


class Deleted(BaseModel):
    deleted: str


def _client(repo_root: str) -> DaemonClient:
    paths = ProjectPaths.for_repo(repo_root)
    client = DaemonClient(paths.socket)
    try:
        client.call("ping")
        return client
    except DaemonError:
        pass
    # Auto-start (run-and-operate §4): exec the CLI; never touch chdb here.
    subprocess.run([sys.executable, "-m", "chgraph.cli", "daemon", "start", repo_root],
                   capture_output=True, timeout=60)
    for _ in range(100):
        try:
            client.call("ping")
            return client
        except DaemonError:
            time.sleep(0.1)
    raise DaemonError(
        f"chgraph daemon unreachable for {repo_root}; try `chgraph daemon status`")


def main(repo_root: str | None = None) -> None:
    from mcp.server.fastmcp import FastMCP

    repo = os.path.realpath(repo_root or os.getcwd())
    mcp = FastMCP("chgraph")

    @mcp.tool()
    def index_repository() -> IndexJob:
        """Index this repository into the code graph (async; poll index_status)."""
        return IndexJob(**_client(repo).call("index"))

    @mcp.tool()
    def index_status() -> IndexStatus:
        """Indexing state: queued|running|indexed|degraded|failed. degraded lists reasons."""
        d = _client(repo).call("status")
        return IndexStatus(**{k: v for k, v in d.items() if k in IndexStatus.model_fields})

    @mcp.tool()
    def search_graph(query: str | None = None, name_pattern: str | None = None,
                     label: str | None = None, limit: int = 200, offset: int = 0) -> SearchPage:
        """Search graph nodes. query: text match ranked by relevance+git-recency+centrality;
        name_pattern: RE2 regex on symbol name; label: node label filter (Function, Class, File)."""
        d = _client(repo).call("search", query=query, name_pattern=name_pattern,
                               label=label, limit=limit, offset=offset)
        return SearchPage(**d)

    @mcp.tool()
    def get_code_snippet(qualified_name: str) -> Snippet:
        """Return the source code for a symbol by qualified name."""
        return Snippet(**_client(repo).call("snippet", qualified_name=qualified_name))

    @mcp.tool()
    def trace_path(function_name: str, direction: str = "callees", depth: int = 5) -> TracePaths:
        """Trace CALLS paths from a function (direction: callees|callers, depth<=10)."""
        return TracePaths(**_client(repo).call("trace", qualified_name=function_name,
                                               direction=direction, depth=depth))

    @mcp.tool()
    def get_graph_schema() -> GraphSchema:
        """List node labels and edge types present in this project's graph."""
        return GraphSchema(**_client(repo).call("schema_info"))

    @mcp.tool()
    def list_projects() -> ProjectList:
        """List projects served by this daemon (v0.1: exactly one)."""
        return ProjectList(**_client(repo).call("list_projects"))

    @mcp.tool()
    def delete_project() -> Deleted:
        """Delete this project's graph data. Explicit-only; never automatic."""
        return Deleted(**_client(repo).call("delete_project"))

    mcp.run()   # stdio


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/unit/test_shim_imports.py tests/chdb/test_shim_e2e.py -v` — Expected: 2 passed. (If the anyio plugin complains, `uv add --group dev anyio` and re-run; note in ADR-0001.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: MCP stdio shim with tier-1 reference-compatible tools"`

---

### Task 13: End-to-end proof, README, ADR-0001

**Skills to read first:** `chgraph-change-control` (ADR template §), `chgraph-run-and-operate` §4 (registration), `mcp-server-reference` §7.

**Files:**
- Create: `tests/chdb/test_e2e_staleness.py`, `README.md`, `docs/adr/0001-v01-implementation-decisions.md`

- [ ] **Step 1: Write the end-to-end staleness test** — `tests/chdb/test_e2e_staleness.py` (the campaign's thesis, asserted through the public API)

```python
"""The campaign thesis end-to-end: fresh code must outrank stale code through the
full stack (index -> evolution -> search), not just in hand-run SQL."""
from chgraph.indexer import index_repository
from chgraph.search import search_graph


def test_fresh_file_symbols_outrank_stale_file_symbols(store, synth_repo):
    res = index_repository(store, "synth", str(synth_repo))
    assert res.degraded_reasons == []
    # 'old_thing' lives in the stale legacy file; 'handle' in the 1-day-old api file.
    fresh = search_graph(store, "synth", query="handle").items[0]
    stale = search_graph(store, "synth", query="old_thing").items[0]
    assert fresh["file_path"] == "src/api.py"
    assert stale["file_path"] == "src/core/legacy.py"
    assert fresh["score"] > stale["score"]   # recency separates them (campaign Phase 5 control)
```

Run: `uv run pytest tests/ -v` — Expected: ALL tests green (≈30 tests).

- [ ] **Step 2: Write `README.md`**

```markdown
# chgraph

A chdb-backed codebase knowledge-graph MCP server. Indexes your repo's symbols
(tree-sitter) and full git history (churn, co-change, ownership, recency) into
embedded ClickHouse, and serves reference-compatible MCP tools whose ranking
demotes stale code.

**Status: v0.1.** macOS/Linux only (chdb has no Windows wheels). Retrieval
quality is unproven — the eval harness is the next milestone; no quality claims
are made or permitted until it reports numbers (see
`.claude/skills/chgraph-validation-and-qa/`).

## Setup

    uv sync                     # Python 3.12 venv; ~554MB (chdb embeds ClickHouse)
    uv run pytest               # verify

## Use with Claude Code

`.mcp.json` in your project (absolute paths — clients spawn servers with
unpredictable cwd):

    {
      "mcpServers": {
        "chgraph": {
          "command": "/absolute/path/to/chgraph/.venv/bin/chgraph",
          "args": ["mcp"]
        }
      }
    }

The shim auto-starts the daemon. Then, from any agent session:
`index_repository` → poll `index_status` → `search_graph` / `trace_path` /
`get_code_snippet`.

Data lives under `~/.local/share/chgraph/<project-slug>/` (override:
`$CHGRAPH_DATA_DIR`). One daemon per project — chdb's data-dir lock is
exclusive; everything routes through the daemon.

Project doctrine, runbooks, and design rationale: `.claude/skills/`.
```

- [ ] **Step 3: Write `docs/adr/0001-v01-implementation-decisions.md`** — record, in the change-control ADR format, every deviation/refinement this implementation made against the founding skills, at minimum: (a) git tables idempotency via TRUNCATE-then-reload (one project per data dir makes it safe; incremental append deferred); (b) the `uninitialized` status state added before first index (extends the mcp-server-reference §4 enum — needs sign-off); (c) `MIN_NODES_PER_KLOC = 5.0` as an OPEN placeholder threshold pending calibration; (d) `query_graph` and `search_code`/tier-2 tools deliberately absent from v0.1; (e) vector weight structurally present but contributing 0 (no embeddings yet); (f) any API drift adaptations from Tasks 6/8/12 steps. Each entry: context → decision → consequences → status (accepted/needs-review).

- [ ] **Step 4: Full-suite run + lock-safety spot check**

Run: `uv run pytest tests/ -v` — Expected: all green.
Then the INV-1 sanity check — with a daemon running on a repo, a second `chgraph daemon start` for the same repo must print "already running" and exit 0 (never a chdb lock traceback).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: e2e staleness test, README, ADR-0001"`

---

## Explicitly OUT of v0.1 (do not build; separate plans)

| Deferred | Why | Where it's specced |
|---|---|---|
| Eval harness + golden set | Own plan; on the critical path of the FIRST retrieval-affecting change after v0.1 | validation-and-qa §2/§6 |
| Embeddings + vector signal | Needs embedding-model decision; brute-force cosineDistance only when it comes (HNSW is compiled out) | campaign Phase 5 |
| `query_graph` (Cypher) | Semantics OPEN (translation vs templates vs raw SQL) — a change-control decision, not an implementation detail | mcp-server-reference §5 |
| Rename-chain handling | The known hard part; three ranked candidates with theory obligations | campaign Phase 4 |
| More languages, closure tables, `evolution_*` MCP tools, team-share export/import | v0.2+ | campaign / run-and-operate §6 |

## Self-review notes

- Type consistency: `Store.rows -> list[dict]` everywhere; `SearchPage`/`IndexResult`/`GitIngestCounts` field names match between producer tasks and daemon/shim consumers; daemon op names match `DaemonClient.call` usage in CLI (Task 11) and shim (Task 12); shim pydantic models mirror daemon op payloads field-for-field.
- Spec coverage: canonical DDL (T3), campaign Phases 1–5 productionized (T4/T5/T8), INV-1 (daemon+worker, T10), INV-2 (T9), INV-3 (T7/T10), INV-5 (batch+FINAL throughout), INV-6 (T3 version gate), INV-7 (pins, T1), run-and-operate lifecycle+exit codes (T11), mcp-server-reference tier-1 table+typed output (T12).
- Known risks for implementers: tree-sitter binding API drift (T6 step 4 has the protocol), `count() OVER ()`+FINAL interplay (T8 step 4 fallback), chdb-in-subprocess test latency (T10). Each has an explicit branch — none requires improvisation.

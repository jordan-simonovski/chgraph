---
name: chgraph-build-and-env
description: Use when recreating or repairing the chgraph development environment - fresh clone, new machine, broken venv, "ModuleNotFoundError: chdb", "pip install chdb==26.5.0" finding no version, mcp refusing Python 3.9, "No module named pip" inside .venv, a 5-second first chdb import, or questions about Windows/Linux/macOS support, install size, or whether a ClickHouse binary is needed. Covers uv, Python 3.12, chdb/mcp/tree-sitter/pytest pins, and known setup traps as of 2026-07-03.
---

# chgraph: Build and Environment Setup

Recreate the chgraph dev environment from zero in ~10 minutes. Every command below was executed end-to-end on 2026-07-03 in a fresh directory on macOS arm64; outputs shown are real observed output, not projections.

**Evidence labels used here:** VERIFIED = executed locally with output seen. REPORTED = from research, source URL given. DECIDED = design decision. OPEN = unproven.

## The 10-minute checklist

Run from the repo root (`chgraph/`). The venv lives at `.venv` in the repo (DECIDED convention).

- [ ] **1. Install uv** (a fast Python package/venv manager). Skip if `uv --version` works.
  ```sh
  brew install uv        # macOS; Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
  uv --version           # VERIFIED present: uv 0.11.25 (Homebrew 2026-06-26 aarch64-apple-darwin)
  ```
- [ ] **2. Create the venv on Python 3.12.** Do NOT use system `python3` (see trap T2).
  ```sh
  uv venv --python 3.12 .venv
  ```
  Observed (VERIFIED):
  ```
  Using CPython 3.12.13 interpreter at: /opt/homebrew/opt/python@3.12/bin/python3.12
  Creating virtual environment at: .venv
  ```
- [ ] **3. Install the four core dependencies.** `VIRTUAL_ENV=` targeting works without activating; interactively you can `source .venv/bin/activate` first and drop the prefix.
  ```sh
  VIRTUAL_ENV=$PWD/.venv uv pip install "chdb==4.2.0" mcp tree-sitter pytest
  ```
  Verified-good set as of 2026-07-03 (VERIFIED via `uv pip list` after install):
  `chdb 4.2.0` + `chdb-core 26.5.0` (pulled automatically), `mcp 1.28.1`, `tree-sitter 0.26.0`, `pytest 9.1.1`, plus `pandas 3.0.3`, `pyarrow 24.0.0`, `numpy 2.5.0` dragged in by chdb. Pin `chdb==4.2.0` always (see trap T1); bump the others deliberately.
- [ ] **4. Verify chdb.** First run is slow — that is normal (trap T4).
  ```sh
  .venv/bin/python -c "import chdb; print('chdb', chdb.__version__, 'engine', chdb.engine_version); print(chdb.query('SELECT version()', 'CSV'))"
  ```
  Observed (VERIFIED):
  ```
  chdb 26.5.0 engine 26.5.1.1
  "26.5.1.1"
  ```
  (First invocation took 5.3s wall; the same command warm takes ~0.14s.)
- [ ] **5. Verify the rest.**
  ```sh
  .venv/bin/python -c "import mcp, tree_sitter, pytest; print('mcp ok; tree_sitter ok; pytest', pytest.__version__)"
  ```
  Observed (VERIFIED): `mcp ok; tree_sitter ok; pytest 9.1.1`
- [ ] **6. (Optional) Smoke-test a persistent chdb session** — proves on-disk data dirs work. Use a throwaway dir; a chdb data dir is exclusively locked by one process (fact owned by **chdb-reference**).
  ```python
  from chdb import session
  s = session.Session("/tmp/chgraph-smoke")   # throwaway dir
  s.query("CREATE DATABASE IF NOT EXISTS smoke; "
          "CREATE TABLE IF NOT EXISTS smoke.t (x UInt8) ENGINE = MergeTree ORDER BY x; "
          "INSERT INTO smoke.t VALUES (1)")
  print(s.query("SELECT count() FROM smoke.t", "CSV"))   # VERIFIED prints: 1
  s.close()
  ```

Done. There is no `pyproject.toml` yet (repo founded 2026-07-03, pre-code); when one is created it must encode these pins, and any later dependency change that alters retrieval behavior or the tool surface goes through **chgraph-change-control** (OPEN: pyproject not yet written).

## Trap list (each one cost real time — read before debugging)

| # | Trap | Fact |
|---|------|------|
| T1 | `pip install chdb==26.5.0` fails: "no version of chdb==26.5.0" | VERIFIED 2026-07-03. PyPI splits the versioning: the wrapper package is `chdb` **4.2.0**, which pins `chdb-core` **26.5.0** (the engine-tracking scheme). Confusingly, `chdb.__version__` reports `26.5.0` while `pip list` shows `chdb 4.2.0`. Pin `chdb==4.2.0`; when quoting "chdb 26.5.0" you mean the core/`__version__`. |
| T2 | System `python3` is 3.9.6 → install fails | VERIFIED: on this machine `/usr/bin/python3 --version` = `Python 3.9.6`, and `mcp` hard-fails resolution there: "all versions of mcp depend on Python>=3.10". (chdb itself resolves on 3.9, so the visible breakage is mcp — but the project standard is 3.12, DECIDED.) Always step 2 first. |
| T3 | Huge install | VERIFIED: fresh venv is **554MB** total; the chdb package alone is **329MB** and drags in pandas + pyarrow + numpy. Budget disk and (cold-cache) download time; with a warm uv cache the install itself took 1.5s. |
| T4 | First `import chdb` takes ~5s | VERIFIED: 5.3s first run, 0.14s warm on Apple Silicon. Likely macOS code-signature verification of the large dylib (OPEN — cause not proven, only the timing is). Do not "fix" this; do not put import inside a request hot path assuming it is always fast on first hit. |
| T5 | Fork-safety: querying chdb in a subprocess after importing chdb in the parent can hang | REPORTED: chdb-io/chdb issue #355, https://github.com/chdb-io/chdb/issues/355 (listed closed, but the pattern is the risk). Matters directly to chgraph: the daemon shells out to `git` for the evolution campaign. Mitigation (spawn-not-fork, or exec git before importing chdb) is OPEN — owned by **chgraph-run-and-operate** once the daemon exists. |
| T6 | `python -m pip` fails inside the venv: "No module named pip" | VERIFIED. uv venvs ship without pip by default. Use `uv pip ...` (with `VIRTUAL_ENV=$PWD/.venv` or an activated shell), never `python -m pip`. |
| T7 | Two processes, one data dir → hard failure | Cross-reference: the exclusive `status`-file lock (VERIFIED, even read-only) is owned by **chdb-reference**; the daemon+socket answer is owned by **chgraph-architecture-contract**. Setup-relevant corollary: never point a second REPL at a data dir a daemon owns. |

## Platform matrix (as of 2026-07-03, chdb 4.2.0 / chdb-core 26.5.0)

| Platform | Status | Evidence |
|---|---|---|
| macOS arm64 | Works | VERIFIED — this runbook, Darwin 25.5.0, Apple Silicon |
| macOS x86_64 | Wheel exists, untested | VERIFIED wheel on PyPI (`macosx_10_15_x86_64`); runtime OPEN |
| Linux x86_64 / aarch64 | Supported, untested here | VERIFIED manylinux2014 wheels on PyPI; supported per https://github.com/chdb-io/chdb — runtime here OPEN (CI should close this) |
| Windows | **NOT supported. Hard fact.** | VERIFIED: PyPI chdb-core 26.5.0 ships zero Windows wheels (queried 2026-07-03). Consolation: the reference tool's Windows story is also broken — an 8-bug Windows tracker, REPORTED https://github.com/DeusData/codebase-memory-mcp/issues/394 |

WSL2 as a Windows path is plausible but untested (OPEN).

## Local machine facts (jordan's dev box, 2026-07-03)

- `/usr/bin/python3` = 3.9.6 — too old, see T2. Homebrew CPython 3.12.13 is what uv picks. VERIFIED.
- Node v22.22.3 present — **irrelevant to v1**: chgraph is Python-only (DECIDED; chdb-node is second-tier, REPORTED https://github.com/chdb-io/chdb).
- No standalone `clickhouse` binary installed (`which clickhouse` → not found, VERIFIED) and **none is needed** — chdb embeds the full engine in-process (DECIDED architecture; engine 26.5.1.1 confirmed via `SELECT version()` above).
- Grammar wheels for py-tree-sitter (e.g. `tree-sitter-python`) are not yet chosen — OPEN, language list owned by **code-graph-reference**.

## When NOT to use this

- Starting/stopping/operating the daemon or MCP shim → **chgraph-run-and-operate**.
- chdb SQL semantics, lock behavior details, index types, recursive CTEs → **chdb-reference**.
- A previously-working env now failing at runtime → **chgraph-debugging-playbook** (this skill is for building, not diagnosing).
- Adding/upgrading dependencies that change schema, retrieval behavior, or the tool surface → **chgraph-change-control** first.
- MCP protocol/SDK usage questions → **mcp-server-reference**.

## Provenance and maintenance

Grounded by executing every command above on 2026-07-03 in a fresh throwaway directory on macOS arm64 (uv 0.11.25, CPython 3.12.13, chdb 4.2.0 / chdb-core 26.5.0 / engine 26.5.1.1, mcp 1.28.1, tree-sitter 0.26.0, pytest 9.1.1), plus PyPI metadata queries and Phase-1 research (chdb-io/chdb, DeusData/codebase-memory-mcp). Outputs shown are pasted, not synthesized.

Re-verification one-liners (run when anything smells stale):

| What may drift | Command |
|---|---|
| chdb wrapper version on PyPI | `curl -s https://pypi.org/pypi/chdb/json \| python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"` (expect `4.2.0`; a dry-run install in the project venv prints only "Would make no changes" once chdb is installed — no version signal) |
| Installed wrapper/core pair | `VIRTUAL_ENV=$PWD/.venv uv pip list \| grep chdb` (expect `chdb 4.2.0` + `chdb-core 26.5.0`) |
| Installed chdb + engine version | `.venv/bin/python -c "import chdb; print(chdb.__version__, chdb.engine_version)"` |
| Windows wheels appearing | `curl -s https://pypi.org/pypi/chdb-core/json \| grep -c win_amd64` (0 = still unsupported) |
| mcp Python floor | `uv pip install --dry-run --python 3.9 --system mcp` (should still refuse: "all versions of mcp depend on Python>=3.10"; without `--system` uv errors with "No virtual environment found for Python 3.9" and never exercises the floor) |
| Fork-safety issue status | open https://github.com/chdb-io/chdb/issues/355 |
| First-import latency | `time .venv/bin/python -c "import chdb"` on a cold cache |
| Venv weight | `du -sh .venv` (~554MB expected) |

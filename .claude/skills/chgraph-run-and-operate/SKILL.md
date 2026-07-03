---
name: chgraph-run-and-operate
description: Use when starting, stopping, or checking a chgraph daemon, wiring the MCP shim into .mcp.json, deciding where chgraph data/sockets/pidfiles live, kicking off or polling indexing, backing up or sharing a graph, or hitting errors like "Cannot lock file .../status", "Another server instance in same directory is already running", "Error initializing EmbeddedServer", or "EmbeddedServer already initialized with path". Covers what file lands where and crash recovery after a killed daemon.
---

# chgraph-run-and-operate

> **This is the operating contract v1 (2026-07-03).** chgraph does not exist as code yet. Commands marked **DECIDED** describe the CLI to be built — they will not run today. chdb-level commands and behaviors marked **VERIFIED** were executed on 2026-07-03 against chdb 26.5.0 (engine 26.5.1.1, macOS arm64) and the real observed output is shown. **REPORTED** facts cite an external source; **OPEN** items are unproven candidates.

Terms used once, defined once:

- **chdb** — in-process ClickHouse engine (Python package). A "Session" is a chdb handle bound to an on-disk data directory.
- **daemon** — the single long-lived chgraph process that owns one chdb data directory (see `chgraph-architecture-contract` for why this is forced).
- **shim** — the thin MCP stdio process each agent session spawns; it never touches chdb, it only relays to the daemon over a unix socket.
- **status file** — chdb's own lockfile (`<chdb-dir>/status`), distinct from chgraph's `status.json`.

## 1. Data directory convention — DECIDED

One directory per project, under the XDG data home:

```
~/.local/share/chgraph/<project-slug>/
├── chdb/              # chdb Session data dir (exclusive-locked while daemon runs)
│   ├── status         # chdb's lockfile: PID, start time (chdb-managed — never touch, see §3)
│   ├── data/  metadata/  store/  tmp/   # native ClickHouse layout (VERIFIED, see §7)
├── daemon.sock        # unix socket the shims connect to
├── daemon.pid         # chgraph daemon pidfile (plain PID, newline-terminated)
├── status.json        # chgraph-level status: index state machine, degraded reasons
└── logs/daemon.log    # daemon log, rotated
```

Overrides: `$XDG_DATA_HOME` respected if set; `$CHGRAPH_DATA_DIR` replaces the whole `~/.local/share/chgraph` root.

**project-slug derivation — DECIDED:** `<sanitized-basename>-<first-8-hex-of-sha256(realpath(repo-root))>`, e.g. `chgraph-3fa2b91c`. Basename alone collides across same-named checkouts; full-path hashing alone is unreadable in `ls`.

Rationale for each choice:

| Choice | Why (label) |
|---|---|
| `~/.local/share` not `~/.cache` | A graph takes minutes-to-hours to build; OS/cache cleaners may purge `~/.cache`. The reference tool uses `~/.cache/codebase-memory-mcp/` (REPORTED: https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/store/store.c) — we deliberately diverge. DECIDED. |
| One dir per project, one daemon per dir | A single OS process can host only ONE chdb embedded server, pinned to one path. VERIFIED — opening a second Session with a different path in the same process fails: `Code: 36. DB::Exception: EmbeddedServer already initialized with path '/…/data', cannot connect with different path '/…/restore/data'. (BAD_ARGUMENTS)`. So one daemon cannot serve two projects' dirs. |
| Socket lives next to the data, not in `$TMPDIR` | Discoverable from the slug alone; survives `$TMPDIR` differences between shells/agents. Path stays well under the ~104-byte macOS `sockaddr_un` limit. DECIDED. |
| Both `daemon.pid` and chdb's `status` | `daemon.pid` gives a fast, friendly liveness check; chdb's flock is the authoritative backstop that makes a second owner physically impossible (§2). DECIDED. |

## 2. Daemon lifecycle — DECIDED spec, VERIFIED enforcement

**Exactly-one-owner rule.** chdb takes an exclusive OS lock (flock) on `<chdb-dir>/status` for the life of the Session. VERIFIED with two processes on chdb 26.5.0 — the second opener's stderr shows:

```
Code: 76. DB::Exception: Cannot lock file /tmp/…/data/status. Another server instance in same directory is already running. (CANNOT_OPEN_FILE)
```

and the Python exception the second process actually catches is the wrapper:

```
Failed to create connection: Code: 36. DB::Exception: Error initializing EmbeddedServer: . (BAD_ARGUMENTS)
```

Operators: **Code 36 "Error initializing EmbeddedServer" in a chgraph log almost always means a second process tried to open an already-owned chdb dir** — look for the Code 76 line on stderr to confirm. Read-only mode does NOT bypass the lock (VERIFIED by Phase 1 on 4.2.0; lock behavior re-verified on 26.5.0).

**CLI semantics (DECIDED — commands of the future `chgraph` CLI, not runnable today):**

| Command | Semantics |
|---|---|
| `chgraph daemon start [<repo-root>]` | Resolve slug from repo root (default: cwd's git toplevel). Create the project dir if absent. Refuse if `daemon.pid` names a live process (exit 0, "already running" — idempotent). Open the chdb Session, bind `daemon.sock`, write `daemon.pid` and initial `status.json`, then daemonize. If chdb raises the Code 36/76 pair above, print a friendly "another chgraph daemon (or stray process) owns <dir>; run `chgraph daemon status`" and exit 1. |
| `chgraph daemon status [<repo-root>]` | Ping over `daemon.sock`. Report: daemon PID, uptime, chdb dir, and the index state from `status.json`. If the socket is dead but `daemon.pid`/`status` files exist, report "stale — crashed" (see §3). Exit codes: 0 running, 1 stopped, 2 stale/crashed. |
| `chgraph daemon stop [<repo-root>]` | Graceful: send shutdown over the socket → daemon finishes in-flight queries, `session.close()`, removes `daemon.sock` + `daemon.pid`, exits. Escalation: SIGTERM after 10s, SIGKILL after 30s (SIGKILL is safe for data — see §3 — but loses in-flight index progress). |
| `chgraph daemon restart [<repo-root>]` | `stop` then `start`. |

Shims never call `Session()` themselves; a shim that cannot reach `daemon.sock` auto-starts the daemon (DECIDED) so users normally never run `daemon start` by hand.

## 3. Crash recovery — VERIFIED

The lock is held by the OS on the open file descriptor, **not** by the existence of the `status` file. VERIFIED end-to-end on chdb 26.5.0: a holder process was killed with `kill -9`, the stale `status` file remained on disk (`PID: 42737 / Started at: 2026-07-03 15:15:19 / Revision: 54510`), and a fresh process immediately opened the same dir and queried successfully — **no cleanup of any kind was needed**.

Recovery table:

| Observation | Meaning | Action |
|---|---|---|
| `daemon.sock` answers ping | Healthy | Nothing. |
| Socket dead; PID in `daemon.pid` not running; `chdb/status` exists | Daemon crashed/killed | Just `chgraph daemon start`. The stale `status` file is harmless (VERIFIED). Delete nothing. |
| Socket dead; PID in `daemon.pid` IS running | Daemon wedged (alive but unresponsive) | SIGTERM the PID, wait, SIGKILL, then start. Do NOT delete `chdb/status` — the wedged process still holds the flock, and deleting the file is the one way to let a second embedded server open the dir concurrently → corruption risk. |
| Start still fails with Code 76 and no chgraph process exists | Some OTHER process (a stray script, a REPL) holds a Session on the dir | `lsof <chdb-dir>/status` to find the holder; kill it. Escalate to `chgraph-debugging-playbook`. |

**When is it safe to remove `chdb/status`?** Practically never as a fix — a crashed daemon needs no removal (VERIFIED), and removing it under a live holder defeats the lock. Only remove it as part of deliberately discarding/rebuilding the whole `chdb/` dir. Anything weirder (Code 76 with no visible holder, corrupt metadata on start) → `chgraph-debugging-playbook`.

## 4. MCP shim registration — DECIDED

The registration snippet's one home is `mcp-server-reference` §7 — copy the JSON from there, don't restate it here. Shape summary: a project-level `.mcp.json` entry whose `command` is the absolute path to the venv's `chgraph` entry point with `args: ["mcp"]` (the planned shim entrypoint; whether the shim is a separate package or the `chgraph mcp` subcommand is OPEN, tracked in `mcp-server-reference` §2).

- The shim infers the repo root from its cwd (MCP clients launch stdio servers in the project dir); `--repo <path>` overrides.
- Until chgraph is packaged/on PATH, `command` will be the absolute path to the repo venv's entry point (`<checkout>/.venv/bin/chgraph`) — venv layout and the python-3.9-vs-3.12 trap are owned by `chgraph-build-and-env`.
- The shim is stateless and cheap: many agent sessions on one project = many shims, one daemon, one lock. This is the whole point of the shim/daemon split (`chgraph-architecture-contract`).

## 5. Index lifecycle — DECIDED (semantics owned by the architecture contract)

Operator flow, using the reference-compatible tool names:

1. `index_repository` — returns immediately with a job id; indexing runs async in the daemon.
2. Poll `index_status` — states: `queued → running (with progress: files parsed / total) → indexed | degraded | failed` (state names and the status-field schema are owned by `mcp-server-reference` §4 — `indexed`, not `ok`, for reference-tool compatibility).
3. Query (`search_graph`, `trace_path`, `query_graph`, …) — allowed in `indexed` and `degraded`; in `degraded`, every query response carries a warning banner naming the degradation.

`degraded` is a first-class honest state, never silently mapped to "indexed": it means the graph is queryable but incomplete, with machine-readable `reasons[]` (e.g. per-language parse-failure counts, node-count plausibility failures). This is a direct response to the reference tool's silent-degradation failures — a 72k-LOC repo indexed to ~500 nodes with status "indexed" (REPORTED: https://github.com/DeusData/codebase-memory-mcp/issues/333) and a reactive `CBM_DUMP_VERIFY_MIN_RATIO` knob added afterwards (REPORTED: reference README).

The exact state machine and tool schemas are owned by `mcp-server-reference` (§4, the status-field home); the degradation thresholds and their gate definitions are owned by `chgraph-validation-and-qa` (§5); the status-honesty invariant itself by `chgraph-architecture-contract` (INV-3). Any change to them routes through `chgraph-change-control` — do not tweak states or thresholds from an ops session.

## 6. Backup / team-share convention — DECIDED, mechanics VERIFIED

Convention (DECIDED): the shareable artifact is a zstd-compressed tar of the closed `chdb/` dir, named `graph.chdb.tar.zst`, placed in the repo's `.chgraph/` dir for teammates to restore instead of reindexing — mirroring the reference tool's team-share pattern (`.codebase-memory/graph.db.zst`, REPORTED: https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md). Future CLI: `chgraph export` / `chgraph import` (DECIDED) which stop/pause the daemon around the copy.

**Rule: never archive a live dir.** MergeTree writes parts as multiple files; tarring mid-write captures torn parts. Stop the daemon first. (An engine-level online `BACKUP TABLE` alternative is OPEN — unverified in chdb.)

Manual mechanics, VERIFIED today on macOS (bsdtar) against a real chdb 26.5.0 dir:

```bash
# create (daemon stopped):
tar --use-compress-program=zstd -cf graph.chdb.tar.zst -C ~/.local/share/chgraph/<slug> chdb
# restore:
tar -xf graph.chdb.tar.zst -C <destination>
```

Observed: a 1-table demo dir compressed to 1,686 bytes; the restored dir opened and returned the correct row count. Two traps hit and confirmed:
- **Extraction with `--use-compress-program=zstd` FAILS on macOS bsdtar** ("Error opening archive: Unrecognized archive format") because bsdtar passes no `-d`. Plain `tar -xf` auto-detects zstd and works (VERIFIED).
- The archive may contain a stale `status` file (e.g. dir last closed by a crash). Harmless — the restored dir opened fine with it present (VERIFIED).

## 7. What-output-lands-where

| Artifact | Location | Written by | Label |
|---|---|---|---|
| Graph tables (nodes/edges/side tables) | `~/.local/share/chgraph/<slug>/chdb/` — native ClickHouse layout: `data/`, `metadata/`, `store/`, `tmp/` (VERIFIED layout on 26.5.0) | daemon (chdb) | DECIDED location, VERIFIED contents |
| chdb lockfile | `<slug>/chdb/status` — present while daemon runs AND after a crash; absent after clean close (VERIFIED both) | chdb | VERIFIED |
| Unix socket | `<slug>/daemon.sock` | daemon | DECIDED |
| Pidfile | `<slug>/daemon.pid` | daemon | DECIDED |
| Index/degradation state | `<slug>/status.json` | daemon | DECIDED |
| Logs | `<slug>/logs/daemon.log` | daemon | DECIDED |
| Team-share artifact | `<repo>/.chgraph/graph.chdb.tar.zst` | `chgraph export` | DECIDED |
| Repo-level config (if any) | `<repo>/.chgraph/config.toml` | user | OPEN — shape undecided |

Nothing chgraph-owned is ever written inside the indexed repo except `.chgraph/`.

## When NOT to use this

- **Setting up the dev environment, venv, installing chdb** → `chgraph-build-and-env` (owns the uv/.venv/python-3.12 convention and the system-python-3.9 trap).
- **Schema, tool names/semantics, state-machine definitions, daemon-vs-shim architecture rationale** → `chgraph-architecture-contract`.
- **Changing any convention in this file** (paths, lifecycle semantics, tool surface, index states) → `chgraph-change-control` — this skill describes the contract, it does not authorize amending it.
- **chdb SQL behavior, index types, recursive CTEs, session API details** → `chdb-reference`.
- **A daemon that won't start/recover after following §3** → `chgraph-debugging-playbook`.
- **What the reference tool does and its issue history** → `code-graph-reference`. **MCP protocol mechanics** → `mcp-server-reference`.

## Provenance and maintenance

Grounded 2026-07-03 by direct experiments against chdb 26.5.0 (engine 26.5.1.1) in a python-3.12 venv on macOS arm64: two-process lock collision, `kill -9` + stale-status reopen, one-embedded-server-per-process path pinning, clean-close vs crash `status`-file presence, and the tar+zstd backup/restore round trip — all outputs above are pasted from those runs. DECIDED items trace to the user-confirmed design decisions of 2026-07-03; REPORTED items carry source URLs inline.

Re-verify when chdb is upgraded or behavior looks off (use a throwaway dir, never a real project dir; `python` below must be a 3.12 venv python with chdb installed — see `chgraph-build-and-env`):

```bash
# chdb version pin (expect 26.5.0 / 26.5.1.1 as of 2026-07-03):
python -c "import chdb; print(chdb.__version__, chdb.engine_version)"

# exclusive lock still holds (expect Code 76 on stderr, Code 36 in the exception):
D=$(mktemp -d); python -c "
from chdb import session; import time
s=session.Session('$D'); time.sleep(8)" &
sleep 3; python -c "
from chdb import session
try: session.Session('$D')
except Exception as e: print(e)"; wait

# crash recovery still lock-free (expect SUCCESS):
D=$(mktemp -d); python -c "
from chdb import session; import time
s=session.Session('$D'); time.sleep(60)" & P=$!; sleep 5; kill -9 $P; sleep 1
python -c "from chdb import session; session.Session('$D'); print('SUCCESS')"

# reference tool's tool list unchanged (expect exactly 14 names incl. index_status; verified 2026-07-03):
curl -s https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c | grep -oE '^\s*\{"[a-z_]+", "' | grep -oE '"[a-z_]+"' | sort -u

# bsdtar zstd auto-detect on this machine:
echo hi > /tmp/z.txt && tar --use-compress-program=zstd -cf /tmp/z.tar.zst -C /tmp z.txt && tar -tf /tmp/z.tar.zst
```

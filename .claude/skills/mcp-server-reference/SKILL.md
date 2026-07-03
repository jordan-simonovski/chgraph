---
name: mcp-server-reference
description: Use when working on chgraph's MCP layer — tool definitions and JSON schemas, tools vs resources, stdio transport, structured output, async indexing status over MCP, registering the server in .mcp.json or claude_desktop_config.json, or naming tools. Home of the codebase-memory-mcp 14-tool compatibility contract (tier-1/tier-2/not-planned) and the evolution_* extension namespace. Keywords: FastMCP, mcp SDK, protocol 2025-11-25, stdio shim, structuredContent, outputSchema.
---

# MCP Server Reference: protocol knowledge pack + chgraph tool-surface compatibility contract

**Status: founding doctrine, written 2026-07-03, before any chgraph code exists.** Everything about the reference tool (DeusData/codebase-memory-mcp) is REPORTED with source URLs. Everything shown with output was run locally on 2026-07-03 (macOS arm64, Python 3.12, `mcp` 1.28.1, chdb 26.5.0) and is VERIFIED. Design choices are DECIDED with rationale; unproven candidates are OPEN.

## 1. MCP in 60 seconds (as it applies to chgraph)

**MCP (Model Context Protocol)** is a JSON-RPC protocol that lets an AI client (Claude Code, Claude Desktop, Zed, ...) discover and call capabilities exposed by a **server** process. The pieces chgraph cares about:

| Concept | What it is | chgraph relevance |
|---|---|---|
| **Tool** | A named, model-invocable function with a JSON Schema for inputs (and optionally outputs). The model decides when to call it. | The entire chgraph query surface is tools (Section 5). |
| **Resource** | Application-controlled content (client/user decides to attach it, the model does not call it). | Mostly avoided — see tradeoff below. |
| **stdio transport** | Client spawns the server as a subprocess; JSON-RPC flows over stdin/stdout. **stdout is the protocol channel — any stray `print()` to stdout corrupts framing. Log to stderr only.** | chgraph's client-facing process is a stdio server (the "shim", Section 2). |
| **Structured tool output** | `structuredContent` in tool results, validated against a declared `outputSchema`. Added in spec 2025-06-18. | Use for every chgraph tool — typed graph rows, not prose. VERIFIED recipe in Section 3. |
| **Spec version** | Client and server negotiate a protocol date-version at `initialize`. | **VERIFIED 2026-07-03:** `mcp` 1.28.1 (Python SDK) negotiates `2025-11-25`. The 2025-11-25 spec era added long-running/async operation support (REPORTED: https://modelcontextprotocol.io/specification — see also https://modelcontextprotocol.io/specification/2025-06-18/server/tools for structured output). |

**Tools vs resources tradeoff (REPORTED):** resources are application-controlled and most clients surface them poorly, so memory/code-graph servers in practice expose everything as tools; consider resources only for bulk artifacts like a full graph snapshot (source: https://modelcontextprotocol.io/specification/2025-06-18/server/tools, corroborated by the reference tool exposing 14 tools and zero resources — https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c). **DECIDED:** chgraph v1 exposes tools only. Adding a resource later is a tool-surface change → route through **chgraph-change-control**.

## 2. Topology: the shim is the MCP server, the daemon owns chdb

Every MCP client spawns its **own** stdio server subprocess. Two Claude Code windows on one project = two server processes. chdb's data directory takes an exclusive lock that even read-only mode does not bypass (VERIFIED fact owned by **chdb-reference** — do not re-derive it here), so naive "each stdio process opens chdb" hard-fails on the second client.

**DECIDED (locked 2026-07-03):**

```
Claude Code #1 ──stdio──> chgraph shim #1 ──┐
                                            ├──unix socket──> chgraph daemon ──> chdb Session (sole owner of data dir)
Claude Code #2 ──stdio──> chgraph shim #2 ──┘
```

- The **shim** is a thin MCP stdio server: it declares the tool schemas, forwards calls over a local unix socket, and never imports chdb.
- The **daemon** is the single process that owns the chdb session; it serializes writes and answers all shims.
- MCP implications: the shim must be cheap to spawn (clients start it per session), must auto-start or wait for the daemon, and must return an MCP tool **error result** (not a protocol crash) when the daemon is unreachable. Daemon lifecycle, socket path, and startup/retry policy are owned by **chgraph-run-and-operate**.
- **OPEN:** whether the shim is a separate small package or a subcommand of one CLI (`chgraph mcp`) — a projection of the not-yet-built CLI either way.

## 3. Python SDK path (VERIFIED 2026-07-03)

Install (into the project venv per **chgraph-build-and-env** — the uv-managed Python 3.12 `.venv`; system python3 is 3.9.6 and is a trap):

```bash
uv pip install mcp   # VERIFIED: installs mcp 1.28.1 as of 2026-07-03
python -c "import importlib.metadata; print(importlib.metadata.version('mcp'))"
# 1.28.1
```

### Smallest working server (one tool, stdio)

```python
"""minimal_server.py — smallest possible MCP server: one tool, stdio transport."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chgraph-smoke")

@mcp.tool()
def index_status(project: str) -> dict:
    """Report indexing status for a project (stub)."""
    return {"project": project, "state": "idle", "degraded": False}

if __name__ == "__main__":
    mcp.run()  # stdio transport by default
```

### Smoke check (real client over stdio)

```python
"""smoke_client.py — spawn the server as a subprocess and call its tool."""
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="python", args=["minimal_server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, "| protocol:", init.protocolVersion)
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])
            result = await session.call_tool("index_status", {"project": "demo"})
            print("call_tool result:", result.content[0].text)
            print("structuredContent:", result.structuredContent)

asyncio.run(main())
```

**Observed output (VERIFIED 2026-07-03, mcp 1.28.1, Python 3.12):**

```
server: chgraph-smoke | protocol: 2025-11-25
tools: ['index_status']
call_tool result: {
  "project": "demo",
  "state": "idle",
  "degraded": false
}
structuredContent: None
```

Two lessons from that run, both VERIFIED:

1. **FastMCP logs to stderr** (`Processing request of type CallToolRequest` lines appear on stderr) and the session survives — the framework respects the stdout-is-protocol rule. Keep it that way: never `print()` in server code.
2. **A bare `dict` return gives `structuredContent: None`.** To get real structured output you must return a typed model:

```python
from pydantic import BaseModel

class IndexStatus(BaseModel):
    project: str
    state: str
    degraded: bool

@mcp.tool()
def index_status(project: str) -> IndexStatus:
    """Report indexing status for a project (stub)."""
    return IndexStatus(project=project, state="idle", degraded=False)
```

**Observed with the typed version (VERIFIED, same session setup):**

```
outputSchema: {"properties": {"project": {"title": "Project", "type": "string"}, "state": {"title": "State", "type": "string"}, "degraded": {"title": "Degraded", "type": "boolean"}}, "required": ["project", "state", "degraded"], "title": "IndexStatus", "type": "object"}
structuredContent: {'project': 'demo', 'state': 'idle', 'degraded': False}
```

**DECIDED:** every chgraph tool returns a pydantic model so clients get `outputSchema` + `structuredContent`. FastMCP also auto-derives the `inputSchema` from type hints (VERIFIED: `{"properties": {"project": {"title": "Project", "type": "string"}}, "required": ["project"], ...}`).

## 4. Long-running operations: async indexing over MCP

Indexing a large repo takes minutes; a synchronous `index_repository` tool call would block the client and risk timeouts. **DECIDED (locked): async indexing with `index_status` polling and explicit `degraded` surfacing.**

Pattern (portable across all clients, no spec-level async needed):

1. `index_repository` validates inputs, enqueues the job in the daemon, and returns **immediately** with `{job_id, state: "queued"}`.
2. Client polls `index_status(project)` → `{state: queued|running|indexed|degraded|failed, nodes_persisted, files_total, files_done, error}`.
3. `degraded` is a first-class state, never folded into `indexed`. Rationale: the reference tool's silent-degradation bugs — status "indexed" with ~500 nodes for a 72k-LOC repo (REPORTED: https://github.com/DeusData/codebase-memory-mcp/issues #333; also #524, #563) — make status honesty a chgraph differentiator.

The 2025-11-25 spec era adds protocol-level long-running/async operation support (REPORTED: https://modelcontextprotocol.io/specification). **OPEN:** whether client adoption is broad enough to use it instead of polling — v1 ships the polling pattern regardless, since it degrades gracefully on every client. Changing this contract later is a tool-surface change → **chgraph-change-control**. This section is the one home of the status-field schema (the state names `queued|running|indexed|degraded|failed` and fields above); **chgraph-architecture-contract** owns the status-honesty invariant (INV-3) the schema implements, and **chgraph-run-and-operate** §5 defers here for the state machine.

## 5. THE COMPATIBILITY CONTRACT: the reference tool's 14 tools, tiered

The reference tool exposes exactly 14 tools, defined in a static `TOOLS[]` array in https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c (purposes/params from that file plus https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md, as of v0.8.1). **The 14 names and the key params below were re-confirmed by fetching and grepping that live mcp.c on 2026-07-03 (VERIFIED-against-source); behavioral claims remain REPORTED — the binary was not run.** **DECIDED (locked): chgraph ships a compatible core** — tier-1 names keep the reference tool's names and semantics verbatim, because agents already know this surface (the reference installer ships a skill teaching `list_projects → get_graph_schema → search_graph → get_code_snippet`).

| # | Tool | Purpose (REPORTED) | Key params / notes (REPORTED) | chgraph tier (DECIDED) |
|---|---|---|---|---|
| 1 | `index_repository` | Index a repo into the graph | repo path; status can be `degraded` via `CBM_DUMP_VERIFY_MIN_RATIO` knob | **Tier-1** — but async-first (Section 4) |
| 2 | `search_graph` | Search nodes; three independent, combinable modes | `query` (BM25 FTS, camelCase-split, structural label boosting), `name_pattern` (regex), `semantic_query` (MUST be an array of keywords, per-keyword min-cosine); filters `label`/`qn_pattern`/`file_pattern`/`min_degree`/`max_degree`/`exclude_entry_points`; `limit` (default 200)/`offset` with `total`+`has_more` in responses; only `project` required | **Tier-1** — same params; scoring backend differs (no native BM25 in ClickHouse; hybrid scoring owned by **chgraph-architecture-contract**) |
| 3 | `query_graph` | Read-only openCypher-subset queries (MATCH/WHERE/WITH/RETURN/UNWIND/aggregates/var-length paths); errors explicitly on unsupported constructs | Cypher string, `project`, `max_rows`; schema states "hard 100k row ceiling", "No offset support — use search_graph for paginated browsing" (its installed skill says 200 rows — doc drift inside the reference) | **Tier-1 name, OPEN semantics** — Cypher-to-SQL translation vs parameterized query templates vs opt-in raw SQL is undecided; resolving it goes through **chgraph-change-control** |
| 4 | `trace_path` | Path tracing over call-type edges from a symbol | `function_name`, `direction`, `depth`, `edge_types`, `mode`, `include_tests`, `risk_labels`, `parameter_name`, `project` | **Tier-1** — backed by recursive CTE + CALLS closure table (**chgraph-architecture-contract**); reference has empty-result bugs (#480) chgraph must beat |
| 5 | `get_code_snippet` | Return source for a node | qualified name / node ref | **Tier-1** |
| 6 | `get_graph_schema` | Describe node labels / edge types | — | **Tier-1** — agents call it first per the reference skill's workflow |
| 7 | `index_status` | Report indexing state | project | **Tier-1** — with explicit `degraded`/`failed` (Section 4) |
| 8 | `list_projects` | List indexed projects | — | **Tier-1** |
| 9 | `delete_project` | Remove a project's graph | project | **Tier-1** — reference had a data-loss bug deleting DBs on corruption detection (#557); chgraph deletion must be explicit-only |
| 10 | `search_code` | Text search over code (distinct from graph search) | query | **Tier-2** — overlaps grep; low differentiation |
| 11 | `get_architecture` | High-level architecture summary (Leiden clustering) | — | **Tier-2** — response shape never extracted (OPEN, per report open question); needs clustering work |
| 12 | `detect_changes` | Git-diff blast radius | diff/range | **Tier-2** — natural fit for the git-evolution tables; design belongs to **chgraph-git-evolution-campaign** |
| 13 | `manage_adr` | Agent-written Architecture Decision Records (the reference's only agent-written knowledge) | ADR CRUD | **Tier-2** — orthogonal to the analytics pitch |
| 14 | `ingest_traces` | Ingest runtime traces to validate HTTP_CALLS edges | trace payload | **Not planned** — HTTP_CALLS/runtime-trace validation is out of v1 scope |

Compatibility caveats, all flagged in the research (REPORTED):
- Exact **response shapes** for `get_architecture` and `detect_changes` were never extracted from the reference — OPEN before claiming drop-in compatibility for tier-2.
- The `query_graph` row cap contradicts itself inside the reference (100k in tool schema vs 200 in its installed skill) — if chgraph claims compatibility, verify empirically against a live reference install first.
- Tier promotions/demotions and any semantic divergence from a reference tool name are tool-surface changes → **chgraph-change-control**, no exceptions.

## 6. Extension namespace for chgraph-unique tools (DECIDED)

Convention for tools that have no reference-tool counterpart:

1. **Family prefix, snake_case:** `<family>_<noun-or-verb>`. First reserved family: **`evolution_*`** for git-history analytics — e.g. `evolution_hotspots` (churn × complexity), `evolution_coupling` (co-change), `evolution_ownership`, `evolution_recency`. Tool designs live in **chgraph-git-evolution-campaign**; this skill owns only the naming rule.
2. **Never** name an extension `chgraph_*` — MCP clients already namespace by server (Claude Code surfaces tools as `mcp__<server>__<tool>`), so the prefix would be redundant noise in the model's context.
3. **Never** reuse a reference tool name with different semantics — that silently breaks agents trained on the reference surface.
4. New families (e.g. a future `metrics_*`) and any new tool name must pass **chgraph-change-control** gates before shipping.

Rationale: a distinct prefix lets agents (and eval harnesses) cleanly separate "compatible core" behavior from chgraph-unique behavior, and lets the description/docs advertise the extensions as one keyword family.

## 7. Client registration snippets

This section is the one home of the chgraph registration snippet — sibling skills (`chgraph-run-and-operate` §4) link here rather than restating the JSON. Reference-tool shape, which chgraph mirrors (REPORTED: https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/README.md — project `.mcp.json` or `~/.claude/.mcp.json` for Claude Code; same `mcpServers` object in `claude_desktop_config.json` for Claude Desktop, per https://modelcontextprotocol.io/docs/develop/connect-local-servers):

```jsonc
// .mcp.json (project root, Claude Code) — DECIDED projection: the chgraph CLI
// does not exist yet; "chgraph mcp" is the planned shim entrypoint (Section 2).
{
  "mcpServers": {
    "chgraph": {
      "command": "/absolute/path/to/.venv/bin/chgraph",
      "args": ["mcp"]
    }
  }
}
```

```jsonc
// claude_desktop_config.json (Claude Desktop) — same shape, same DECIDED caveat.
{
  "mcpServers": {
    "chgraph": {
      "command": "/absolute/path/to/.venv/bin/chgraph",
      "args": ["mcp"]
    }
  }
}
```

Rules that will bite if ignored:
- Use **absolute paths** for `command` — clients spawn servers with unpredictable cwd and PATH.
- The command must be the venv entrypoint (or `/abs/path/.venv/bin/python -m chgraph.shim`) — system python3 is 3.9.6 and cannot import the stack (**chgraph-build-and-env** owns the env story).
- The registered command is the **shim**; it must not open chdb (Section 2).
- The reference tool additionally installs a skill + PreToolUse hook for Claude Code (REPORTED: https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/cli/cli.c). Whether chgraph ships equivalents is **OPEN** (adoption matters — the reference has an open "agents under-use the tools" issue, #509).

## 8. When NOT to use this

| You actually want | Go to |
|---|---|
| chdb SQL behavior, session/lock semantics, index types | **chdb-reference** |
| Daemon/shim lifecycle, socket management, start/stop/restart, ops | **chgraph-run-and-operate** |
| Schema DDL, hybrid ranking design, traversal/closure-table design | **chgraph-architecture-contract** |
| Designing the `evolution_*` tools themselves (churn/coupling/ownership queries) | **chgraph-git-evolution-campaign** |
| Reference tool's internals beyond its tool surface (pipeline, storage, issue archaeology) | **code-graph-reference** |
| Setting up the venv / installing packages | **chgraph-build-and-env** |
| Changing any tool name, schema, or retrieval behavior | **chgraph-change-control** (mandatory gate) |
| A live MCP request is failing right now | **chgraph-debugging-playbook** |

## 9. Provenance and maintenance

**How this was grounded (2026-07-03):** the FastMCP server + client smoke tests in Section 3 were written and executed locally (Python 3.12 venv, `mcp` 1.28.1 installed via uv that day); all pasted output is real. The 14-tool table and all reference-tool claims come from Phase-1 research of DeusData/codebase-memory-mcp v0.8.1 (sources: the repo's `src/mcp/mcp.c`, `src/cli/cli.c`, README, and issue tracker — URLs inline). Spec-era claims about 2025-06-18/2025-11-25 come from modelcontextprotocol.io. Tier assignments implement the locked design decisions of 2026-07-03. No chgraph code existed when this was written.

**Re-verify on drift:**

| What may drift | One-liner |
|---|---|
| mcp SDK version | `python -c "import importlib.metadata; print(importlib.metadata.version('mcp'))"` (was 1.28.1) |
| Negotiated protocol version | Run the Section 3 smoke client; check the `protocol:` line (was 2025-11-25) |
| Structured-output behavior (bare dict vs pydantic) | Run the typed variant in Section 3; confirm `structuredContent` is non-None |
| Reference tool's tool list (currently 14) | `curl -s https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/src/mcp/mcp.c \| awk '/^static const tool_def_t TOOLS/,/^};/' \| grep -oE '^    \{"[a-z_]+"' \| tr -d ' {"'` (ran 2026-07-03: prints exactly the 14 names above) |
| Reference tool release | `curl -s https://api.github.com/repos/DeusData/codebase-memory-mcp/releases/latest \| grep tag_name` (was v0.8.1) |
| chdb version / lock behavior (owned by chdb-reference) | see **chdb-reference** re-verification commands |

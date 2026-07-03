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

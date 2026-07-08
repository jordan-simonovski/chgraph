"""Map an eval condition to Claude Agent SDK options (validation-and-qa §2).

The three conditions differ ONLY in which tools the agent may call; everything
else (model, prompt, cwd) is held constant so the comparison is fair. Returns
plain kwargs so this stays SDK-import-free and unit-testable; agent.py feeds
them to ClaudeAgentOptions(**kwargs).
"""
from __future__ import annotations

FILE_TOOLS = ["Read", "Glob", "Grep"]

# Hard-blocked in every condition so the agent can't escape the file/graph tool surface into a
# shell (grep/find via Bash) or mutate the checkout. disallowed_tools is enforced even under
# bypassPermissions — allowed_tools alone is only a permission allowlist, not a filter (VERIFIED
# 2026-07-08: the agent used Bash despite allowed_tools omitting it). Combined with
# setting_sources=[] (no host hooks/skills/plugins) this keeps A strictly file-only.
NON_FILE_TOOLS = [
    "Bash", "BashOutput", "KillShell", "Write", "Edit", "NotebookEdit", "Task",
    "WebFetch", "WebSearch", "TodoWrite", "ExitPlanMode", "ToolSearch", "SlashCommand", "LS",
]
# NOTE: this denylist is defence-in-depth, not the guarantee. The guarantee is the per-question
# tool-leak check in runner.py (any tool outside the condition's allow-set -> recorded failure),
# so a missing builtin here surfaces loudly instead of silently invalidating a run.

# chgraph MCP query surface (read-only; index tools deliberately excluded —
# the corpus is indexed before a run, the agent must not spend turns indexing).
CHGRAPH_TOOLS = [
    "mcp__chgraph__search_graph",
    "mcp__chgraph__get_code_snippet",
    "mcp__chgraph__trace_path",
    "mcp__chgraph__get_graph_schema",
]

SYSTEM_PROMPT = (
    "You are a software engineer answering a question about the codebase in the "
    "current working directory. Investigate using only the tools available to you, "
    "then give a concise, concrete answer that names the specific files, symbols, "
    "and relationships involved. Do not modify any files."
)

# ponytail: max_turns/budget caps live here so a runaway agent can't burn the run.
MAX_TURNS = 40


def agent_options(condition: str, checkout: str, model: str,
                  chgraph_cmd: list[str] | None = None,
                  reference_cmd: list[str] | None = None,
                  max_budget_usd: float | None = None) -> dict:
    base = dict(
        cwd=checkout, model=model, system_prompt=SYSTEM_PROMPT,
        max_turns=MAX_TURNS, permission_mode="bypassPermissions",
        mcp_servers={}, allowed_tools=list(FILE_TOOLS),
        disallowed_tools=list(NON_FILE_TOOLS),
        # ISOLATION (VERIFIED-necessary 2026-07-08): without these the SDK agent inherits the
        # host's ~/.claude — user SessionStart hooks fire inside the eval agent (observed:
        # "PONYTAIL MODE"/superpowers injected), plugin tools load, and the host .mcp.json is read.
        setting_sources=[],           # load NO filesystem settings: no host hooks/skills/plugins
        strict_mcp_config=True,       # only the mcp_servers below; ignore the repo/user .mcp.json
    )
    if max_budget_usd is not None:
        base["max_budget_usd"] = max_budget_usd  # hard per-question spend ceiling
    if condition == "A":
        return base
    if condition == "C":
        if not chgraph_cmd:
            raise ValueError("condition C requires chgraph_cmd")
        # bind the MCP server to this checkout's graph explicitly (not the spawn cwd)
        args = chgraph_cmd[1:] + ["--repo", checkout]
        base["mcp_servers"] = {"chgraph": {"command": chgraph_cmd[0], "args": args}}
        base["allowed_tools"] = FILE_TOOLS + CHGRAPH_TOOLS
        return base
    if condition == "B":
        if not reference_cmd:
            raise ValueError("condition B requires reference_cmd")
        # ponytail: reference tool's MCP tool names wired in Step 3 when it's stood up.
        base["mcp_servers"] = {"reference": {"command": reference_cmd[0], "args": reference_cmd[1:]}}
        base["allowed_tools"] = FILE_TOOLS + ["mcp__reference"]
        return base
    raise ValueError(f"unknown condition {condition!r} (want A, B, or C)")

"""Condition -> agent options mapping. Pure: no SDK import, returns plain kwargs."""
import pytest

from chgraph.eval.conditions import agent_options


def test_condition_a_is_files_only_no_mcp():
    opts = agent_options("A", checkout="/tmp/click", model="claude-sonnet-5")
    assert opts["mcp_servers"] == {}
    assert set(opts["allowed_tools"]) == {"Read", "Glob", "Grep"}
    assert "Bash" not in opts["allowed_tools"]
    assert opts["cwd"] == "/tmp/click"
    assert opts["model"] == "claude-sonnet-5"
    assert opts["system_prompt"]


def test_max_budget_usd_sets_hard_ceiling_when_given():
    opts = agent_options("A", checkout="/tmp/click", model="m", max_budget_usd=0.50)
    assert opts["max_budget_usd"] == 0.50


def test_max_budget_usd_omitted_when_none():
    opts = agent_options("A", checkout="/tmp/click", model="m")
    assert "max_budget_usd" not in opts


def test_condition_c_wires_chgraph_mcp_and_query_tools():
    opts = agent_options("C", checkout="/tmp/click", model="claude-sonnet-5",
                         chgraph_cmd=["/x/.venv/bin/chgraph", "mcp"])
    assert "chgraph" in opts["mcp_servers"]
    assert opts["mcp_servers"]["chgraph"]["command"] == "/x/.venv/bin/chgraph"
    # --repo binds the MCP server to THIS checkout's graph, not the spawn cwd
    assert opts["mcp_servers"]["chgraph"]["args"] == ["mcp", "--repo", "/tmp/click"]
    tools = opts["allowed_tools"]
    assert "mcp__chgraph__search_graph" in tools
    assert "mcp__chgraph__trace_path" in tools
    assert {"Read", "Glob", "Grep"} <= set(tools)
    # agent must not (re)index during a run; corpus is pre-indexed.
    assert "mcp__chgraph__index_repository" not in tools


def test_condition_c_requires_chgraph_cmd():
    with pytest.raises(ValueError, match="chgraph_cmd"):
        agent_options("C", checkout="/tmp/click", model="m")


def test_unknown_condition_rejected():
    with pytest.raises(ValueError, match="condition"):
        agent_options("Z", checkout="/tmp/click", model="m")

import os
import shutil
import sys
import tempfile

import pytest


@pytest.fixture
def mcp_data_dir():
    # Short, unique, per-process data dir directly under /tmp (not pytest's
    # tmp_path, which nests deep enough that daemon.sock overflows AF_UNIX's
    # ~104-byte sockaddr_un limit on macOS once CHGRAPH_DATA_DIR/<slug>/ is
    # appended) — same scoping as tests/chdb/test_daemon.py's `daemon` fixture
    # and tests/chdb/test_cli.py's `cli_data_dir` fixture.
    data_dir = tempfile.mkdtemp(dir="/tmp", prefix=f"cgmcp{os.getpid()}-")
    yield data_dir
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.mark.anyio
async def test_shim_lists_tier1_tools_and_indexes(mcp_data_dir, synth_repo, monkeypatch):
    monkeypatch.setenv("CHGRAPH_DATA_DIR", mcp_data_dir)
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "chgraph.cli", "mcp", "--repo", str(synth_repo)],
        env={**os.environ, "CHGRAPH_DATA_DIR": mcp_data_dir},
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
            item = res.structuredContent["items"][0]

            # Fix 1: search_graph's reference-only params (semantic_query et al.) must
            # raise a clear "not supported in v0.1" error, never be silently ignored
            # (this is the ONLY enforcement of that global constraint). FastMCP catches
            # the tool's ValueError and returns it as an error CallToolResult
            # (isError=True, content=[TextContent(text=str(exc))]) rather than raising
            # McpError client-side — see mcp/server/lowlevel/server.py's call_tool
            # handler: `except Exception as e: return self._make_error_result(str(e))`.
            bad = await session.call_tool(
                "search_graph", {"query": "x", "semantic_query": ["y"]})
            assert bad.isError, "unsupported param must surface as an MCP tool error"
            bad_text = "".join(
                getattr(c, "text", "") for c in bad.content)
            assert "not supported in v0.1" in bad_text
            assert "semantic_query" in bad_text

            # Fix 2: exercise the remaining tier-1 tools end-to-end against the live
            # daemon, proving their kwarg names and pydantic model mapping actually
            # work (previously only asserted present in list_tools).
            schema = await session.call_tool("get_graph_schema", {})
            assert "Function" in schema.structuredContent["labels"]

            projects = await session.call_tool("list_projects", {})
            assert len(projects.structuredContent["projects"]) >= 1

            snippet = await session.call_tool(
                "get_code_snippet", {"qualified_name": item["qualified_name"]})
            assert snippet.structuredContent["text"]

            traced = await session.call_tool(
                "trace_path", {"function_name": item["qualified_name"]})
            assert isinstance(traced.structuredContent["paths"], list)

            # delete_project wipes the graph -- must run LAST.
            deleted = await session.call_tool("delete_project", {})
            assert deleted.structuredContent["deleted"]

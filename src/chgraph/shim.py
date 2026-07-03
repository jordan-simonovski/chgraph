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
                     label: str | None = None, limit: int = 200, offset: int = 0,
                     semantic_query: list[str] | None = None, qn_pattern: str | None = None,
                     file_pattern: str | None = None, min_degree: int | None = None,
                     max_degree: int | None = None,
                     exclude_entry_points: bool | None = None) -> SearchPage:
        """Search graph nodes. query: text match ranked by relevance+git-recency+centrality;
        name_pattern: RE2 regex on symbol name; label: node label filter (Function, Class, File)."""
        unsupported = {
            "semantic_query": semantic_query, "qn_pattern": qn_pattern,
            "file_pattern": file_pattern, "min_degree": min_degree,
            "max_degree": max_degree, "exclude_entry_points": exclude_entry_points,
        }
        used = [name for name, val in unsupported.items() if val is not None]
        if used:
            raise ValueError(
                f"search_graph param(s) not supported in v0.1: {', '.join(used)}")
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

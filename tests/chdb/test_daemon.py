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

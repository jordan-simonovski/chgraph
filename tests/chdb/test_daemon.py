import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from chgraph.client import DaemonClient, DaemonError
from chgraph.paths import ProjectPaths


@pytest.fixture
def daemon(tmp_path, synth_repo):
    # Short, unique, per-process data dir directly under /tmp (not pytest's
    # tmp_path, which nests deep enough that daemon.sock overflows AF_UNIX's
    # ~104-byte sockaddr_un limit on macOS once CHGRAPH_DATA_DIR/<slug>/ is
    # appended). Scoped to this fixture rather than a global pytest basetemp
    # hack, so it can't collide across concurrent/multi-user test runs.
    data_dir = tempfile.mkdtemp(dir="/tmp", prefix=f"cg{os.getpid()}-")
    env = {**os.environ, "CHGRAPH_DATA_DIR": data_dir}
    proc = subprocess.Popen(
        [sys.executable, "-m", "chgraph.daemon", str(synth_repo)],
        env=env, stderr=subprocess.PIPE,
    )
    # Recompute under the overridden env:
    os.environ["CHGRAPH_DATA_DIR"] = data_dir
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
    shutil.rmtree(data_dir, ignore_errors=True)


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


def test_shutdown_releases_socket_and_pidfile(daemon):
    """INV lifecycle: a clean shutdown must release the socket and pidfile so a
    subsequent daemon for the same project can start without stale-state errors."""
    client, paths = daemon
    assert paths.socket.exists()
    assert paths.pidfile.exists()
    client.call("shutdown")
    deadline = time.time() + 10
    while time.time() < deadline and (paths.socket.exists() or paths.pidfile.exists()):
        time.sleep(0.1)
    assert not paths.socket.exists(), "socket still present after shutdown"
    assert not paths.pidfile.exists(), "pidfile still present after shutdown"


def test_index_failure_reports_failed_state(tmp_path):
    """INV-3 (honest degradation/failure reporting): when the index job itself
    raises, status must land on state=='failed' with a non-null error instead of
    hanging at 'running'/'queued' or crashing the daemon.

    Forced by pointing the daemon at a directory that is NOT a git repository:
    index_repository()'s `git -C repo_root ls-files` (indexer._py_files) then
    raises CalledProcessError, which _index_job's except clause turns into a
    'failed' status write. This exercises the real subprocess boundary rather
    than an in-process stub, since monkeypatching across the daemon subprocess
    isn't possible.
    """
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    data_dir = tempfile.mkdtemp(dir="/tmp", prefix=f"cg{os.getpid()}-fail-")
    env = {**os.environ, "CHGRAPH_DATA_DIR": data_dir}
    proc = subprocess.Popen(
        [sys.executable, "-m", "chgraph.daemon", str(not_a_repo)],
        env=env, stderr=subprocess.PIPE,
    )
    prev_data_dir = os.environ.get("CHGRAPH_DATA_DIR")
    os.environ["CHGRAPH_DATA_DIR"] = data_dir
    try:
        paths = ProjectPaths.for_repo(str(not_a_repo))
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

        job = client.call("index")
        assert job["state"] == "queued" and job["job_id"]
        st = _wait_indexed(client)
        assert st["state"] == "failed"
        assert st["error"]

        try:
            client.call("shutdown")
        except DaemonError:
            pass
        proc.wait(timeout=10)
    finally:
        if prev_data_dir is None:
            os.environ.pop("CHGRAPH_DATA_DIR", None)
        else:
            os.environ["CHGRAPH_DATA_DIR"] = prev_data_dir
        shutil.rmtree(data_dir, ignore_errors=True)

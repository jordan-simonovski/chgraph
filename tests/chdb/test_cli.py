import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from chgraph.paths import ProjectPaths


def run_cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "chgraph.cli", *args],
                          capture_output=True, text=True, env=env, timeout=120)


@pytest.fixture
def cli_data_dir():
    # Short, unique, per-process data dir directly under /tmp (not pytest's
    # tmp_path, which nests deep enough that daemon.sock overflows AF_UNIX's
    # ~104-byte sockaddr_un limit on macOS once CHGRAPH_DATA_DIR/<slug>/ is
    # appended) — same scoping as tests/chdb/test_daemon.py's `daemon` fixture.
    data_dir = tempfile.mkdtemp(dir="/tmp", prefix=f"cgcli{os.getpid()}-")
    yield data_dir
    shutil.rmtree(data_dir, ignore_errors=True)


def test_daemon_lifecycle_via_cli(cli_data_dir, synth_repo):
    env = {**os.environ, "CHGRAPH_DATA_DIR": cli_data_dir}

    r = run_cli("daemon", "status", str(synth_repo), env=env)
    assert r.returncode == 1                                   # stopped

    assert run_cli("daemon", "start", str(synth_repo), env=env).returncode == 0
    for _ in range(100):                                        # wait for ready
        if run_cli("daemon", "status", str(synth_repo), env=env).returncode == 0:
            break
        time.sleep(0.1)
    assert run_cli("daemon", "start", str(synth_repo), env=env).returncode == 0  # idempotent

    r = run_cli("index", str(synth_repo), env=env)
    assert r.returncode == 0 and "idx-" in r.stdout

    assert run_cli("daemon", "stop", str(synth_repo), env=env).returncode == 0
    assert run_cli("daemon", "status", str(synth_repo), env=env).returncode == 1


def test_daemon_status_stale_crashed_exits_2(cli_data_dir, synth_repo, monkeypatch):
    # Fabricate the "stale — crashed" condition daemon_status checks: pid/lock
    # artifacts on disk but no daemon actually listening on the socket.
    monkeypatch.setenv("CHGRAPH_DATA_DIR", cli_data_dir)
    paths = ProjectPaths.for_repo(str(synth_repo))
    paths.ensure()
    paths.chdb_dir.mkdir(parents=True, exist_ok=True)
    (paths.chdb_dir / "status").touch()

    # A pid guaranteed to be dead (and reaped, not just exited-but-zombie).
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    paths.pidfile.write_text(str(dead.pid))

    env = {**os.environ, "CHGRAPH_DATA_DIR": cli_data_dir}
    r = run_cli("daemon", "status", str(synth_repo), env=env)
    assert r.returncode == 2

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from chgraph.paths import ProjectPaths
from chgraph.client import DaemonClient, DaemonError


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


@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.skipif(shutil.which("codegraph") is None, reason="requires optional CodeGraph CLI")
def test_codegraph_cli_index_query_restart_and_failed_rebuild(cli_data_dir, tmp_path, monkeypatch, durable):
    version = subprocess.run(["codegraph", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    if version != "1.6.2":
        pytest.skip("requires pinned CodeGraph CLI")
    repo = tmp_path / "repo"
    fixtures = Path(__file__).parents[1] / "fixtures" / "codegraph"
    shutil.copytree(fixtures, repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=t@t",
                    "commit", "-qm", "fixture"], check=True)
    monkeypatch.setenv("CHGRAPH_DATA_DIR", cli_data_dir)
    env = dict(os.environ)
    if durable:
        env["CHGRAPH_DURABLE_URI"] = f"local:{tmp_path / 'durable'}"
    client = DaemonClient(ProjectPaths.for_repo(str(repo)).socket)

    def indexed():
        for _ in range(150):
            status = client.call("status")
            if status["state"] in ("indexed", "degraded", "failed"):
                return status
            time.sleep(0.1)
        pytest.fail("index never finished")

    try:
        run = run_cli("index", str(repo), "--source", "codegraph", env=env)
        assert run.returncode == 0, run.stderr
        status = indexed()
        assert status["state"] == "degraded", status.get("error") or status
        assert status["source"] == "codegraph" and status["experimental"]
        assert status["rejected_edges"] == 1
        name = "src.fixture_pkg.api.Service.run"
        assert "self.finish()" in client.call("snippet", qualified_name=name)["text"]
        assert "src.fixture_pkg.api.Service.finish" in {
            r["node"] for r in client.call("trace", qualified_name=name)["paths"]}
        shadowed = client.call("trace", qualified_name="src.fixture_pkg.api.shadowed")["paths"]
        assert len(shadowed) == 1
        context = client.call("explore", query="Service", max_chars=1800)
        assert "self.finish()" in context["context"]
        assert len(context["context"]) <= 1800
        impact = client.call("impact", qualified_name="src.fixture_pkg.helpers.target")
        assert "src.fixture_pkg.api.Service.run" in {n["qualified_name"] for n in impact["affected"]}
        reexport = repo / "src" / "fixture_pkg" / "__init__.py"
        reexport_original = reexport.read_bytes()
        reexport.write_text("from .other import duplicate as target\n")
        stale = client.call("explore", query="unchanged")
        assert any("stale graph dependency" in warning for warning in stale["warnings"])
        assert "-> src.fixture_pkg.helpers.target" not in stale["context"]
        reexport.write_bytes(reexport_original)
        path = repo / "src" / "fixture_pkg" / "api.py"
        original = path.read_bytes()
        path.write_bytes(b"# changed\n" + original)
        with pytest.raises(DaemonError, match="stale"):
            client.call("snippet", qualified_name=name)
        path.write_bytes(original)
        before = client.call("search", query="Service")
        assert run_cli("daemon", "stop", str(repo), env=env).returncode == 0
        assert run_cli("daemon", "start", str(repo), env=env).returncode == 0
        assert client.call("search", query="Service") == before
        (repo / ".git").rename(repo / ".git.saved")
        run = run_cli("index", str(repo), "--source", "codegraph", env=env)
        assert run.returncode == 0, run.stderr
        assert indexed()["state"] == "failed"
        assert client.call("search", query="Service") == before
        assert "self.finish()" in client.call("snippet", qualified_name=name)["text"]
        (repo / ".git.saved").rename(repo / ".git")
    finally:
        run_cli("daemon", "stop", str(repo), env=env)


@pytest.mark.parametrize("exit_launcher", [False, True])
def test_stop_cancels_and_reaps_codegraph_extractor(cli_data_dir, tmp_path, exit_launcher):
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "codegraph"
    fake.write_text(f"#!{sys.executable}\n" + '''import os, sys, time, subprocess
from pathlib import Path
if '--version' in sys.argv:
    print('1.6.2')
elif os.environ.get('EXIT_LAUNCHER') == '1':
    subprocess.Popen([sys.executable, '-c', "import os,time; from pathlib import Path; Path(os.environ['EXTRACTOR_PID_FILE']).write_text(str(os.getpid())); time.sleep(60)"])
else:
    Path(os.environ['EXTRACTOR_PID_FILE']).write_text(str(os.getpid()))
    time.sleep(60)
''')
    fake.chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    pid_file = tmp_path / "extractor.pid"
    env = {**os.environ, "CHGRAPH_DATA_DIR": cli_data_dir,
           "PATH": str(binary) + os.pathsep + os.environ["PATH"],
           "EXTRACTOR_PID_FILE": str(pid_file), "EXIT_LAUNCHER": str(int(exit_launcher))}
    pid = None
    try:
        assert run_cli("index", str(repo), "--source", "codegraph", env=env).returncode == 0
        for _ in range(100):
            if pid_file.exists():
                break
            time.sleep(0.05)
        assert pid_file.exists()
        pid = int(pid_file.read_text())
        assert run_cli("daemon", "stop", str(repo), env=env).returncode == 0
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        run_cli("daemon", "stop", str(repo), env=env)
        if pid is not None:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass

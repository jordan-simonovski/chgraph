"""chgraph CLI. Command semantics: chgraph-run-and-operate §2."""
import argparse
import os
import signal
import subprocess
import sys
import time

from chgraph.client import DaemonClient, DaemonError
from chgraph.paths import ProjectPaths
from chgraph.status import read_status


def _repo(arg: str | None) -> str:
    if arg:
        return os.path.realpath(arg)
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else os.getcwd()


def _pid_alive(paths: ProjectPaths) -> int | None:
    try:
        pid = int(paths.pidfile.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def daemon_start(repo: str) -> int:
    paths = ProjectPaths.for_repo(repo)
    paths.ensure()
    try:
        DaemonClient(paths.socket).call("ping")
        print("already running")
        return 0
    except DaemonError:
        pass
    errlog = open(paths.log_dir / "daemon.err", "ab")
    subprocess.Popen([sys.executable, "-m", "chgraph.daemon", repo],
                     start_new_session=True, stdout=errlog, stderr=errlog)
    for _ in range(100):
        try:
            DaemonClient(paths.socket).call("ping")
            print(f"started (socket {paths.socket})")
            return 0
        except DaemonError:
            time.sleep(0.1)
    print("daemon did not come up; see logs/daemon.err", file=sys.stderr)
    return 1


def daemon_status(repo: str) -> int:
    paths = ProjectPaths.for_repo(repo)
    try:
        info = DaemonClient(paths.socket).call("ping")
        st = read_status(paths.status_json)
        print(f"running pid={info['pid']} project={info['project']} index={st['state']}")
        return 0
    except DaemonError:
        if _pid_alive(paths) or (paths.chdb_dir / "status").exists() and paths.pidfile.exists():
            print("stale — crashed (socket dead but pid/lock artifacts present)")
            return 2
        print("stopped")
        return 1


def daemon_stop(repo: str) -> int:
    paths = ProjectPaths.for_repo(repo)
    try:
        DaemonClient(paths.socket).call("shutdown")
    except DaemonError:
        pid = _pid_alive(paths)
        if pid is None:
            print("not running")
            return 0
        os.kill(pid, signal.SIGTERM)                    # escalation per run-and-operate §2
    for _ in range(100):
        if _pid_alive(paths) is None:
            print("stopped")
            return 0
        time.sleep(0.1)
    os.kill(_pid_alive(paths), signal.SIGKILL)          # safe for data (run-and-operate §3)
    print("killed")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="chgraph")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pd = sub.add_parser("daemon")
    pd.add_argument("action", choices=["start", "stop", "status", "restart"])
    pd.add_argument("repo", nargs="?")
    pi = sub.add_parser("index")
    pi.add_argument("repo", nargs="?")
    ps = sub.add_parser("status")
    ps.add_argument("repo", nargs="?")
    pm = sub.add_parser("mcp")
    pm.add_argument("--repo")
    args = ap.parse_args()

    if args.cmd == "daemon":
        repo = _repo(args.repo)
        if args.action == "restart":
            daemon_stop(repo)
            raise SystemExit(daemon_start(repo))
        raise SystemExit({"start": daemon_start, "stop": daemon_stop,
                          "status": daemon_status}[args.action](repo))
    if args.cmd == "index":
        repo = _repo(args.repo)
        if daemon_start(repo) != 0:
            raise SystemExit(1)
        job = DaemonClient(ProjectPaths.for_repo(repo).socket).call("index")
        print(f"queued {job['job_id']}; poll with `chgraph status`")
        raise SystemExit(0)
    if args.cmd == "status":
        print(read_status(ProjectPaths.for_repo(_repo(args.repo)).status_json))
        raise SystemExit(0)
    if args.cmd == "mcp":
        from chgraph import shim                        # deferred: Task 12
        shim.main(repo_root=_repo(args.repo))


if __name__ == "__main__":
    main()

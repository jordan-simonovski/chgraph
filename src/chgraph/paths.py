"""Data-directory layout. Contract: chgraph-run-and-operate §1 (DECIDED 2026-07-03)."""
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path


def project_slug(repo_root: str) -> str:
    real = os.path.realpath(repo_root)
    base = re.sub(r"[^A-Za-z0-9_-]", "-", os.path.basename(real)) or "repo"
    return f"{base}-{hashlib.sha256(real.encode()).hexdigest()[:8]}"


def _data_root() -> Path:
    if os.environ.get("CHGRAPH_DATA_DIR"):
        return Path(os.environ["CHGRAPH_DATA_DIR"])
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(xdg) / "chgraph"


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    chdb_dir: Path
    socket: Path
    pidfile: Path
    status_json: Path
    log_dir: Path

    @classmethod
    def for_repo(cls, repo_root: str) -> "ProjectPaths":
        root = _data_root() / project_slug(repo_root)
        return cls(
            root=root,
            chdb_dir=root / "chdb",
            socket=root / "daemon.sock",
            pidfile=root / "daemon.pid",
            status_json=root / "status.json",
            log_dir=root / "logs",
        )

    def ensure(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

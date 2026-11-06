import hashlib
import os

from chgraph.paths import ProjectPaths, project_slug


def test_slug_is_basename_plus_hash(tmp_path):
    repo = tmp_path / "my-repo"
    repo.mkdir()
    real = os.path.realpath(str(repo))
    want = "my-repo-" + hashlib.sha256(real.encode()).hexdigest()[:8]
    assert project_slug(str(repo)) == want


def test_slug_sanitizes_weird_names(tmp_path):
    repo = tmp_path / "a b@c"
    repo.mkdir()
    assert project_slug(str(repo)).startswith("a-b-c-")


def test_paths_layout_respects_chgraph_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHGRAPH_DATA_DIR", str(tmp_path / "root"))
    repo = tmp_path / "repo"
    repo.mkdir()
    p = ProjectPaths.for_repo(str(repo))
    slug = project_slug(str(repo))
    assert p.root == tmp_path / "root" / slug
    assert p.chdb_dir == p.root / "chdb"
    assert p.socket == p.root / "daemon.sock"
    assert p.pidfile == p.root / "daemon.pid"
    assert p.status_json == p.root / "status.json"
    assert p.log_dir == p.root / "logs"
    p.ensure()
    assert p.log_dir.is_dir()
def test_source_reader_rejects_fifo_and_oversized_files(tmp_path):
    import os
    import pytest
    from chgraph.paths import read_source

    os.mkfifo(tmp_path / "pipe.py")
    with pytest.raises(ValueError, match="regular"):
        read_source(tmp_path, "pipe.py")
    (tmp_path / "large.py").write_bytes(b"x" * 100)
    with pytest.raises(ValueError, match="limit"):
        read_source(tmp_path, "large.py", max_bytes=10)

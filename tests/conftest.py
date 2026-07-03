import subprocess
from pathlib import Path

import pytest

from chgraph.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store(tmp_path):
    # One Session per process; fresh tmp dir per test; always close (INV-1).
    s = Store.open(tmp_path / "data")
    yield s
    s.close()


@pytest.fixture(scope="session")
def synth_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("synth") / "repo"
    out = subprocess.run(
        ["bash", str(FIXTURES / "make_synth_repo.sh"), str(repo)],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "TOTAL_COMMITS=14" in out
    return repo

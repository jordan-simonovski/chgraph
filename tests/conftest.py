import pytest

from chgraph.store import Store


@pytest.fixture
def store(tmp_path):
    # One Session per process; fresh tmp dir per test; always close (INV-1).
    s = Store.open(tmp_path / "data")
    yield s
    s.close()

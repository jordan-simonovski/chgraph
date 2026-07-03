import pytest

from chgraph.traverse import trace_path


@pytest.fixture
def cyclic(store):
    # Deliberate cycle a->b->c->a plus branch c->d (the INV-2 verification shape).
    store.exec("""
        INSERT INTO chgraph.edges VALUES
        ('p','a','b','CALLS','{}',1), ('p','b','c','CALLS','{}',1),
        ('p','c','a','CALLS','{}',1), ('p','c','d','CALLS','{}',1)
    """)
    return store


def test_terminates_on_cycle_and_finds_all(cyclic):
    rows = trace_path(cyclic, "p", "a", direction="callees", depth=10)
    reached = {r["node"] for r in rows}
    assert reached == {"a", "b", "c", "d"}
    d = next(r for r in rows if r["node"] == "d")
    assert d["path"] == ["a", "b", "c", "d"] and d["depth"] == 3


def test_callers_direction(cyclic):
    rows = trace_path(cyclic, "p", "d", direction="callers", depth=10)
    assert {r["node"] for r in rows} == {"d", "c", "b", "a"}


def test_depth_clamped(cyclic):
    rows = trace_path(cyclic, "p", "a", depth=99)   # silently clamped to 10, must not error
    assert rows
    with pytest.raises(ValueError):
        trace_path(cyclic, "p", "a", direction="sideways")

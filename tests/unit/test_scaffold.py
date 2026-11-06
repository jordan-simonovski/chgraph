import chgraph


def test_version():
    assert chgraph.__version__ == "0.1.0"


def test_chdb_pin():
    import chdb
    assert chdb.__version__ == "4.4.0"

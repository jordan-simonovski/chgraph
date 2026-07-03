def test_nodes_roundtrip_and_replace(store):
    store.exec("""
        INSERT INTO chgraph.nodes VALUES
        ('demo','Function','parse','pkg.mod.parse','pkg/mod.py',10,42,'{}',1),
        ('demo','Function','emit','pkg.mod.emit','pkg/mod.py',44,60,'{}',1)
    """)
    store.exec("""
        INSERT INTO chgraph.nodes VALUES
        ('demo','Function','parse','pkg.mod.parse','pkg/mod.py',12,50,'{}',2)
    """)
    rows = store.rows(
        "SELECT qualified_name, start_line, version FROM chgraph.nodes FINAL "
        "WHERE qualified_name = 'pkg.mod.parse'"
    )
    assert rows == [{"qualified_name": "pkg.mod.parse", "start_line": 12, "version": 2}]


def test_all_tables_exist(store):
    names = {r["name"] for r in store.rows("SELECT name FROM system.tables WHERE database='chgraph'")}
    assert {"nodes", "edges", "git_commits", "git_file_changes",
            "file_evolution", "embeddings", "meta"} <= names


def test_edges_dedup_on_final(store):
    store.exec("INSERT INTO chgraph.edges VALUES ('p','a','b','CALLS','{}',1),('p','a','b','CALLS','{}',2)")
    assert store.rows("SELECT count() AS n FROM chgraph.edges FINAL")[0]["n"] == 1

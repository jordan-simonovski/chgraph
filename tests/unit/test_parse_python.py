from chgraph.parse_python import parse_file

SRC = b'''\
from os import path
import json

def top(x):
    return helper(x)

def helper(x):
    return path.join("a", x)

class Greeter:
    def greet(self):
        return top(1)

def uses_import():
    return json.dumps({})
'''


def _nodes_by_qn(nodes):
    return {n["qualified_name"]: n for n in nodes}


def test_nodes_extracted():
    nodes, _ = parse_file("src/demo.py", SRC)
    qns = _nodes_by_qn(nodes)
    assert qns["src/demo.py"]["label"] == "File"
    assert qns["src.demo.top"]["label"] == "Function"
    assert qns["src.demo.Greeter"]["label"] == "Class"
    assert qns["src.demo.Greeter.greet"]["label"] == "Function"
    assert qns["src.demo.top"]["start_line"] == 4  # 1-based


def test_defines_and_imports_edges():
    _, edges = parse_file("src/demo.py", SRC)
    defines = {(e["source"], e["target"]) for e in edges if e["type"] == "DEFINES"}
    assert ("src/demo.py", "src.demo.top") in defines
    assert ("src/demo.py", "src.demo.Greeter") in defines
    imports = {e["target"] for e in edges if e["type"] == "IMPORTS"}
    assert {"os.path", "json"} <= imports


def test_calls_resolved_precision_first():
    _, edges = parse_file("src/demo.py", SRC)
    calls = {(e["source"], e["target"]) for e in edges if e["type"] == "CALLS"}
    assert ("src.demo.top", "src.demo.helper") in calls          # same-module def
    assert ("src.demo.Greeter.greet", "src.demo.top") in calls   # method -> module def
    # precision-first: attribute calls on imported modules are NOT guessed into CALLS
    assert not any(t == "json.dumps" for _, t in calls)


def test_init_py_module_name():
    nodes, _ = parse_file("pkg/__init__.py", b"def f():\n    pass\n")
    assert "pkg.f" in _nodes_by_qn(nodes)

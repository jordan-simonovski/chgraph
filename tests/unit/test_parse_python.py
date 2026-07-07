import json

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


def test_no_false_calls_edge_when_param_shadows_module_def():
    src = b'''\
def filter():
    pass

def process(filter):
    return filter(1)
'''
    _, edges = parse_file("src/shadow.py", src)
    calls = {(e["source"], e["target"]) for e in edges if e["type"] == "CALLS"}
    # `filter` inside process() is the parameter, not the module-level def --
    # precision-first: no CALLS edge should be emitted for the shadowed name.
    assert not any(t == "src.shadow.filter" for _, t in calls)


def test_parse_file_never_raises_on_malformed_input():
    for bad_src in (
        b"def f(:\n    return json.",
        b"foo(bar(baz(",
        b"\x00\xff\x01" * 50,
    ):
        nodes, edges = parse_file("src/bad.py", bad_src)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)
        assert any(n["label"] == "File" for n in nodes)


# --- deprecation detection (Phase-6 promotion): whole-symbol deprecation only ---

DEPREC_SRC = b'''\
import warnings
from django.utils.deprecation import RemovedInDjango70Warning


def old_fn():
    """Legacy helper."""
    warnings.warn("old_fn is deprecated", category=RemovedInDjango70Warning)
    return 1


def emits_warn_for_arg(safe=None):
    """Live function; only a PARAMETER is deprecated (guarded warn)."""
    if safe is None:
        safe = False
    else:
        warnings.warn("The safe parameter is deprecated", DeprecationWarning)
    return safe


@deprecated("use NewThing")
def decorated_old():
    return 2


def docstring_old():
    """Do a thing.

    .. deprecated:: 4.2
        Use thing2 instead.
    """
    return 3


class DeprecatedClass(Base):
    def __init__(self, x):
        warnings.warn("DeprecatedClass is deprecated", category=RemovedInDjango70Warning)
        super().__init__(x)


class LiveClass(Base):
    """A live class that merely deprecates a method arg."""
    def __init__(self, x):
        self.x = x

    def method(self, legacy=None):
        if legacy is not None:
            warnings.warn("legacy arg is deprecated", DeprecationWarning)
        return self.x
'''


def _dep(nodes, qn):
    return json.loads(_nodes_by_qn(nodes)[qn]["properties"]).get("deprecated", False)


def test_deprecation_detected_only_for_whole_symbol():
    nodes, _ = parse_file("m.py", DEPREC_SRC)
    # deprecated: unconditional warn, @deprecated decorator, .. deprecated:: docstring,
    # class whose __init__ unconditionally warns
    assert _dep(nodes, "m.old_fn") is True
    assert _dep(nodes, "m.decorated_old") is True
    assert _dep(nodes, "m.docstring_old") is True
    assert _dep(nodes, "m.DeprecatedClass") is True
    # NOT deprecated: guarded/param-level warn (JsonResponse/QuerySet false-positive class)
    assert _dep(nodes, "m.emits_warn_for_arg") is False
    assert _dep(nodes, "m.LiveClass") is False
    assert _dep(nodes, "m.LiveClass.method") is False


def test_plain_symbols_not_deprecated():
    nodes, _ = parse_file("src/demo.py", SRC)
    assert _dep(nodes, "src.demo.top") is False
    assert _dep(nodes, "src.demo.Greeter") is False

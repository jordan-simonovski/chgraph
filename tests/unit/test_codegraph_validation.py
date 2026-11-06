import ast

import pytest

from chgraph.parse_python import _overload_lines, _shadowed_parameter_calls


@pytest.mark.parametrize("source, expected", [
    (b"def generic[T](callback):\n    callback()\n", {(2, 4)}),
    (b"def generic[T]():\n    def nested(callback):\n        callback()\n    return nested\n", {(3, 8)}),
    (b"class Generic[T]:\n    def method(self, callback):\n        callback()\n", {(3, 8)}),
])
def test_generic_definitions_validate_the_function_binding_scope(source, expected):
    assert _shadowed_parameter_calls(source) == expected


def test_parameter_calls_are_rejected_without_losing_real_calls():
    source = b'''from other import target
def direct(target, /, *, keyword, **kwargs):
    target()
    keyword()
    kwargs()
    def nested():
        return target()
    return nested()
def valid():
    return target()
class Service:
    def run(self):
        return self.finish()
'''
    rejected = _shadowed_parameter_calls(source)
    calls = {(n.lineno, n.col_offset): ast.unparse(n.func)
             for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)}
    assert {calls[site] for site in rejected} == {"target", "keyword", "kwargs"}
    assert len(rejected) == 4
    assert (10, 11) not in rejected  # module import stays callable


def test_nested_parameter_scope_does_not_leak_to_outer_calls():
    source = b'''def outer():
    def inner(target):
        return target()
    return target()
'''
    assert _shadowed_parameter_calls(source) == {(3, 15)}


def test_comprehension_binding_and_lambda_parameter_have_distinct_scopes():
    source = b'''def f(target):
    return [target() for target in factories], (lambda target: target())
'''
    assert _shadowed_parameter_calls(source) == {(2, 63)}


def test_method_captures_outer_parameter_across_class_attribute_scope():
    source = b'''def outer(target):
    class C:
        target = 1
        def run(self):
            return target()
'''
    assert _shadowed_parameter_calls(source) == {(5, 19)}


def test_class_comprehension_captures_outer_parameter():
    source = b'''def outer(target):
    class C:
        target = 1
        values = [target() for _ in range(1)]
'''
    assert _shadowed_parameter_calls(source) == {(4, 18)}


def test_private_parameter_names_are_mangled_for_symbol_lookup():
    source = b'''class C:
    def run(self, __target):
        return __target()
'''
    assert _shadowed_parameter_calls(source) == {(3, 15)}


def test_postponed_annotations_are_not_runtime_parameter_calls():
    source = b'''from __future__ import annotations
def f(target=default()):
    value: list[str()] = []
    return target()
'''
    assert _shadowed_parameter_calls(source) == {(4, 11)}


def test_class_comprehension_can_call_implicit_global_builtins():
    assert _shadowed_parameter_calls(b"class C:\n    values = {str(x) for x in range(3)}\n") == set()


def test_only_typing_overload_declarations_are_identified():
    source = b'''from typing import overload as declared
@declared
def f(x: int): ...
def f(x): return x
'''
    assert _overload_lines(source) == {3}
    assert _overload_lines(b"@overload\ndef f(): ...\n") == set()


def test_shadowed_decorator_does_not_hide_a_live_function():
    source = b'''from typing import overload
def build(overload):
    @overload
    def live(): return 1
    return live
class C:
    overload = lambda fn: fn
    @overload
    def method(self): return 1
'''
    assert _overload_lines(source) == set()

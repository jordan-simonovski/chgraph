"""Python symbol/edge extraction via tree-sitter (Decision 9: precision over breadth).
CALLS edges are emitted ONLY when the callee resolves to a same-module def or an
explicit `from X import name` binding — unresolvable calls are dropped, not guessed
(the reference tool's false-CALLS bugs are the cautionary tale, code-graph-reference)."""
import json
import re

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

_PY = Language(tspython.language())
_parser = Parser(_PY)

# A symbol is DEPRECATED (whole-symbol, not "mentions deprecation") iff it carries an
# `@deprecated`-named decorator, or unconditionally issues a deprecation warning in its own
# body — a direct body statement, not nested in if/try/…, not a comment. This separates a
# deprecated symbol from a live one that merely deprecates a parameter (django's
# JsonResponse/QuerySet, sqlalchemy's relationship/and_/or_). Detected at parse time; the
# coarse body-text regex and the ambiguous `.. deprecated::` docstring signal are retired.
_DEP_TOKEN = re.compile(r"(?:Pending)?DeprecationWarning\b|RemovedIn\w*Warning\b")


def _stmt_issues_dep_warning(stmt, text) -> bool:
    """True if `stmt` (a DIRECT body child) unconditionally issues a deprecation
    warning: a warnings.warn(...)/warn(...) with a deprecation category, or a raise of
    a *Deprecation/RemovedIn*Warning. Guarded (nested) warns and comments aren't direct
    children, so they don't count."""
    if stmt.type == "expression_statement" and stmt.named_children:
        call = stmt.named_children[0]
        if call.type == "call":
            fn = call.child_by_field_name("function")
            if (text(fn) if fn is not None else "") in ("warnings.warn", "warn"):
                return bool(_DEP_TOKEN.search(text(call)))
    elif stmt.type == "raise_statement":
        return bool(_DEP_TOKEN.search(text(stmt)))
    return False


def _body_unconditionally_warns(body, text) -> bool:
    return body is not None and any(
        _stmt_issues_dep_warning(c, text) for c in body.named_children)


def _decorator_name(dec, text) -> str:
    """Final dotted component of a decorator's callable — 'deprecated' for
    @deprecated, @deprecated(...), @warnings.deprecated, @x.deprecated(...). Match on the
    NAME, never the arguments: @ignore_warnings(message='X is deprecated') is not @deprecated."""
    expr = dec.named_children[0] if dec.named_children else None
    if expr is not None and expr.type == "call":
        expr = expr.child_by_field_name("function")
    return text(expr).rsplit(".", 1)[-1] if expr is not None else ""


def _has_deprecated_decorator(node, text) -> bool:
    parent = node.parent
    if parent is not None and parent.type == "decorated_definition":
        return any(c.type == "decorator" and _decorator_name(c, text) == "deprecated"
                   for c in parent.named_children)
    return False


def _is_deprecated_def(node, text) -> bool:
    """Whole-symbol deprecation for a function_definition/class_definition node.

    Two precise signals only: an `@deprecated`-named decorator, or an unconditional
    deprecation warn in the symbol's own body (or __init__). A `.. deprecated::` docstring
    directive is deliberately NOT a signal — docstrings document deprecated *parameters* and
    calling conventions inline (sqlalchemy `relationship`/`and_`/`or_` carry such directives
    while the symbol itself is live), so it false-positives. Recall cost: a symbol deprecated
    ONLY via docstring (no decorator, no warn) is missed — acceptable, precision-first, since a
    false demotion of live code is worse than a missed stale one."""
    body = node.child_by_field_name("body")
    if _has_deprecated_decorator(node, text):
        return True
    if node.type == "function_definition":
        return _body_unconditionally_warns(body, text)
    for child in (body.named_children if body is not None else ()):   # class: check __init__
        fn = child
        if child.type == "decorated_definition":
            fn = next((g for g in child.named_children
                       if g.type == "function_definition"), None)
        if (fn is not None and fn.type == "function_definition"
                and text(fn.child_by_field_name("name")) == "__init__"
                and _body_unconditionally_warns(fn.child_by_field_name("body"), text)):
            return True
    return False


def _module_name(rel_path: str) -> str:
    p = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def parse_file(rel_path: str, source: bytes) -> tuple[list[dict], list[dict]]:
    tree = _parser.parse(source)
    module = _module_name(rel_path)
    nodes: list[dict] = [{
        "label": "File", "name": rel_path.rsplit("/", 1)[-1],
        "qualified_name": rel_path, "file_path": rel_path,
        "start_line": 1, "end_line": tree.root_node.end_point[0] + 1, "properties": "{}",
    }]
    edges: list[dict] = []
    module_defs: dict[str, str] = {}     # local name -> qualified_name (module-level defs)
    imported: dict[str, str] = {}        # local name -> dotted module path

    def text(n) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="replace")

    def add_symbol(node, kind: str, scope: str) -> str:
        name_node = node.child_by_field_name("name")
        name = text(name_node)
        qn = f"{scope}.{name}"
        props = '{"deprecated": true}' if _is_deprecated_def(node, text) else "{}"
        nodes.append({
            "label": kind, "name": name, "qualified_name": qn, "file_path": rel_path,
            "start_line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1,
            "properties": props,
        })
        edges.append({"source": rel_path, "target": qn, "type": "DEFINES", "properties": "{}"})
        return qn

    def collect_imports(node) -> None:
        if node.type == "import_statement":            # import json, os.path
            for child in node.named_children:
                dotted = text(child.child_by_field_name("name") or child)
                alias = text(child.child_by_field_name("alias")) if child.type == "aliased_import" else dotted.split(".")[0]
                imported[alias] = dotted
                edges.append({"source": rel_path, "target": dotted, "type": "IMPORTS", "properties": "{}"})
        elif node.type == "import_from_statement":     # from os import path [as p]
            mod = text(node.child_by_field_name("module_name"))
            join = "" if mod.endswith(".") else "."   # `from . import x` -> ".x", not "..x"
            for child in node.named_children[1:]:
                if child.type in ("dotted_name", "aliased_import"):
                    name_node = child.child_by_field_name("name") or child
                    name = text(name_node)
                    alias = text(child.child_by_field_name("alias")) if child.type == "aliased_import" else name
                    dotted = f"{mod}{join}{name}"
                    imported[alias] = dotted
                    edges.append({"source": rel_path, "target": dotted, "type": "IMPORTS", "properties": "{}"})

    # Pass 1: symbols + imports (so calls can resolve forward references).
    def walk_defs(node, scope: str) -> None:
        for child in node.named_children:
            if child.type == "function_definition":
                qn = add_symbol(child, "Function", scope)
                if scope == module:
                    module_defs[text(child.child_by_field_name("name"))] = qn
                walk_defs(child.child_by_field_name("body"), qn)
            elif child.type == "class_definition":
                qn = add_symbol(child, "Class", scope)
                if scope == module:
                    module_defs[text(child.child_by_field_name("name"))] = qn
                walk_defs(child.child_by_field_name("body"), qn)
            elif child.type in ("import_statement", "import_from_statement"):
                collect_imports(child)
            else:
                walk_defs(child, scope)

    walk_defs(tree.root_node, module)

    # Pass 2: CALLS. Track the enclosing function scope, and the set of names
    # locally bound within it (params, `=` targets, nested def/class names),
    # while walking -- a locally-bound callee shadows any module-level def of
    # the same name and must never resolve to a CALLS edge (see module docstring).
    def param_names(fn_node) -> set[str]:
        names: set[str] = set()
        params = fn_node.child_by_field_name("parameters")
        if params is None:
            return names
        for child in params.named_children:
            if child.type == "identifier":
                names.add(text(child))
                continue
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                names.add(text(name_node))
                continue
            for gc in child.named_children:      # *args / **kwargs / typed w/o "name" field
                if gc.type == "identifier":
                    names.add(text(gc))
                    break
        return names

    def assign_targets(target, names: set[str]) -> None:
        if target.type == "identifier":
            names.add(text(target))
        elif target.type in ("pattern_list", "tuple_pattern", "list_pattern"):
            for t in target.named_children:
                assign_targets(t, names)

    def body_local_names(body) -> set[str]:
        """Names bound directly in `body`'s own scope: `=` targets and nested
        def/class names. Does NOT descend into nested function/class bodies --
        those are separate scopes with their own bindings."""
        names: set[str] = set()

        def scan(n) -> None:
            for child in n.named_children:
                if child.type == "function_definition":
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        names.add(text(name_node))
                elif child.type == "class_definition":
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        names.add(text(name_node))
                elif child.type == "assignment":
                    left = child.child_by_field_name("left")
                    if left is not None:
                        assign_targets(left, names)
                else:
                    scan(child)

        scan(body)
        return names

    def walk_calls(node, scope: str, local_names: frozenset[str] = frozenset()) -> None:
        for child in node.named_children:
            if child.type == "function_definition":
                body = child.child_by_field_name("body")
                fn_locals = local_names | param_names(child) | body_local_names(body)
                walk_calls(body, f"{scope}.{text(child.child_by_field_name('name'))}", fn_locals)
            elif child.type == "class_definition":
                walk_calls(child.child_by_field_name("body"),
                           f"{scope}.{text(child.child_by_field_name('name'))}", local_names)
            elif child.type == "call":
                fn = child.child_by_field_name("function")
                if fn.type == "identifier":
                    callee = text(fn)
                    if callee not in local_names:
                        target = module_defs.get(callee) or imported.get(callee)
                        if target:
                            edges.append({"source": scope, "target": target,
                                          "type": "CALLS", "properties": "{}"})
                walk_calls(child, scope, local_names)
            else:
                walk_calls(child, scope, local_names)

    walk_calls(tree.root_node, module)
    return nodes, edges

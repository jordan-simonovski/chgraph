"""Python symbol/edge extraction via tree-sitter (Decision 9: precision over breadth).
CALLS edges are emitted ONLY when the callee resolves to a same-module def or an
explicit `from X import name` binding — unresolvable calls are dropped, not guessed
(the reference tool's false-CALLS bugs are the cautionary tale, code-graph-reference)."""
import ast
import json
import symtable
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


def _docstring(body, text) -> str:
    """First paragraph of a def's docstring (single line, capped) — the NL description
    used to build embed-text (indexer). Empty when there is no docstring."""
    if body is None or not body.named_children:
        return ""
    first = body.named_children[0]
    if first.type != "expression_statement" or not first.named_children:
        return ""
    s = first.named_children[0]
    if s.type != "string":
        return ""
    content = "".join(text(c) for c in s.named_children if c.type == "string_content").strip()
    return content.split("\n\n")[0].replace("\n", " ").strip()[:200]


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
        "doc": "",   # transient (indexer pops it to build embed-text); never persisted to nodes
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
            "doc": _docstring(node.child_by_field_name("body"), text),
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


def _shadowed_parameter_calls(source: bytes) -> set[tuple[int, int]]:
    """Locate bare calls bound to parameters, including captured closure parameters."""
    scopes = [symtable.symtable(source, "<indexed source>", "exec")]
    bindings = [(scopes[0], set())]
    used = set()
    rejected = set()
    tree = ast.parse(source)
    postponed = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                    and any(a.name == "annotations" for a in n.names) for n in tree.body)

    class Calls(ast.NodeVisitor):
        def scoped(self, node, name, body):
            children = scopes[-1].get_children()
            while True:
                table = next((t for t in children if t.get_name() == name
                              and t.get_lineno() == node.lineno and t.get_id() not in used), None)
                if table is None:
                    raise ValueError(f"unmatched Python binding scope: {name} at line {node.lineno}")
                used.add(table.get_id())
                if table.get_type() in ("function", "class"):
                    break
                children = table.get_children()  # Generic definitions have a type-parameter wrapper.
            scopes.append(table)
            bindings.append((table, set()))
            for child in body:
                self.visit(child)
            scopes.pop()
            bindings.pop()

        def visit_FunctionDef(self, node):
            annotations = [] if postponed else [node.args, *([node.returns] if node.returns else [])]
            defaults = [*node.args.defaults, *(n for n in node.args.kw_defaults if n is not None)] if postponed else []
            for child in [*annotations, *defaults, *node.decorator_list]:
                self.visit(child)
            self.scoped(node, node.name, node.body)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_AnnAssign(self, node):
            self.visit(node.target)
            if node.value is not None:
                self.visit(node.value)
            if not postponed:
                self.visit(node.annotation)

        def visit_ClassDef(self, node):
            for child in [*node.bases, *node.keywords, *node.decorator_list]:
                self.visit(child)
            self.scoped(node, node.name, node.body)

        def visit_Lambda(self, node):
            self.visit(node.args)
            self.scoped(node, "lambda", [node.body])

        def comprehension(self, node):
            self.visit(node.generators[0].iter)
            name = {ast.ListComp: "listcomp", ast.SetComp: "setcomp",
                    ast.DictComp: "dictcomp", ast.GeneratorExp: "genexpr"}[type(node)]
            table = next((t for t in scopes[-1].get_children()
                          if t.get_name() == name and t.get_lineno() == node.lineno), None)
            if table:
                scopes.append(table)
                bindings.append((table, set()))
            masked = set()
            bindings.append((None, masked))
            for i, generator in enumerate(node.generators):
                if i:
                    self.visit(generator.iter)
                masked.update(n.id for n in ast.walk(generator.target) if isinstance(n, ast.Name))
                for condition in generator.ifs:
                    self.visit(condition)
            for child in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                self.visit(child)
            bindings.pop()
            if table:
                bindings.pop()
                scopes.pop()

        visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = comprehension

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                owner = next((s.get_name().lstrip("_") for s in reversed(scopes)
                              if s.get_type() == "class"), None)
                if owner and name.startswith("__") and not name.endswith("__"):
                    name = f"_{owner}{name}"
                function_scope = False
                for scope, masked in reversed(bindings):
                    if node.func.id in masked:
                        break
                    if scope is None:
                        function_scope = True  # Inlined comprehensions also skip class attributes.
                        continue
                    if function_scope and scope.get_type() == "class":
                        continue
                    function_scope |= scope.get_type() == "function"
                    try:
                        symbol = scope.lookup(name)
                    except KeyError:  # Implicit globals need no symbol in the module table.
                        continue
                    if symbol.is_parameter():
                        rejected.add((node.lineno, node.col_offset))
                    if not symbol.is_free():
                        break
            self.generic_visit(node)

    Calls().visit(tree)
    return rejected


def _overload_lines(source: bytes) -> set[int]:
    tree = ast.parse(source)
    names = {alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom)
             and node.module in ("typing", "typing_extensions") for alias in node.names if alias.name == "overload"}
    modules = {alias.asname or alias.name for node in tree.body if isinstance(node, ast.Import)
               for alias in node.names if alias.name in ("typing", "typing_extensions")}
    allowed_imports = {id(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                       and ((isinstance(n, ast.ImportFrom) and n.module in ("typing", "typing_extensions"))
                            or (isinstance(n, ast.Import) and all(a.name in ("typing", "typing_extensions") for a in n.names)))}
    result = set()

    def bindings(body):
        pending, bound = list(body), set()
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in allowed_imports:
                bound.update(a.asname or a.name.split('.')[0] for a in node.names)
            pending.extend(ast.iter_child_nodes(node))
        return bound

    def walk(body, shadowed):
        shadowed = shadowed | bindings(body)
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any((isinstance(d, ast.Name) and d.id in names - shadowed)
                       or (isinstance(d, ast.Attribute) and d.attr == "overload"
                           and isinstance(d.value, ast.Name) and d.value.id in modules - shadowed)
                       for d in node.decorator_list):
                    result.add(node.lineno)
                # Local decorators require richer binding analysis; keep those definitions visible.
            elif isinstance(node, ast.ClassDef):
                walk(node.body, shadowed)
            else:
                walk(list(ast.iter_child_nodes(node)), shadowed)

    walk(tree.body, set())
    return result

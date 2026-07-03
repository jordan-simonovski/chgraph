"""Python symbol/edge extraction via tree-sitter (Decision 9: precision over breadth).
CALLS edges are emitted ONLY when the callee resolves to a same-module def or an
explicit `from X import name` binding — unresolvable calls are dropped, not guessed
(the reference tool's false-CALLS bugs are the cautionary tale, code-graph-reference)."""
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

_PY = Language(tspython.language())
_parser = Parser(_PY)


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
        "start_line": 1, "end_line": source.count(b"\n") + 1, "properties": "{}",
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
        nodes.append({
            "label": kind, "name": name, "qualified_name": qn, "file_path": rel_path,
            "start_line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1,
            "properties": "{}",
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
            for child in node.named_children[1:]:
                if child.type in ("dotted_name", "aliased_import"):
                    name_node = child.child_by_field_name("name") or child
                    name = text(name_node)
                    alias = text(child.child_by_field_name("alias")) if child.type == "aliased_import" else name
                    imported[alias] = f"{mod}.{name}"
                    edges.append({"source": rel_path, "target": f"{mod}.{name}", "type": "IMPORTS", "properties": "{}"})

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

    # Pass 2: CALLS. Track the enclosing function scope while walking.
    def walk_calls(node, scope: str) -> None:
        for child in node.named_children:
            if child.type in ("function_definition", "class_definition"):
                walk_calls(child.child_by_field_name("body"),
                           f"{scope}.{text(child.child_by_field_name('name'))}")
            elif child.type == "call":
                fn = child.child_by_field_name("function")
                if fn.type == "identifier":
                    callee = text(fn)
                    target = module_defs.get(callee) or imported.get(callee)
                    if target:
                        edges.append({"source": scope, "target": target,
                                      "type": "CALLS", "properties": "{}"})
                walk_calls(child, scope)
            else:
                walk_calls(child, scope)

    walk_calls(tree.root_node, module)
    return nodes, edges

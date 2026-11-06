import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_convergence_detects_an_isolated_stale_node():
    path = Path(__file__).resolve().parents[2] / "evals" / "codegraph_compare.py"
    spec = importlib.util.spec_from_file_location("codegraph_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    common = {"nodes": [{"id": "a", "updated_at": 1}], "files": [], "unresolved_refs": []}
    reindexed = {**common, "nodes": [{"id": "a", "updated_at": 2}]}
    assert module._semantic_state(common, set()) == module._semantic_state(reindexed, set())
    stale = {**common, "nodes": [*common["nodes"], {"id": "ghost", "updated_at": 1}]}
    assert module._semantic_state(stale, set()) != module._semantic_state(common, set())


@pytest.mark.skipif(shutil.which("codegraph") is None, reason="requires optional CodeGraph CLI")
def test_real_codegraph_fixture_import_and_quality_gate(tmp_path):
    version = subprocess.run(["codegraph", "--version"], check=True, text=True,
                             capture_output=True, timeout=10).stdout.strip()
    if version != "1.6.2":
        pytest.skip("snapshot experiment is pinned to CodeGraph 1.6.2")
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "comparison.json"
    run = subprocess.run([sys.executable, str(root / "evals" / "codegraph_compare.py"),
                          "--output", str(output)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    report = json.loads(output.read_text())
    for phase in ("codegraph", "after_sync", "after_rebuild"):
        assert report[phase]["lossless_import"]
        assert report[phase]["traversal_matches"]
        assert report[phase]["counts"]["nodes"] > 0
    assert report["codegraph"]["raw_quality"]["false_calls"] == [[
        "src/fixture_pkg/api.py::shadowed", "src/fixture_pkg/helpers.py::target"]]
    assert report["codegraph"]["quality"]["missing_calls"] == []
    assert report["codegraph"]["quality"]["false_calls"] == []
    assert report["codegraph"]["rejected_edges"] == 1
    assert report["codegraph"]["overload_lookup"] == ["src.fixture_pkg.helpers.convert"]
    assert report["native"]["false_calls"] == []
    assert report["native"]["missing_calls"]
    assert report["sync_matches_rebuild"] is True
    assert report["fixture_gate_passed"] is True

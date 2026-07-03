"""Run-artifact aggregation. Pure build; thin JSON write."""
import json

from chgraph.eval.report import build_report, write_report, noise_band
from chgraph.eval.agent import AnswerResult
from chgraph.eval.judge import Verdict


def _pair(gid, passed, tokens, err=False):
    return (
        AnswerResult(golden_id=gid, condition="A", corpus_sha="sha1", answer="a",
                     tokens_total=tokens, num_turns=2, is_error=err,
                     tokens_raw={"input_tokens": tokens, "cache_read_input_tokens": 7}),
        Verdict(passed=passed, score=1.0 if passed else 0.0, covered=[passed]),
    )


def test_build_report_persists_raw_usage_for_cost_audit():
    # §2: token accounting must be reproducible — keep the input/output/cache split
    rep = build_report(
        run_id="r", condition="A", pairs=[_pair("g1", True, 1000)],
        corpus={"click": "sha1"}, scaffold_model="m",
        judge_model="j", rubric_version="1", golden_set_version=1,
    )
    assert rep["questions"][0]["tokens_raw"]["cache_read_input_tokens"] == 7


def test_build_report_aggregates_quality_and_tokens_with_provenance():
    rep = build_report(
        run_id="run-2026-07-03-a", condition="A",
        pairs=[_pair("g1", True, 1000), _pair("g2", False, 3000)],
        corpus={"click": "sha1"}, scaffold_model="claude-sonnet-5",
        judge_model="claude-opus-4-8", rubric_version="1", golden_set_version=1,
    )
    assert rep["summary"]["n"] == 2
    assert rep["summary"]["passed"] == 1
    assert rep["summary"]["quality"] == 0.5
    assert rep["summary"]["tokens_total"] == 4000
    assert rep["summary"]["tokens_mean"] == 2000
    # provenance — a number without these four is an anecdote (§2)
    assert rep["run_id"] == "run-2026-07-03-a"
    assert rep["corpus"] == {"click": "sha1"}
    assert rep["judge_model"] == "claude-opus-4-8"
    assert rep["rubric_version"] == "1"
    assert rep["golden_set_version"] == 1
    # the failing golden is surfaced, not buried
    assert [f["golden_id"] for f in rep["failures"]] == ["g2"]


def test_build_report_counts_agent_error_as_failure():
    rep = build_report(
        run_id="r", condition="A", pairs=[_pair("g1", False, 500, err=True)],
        corpus={"click": "sha1"}, scaffold_model="m",
        judge_model="j", rubric_version="1", golden_set_version=1,
    )
    assert rep["failures"][0]["reason"] == "agent_error"


def test_noise_band_computes_mean_and_spread_across_runs():
    reports = [
        {"summary": {"quality": 1.0, "tokens_mean": 100}},
        {"summary": {"quality": 0.5, "tokens_mean": 200}},
        {"summary": {"quality": 0.5, "tokens_mean": 300}},
    ]
    band = noise_band(reports)
    assert band["n_runs"] == 3
    assert abs(band["quality"]["mean"] - 2 / 3) < 1e-9
    assert band["quality"]["min"] == 0.5
    assert band["quality"]["max"] == 1.0
    assert band["tokens_per_q"]["mean"] == 200
    assert band["quality"]["stdev"] > 0  # runs disagree -> non-zero band


def test_noise_band_single_run_has_zero_stdev():
    band = noise_band([{"summary": {"quality": 0.9, "tokens_mean": 150}}])
    assert band["n_runs"] == 1
    assert band["quality"]["stdev"] == 0.0


def test_write_report_persists_json(tmp_path):
    rep = build_report(
        run_id="run-x", condition="A", pairs=[_pair("g1", True, 100)],
        corpus={"click": "sha1"}, scaffold_model="m",
        judge_model="j", rubric_version="1", golden_set_version=1,
    )
    path = write_report(rep, tmp_path)
    assert path.exists()
    assert json.loads(path.read_text())["run_id"] == "run-x"

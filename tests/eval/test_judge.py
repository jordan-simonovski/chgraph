"""LLM-as-judge. Prompt-build and verdict-parse are pure; the API call is injected."""
import pytest

from chgraph.eval.judge import build_prompt, parse_verdict, judge_answer, Verdict
from chgraph.eval.goldens import Golden


def _golden():
    return Golden(id="g1", question="Where is BaseCommand defined?", repo="click",
                  category="symbol_lookup",
                  key_points=["defined in click/core.py", "subclass of Command"],
                  golden_set_version=1)


def test_build_prompt_includes_every_key_point_and_the_answer():
    p = build_prompt(_golden(), "It's in click/core.py.")
    assert "defined in click/core.py" in p
    assert "subclass of Command" in p
    assert "It's in click/core.py." in p


def test_parse_verdict_handles_fenced_json():
    raw = '```json\n{"covered": [true, false], "pass": false, "score": 0.5, "notes": "half"}\n```'
    v = parse_verdict(raw)
    assert v.covered == [True, False]
    assert v.passed is False
    assert v.score == 0.5
    assert v.notes == "half"


def test_parse_verdict_rejects_non_json():
    with pytest.raises(ValueError):
        parse_verdict("the answer is basically fine")


def test_judge_answer_validates_covered_length_against_key_points():
    # judge returned one flag but the golden has two key points -> caught, not silently trusted
    def bad_caller(prompt, model):
        return '{"covered": [true], "pass": true, "score": 1.0, "notes": ""}'
    with pytest.raises(ValueError, match="key point"):
        judge_answer(_golden(), "answer", model="claude-opus-4-8", caller=bad_caller)


def test_judge_answer_returns_verdict():
    def caller(prompt, model):
        assert model == "claude-opus-4-8"
        return '{"covered": [true, true], "pass": true, "score": 1.0, "notes": "good"}'
    v = judge_answer(_golden(), "answer", model="claude-opus-4-8", caller=caller)
    assert isinstance(v, Verdict)
    assert v.passed and v.score == 1.0

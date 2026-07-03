"""Agent run wrapper. The SDK call is injected, so these tests make no live calls."""
from chgraph.eval.agent import total_tokens, run_question, AnswerResult
from chgraph.eval.goldens import Golden


def test_total_tokens_sums_io_and_cache():
    usage = {
        "input_tokens": 100, "output_tokens": 50,
        "cache_creation_input_tokens": 20, "cache_read_input_tokens": 10,
    }
    assert total_tokens(usage) == 180


def test_total_tokens_tolerates_missing_cache_fields():
    assert total_tokens({"input_tokens": 5, "output_tokens": 3}) == 8


def _golden():
    return Golden(id="g1", question="Where is X?", repo="click",
                  category="symbol_lookup", key_points=["in core.py"],
                  golden_set_version=1)


def test_run_question_maps_result_to_answerresult():
    captured = {}

    class FakeResult:
        result = "X is defined in click/core.py"
        usage = {"input_tokens": 200, "output_tokens": 40}
        num_turns = 4
        is_error = False

    def fake_runner(prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        return FakeResult()

    res = run_question(_golden(), condition="A", checkout="/tmp/click",
                       model="claude-sonnet-5", corpus_sha="deadbeef",
                       runner=fake_runner)

    assert isinstance(res, AnswerResult)
    assert res.answer == "X is defined in click/core.py"
    assert res.tokens_total == 240
    assert res.num_turns == 4
    assert res.condition == "A"
    assert res.corpus_sha == "deadbeef"
    assert res.golden_id == "g1"
    assert not res.is_error
    # the question text reaches the agent, and condition A uses file tools only
    assert "Where is X?" in captured["prompt"]
    assert captured["options"]["mcp_servers"] == {}

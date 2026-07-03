"""Orchestration loop: run each golden, judge it, pair results. Fakes injected."""
from chgraph.eval.runner import run_eval
from chgraph.eval.goldens import Golden, CorpusRepo


def _golden(gid):
    return Golden(id=gid, question="q", repo="click", category="symbol_lookup",
                  key_points=["kp"], golden_set_version=1)


CORPUS = {"click": CorpusRepo(name="click", repo="pallets/click", sha="sha1",
                              language="python", role="dev")}


def test_run_eval_pairs_answers_with_verdicts():
    def agent_runner(prompt, options):
        class R:
            result = "answer"; usage = {"input_tokens": 10, "output_tokens": 5}
            num_turns = 1; is_error = False
        return R()

    def judge_caller(prompt, model):
        return '{"covered": [true], "pass": true, "score": 1.0, "notes": ""}'

    pairs = run_eval([_golden("g1"), _golden("g2")], condition="A",
                     checkout_for=lambda name: f"/tmp/{name}", corpus=CORPUS,
                     model="m", judge_model="j",
                     agent_runner=agent_runner, judge_caller=judge_caller)
    assert len(pairs) == 2
    assert all(v.passed for _, v in pairs)
    assert pairs[0][0].corpus_sha == "sha1"


def test_run_eval_skips_judge_on_agent_error():
    judged = []

    def agent_runner(prompt, options):
        class R:
            result = None; usage = None; num_turns = 0; is_error = True
        return R()

    def judge_caller(prompt, model):
        judged.append(1)
        return '{"covered": [true], "pass": true, "score": 1.0, "notes": ""}'

    pairs = run_eval([_golden("g1")], condition="A",
                     checkout_for=lambda name: f"/tmp/{name}", corpus=CORPUS,
                     model="m", judge_model="j",
                     agent_runner=agent_runner, judge_caller=judge_caller)
    assert judged == []                       # judge never called on a broken run
    assert pairs[0][1].passed is False
    assert pairs[0][0].is_error is True

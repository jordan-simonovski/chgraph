"""Orchestrate one condition over the golden set: answer, then judge, then pair.

A broken agent run (is_error) is a failure recorded as such — the judge is not
asked to grade a non-answer.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from chgraph.eval.agent import AnswerResult, run_question
from chgraph.eval.conditions import CHGRAPH_TOOLS, FILE_TOOLS
from chgraph.eval.goldens import CorpusRepo, Golden
from chgraph.eval.judge import Verdict, judge_answer


def _allowed_tools(condition: str) -> set[str] | None:
    """Tools each condition is permitted to use. None = don't enforce (condition B is a stub).
    Used to turn a denylist gap (a non-file tool leaking into A) into a LOUD recorded failure
    rather than silently invalidating the run — the isolation guarantee, verified per question."""
    if condition == "A":
        return set(FILE_TOOLS)
    if condition == "C":
        return set(FILE_TOOLS) | set(CHGRAPH_TOOLS)
    return None


def run_eval(goldens: list[Golden], condition: str,
             checkout_for: Callable[[str], str], corpus: dict[str, CorpusRepo],
             model: str, judge_model: str,
             chgraph_cmd: list[str] | None = None,
             reference_cmd: list[str] | None = None,
             max_budget_usd: float | None = None,
             agent_runner=None, judge_caller=None) -> list[tuple[AnswerResult, Verdict]]:
    pairs: list[tuple[AnswerResult, Verdict]] = []
    for g in goldens:
        repo = corpus[g.repo]
        try:
            res = run_question(g, condition=condition, checkout=checkout_for(g.repo),
                               model=model, corpus_sha=repo.sha, chgraph_cmd=chgraph_cmd,
                               reference_cmd=reference_cmd, max_budget_usd=max_budget_usd,
                               runner=agent_runner)
        except Exception as e:                          # noqa: BLE001 — one bad question must not
            # abort the whole run. The SDK RAISES on some agent errors (budget ceiling, CLI
            # failure) instead of returning is_error=True, so map that to a recorded failure.
            res = AnswerResult(golden_id=g.id, condition=condition, corpus_sha=repo.sha,
                               answer=f"agent error: {e}", tokens_total=0, num_turns=0,
                               is_error=True)
        allowed = _allowed_tools(condition)
        leaked = sorted(t for t in res.tool_calls if allowed is not None and t not in allowed)
        if leaked and not res.is_error:
            # isolation breach: the agent escaped its tool surface — invalidate this answer loudly
            res = replace(res, is_error=True,
                          answer=f"tool leak (condition {condition}): {leaked}")
        if res.is_error:
            verdict = Verdict(passed=False, score=0.0,
                              covered=[False] * len(g.key_points), notes="agent error")
        else:
            verdict = judge_answer(g, res.answer, model=judge_model, caller=judge_caller)
        pairs.append((res, verdict))
    return pairs

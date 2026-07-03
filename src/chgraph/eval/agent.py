"""Run one golden question through a headless Claude Agent SDK session.

The SDK call is isolated in `_sdk_runner` and injected via the `runner` param so
the orchestration (options, token accounting, result mapping) is testable without
live API calls or the `claude` CLI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from chgraph.eval.conditions import agent_options
from chgraph.eval.goldens import Golden

_TOKEN_KEYS = (
    "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens",
)


def total_tokens(usage: dict | None) -> int:
    """Every token through the loop. Tool-result tokens count: they are next-turn
    input, which is exactly where graph retrieval saves or spends (§2)."""
    if not usage:
        return 0
    return sum(int(usage.get(k, 0) or 0) for k in _TOKEN_KEYS)


@dataclass
class AnswerResult:
    golden_id: str
    condition: str
    corpus_sha: str
    answer: str
    tokens_total: int
    num_turns: int
    is_error: bool = False
    tokens_raw: dict = field(default_factory=dict)


class _ResultLike(Protocol):
    result: str | None
    usage: dict | None
    num_turns: int
    is_error: bool


Runner = Callable[[str, dict], _ResultLike]


def run_question(golden: Golden, condition: str, checkout: str, model: str,
                 corpus_sha: str, chgraph_cmd: list[str] | None = None,
                 reference_cmd: list[str] | None = None,
                 max_budget_usd: float | None = None,
                 runner: Runner | None = None) -> AnswerResult:
    opts = agent_options(condition, checkout=checkout, model=model,
                         chgraph_cmd=chgraph_cmd, reference_cmd=reference_cmd,
                         max_budget_usd=max_budget_usd)
    run = runner or _sdk_runner
    r = run(golden.question, opts)
    return AnswerResult(
        golden_id=golden.id, condition=condition, corpus_sha=corpus_sha,
        answer=r.result or "", tokens_total=total_tokens(r.usage),
        num_turns=r.num_turns, is_error=r.is_error, tokens_raw=r.usage or {},
    )


def _sdk_runner(prompt: str, options: dict) -> Any:
    """Default runner: drive the Claude Agent SDK and return its ResultMessage.
    Imported lazily so importing this module needs neither the SDK nor the CLI."""
    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, query, ResultMessage

    async def _go():
        result = None
        async for msg in query(prompt=prompt, options=ClaudeAgentOptions(**options)):
            if isinstance(msg, ResultMessage):
                result = msg
        if result is None:
            raise RuntimeError("agent produced no ResultMessage")
        return result

    return anyio.run(_go)

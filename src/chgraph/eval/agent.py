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
    tool_calls: dict = field(default_factory=dict)   # tool name -> call count (which surface the agent used)


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
        tool_calls=dict(getattr(r, "tool_calls", {}) or {}),
    )


class _RunnerResult:
    """Carries the ResultMessage fields plus the per-tool call counts, so the harness can
    VERIFY which tool surface the agent used (condition A must be file-only; condition C must
    actually reach for mcp__chgraph__*)."""
    def __init__(self, rm: Any, tool_calls: dict):
        self.result = rm.result
        self.usage = rm.usage
        self.num_turns = rm.num_turns
        self.is_error = rm.is_error
        self.tool_calls = tool_calls


def _sdk_runner(prompt: str, options: dict) -> Any:
    """Default runner: drive the Claude Agent SDK, tallying tool_use blocks along the way.
    Imported lazily so importing this module needs neither the SDK nor the CLI."""
    import anyio
    from collections import Counter
    from claude_agent_sdk import ClaudeAgentOptions, query, ResultMessage

    async def _go():
        result = None
        tools: Counter = Counter()
        async for msg in query(prompt=prompt, options=ClaudeAgentOptions(**options)):
            for blk in getattr(msg, "content", None) or []:
                if getattr(blk, "type", None) == "tool_use" or hasattr(blk, "name"):
                    name = getattr(blk, "name", None)
                    if name:
                        tools[name] += 1
            if isinstance(msg, ResultMessage):
                result = msg
        if result is None:
            raise RuntimeError("agent produced no ResultMessage")
        return _RunnerResult(result, dict(tools))

    return anyio.run(_go)

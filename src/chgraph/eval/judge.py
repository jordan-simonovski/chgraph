"""LLM-as-judge: grade a candidate answer against a golden's required key points.

Judge model ID + this rubric's wording are recorded in every run report (§2) —
a score is not comparable across judge versions. The API call is isolated in
`_anthropic_caller` and injected for testing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from chgraph.eval.goldens import Golden

RUBRIC_VERSION = "1"  # bump on any wording change below; it invalidates cross-run comparison

_INSTRUCTIONS = """You are grading whether an answer to a codebase question covers the \
required key points. Judge ONLY against the key points; ignore style and extra detail. \
For each key point decide if the answer covers it (true/false). \
Respond with ONLY a JSON object, no prose:
{"covered": [<bool per key point, in order>], "pass": <bool>, "score": <0..1>, "notes": "<short>"}
"pass" is true only if every key point is covered. "score" is the fraction covered."""


@dataclass
class Verdict:
    passed: bool
    score: float
    covered: list[bool]
    notes: str = ""


Caller = Callable[[str, str], str]  # (prompt, model) -> raw text


def build_prompt(golden: Golden, answer: str) -> str:
    points = "\n".join(f"{i+1}. {p}" for i, p in enumerate(golden.key_points))
    return (
        f"{_INSTRUCTIONS}\n\n"
        f"QUESTION:\n{golden.question}\n\n"
        f"REQUIRED KEY POINTS:\n{points}\n\n"
        f"CANDIDATE ANSWER:\n{answer}\n"
    )


def parse_verdict(raw: str) -> Verdict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate ```json fences / stray prose
    if not m:
        raise ValueError(f"judge returned no JSON object: {raw[:200]!r}")
    d = json.loads(m.group(0))
    return Verdict(passed=bool(d["pass"]), score=float(d["score"]),
                   covered=[bool(x) for x in d["covered"]], notes=d.get("notes", ""))


def judge_answer(golden: Golden, answer: str, model: str,
                 caller: Caller | None = None) -> Verdict:
    call = caller or _anthropic_caller
    v = parse_verdict(call(build_prompt(golden, answer), model))
    if len(v.covered) != len(golden.key_points):
        raise ValueError(
            f"judge returned {len(v.covered)} flags for {len(golden.key_points)} key points")
    return v


def _anthropic_caller(prompt: str, model: str) -> str:
    """Default caller: one Anthropic Messages call. Imported lazily; needs ANTHROPIC_API_KEY."""
    import anthropic

    resp = anthropic.Anthropic().messages.create(
        model=model, max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")

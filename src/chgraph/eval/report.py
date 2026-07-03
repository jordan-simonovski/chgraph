"""Build + persist the eval run artifact (validation-and-qa §2).

Every run emits a report carrying its own provenance (run id, corpus SHAs,
golden-set version, judge model + rubric version). Checked into evals/runs/ —
numbers that live only in scrollback do not exist.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from chgraph.eval.agent import AnswerResult
from chgraph.eval.judge import Verdict


def build_report(run_id: str, condition: str,
                 pairs: list[tuple[AnswerResult, Verdict]],
                 corpus: dict[str, str], scaffold_model: str,
                 judge_model: str, rubric_version: str,
                 golden_set_version: int) -> dict:
    n = len(pairs)
    passed = sum(1 for _, v in pairs if v.passed)
    tokens_total = sum(r.tokens_total for r, _ in pairs)
    questions, failures = [], []
    for r, v in pairs:
        questions.append({
            "golden_id": r.golden_id, "passed": v.passed, "score": v.score,
            "tokens": r.tokens_total, "tokens_raw": r.tokens_raw,
            "num_turns": r.num_turns, "is_error": r.is_error, "notes": v.notes,
        })
        if r.is_error or not v.passed:
            failures.append({
                "golden_id": r.golden_id,
                "reason": "agent_error" if r.is_error else "judge_fail",
            })
    return {
        "run_id": run_id,
        "condition": condition,
        "scaffold_model": scaffold_model,
        "judge_model": judge_model,
        "rubric_version": rubric_version,
        "golden_set_version": golden_set_version,
        "corpus": corpus,
        "summary": {
            "n": n,
            "passed": passed,
            "quality": (passed / n) if n else 0.0,
            "tokens_total": tokens_total,
            "tokens_mean": (tokens_total / n) if n else 0,
        },
        "questions": questions,
        "failures": failures,
    }


def noise_band(reports: list[dict]) -> dict:
    """Run-to-run variance across N≥1 runs of the same condition (§2 noise band).
    Sets the non-regression band before any threshold is enforced."""
    qual = [r["summary"]["quality"] for r in reports]
    toks = [r["summary"]["tokens_mean"] for r in reports]

    def stats(xs):
        return {"mean": statistics.fmean(xs), "min": min(xs), "max": max(xs),
                "stdev": statistics.stdev(xs) if len(xs) > 1 else 0.0}

    return {"n_runs": len(reports), "quality": stats(qual), "tokens_per_q": stats(toks)}


def write_report(report: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{report['run_id']}.json"
    path.write_text(json.dumps(report, indent=2))
    (out / f"{report['run_id']}.md").write_text(render_markdown(report))
    return path


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        f"# Eval run `{report['run_id']}` — condition {report['condition']}",
        "",
        f"- scaffold model: `{report['scaffold_model']}`",
        f"- judge model: `{report['judge_model']}` (rubric v{report['rubric_version']})",
        f"- golden-set version: {report['golden_set_version']}",
        f"- corpus: " + ", ".join(f"`{k}`@`{v[:12]}`" for k, v in report["corpus"].items()),
        "",
        f"**Quality {s['quality']:.0%}** ({s['passed']}/{s['n']}) · "
        f"**{s['tokens_total']:,} tokens** ({s['tokens_mean']:,.0f}/question)",
        "",
    ]
    if report["failures"]:
        lines.append("## Failures")
        lines += [f"- `{f['golden_id']}` — {f['reason']}" for f in report["failures"]]
    return "\n".join(lines) + "\n"

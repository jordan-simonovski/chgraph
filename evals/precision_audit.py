"""Deprecation-detector precision audit over the corpus (second-corpus validation for
ADR-0002's default flip). Parses every .py in each indexed corpus repo with the shipped
parser and lists every symbol flagged `deprecated`. False positives here = live symbols
that would be wrongly demoted, the risk that flipping CHGRAPH_RANK_DEPRECATION_WEIGHT on
by default must retire. Writes evals/runs/precision-<date>.json.

    python evals/precision_audit.py            # audits every repo under evals/.cache
"""
import json
import sys
from pathlib import Path

from chgraph.parse_python import parse_file

REPO_ROOT = Path(__file__).resolve().parents[1]


def audit(checkout: Path) -> dict:
    symbols = 0
    flagged: list[str] = []
    for f in checkout.rglob("*.py"):
        try:
            nodes, _ = parse_file(str(f.relative_to(checkout)), f.read_bytes())
        except Exception:                       # noqa: BLE001 — skip unparseable, keep auditing
            continue
        for n in nodes:
            if n["label"] == "File":
                continue
            symbols += 1
            if json.loads(n["properties"]).get("deprecated"):
                flagged.append(n["qualified_name"])
    return {"symbols": symbols, "flagged": sorted(flagged),
            "flagged_count": len(flagged),
            "flagged_pct": round(100 * len(flagged) / max(symbols, 1), 4)}


def main(argv=None) -> int:
    cache = REPO_ROOT / "evals" / ".cache"
    repos = argv or [p.name for p in sorted(cache.iterdir()) if (p / ".git").exists()]
    report = {"repos": {r: audit(cache / r) for r in repos}}
    for r, a in report["repos"].items():
        print(f"{r:10s} symbols={a['symbols']:6d}  flagged={a['flagged_count']:3d} "
              f"({a['flagged_pct']}%)", file=sys.stderr)
    import datetime
    out = REPO_ROOT / "evals" / "runs" / f"precision-{datetime.date.today().isoformat()}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"-> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

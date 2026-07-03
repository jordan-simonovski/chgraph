"""Load + validate the eval corpus and golden question set (validation-and-qa §4).

Goldens are ground truth; a malformed one silently weakens the bar, so loading is
strict: empty key_points and duplicate IDs are hard errors, not warnings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATEGORIES = {
    "symbol_lookup", "caller_callee", "impact",
    "architecture", "data_flow", "evolution",
}


@dataclass(frozen=True)
class CorpusRepo:
    name: str
    repo: str
    sha: str
    language: str
    role: str = ""


@dataclass(frozen=True)
class Golden:
    id: str
    question: str
    repo: str
    category: str
    key_points: list[str]
    golden_set_version: int
    provenance: dict = field(default_factory=dict)


def load_corpus(path: str | Path) -> dict[str, CorpusRepo]:
    data = yaml.safe_load(Path(path).read_text())
    repos = {}
    for r in data["repos"]:
        repos[r["name"]] = CorpusRepo(
            name=r["name"], repo=r["repo"], sha=r["sha"],
            language=r["language"], role=r.get("role", ""),
        )
    return repos


def load_goldens(dir: str | Path) -> list[Golden]:
    goldens: list[Golden] = []
    seen: set[str] = set()
    for f in sorted(Path(dir).glob("*.yaml")):
        for raw in yaml.safe_load(f.read_text()) or []:
            g = _parse_golden(raw, f)
            if g.id in seen:
                raise ValueError(f"duplicate golden id {g.id!r} (in {f.name})")
            seen.add(g.id)
            goldens.append(g)
    return goldens


def _parse_golden(raw: dict, src: Path) -> Golden:
    key_points = raw.get("key_points") or []
    if not key_points:
        raise ValueError(f"golden {raw.get('id')!r} in {src.name} has empty key_points")
    category = raw["category"]
    if category not in CATEGORIES:
        raise ValueError(f"golden {raw['id']!r} has unknown category {category!r}")
    return Golden(
        id=raw["id"], question=raw["question"], repo=raw["repo"],
        category=category, key_points=list(key_points),
        golden_set_version=raw["golden_set_version"],
        provenance=raw.get("provenance", {}),
    )

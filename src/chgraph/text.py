"""Identifier tokenization — one home, shared by the lexical signal (search) and the
embed-text builder (indexer). The VERIFIED RE2-safe two-pass splitter (code-graph-reference):
acronym->word boundary, then lower->Upper boundary, then split on non-alphanumerics, lowercased.
"""
import re

_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")


def subtokens(s: str) -> list[str]:
    s = _CAMEL.sub(r"\1 \2", _ACRONYM.sub(r"\1 \2", s))
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]

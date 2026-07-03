"""Golden + corpus loading/validation. Pure Python, no chdb, no API."""
import pytest

from chgraph.eval.goldens import load_corpus, load_goldens, Golden


def test_load_corpus_pins_sha(tmp_path):
    (tmp_path / "corpus.yaml").write_text(
        "repos:\n"
        "  - name: click\n"
        "    repo: pallets/click\n"
        "    sha: abc123\n"
        "    language: python\n"
        "    role: dev\n"
    )
    corpus = load_corpus(tmp_path / "corpus.yaml")
    assert corpus["click"].sha == "abc123"
    assert corpus["click"].repo == "pallets/click"


def test_load_goldens_reads_all_categories(tmp_path):
    (tmp_path / "symbol_lookup.yaml").write_text(
        "- id: sym-001\n"
        "  question: Where is BaseCommand defined?\n"
        "  repo: click\n"
        "  category: symbol_lookup\n"
        "  key_points:\n"
        "    - defined in click/core.py\n"
        "  golden_set_version: 1\n"
    )
    goldens = load_goldens(tmp_path)
    assert len(goldens) == 1
    g = goldens[0]
    assert isinstance(g, Golden)
    assert g.id == "sym-001"
    assert g.category == "symbol_lookup"
    assert g.key_points == ["defined in click/core.py"]


def test_load_goldens_rejects_empty_key_points(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "- id: bad-001\n"
        "  question: q\n"
        "  repo: click\n"
        "  category: symbol_lookup\n"
        "  key_points: []\n"
        "  golden_set_version: 1\n"
    )
    with pytest.raises(ValueError, match="key_points"):
        load_goldens(tmp_path)


def test_load_goldens_rejects_duplicate_ids(tmp_path):
    body = (
        "- id: dup-001\n"
        "  question: q\n"
        "  repo: click\n"
        "  category: symbol_lookup\n"
        "  key_points: [x]\n"
        "  golden_set_version: 1\n"
    )
    (tmp_path / "a.yaml").write_text(body)
    (tmp_path / "b.yaml").write_text(body)
    with pytest.raises(ValueError, match="duplicate"):
        load_goldens(tmp_path)

import json
from pathlib import Path

from shelfsight.brand_match import brand_id_for_name, dictionary_hits
from shelfsight.config import load_workspace


def fixture_text():
    data = json.loads(Path("tests/fixtures/gemini_grounded.json").read_text(encoding="utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def ids(text):
    return [h["brand_id"] for h in dictionary_hits(text, load_workspace("dotandkey").brands)]


def test_hits_are_ordered_by_first_appearance():
    assert ids(fixture_text()) == ["minimalist", "reequil", "dotandkey", "neutrogena"]


def test_alias_variants_all_match():
    for s in ["Re'equil", "reequil", "re equil", "RE’EQUIL", "Dot and Key", "dot n key"]:
        assert len(ids(f"try {s} today")) == 1, s


def test_fuzzy_typo_on_long_alias():
    assert ids("try nutrogena ultra sheer") == ["neutrogena"]


def test_short_alias_needs_exact_match():
    assert ids("lakmi sun expert") == []


def test_suffix_does_not_fuzzy_match():
    assert ids("a deconstructed routine") == []


def test_brand_id_for_name():
    brands = load_workspace("dotandkey").brands
    assert brand_id_for_name("Dot & Key Watermelon Cooling Sunscreen", brands) == "dotandkey"
    assert brand_id_for_name("Re'equil Oil Control", brands) == "reequil"
    assert brand_id_for_name("Foxtale", brands) is None

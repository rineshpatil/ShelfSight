from rapidfuzz import fuzz

from shelfsight.config import Brand
from shelfsight.text import normalize

FUZZY_MIN_SCORE = 93  # "nutrogena" → neutrogena scores 94.7 and passes; "deconstructed" → deconstruct scores 91.7 and doesn't
FUZZY_MIN_LEN = 6     # shorter aliases fuzzy-match too much, so they must match exactly


def _aliases(brand: Brand) -> set[str]:
    return {normalize(a) for a in [brand.name, *brand.aliases]} - {""}


def _first_index(alias: str, words: list[str]) -> int | None:
    """Index of the first word window equal to (or, for long aliases, very close to) the alias."""
    n = alias.count(" ") + 1
    fuzzy = len(alias) >= FUZZY_MIN_LEN
    for i in range(len(words) - n + 1):
        window = " ".join(words[i:i + n])
        if window == alias or (fuzzy and fuzz.ratio(alias, window) >= FUZZY_MIN_SCORE):
            return i
    return None


def dictionary_hits(text: str, brands: list[Brand]) -> list[dict]:
    """Tracked brands found in the text, ordered by first appearance: [{"brand_id", "alias", "word_index"}]."""
    words = normalize(text).split()
    hits = []
    for b in brands:
        found = [(i, a) for a in _aliases(b) if (i := _first_index(a, words)) is not None]
        if found:
            i, alias = min(found)
            hits.append({"brand_id": b.id, "alias": alias, "word_index": i})
    return sorted(hits, key=lambda h: h["word_index"])


def brand_id_for_name(name: str, brands: list[Brand]) -> str | None:
    """Map a brand name as the LLM wrote it ("Dot & Key Watermelon Sunscreen") to a tracked brand id."""
    padded = f" {normalize(name)} "
    for b in brands:
        if any(f" {a} " in padded for a in _aliases(b)):
            return b.id
    return None

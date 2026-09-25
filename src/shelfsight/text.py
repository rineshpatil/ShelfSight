import re
import unicodedata

_APOSTROPHES = re.compile(r"[’'`]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(s: str) -> str:
    """Lowercase, strip accents and apostrophes, collapse everything else to single spaces."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = _APOSTROPHES.sub("", s.lower())
    return _NON_ALNUM.sub(" ", s).strip()

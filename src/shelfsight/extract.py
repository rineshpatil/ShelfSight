from typing import Literal

from pydantic import BaseModel

from shelfsight.brand_match import brand_id_for_name, dictionary_hits
from shelfsight.config import Brand, Workspace
from shelfsight.engines.base import RetryableError
from shelfsight.store import Store
from shelfsight.text import normalize


class ExtractedBrand(BaseModel):
    name: str
    rank: int | None = None
    # Optional on purpose: Bedrock doesn't enforce the tool schema, and on long answers Nova drops
    # fields for a brand. The brand is still named; "unknown" beats losing the whole answer.
    is_recommended: bool | None = None
    sentiment: Literal["positive", "neutral", "negative"] | None = None
    claims: list[str] = []


class Extraction(BaseModel):
    brands: list[ExtractedBrand]
    answer_type: Literal["list", "single", "comparison", "refusal", "other"]


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "brands": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"},
            "rank": {"type": "INTEGER", "nullable": True},
            "is_recommended": {"type": "BOOLEAN"},
            "sentiment": {"type": "STRING", "enum": ["positive", "neutral", "negative"]},
            "claims": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": ["name", "rank", "is_recommended", "sentiment", "claims"]}},  # rank required: Nova omits optional fields
        "answer_type": {"type": "STRING", "enum": ["list", "single", "comparison", "refusal", "other"]},
    },
    "required": ["brands", "answer_type"],
}

PROMPT = """You extract brand mentions from a shopping assistant's answer.
Known brands in this category: {brands}.
List every brand the answer names, known or not, in the order the answer presents them.
rank: 1 for the first brand presented as a pick, 2 for the next, and so on; null if the brand is only mentioned in passing.
In a comparison, the brand the answer favours is rank 1 and the other compared brands follow in order of preference.
is_recommended: true only if the answer suggests the buyer choose it.
sentiment: how the answer describes the brand.
claims: short product claims attached to the brand, lowercase, e.g. "no white cast", "spf 50".

ANSWER:
{answer}"""

_KEYS = ("response_id", "workspace", "run_date", "engine", "mode", "prompt_id")


def reconcile(response: dict, ext: Extraction, hits: list[dict], brands: list[Brand],
              extractor_version: str) -> list[dict]:
    """Merge the LLM pass with the dictionary pass.

    - An LLM brand that maps to a tracked brand gets source "both" if the dictionary also saw it, else "llm".
    - An LLM brand that maps to nothing is kept with brand_id NULL: an early warning on new competitors.
    - A dictionary-only hit is added with rank and is_recommended NULL, unless the brand is ambiguous.
    - If nothing is found, one sentinel row (source "none") marks the response as extracted.
    """
    base = {k: response[k] for k in _KEYS} | {"extractor_version": extractor_version}
    ambiguous = {b.id for b in brands if b.ambiguous}
    dict_ids = {h["brand_id"] for h in hits}
    rows, seen = [], set()
    for eb in ext.brands:
        bid = brand_id_for_name(eb.name, brands)
        key = bid or f"raw:{normalize(eb.name)}"
        if key in seen:  # the LLM listed two products of one brand; keep the first, best-ranked
            continue
        seen.add(key)
        rows.append(base | {"brand_id": bid, "brand_raw": eb.name, "rank": eb.rank,
                            "is_recommended": eb.is_recommended, "sentiment": eb.sentiment,
                            "claims": eb.claims, "source": "both" if bid in dict_ids else "llm"})
    for h in hits:
        if h["brand_id"] in seen or h["brand_id"] in ambiguous:
            continue
        seen.add(h["brand_id"])
        rows.append(base | {"brand_id": h["brand_id"], "brand_raw": h["alias"], "rank": None,
                            "is_recommended": None, "sentiment": None, "claims": [], "source": "dictionary"})
    return rows or [base | {"brand_id": None, "brand_raw": None, "claims": [], "source": "none"}]


def extract_mentions(response: dict, llm, brands: list[Brand], extractor_version: str) -> list[dict]:
    prompt = PROMPT.format(brands=", ".join(b.name for b in brands), answer=response["response_text"])
    ext = Extraction.model_validate(llm.generate_json(prompt, SCHEMA))
    return reconcile(response, ext, dictionary_hits(response["response_text"], brands), brands, extractor_version)


def run_extract(*, store: Store, workspace: Workspace, llm, extractor_version: str, run_date) -> dict:
    """Extract every ok response for the day that has no rows yet at this extractor_version."""
    done = {r["response_id"] for r in store.rows(
        "SELECT DISTINCT response_id FROM mentions WHERE workspace = ? AND run_date = ? AND extractor_version = ?",
        [workspace.id, run_date, extractor_version])}
    todo = [r for r in store.rows(
        "SELECT response_id, workspace, run_date, engine, mode, prompt_id, response_text FROM raw_responses "
        "WHERE workspace = ? AND run_date = ? AND status = 'ok' ORDER BY response_id",
        [workspace.id, run_date]) if r["response_id"] not in done]
    rows, extracted, failed = [], 0, 0
    for r in todo:
        try:
            rows += extract_mentions(r, llm, workspace.brands, extractor_version)
            extracted += 1
        except RetryableError as e:
            failed += 1
            if e.status == 429:
                break  # quota gone: the rest waits for the next extract run
        except Exception:  # invalid JSON, schema mismatch or API failure: retried on the next extract run
            failed += 1
    store.write("mentions", rows)
    return {"todo": len(todo), "extracted": extracted, "failed": failed, "mentions": len(rows)}

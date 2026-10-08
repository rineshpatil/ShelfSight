"""Everything ShelfSight knows about one page: paste a URL, get its funnel, the searches it won and lost,
who wins instead, and a live check of the page against the claims that win.

Read-only over the lake: no engine calls, so a lookup is instant and free. Only URLs owned by a brand in a
tracked workspace are answered (and fetched), which also keeps the live check off arbitrary hosts.
"""
import html
import json
import re
from pathlib import Path

import httpx

from shelfsight.config import Brand, Settings, Workspace, load_workspace
from shelfsight.report import MEANING
from shelfsight.store import Store
from shelfsight.urls import brand_for_url, normalize_url

_LD_JSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_NOT_TEXT = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def find_brand(url: str, root: str | Path = "config/workspaces") -> tuple[Workspace, Brand] | None:
    """The first tracked workspace with a brand that owns this URL (its domain or a marketplace listing)."""
    for path in sorted(Path(root).glob("*.yaml")):
        ws = load_workspace(path.stem, root)
        bid = brand_for_url(url, ws.brands)
        if bid:
            return ws, next(b for b in ws.brands if b.id == bid)
    return None


def _schema_types(node, out: list[str]) -> None:
    if isinstance(node, list):
        for n in node:
            _schema_types(n, out)
    elif isinstance(node, dict):
        t = node.get("@type")
        out += t if isinstance(t, list) else [t] if t else []
        for v in node.values():
            if isinstance(v, (dict, list)):
                _schema_types(v, out)


def check_page(page_html: str, claims: list[str]) -> dict:
    """Schema types, FAQ size and which winning claims the page states in plain words."""
    types: list[str] = []
    for block in _LD_JSON.findall(page_html):
        try:
            _schema_types(json.loads(block), types)
        except json.JSONDecodeError:
            continue  # a broken block is invisible to assistants too
    text = html.unescape(_TAG.sub(" ", _NOT_TEXT.sub(" ", page_html))).lower()
    text = " ".join(text.split())
    title = _TITLE.search(page_html)
    return {
        "title": html.unescape(title.group(1)).strip() if title else None,
        "schema_types": sorted({t for t in types if isinstance(t, str)}),
        "faq_questions": types.count("Question"),
        "words": len(text.split()),
        # ponytail: exact-phrase match, so "spf 50 pa+++" misses "SPF 50+ PA++++"; fuzzy match if that misleads
        "claims": [{"claim": c, "on_page": c.lower() in text} for c in claims],
    }


def fetch_html(url: str) -> str:
    r = httpx.get(url, follow_redirects=True, timeout=20,
                  headers={"User-Agent": "Mozilla/5.0 (compatible; ShelfSight/0.1)"})
    r.raise_for_status()
    return r.text


# Responses where this brand did not win, at the current extractor and joiner versions
_LOSING = """SELECT response_id FROM funnel WHERE workspace = $ws AND brand_id = $b
             AND extractor_version = $ev AND joiner_version = $jv AND diagnosis <> 'winning'"""


def lookup(url: str, store: Store, settings: Settings, *, root: str | Path = "config/workspaces",
           fetch=fetch_html) -> dict:
    """The full AEO picture for one URL, across every stored run. Raises ValueError for a non-http URL."""
    raw = url.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    if not raw.lower().startswith(("http://", "https://")):
        raise ValueError("url must be http or https")
    url = normalize_url(raw)
    found = find_brand(url, root)
    if not found:
        return {"tracked": False, "url": url}
    ws, brand = found
    q = {"ws": ws.id, "b": brand.id, "ev": settings.extractor.version, "jv": settings.joiner_version}

    span = store.rows("""SELECT min(run_date) AS first, max(run_date) AS last, count(*) AS answers
                         FROM raw_responses WHERE workspace = $ws AND status = 'ok'""", {"ws": ws.id})[0]
    searches = store.rows("""
        SELECT search_query, count(*) AS times, min(position) AS best_position,
               list_sort(list(DISTINCT prompt_id)) AS prompts, list_sort(list(DISTINCT engine)) AS engines
        FROM candidates WHERE workspace = $ws AND url = $url
        GROUP BY search_query ORDER BY times DESC, best_position""", {"ws": ws.id, "url": url})
    cited = store.rows("""
        SELECT prompt_id, engine, count(*) AS times FROM citations
        WHERE workspace = $ws AND url = $url GROUP BY ALL ORDER BY times DESC, prompt_id""",
                       {"ws": ws.id, "url": url})
    diagnoses = store.rows("""
        SELECT diagnosis, count(*) AS answers, list_sort(list(DISTINCT prompt_id)) AS prompts
        FROM funnel WHERE workspace = $ws AND brand_id = $b AND extractor_version = $ev AND joiner_version = $jv
        GROUP BY diagnosis ORDER BY answers DESC, diagnosis""", q)
    missed = store.rows("""
        SELECT search_query, count(DISTINCT response_id) AS answers FROM candidates
        WHERE workspace = $ws AND response_id IN (
            SELECT response_id FROM funnel WHERE workspace = $ws AND brand_id = $b AND diagnosis = 'not_eligible'
              AND extractor_version = $ev AND joiner_version = $jv)
        GROUP BY search_query ORDER BY answers DESC, search_query LIMIT 15""", q)
    sources = store.rows(f"""
        SELECT domain, any_value(domain_type) AS domain_type, count(*) AS citations FROM citations
        WHERE workspace = $ws AND coalesce(brand_id, '') <> $b AND response_id IN ({_LOSING})
        GROUP BY domain ORDER BY citations DESC, domain LIMIT 12""", q)
    winners = store.rows(f"""
        SELECT coalesce(brand_id, brand_raw) AS brand, brand_id IS NOT NULL AS tracked, count(*) AS answers
        FROM mentions WHERE workspace = $ws AND extractor_version = $ev AND is_recommended
          AND coalesce(brand_id, '') <> $b AND response_id IN ({_LOSING})
        GROUP BY ALL ORDER BY answers DESC, brand LIMIT 10""", q)
    claims = store.rows(f"""
        SELECT claim, count(*) AS times FROM (
            SELECT unnest(claims) AS claim FROM mentions
            WHERE workspace = $ws AND extractor_version = $ev AND is_recommended
              AND coalesce(brand_id, '') <> $b AND response_id IN ({_LOSING}))
        WHERE coalesce(claim, '') <> ''
        GROUP BY claim ORDER BY times DESC, claim LIMIT 15""", q)

    try:
        page = check_page(fetch(raw), [c["claim"] for c in claims])
    except Exception as e:  # the stored results still stand without the live check
        page = {"error": f"{type(e).__name__}: {e}"}
    on_page = [x["on_page"] for x in page["claims"]] if "claims" in page else [None] * len(claims)
    names = {b.id: b.name for b in ws.brands}
    return {
        "tracked": True, "url": url, "workspace": ws.id, "category": ws.category,
        "brand": {"id": brand.id, "name": brand.name, "is_client": brand.is_client},
        "runs": span,
        "eligible_top_n": settings.eligible_top_n,
        "searches": searches,
        "cited": cited,
        "diagnoses": [r | {"meaning": MEANING[r["diagnosis"]]} for r in diagnoses],
        "missed_searches": missed,
        "sources_instead": sources,
        "winners": [r | {"name": names.get(r["brand"], r["brand"])} for r in winners],
        "winning_claims": [c | {"on_page": o} for c, o in zip(claims, on_page)],
        "page": page,
    }

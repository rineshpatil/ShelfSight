from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import tldextract

from shelfsight.config import Brand

GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_extract = tldextract.TLDExtract(suffix_list_urls=())  # bundled suffix list; never hits the network
_TRACKING_KEYS = {"gclid", "fbclid", "srsltid", "ref", "mc_cid", "mc_eid"}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in _TRACKING_KEYS


def normalize_url(url: str) -> str:
    """Lowercase host, drop www., tracking params, fragment and trailing slash."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def registered_domain(url_or_host: str) -> str:
    ext = _extract(url_or_host)
    return ".".join(p for p in (ext.domain, ext.suffix) if p).lower()


def domain_type(domain: str, brand_domains: set[str], domain_types: dict[str, list[str]]) -> str:
    if domain in brand_domains:
        return "brand"
    for kind, domains in domain_types.items():
        if domain in domains:
            return kind
    return "other"


def brand_for_url(url: str, brands: list[Brand]) -> str | None:
    """The brand that owns this URL: its own domain, or a marketplace listing matching one of its url_patterns."""
    domain = registered_domain(url)
    lowered = url.lower()
    for b in brands:
        if domain in b.domains or any(p in lowered for p in b.url_patterns):
            return b.id
    return None


def resolve_grounding_url(uri: str, client: httpx.Client) -> str:
    """Gemini grounding chunks point at a Google redirect. Return its destination, or the input if unresolved."""
    if urlsplit(uri).netloc != GROUNDING_REDIRECT_HOST:
        return uri
    try:
        r = client.get(uri, follow_redirects=False, timeout=10)
    except httpx.HTTPError:
        return uri
    return r.headers.get("location", uri)

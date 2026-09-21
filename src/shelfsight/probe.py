import httpx

from shelfsight.config import Brand
from shelfsight.engines.base import Pacer, raise_for_status, retrying
from shelfsight.store import utcnow
from shelfsight.urls import brand_for_url, domain_type, normalize_url, registered_domain


class SearxProbe:
    """Runs a model's own search query through SearxNG to approximate the candidate set it chose from."""

    def __init__(self, base_url: str, engines: list[str], client: httpx.Client, top_n: int = 20,
                 min_interval_s: float = 0, retry_wait=None):
        self.base_url, self.engines, self.top_n = base_url.rstrip("/"), engines, top_n
        self._client = client
        self._pacer = Pacer(min_interval_s)
        self._retrying = retrying(retry_wait)

    def search(self, query: str) -> list[dict]:
        """Top-N results in SearxNG's ranked order, as [{"url", "source_engine"}]."""
        data = self._retrying(self._get, query)
        return [{"url": r["url"], "source_engine": r.get("engine", "")} for r in data.get("results", [])[: self.top_n]]

    def _get(self, query: str) -> dict:
        self._pacer.wait()
        params = {"q": query, "format": "json", "engines": ",".join(self.engines)}
        r = self._client.get(f"{self.base_url}/search", params=params, timeout=30)
        raise_for_status(r)
        return r.json()


def candidate_rows(probe: SearxProbe, response: dict, queries: list[str], max_queries: int,
                   brands: list[Brand], domain_types: dict[str, list[str]]) -> list[dict]:
    brand_domains = {d for b in brands for d in b.domains}
    rows = []
    for query_index, query in enumerate(queries[:max_queries]):
        probed_at = utcnow()
        for position, hit in enumerate(probe.search(query), start=1):
            url = normalize_url(hit["url"])
            domain = registered_domain(url)
            rows.append({
                "response_id": response["response_id"], "workspace": response["workspace"],
                "run_date": response["run_date"], "engine": response["engine"], "prompt_id": response["prompt_id"],
                "search_query": query, "query_index": query_index, "position": position,
                "url": url, "domain": domain, "domain_type": domain_type(domain, brand_domains, domain_types),
                "source_engine": hit["source_engine"], "brand_id": brand_for_url(url, brands),
                "probed_at": probed_at,
            })
    return rows

import json
from datetime import date
from pathlib import Path

import httpx
import respx
from tenacity import wait_none

from shelfsight.config import load_settings, load_workspace
from shelfsight.probe import SearxProbe, candidate_rows

RESPONSE = {"response_id": "r1", "workspace": "dotandkey", "run_date": date(2026, 9, 21),
            "engine": "gemini", "prompt_id": "SUN-DISC-001"}


def fixture():
    return json.loads(Path("tests/fixtures/searxng.json").read_text(encoding="utf-8"))


@respx.mock
def test_search_sends_json_format_and_engines_and_truncates():
    route = respx.get(url__startswith="http://searx.test/search").mock(return_value=httpx.Response(200, json=fixture()))
    with httpx.Client() as c:
        hits = SearxProbe("http://searx.test/", ["google", "bing"], c, top_n=2, language="en-IN",
                          retry_wait=wait_none()).search("sunscreen")
    params = route.calls[0].request.url.params
    assert (params["q"], params["format"], params["engines"]) == ("sunscreen", "json", "google,bing")
    assert params["language"] == "en-IN"  # otherwise a US-hosted probe scores US results
    assert hits == [
        {"url": "https://www.nykaa.com/dot-key-watermelon-sunscreen/p/1?utm_source=x", "source_engine": "google"},
        {"url": "https://www.reddit.com/r/IndianSkincareAddicts/comments/abc/", "source_engine": "brave"},
    ]


@respx.mock
def test_candidate_rows_rank_and_attribute_results():
    respx.get(url__startswith="http://searx.test/search").mock(return_value=httpx.Response(200, json=fixture()))
    ws, s = load_workspace("dotandkey"), load_settings()
    with httpx.Client() as c:
        probe = SearxProbe("http://searx.test", ["google"], c, top_n=3, retry_wait=wait_none())
        rows = candidate_rows(probe, RESPONSE, ["q1", "q2", "q3"], max_queries=2,
                              brands=ws.brands, domain_types=s.domain_types)
    assert len(rows) == 6  # 2 queries × top 3
    assert {r["search_query"] for r in rows} == {"q1", "q2"}
    first = rows[:3]
    assert [r["position"] for r in first] == [1, 2, 3]
    assert [r["brand_id"] for r in first] == ["dotandkey", None, "minimalist"]
    assert [r["domain_type"] for r in first] == ["marketplace", "reddit", "brand"]
    assert first[0]["url"] == "https://nykaa.com/dot-key-watermelon-sunscreen/p/1"
    assert (first[0]["response_id"], first[0]["query_index"]) == ("r1", 0)

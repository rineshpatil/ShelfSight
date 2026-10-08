from datetime import date

import pytest

from shelfsight.config import load_settings
from shelfsight.page import check_page, lookup
from shelfsight.store import Store

D = date(2026, 9, 22)
WS = "acme"
PAGE = "https://ours.com/products/spf50"

WORKSPACE = """
id: acme
category: sunscreen
brands:
  - {id: ours, name: Ours, is_client: true, aliases: [ours], domains: [ours.com]}
  - {id: rival, name: Rival, aliases: [rival], domains: [rival.com]}
"""

HTML = """<html><head><title>SPF 50 &amp; more</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
  {"@type": "Product", "name": "SPF 50"},
  {"@type": "FAQPage", "mainEntity": [{"@type": "Question"}, {"@type": "Question"}]}]}</script>
<script type="application/ld+json">{broken</script>
<script>var x = "matte finish";</script></head>
<body><h1>Lightweight SPF 50</h1><p>No white cast.</p></body></html>"""


def test_check_page_reads_schema_faq_and_visible_claims():
    out = check_page(HTML, ["no white cast", "matte finish", "lightweight"])
    assert out["title"] == "SPF 50 & more"
    assert out["schema_types"] == ["FAQPage", "Product", "Question"]
    assert out["faq_questions"] == 2
    # "matte finish" only appears inside a script, which no reader sees
    assert [c["on_page"] for c in out["claims"]] == [True, False, True]


@pytest.fixture
def lake(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    (root / "acme.yaml").write_text(WORKSPACE, encoding="utf-8")
    s = load_settings()
    ev, jv = s.extractor.version, s.joiner_version
    store = Store(tmp_path / "lake")
    base = dict(workspace=WS, run_date=D, engine="gemini")
    store.write("raw_responses", [base | dict(response_id=r, prompt_id=f"P{r}", mode="web", status="ok")
                                  for r in ("1", "2")])
    store.write("candidates", [
        base | dict(response_id="1", prompt_id="P1", search_query="best spf", position=2, url=PAGE, brand_id="ours"),
        base | dict(response_id="2", prompt_id="P2", search_query="spf oily skin", position=1,
                    url="https://rival.com/", brand_id="rival"),
    ])
    store.write("citations", [
        base | dict(response_id="1", prompt_id="P1", mode="web", position=1, url=PAGE, domain="ours.com",
                    brand_id="ours"),
        base | dict(response_id="2", prompt_id="P2", mode="web", position=1, url="https://blog.com/x",
                    domain="blog.com", domain_type="other"),
    ])
    store.write("mentions", [
        base | dict(response_id="1", prompt_id="P1", mode="web", brand_id="ours", rank=1, is_recommended=True,
                    claims=[], extractor_version=ev),
        base | dict(response_id="2", prompt_id="P2", mode="web", brand_id="rival", rank=1, is_recommended=True,
                    claims=["matte finish", "no white cast"], extractor_version=ev),
    ])
    store.write("funnel", [
        base | dict(response_id="1", prompt_id="P1", mode="web", brand_id="ours", is_client=True,
                    diagnosis="winning", extractor_version=ev, joiner_version=jv),
        base | dict(response_id="2", prompt_id="P2", mode="web", brand_id="ours", is_client=True,
                    diagnosis="not_eligible", extractor_version=ev, joiner_version=jv),
        base | dict(response_id="2", prompt_id="P2", mode="web", brand_id="ours", is_client=True,
                    diagnosis="not_eligible", extractor_version="old", joiner_version=jv),  # stale version: ignored
    ])
    return store, s, root


def test_lookup_joins_the_page_to_its_funnel(lake):
    store, settings, root = lake
    out = lookup("www.ours.com/products/spf50/", store, settings, root=root, fetch=lambda url: HTML)
    assert out["url"] == PAGE  # bare host, www and trailing slash all normalise to the stored form
    assert out["brand"]["id"] == "ours"
    assert [(s["search_query"], s["best_position"]) for s in out["searches"]] == [("best spf", 2)]
    assert out["cited"] == [{"prompt_id": "P1", "engine": "gemini", "times": 1}]
    assert {d["diagnosis"]: d["answers"] for d in out["diagnoses"]} == {"winning": 1, "not_eligible": 1}
    assert out["missed_searches"] == [{"search_query": "spf oily skin", "answers": 1}]
    assert [s["domain"] for s in out["sources_instead"]] == ["blog.com"]
    assert [w["name"] for w in out["winners"]] == ["Rival"]
    assert {c["claim"]: c["on_page"] for c in out["winning_claims"]} == {"matte finish": False, "no white cast": True}


def test_lookup_survives_a_page_that_cannot_be_fetched(lake):
    store, settings, root = lake

    def fail(url):
        raise OSError("connection refused")

    out = lookup(PAGE, store, settings, root=root, fetch=fail)
    assert out["page"] == {"error": "OSError: connection refused"}
    assert out["winning_claims"][0]["on_page"] is None


def test_lookup_of_an_untracked_url_fetches_nothing(lake):
    store, settings, root = lake
    out = lookup("https://elsewhere.com/p", store, settings, root=root, fetch=pytest.fail)
    assert out == {"tracked": False, "url": "https://elsewhere.com/p"}


def test_lookup_rejects_non_http_urls(lake):
    store, settings, root = lake
    with pytest.raises(ValueError):
        lookup("file:///etc/passwd", store, settings, root=root)

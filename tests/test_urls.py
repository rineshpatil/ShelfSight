import httpx
import respx

from shelfsight.config import Brand
from shelfsight.urls import brand_for_url, domain_type, normalize_url, registered_domain, resolve_grounding_url


def test_normalize_strips_tracking_www_fragment_and_trailing_slash():
    got = normalize_url("https://www.Nykaa.com/dot-key/p/123/?utm_source=x&gclid=y&size=50#reviews")
    assert got == "https://nykaa.com/dot-key/p/123?size=50"


def test_normalize_keeps_root_path():
    assert normalize_url("https://dotandkey.com") == "https://dotandkey.com/"


def test_registered_domain_handles_subdomains_and_cc_tlds():
    assert registered_domain("https://m.youtube.com/watch?v=1") == "youtube.com"
    assert registered_domain("https://www.amazon.in/dp/B0") == "amazon.in"
    assert registered_domain("https://beminimalist.co/products/x") == "beminimalist.co"


def test_domain_type():
    types = {"marketplace": ["nykaa.com"], "reddit": ["reddit.com"]}
    assert domain_type("dotandkey.com", {"dotandkey.com"}, types) == "brand"
    assert domain_type("nykaa.com", set(), types) == "marketplace"
    assert domain_type("example.com", set(), types) == "other"


def test_brand_for_url_matches_own_domain_or_marketplace_pattern():
    brands = [Brand(id="dk", name="Dot & Key", domains=["dotandkey.com"], url_patterns=["nykaa.com/dot-key"])]
    assert brand_for_url("https://dotandkey.com/products/x", brands) == "dk"
    assert brand_for_url("https://nykaa.com/dot-key-sunscreen/p/1", brands) == "dk"
    assert brand_for_url("https://nykaa.com/minimalist/p/2", brands) is None


@respx.mock
def test_resolve_grounding_url_reads_location_header():
    src = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA"
    respx.get(src).mock(return_value=httpx.Response(302, headers={"location": "https://www.nykaa.com/x"}))
    with httpx.Client() as c:
        assert resolve_grounding_url(src, c) == "https://www.nykaa.com/x"


@respx.mock
def test_resolve_grounding_url_returns_input_on_network_error():
    src = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB"
    respx.get(src).mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as c:
        assert resolve_grounding_url(src, c) == src


def test_resolve_grounding_url_passes_through_normal_urls():
    with httpx.Client() as c:
        assert resolve_grounding_url("https://reddit.com/r/x", c) == "https://reddit.com/r/x"

import json
from pathlib import Path

import httpx
import pytest
import respx
from tenacity import wait_none

from shelfsight.engines.base import RetryableError
from shelfsight.engines.gemini import API, GeminiEngine

URL = API.format(model="gemini-test")
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def engine(client):
    return GeminiEngine(api_key="k", model="gemini-test", client=client, retry_wait=wait_none())


def ok(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


@respx.mock
def test_web_mode_parses_queries_and_resolves_citations():
    fixture = json.loads(Path("tests/fixtures/gemini_grounded.json").read_text(encoding="utf-8"))
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=fixture))
    respx.get(REDIRECT + "AAA").mock(
        return_value=httpx.Response(302, headers={"location": "https://www.nykaa.com/dot-key-sunscreen/p/1"}))
    respx.get(REDIRECT + "BBB").mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as c:
        res = engine(c).ask("best sunscreen", "sys", mode="web")
    req = route.calls[0].request
    assert json.loads(req.content)["tools"] == [{"google_search": {}}]
    assert req.headers["x-goog-api-key"] == "k"
    assert "key" not in req.url.params
    assert res.web_search_queries == ["best sunscreen for oily skin india under 500"]
    assert [(c.position, c.url) for c in res.citations] == [
        (1, "https://www.nykaa.com/dot-key-sunscreen/p/1"),
        (2, "https://reddit.com/"),  # unresolved redirect falls back to the chunk title
    ]
    assert (res.input_tokens, res.output_tokens) == (42, 180)
    assert "Minimalist" in res.text


@respx.mock
def test_no_web_mode_sends_no_tools():
    route = respx.post(URL).mock(return_value=ok("hi"))
    with httpx.Client() as c:
        res = engine(c).ask("q", "sys", mode="no_web")
    assert "tools" not in json.loads(route.calls[0].request.content)
    assert (res.text, res.citations, res.web_search_queries) == ("hi", [], [])


@respx.mock
def test_retries_429_then_succeeds():
    respx.post(URL).mock(side_effect=[httpx.Response(429, text="slow down"), ok("ok")])
    with httpx.Client() as c:
        assert engine(c).ask("q", "sys", mode="no_web").text == "ok"


@respx.mock
def test_persistent_429_raises_retryable_with_status():
    respx.post(URL).mock(return_value=httpx.Response(429, text="quota"))
    with httpx.Client() as c, pytest.raises(RetryableError) as e:
        engine(c).ask("q", "sys", mode="no_web")
    assert e.value.status == 429


@respx.mock
def test_generate_json_requests_json_and_parses_it():
    route = respx.post(URL).mock(return_value=ok('{"brands": []}'))
    with httpx.Client() as c:
        assert engine(c).generate_json("p", {"type": "OBJECT"}) == {"brands": []}
    cfg = json.loads(route.calls[0].request.content)["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["responseSchema"] == {"type": "OBJECT"}

import json

import httpx
import pytest
import respx
from tenacity import wait_none

from shelfsight.engines.groq import API, GroqEngine

OK = {"model": "llama-served", "choices": [{"message": {"content": "Try Minimalist."}}],
      "usage": {"prompt_tokens": 20, "completion_tokens": 5}}


def engine(client):
    return GroqEngine(api_key="k", model="groq-test", client=client, retry_wait=wait_none())


@respx.mock
def test_parses_chat_completion_and_sends_bearer_key():
    route = respx.post(API).mock(return_value=httpx.Response(200, json=OK))
    with httpx.Client() as c:
        res = engine(c).ask("q", "sys", mode="no_web")
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer k"
    body = json.loads(req.content)
    assert body["model"] == "groq-test"
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    assert (res.text, res.model, res.input_tokens, res.output_tokens) == ("Try Minimalist.", "groq-test", 20, 5)
    assert res.raw["model"] == "llama-served"  # served model kept in raw, to spot silent swaps


def test_rejects_web_mode():
    with httpx.Client() as c, pytest.raises(ValueError, match="no web search"):
        engine(c).ask("q", "sys", mode="web")


@respx.mock
def test_retries_503():
    respx.post(API).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=OK)])
    with httpx.Client() as c:
        assert engine(c).ask("q", "sys", mode="no_web").text == "Try Minimalist."

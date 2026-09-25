import pytest
from botocore.exceptions import ClientError
from tenacity import wait_none

from shelfsight.engines.base import RetryableError
from shelfsight.engines.nova import NovaEngine, to_json_schema

# Shape of a real nova_grounding Converse response (captured 2026-09-22, trimmed).
GROUNDED = {
    "output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "nova_grounding",
                     "input": {"query": "best sunscreen for oily skin in india under 500"}}},
        {"toolResult": {"toolUseId": "t1", "content": [{"text": "[HIDDEN]"}], "status": "success",
                        "type": "nova_grounding_result"}},
        {"text": "1. **Minimalist SPF 50**"},
        {"citationsContent": {"citations": [{"location": {"web": {"url": "https://www.healthshots.com/a", "domain": "healthshots.com"}}}]}},
        {"text": "\n2. **Foxtale Glow**"},
        {"citationsContent": {"citations": [{"location": {"web": {"url": "https://www.healthshots.com/a", "domain": "healthshots.com"}}},
                                            {"location": {"web": {"url": "https://foxtale.in/b", "domain": "foxtale.in"}}}]}},
    ]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 148, "outputTokens": 493, "totalTokens": 641},
    "metrics": {"latencyMs": 9000},
}


class FakeBedrock:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def throttled(code="ThrottlingException"):
    return ClientError({"Error": {"Code": code, "Message": "slow down"}}, "Converse")


def engine(client, **kw):
    return NovaEngine(model="us.amazon.nova-2-lite-v1:0", client=client, retry_wait=wait_none(), **kw)


def test_web_mode_returns_model_queries_and_unique_citations():
    fake = FakeBedrock(GROUNDED)
    res = engine(fake).ask("best sunscreen", "sys", mode="web")
    call = fake.calls[0]
    assert call["toolConfig"] == {"tools": [{"systemTool": {"name": "nova_grounding"}}]}
    assert call["system"] == [{"text": "sys"}]
    assert call["inferenceConfig"]["maxTokens"] == 2000  # always explicit: unset maxTokens reserves quota
    assert res.web_search_queries == ["best sunscreen for oily skin in india under 500"]
    assert [(c.position, c.url) for c in res.citations] == [(1, "https://www.healthshots.com/a"), (2, "https://foxtale.in/b")]
    assert res.text == "1. **Minimalist SPF 50**\n2. **Foxtale Glow**"
    assert (res.input_tokens, res.output_tokens, res.latency_ms) == (148, 493, 9000)


def test_no_web_mode_sends_no_tools():
    fake = FakeBedrock({"output": {"message": {"content": [{"text": "hi"}]}}, "usage": {}})
    res = engine(fake).ask("q", "sys", mode="no_web")
    assert "toolConfig" not in fake.calls[0]
    assert (res.text, res.citations, res.web_search_queries) == ("hi", [], [])


def test_throttling_is_retried_then_succeeds():
    fake = FakeBedrock(throttled(), {"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {}})
    assert engine(fake).ask("q", "sys", mode="no_web").text == "ok"


def test_persistent_throttling_raises_retryable_429():
    fake = FakeBedrock(*[throttled()] * 4)
    with pytest.raises(RetryableError) as e:
        engine(fake).ask("q", "sys", mode="no_web")
    assert e.value.status == 429


def test_validation_error_is_not_retried():
    fake = FakeBedrock(throttled("ValidationException"))
    with pytest.raises(ClientError):
        engine(fake).ask("q", "sys", mode="no_web")
    assert len(fake.calls) == 1


def test_generate_json_forces_the_tool_and_returns_its_input():
    fake = FakeBedrock({"output": {"message": {"content": [
        {"toolUse": {"toolUseId": "x", "name": "record", "input": {"brands": []}}}]}}, "usage": {}})
    assert engine(fake).generate_json("p", {"type": "OBJECT", "properties": {}}) == {"brands": []}
    tc = fake.calls[0]["toolConfig"]
    assert tc["toolChoice"] == {"tool": {"name": "record"}}
    assert tc["tools"][0]["toolSpec"]["inputSchema"] == {"json": {"type": "object", "properties": {}}}
    assert fake.calls[0]["inferenceConfig"]["temperature"] == 0


def test_to_json_schema_converts_gemini_style_schema():
    gemini = {"type": "OBJECT", "properties": {
        "rank": {"type": "INTEGER", "nullable": True},
        "tags": {"type": "ARRAY", "items": {"type": "STRING", "enum": ["a", "b"]}}}, "required": ["rank"]}
    assert to_json_schema(gemini) == {"type": "object", "properties": {
        "rank": {"type": ["integer", "null"]},
        "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}}, "required": ["rank"]}

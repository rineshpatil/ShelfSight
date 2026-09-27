"""Amazon Nova on Bedrock via the Converse API.

Web mode uses the nova_grounding system tool. Its response carries the model's own search queries
as toolUse blocks (input.query) and the pages it used as citationsContent blocks. The raw search
results come back as "[HIDDEN]", so the candidate set still comes from the SearxNG probe.
The query field isn't documented, so scripts/smoke_nova.py checks it before real runs.
"""
import time

from botocore.exceptions import ClientError

from shelfsight.engines.base import Citation, EngineResult, Pacer, RetryableError, retrying

_THROTTLED = {"ThrottlingException", "TooManyRequestsException"}
_TRANSIENT = {"ServiceUnavailableException", "InternalServerException", "ModelTimeoutException",
              "ModelNotReadyException"}


def to_json_schema(schema: dict) -> dict:
    """Gemini-style schema (uppercase types, nullable) → JSON Schema, so one extraction schema serves both engines."""
    out = {}
    for key, value in schema.items():
        if key == "type":
            out["type"] = value.lower()
        elif key == "nullable":
            continue
        elif key == "properties":
            out[key] = {name: to_json_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out[key] = to_json_schema(value)
        else:
            out[key] = value
    if schema.get("nullable"):
        out["type"] = [out["type"], "null"]
    return out


class NovaEngine:
    name = "nova"

    def __init__(self, model: str, client, min_interval_s: float = 0, temperature: float = 0.7,
                 max_tokens: int = 2000, retry_wait=None):
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens
        self._client = client  # a boto3 bedrock-runtime client; build it with botocore retries off
        self._pacer = Pacer(min_interval_s)
        self._retrying = retrying(retry_wait)

    def ask(self, prompt: str, system_prompt: str, mode: str) -> EngineResult:
        kwargs = {
            "system": [{"text": system_prompt}],
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": self.max_tokens, "temperature": self.temperature},
        }
        if mode == "web":
            kwargs["toolConfig"] = {"tools": [{"systemTool": {"name": "nova_grounding"}}]}
        data, latency_ms = self._retrying(self._converse, kwargs)
        blocks = data.get("output", {}).get("message", {}).get("content", [])
        queries = [b["toolUse"]["input"]["query"] for b in blocks
                   if b.get("toolUse", {}).get("name") == "nova_grounding" and "query" in b["toolUse"].get("input", {})]
        urls: list[str] = []
        for b in blocks:
            for c in b.get("citationsContent", {}).get("citations", []):
                url = c.get("location", {}).get("web", {}).get("url")
                if url and url not in urls:  # Nova repeats a source for every sentence it supports
                    urls.append(url)
        usage = data.get("usage", {})
        return EngineResult(
            text="".join(b["text"] for b in blocks if "text" in b),
            model=self.model,
            web_search_queries=queries,
            citations=[Citation(url=u, title="", position=i) for i, u in enumerate(urls, start=1)],
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            latency_ms=data.get("metrics", {}).get("latencyMs", latency_ms),
            raw=data,
        )

    def generate_json(self, prompt: str, schema: dict) -> dict:
        """Structured output via a forced tool call; returns the tool input."""
        kwargs = {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": self.max_tokens, "temperature": 0},
            "toolConfig": {
                "tools": [{"toolSpec": {"name": "record", "description": "Record the extracted data",
                                        "inputSchema": {"json": to_json_schema(schema)}}}],
                "toolChoice": {"tool": {"name": "record"}},
            },
        }
        data, _ = self._retrying(self._converse, kwargs)
        for b in data.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in b:
                return b["toolUse"]["input"]
        raise ValueError(f"nova returned no tool call (stopReason={data.get('stopReason')})")

    def _converse(self, kwargs: dict) -> tuple[dict, int]:
        self._pacer.wait()
        start = time.monotonic()
        try:
            data = self._client.converse(modelId=self.model, **kwargs)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in _THROTTLED:
                raise RetryableError(429, str(e)) from e
            if code in _TRANSIENT:
                raise RetryableError(503, str(e)) from e
            raise
        return data, int((time.monotonic() - start) * 1000)

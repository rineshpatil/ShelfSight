import httpx

from shelfsight.engines.base import EngineResult, Pacer, post_json, retrying

API = "https://api.groq.com/openai/v1/chat/completions"


class GroqEngine:
    name = "groq"

    def __init__(self, api_key: str, model: str, client: httpx.Client, min_interval_s: float = 0,
                 temperature: float = 0.7, retry_wait=None):
        self.model, self.temperature = model, temperature
        self._client = client
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._pacer = Pacer(min_interval_s)
        self._retrying = retrying(retry_wait)

    def ask(self, prompt: str, system_prompt: str, mode: str) -> EngineResult:
        if mode != "no_web":
            raise ValueError("groq has no web search tool; only no_web mode is supported")
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        }
        data, latency_ms = self._retrying(post_json, self._client, API, self._headers, body, self._pacer)
        usage = data.get("usage", {})
        return EngineResult(
            text=data["choices"][0]["message"].get("content") or "",
            model=self.model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            raw=data,
        )

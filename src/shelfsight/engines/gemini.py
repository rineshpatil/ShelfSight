import json

import httpx

from shelfsight.engines.base import Citation, EngineResult, Pacer, post_json, retrying
from shelfsight.urls import GROUNDING_REDIRECT_HOST, resolve_grounding_url

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _text(candidate: dict) -> str:
    return "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))


class GeminiEngine:
    name = "gemini"

    def __init__(self, api_key: str, model: str, client: httpx.Client, min_interval_s: float = 0,
                 temperature: float = 0.7, retry_wait=None):
        self.model, self.temperature = model, temperature
        self._client = client
        self._url = API.format(model=model)
        self._headers = {"x-goog-api-key": api_key}  # a header, never a query string
        self._pacer = Pacer(min_interval_s)
        self._retrying = retrying(retry_wait)

    def ask(self, prompt: str, system_prompt: str, mode: str) -> EngineResult:
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature},
        }
        if mode == "web":
            body["tools"] = [{"google_search": {}}]
        data, latency_ms = self._retrying(post_json, self._client, self._url, self._headers, body, self._pacer)
        cand = (data.get("candidates") or [{}])[0]
        gm = cand.get("groundingMetadata", {})
        chunks = [ch["web"] for ch in gm.get("groundingChunks", []) if "web" in ch]
        usage = data.get("usageMetadata", {})
        return EngineResult(
            text=_text(cand),
            model=self.model,
            web_search_queries=gm.get("webSearchQueries", []),
            citations=[Citation(url=self._resolve(w), title=w.get("title", ""), position=i)
                       for i, w in enumerate(chunks, start=1)],
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            latency_ms=latency_ms,
            raw=data,
        )

    def generate_json(self, prompt: str, schema: dict) -> dict:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema},
        }
        data, _ = self._retrying(post_json, self._client, self._url, self._headers, body, self._pacer)
        return json.loads(_text(data["candidates"][0]))

    def _resolve(self, web: dict) -> str:
        url = resolve_grounding_url(web["uri"], self._client)
        title = web.get("title", "")
        if GROUNDING_REDIRECT_HOST in url and "." in title:
            return f"https://{title}/"  # unresolved redirect: the chunk title is the source domain
        return url

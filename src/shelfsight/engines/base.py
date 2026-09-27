import time
from dataclasses import dataclass, field

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential


class RetryableError(Exception):
    """HTTP 429 or 5xx. Retried; a 429 that persists means the free-tier quota is gone for this run."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:2000]}")  # long enough to keep the quota id Google puts late in the body
        self.status = status


@dataclass
class Citation:
    url: str
    title: str
    position: int


@dataclass
class EngineResult:
    text: str
    model: str
    web_search_queries: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)


class Pacer:
    """Enforces a minimum gap between calls. Free tiers are rate-limited per minute."""

    def __init__(self, min_interval_s: float, clock=time.monotonic, sleep=time.sleep):
        self.min_interval_s = min_interval_s
        self._clock, self._sleep, self._last = clock, sleep, None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            gap = self.min_interval_s - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now += gap
        self._last = now


def retrying(wait=None) -> Retrying:
    """4 attempts with exponential backoff on 429/5xx and transport errors. Pass wait=wait_none() in tests."""
    return Retrying(
        retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
        stop=stop_after_attempt(4),
        wait=wait or wait_exponential(multiplier=2, max=30),
        reraise=True,
    )


def raise_for_status(r: httpx.Response) -> None:
    if r.status_code == 429 or r.status_code >= 500:
        raise RetryableError(r.status_code, r.text)
    r.raise_for_status()


def post_json(client: httpx.Client, url: str, headers: dict, body: dict, pacer: Pacer) -> tuple[dict, int]:
    """One paced POST. Returns (json, latency_ms); the latency excludes the pacing wait."""
    pacer.wait()
    start = time.monotonic()
    r = client.post(url, headers=headers, json=body, timeout=120)
    latency_ms = int((time.monotonic() - start) * 1000)
    raise_for_status(r)
    return r.json(), latency_ms

"""LIVE: confirm the pinned Gemini model still returns webSearchQueries and groundingChunks. Run after any model change."""
import os
import sys

import httpx

from shelfsight.config import load_settings
from shelfsight.engines.gemini import GeminiEngine

s = load_settings()
cfg = next(e for e in s.engines if e.name == "gemini")
with httpx.Client() as c:
    res = GeminiEngine(os.environ["GEMINI_API_KEY"], cfg.model, c).ask(
        "best sunscreen for oily skin in india under 500", s.system_prompt, "web")
gm = res.raw["candidates"][0].get("groundingMetadata", {})
print("model:", cfg.model)
print("webSearchQueries:", gm.get("webSearchQueries"))
print("groundingChunks:", len(gm.get("groundingChunks", [])))
for cit in res.citations:
    print(f"  {cit.position}. {cit.url}")
if not gm.get("webSearchQueries") or not gm.get("groundingChunks"):
    sys.exit(f"FAIL: {cfg.model} is missing webSearchQueries or groundingChunks. Pin another model in config/config.yaml.")
print("OK")

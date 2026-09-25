"""LIVE: confirm the pinned Nova model still returns its own search queries and citations, and can extract.

The query comes from an undocumented toolUse block in the nova_grounding response, so run this
before real runs and after any model change. Uses your AWS credentials; each call is billed.
"""
import sys

from shelfsight.cli import bedrock_client
from shelfsight.config import load_settings
from shelfsight.engines.nova import NovaEngine
from shelfsight.extract import SCHEMA

s = load_settings()
cfg = next(e for e in s.engines if e.name == "nova")
nova = NovaEngine(cfg.model, bedrock_client(cfg.region))
res = nova.ask("best sunscreen for oily skin in india under 500", s.system_prompt, "web")
print("model:", cfg.model)
print("webSearchQueries:", res.web_search_queries)
print("citations:", len(res.citations))
for c in res.citations:
    print(f"  {c.position}. {c.url}")

ex = NovaEngine(s.extractor.model, bedrock_client(s.extractor.region)).generate_json(
    'Known brands: Minimalist, Dot & Key. List brands in: "Minimalist beats Dot & Key for oily skin."', SCHEMA)
print("extraction:", ex)

if not res.web_search_queries or not res.citations:
    sys.exit(f"FAIL: {cfg.model} returned no search queries or no citations. The Eligible stage can't run on Nova.")
if not ex.get("brands"):
    sys.exit("FAIL: Nova extraction returned no brands.")
print("OK")

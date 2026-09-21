"""LIVE check of extractor accuracy against hand-labelled answers. Calls Gemini, so it is not run in CI.

  uv run python scripts/eval_extractor.py
      score every tests/fixtures/labelled/*.json; exit 1 if brand recall < 95%
  uv run python scripts/eval_extractor.py --export 40 --workspace dotandkey --date 2026-09-28
      dump 40 random real answers into tests/fixtures/labelled/ for a person to label
"""
import argparse
import json
import os
import random
import sys
from datetime import date
from pathlib import Path

import httpx

from shelfsight.config import load_settings, load_workspace
from shelfsight.engines.gemini import GeminiEngine
from shelfsight.extract import extract_mentions
from shelfsight.store import Store

LABELLED = Path("tests/fixtures/labelled")
TARGET_RECALL = 0.95


def export(n: int, workspace: str, day: date, lake: str) -> int:
    rows = Store(lake).rows("SELECT response_id, response_text FROM raw_responses "
                            "WHERE workspace = ? AND run_date = ? AND status = 'ok'", [workspace, day])
    picked = random.sample(rows, min(n, len(rows)))
    LABELLED.mkdir(parents=True, exist_ok=True)
    for r in picked:
        case = {"workspace": workspace, "response_text": r["response_text"], "expected": []}
        (LABELLED / f"{r['response_id']}.json").write_text(json.dumps(case, indent=2, ensure_ascii=False),
                                                          encoding="utf-8")
    print(f"wrote {len(picked)} files to {LABELLED}. Fill each 'expected' with "
          '[{"brand_id": ..., "rank": ...}] using rank null for brands mentioned only in passing.')
    return 0


def score() -> int:
    s = load_settings()
    found = total = rank_ok = rank_total = 0
    with httpx.Client() as c:
        llm = GeminiEngine(os.environ["GEMINI_API_KEY"], s.extractor.model, c, min_interval_s=6.5)
        for f in sorted(LABELLED.glob("*.json")):
            case = json.loads(f.read_text(encoding="utf-8"))
            ws = load_workspace(case["workspace"])
            resp = {"response_id": f.stem, "workspace": ws.id, "run_date": None, "engine": "eval",
                    "mode": "eval", "prompt_id": f.stem, "response_text": case["response_text"]}
            got = {m["brand_id"]: m["rank"] for m in extract_mentions(resp, llm, ws.brands, "eval") if m["brand_id"]}
            for exp in case["expected"]:
                total += 1
                if exp["brand_id"] not in got:
                    print(f"  MISS {f.name}: {exp['brand_id']}")
                    continue
                found += 1
                if exp.get("rank") is not None:
                    rank_total += 1
                    rank_ok += got[exp["brand_id"]] == exp["rank"]
    recall = found / total if total else 0.0
    print(f"brand recall {found}/{total} = {recall:.1%} (target {TARGET_RECALL:.0%}); "
          f"rank accuracy {rank_ok}/{rank_total}")
    return 0 if recall >= TARGET_RECALL else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=int)
    ap.add_argument("--workspace")
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--lake", default="data/lake")
    a = ap.parse_args()
    sys.exit(export(a.export, a.workspace, a.date, a.lake) if a.export else score())

"""Evaluate reviewed labels against saved predictions, or explicitly collect new predictions.

Offline: python scripts/eval_extractor.py --predictions predictions.json --output evaluation.json
Live (billable; never part of tests): add --live instead of --predictions.
Export: --export 40 --workspace dotandkey --date YYYY-MM-DD --lake data/lake
Exports are PENDING, never silently treated as reviewed empty/negative labels.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import random

import httpx

from shelfsight.cli import build_llm
from shelfsight.config import load_settings, load_workspace
from shelfsight.evaluation import evaluate
from shelfsight.extract import extract_mentions
from shelfsight.store import Store

LABELLED = Path("tests/fixtures/labelled")


def export(n, workspace, day, lake):
    rows = Store(lake).rows("SELECT response_id, response_text FROM raw_responses WHERE workspace=? AND run_date=? AND status='ok'",
                            [workspace, day])
    picked = random.Random(0).sample(rows, min(n, len(rows)))
    for row in picked:
        path = LABELLED / (row["response_id"] + ".json")
        if path.exists():
            continue  # preserve human labels
        case = {"workspace": workspace, "response_text": row["response_text"], "label_status": "pending",
                "expected": [], "expected_citations": []}
        path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    print("Review every exported label: brand_id, is_recommended, rank (including null), and exact citation URLs.")


def score(predictions, live=False):
    settings = load_settings()
    cases = []
    with httpx.Client() as client:
        llm = build_llm(settings, client) if live else None
        if live and llm is None:
            raise ValueError("configured extractor credentials are unavailable")
        for path in sorted(LABELLED.glob("*.json")):
            case = json.loads(path.read_text(encoding="utf-8"))
            if case.get("label_status") not in ("reviewed", "reviewed_synthetic"):
                raise ValueError(f"{path}: pending or missing review status")
            if live:
                response = {"response_id": path.stem, "workspace": case["workspace"], "run_date": None,
                            "engine": "eval", "mode": "eval", "prompt_id": path.stem, "response_text": case["response_text"]}
                got = extract_mentions(response, llm, load_workspace(case["workspace"]).brands, settings.extractor.version)
                # This extractor takes answer text only. Citation parsing needs a separate saved provider fixture.
                prediction = {"mentions": got, "citations": []}
            else:
                prediction = predictions[path.stem]  # missing prediction must fail, not silently disappear
            cases.append(case | {"id": path.stem, "predicted": prediction["mentions"],
                                "predicted_citations": prediction["citations"]})
    if not cases:
        raise ValueError("no reviewed cases")
    return {"scope": "small reviewed set; not a calibrated production accuracy claim",
            "extractor_version": settings.extractor.version if live else "see saved prediction provenance",
            "label_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            "cases": cases, **evaluate(cases)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--export", type=int); p.add_argument("--workspace")
    p.add_argument("--date", type=date.fromisoformat); p.add_argument("--lake", default="data/lake")
    mode = p.add_mutually_exclusive_group(); mode.add_argument("--predictions", type=Path); mode.add_argument("--live", action="store_true")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if args.export:
        export(args.export, args.workspace, args.date, args.lake)
    else:
        if not (args.predictions or args.live) or not args.output:
            p.error("choose --predictions or --live, and an --output path to preserve the evaluation")
        if args.output.exists():
            p.error("output already exists; preserve earlier evaluations")
        result = score(json.loads(args.predictions.read_text()) if args.predictions else {}, args.live)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k:v for k,v in result.items() if k != "cases"}, indent=2))

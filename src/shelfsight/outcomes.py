"""Pre-specified, matched observation summaries. Descriptive differences are not causal estimates."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from shelfsight.interventions import Ledger, Proposal, digest
from shelfsight.store import Store
from shelfsight.urls import normalize_url


METRICS = ("visibility", "recommendation", "citation")


def assess(store, ledger, intervention_id):
    events = ledger.history(intervention_id)
    if not events or events[-1]["kind"] != "published_local":
        raise ValueError("a local publication record is required before assessment")
    proposal = Proposal.model_validate_json(events[0]["payload"])
    plan = proposal.plan
    raw = store.rows("SELECT * FROM raw_responses WHERE workspace=?", [proposal.workspace])
    funnel = store.rows("SELECT * FROM funnel WHERE workspace=? AND brand_id=? AND extractor_version=? AND joiner_version=?",
                        [proposal.workspace, proposal.brand_id, plan.extractor_version, plan.joiner_version])
    citations = store.rows("SELECT response_id, url FROM citations WHERE workspace=?", [proposal.workspace])
    by_response = {}
    for row in funnel:
        by_response.setdefault(row["response_id"], []).append(row)
    cited = {}
    for row in citations:
        cited.setdefault(row["response_id"], set()).add(normalize_url(row["url"]))
    cells, excluded = [], Counter()
    strata = sorted({str(r.get("model")) for r in raw if r.get("prompt_id") in {q.id for q in plan.targets + plan.comparisons}})
    for period, dates in (("baseline", plan.baseline_dates), ("post", plan.post_dates)):
        for group, questions in (("target", plan.targets), ("comparison", plan.comparisons)):
            for day in dates:
                for q in questions:
                    candidates = [r for r in raw if r["run_date"] == day and r["prompt_id"] == q.id and r["engine"] == plan.engine and r["mode"] == plan.mode]
                    # Exactly one planned observation per prompt/day. Never cherry-pick a successful retry.
                    cell = {"period": period, "group": group, "day": str(day), "prompt": q.id, "page": q.page,
                            "response_ids": [r["response_id"] for r in candidates], "values": {m: None for m in METRICS}}
                    reason = None
                    if not candidates:
                        reason = "missing_run"
                    elif len(candidates) != 1:
                        reason = "duplicate_sampling_cell"
                    else:
                        r = candidates[0]
                        cell["model"] = r["model"]
                        cell["raw_sha256"] = digest(json.dumps(r, default=str, sort_keys=True))
                        if r["status"] != "ok":
                            reason = "failed_or_skipped_run"
                        elif r["model"] != plan.model:
                            reason = "model_changed"
                        elif any(r.get(k) != value for k, value in {
                            "prompt_version": q.version, "prompt_text": q.text, "temperature": plan.temperature,
                            "system_prompt": plan.system_prompt, "collection_settings": plan.collection_settings}.items()):
                            reason = "settings_changed_or_unknown"
                        else:
                            # Citation = exact page URL reported by the provider, not inferred from brand naming.
                            cell["values"]["citation"] = normalize_url(q.page) in cited.get(r["response_id"], set()) if plan.mode == "web" else None
                            matches = by_response.get(r["response_id"], [])
                            if len(matches) != 1:
                                reason = "extraction_missing_or_duplicate"
                            else:
                                f = matches[0]
                                cell["values"]["visibility"] = f["is_named"]
                                cell["values"]["recommendation"] = False if f["is_named"] is False else f["is_recommended"]
                                cell["derived_sha256"] = digest(json.dumps(f, default=str, sort_keys=True))
                    if reason:
                        excluded[reason] += 1
                    cell["issue"] = reason
                    cells.append(cell)
    summaries, differences = {}, {}
    for metric in METRICS:
        summaries[metric] = {}
        for group in ("target", "comparison"):
            for period in ("baseline", "post"):
                subset = [c for c in cells if c["group"] == group and c["period"] == period]
                known = [c["values"][metric] for c in subset if c["values"][metric] is not None]
                summaries[metric][group + "_" + period] = {"expected": len(subset), "observed": len(known),
                    "missing": len(subset)-len(known), "successes": sum(known),
                    "rate": sum(known)/len(known) if known else None}
        s = summaries[metric]
        complete = all(v["observed"] == v["expected"] and v["expected"] > 0 for v in s.values())
        enough_days = min(len(plan.baseline_dates), len(plan.post_dates)) >= plan.minimum_days
        differences[metric] = ((s["target_post"]["rate"]-s["target_baseline"]["rate"]) -
                               (s["comparison_post"]["rate"]-s["comparison_baseline"]["rate"])) if complete and enough_days else None
    return {"intervention_id": proposal.id, "fixture": proposal.fixture,
            "assessed_at": datetime.now(timezone.utc).isoformat(), "analysis_version": "descriptive-1",
            "proposal_sha256": digest(events[0]["payload"]), "publication": events[-1],
            "plan": plan.model_dump(mode="json"), "models_seen": strata, "excluded_cells": dict(excluded),
            "summaries": summaries, "descriptive_difference_in_differences": differences,
            "causal_verdict": "inconclusive", "cells": cells,
            "limitations": ["Local artifact only: no brand website was changed.",
                "Non-randomized comparisons cannot establish causality; parallel trends and spillover are unverified.",
                "Differences are withheld for incomplete cells or insufficient repeated days; missing is never zero.",
                "Citation absence means no citation was reported, not proof of no retrieval or of training-data reliance.",
                "Pinned model IDs may conceal silent model changes; API answers are not consumer app observations."],
            "business_outcomes": {"referral_traffic": "not_measured", "purchases": "not_measured",
                                  "revenue_attribution": "not_measured"}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lake", required=True); p.add_argument("--ledger", required=True)
    p.add_argument("--id", required=True); p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if str(args.lake).startswith("s3://"):
        p.error("this local assessment interface accepts local lakes only")
    result = assess(Store(args.lake), Ledger(args.ledger), args.id)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(args.output)


if __name__ == "__main__":
    main()

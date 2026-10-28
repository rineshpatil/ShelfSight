"""Reproducible offline fixture: real collect/extract/funnel -> local ledger -> outcome assessment.

python scripts/intervention_example.py --output data/reliability-demo
The destination must not exist. No credentials, external calls or real product claims.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from shelfsight.collect import run_collect
from shelfsight.config import Brand, Workspace, load_settings
from shelfsight.engines.base import Citation, EngineResult
from shelfsight.extract import run_extract
from shelfsight.funnel import run_funnel
from shelfsight.interventions import Ledger
from shelfsight.outcomes import assess
from shelfsight.prompts import Prompt
from shelfsight.store import Store

PAGE = "https://fixture.example/sunscreen"
CONTROL = "https://fixture.example/control"


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    ws = Workspace(id="fixture", category="sunscreen", brands=[Brand(id="fixturebrand", name="Fixture Brand",
                    aliases=["Fixture Brand"], domains=["fixture.example"], is_client=True)])
    s = load_settings()
    cfg = s.engines[1].model_copy(update={"model": "fixture-model-v1", "modes": ["web"], "max_prompts": None})
    s = s.model_copy(update={"engines": [cfg]})
    pre = [date(2026, 9, 1)+timedelta(days=i) for i in range(7)]
    post = [date(2026, 9, 15)+timedelta(days=i) for i in range(7)]
    questions = [{"id": "target", "version": 1, "text": "Which sunscreen lists SPF 50?", "page": PAGE},
                 {"id": "comparison", "version": 1, "text": "Which control product lists SPF 50?", "page": CONTROL}]
    prompts = [Prompt(prompt_id=q["id"], prompt_text=q["text"], version=1, intent="discovery",
                      priority="high", active=True, persona="fixture", market="IN", owner="fixture",
                      last_reviewed=pre[0]) for q in questions]
    store = Store(output / "lake")
    class Probe:
        def search(self, query):
            return [{"url": PAGE, "source_engine": "fixture-index"}]
    class Engine:
        def ask(self, prompt, system, mode):
            target = prompt == questions[0]["text"]
            positive = (day in post) if target else day.day % 7 in (1, 2, 3)
            text = "Choose Fixture Brand first." if positive else "No brand recommendation is supported in this fixture answer."
            return EngineResult(text=text, model=cfg.model, web_search_queries=[prompt],
                citations=[Citation(PAGE if target else CONTROL, "Fixture", 1)] if positive else [],
                raw={"fixture": True, "answer": text})
    class Extractor:
        model = "fixture-extractor-v1"
        def generate_json(self, prompt, schema):
            positive = "Choose Fixture Brand first." in prompt
            return {"answer_type": "single" if positive else "refusal", "brands": [
                {"name": "Fixture Brand", "rank": 1, "is_recommended": True}] if positive else []}
    for day in pre + post:
        run_collect(settings=s, workspace=ws, prompts=prompts, engines={"gemini": Engine()}, probe=Probe(), store=store, run_date=day)
        run_extract(store=store, workspace=ws, llm=Extractor(), extractor_version=s.extractor.version, run_date=day)
        run_funnel(store=store, workspace=ws, settings=s, run_date=day)
    baseline = store.rows("SELECT * FROM raw_responses WHERE prompt_id='target' ORDER BY run_date")[0]
    payload = {"id": "fixture-spf-copy", "workspace": ws.id, "brand_id": ws.client.id, "product": "Synthetic sunscreen",
        "page": PAGE, "observed_issue": "The fixture baseline answer makes no brand recommendation; fixture page omits verified SPF wording.",
        "hypothesis": "Adding verified SPF wording may help answer the target question; this is untested.",
        "evidence": [
            {"id": "answer", "kind": "observation", "reference": baseline["response_id"], "snapshot": baseline["response_text"],
             "quote": baseline["response_text"], "captured_at": "2026-09-07T10:00:00Z"},
            {"id": "fact", "kind": "verified_brand", "reference": PAGE,
             "snapshot": "SYNTHETIC brand-approved fixture specification: SPF 50.", "quote": "SPF 50.",
             "verified_by": "fixture-brand-reviewer", "captured_at": "2026-09-07T10:00:00Z"}],
        "supporting_fact_ids": ["fact"], "before": "Synthetic sunscreen.", "after": "SPF 50.",
        "unknowns": ["ingredients", "oily-skin suitability", "water resistance", "product benefits beyond fixture SPF label"],
        "fixture": True, "plan": {"baseline_dates": [str(d) for d in pre], "post_dates": [str(d) for d in post],
            "targets": questions[:1], "comparisons": questions[1:], "engine": "gemini", "model": cfg.model,
            "mode": "web", "temperature": cfg.temperature, "system_prompt": s.system_prompt,
            "collection_settings": baseline["collection_settings"], "extractor_version": s.extractor.version,
            "joiner_version": s.joiner_version}}
    (output / "proposal.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    ledger = Ledger(output / "interventions.sqlite")
    for kind, hour in (("proposed", 10), ("approved", 11), ("published_local", 12)):
        ledger.transition(payload["id"], kind, "fixture-operator", payload if kind == "proposed" else None,
                          at=datetime(2026, 9, 8, hour, tzinfo=timezone.utc), store=store)
    result = assess(store, ledger, payload["id"])
    (output / "assessment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    summary = """# ShelfSight local intervention example — SYNTHETIC FIXTURE DATA

Observation: seven baseline answers omit Fixture Brand. An independent search finds its page;
that proxy does not explain the omissions. The retained fixture specification supports only SPF 50.

Proposed fix: replace `Synthetic sunscreen.` with `SPF 50.` on the synthetic page.
Ingredients, suitability and other benefits remain unknown. Adding copy may help; this is a hypothesis.

Approval and publication: named fixture actors, chronological timestamps and a SHA-256 of the exact
copy are retained in interventions.sqlite. Publication creates only a local ledger artifact.
No brand website was changed. This is a simulated timeline with deliberately constructed outcomes.

Measurement: seven baseline and seven post days, one observation per question/day, one target and one
comparison question on separate pages, same model, prompt versions and collection settings.
Target visibility, recommendation and reported citation rates move 0/7 to 7/7; comparison rates remain
3/7 to 3/7. The descriptive difference-in-differences is +1.0 for each measure.

Causal assessment: INCONCLUSIVE. Synthetic movement demonstrates the calculation, not effectiveness.
Even real non-randomized before/after differences would not prove causality. Missing observations,
model/settings changes or duplicate cells withhold the affected difference. Referral traffic,
purchases and revenue attribution are NOT MEASURED.

Inspect proposal.json, assessment.json (including every cell and provenance), interventions.sqlite,
and lake/ for the complete chain. Re-run the script with a new output directory; semantic results
are reproducible while run IDs, file IDs and collection timestamps are newly generated.
"""
    (output / "README.md").write_text(summary, encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build(args.output)
    print(Path(args.output).resolve())

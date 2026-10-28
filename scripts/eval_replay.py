"""Offline synthetic contract checks, NOT a measurement of live model accuracy.

python scripts/eval_replay.py --output evaluation.json
For real accuracy use reviewed labels and saved predictions with evaluation.evaluate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import httpx

from shelfsight.config import load_settings, load_workspace
from shelfsight.evaluation import evaluate
from shelfsight.extract import extract_mentions
from shelfsight.engines.gemini import GeminiEngine


def replay():
    path = Path("tests/fixtures/extractor_replay.json")
    cases = json.loads(path.read_text())
    # Explicit synthetic model replies for the two original labelled examples.
    original_outputs = {
        "example-001": {"answer_type": "list", "brands": [
            {"name": "Minimalist", "rank": 1, "is_recommended": True},
            {"name": "Re'equil", "rank": 2, "is_recommended": True},
            {"name": "Dot & Key", "rank": 3, "is_recommended": True},
            {"name": "Neutrogena", "rank": None, "is_recommended": False}]},
        "example-002": {"answer_type": "list", "brands": [
            {"name": "The Derma Co", "rank": 1, "is_recommended": True},
            {"name": "Lakme", "rank": 2, "is_recommended": True}]},
    }
    for name, output in original_outputs.items():
        original = json.loads((Path("tests/fixtures/labelled") / (name + ".json")).read_text(encoding="utf-8"))
        cases.append(original | {"id": name, "output": output, "predicted_citations": []})
    settings = load_settings()
    for case in cases:
        class FixtureModel:
            model = "synthetic-output-v1"

            def generate_json(self, prompt, schema):
                return case["output"]
        response = dict(response_id=case["id"], workspace=case["workspace"], run_date=None,
                        engine="fixture", mode="no_web", prompt_id=case["id"], response_text=case["response_text"])
        case["predicted"] = extract_mentions(response, FixtureModel(), load_workspace(case["workspace"]).brands,
                                              settings.extractor.version)
    provider_fixture = Path("tests/fixtures/gemini_grounded.json")
    provider = json.loads(provider_fixture.read_text())
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
    def transport(request):
        if request.method == "POST":
            return httpx.Response(200, json=provider)
        if str(request.url) == redirect + "AAA":
            return httpx.Response(302, headers={"location": "https://www.nykaa.com/dot-key-sunscreen/p/1"})
        raise httpx.ConnectError("fixture unresolved redirect")
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = GeminiEngine("fixture", "fixture-model", client).ask("fixture question", "fixture", "web")
    citation_case = {"id": "provider-citations", "expected": [], "predicted": [],
        "expected_citations": ["https://www.nykaa.com/dot-key-sunscreen/p/1", redirect + "BBB"],
        "predicted_citations": [c.url for c in result.citations]}
    return {"scope": "synthetic offline reconciliation regression; live extractor accuracy unmeasured",
            "extractor_version": settings.extractor.version,
            "fixture_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            "cases": cases, **evaluate(cases),
            "citation_parser": {"fixture_sha256": hashlib.sha256(provider_fixture.read_bytes()).hexdigest(),
                "case": citation_case, "metrics": evaluate([citation_case])["metrics"]["citations"],
                "limitation": "two returned URLs; unresolved URL retained, destination unknown; not citation faithfulness"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay()
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)

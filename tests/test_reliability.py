import json
import runpy
from datetime import date, datetime, timezone

import duckdb
import pytest

from shelfsight.evaluation import evaluate
from shelfsight.interventions import Ledger, Proposal
from shelfsight.outcomes import assess
from shelfsight.store import Store


def test_evaluator_penalizes_false_positives_missed_ranks_and_citations():
    result = evaluate([dict(id="negative", expected=[dict(brand_id="a", rank=1, is_recommended=True)],
        predicted=[dict(brand_id="wrong", rank=1, is_recommended=True)],
        expected_citations=["https://a.test/page"], predicted_citations=["https://a.test/"])])
    assert result["metrics"]["mentions"] == dict(tp=0, fp=1, fn=1, precision=0, recall=0)
    assert result["metrics"]["recommendations"]["recall"] == 0
    assert result["metrics"]["citations"]["fp"] == 1
    assert result["rank"] == dict(correct=0, total=1, accuracy=0)


def test_synthetic_replay_exposes_unknown_instead_of_counting_it_as_false():
    result = runpy.run_path("scripts/eval_replay.py")["replay"]()
    assert result["metrics"]["mentions"]["precision"] == 1
    assert result["recommendation_unknown"] == 1
    assert any(e["kind"] == "recommendation_unknown" for e in result["errors"])
    assert result["metrics"]["citations"]["recall"] is None  # no citation labels in this slice


def test_old_parquet_gets_typed_unknown_columns(tmp_path):
    path = tmp_path / "raw_responses" / "old.parquet"
    path.parent.mkdir()
    with duckdb.connect() as db:
        db.execute("COPY (SELECT 'old' AS response_id) TO ? (FORMAT parquet)", [str(path)])
    assert Store(tmp_path).rows("SELECT response_id, probe_status, prompt_text FROM raw_responses") == [
        dict(response_id="old", probe_status=None, prompt_text=None)]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    output = tmp_path_factory.mktemp("intervention") / "demo"
    result = runpy.run_path("scripts/intervention_example.py")["build"](output)
    return output, result


def test_complete_workflow_has_separate_metrics_and_no_causal_claim(demo):
    output, result = demo
    assert result["descriptive_difference_in_differences"] == dict(visibility=1, recommendation=1, citation=1)
    assert result["causal_verdict"] == "inconclusive"
    assert result["business_outcomes"]["purchases"] == "not_measured"
    assert result["excluded_cells"] == {}
    history = Ledger(output / "interventions.sqlite").history("fixture-spf-copy")
    assert [r["kind"] for r in history] == ["proposed", "approved", "published_local"]
    assert json.loads(history[-1]["payload"])["external_publication"] is False
    assert "SPF 50." == json.loads(history[-1]["payload"])["content"]


def test_unsupported_claims_and_wrong_page_are_blocked(demo):
    payload = json.loads((demo[0] / "proposal.json").read_text())
    with pytest.raises(ValueError, match="unsupported prose"):
        Proposal.model_validate(payload | {"after": "Oil-free and safe for acne-prone skin."})
    payload["evidence"][1]["reference"] = "https://other.example/"
    with pytest.raises(ValueError, match="exact product page"):
        Proposal.model_validate(payload)


def test_approval_required_and_duplicate_publication_blocked(demo, tmp_path):
    payload = json.loads((demo[0] / "proposal.json").read_text())
    ledger = Ledger(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError, match="transition"):
        ledger.transition(payload["id"], "published_local", "operator")
    with pytest.raises(ValueError, match="duplicate transition"):
        Ledger(demo[0] / "interventions.sqlite").transition(payload["id"], "approved", "operator")


def test_proposal_must_reference_real_target_observation(demo, tmp_path):
    payload = json.loads((demo[0] / "proposal.json").read_text())
    payload["evidence"][0]["reference"] = "invented-response-id"
    with pytest.raises(ValueError, match="stored response snapshot"):
        Ledger(tmp_path / "ledger.sqlite").transition(payload["id"], "proposed", "operator", payload,
            at=datetime(2026, 9, 8, 10, tzinfo=timezone.utc), store=Store(demo[0] / "lake"))


def test_too_few_days_withholds_descriptive_difference(demo):
    output, _ = demo
    real = Ledger(output / "interventions.sqlite")
    class ShortPlan:
        def history(self, intervention_id):
            events = real.history(intervention_id)
            proposal = json.loads(events[0]["payload"])
            proposal["plan"]["minimum_days"] = 8
            events[0]["payload"] = json.dumps(proposal)
            return events
    result = assess(Store(output / "lake"), ShortPlan(), "fixture-spf-copy")
    assert all(value is None for value in result["descriptive_difference_in_differences"].values())


@pytest.mark.parametrize("fault", ["missing", "failed", "model", "settings", "duplicate", "extraction"])
def test_incomplete_or_incompatible_runs_withhold_differences(demo, fault):
    output, _ = demo
    real = Store(output / "lake")
    class FaultStore:
        def rows(self, sql, args):
            rows = real.rows(sql, args)
            if "raw_responses" in sql:
                index = next(i for i, r in enumerate(rows) if r["prompt_id"] == "target" and r["run_date"] == date(2026, 9, 15))
                row = rows[index]
                if fault == "missing": rows.pop(index)
                if fault == "failed": row["status"] = "error"
                if fault == "model": row["model"] = "new-model"
                if fault == "settings": row["temperature"] = None
                if fault == "duplicate": rows.append(row.copy())
            if "FROM funnel" in sql and fault == "extraction":
                rows = [r for r in rows if not (r["prompt_id"] == "target" and r["run_date"] == date(2026, 9, 15))]
            return rows
    result = assess(FaultStore(), Ledger(output / "interventions.sqlite"), "fixture-spf-copy")
    assert result["descriptive_difference_in_differences"]["visibility"] is None
    assert result["summaries"]["visibility"]["target_post"]["missing"] == 1
    assert sum(result["excluded_cells"].values()) == 1
    assert result["causal_verdict"] == "inconclusive"

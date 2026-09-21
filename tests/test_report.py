from datetime import date

from shelfsight.config import load_settings, load_workspace
from shelfsight.report import diagnosis_summary
from shelfsight.store import Store

D = date(2026, 9, 21)


def test_summary_groups_client_rows_by_diagnosis(tmp_path):
    s, ws, store = load_settings(), load_workspace("dotandkey"), Store(tmp_path)
    base = dict(workspace="dotandkey", run_date=D, engine="gemini", mode="web",
                extractor_version=s.extractor.version, joiner_version=s.joiner_version)
    store.write("funnel", [
        base | dict(prompt_id="P2", brand_id="dotandkey", is_client=True, diagnosis="not_eligible"),
        base | dict(prompt_id="P1", brand_id="dotandkey", is_client=True, diagnosis="not_eligible"),
        base | dict(prompt_id="P3", brand_id="dotandkey", is_client=True, diagnosis="winning"),
        base | dict(prompt_id="P1", brand_id="minimalist", is_client=False, diagnosis="winning"),
    ])
    md = diagnosis_summary(store, ws, s, D)
    assert "3 answers diagnosed for Dot & Key." in md
    table = [line for line in md.splitlines() if line.startswith(("| not_eligible", "| winning"))]
    assert table == [
        "| not_eligible | 2 | Not in the candidate set: content gap, no page for this question | P1, P2 |",
        "| winning | 1 | Recommended in the top 3 | P3 |",
    ]


def test_summary_when_empty(tmp_path):
    assert "No funnel rows" in diagnosis_summary(Store(tmp_path), load_workspace("dotandkey"), load_settings(), D)

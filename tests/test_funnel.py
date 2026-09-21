from datetime import date

import pytest

from shelfsight.config import load_settings, load_workspace
from shelfsight.funnel import diagnose, run_funnel
from shelfsight.store import Store

D = date(2026, 9, 21)
WS = "dotandkey"


@pytest.mark.parametrize("kw, expected", [
    (dict(is_recommended=True, rank=2), "winning"),
    (dict(is_recommended=True, rank=5), "recommended_not_top3"),
    (dict(is_recommended=True, rank=None), "recommended_not_top3"),
    (dict(is_named=True), "named_not_recommended"),
    (dict(retrieval=False), "not_named"),
    (dict(is_cited=True), "cited_not_named"),
    (dict(probed=False), "probe_missing"),
    (dict(is_eligible=True), "eligible_not_cited"),
    (dict(), "not_eligible"),
])
def test_diagnosis_ladder(kw, expected):
    base = dict(retrieval=True, probed=True, is_eligible=False, is_cited=False, is_named=False,
                is_recommended=None, rank=None)
    assert diagnose(**(base | kw)) == expected


def resp(rid, mode, queries=None):
    return dict(response_id=rid, run_id="run", workspace=WS, run_date=D, prompt_id=f"P-{rid}", prompt_version=1,
                intent="discovery", priority="high", engine="gemini", model="g", mode=mode, sample_n=1,
                status="ok", response_text="...", web_search_queries=queries)


def cand(rid, brand, position):
    host = f"{brand or 'other'}.test"
    return dict(response_id=rid, workspace=WS, run_date=D, engine="gemini", prompt_id=f"P-{rid}",
                search_query="q", query_index=0, position=position, url=f"https://{host}/", domain=host,
                domain_type="other", source_engine="google", brand_id=brand)


def cite(rid, brand):
    return dict(response_id=rid, workspace=WS, run_date=D, engine="gemini", mode="web", prompt_id=f"P-{rid}",
                position=1, url=f"https://{brand}.test/", domain=f"{brand}.test", domain_type="brand", brand_id=brand)


def ment(rid, brand, rank=None, rec=None, mode="web"):
    return dict(response_id=rid, workspace=WS, run_date=D, engine="gemini", mode=mode, prompt_id=f"P-{rid}",
                brand_id=brand, brand_raw=brand, rank=rank, is_recommended=rec, claims=[],
                source="none" if brand is None else "llm", extractor_version="ext-1")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    s.write("raw_responses", [resp("W1", "web", ["q"]),   # probed, client eligible at position 4
                              resp("W2", "web", ["q"]),   # searched, but the probe failed
                              resp("W3", "web", ["q"]),   # not extracted yet
                              resp("W4", "web", []),      # web mode, but the model didn't search
                              resp("N1", "no_web")])
    s.write("candidates", [cand("W1", "minimalist", 1), cand("W1", None, 2), cand("W1", "dotandkey", 4)])
    s.write("citations", [cite("W1", "minimalist")])
    s.write("mentions", [ment("W1", "minimalist", 1, True), ment("W2", None), ment("W4", None),
                         ment("N1", "dotandkey", 5, True, mode="no_web")])
    return s


def test_run_funnel_diagnoses_each_response_and_brand(store):
    out = run_funnel(store=store, workspace=load_workspace(WS), settings=load_settings(), run_date=D)
    assert (out["status"], out["rows"]) == ("ok", 4 * 9)  # W3 isn't extracted yet, so it's skipped
    assert out["client"] == {"eligible_not_cited": 1, "probe_missing": 1, "not_named": 1,
                             "recommended_not_top3": 1}
    rows = store.rows("SELECT response_id, brand_id, diagnosis, is_eligible, is_cited, eligible_n FROM funnel "
                      "WHERE brand_id IN ('dotandkey', 'minimalist') ORDER BY response_id, brand_id")
    assert [(r["response_id"], r["brand_id"], r["diagnosis"]) for r in rows] == [
        ("N1", "dotandkey", "recommended_not_top3"), ("N1", "minimalist", "not_named"),
        ("W1", "dotandkey", "eligible_not_cited"), ("W1", "minimalist", "winning"),
        ("W2", "dotandkey", "probe_missing"), ("W2", "minimalist", "probe_missing"),
        ("W4", "dotandkey", "not_named"), ("W4", "minimalist", "not_named"),
    ]
    n1, _, w1_dk, _, w2_dk, _, w4_dk, _ = rows
    assert (n1["is_eligible"], n1["is_cited"]) == (None, None)
    assert (w1_dk["is_eligible"], w1_dk["is_cited"], w1_dk["eligible_n"]) == (True, False, 1)
    assert (w2_dk["is_eligible"], w2_dk["is_cited"]) == (None, False)
    assert (w4_dk["is_eligible"], w4_dk["is_cited"]) == (None, None)


def test_run_funnel_is_idempotent_per_version(store):
    ws, s = load_workspace(WS), load_settings()
    run_funnel(store=store, workspace=ws, settings=s, run_date=D)
    assert run_funnel(store=store, workspace=ws, settings=s, run_date=D)["status"] == "skipped"
    assert store.rows("SELECT count(*) AS n FROM funnel") == [{"n": 36}]
    bumped = s.model_copy(update={"joiner_version": "join-2"})
    assert run_funnel(store=store, workspace=ws, settings=bumped, run_date=D)["rows"] == 36

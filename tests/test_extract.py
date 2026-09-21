import json
from datetime import date
from pathlib import Path

from shelfsight.brand_match import dictionary_hits
from shelfsight.config import load_workspace
from shelfsight.engines.base import RetryableError
from shelfsight.extract import Extraction, extract_mentions, reconcile, run_extract
from shelfsight.store import Store

D = date(2026, 9, 21)

LLM_OUT = {"answer_type": "list", "brands": [
    {"name": "Minimalist SPF 50", "rank": 1, "is_recommended": True, "sentiment": "positive", "claims": ["no white cast"]},
    {"name": "Re'equil Oil Control", "rank": 2, "is_recommended": True, "sentiment": "positive", "claims": ["matte finish"]},
    {"name": "Dot & Key Watermelon", "rank": 3, "is_recommended": True, "sentiment": "neutral", "claims": ["gel texture"]},
    {"name": "Dot & Key Vitamin C", "rank": 4, "is_recommended": False, "sentiment": "neutral", "claims": []},
    {"name": "Foxtale", "rank": None, "is_recommended": False, "sentiment": "neutral", "claims": []},
]}


def answer():
    data = json.loads(Path("tests/fixtures/gemini_grounded.json").read_text(encoding="utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def response(text=None, rid="r1"):
    return {"response_id": rid, "workspace": "dotandkey", "run_date": D, "engine": "gemini", "mode": "web",
            "prompt_id": "P1", "response_text": answer() if text is None else text}


class FakeLLM:
    def __init__(self, *outs):
        self.outs, self.prompts = list(outs), []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        out = self.outs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def test_reconcile_merges_llm_and_dictionary():
    ws = load_workspace("dotandkey")
    resp = response()
    hits = dictionary_hits(resp["response_text"], ws.brands)
    rows = reconcile(resp, Extraction.model_validate(LLM_OUT), hits, ws.brands, "ext-1")
    assert [(r["brand_id"], r["brand_raw"], r["source"]) for r in rows] == [
        ("minimalist", "Minimalist SPF 50", "both"),
        ("reequil", "Re'equil Oil Control", "both"),
        ("dotandkey", "Dot & Key Watermelon", "both"),  # the second Dot & Key product is dropped
        (None, "Foxtale", "llm"),                        # unknown brand kept: early warning on new competitors
        ("neutrogena", "neutrogena", "dictionary"),      # the LLM missed it, the dictionary didn't
    ]
    assert rows[4]["is_recommended"] is None and rows[4]["rank"] is None
    assert all(r["extractor_version"] == "ext-1" for r in rows)


def test_ambiguous_dictionary_only_hit_is_dropped_and_sentinel_written():
    ws = load_workspace("dotandkey")
    resp = response("a minimalist routine works best")
    hits = dictionary_hits(resp["response_text"], ws.brands)
    rows = reconcile(resp, Extraction(brands=[], answer_type="other"), hits, ws.brands, "ext-1")
    assert [(r["brand_id"], r["source"]) for r in rows] == [(None, "none")]


def test_extract_mentions_prompts_with_known_brands_and_answer():
    llm = FakeLLM(LLM_OUT)
    extract_mentions(response(), llm, load_workspace("dotandkey").brands, "ext-1")
    assert "Dot & Key, Minimalist" in llm.prompts[0]
    assert "Re'equil Oil Control Sunscreen" in llm.prompts[0]


def test_run_extract_is_idempotent_and_survives_bad_output(tmp_path):
    ws, store = load_workspace("dotandkey"), Store(tmp_path)
    base = dict(run_id="run", workspace="dotandkey", run_date=D, engine="gemini", mode="web", prompt_id="P1", status="ok")
    store.write("raw_responses", [base | {"response_id": "a", "response_text": answer()},
                                  base | {"response_id": "b", "response_text": "hmm"},
                                  base | {"response_id": "c", "status": "error"}])
    first = run_extract(store=store, workspace=ws, llm=FakeLLM(LLM_OUT, {"not": "valid"}),
                        extractor_version="ext-1", run_date=D)
    assert (first["todo"], first["extracted"], first["failed"]) == (2, 1, 1)
    second = run_extract(store=store, workspace=ws, llm=FakeLLM({"brands": [], "answer_type": "other"}),
                         extractor_version="ext-1", run_date=D)
    assert (second["todo"], second["extracted"]) == (1, 1)  # only "b" is retried
    assert store.rows("SELECT source FROM mentions WHERE response_id = 'b'") == [{"source": "none"}]


def test_run_extract_stops_when_quota_is_gone(tmp_path):
    ws, store = load_workspace("dotandkey"), Store(tmp_path)
    base = dict(run_id="run", workspace="dotandkey", run_date=D, engine="gemini", mode="web", prompt_id="P1",
                status="ok", response_text="x")
    store.write("raw_responses", [base | {"response_id": "a"}, base | {"response_id": "b"}])
    llm = FakeLLM(RetryableError(429, "quota"))
    out = run_extract(store=store, workspace=ws, llm=llm, extractor_version="ext-1", run_date=D)
    assert (out["extracted"], out["failed"], len(llm.prompts)) == (0, 1, 1)

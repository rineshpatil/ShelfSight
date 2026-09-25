from datetime import date

import pytest

from shelfsight.collect import run_collect
from shelfsight.config import EngineCfg, load_settings, load_workspace
from shelfsight.engines.base import Citation, EngineResult, RetryableError
from shelfsight.prompts import Prompt
from shelfsight.store import Store

D = date(2026, 9, 21)


def prompt(pid, priority="high"):
    return Prompt(prompt_id=pid, prompt_text=f"question {pid}", intent="discovery", persona="", market="IN",
                  priority=priority, version=1, active=True, owner="R", last_reviewed=D)


class FakeEngine:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def ask(self, prompt, system_prompt, mode):
        self.calls.append((prompt, mode))
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class FakeProbe:
    def __init__(self, fail=False):
        self.fail = fail

    def search(self, query):
        if self.fail:
            raise RuntimeError("searx down")
        return [{"url": "https://www.dotandkey.com/p", "source_engine": "google"},
                {"url": "https://nykaa.com/x", "source_engine": "bing"}]


WEB = EngineResult(text="Try Minimalist", model="g", web_search_queries=["q"],
                   citations=[Citation(url="https://www.dotandkey.com/p?utm_source=x", title="", position=1)],
                   input_tokens=10, output_tokens=20)
NO_WEB = EngineResult(text="Try Lakme", model="g", input_tokens=5, output_tokens=5)


def settings(modes):
    engine = EngineCfg(name="gemini", model="g", modes=list(modes), min_interval_s=0)
    return load_settings().model_copy(update={"engines": [engine]})


def run(tmp_path, engine, prompts, modes=("web", "no_web"), probe=None):
    store = Store(tmp_path)
    r = run_collect(settings=settings(modes), workspace=load_workspace("dotandkey"), prompts=prompts,
                    engines={"gemini": engine}, probe=probe or FakeProbe(), store=store, run_date=D)
    return r, store


def test_happy_path_writes_every_table(tmp_path):
    run_row, store = run(tmp_path, FakeEngine(WEB, NO_WEB), [prompt("P1")])
    assert (run_row["status"], run_row["coverage"], run_row["planned_calls"]) == ("finished", 1.0, 2)
    assert store.rows("SELECT mode, status, web_search_queries, intent FROM raw_responses ORDER BY mode") == [
        {"mode": "no_web", "status": "ok", "web_search_queries": [], "intent": "discovery"},
        {"mode": "web", "status": "ok", "web_search_queries": ["q"], "intent": "discovery"},
    ]
    assert store.rows("SELECT url, brand_id, domain_type FROM citations") == [
        {"url": "https://dotandkey.com/p", "brand_id": "dotandkey", "domain_type": "brand"}]
    assert store.rows("SELECT position, brand_id FROM candidates ORDER BY position") == [
        {"position": 1, "brand_id": "dotandkey"}, {"position": 2, "brand_id": None}]
    assert store.rows("SELECT input_tokens, output_tokens FROM runs") == [{"input_tokens": 15, "output_tokens": 25}]


def test_persistent_429_exhausts_engine_and_skips_the_rest(tmp_path):
    engine = FakeEngine(NO_WEB, RetryableError(429, "quota"))
    run_row, store = run(tmp_path, engine, [prompt("P1"), prompt("P2"), prompt("P3")], modes=["no_web"])
    assert len(engine.calls) == 2
    statuses = [r["status"] for r in store.rows("SELECT status FROM raw_responses ORDER BY prompt_id")]
    assert statuses == ["ok", "error", "skipped_quota"]
    assert (run_row["status"], run_row["quota_exhausted"], run_row["coverage"]) == ("partial", ["gemini"], 1 / 3)


def test_5xx_error_does_not_exhaust_engine(tmp_path):
    run_row, _ = run(tmp_path, FakeEngine(RetryableError(503, "down"), NO_WEB), [prompt("P1"), prompt("P2")],
                     modes=["no_web"])
    assert (run_row["ok_calls"], run_row["error_calls"], run_row["quota_exhausted"]) == (1, 1, [])


def test_probe_failure_keeps_the_answer(tmp_path):
    run_row, store = run(tmp_path, FakeEngine(WEB), [prompt("P1")], modes=["web"], probe=FakeProbe(fail=True))
    assert (run_row["status"], run_row["probe_errors"]) == ("finished", 1)
    assert store.rows("SELECT count(*) AS n FROM candidates") == [{"n": 0}]
    assert store.rows("SELECT status FROM raw_responses") == [{"status": "ok"}]


def test_max_prompts_caps_an_engine_to_the_top_prompts(tmp_path):
    engine = EngineCfg(name="gemini", model="g", modes=["no_web"], min_interval_s=0, max_prompts=2)
    s = load_settings().model_copy(update={"engines": [engine]})
    fake = FakeEngine(NO_WEB, NO_WEB)
    run_row = run_collect(settings=s, workspace=load_workspace("dotandkey"),
                          prompts=[prompt("P1"), prompt("P2"), prompt("P3")], engines={"gemini": fake},
                          probe=FakeProbe(), store=Store(tmp_path), run_date=D)
    assert [c[0] for c in fake.calls] == ["question P1", "question P2"]
    assert (run_row["planned_calls"], run_row["coverage"], run_row["status"]) == (2, 1.0, "finished")


def test_interrupted_run_still_persists_collected_answers(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        run_collect(settings=settings(["no_web"]), workspace=load_workspace("dotandkey"),
                    prompts=[prompt("P1"), prompt("P2")], engines={"gemini": FakeEngine(NO_WEB, KeyboardInterrupt())},
                    probe=FakeProbe(), store=store, run_date=D)
    assert store.rows("SELECT prompt_id FROM raw_responses") == [{"prompt_id": "P1"}]
    assert store.rows("SELECT count(*) AS n FROM runs") == [{"n": 0}]

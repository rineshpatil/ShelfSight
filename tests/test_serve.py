import threading
import time

import httpx
import pytest

from shelfsight.serve import alerts_for, make_server

TOKEN = "t0ken"


@pytest.fixture
def bridge():
    calls = []

    def pipeline(workspace, limit):
        calls.append((workspace, limit))
        if workspace == "slow":
            time.sleep(0.5)
        return {"ok": True, "workspace": workspace}

    def report(workspace, day):
        return f"# report {workspace} {day}"

    server = make_server(TOKEN, pipeline=pipeline, report=report, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()
    server.server_close()


def auth(token=TOKEN):
    return {"X-ShelfSight-Token": token}


def test_health_needs_no_token(bridge):
    url, _ = bridge
    r = httpx.get(f"{url}/health")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})


def test_run_rejects_missing_or_wrong_token(bridge):
    url, calls = bridge
    assert httpx.post(f"{url}/run", json={"workspace": "dotandkey"}).status_code == 401
    assert httpx.post(f"{url}/run", json={"workspace": "dotandkey"}, headers=auth("nope")).status_code == 401
    assert calls == []


def test_run_calls_the_pipeline(bridge):
    url, calls = bridge
    r = httpx.post(f"{url}/run", json={"workspace": "dotandkey", "limit": 3}, headers=auth())
    assert (r.status_code, r.json()) == (200, {"ok": True, "workspace": "dotandkey"})
    assert calls == [("dotandkey", 3)]


@pytest.mark.parametrize("body", [{"workspace": "../etc"}, {"workspace": ""}, {}, {"workspace": "x", "limit": "3"}])
def test_run_rejects_bad_input(bridge, body):
    url, calls = bridge
    assert httpx.post(f"{url}/run", json=body, headers=auth()).status_code == 400
    assert calls == []


def test_only_one_run_at_a_time(bridge):
    url, _ = bridge
    first = threading.Thread(target=lambda: httpx.post(f"{url}/run", json={"workspace": "slow"}, headers=auth()))
    first.start()
    time.sleep(0.1)
    assert httpx.post(f"{url}/run", json={"workspace": "dotandkey"}, headers=auth()).status_code == 409
    first.join()


def test_report_returns_markdown(bridge):
    url, _ = bridge
    r = httpx.get(f"{url}/report", params={"workspace": "dotandkey", "date": "2026-09-22"}, headers=auth())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text == "# report dotandkey 2026-09-22"


def test_report_rejects_a_bad_date(bridge):
    url, _ = bridge
    r = httpx.get(f"{url}/report", params={"workspace": "dotandkey", "date": "yesterday"}, headers=auth())
    assert r.status_code == 400


def test_alerts_for_a_clean_run_is_empty():
    run = {"status": "finished", "coverage": 1.0, "probe_errors": 0, "quota_exhausted": []}
    assert alerts_for(run, {"failed": 0}) == []


def test_alerts_for_a_degraded_run():
    run = {"status": "partial", "coverage": 0.5, "probe_errors": 2, "quota_exhausted": ["gemini"]}
    assert alerts_for(run, {"failed": 1}) == [
        "collection status is partial",
        "coverage 50% is below 95%",
        "2 retrieval probes failed; affected answers are diagnosed probe_missing",
        "quota exhausted: gemini",
        "1 answers failed extraction; the next run retries them",
    ]

import pytest
from pydantic import ValidationError

from shelfsight.config import EngineCfg, Workspace, load_settings, load_workspace


def test_repo_config_loads():
    s = load_settings()
    assert {e.name for e in s.engines} == {"gemini", "groq", "nova"}
    assert s.eligible_top_n == 10
    assert (s.extractor.engine, s.extractor.region) == ("nova", "us-east-1")
    ws = load_workspace("dotandkey")
    assert ws.client.id == "dotandkey"
    assert len(ws.brands) == 9


def test_workspace_requires_exactly_one_client():
    with pytest.raises(ValidationError, match="exactly one is_client"):
        Workspace.model_validate({"id": "x", "category": "c", "brands": [{"id": "a", "name": "A"}]})


def test_workspace_rejects_duplicate_brand_ids():
    brands = [{"id": "a", "name": "A", "is_client": True}, {"id": "a", "name": "B"}]
    with pytest.raises(ValidationError, match="duplicate brand ids"):
        Workspace.model_validate({"id": "x", "category": "c", "brands": brands})


def test_searxng_url_can_be_overridden_by_env(monkeypatch):
    # The image bakes in localhost:8888 for laptops; on the EC2 host SearxNG is another container.
    assert load_settings().searxng.url == "http://localhost:8888"
    monkeypatch.setenv("SHELFSIGHT_SEARXNG_URL", "http://searxng:8080")
    assert load_settings().searxng.url == "http://searxng:8080"


def test_searxng_language_defaults_to_india():
    # The probe must see what an Indian buyer sees, wherever the host runs.
    assert load_settings().searxng.language == "en-IN"


def test_nova_can_run_web_mode_and_needs_a_region():
    cfg = EngineCfg.model_validate({"name": "nova", "model": "m", "modes": ["web", "no_web"],
                                    "min_interval_s": 1, "region": "us-east-1"})
    assert cfg.region == "us-east-1"
    with pytest.raises(ValidationError, match="needs a region"):
        EngineCfg.model_validate({"name": "nova", "model": "m", "modes": ["web"], "min_interval_s": 1})


def test_groq_cannot_run_web_mode():
    with pytest.raises(ValidationError, match="no web search"):
        EngineCfg.model_validate({"name": "groq", "model": "m", "modes": ["web"], "min_interval_s": 1})

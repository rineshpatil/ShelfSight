import pytest
from pydantic import ValidationError

from shelfsight.config import EngineCfg, Workspace, load_settings, load_workspace


def test_repo_config_loads():
    s = load_settings()
    assert {e.name for e in s.engines} == {"gemini", "groq"}
    assert s.eligible_top_n == 10
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


def test_groq_cannot_run_web_mode():
    with pytest.raises(ValidationError, match="no web search"):
        EngineCfg.model_validate({"name": "groq", "model": "m", "modes": ["web"], "min_interval_s": 1})

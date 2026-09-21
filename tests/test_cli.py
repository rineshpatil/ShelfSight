from pathlib import Path

import httpx
import yaml

from shelfsight import cli
from shelfsight.cli import build_engines, build_llm, main
from shelfsight.config import load_settings
from shelfsight.engines.gemini import GeminiEngine
from shelfsight.engines.nova import NovaEngine


def config_without_nova(tmp_path: Path) -> str:
    raw = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    raw["engines"] = [e for e in raw["engines"] if e["name"] != "nova"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def test_validate_prompts_passes_on_seed():
    assert main(["validate-prompts", "--workspace", "dotandkey"]) == 0


def test_collect_without_keys_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    args = ["--config", config_without_nova(tmp_path), "--lake", str(tmp_path), "collect", "--workspace", "dotandkey"]
    assert main(args) == 2
    assert "no engine is available" in capsys.readouterr().err


def test_nova_is_built_from_aws_credentials_not_an_env_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    regions = []
    monkeypatch.setattr(cli, "bedrock_client", lambda region: regions.append(region) or object())
    with httpx.Client() as c:
        engines = build_engines(load_settings(), c)
    assert list(engines) == ["nova"]
    assert isinstance(engines["nova"], NovaEngine) and regions == ["us-east-1"]


def test_build_llm_follows_extractor_engine(monkeypatch):
    monkeypatch.setattr(cli, "bedrock_client", lambda region: object())
    s = load_settings()
    with httpx.Client() as c:
        assert isinstance(build_llm(s, c), NovaEngine)
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        gem = s.model_copy(update={"extractor": s.extractor.model_copy(update={"engine": "gemini", "model": "g"})})
        assert isinstance(build_llm(gem, c), GeminiEngine)


def test_report_on_empty_lake(tmp_path, capsys):
    assert main(["--lake", str(tmp_path), "report", "--workspace", "dotandkey", "--date", "2026-09-21"]) == 0
    assert "No funnel rows" in capsys.readouterr().out


def test_funnel_on_empty_lake(tmp_path, capsys):
    assert main(["--lake", str(tmp_path), "funnel", "--workspace", "dotandkey", "--date", "2026-09-21"]) == 0
    assert '"rows": 0' in capsys.readouterr().out

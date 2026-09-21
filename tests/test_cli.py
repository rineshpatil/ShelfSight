from shelfsight.cli import main


def test_validate_prompts_passes_on_seed():
    assert main(["validate-prompts", "--workspace", "dotandkey"]) == 0


def test_collect_without_keys_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert main(["--lake", str(tmp_path), "collect", "--workspace", "dotandkey"]) == 2
    assert "no engine has an API key" in capsys.readouterr().err


def test_report_on_empty_lake(tmp_path, capsys):
    assert main(["--lake", str(tmp_path), "report", "--workspace", "dotandkey", "--date", "2026-09-21"]) == 0
    assert "No funnel rows" in capsys.readouterr().out


def test_funnel_on_empty_lake(tmp_path, capsys):
    assert main(["--lake", str(tmp_path), "funnel", "--workspace", "dotandkey", "--date", "2026-09-21"]) == 0
    assert '"rows": 0' in capsys.readouterr().out

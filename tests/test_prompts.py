from datetime import date

from shelfsight.config import load_workspace
from shelfsight.prompts import Prompt, active_prompts, load_prompts, stale_prompts, validate_prompts
from shelfsight.text import normalize


def P(**kw) -> Prompt:
    base = dict(prompt_id="SUN-DISC-001", prompt_text="best sunscreen for oily skin", intent="discovery",
                persona="", market="IN", priority="high", version=1, active=True, owner="R",
                last_reviewed=date(2026, 9, 1))
    return Prompt(**{**base, **kw})


def test_normalize():
    assert normalize("Re’equil") == "reequil"
    assert normalize("Dot & Key!") == "dot key"
    assert normalize("  Lakmé   SPF-50 ") == "lakme spf 50"


def test_seed_file_is_valid():
    ws = load_workspace("dotandkey")
    prompts = load_prompts("data/prompts_seed.csv")
    assert len(prompts) == 10
    assert validate_prompts(prompts, ws) == []


def test_discovery_prompt_naming_client_is_rejected():
    ws = load_workspace("dotandkey")
    errs = validate_prompts([P(prompt_text="is dot and key good for oily skin")], ws)
    assert any("names the client brand" in e for e in errs)


def test_brand_direct_prompt_may_name_client():
    ws = load_workspace("dotandkey")
    assert validate_prompts([P(intent="brand_direct", prompt_text="is Dot & Key sunscreen good?")], ws) == []


def test_bad_rows_are_reported():
    ws = load_workspace("dotandkey")
    errs = validate_prompts([P(), P(), P(prompt_id="X", intent="vibes", priority="urgent", prompt_text="")], ws)
    assert "SUN-DISC-001 v1: duplicate id+version" in errs
    assert "X: empty prompt_text" in errs
    assert "X: unknown intent 'vibes'" in errs
    assert "X: unknown priority 'urgent'" in errs


def test_active_prompts_takes_latest_version_and_sorts_by_priority():
    ps = [P(prompt_id="B", priority="low"), P(prompt_id="A", version=1, prompt_text="old"),
          P(prompt_id="A", version=2, prompt_text="new", priority="medium"), P(prompt_id="C", active=False)]
    assert [(p.prompt_id, p.version) for p in active_prompts(ps)] == [("A", 2), ("B", 1)]


def test_retired_latest_version_drops_prompt():
    assert active_prompts([P(prompt_id="A", version=1), P(prompt_id="A", version=2, active=False)]) == []


def test_stale_prompts():
    ps = [P(prompt_id="old", last_reviewed=date(2026, 6, 1)), P(prompt_id="fresh", last_reviewed=date(2026, 9, 1))]
    assert [p.prompt_id for p in stale_prompts(ps, today=date(2026, 9, 21))] == ["old"]

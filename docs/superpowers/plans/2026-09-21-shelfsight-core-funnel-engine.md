# ShelfSight Core Funnel Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Python pipeline that sends a versioned set of buyer prompts to Gemini (grounded) and Groq, probes SearxNG with the model's own search queries, extracts brand mentions, and gives every prompt × engine × brand a funnel diagnosis.

**Architecture:** Four stages, each of which runs on its own over an append-only Parquet lake: `collect` (answers + citations + retrieval probe) → `extract` (dictionary + LLM mentions) → `funnel` (join + diagnose) → `report` (Markdown summary). The stages share nothing except the lake and versioned config, so extraction and diagnosis can be re-run over stored answers without collecting again.

**Tech Stack:** Python 3.12, uv, httpx, pydantic v2, tenacity, rapidfuzz, tldextract, DuckDB, PyYAML, pytest, respx, SearxNG (Docker).

**Spec:** `docs/superpowers/specs/2026-09-21-shelfsight-design.md`

## Global Constraints

- Python `>=3.12`, managed with `uv`. Run everything from the repo root.
- Runtime dependencies are limited to `httpx`, `pydantic>=2`, `tenacity`, `rapidfuzz`, `tldextract`, `duckdb` and `pyyaml`. Dev-only: `pytest`, `respx`.
- Tests never touch the network. HTTP goes through `respx` mocks, and `tldextract` uses its bundled suffix list (`suffix_list_urls=()`). Live checks go in `scripts/`, not `tests/`.
- API keys come from the env vars `GEMINI_API_KEY` and `GROQ_API_KEY`. They are sent in headers, never in URLs, and are never written to config, the lake or git.
- Every model id is pinned in `config/config.yaml`.
- Official APIs only. Never scrape consumer chat apps.
- The lake is append-only. No code rewrites or deletes a Parquet file.
- Derived rows carry `extractor_version` / `joiner_version`, and readers filter on the current value.
- Run dates are IST calendar dates (UTC+05:30, no DST).
- Every commit message ends with the trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## Out of scope for this plan (see spec §6)

The Indexed stage and `page_index` (Plan 2), interventions and verification (Plan 2), the S3 backend, n8n, AWS and the Google Sheet sync (Plan 3), the dashboard (Plan 4), and the AI Overviews collector (later).

## File Structure

```text
pyproject.toml                    project + deps (uv)
.gitignore
.github/workflows/ci.yml          pytest on push
README.md                         how to run locally
config/config.yaml                engines, pinned models, thresholds, domain types
config/workspaces/dotandkey.yaml  client + competitor brands, aliases, domains
data/prompts_seed.csv             versioned prompt library (Sheet export format)
infra/searxng/docker-compose.yml  local SearxNG on :8888
infra/searxng/settings.yml        enables the JSON API
scripts/smoke_gemini.py           LIVE: checks the pinned model returns grounding fields
scripts/eval_extractor.py         LIVE: extractor recall on labelled answers
src/shelfsight/__init__.py
src/shelfsight/__main__.py        `python -m shelfsight`
src/shelfsight/config.py          pydantic models + YAML loaders
src/shelfsight/text.py            normalize(), shared by prompts + brand matching
src/shelfsight/prompts.py         load / validate / select prompts
src/shelfsight/urls.py            URL normalising, domains, brand attribution, redirect resolution
src/shelfsight/schemas.py         DuckDB DDL for every lake table
src/shelfsight/store.py           Parquet lake write + DuckDB read
src/shelfsight/engines/__init__.py
src/shelfsight/engines/base.py    result types, pacing, retries, errors
src/shelfsight/engines/gemini.py  grounded + no-web answers, JSON generation
src/shelfsight/engines/groq.py    no-web baseline answers
src/shelfsight/probe.py           SearxNG client + candidate rows
src/shelfsight/collect.py         one daily run → raw_responses, citations, candidates, runs
src/shelfsight/brand_match.py     dictionary pass
src/shelfsight/extract.py         LLM pass + reconcile + run_extract
src/shelfsight/funnel.py          join + diagnose + run_funnel
src/shelfsight/report.py          Markdown diagnosis summary
src/shelfsight/cli.py             argparse entry point
tests/...                         one test file per module, fixtures in tests/fixtures/
```

---

### Task 1: Project scaffold and config models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `config/config.yaml`, `config/workspaces/dotandkey.yaml`, `src/shelfsight/__init__.py`, `src/shelfsight/config.py`, `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Mode = Literal["web", "no_web"]`
  - `Brand(id: str, name: str, aliases: list[str], domains: list[str], url_patterns: list[str], ambiguous: bool, is_client: bool)`
  - `Workspace(id: str, category: str, market: str, brands: list[Brand])` with property `.client -> Brand`
  - `EngineCfg(name: Literal["gemini","groq"], model: str, modes: list[Mode], min_interval_s: float, temperature: float)`
  - `ExtractorCfg(model: str, version: str)`
  - `SearxngCfg(url: str, engines: list[str], probe_top_n: int, max_queries_per_response: int, min_interval_s: float)`
  - `Settings(system_prompt: str, engines: list[EngineCfg], extractor: ExtractorCfg, joiner_version: str, searxng: SearxngCfg, eligible_top_n: int, domain_types: dict[str, list[str]])`
  - `load_settings(path="config/config.yaml") -> Settings`
  - `load_workspace(workspace_id: str, root="config/workspaces") -> Workspace`

- [ ] **Step 1: Create the project files**

The folder is already a git repo tracking `origin/main` (https://github.com/rineshpatil/ShelfSight). That branch holds an MIT `LICENSE` and a one-line `README.md`, so do not run `git init`.

Create `pyproject.toml`:

```toml
[project]
name = "shelfsight"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.7",
  "tenacity>=8.2",
  "rapidfuzz>=3.9",
  "tldextract>=5.1",
  "duckdb>=1.1",
  "pyyaml>=6.0",
]

[dependency-groups]
dev = ["pytest>=8", "respx>=0.21"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shelfsight"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `.gitignore`:

```text
.venv/
__pycache__/
.pytest_cache/
data/lake/
.env
# private working notes, never published
AI Visibility Tracker — Project Blueprint.md
```

Create an empty `src/shelfsight/__init__.py`, then run:

```bash
uv sync
```

Expected: `.venv/` and `uv.lock` are created, with no errors.

- [ ] **Step 2: Write the config files**

Create `config/config.yaml`:

```yaml
system_prompt: "You are a helpful shopping assistant. The user is in India."

engines:
  - name: gemini
    model: gemini-2.5-flash        # must return groundingChunks; verify with scripts/smoke_gemini.py
    modes: [web, no_web]
    min_interval_s: 6.5            # free tier is ~10 requests/minute
    temperature: 0.7
  - name: groq
    model: llama-3.3-70b-versatile # verify the id at https://console.groq.com/docs/models
    modes: [no_web]
    min_interval_s: 2.5
    temperature: 0.7

extractor:
  model: gemini-2.5-flash
  version: "ext-1"

joiner_version: "join-1"

searxng:
  url: http://localhost:8888
  engines: [google, bing, brave, duckduckgo]
  probe_top_n: 20
  max_queries_per_response: 3
  min_interval_s: 1.5

eligible_top_n: 10

domain_types:
  marketplace: [nykaa.com, amazon.in, flipkart.com, myntra.com, purplle.com, tirabeauty.com]
  reddit: [reddit.com]
  youtube: [youtube.com, youtu.be]
  media: [vogue.in, elle.in, femina.in, cosmopolitan.in, healthshots.com, ndtv.com]
```

Create `config/workspaces/dotandkey.yaml`:

```yaml
# Domains and marketplace URL patterns are best-effort seeds. Confirm each one before the first real run.
id: dotandkey
category: sunscreen
market: IN
brands:
  - id: dotandkey
    name: Dot & Key
    is_client: true
    aliases: ["dot & key", "dot and key", "dot n key", "dotandkey"]
    domains: [dotandkey.com]
    url_patterns: ["nykaa.com/dot-key", "amazon.in/dot-key"]
  - id: minimalist
    name: Minimalist
    ambiguous: true              # also an ordinary English word
    aliases: ["minimalist", "be minimalist"]
    domains: [beminimalist.co]
  - id: dermaco
    name: The Derma Co
    aliases: ["the derma co", "derma co", "dermaco"]
    domains: [thedermaco.com]
  - id: reequil
    name: Re'equil
    aliases: ["re'equil", "reequil", "re equil"]
    domains: [reequil.com]
  - id: lashield
    name: La Shield
    aliases: ["la shield", "lashield"]
  - id: neutrogena
    name: Neutrogena
    aliases: ["neutrogena"]
    domains: [neutrogena.in, neutrogena.com]
  - id: lakme
    name: Lakme
    aliases: ["lakme", "lakmé"]
    domains: [lakmeindia.com]
  - id: aqualogica
    name: Aqualogica
    aliases: ["aqualogica"]
    domains: [aqualogica.in]
  - id: deconstruct
    name: Deconstruct
    ambiguous: true
    aliases: ["deconstruct"]
    domains: [thedeconstruct.in]
```

- [ ] **Step 3: Write the failing tests**

Create `tests/conftest.py`:

```python
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _repo_root(monkeypatch):
    """Config paths are repo-relative; run every test from the repo root."""
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
```

Create `tests/test_config.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.config'`

- [ ] **Step 5: Implement `src/shelfsight/config.py`**

```python
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

Mode = Literal["web", "no_web"]


class Brand(BaseModel):
    id: str
    name: str
    aliases: list[str] = []
    domains: list[str] = []
    url_patterns: list[str] = []  # substrings of marketplace listing URLs, e.g. "nykaa.com/dot-key"
    ambiguous: bool = False       # name is also a common word; dictionary-only hits are ignored
    is_client: bool = False


class Workspace(BaseModel):
    id: str
    category: str
    market: str = "IN"
    brands: list[Brand]

    @model_validator(mode="after")
    def _check_brands(self):
        clients = [b for b in self.brands if b.is_client]
        if len(clients) != 1:
            raise ValueError(f"workspace {self.id} needs exactly one is_client brand, found {len(clients)}")
        ids = [b.id for b in self.brands]
        if len(ids) != len(set(ids)):
            raise ValueError(f"workspace {self.id} has duplicate brand ids")
        return self

    @property
    def client(self) -> Brand:
        return next(b for b in self.brands if b.is_client)


class EngineCfg(BaseModel):
    name: Literal["gemini", "groq"]
    model: str
    modes: list[Mode]
    min_interval_s: float
    temperature: float = 0.7

    @model_validator(mode="after")
    def _groq_is_no_web_only(self):
        if self.name == "groq" and "web" in self.modes:
            raise ValueError("groq has no web search tool; use modes: [no_web]")
        return self


class ExtractorCfg(BaseModel):
    model: str
    version: str


class SearxngCfg(BaseModel):
    url: str
    engines: list[str]
    probe_top_n: int = 20
    max_queries_per_response: int = 3
    min_interval_s: float = 1.5


class Settings(BaseModel):
    system_prompt: str
    engines: list[EngineCfg]
    extractor: ExtractorCfg
    joiner_version: str
    searxng: SearxngCfg
    eligible_top_n: int = 10
    domain_types: dict[str, list[str]] = {}


def load_settings(path: str | Path = "config/config.yaml") -> Settings:
    return Settings.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_workspace(workspace_id: str, root: str | Path = "config/workspaces") -> Workspace:
    text = (Path(root) / f"{workspace_id}.yaml").read_text(encoding="utf-8")
    return Workspace.model_validate(yaml.safe_load(text))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml uv.lock config src tests
git commit -m "feat: project scaffold and config models" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Prompt library loader and validator

**Files:**
- Create: `src/shelfsight/text.py`, `src/shelfsight/prompts.py`, `data/prompts_seed.csv`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `Workspace`, `load_workspace` (Task 1)
- Produces:
  - `normalize(s: str) -> str` in `text.py`: lowercases, strips accents and apostrophes, and collapses all other non-alphanumerics to single spaces
  - `Prompt` frozen dataclass: `prompt_id, prompt_text, intent, persona, market, priority, version: int, active: bool, owner, last_reviewed: date`
  - `INTENTS`, `PRIORITIES` (sets of str)
  - `load_prompts(path) -> list[Prompt]`
  - `validate_prompts(prompts: list[Prompt], workspace: Workspace) -> list[str]` (an empty list means valid)
  - `active_prompts(prompts) -> list[Prompt]`: the latest version of each prompt, kept only if that version is active, sorted high → low priority and then by id
  - `stale_prompts(prompts, today: date, days: int = 60) -> list[Prompt]`

- [ ] **Step 1: Write the seed prompt file**

Create `data/prompts_seed.csv` with 2 rows per intent. Step 7 extends it to 50.

```csv
prompt_id,prompt_text,intent,persona,market,priority,version,active,owner,last_reviewed
SUN-DISC-001,"best sunscreen for oily skin in india under ₹500",discovery,"oily skin, 25, budget",IN,high,1,TRUE,Rinesh,2026-09-21
SUN-DISC-002,"sunscreen for oily skin under 500 rs",discovery,,IN,high,1,TRUE,Rinesh,2026-09-21
SUN-PROB-001,"sunscreen that doesn't leave a white cast on brown skin",problem_led,,IN,high,1,TRUE,Rinesh,2026-09-21
SUN-PROB-002,"konsa sunscreen lagau jo pimples na kare",problem_led,"acne-prone, 22",IN,medium,1,TRUE,Rinesh,2026-09-21
SUN-COMP-001,"Dot & Key vs Minimalist sunscreen, which is better?",comparison,,IN,high,1,TRUE,Rinesh,2026-09-21
SUN-COMP-002,"re'equil vs the derma co sunscreen for oily skin",comparison,,IN,medium,1,TRUE,Rinesh,2026-09-21
SUN-ING-001,"Is a chemical or mineral sunscreen better for acne-prone skin?",ingredient_claim,,IN,medium,1,TRUE,Rinesh,2026-09-21
SUN-ING-002,"is spf 50 really better than spf 30 for indian summers",ingredient_claim,,IN,low,1,TRUE,Rinesh,2026-09-21
SUN-BRAND-001,"Is Dot & Key sunscreen good? What are its downsides?",brand_direct,,IN,high,1,TRUE,Rinesh,2026-09-21
SUN-BRAND-002,"dot and key watermelon sunscreen review",brand_direct,,IN,medium,1,TRUE,Rinesh,2026-09-21
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_prompts.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.prompts'`

- [ ] **Step 4: Implement `src/shelfsight/text.py`**

```python
import re
import unicodedata

_APOSTROPHES = re.compile(r"[’'`]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(s: str) -> str:
    """Lowercase, strip accents and apostrophes, collapse everything else to single spaces."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = _APOSTROPHES.sub("", s.lower())
    return _NON_ALNUM.sub(" ", s).strip()
```

- [ ] **Step 5: Implement `src/shelfsight/prompts.py`**

```python
import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from shelfsight.config import Workspace
from shelfsight.text import normalize

INTENTS = {"discovery", "problem_led", "comparison", "ingredient_claim", "brand_direct"}
PRIORITIES = {"high", "medium", "low"}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_BRAND_FREE_INTENTS = {"discovery", "problem_led"}  # naming the client here inflates its mention rate


@dataclass(frozen=True)
class Prompt:
    prompt_id: str
    prompt_text: str
    intent: str
    persona: str
    market: str
    priority: str
    version: int
    active: bool
    owner: str
    last_reviewed: date


def load_prompts(path: str | Path) -> list[Prompt]:
    with open(path, newline="", encoding="utf-8") as f:
        return [
            Prompt(
                prompt_id=r["prompt_id"].strip(),
                prompt_text=r["prompt_text"].strip(),
                intent=r["intent"].strip(),
                persona=(r.get("persona") or "").strip(),
                market=(r.get("market") or "").strip() or "IN",
                priority=r["priority"].strip(),
                version=int(r["version"]),
                active=r["active"].strip().upper() == "TRUE",
                owner=(r.get("owner") or "").strip(),
                last_reviewed=date.fromisoformat(r["last_reviewed"].strip()),
            )
            for r in csv.DictReader(f)
        ]


def validate_prompts(prompts: list[Prompt], workspace: Workspace) -> list[str]:
    errors, seen = [], set()
    client = workspace.client
    client_aliases = {normalize(a) for a in [client.name, *client.aliases]} - {""}
    for p in prompts:
        if (p.prompt_id, p.version) in seen:
            errors.append(f"{p.prompt_id} v{p.version}: duplicate id+version")
        seen.add((p.prompt_id, p.version))
        if not p.prompt_text:
            errors.append(f"{p.prompt_id}: empty prompt_text")
        if p.intent not in INTENTS:
            errors.append(f"{p.prompt_id}: unknown intent {p.intent!r}")
        if p.priority not in PRIORITIES:
            errors.append(f"{p.prompt_id}: unknown priority {p.priority!r}")
        if p.intent in _BRAND_FREE_INTENTS:
            text = f" {normalize(p.prompt_text)} "
            if any(f" {a} " in text for a in client_aliases):
                errors.append(f"{p.prompt_id}: {p.intent} prompt names the client brand, which inflates its mention rate")
    return errors


def active_prompts(prompts: list[Prompt]) -> list[Prompt]:
    latest: dict[str, Prompt] = {}
    for p in prompts:
        if p.prompt_id not in latest or p.version > latest[p.prompt_id].version:
            latest[p.prompt_id] = p
    live = [p for p in latest.values() if p.active]
    return sorted(live, key=lambda p: (_PRIORITY_ORDER.get(p.priority, 9), p.prompt_id))


def stale_prompts(prompts: list[Prompt], today: date, days: int = 60) -> list[Prompt]:
    cutoff = today - timedelta(days=days)
    return [p for p in active_prompts(prompts) if p.last_reviewed < cutoff]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: 8 passed

- [ ] **Step 7: Extend the seed library to 50 prompts**

This is a content task for the operator, not code. Add 8 more rows per intent to `data/prompts_seed.csv` (10 per intent, 50 in total), following these rules from the spec:

- Write the way buyers type: mostly lowercase, with typos and Hinglish in about 1 in 10 prompts.
- Never name the client in `discovery` or `problem_led` prompts. The validator enforces this.
- `prompt_id` follows `SUN-<DISC|PROB|COMP|ING|BRAND>-NNN` and is never reused.
- Mark about 20 prompts `high`. On free tiers, those are collected first.

Then change the count in `test_seed_file_is_valid` from `10` to `50` and run:

Run: `uv run pytest tests/test_prompts.py -v`
Expected: 8 passed

- [ ] **Step 8: Commit**

```bash
git add src/shelfsight/text.py src/shelfsight/prompts.py data/prompts_seed.csv tests/test_prompts.py
git commit -m "feat: versioned prompt library loader and validator" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: URL utilities

**Files:**
- Create: `src/shelfsight/urls.py`
- Test: `tests/test_urls.py`

**Interfaces:**
- Consumes: `Brand` (Task 1)
- Produces:
  - `GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"`
  - `normalize_url(url: str) -> str`: lowercases the host and drops `www.`, tracking params, the fragment and any trailing slash. A root path becomes `/`.
  - `registered_domain(url_or_host: str) -> str`: `m.youtube.com` → `youtube.com`, `www.amazon.in` → `amazon.in`
  - `domain_type(domain: str, brand_domains: set[str], domain_types: dict[str, list[str]]) -> str` returning `"brand"`, a key of `domain_types`, or `"other"`
  - `brand_for_url(url: str, brands: list[Brand]) -> str | None`
  - `resolve_grounding_url(uri: str, client: httpx.Client) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_urls.py`:

```python
import httpx
import respx

from shelfsight.config import Brand
from shelfsight.urls import brand_for_url, domain_type, normalize_url, registered_domain, resolve_grounding_url


def test_normalize_strips_tracking_www_fragment_and_trailing_slash():
    got = normalize_url("https://www.Nykaa.com/dot-key/p/123/?utm_source=x&gclid=y&size=50#reviews")
    assert got == "https://nykaa.com/dot-key/p/123?size=50"


def test_normalize_keeps_root_path():
    assert normalize_url("https://dotandkey.com") == "https://dotandkey.com/"


def test_registered_domain_handles_subdomains_and_cc_tlds():
    assert registered_domain("https://m.youtube.com/watch?v=1") == "youtube.com"
    assert registered_domain("https://www.amazon.in/dp/B0") == "amazon.in"
    assert registered_domain("https://beminimalist.co/products/x") == "beminimalist.co"


def test_domain_type():
    types = {"marketplace": ["nykaa.com"], "reddit": ["reddit.com"]}
    assert domain_type("dotandkey.com", {"dotandkey.com"}, types) == "brand"
    assert domain_type("nykaa.com", set(), types) == "marketplace"
    assert domain_type("example.com", set(), types) == "other"


def test_brand_for_url_matches_own_domain_or_marketplace_pattern():
    brands = [Brand(id="dk", name="Dot & Key", domains=["dotandkey.com"], url_patterns=["nykaa.com/dot-key"])]
    assert brand_for_url("https://dotandkey.com/products/x", brands) == "dk"
    assert brand_for_url("https://nykaa.com/dot-key-sunscreen/p/1", brands) == "dk"
    assert brand_for_url("https://nykaa.com/minimalist/p/2", brands) is None


@respx.mock
def test_resolve_grounding_url_reads_location_header():
    src = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA"
    respx.get(src).mock(return_value=httpx.Response(302, headers={"location": "https://www.nykaa.com/x"}))
    with httpx.Client() as c:
        assert resolve_grounding_url(src, c) == "https://www.nykaa.com/x"


@respx.mock
def test_resolve_grounding_url_returns_input_on_network_error():
    src = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB"
    respx.get(src).mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as c:
        assert resolve_grounding_url(src, c) == src


def test_resolve_grounding_url_passes_through_normal_urls():
    with httpx.Client() as c:
        assert resolve_grounding_url("https://reddit.com/r/x", c) == "https://reddit.com/r/x"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_urls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.urls'`

- [ ] **Step 3: Implement `src/shelfsight/urls.py`**

```python
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import tldextract

from shelfsight.config import Brand

GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_extract = tldextract.TLDExtract(suffix_list_urls=())  # bundled suffix list; never hits the network
_TRACKING_KEYS = {"gclid", "fbclid", "srsltid", "ref", "mc_cid", "mc_eid"}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in _TRACKING_KEYS


def normalize_url(url: str) -> str:
    """Lowercase host, drop www., tracking params, fragment and trailing slash."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def registered_domain(url_or_host: str) -> str:
    ext = _extract(url_or_host)
    return ".".join(p for p in (ext.domain, ext.suffix) if p).lower()


def domain_type(domain: str, brand_domains: set[str], domain_types: dict[str, list[str]]) -> str:
    if domain in brand_domains:
        return "brand"
    for kind, domains in domain_types.items():
        if domain in domains:
            return kind
    return "other"


def brand_for_url(url: str, brands: list[Brand]) -> str | None:
    """The brand that owns this URL: its own domain, or a marketplace listing matching one of its url_patterns."""
    domain = registered_domain(url)
    lowered = url.lower()
    for b in brands:
        if domain in b.domains or any(p in lowered for p in b.url_patterns):
            return b.id
    return None


def resolve_grounding_url(uri: str, client: httpx.Client) -> str:
    """Gemini grounding chunks point at a Google redirect. Return its destination, or the input if unresolved."""
    if urlsplit(uri).netloc != GROUNDING_REDIRECT_HOST:
        return uri
    try:
        r = client.get(uri, follow_redirects=False, timeout=10)
    except httpx.HTTPError:
        return uri
    return r.headers.get("location", uri)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_urls.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/shelfsight/urls.py tests/test_urls.py
git commit -m "feat: URL normalising, domain typing and brand attribution" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Table schemas and the Parquet lake store

**Files:**
- Create: `src/shelfsight/schemas.py`, `src/shelfsight/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `TABLES: dict[str, str]`: the DuckDB column DDL for `runs`, `raw_responses`, `citations`, `candidates`, `mentions` and `funnel`
  - `columns(table: str) -> list[tuple[str, str]]`: `(name, type)` pairs in DDL order
  - `utcnow() -> datetime` (naive UTC)
  - `Store(root: str | Path)` with:
    - `.write(table: str, rows: list[dict]) -> int`: one new Parquet file per `(workspace, run_date)`. Missing keys become NULL, and an unknown key raises `ValueError("... unknown columns ...")`. Every row must have `workspace` and `run_date`.
    - `.connect() -> duckdb.DuckDBPyConnection`: one view per table, named after the table
    - `.rows(sql: str, params: list | dict | None = None) -> list[dict]`. Positional params use `?`; named params use `$name` with a dict.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store.py`:

```python
from datetime import date

import pytest

from shelfsight.store import Store


def row(**kw):
    base = dict(response_id="r1", workspace="ws", run_date=date(2026, 9, 21), engine="gemini", mode="web",
                prompt_id="P1", position=1, url="https://a.com/", domain="a.com", domain_type="other", brand_id=None)
    return {**base, **kw}


def test_write_then_read_roundtrip(tmp_path):
    s = Store(tmp_path)
    assert s.write("citations", [row(), row(position=2, url="https://b.com/", domain="b.com")]) == 2
    got = s.rows("SELECT position, domain FROM citations ORDER BY position")
    assert got == [{"position": 1, "domain": "a.com"}, {"position": 2, "domain": "b.com"}]


def test_writes_are_partitioned_by_workspace_and_date(tmp_path):
    s = Store(tmp_path)
    s.write("citations", [row(), row(response_id="r2", run_date=date(2026, 9, 22))])
    dirs = sorted(p.relative_to(tmp_path).parent.as_posix() for p in tmp_path.rglob("*.parquet"))
    assert dirs == ["citations/workspace=ws/run_date=2026-09-21", "citations/workspace=ws/run_date=2026-09-22"]


def test_second_write_to_same_partition_appends_a_file(tmp_path):
    s = Store(tmp_path)
    s.write("citations", [row()])
    s.write("citations", [row(response_id="r2")])
    assert len(list(tmp_path.rglob("*.parquet"))) == 2
    assert s.rows("SELECT count(*) AS n FROM citations") == [{"n": 2}]


def test_list_columns_roundtrip(tmp_path):
    s = Store(tmp_path)
    s.write("mentions", [dict(response_id="r1", workspace="ws", run_date=date(2026, 9, 21), engine="gemini",
                              mode="web", prompt_id="P1", brand_id="dk", brand_raw="Dot & Key", rank=1,
                              is_recommended=True, sentiment="positive", claims=["no white cast", "spf 50"],
                              source="both", extractor_version="ext-1")])
    assert s.rows("SELECT claims FROM mentions") == [{"claims": ["no white cast", "spf 50"]}]


def test_named_params(tmp_path):
    s = Store(tmp_path)
    s.write("citations", [row(), row(response_id="r2", workspace="other")])
    assert s.rows("SELECT response_id FROM citations WHERE workspace = $ws", {"ws": "ws"}) == [{"response_id": "r1"}]


def test_empty_table_is_queryable(tmp_path):
    assert Store(tmp_path).rows("SELECT count(*) AS n FROM funnel") == [{"n": 0}]


def test_unknown_column_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown columns"):
        Store(tmp_path).write("citations", [row(bogus=1)])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.store'`

- [ ] **Step 3: Implement `src/shelfsight/schemas.py`**

```python
"""DuckDB column definitions for every lake table.

Types must be unparameterised (no DECIMAL(p, s)): columns() splits on commas.
Adding a column is safe because reads use union_by_name; renaming or retyping one is not.
"""

TABLES: dict[str, str] = {
    "runs": """
        run_id VARCHAR, workspace VARCHAR, run_date DATE, status VARCHAR,
        started_at TIMESTAMP, finished_at TIMESTAMP,
        planned_calls INTEGER, ok_calls INTEGER, error_calls INTEGER, skipped_calls INTEGER, probe_errors INTEGER,
        coverage DOUBLE, quota_exhausted VARCHAR[], input_tokens BIGINT, output_tokens BIGINT
    """,
    "raw_responses": """
        response_id VARCHAR, run_id VARCHAR, workspace VARCHAR, run_date DATE,
        prompt_id VARCHAR, prompt_version INTEGER, intent VARCHAR, priority VARCHAR,
        engine VARCHAR, model VARCHAR, mode VARCHAR, sample_n INTEGER,
        status VARCHAR, error VARCHAR,
        response_text VARCHAR, web_search_queries VARCHAR[], raw_json VARCHAR,
        system_prompt VARCHAR, temperature DOUBLE,
        input_tokens INTEGER, output_tokens INTEGER, latency_ms INTEGER, created_at TIMESTAMP
    """,
    "citations": """
        response_id VARCHAR, workspace VARCHAR, run_date DATE, engine VARCHAR, mode VARCHAR, prompt_id VARCHAR,
        position INTEGER, url VARCHAR, domain VARCHAR, domain_type VARCHAR, brand_id VARCHAR
    """,
    "candidates": """
        response_id VARCHAR, workspace VARCHAR, run_date DATE, engine VARCHAR, prompt_id VARCHAR,
        search_query VARCHAR, query_index INTEGER, position INTEGER,
        url VARCHAR, domain VARCHAR, domain_type VARCHAR, source_engine VARCHAR, brand_id VARCHAR,
        probed_at TIMESTAMP
    """,
    # source: llm | dictionary | both | none. A "none" row is a sentinel meaning
    # "extracted, no brands found", so a response is never re-extracted or mistaken for unextracted.
    "mentions": """
        response_id VARCHAR, workspace VARCHAR, run_date DATE, engine VARCHAR, mode VARCHAR, prompt_id VARCHAR,
        brand_id VARCHAR, brand_raw VARCHAR, rank INTEGER, is_recommended BOOLEAN,
        sentiment VARCHAR, claims VARCHAR[], source VARCHAR, extractor_version VARCHAR
    """,
    "funnel": """
        workspace VARCHAR, run_date DATE, engine VARCHAR, mode VARCHAR, prompt_id VARCHAR, prompt_version INTEGER,
        intent VARCHAR, priority VARCHAR, brand_id VARCHAR, is_client BOOLEAN, response_id VARCHAR,
        is_eligible BOOLEAN, is_cited BOOLEAN, is_named BOOLEAN, is_recommended BOOLEAN, rank INTEGER,
        eligible_n INTEGER, diagnosis VARCHAR, extractor_version VARCHAR, joiner_version VARCHAR
    """,
}


def columns(table: str) -> list[tuple[str, str]]:
    """[(name, type), ...] in DDL order."""
    parts = (c.strip() for c in TABLES[table].split(","))
    return [tuple(c.split(maxsplit=1)) for c in parts if c]
```

- [ ] **Step 4: Implement `src/shelfsight/store.py`**

```python
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from shelfsight.schemas import TABLES, columns


def utcnow() -> datetime:
    """Naive UTC timestamp. The lake stores TIMESTAMP, not TIMESTAMPTZ."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Store:
    """Append-only Parquet lake: {root}/{table}/workspace={w}/run_date={d}/{uuid}.parquet, queried with DuckDB."""

    def __init__(self, root: str | Path):
        self.root = Path(root)  # ponytail: local directory only; Plan 3 adds an s3:// root via httpfs

    def write(self, table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        names = [n for n, _ in columns(table)]
        groups: dict[tuple[str, str], list[tuple]] = defaultdict(list)
        for r in rows:
            unknown = set(r) - set(names)
            if unknown:
                raise ValueError(f"{table}: unknown columns {sorted(unknown)}")
            groups[(r["workspace"], str(r["run_date"]))].append(tuple(r.get(n) for n in names))
        con = duckdb.connect()
        try:
            con.execute(f"CREATE TABLE w ({TABLES[table]})")
            insert = f"INSERT INTO w VALUES ({', '.join('?' * len(names))})"
            for (workspace, day), values in groups.items():
                out = self.root / table / f"workspace={workspace}" / f"run_date={day}"
                out.mkdir(parents=True, exist_ok=True)
                con.execute("DELETE FROM w")
                con.executemany(insert, values)
                con.execute(f"COPY w TO '{(out / f'{uuid.uuid4().hex}.parquet').as_posix()}' (FORMAT parquet)")
        finally:
            con.close()
        return len(rows)

    def connect(self) -> duckdb.DuckDBPyConnection:
        """In-memory DuckDB with one view per table. A table with no files yet is empty but still queryable."""
        con = duckdb.connect()
        for table in TABLES:
            base = self.root / table
            if base.exists() and any(base.rglob("*.parquet")):
                src = f"read_parquet('{base.as_posix()}/**/*.parquet', union_by_name = true, hive_partitioning = false)"
            else:
                typed_nulls = ", ".join(f"NULL::{t} AS {n}" for n, t in columns(table))
                src = f"(SELECT {typed_nulls}) WHERE false"
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM {src}")
        return con

    def rows(self, sql: str, params: list | dict | None = None) -> list[dict]:
        con = self.connect()
        try:
            cur = con.execute(sql, params) if params is not None else con.execute(sql)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        finally:
            con.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add src/shelfsight/schemas.py src/shelfsight/store.py tests/test_store.py
git commit -m "feat: append-only Parquet lake with DuckDB views" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Engine base and the Gemini engine

**Files:**
- Create: `src/shelfsight/engines/__init__.py` (empty), `src/shelfsight/engines/base.py`, `src/shelfsight/engines/gemini.py`, `tests/fixtures/gemini_grounded.json`
- Test: `tests/test_engine_base.py`, `tests/test_gemini.py`

**Interfaces:**
- Consumes: `resolve_grounding_url`, `GROUNDING_REDIRECT_HOST` (Task 3)
- Produces:
  - `RetryableError(status: int, body: str)` with `.status`
  - `Citation(url: str, title: str, position: int)` (position is 1-based)
  - `EngineResult(text, model, web_search_queries: list[str], citations: list[Citation], input_tokens: int, output_tokens: int, latency_ms: int, raw: dict)`
  - `Pacer(min_interval_s, clock=time.monotonic, sleep=time.sleep)` with `.wait()`
  - `retrying(wait=None) -> tenacity.Retrying` (4 attempts, retries `RetryableError` and `httpx.TransportError`)
  - `raise_for_status(r: httpx.Response) -> None`
  - `post_json(client, url, headers, body, pacer) -> tuple[dict, int]`
  - `GeminiEngine(api_key, model, client, min_interval_s=0, temperature=0.7, retry_wait=None)` with:
    - `.name == "gemini"`
    - `.ask(prompt: str, system_prompt: str, mode: str) -> EngineResult`: `mode="web"` adds the `google_search` tool
    - `.generate_json(prompt: str, schema: dict) -> dict`
  - `API` (URL template with `{model}`) in `gemini.py`

- [ ] **Step 1: Write the grounded-response fixture**

Create `tests/fixtures/gemini_grounded.json`. Tasks 9 and 10 reuse its answer text.

```json
{
  "candidates": [{
    "content": {"role": "model", "parts": [{"text": "For oily skin in India, try:\n1. **Minimalist SPF 50** - lightweight, no white cast.\n2. **Re'equil Oil Control Sunscreen** - matte finish.\n3. **Dot & Key Watermelon Cooling Sunscreen** - gel texture, though some find it sticky.\nNeutrogena Ultra Sheer is also widely available."}]},
    "groundingMetadata": {
      "webSearchQueries": ["best sunscreen for oily skin india under 500"],
      "groundingChunks": [
        {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA", "title": "nykaa.com"}},
        {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB", "title": "reddit.com"}}
      ]
    }
  }],
  "usageMetadata": {"promptTokenCount": 42, "candidatesTokenCount": 180}
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_engine_base.py`:

```python
import httpx
import pytest
import respx
from tenacity import wait_none

from shelfsight.engines.base import Pacer, RetryableError, post_json, retrying


def test_pacer_sleeps_only_the_remaining_gap():
    t, slept = [100.0], []
    p = Pacer(5, clock=lambda: t[0], sleep=slept.append)
    p.wait()
    t[0] = 102.0
    p.wait()
    t[0] = 111.0
    p.wait()
    assert slept == [3.0]


@respx.mock
def test_4xx_other_than_429_is_not_retried():
    route = respx.post("https://x.test/").mock(return_value=httpx.Response(400, text="bad"))
    with httpx.Client() as c, pytest.raises(httpx.HTTPStatusError):
        retrying(wait_none())(post_json, c, "https://x.test/", {}, {}, Pacer(0))
    assert route.call_count == 1


@respx.mock
def test_5xx_is_retried_four_times_then_raised():
    route = respx.post("https://x.test/").mock(return_value=httpx.Response(503, text="down"))
    with httpx.Client() as c, pytest.raises(RetryableError) as e:
        retrying(wait_none())(post_json, c, "https://x.test/", {}, {}, Pacer(0))
    assert e.value.status == 503
    assert route.call_count == 4
```

Create `tests/test_gemini.py`:

```python
import json
from pathlib import Path

import httpx
import pytest
import respx
from tenacity import wait_none

from shelfsight.engines.base import RetryableError
from shelfsight.engines.gemini import API, GeminiEngine

URL = API.format(model="gemini-test")
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def engine(client):
    return GeminiEngine(api_key="k", model="gemini-test", client=client, retry_wait=wait_none())


def ok(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


@respx.mock
def test_web_mode_parses_queries_and_resolves_citations():
    fixture = json.loads(Path("tests/fixtures/gemini_grounded.json").read_text(encoding="utf-8"))
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=fixture))
    respx.get(REDIRECT + "AAA").mock(
        return_value=httpx.Response(302, headers={"location": "https://www.nykaa.com/dot-key-sunscreen/p/1"}))
    respx.get(REDIRECT + "BBB").mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as c:
        res = engine(c).ask("best sunscreen", "sys", mode="web")
    req = route.calls[0].request
    assert json.loads(req.content)["tools"] == [{"google_search": {}}]
    assert req.headers["x-goog-api-key"] == "k"
    assert "key" not in req.url.params
    assert res.web_search_queries == ["best sunscreen for oily skin india under 500"]
    assert [(c.position, c.url) for c in res.citations] == [
        (1, "https://www.nykaa.com/dot-key-sunscreen/p/1"),
        (2, "https://reddit.com/"),  # unresolved redirect falls back to the chunk title
    ]
    assert (res.input_tokens, res.output_tokens) == (42, 180)
    assert "Minimalist" in res.text


@respx.mock
def test_no_web_mode_sends_no_tools():
    route = respx.post(URL).mock(return_value=ok("hi"))
    with httpx.Client() as c:
        res = engine(c).ask("q", "sys", mode="no_web")
    assert "tools" not in json.loads(route.calls[0].request.content)
    assert (res.text, res.citations, res.web_search_queries) == ("hi", [], [])


@respx.mock
def test_retries_429_then_succeeds():
    respx.post(URL).mock(side_effect=[httpx.Response(429, text="slow down"), ok("ok")])
    with httpx.Client() as c:
        assert engine(c).ask("q", "sys", mode="no_web").text == "ok"


@respx.mock
def test_persistent_429_raises_retryable_with_status():
    respx.post(URL).mock(return_value=httpx.Response(429, text="quota"))
    with httpx.Client() as c, pytest.raises(RetryableError) as e:
        engine(c).ask("q", "sys", mode="no_web")
    assert e.value.status == 429


@respx.mock
def test_generate_json_requests_json_and_parses_it():
    route = respx.post(URL).mock(return_value=ok('{"brands": []}'))
    with httpx.Client() as c:
        assert engine(c).generate_json("p", {"type": "OBJECT"}) == {"brands": []}
    cfg = json.loads(route.calls[0].request.content)["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["responseSchema"] == {"type": "OBJECT"}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine_base.py tests/test_gemini.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.engines'`

- [ ] **Step 4: Implement `src/shelfsight/engines/base.py`**

Create an empty `src/shelfsight/engines/__init__.py`, then:

```python
import time
from dataclasses import dataclass, field

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential


class RetryableError(Exception):
    """HTTP 429 or 5xx. Retried; a 429 that persists means the free-tier quota is gone for this run."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


@dataclass
class Citation:
    url: str
    title: str
    position: int


@dataclass
class EngineResult:
    text: str
    model: str
    web_search_queries: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)


class Pacer:
    """Enforces a minimum gap between calls. Free tiers are rate-limited per minute."""

    def __init__(self, min_interval_s: float, clock=time.monotonic, sleep=time.sleep):
        self.min_interval_s = min_interval_s
        self._clock, self._sleep, self._last = clock, sleep, None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            gap = self.min_interval_s - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now += gap
        self._last = now


def retrying(wait=None) -> Retrying:
    """4 attempts with exponential backoff on 429/5xx and transport errors. Pass wait=wait_none() in tests."""
    return Retrying(
        retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
        stop=stop_after_attempt(4),
        wait=wait or wait_exponential(multiplier=2, max=30),
        reraise=True,
    )


def raise_for_status(r: httpx.Response) -> None:
    if r.status_code == 429 or r.status_code >= 500:
        raise RetryableError(r.status_code, r.text)
    r.raise_for_status()


def post_json(client: httpx.Client, url: str, headers: dict, body: dict, pacer: Pacer) -> tuple[dict, int]:
    """One paced POST. Returns (json, latency_ms); the latency excludes the pacing wait."""
    pacer.wait()
    start = time.monotonic()
    r = client.post(url, headers=headers, json=body, timeout=120)
    latency_ms = int((time.monotonic() - start) * 1000)
    raise_for_status(r)
    return r.json(), latency_ms
```

- [ ] **Step 5: Implement `src/shelfsight/engines/gemini.py`**

```python
import json

import httpx

from shelfsight.engines.base import Citation, EngineResult, Pacer, post_json, retrying
from shelfsight.urls import GROUNDING_REDIRECT_HOST, resolve_grounding_url

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _text(candidate: dict) -> str:
    return "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))


class GeminiEngine:
    name = "gemini"

    def __init__(self, api_key: str, model: str, client: httpx.Client, min_interval_s: float = 0,
                 temperature: float = 0.7, retry_wait=None):
        self.model, self.temperature = model, temperature
        self._client = client
        self._url = API.format(model=model)
        self._headers = {"x-goog-api-key": api_key}  # a header, never a query string
        self._pacer = Pacer(min_interval_s)
        self._retrying = retrying(retry_wait)

    def ask(self, prompt: str, system_prompt: str, mode: str) -> EngineResult:
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature},
        }
        if mode == "web":
            body["tools"] = [{"google_search": {}}]
        data, latency_ms = self._retrying(post_json, self._client, self._url, self._headers, body, self._pacer)
        cand = (data.get("candidates") or [{}])[0]
        gm = cand.get("groundingMetadata", {})
        chunks = [ch["web"] for ch in gm.get("groundingChunks", []) if "web" in ch]
        usage = data.get("usageMetadata", {})
        return EngineResult(
            text=_text(cand),
            model=self.model,
            web_search_queries=gm.get("webSearchQueries", []),
            citations=[Citation(url=self._resolve(w), title=w.get("title", ""), position=i)
                       for i, w in enumerate(chunks, start=1)],
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            latency_ms=latency_ms,
            raw=data,
        )

    def generate_json(self, prompt: str, schema: dict) -> dict:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema},
        }
        data, _ = self._retrying(post_json, self._client, self._url, self._headers, body, self._pacer)
        return json.loads(_text(data["candidates"][0]))

    def _resolve(self, web: dict) -> str:
        url = resolve_grounding_url(web["uri"], self._client)
        title = web.get("title", "")
        if GROUNDING_REDIRECT_HOST in url and "." in title:
            return f"https://{title}/"  # unresolved redirect: the chunk title is the source domain
        return url
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine_base.py tests/test_gemini.py -v`
Expected: 8 passed

- [ ] **Step 7: Commit**

```bash
git add src/shelfsight/engines tests/test_engine_base.py tests/test_gemini.py tests/fixtures/gemini_grounded.json
git commit -m "feat: Gemini engine with grounding, pacing and retries" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Groq no-web baseline engine

**Files:**
- Create: `src/shelfsight/engines/groq.py`
- Test: `tests/test_groq.py`

**Interfaces:**
- Consumes: `EngineResult`, `Pacer`, `post_json`, `retrying` (Task 5)
- Produces: `GroqEngine(api_key, model, client, min_interval_s=0, temperature=0.7, retry_wait=None)` with `.name == "groq"` and `.ask(prompt, system_prompt, mode) -> EngineResult`. It raises `ValueError` unless `mode == "no_web"`. `API` is the endpoint constant.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_groq.py`:

```python
import json

import httpx
import pytest
import respx
from tenacity import wait_none

from shelfsight.engines.groq import API, GroqEngine

OK = {"model": "llama-served", "choices": [{"message": {"content": "Try Minimalist."}}],
      "usage": {"prompt_tokens": 20, "completion_tokens": 5}}


def engine(client):
    return GroqEngine(api_key="k", model="groq-test", client=client, retry_wait=wait_none())


@respx.mock
def test_parses_chat_completion_and_sends_bearer_key():
    route = respx.post(API).mock(return_value=httpx.Response(200, json=OK))
    with httpx.Client() as c:
        res = engine(c).ask("q", "sys", mode="no_web")
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer k"
    body = json.loads(req.content)
    assert body["model"] == "groq-test"
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    assert (res.text, res.model, res.input_tokens, res.output_tokens) == ("Try Minimalist.", "groq-test", 20, 5)
    assert res.raw["model"] == "llama-served"  # served model kept in raw, to spot silent swaps


def test_rejects_web_mode():
    with httpx.Client() as c, pytest.raises(ValueError, match="no web search"):
        engine(c).ask("q", "sys", mode="web")


@respx.mock
def test_retries_503():
    respx.post(API).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=OK)])
    with httpx.Client() as c:
        assert engine(c).ask("q", "sys", mode="no_web").text == "Try Minimalist."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_groq.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.engines.groq'`

- [ ] **Step 3: Implement `src/shelfsight/engines/groq.py`**

```python
import httpx

from shelfsight.engines.base import EngineResult, Pacer, post_json, retrying

API = "https://api.groq.com/openai/v1/chat/completions"


class GroqEngine:
    name = "groq"

    def __init__(self, api_key: str, model: str, client: httpx.Client, min_interval_s: float = 0,
                 temperature: float = 0.7, retry_wait=None):
        self.model, self.temperature = model, temperature
        self._client = client
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._pacer = Pacer(min_interval_s)
        self._retrying = retrying(retry_wait)

    def ask(self, prompt: str, system_prompt: str, mode: str) -> EngineResult:
        if mode != "no_web":
            raise ValueError("groq has no web search tool; only no_web mode is supported")
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        }
        data, latency_ms = self._retrying(post_json, self._client, API, self._headers, body, self._pacer)
        usage = data.get("usage", {})
        return EngineResult(
            text=data["choices"][0]["message"].get("content") or "",
            model=self.model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            raw=data,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_groq.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/shelfsight/engines/groq.py tests/test_groq.py
git commit -m "feat: Groq no-web baseline engine" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: SearxNG retrieval probe

This is the instrument that makes ShelfSight different: it takes the search queries the model itself generated and runs them through an independent index, so we can tell whether the client was even in the candidate set.

**Files:**
- Create: `src/shelfsight/probe.py`, `infra/searxng/docker-compose.yml`, `infra/searxng/settings.yml`, `tests/fixtures/searxng.json`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `Brand` (Task 1); `normalize_url`, `registered_domain`, `domain_type`, `brand_for_url` (Task 3); `utcnow` (Task 4); `Pacer`, `raise_for_status`, `retrying` (Task 5)
- Produces:
  - `SearxProbe(base_url: str, engines: list[str], client: httpx.Client, top_n=20, min_interval_s=0, retry_wait=None)` with `.search(query: str) -> list[dict]`, returning `[{"url": str, "source_engine": str}]` in rank order, capped at `top_n`
  - `candidate_rows(probe, response: dict, queries: list[str], max_queries: int, brands: list[Brand], domain_types: dict) -> list[dict]`: rows for the `candidates` table. `response` needs `response_id, workspace, run_date, engine, prompt_id`. `position` is 1-based within each query.

- [ ] **Step 1: Add local SearxNG**

Create `infra/searxng/docker-compose.yml`:

```yaml
# Local SearxNG for the retrieval probe.
# Start: docker compose -f infra/searxng/docker-compose.yml up -d
services:
  searxng:
    image: searxng/searxng:latest   # ponytail: floating tag; pin a dated tag once a working one is known
    ports:
      - "127.0.0.1:8888:8080"      # loopback only: never expose the metasearch publicly
    volumes:
      - ./settings.yml:/etc/searxng/settings.yml:ro
    restart: unless-stopped
```

Create `infra/searxng/settings.yml`:

```yaml
use_default_settings: true
server:
  secret_key: "local-dev-only-change-me"  # Plan 3 injects a real one from SSM
  limiter: false                           # the collector is the only client
  image_proxy: false
search:
  formats: [html, json]                    # the JSON API is off by default
```

Start it and check that the JSON API answers:

```bash
docker compose -f infra/searxng/docker-compose.yml up -d
curl -s "http://localhost:8888/search?q=sunscreen&format=json" | head -c 300
```

Expected: a JSON object beginning with `{"query": "sunscreen"` and containing a `"results"` array. A `403` means `formats` is missing `json`.

- [ ] **Step 2: Write the fixture and the failing tests**

Create `tests/fixtures/searxng.json`:

```json
{"query": "best sunscreen for oily skin india under 500", "results": [
  {"url": "https://www.nykaa.com/dot-key-watermelon-sunscreen/p/1?utm_source=x", "engine": "google"},
  {"url": "https://www.reddit.com/r/IndianSkincareAddicts/comments/abc/", "engine": "brave"},
  {"url": "https://beminimalist.co/products/sunscreen", "engine": "bing"},
  {"url": "https://www.dotandkey.com/products/watermelon-sunscreen", "engine": "google"}
]}
```

Create `tests/test_probe.py`:

```python
import json
from datetime import date
from pathlib import Path

import httpx
import respx
from tenacity import wait_none

from shelfsight.config import load_settings, load_workspace
from shelfsight.probe import SearxProbe, candidate_rows

RESPONSE = {"response_id": "r1", "workspace": "dotandkey", "run_date": date(2026, 9, 21),
            "engine": "gemini", "prompt_id": "SUN-DISC-001"}


def fixture():
    return json.loads(Path("tests/fixtures/searxng.json").read_text(encoding="utf-8"))


@respx.mock
def test_search_sends_json_format_and_engines_and_truncates():
    route = respx.get(url__startswith="http://searx.test/search").mock(return_value=httpx.Response(200, json=fixture()))
    with httpx.Client() as c:
        hits = SearxProbe("http://searx.test/", ["google", "bing"], c, top_n=2, retry_wait=wait_none()).search("sunscreen")
    params = route.calls[0].request.url.params
    assert (params["q"], params["format"], params["engines"]) == ("sunscreen", "json", "google,bing")
    assert hits == [
        {"url": "https://www.nykaa.com/dot-key-watermelon-sunscreen/p/1?utm_source=x", "source_engine": "google"},
        {"url": "https://www.reddit.com/r/IndianSkincareAddicts/comments/abc/", "source_engine": "brave"},
    ]


@respx.mock
def test_candidate_rows_rank_and_attribute_results():
    respx.get(url__startswith="http://searx.test/search").mock(return_value=httpx.Response(200, json=fixture()))
    ws, s = load_workspace("dotandkey"), load_settings()
    with httpx.Client() as c:
        probe = SearxProbe("http://searx.test", ["google"], c, top_n=3, retry_wait=wait_none())
        rows = candidate_rows(probe, RESPONSE, ["q1", "q2", "q3"], max_queries=2,
                              brands=ws.brands, domain_types=s.domain_types)
    assert len(rows) == 6  # 2 queries × top 3
    assert {r["search_query"] for r in rows} == {"q1", "q2"}
    first = rows[:3]
    assert [r["position"] for r in first] == [1, 2, 3]
    assert [r["brand_id"] for r in first] == ["dotandkey", None, "minimalist"]
    assert [r["domain_type"] for r in first] == ["marketplace", "reddit", "brand"]
    assert first[0]["url"] == "https://nykaa.com/dot-key-watermelon-sunscreen/p/1"
    assert (first[0]["response_id"], first[0]["query_index"]) == ("r1", 0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.probe'`

- [ ] **Step 4: Implement `src/shelfsight/probe.py`**

```python
import httpx

from shelfsight.config import Brand
from shelfsight.engines.base import Pacer, raise_for_status, retrying
from shelfsight.store import utcnow
from shelfsight.urls import brand_for_url, domain_type, normalize_url, registered_domain


class SearxProbe:
    """Runs a model's own search query through SearxNG to approximate the candidate set it chose from."""

    def __init__(self, base_url: str, engines: list[str], client: httpx.Client, top_n: int = 20,
                 min_interval_s: float = 0, retry_wait=None):
        self.base_url, self.engines, self.top_n = base_url.rstrip("/"), engines, top_n
        self._client = client
        self._pacer = Pacer(min_interval_s)
        self._retrying = retrying(retry_wait)

    def search(self, query: str) -> list[dict]:
        """Top-N results in SearxNG's ranked order, as [{"url", "source_engine"}]."""
        data = self._retrying(self._get, query)
        return [{"url": r["url"], "source_engine": r.get("engine", "")} for r in data.get("results", [])[: self.top_n]]

    def _get(self, query: str) -> dict:
        self._pacer.wait()
        params = {"q": query, "format": "json", "engines": ",".join(self.engines)}
        r = self._client.get(f"{self.base_url}/search", params=params, timeout=30)
        raise_for_status(r)
        return r.json()


def candidate_rows(probe: SearxProbe, response: dict, queries: list[str], max_queries: int,
                   brands: list[Brand], domain_types: dict[str, list[str]]) -> list[dict]:
    brand_domains = {d for b in brands for d in b.domains}
    rows = []
    for query_index, query in enumerate(queries[:max_queries]):
        probed_at = utcnow()
        for position, hit in enumerate(probe.search(query), start=1):
            url = normalize_url(hit["url"])
            domain = registered_domain(url)
            rows.append({
                "response_id": response["response_id"], "workspace": response["workspace"],
                "run_date": response["run_date"], "engine": response["engine"], "prompt_id": response["prompt_id"],
                "search_query": query, "query_index": query_index, "position": position,
                "url": url, "domain": domain, "domain_type": domain_type(domain, brand_domains, domain_types),
                "source_engine": hit["source_engine"], "brand_id": brand_for_url(url, brands),
                "probed_at": probed_at,
            })
    return rows
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_probe.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/shelfsight/probe.py infra/searxng tests/test_probe.py tests/fixtures/searxng.json
git commit -m "feat: SearxNG retrieval probe over the model's own queries" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Collector run orchestration

**Files:**
- Create: `src/shelfsight/collect.py`
- Test: `tests/test_collect.py`

**Interfaces:**
- Consumes: `Settings`, `Workspace` (Task 1); `Prompt` (Task 2); `normalize_url`, `registered_domain`, `domain_type`, `brand_for_url` (Task 3); `Store`, `utcnow` (Task 4); `EngineResult`, `RetryableError` (Task 5); `candidate_rows` (Task 7). Engines are any object with `.ask(prompt, system_prompt, mode) -> EngineResult`; the probe is any object with `.search(query) -> list[dict]`.
- Produces: `run_collect(*, settings, workspace, prompts, engines: dict[str, engine], probe, store, run_date: date) -> dict` (the `runs` row). It writes the `raw_responses`, `citations`, `candidates` and `runs` tables. `raw_responses.status` is one of `ok | error | skipped_quota`, and `runs.status` is one of `finished | partial | failed`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_collect.py`:

```python
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


def test_interrupted_run_still_persists_collected_answers(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        run_collect(settings=settings(["no_web"]), workspace=load_workspace("dotandkey"),
                    prompts=[prompt("P1"), prompt("P2")], engines={"gemini": FakeEngine(NO_WEB, KeyboardInterrupt())},
                    probe=FakeProbe(), store=store, run_date=D)
    assert store.rows("SELECT prompt_id FROM raw_responses") == [{"prompt_id": "P1"}]
    assert store.rows("SELECT count(*) AS n FROM runs") == [{"n": 0}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_collect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.collect'`

- [ ] **Step 3: Implement `src/shelfsight/collect.py`**

```python
import json
import uuid
from datetime import date

from shelfsight.config import Settings, Workspace
from shelfsight.engines.base import EngineResult, RetryableError
from shelfsight.probe import candidate_rows
from shelfsight.prompts import Prompt
from shelfsight.store import Store, utcnow
from shelfsight.urls import brand_for_url, domain_type, normalize_url, registered_domain

_KEYS = ("response_id", "workspace", "run_date", "engine", "mode", "prompt_id")


def _citation_rows(row: dict, res: EngineResult, workspace: Workspace, settings: Settings) -> list[dict]:
    brand_domains = {d for b in workspace.brands for d in b.domains}
    out = []
    for c in res.citations:
        url = normalize_url(c.url)
        domain = registered_domain(url)
        out.append({k: row[k] for k in _KEYS} | {
            "position": c.position, "url": url, "domain": domain,
            "domain_type": domain_type(domain, brand_domains, settings.domain_types),
            "brand_id": brand_for_url(url, workspace.brands),
        })
    return out


def run_collect(*, settings: Settings, workspace: Workspace, prompts: list[Prompt], engines: dict,
                probe, store: Store, run_date: date) -> dict:
    """One daily run over prompts × engines × modes. Returns the runs row.

    A failed call becomes an error row, never an exception. A 429 that survives retries marks that
    engine exhausted, and its remaining calls are recorded as skipped_quota. Pass prompts high-priority
    first (active_prompts does this), so running out of quota costs the least important ones.
    """
    run_id, started = uuid.uuid4().hex, utcnow()
    plan = [(p, cfg, mode) for p in prompts for cfg in settings.engines if cfg.name in engines for mode in cfg.modes]
    responses, citations, candidates = [], [], []
    exhausted: set[str] = set()
    ok = errors = skipped = probe_errors = tokens_in = tokens_out = 0
    try:
        for p, cfg, mode in plan:
            row = {"response_id": uuid.uuid4().hex, "run_id": run_id, "workspace": workspace.id,
                   "run_date": run_date, "prompt_id": p.prompt_id, "prompt_version": p.version,
                   "intent": p.intent, "priority": p.priority, "engine": cfg.name, "model": cfg.model,
                   "mode": mode, "sample_n": 1, "system_prompt": settings.system_prompt,
                   "temperature": cfg.temperature, "created_at": utcnow()}
            if cfg.name in exhausted:
                responses.append(row | {"status": "skipped_quota"})
                skipped += 1
                continue
            try:
                res = engines[cfg.name].ask(p.prompt_text, settings.system_prompt, mode)
            except RetryableError as e:
                if e.status == 429:
                    exhausted.add(cfg.name)
                responses.append(row | {"status": "error", "error": str(e)})
                errors += 1
                continue
            except Exception as e:  # one bad call must not end the run
                responses.append(row | {"status": "error", "error": f"{type(e).__name__}: {e}"})
                errors += 1
                continue
            row |= {"status": "ok", "model": res.model, "response_text": res.text,
                    "web_search_queries": res.web_search_queries, "raw_json": json.dumps(res.raw),
                    "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                    "latency_ms": res.latency_ms}
            responses.append(row)
            ok += 1
            tokens_in += res.input_tokens
            tokens_out += res.output_tokens
            citations += _citation_rows(row, res, workspace, settings)
            if mode == "web" and res.web_search_queries:
                try:
                    candidates += candidate_rows(probe, row, res.web_search_queries,
                                                 settings.searxng.max_queries_per_response,
                                                 workspace.brands, settings.domain_types)
                except Exception:  # no candidate rows → the funnel says probe_missing, not a false not_eligible
                    probe_errors += 1
    finally:  # keep whatever was collected, even if the run is interrupted
        store.write("raw_responses", responses)
        store.write("citations", citations)
        store.write("candidates", candidates)
    run = {"run_id": run_id, "workspace": workspace.id, "run_date": run_date,
           "status": "finished" if ok == len(plan) else ("partial" if ok else "failed"),
           "started_at": started, "finished_at": utcnow(),
           "planned_calls": len(plan), "ok_calls": ok, "error_calls": errors, "skipped_calls": skipped,
           "probe_errors": probe_errors, "coverage": ok / len(plan) if plan else 0.0,
           "quota_exhausted": sorted(exhausted), "input_tokens": tokens_in, "output_tokens": tokens_out}
    store.write("runs", [run])
    return run
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_collect.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/shelfsight/collect.py tests/test_collect.py
git commit -m "feat: collector run with quota handling and coverage" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Dictionary brand matcher

**Files:**
- Create: `src/shelfsight/brand_match.py`
- Test: `tests/test_brand_match.py`

**Interfaces:**
- Consumes: `Brand`, `load_workspace` (Task 1); `normalize` (Task 2); the answer text in `tests/fixtures/gemini_grounded.json` (Task 5)
- Produces:
  - `FUZZY_MIN_SCORE = 93`, `FUZZY_MIN_LEN = 6`
  - `dictionary_hits(text: str, brands: list[Brand]) -> list[dict]`: `[{"brand_id", "alias", "word_index"}]`, one per brand, ordered by first appearance
  - `brand_id_for_name(name: str, brands: list[Brand]) -> str | None`: maps a name the LLM wrote, such as "Dot & Key Watermelon Sunscreen", to a tracked brand id

- [ ] **Step 1: Write the failing tests**

Create `tests/test_brand_match.py`:

```python
import json
from pathlib import Path

from shelfsight.brand_match import brand_id_for_name, dictionary_hits
from shelfsight.config import load_workspace


def fixture_text():
    data = json.loads(Path("tests/fixtures/gemini_grounded.json").read_text(encoding="utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def ids(text):
    return [h["brand_id"] for h in dictionary_hits(text, load_workspace("dotandkey").brands)]


def test_hits_are_ordered_by_first_appearance():
    assert ids(fixture_text()) == ["minimalist", "reequil", "dotandkey", "neutrogena"]


def test_alias_variants_all_match():
    for s in ["Re'equil", "reequil", "re equil", "RE’EQUIL", "Dot and Key", "dot n key"]:
        assert len(ids(f"try {s} today")) == 1, s


def test_fuzzy_typo_on_long_alias():
    assert ids("try nutrogena ultra sheer") == ["neutrogena"]


def test_short_alias_needs_exact_match():
    assert ids("lakmi sun expert") == []


def test_suffix_does_not_fuzzy_match():
    assert ids("a deconstructed routine") == []


def test_brand_id_for_name():
    brands = load_workspace("dotandkey").brands
    assert brand_id_for_name("Dot & Key Watermelon Cooling Sunscreen", brands) == "dotandkey"
    assert brand_id_for_name("Re'equil Oil Control", brands) == "reequil"
    assert brand_id_for_name("Foxtale", brands) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_brand_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.brand_match'`

- [ ] **Step 3: Implement `src/shelfsight/brand_match.py`**

```python
from rapidfuzz import fuzz

from shelfsight.config import Brand
from shelfsight.text import normalize

FUZZY_MIN_SCORE = 93  # "nutrogena" → neutrogena scores 94.7 and passes; "deconstructed" → deconstruct scores 91.7 and doesn't
FUZZY_MIN_LEN = 6     # shorter aliases fuzzy-match too much, so they must match exactly


def _aliases(brand: Brand) -> set[str]:
    return {normalize(a) for a in [brand.name, *brand.aliases]} - {""}


def _first_index(alias: str, words: list[str]) -> int | None:
    """Index of the first word window equal to (or, for long aliases, very close to) the alias."""
    n = alias.count(" ") + 1
    fuzzy = len(alias) >= FUZZY_MIN_LEN
    for i in range(len(words) - n + 1):
        window = " ".join(words[i:i + n])
        if window == alias or (fuzzy and fuzz.ratio(alias, window) >= FUZZY_MIN_SCORE):
            return i
    return None


def dictionary_hits(text: str, brands: list[Brand]) -> list[dict]:
    """Tracked brands found in the text, ordered by first appearance: [{"brand_id", "alias", "word_index"}]."""
    words = normalize(text).split()
    hits = []
    for b in brands:
        found = [(i, a) for a in _aliases(b) if (i := _first_index(a, words)) is not None]
        if found:
            i, alias = min(found)
            hits.append({"brand_id": b.id, "alias": alias, "word_index": i})
    return sorted(hits, key=lambda h: h["word_index"])


def brand_id_for_name(name: str, brands: list[Brand]) -> str | None:
    """Map a brand name as the LLM wrote it ("Dot & Key Watermelon Sunscreen") to a tracked brand id."""
    padded = f" {normalize(name)} "
    for b in brands:
        if any(f" {a} " in padded for a in _aliases(b)):
            return b.id
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_brand_match.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/shelfsight/brand_match.py tests/test_brand_match.py
git commit -m "feat: dictionary brand matcher with bounded fuzzy matching" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: LLM extraction, reconciliation and the accuracy check

**Files:**
- Create: `src/shelfsight/extract.py`, `scripts/eval_extractor.py`, `tests/fixtures/labelled/example-001.json`, `tests/fixtures/labelled/example-002.json`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `Brand`, `Workspace` (Task 1); `normalize` (Task 2); `Store` (Task 4); `RetryableError` (Task 5); `dictionary_hits`, `brand_id_for_name` (Task 9). The LLM is any object with `.generate_json(prompt: str, schema: dict) -> dict`, such as `GeminiEngine`.
- Produces:
  - `ExtractedBrand(name, rank: int | None, is_recommended: bool, sentiment, claims: list[str])`, `Extraction(brands: list[ExtractedBrand], answer_type)`
  - `SCHEMA` (Gemini `responseSchema`), `PROMPT` (format fields `brands`, `answer`)
  - `reconcile(response: dict, ext: Extraction, hits: list[dict], brands, extractor_version: str) -> list[dict]` (`mentions` rows)
  - `extract_mentions(response: dict, llm, brands, extractor_version: str) -> list[dict]`. `response` needs `response_id, workspace, run_date, engine, mode, prompt_id, response_text`.
  - `run_extract(*, store, workspace, llm, extractor_version: str, run_date) -> dict` returning `{"todo", "extracted", "failed", "mentions"}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_extract.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.extract'`

- [ ] **Step 3: Implement `src/shelfsight/extract.py`**

```python
from typing import Literal

from pydantic import BaseModel

from shelfsight.brand_match import brand_id_for_name, dictionary_hits
from shelfsight.config import Brand, Workspace
from shelfsight.engines.base import RetryableError
from shelfsight.store import Store
from shelfsight.text import normalize


class ExtractedBrand(BaseModel):
    name: str
    rank: int | None = None
    is_recommended: bool
    sentiment: Literal["positive", "neutral", "negative"]
    claims: list[str] = []


class Extraction(BaseModel):
    brands: list[ExtractedBrand]
    answer_type: Literal["list", "single", "comparison", "refusal", "other"]


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "brands": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"},
            "rank": {"type": "INTEGER", "nullable": True},
            "is_recommended": {"type": "BOOLEAN"},
            "sentiment": {"type": "STRING", "enum": ["positive", "neutral", "negative"]},
            "claims": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": ["name", "is_recommended", "sentiment", "claims"]}},
        "answer_type": {"type": "STRING", "enum": ["list", "single", "comparison", "refusal", "other"]},
    },
    "required": ["brands", "answer_type"],
}

PROMPT = """You extract brand mentions from a shopping assistant's answer.
Known brands in this category: {brands}.
List every brand the answer names, known or not, in the order the answer presents them.
rank: 1 for the first brand presented as a pick, 2 for the next, and so on; null if the brand is only mentioned in passing.
is_recommended: true only if the answer suggests the buyer choose it.
sentiment: how the answer describes the brand.
claims: short product claims attached to the brand, lowercase, e.g. "no white cast", "spf 50".

ANSWER:
{answer}"""

_KEYS = ("response_id", "workspace", "run_date", "engine", "mode", "prompt_id")


def reconcile(response: dict, ext: Extraction, hits: list[dict], brands: list[Brand],
              extractor_version: str) -> list[dict]:
    """Merge the LLM pass with the dictionary pass.

    - An LLM brand that maps to a tracked brand gets source "both" if the dictionary also saw it, else "llm".
    - An LLM brand that maps to nothing is kept with brand_id NULL: an early warning on new competitors.
    - A dictionary-only hit is added with rank and is_recommended NULL, unless the brand is ambiguous.
    - If nothing is found, one sentinel row (source "none") marks the response as extracted.
    """
    base = {k: response[k] for k in _KEYS} | {"extractor_version": extractor_version}
    ambiguous = {b.id for b in brands if b.ambiguous}
    dict_ids = {h["brand_id"] for h in hits}
    rows, seen = [], set()
    for eb in ext.brands:
        bid = brand_id_for_name(eb.name, brands)
        key = bid or f"raw:{normalize(eb.name)}"
        if key in seen:  # the LLM listed two products of one brand; keep the first, best-ranked
            continue
        seen.add(key)
        rows.append(base | {"brand_id": bid, "brand_raw": eb.name, "rank": eb.rank,
                            "is_recommended": eb.is_recommended, "sentiment": eb.sentiment,
                            "claims": eb.claims, "source": "both" if bid in dict_ids else "llm"})
    for h in hits:
        if h["brand_id"] in seen or h["brand_id"] in ambiguous:
            continue
        seen.add(h["brand_id"])
        rows.append(base | {"brand_id": h["brand_id"], "brand_raw": h["alias"], "rank": None,
                            "is_recommended": None, "sentiment": None, "claims": [], "source": "dictionary"})
    return rows or [base | {"brand_id": None, "brand_raw": None, "claims": [], "source": "none"}]


def extract_mentions(response: dict, llm, brands: list[Brand], extractor_version: str) -> list[dict]:
    prompt = PROMPT.format(brands=", ".join(b.name for b in brands), answer=response["response_text"])
    ext = Extraction.model_validate(llm.generate_json(prompt, SCHEMA))
    return reconcile(response, ext, dictionary_hits(response["response_text"], brands), brands, extractor_version)


def run_extract(*, store: Store, workspace: Workspace, llm, extractor_version: str, run_date) -> dict:
    """Extract every ok response for the day that has no rows yet at this extractor_version."""
    done = {r["response_id"] for r in store.rows(
        "SELECT DISTINCT response_id FROM mentions WHERE workspace = ? AND run_date = ? AND extractor_version = ?",
        [workspace.id, run_date, extractor_version])}
    todo = [r for r in store.rows(
        "SELECT response_id, workspace, run_date, engine, mode, prompt_id, response_text FROM raw_responses "
        "WHERE workspace = ? AND run_date = ? AND status = 'ok' ORDER BY response_id",
        [workspace.id, run_date]) if r["response_id"] not in done]
    rows, extracted, failed = [], 0, 0
    for r in todo:
        try:
            rows += extract_mentions(r, llm, workspace.brands, extractor_version)
            extracted += 1
        except RetryableError as e:
            failed += 1
            if e.status == 429:
                break  # quota gone: the rest waits for the next extract run
        except Exception:  # invalid JSON, schema mismatch or API failure: retried on the next extract run
            failed += 1
    store.write("mentions", rows)
    return {"todo": len(todo), "extracted": extracted, "failed": failed, "mentions": len(rows)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: 5 passed

- [ ] **Step 5: Add the labelled examples and the live accuracy check**

Create `tests/fixtures/labelled/example-001.json`:

```json
{"workspace": "dotandkey",
 "response_text": "For oily skin in India, try:\n1. **Minimalist SPF 50** - lightweight, no white cast.\n2. **Re'equil Oil Control Sunscreen** - matte finish.\n3. **Dot & Key Watermelon Cooling Sunscreen** - gel texture, though some find it sticky.\nNeutrogena Ultra Sheer is also widely available.",
 "expected": [{"brand_id": "minimalist", "rank": 1}, {"brand_id": "reequil", "rank": 2},
              {"brand_id": "dotandkey", "rank": 3}, {"brand_id": "neutrogena", "rank": null}]}
```

Create `tests/fixtures/labelled/example-002.json`:

```json
{"workspace": "dotandkey",
 "response_text": "Agar aapki skin oily hai toh The Derma Co 1% Hyaluronic Sunscreen Aqua Gel accha option hai. Lakme Sun Expert bhi budget mein theek hai, lekin white cast chhod sakta hai.",
 "expected": [{"brand_id": "dermaco", "rank": 1}, {"brand_id": "lakme", "rank": 2}]}
```

Create `scripts/eval_extractor.py`:

```python
"""LIVE check of extractor accuracy against hand-labelled answers. Calls Gemini, so it is not run in CI.

  uv run python scripts/eval_extractor.py
      score every tests/fixtures/labelled/*.json; exit 1 if brand recall < 95%
  uv run python scripts/eval_extractor.py --export 40 --workspace dotandkey --date 2026-09-28
      dump 40 random real answers into tests/fixtures/labelled/ for a person to label
"""
import argparse
import json
import os
import random
import sys
from datetime import date
from pathlib import Path

import httpx

from shelfsight.config import load_settings, load_workspace
from shelfsight.engines.gemini import GeminiEngine
from shelfsight.extract import extract_mentions
from shelfsight.store import Store

LABELLED = Path("tests/fixtures/labelled")
TARGET_RECALL = 0.95


def export(n: int, workspace: str, day: date, lake: str) -> int:
    rows = Store(lake).rows("SELECT response_id, response_text FROM raw_responses "
                            "WHERE workspace = ? AND run_date = ? AND status = 'ok'", [workspace, day])
    picked = random.sample(rows, min(n, len(rows)))
    LABELLED.mkdir(parents=True, exist_ok=True)
    for r in picked:
        case = {"workspace": workspace, "response_text": r["response_text"], "expected": []}
        (LABELLED / f"{r['response_id']}.json").write_text(json.dumps(case, indent=2, ensure_ascii=False),
                                                          encoding="utf-8")
    print(f"wrote {len(picked)} files to {LABELLED}. Fill each 'expected' with "
          '[{"brand_id": ..., "rank": ...}] using rank null for brands mentioned only in passing.')
    return 0


def score() -> int:
    s = load_settings()
    found = total = rank_ok = rank_total = 0
    with httpx.Client() as c:
        llm = GeminiEngine(os.environ["GEMINI_API_KEY"], s.extractor.model, c, min_interval_s=6.5)
        for f in sorted(LABELLED.glob("*.json")):
            case = json.loads(f.read_text(encoding="utf-8"))
            ws = load_workspace(case["workspace"])
            resp = {"response_id": f.stem, "workspace": ws.id, "run_date": None, "engine": "eval",
                    "mode": "eval", "prompt_id": f.stem, "response_text": case["response_text"]}
            got = {m["brand_id"]: m["rank"] for m in extract_mentions(resp, llm, ws.brands, "eval") if m["brand_id"]}
            for exp in case["expected"]:
                total += 1
                if exp["brand_id"] not in got:
                    print(f"  MISS {f.name}: {exp['brand_id']}")
                    continue
                found += 1
                if exp.get("rank") is not None:
                    rank_total += 1
                    rank_ok += got[exp["brand_id"]] == exp["rank"]
    recall = found / total if total else 0.0
    print(f"brand recall {found}/{total} = {recall:.1%} (target {TARGET_RECALL:.0%}); "
          f"rank accuracy {rank_ok}/{rank_total}")
    return 0 if recall >= TARGET_RECALL else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=int)
    ap.add_argument("--workspace")
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--lake", default="data/lake")
    a = ap.parse_args()
    sys.exit(export(a.export, a.workspace, a.date, a.lake) if a.export else score())
```

The labelled set grows to 40 answers in Task 12, Step 6, once real answers exist.

- [ ] **Step 6: Commit**

```bash
git add src/shelfsight/extract.py scripts/eval_extractor.py tests/test_extract.py tests/fixtures/labelled
git commit -m "feat: LLM extraction reconciled with the dictionary pass" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Funnel joiner and diagnosis

**Files:**
- Create: `src/shelfsight/funnel.py`
- Test: `tests/test_funnel.py`

**Interfaces:**
- Consumes: `Settings`, `Workspace` (Task 1); `Store` (Task 4); the `raw_responses`, `candidates`, `citations` tables (Task 8) and the `mentions` table (Task 10)
- Produces:
  - `DIAGNOSES` (tuple of the 8 diagnosis strings, in ladder order)
  - `diagnose(*, retrieval: bool, probed: bool, is_eligible: bool, is_cited: bool, is_named: bool, is_recommended: bool | None, rank: int | None) -> str`
  - `run_funnel(*, store, workspace, settings, run_date) -> dict` returning `{"status": "ok" | "skipped", "rows": int, "client": {diagnosis: count}}`. It writes one `funnel` row per extracted ok response × tracked brand, and skips if rows already exist for this `(extractor_version, joiner_version)`.

Rules for the output columns:
- `retrieval` is true only for web mode where the model actually searched (non-empty `web_search_queries`).
- `is_cited` is NULL when there's no retrieval.
- `is_eligible` and `eligible_n` are NULL unless the response was probed, i.e. it has at least one candidate row.
- Responses with no `mentions` rows at the current `extractor_version` are skipped. They haven't been extracted yet, and joining them would falsely diagnose "not named".

- [ ] **Step 1: Write the failing tests**

Create `tests/test_funnel.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_funnel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.funnel'`

- [ ] **Step 3: Implement `src/shelfsight/funnel.py`**

```python
from collections import Counter
from datetime import date

from shelfsight.config import Settings, Workspace
from shelfsight.store import Store

DIAGNOSES = ("winning", "recommended_not_top3", "named_not_recommended", "not_named",
             "cited_not_named", "probe_missing", "eligible_not_cited", "not_eligible")


def diagnose(*, retrieval: bool, probed: bool, is_eligible: bool, is_cited: bool, is_named: bool,
             is_recommended: bool | None, rank: int | None) -> str:
    """The first matching rung wins. retrieval is False for no_web answers and for web answers without a search."""
    if is_recommended and rank is not None and rank <= 3:
        return "winning"
    if is_recommended:
        return "recommended_not_top3"
    if is_named:
        return "named_not_recommended"
    if not retrieval:
        return "not_named"
    if is_cited:
        return "cited_not_named"
    if not probed:
        return "probe_missing"
    if is_eligible:
        return "eligible_not_cited"
    return "not_eligible"


JOIN_SQL = """
WITH extracted AS (
    SELECT DISTINCT response_id FROM mentions
    WHERE workspace = $ws AND run_date = $d AND extractor_version = $ev
),
resp AS (
    SELECT response_id, workspace, run_date, engine, mode, prompt_id, prompt_version, intent, priority,
           coalesce(len(web_search_queries), 0) > 0 AS searched
    FROM raw_responses
    WHERE workspace = $ws AND run_date = $d AND status = 'ok'
      AND response_id IN (SELECT response_id FROM extracted)
),
probed AS (SELECT DISTINCT response_id FROM candidates WHERE workspace = $ws AND run_date = $d),
elig AS (
    SELECT response_id, brand_id, count(*) AS eligible_n FROM candidates
    WHERE workspace = $ws AND run_date = $d AND brand_id IS NOT NULL AND position <= $top_n
    GROUP BY 1, 2
),
cited AS (
    SELECT DISTINCT response_id, brand_id FROM citations
    WHERE workspace = $ws AND run_date = $d AND brand_id IS NOT NULL
),
ment AS (
    SELECT response_id, brand_id, bool_or(is_recommended) AS is_recommended, min(rank) AS rank FROM mentions
    WHERE workspace = $ws AND run_date = $d AND extractor_version = $ev AND brand_id IS NOT NULL
    GROUP BY 1, 2
)
SELECT r.*, b.brand_id,
       p.response_id IS NOT NULL AS probed,
       e.response_id IS NOT NULL AS is_eligible,
       coalesce(e.eligible_n, 0) AS eligible_n,
       c.response_id IS NOT NULL AS is_cited,
       m.response_id IS NOT NULL AS is_named,
       m.is_recommended, m.rank
FROM resp r
CROSS JOIN (SELECT unnest($brand_ids::VARCHAR[]) AS brand_id) b
LEFT JOIN probed p ON p.response_id = r.response_id
LEFT JOIN elig e ON e.response_id = r.response_id AND e.brand_id = b.brand_id
LEFT JOIN cited c ON c.response_id = r.response_id AND c.brand_id = b.brand_id
LEFT JOIN ment m ON m.response_id = r.response_id AND m.brand_id = b.brand_id
"""


def run_funnel(*, store: Store, workspace: Workspace, settings: Settings, run_date: date) -> dict:
    """Diagnose every extracted response × tracked brand for one day. Idempotent per (extractor, joiner) version."""
    ev, jv = settings.extractor.version, settings.joiner_version
    existing = store.rows("SELECT 1 FROM funnel WHERE workspace = $ws AND run_date = $d "
                          "AND extractor_version = $ev AND joiner_version = $jv LIMIT 1",
                          {"ws": workspace.id, "d": run_date, "ev": ev, "jv": jv})
    if existing:
        return {"status": "skipped", "rows": 0, "client": {}}
    joined = store.rows(JOIN_SQL, {"ws": workspace.id, "d": run_date, "ev": ev,
                                   "top_n": settings.eligible_top_n,
                                   "brand_ids": [b.id for b in workspace.brands]})
    client_id = workspace.client.id
    out, client_counts = [], Counter()
    for r in joined:
        retrieval = r["mode"] == "web" and r["searched"]
        probed = retrieval and r["probed"]
        diagnosis = diagnose(retrieval=retrieval, probed=probed, is_eligible=r["is_eligible"],
                             is_cited=r["is_cited"], is_named=r["is_named"],
                             is_recommended=r["is_recommended"], rank=r["rank"])
        out.append({
            "workspace": r["workspace"], "run_date": r["run_date"], "engine": r["engine"], "mode": r["mode"],
            "prompt_id": r["prompt_id"], "prompt_version": r["prompt_version"], "intent": r["intent"],
            "priority": r["priority"], "brand_id": r["brand_id"], "is_client": r["brand_id"] == client_id,
            "response_id": r["response_id"],
            "is_eligible": r["is_eligible"] if probed else None,
            "is_cited": r["is_cited"] if retrieval else None,
            "is_named": r["is_named"], "is_recommended": r["is_recommended"], "rank": r["rank"],
            "eligible_n": r["eligible_n"] if probed else None,
            "diagnosis": diagnosis, "extractor_version": ev, "joiner_version": jv,
        })
        if r["brand_id"] == client_id:
            client_counts[diagnosis] += 1
    store.write("funnel", out)
    return {"status": "ok", "rows": len(out), "client": dict(client_counts)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_funnel.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/shelfsight/funnel.py tests/test_funnel.py
git commit -m "feat: funnel joiner with retrieval-aware diagnosis ladder" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Report, CLI and the first live run

**Files:**
- Create: `src/shelfsight/report.py`, `src/shelfsight/cli.py`, `src/shelfsight/__main__.py`, `scripts/smoke_gemini.py`, `.github/workflows/ci.yml`
- Modify: `pyproject.toml` (add `[project.scripts]`), `README.md` (replace the one-line README from the initial GitHub commit)
- Test: `tests/test_report.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above. Exact names: `load_settings`, `load_workspace`, `load_prompts`, `validate_prompts`, `active_prompts`, `Store`, `GeminiEngine`, `GroqEngine`, `SearxProbe`, `run_collect`, `run_extract`, `run_funnel`.
- Produces:
  - `MEANING: dict[str, str]` (one line per diagnosis)
  - `diagnosis_summary(store, workspace, settings, run_date) -> str` (Markdown)
  - `main(argv: list[str] | None = None) -> int` with subcommands `validate-prompts | collect | extract | funnel | report`. Global flags `--config` and `--lake` go before the subcommand. Per-command flags: `--workspace` (required), `--date` (IST, default today), `--prompts`, `--limit`.
  - Exit codes: 0 ok; 1 invalid prompts or a failed run; 2 missing API key.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report.py`:

```python
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
```

Create `tests/test_cli.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_report.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfsight.report'`

- [ ] **Step 3: Implement `src/shelfsight/report.py`**

```python
from datetime import date

from shelfsight.config import Settings, Workspace
from shelfsight.store import Store

MEANING = {
    "winning": "Recommended in the top 3",
    "recommended_not_top3": "Recommended, but below the top 3: strengthen the claim",
    "named_not_recommended": "Named, not recommended: the claim the buyer asked about is missing",
    "not_named": "Not named, and no retrieval to diagnose (no-web answer, or the model didn't search)",
    "cited_not_named": "Our page was cited, but the answer names a rival: our page feeds their win",
    "probe_missing": "The retrieval probe failed: re-probe before drawing conclusions",
    "eligible_not_cited": "In the candidate set, but not chosen: format, authority, extractability",
    "not_eligible": "Not in the candidate set: content gap, no page for this question",
}


def diagnosis_summary(store: Store, workspace: Workspace, settings: Settings, run_date: date) -> str:
    rows = store.rows("""
        SELECT diagnosis, count(*) AS answers, list_sort(list(DISTINCT prompt_id)) AS prompts
        FROM funnel
        WHERE workspace = $ws AND run_date = $d AND is_client
          AND extractor_version = $ev AND joiner_version = $jv
        GROUP BY diagnosis
        ORDER BY answers DESC, diagnosis
    """, {"ws": workspace.id, "d": run_date, "ev": settings.extractor.version, "jv": settings.joiner_version})
    if not rows:
        return f"No funnel rows for {workspace.id} on {run_date}. Run `funnel` first."
    name = workspace.client.name
    lines = [f"# ShelfSight: {name}, {run_date}", "",
             f"{sum(r['answers'] for r in rows)} answers diagnosed for {name}.", "",
             "| Diagnosis | Answers | Meaning | Prompts |", "|---|---|---|---|"]
    lines += [f"| {r['diagnosis']} | {r['answers']} | {MEANING[r['diagnosis']]} | {', '.join(r['prompts'])} |"
              for r in rows]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Implement `src/shelfsight/cli.py` and `src/shelfsight/__main__.py`**

`src/shelfsight/cli.py`:

```python
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

from shelfsight.collect import run_collect
from shelfsight.config import load_settings, load_workspace
from shelfsight.engines.gemini import GeminiEngine
from shelfsight.engines.groq import GroqEngine
from shelfsight.extract import run_extract
from shelfsight.funnel import run_funnel
from shelfsight.probe import SearxProbe
from shelfsight.prompts import active_prompts, load_prompts, validate_prompts
from shelfsight.report import diagnosis_summary
from shelfsight.store import Store

IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset: no tzdata needed on Windows, and India has no DST
_ENGINES = {"gemini": GeminiEngine, "groq": GroqEngine}


def today_ist() -> date:
    return datetime.now(IST).date()


def build_engines(settings, client: httpx.Client) -> dict:
    engines = {}
    for cfg in settings.engines:
        key = os.environ.get(f"{cfg.name.upper()}_API_KEY")
        if not key:
            print(f"warning: {cfg.name.upper()}_API_KEY is not set; skipping {cfg.name}", file=sys.stderr)
            continue
        engines[cfg.name] = _ENGINES[cfg.name](key, cfg.model, client, min_interval_s=cfg.min_interval_s,
                                               temperature=cfg.temperature)
    return engines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shelfsight")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--lake", default="data/lake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("validate-prompts", "collect", "extract", "funnel", "report"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", required=True)
        p.add_argument("--date", type=date.fromisoformat, default=None, help="IST run date; default today")
        p.add_argument("--prompts", default="data/prompts_seed.csv")
        p.add_argument("--limit", type=int, default=None, help="collect: only the first N prompts")
    args = ap.parse_args(argv)

    settings, ws, store = load_settings(args.config), load_workspace(args.workspace), Store(args.lake)
    day = args.date or today_ist()

    if args.cmd == "validate-prompts":
        errors = validate_prompts(load_prompts(args.prompts), ws)
        for e in errors:
            print(e)
        return 1 if errors else 0
    if args.cmd == "report":
        print(diagnosis_summary(store, ws, settings, day))
        return 0
    if args.cmd == "funnel":
        print(json.dumps(run_funnel(store=store, workspace=ws, settings=settings, run_date=day)))
        return 0

    with httpx.Client() as client:
        if args.cmd == "collect":
            prompts = load_prompts(args.prompts)
            errors = validate_prompts(prompts, ws)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            engines = build_engines(settings, client)
            if not engines:
                print("error: no engine has an API key; set GEMINI_API_KEY and/or GROQ_API_KEY", file=sys.stderr)
                return 2
            sx = settings.searxng
            probe = SearxProbe(sx.url, sx.engines, client, top_n=sx.probe_top_n, min_interval_s=sx.min_interval_s)
            run = run_collect(settings=settings, workspace=ws, prompts=active_prompts(prompts)[: args.limit],
                              engines=engines, probe=probe, store=store, run_date=day)
            print(json.dumps(run, default=str))
            return 1 if run["status"] == "failed" else 0

        key = os.environ.get("GEMINI_API_KEY")  # extract
        if not key:
            print("error: GEMINI_API_KEY is required for extract", file=sys.stderr)
            return 2
        pace = next(e.min_interval_s for e in settings.engines if e.name == "gemini")
        llm = GeminiEngine(key, settings.extractor.model, client, min_interval_s=pace)
        print(json.dumps(run_extract(store=store, workspace=ws, llm=llm,
                                     extractor_version=settings.extractor.version, run_date=day)))
        return 0
```

`src/shelfsight/__main__.py`:

```python
from shelfsight.cli import main

raise SystemExit(main())
```

Add to `pyproject.toml`, below `dependencies`:

```toml
[project.scripts]
shelfsight = "shelfsight.cli:main"
```

Then run `uv sync` so the `shelfsight` command is installed.

- [ ] **Step 5: Run the full offline suite**

Run: `uv run pytest -q`
Expected: 73 passed, no network access

- [ ] **Step 6: First live run (manual; needs free API keys and Docker)**

Get a Gemini key from Google AI Studio and a Groq key from console.groq.com. Put both in the shell. Never put them in a file inside the repo.

```bash
export GEMINI_API_KEY=...   # PowerShell: $env:GEMINI_API_KEY="..."
export GROQ_API_KEY=...
docker compose -f infra/searxng/docker-compose.yml up -d
uv run python scripts/smoke_gemini.py
```

The last command runs `scripts/smoke_gemini.py`. Create it before running the block above:

```python
"""LIVE: confirm the pinned Gemini model still returns webSearchQueries and groundingChunks. Run after any model change."""
import os
import sys

import httpx

from shelfsight.config import load_settings
from shelfsight.engines.gemini import GeminiEngine

s = load_settings()
cfg = next(e for e in s.engines if e.name == "gemini")
with httpx.Client() as c:
    res = GeminiEngine(os.environ["GEMINI_API_KEY"], cfg.model, c).ask(
        "best sunscreen for oily skin in india under 500", s.system_prompt, "web")
gm = res.raw["candidates"][0].get("groundingMetadata", {})
print("model:", cfg.model)
print("webSearchQueries:", gm.get("webSearchQueries"))
print("groundingChunks:", len(gm.get("groundingChunks", [])))
for cit in res.citations:
    print(f"  {cit.position}. {cit.url}")
if not gm.get("webSearchQueries") or not gm.get("groundingChunks"):
    sys.exit(f"FAIL: {cfg.model} is missing webSearchQueries or groundingChunks. Pin another model in config/config.yaml.")
print("OK")
```

Expected: `OK`, at least one query, and resolved citation URLs (not `vertexaisearch...` links). If it prints `FAIL`, change `engines[gemini].model` and `extractor.model` in `config/config.yaml` and run it again. The whole Eligible stage depends on this passing.

Then run a 3-prompt end-to-end:

```bash
uv run shelfsight collect --workspace dotandkey --limit 3
uv run shelfsight extract --workspace dotandkey
uv run shelfsight funnel --workspace dotandkey
uv run shelfsight report --workspace dotandkey
```

Expected:
- `collect` prints a runs row with `planned_calls` 9 (3 prompts × Gemini web + Gemini no-web + Groq no-web), `status` finished, and `probe_errors` 0.
- `extract` prints `failed` 0.
- `funnel` prints `rows` 81 (9 answers × 9 brands).
- `report` prints a Markdown table of Dot & Key diagnoses.

A non-zero `probe_errors` means SearxNG isn't reachable, or its upstream engines are blocking you.

- [ ] **Step 7: Build the 40-answer labelled set**

After the first full-day run (no `--limit`):

```bash
uv run python scripts/eval_extractor.py --export 40 --workspace dotandkey --date <that run date>
```

Label the `expected` list in each exported file by hand, then score:

```bash
uv run python scripts/eval_extractor.py
```

Expected: `brand recall ... (target 95%)` and exit code 0. If recall is below 95%, look at the `MISS` lines. Add the missing aliases to `config/workspaces/dotandkey.yaml` or tighten `PROMPT` in `extract.py`, bump `extractor.version` in `config/config.yaml`, then re-run `extract` and `funnel`. Stored answers are re-used, so nothing is collected again.

- [ ] **Step 8: README and CI**

Replace the contents of `README.md` (currently just `# ShelfSight`) with:

```markdown
# ShelfSight

AEO diagnosis for agencies: why AI assistants do or don't recommend a client brand, measured
through the retrieval chain the assistant actually ran. Design:
`docs/superpowers/specs/2026-09-21-shelfsight-design.md`.

## Run locally

    uv sync
    docker compose -f infra/searxng/docker-compose.yml up -d
    export GEMINI_API_KEY=...      # Google AI Studio, free tier
    export GROQ_API_KEY=...        # console.groq.com, free tier
    uv run python scripts/smoke_gemini.py
    uv run shelfsight collect --workspace dotandkey --limit 3
    uv run shelfsight extract --workspace dotandkey
    uv run shelfsight funnel  --workspace dotandkey
    uv run shelfsight report  --workspace dotandkey

Data lands in `data/lake/` (git-ignored). `uv run pytest` runs the offline test suite.
Live checks are in `scripts/` and are never run in CI.
```

Create `.github/workflows/ci.yml`:

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run pytest -q
```

- [ ] **Step 9: Commit**

```bash
git add src/shelfsight/report.py src/shelfsight/cli.py src/shelfsight/__main__.py scripts/smoke_gemini.py \
        pyproject.toml uv.lock README.md .github tests/test_report.py tests/test_cli.py tests/fixtures/labelled
git commit -m "feat: CLI, diagnosis report and live smoke check" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Done when

- `uv run pytest -q` shows 73 passed, offline.
- `scripts/smoke_gemini.py` prints `OK` for the pinned model.
- A real day's run produces a Dot & Key diagnosis table, and `scripts/eval_extractor.py` reports ≥ 95% brand recall on 40 labelled answers.

Next: Plan 2 (fix verification), then Plan 3 (AWS + n8n), which can start in parallel once this plan's lake format is stable.

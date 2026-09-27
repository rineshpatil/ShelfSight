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

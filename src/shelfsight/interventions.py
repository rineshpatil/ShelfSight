"""Local-only, immutable intervention ledger. No website publishing or network clients."""
import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IST = timezone(timedelta(hours=5, minutes=30))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["observation", "proxy", "verified_brand"]
    reference: str = Field(min_length=1)
    snapshot: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    captured_at: datetime
    verified_by: str | None = None

    @model_validator(mode="after")
    def validate_quote(self):
        if self.quote not in self.snapshot:
            raise ValueError("evidence quote must occur in retained snapshot")
        if self.captured_at.tzinfo is None:
            raise ValueError("evidence timestamps must include timezone")
        if self.kind == "verified_brand" and not self.verified_by:
            raise ValueError("brand facts require a named verifier")
        return self


class Question(StrictModel):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    text: str = Field(min_length=1)
    page: str = Field(pattern=r"^https?://")


class MeasurementPlan(StrictModel):
    baseline_dates: list[date] = Field(min_length=1)
    post_dates: list[date] = Field(min_length=1)
    targets: list[Question] = Field(min_length=1)
    comparisons: list[Question] = Field(min_length=1)
    engine: str
    model: str
    mode: Literal["web", "no_web"]
    temperature: float
    system_prompt: str
    collection_settings: str = Field(min_length=1)
    extractor_version: str
    joiner_version: str
    minimum_days: int = Field(default=7, ge=2)

    @model_validator(mode="after")
    def validate_plan(self):
        for values in (self.baseline_dates, self.post_dates):
            if len(set(values)) != len(values):
                raise ValueError("duplicate dates")
        if max(self.baseline_dates) >= min(self.post_dates):
            raise ValueError("baseline must precede post dates")
        ids = [q.id for q in self.targets + self.comparisons]
        if len(set(ids)) != len(ids):
            raise ValueError("target and comparison prompts must be distinct")
        if {q.page for q in self.targets} & {q.page for q in self.comparisons}:
            raise ValueError("comparison pages must be separate from target pages")
        json.loads(self.collection_settings)
        return self


class Proposal(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    workspace: str = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    product: str = Field(min_length=1)
    page: str = Field(pattern=r"^https?://")
    observed_issue: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=2)
    supporting_fact_ids: list[str] = Field(min_length=1)
    before: str
    after: str = Field(min_length=1)
    unknowns: list[str]
    plan: MeasurementPlan
    fixture: bool = False

    @model_validator(mode="after")
    def supported_copy(self):
        evidence = {e.id: e for e in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("duplicate evidence ids")
        if not any(e.kind == "observation" for e in self.evidence):
            raise ValueError("a direct observation is required")
        if any(i not in evidence for i in self.supporting_fact_ids):
            raise ValueError("supporting fact id is missing")
        facts = [evidence[i] for i in self.supporting_fact_ids]
        if any(e.kind != "verified_brand" or e.reference != self.page for e in facts):
            raise ValueError("copy requires verified brand facts for this exact product page")
        if self.after != "\n".join(e.quote for e in facts):
            raise ValueError("local publisher supports exact verified fact copy only; unsupported prose is blocked")
        if any(q.page != self.page for q in self.plan.targets):
            raise ValueError("target questions must reference the intervention page")
        return self


class Ledger:
    """SQLite transactions prevent duplicate transitions. Actors are local attestations, not authenticated identities."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT, seq INTEGER, kind TEXT, at TEXT, actor TEXT, payload TEXT, PRIMARY KEY(id,seq))")

    def connect(self):
        return sqlite3.connect(self.path)

    def history(self, intervention_id):
        with self.connect() as db:
            return [dict(zip(("id", "seq", "kind", "at", "actor", "payload"), r)) for r in
                    db.execute("SELECT * FROM events WHERE id=? ORDER BY seq", (intervention_id,))]

    def transition(self, intervention_id, kind, actor, payload=None, at=None, store=None):
        if not actor.strip():
            raise ValueError("named actor required")
        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("timezone required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = list(db.execute("SELECT kind, at, payload FROM events WHERE id=? ORDER BY seq", (intervention_id,)))
            expected = {"proposed": [], "approved": ["proposed"], "published_local": ["proposed", "approved"]}
            if kind not in expected or [r[0] for r in rows] != expected[kind]:
                raise ValueError("invalid or duplicate transition")
            if rows and now < datetime.fromisoformat(rows[-1][1]):
                raise ValueError("timestamps must be chronological")
            if kind == "proposed":
                proposal = Proposal.model_validate(payload)
                if proposal.id != intervention_id:
                    raise ValueError("id mismatch")
                if max(e.captured_at for e in proposal.evidence) > now:
                    raise ValueError("evidence cannot be captured after proposal")
                if now.astimezone(IST).date() >= min(proposal.plan.post_dates):
                    raise ValueError("measurement plan must be recorded before post observations")
                if store is None or store.is_remote:
                    raise ValueError("a local observation lake is required")
                for evidence in proposal.evidence:
                    if evidence.kind == "observation":
                        matches = store.rows("SELECT response_text, prompt_id FROM raw_responses WHERE workspace=? AND response_id=? AND status='ok'",
                                             [proposal.workspace, evidence.reference])
                        if len(matches) != 1 or matches[0]["response_text"] != evidence.snapshot:
                            raise ValueError("observation must match a stored response snapshot")
                        if matches[0]["prompt_id"] not in {q.id for q in proposal.plan.targets}:
                            raise ValueError("observation must support a target question")
                data = proposal.model_dump(mode="json")
            else:
                proposal = Proposal.model_validate_json(rows[0][2])
                data = {"approved_content_sha256": digest(proposal.after)}
                if kind == "published_local":
                    # Immutable SQLite artifact is the actual locally published copy; never a website receipt.
                    if not max(proposal.plan.baseline_dates) < now.astimezone(IST).date() < min(proposal.plan.post_dates):
                        raise ValueError("publication must separate baseline and post windows")
                    data |= {"content": proposal.after, "before": proposal.before,
                             "destination": "local-ledger-artifact", "external_publication": False}
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (intervention_id, len(rows)+1, kind,
                       now.isoformat(), actor, json.dumps(data, sort_keys=True)))
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("propose"); p.add_argument("file"); p.add_argument("--actor", required=True); p.add_argument("--lake", required=True)
    for name in ("approve", "publish-local", "show"):
        p = sub.add_parser(name); p.add_argument("id")
        if name != "show": p.add_argument("--actor", required=True)
    args = parser.parse_args()
    ledger = Ledger(args.ledger)
    if args.command == "propose":
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        from shelfsight.store import Store
        result = ledger.transition(data["id"], "proposed", args.actor, data, store=Store(args.lake))
    elif args.command == "show":
        result = ledger.history(args.id)
    else:
        result = ledger.transition(args.id, "approved" if args.command == "approve" else "published_local", args.actor)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

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

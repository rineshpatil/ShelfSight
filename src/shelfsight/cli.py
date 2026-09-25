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
from shelfsight.engines.nova import NovaEngine
from shelfsight.extract import run_extract
from shelfsight.funnel import run_funnel
from shelfsight.probe import SearxProbe
from shelfsight.prompts import active_prompts, load_prompts, validate_prompts
from shelfsight.report import diagnosis_summary
from shelfsight.store import Store

IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset: no tzdata needed on Windows, and India has no DST
_KEYED_ENGINES = {"gemini": GeminiEngine, "groq": GroqEngine}


def today_ist() -> date:
    return datetime.now(IST).date()


def bedrock_client(region: str):
    """bedrock-runtime client using the standard AWS credential chain (aws login, profile, or role)."""
    import boto3
    from botocore.config import Config

    # botocore retries off: tenacity in the engine retries, and throttling must surface as a 429 once
    return boto3.client("bedrock-runtime", region_name=region,
                        config=Config(read_timeout=300, retries={"max_attempts": 1, "mode": "standard"}))


def build_engines(settings, client: httpx.Client) -> dict:
    engines = {}
    for cfg in settings.engines:
        if cfg.name == "nova":
            engines["nova"] = NovaEngine(cfg.model, bedrock_client(cfg.region), min_interval_s=cfg.min_interval_s,
                                         temperature=cfg.temperature)
            continue
        key = os.environ.get(f"{cfg.name.upper()}_API_KEY")
        if not key:
            print(f"warning: {cfg.name.upper()}_API_KEY is not set; skipping {cfg.name}", file=sys.stderr)
            continue
        engines[cfg.name] = _KEYED_ENGINES[cfg.name](key, cfg.model, client, min_interval_s=cfg.min_interval_s,
                                                     temperature=cfg.temperature)
    return engines


def build_llm(settings, client: httpx.Client):
    """The extraction model named by settings.extractor, or None when its credentials are missing."""
    ex = settings.extractor
    if ex.engine == "nova":
        return NovaEngine(ex.model, bedrock_client(ex.region), min_interval_s=1.0)
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    pace = next((e.min_interval_s for e in settings.engines if e.name == "gemini"), 6.5)
    return GeminiEngine(key, ex.model, client, min_interval_s=pace)


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
    serve = sub.add_parser("serve", help="HTTP bridge for n8n on 127.0.0.1 (needs SHELFSIGHT_BRIDGE_TOKEN)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        token = os.environ.get("SHELFSIGHT_BRIDGE_TOKEN")
        if not token:
            print("error: set SHELFSIGHT_BRIDGE_TOKEN; the bridge never runs without a token", file=sys.stderr)
            return 2
        from shelfsight.serve import make_server, run_pipeline

        def pipeline(workspace, limit):
            return run_pipeline(workspace, limit, config=args.config, lake=args.lake)

        server = make_server(token, pipeline=pipeline, host=args.host, port=args.port)
        print(f"shelfsight bridge on http://{args.host}:{args.port} (n8n: http://host.docker.internal:{args.port})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

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
                print("error: no engine is available; configure nova or set GEMINI_API_KEY / GROQ_API_KEY",
                      file=sys.stderr)
                return 2
            sx = settings.searxng
            probe = SearxProbe(sx.url, sx.engines, client, top_n=sx.probe_top_n, min_interval_s=sx.min_interval_s,
                               language=sx.language)
            run = run_collect(settings=settings, workspace=ws, prompts=active_prompts(prompts)[: args.limit],
                              engines=engines, probe=probe, store=store, run_date=day)
            print(json.dumps(run, default=str))
            return 1 if run["status"] == "failed" else 0

        llm = build_llm(settings, client)  # extract
        if llm is None:
            print("error: GEMINI_API_KEY is required when extractor.engine is gemini", file=sys.stderr)
            return 2
        print(json.dumps(run_extract(store=store, workspace=ws, llm=llm,
                                     extractor_version=settings.extractor.version, run_date=day)))
        return 0

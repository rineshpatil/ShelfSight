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
    plan = [(p, cfg, mode) for i, p in enumerate(prompts) for cfg in settings.engines
            if cfg.name in engines and (cfg.max_prompts is None or i < cfg.max_prompts) for mode in cfg.modes]
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

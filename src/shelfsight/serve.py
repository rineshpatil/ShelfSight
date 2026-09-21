"""Local HTTP bridge so n8n (in Docker) can run the pipeline on this machine.

n8n reaches it at http://host.docker.internal:8765. It binds to 127.0.0.1 only; Docker Desktop still
routes the container there, so the bridge is never exposed to the network. Every endpoint except
/health needs the X-ShelfSight-Token header. All run logic stays here; n8n only schedules and notifies.
"""
import hmac
import json
import re
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

_WORKSPACE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")  # becomes a file path, so no dots or slashes
MIN_COVERAGE = 0.95
MAX_BODY = 64 * 1024  # a /run body is a few bytes; refuse anything that isn't


def alerts_for(run: dict, extract: dict) -> list[str]:
    """Plain-language problems with a run, for the alert email. Empty means healthy."""
    out = []
    if run.get("status") != "finished":
        out.append(f"collection status is {run.get('status')}")
    if run.get("coverage", 0) < MIN_COVERAGE:
        out.append(f"coverage {run.get('coverage', 0):.0%} is below {MIN_COVERAGE:.0%}")
    if run.get("probe_errors"):
        out.append(f"{run['probe_errors']} retrieval probes failed; affected answers are diagnosed probe_missing")
    if run.get("quota_exhausted"):
        out.append(f"quota exhausted: {', '.join(run['quota_exhausted'])}")
    if extract.get("failed"):
        out.append(f"{extract['failed']} answers failed extraction; the next run retries them")
    return out


def run_pipeline(workspace_id: str, limit: int | None = None, *, config: str = "config/config.yaml",
                 lake: str = "data/lake", prompts_path: str = "data/prompts_seed.csv") -> dict:
    """collect → extract → funnel → report for today (IST). Returns a JSON-able summary with alerts."""
    import httpx

    from shelfsight.cli import build_engines, build_llm, today_ist
    from shelfsight.collect import run_collect
    from shelfsight.config import load_settings, load_workspace
    from shelfsight.extract import run_extract
    from shelfsight.funnel import run_funnel
    from shelfsight.probe import SearxProbe
    from shelfsight.prompts import active_prompts, load_prompts, validate_prompts
    from shelfsight.report import diagnosis_summary
    from shelfsight.store import Store

    settings, ws, store, day = load_settings(config), load_workspace(workspace_id), Store(lake), today_ist()
    prompts = load_prompts(prompts_path)
    errors = validate_prompts(prompts, ws)
    if errors:
        return {"ok": False, "workspace": ws.id, "run_date": day, "alerts": ["invalid prompt library", *errors]}
    with httpx.Client() as client:
        engines = build_engines(settings, client)
        if not engines:
            return {"ok": False, "workspace": ws.id, "run_date": day, "alerts": ["no engine is available"]}
        sx = settings.searxng
        probe = SearxProbe(sx.url, sx.engines, client, top_n=sx.probe_top_n, min_interval_s=sx.min_interval_s)
        run = run_collect(settings=settings, workspace=ws, prompts=active_prompts(prompts)[:limit],
                          engines=engines, probe=probe, store=store, run_date=day)
        llm = build_llm(settings, client)
        extract = (run_extract(store=store, workspace=ws, llm=llm, extractor_version=settings.extractor.version,
                               run_date=day) if llm else {"failed": 0, "skipped": "no extractor credentials"})
    funnel = run_funnel(store=store, workspace=ws, settings=settings, run_date=day)
    alerts = alerts_for(run, extract)
    return {"ok": not alerts, "workspace": ws.id, "run_date": day, "alerts": alerts, "run": run,
            "extract": extract, "funnel": funnel, "report": diagnosis_summary(store, ws, settings, day)}


def default_report(workspace_id: str, day: date) -> str:
    from shelfsight.config import load_settings, load_workspace
    from shelfsight.report import diagnosis_summary
    from shelfsight.store import Store

    return diagnosis_summary(Store("data/lake"), load_workspace(workspace_id), load_settings(), day)


def make_server(token: str, *, pipeline=run_pipeline, report=default_report,
                host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    busy = threading.Lock()  # one paid run at a time

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body, content_type: str = "application/json"):
            data = (json.dumps(body, default=str) if content_type == "application/json" else body).encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if hmac.compare_digest(self.headers.get("X-ShelfSight-Token", ""), token):
                return True
            self._send(401, {"error": "missing or wrong X-ShelfSight-Token"})
            return False

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/health":
                return self._send(200, {"status": "ok"})
            if not self._authorized():
                return
            if url.path != "/report":
                return self._send(404, {"error": "not found"})
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            workspace = q.get("workspace", "")
            try:
                day = date.fromisoformat(q["date"]) if "date" in q else None
            except ValueError:
                return self._send(400, {"error": "date must be YYYY-MM-DD"})
            if not _WORKSPACE_ID.match(workspace):
                return self._send(400, {"error": "invalid workspace"})
            if day is None:
                from shelfsight.cli import today_ist
                day = today_ist()
            self._send(200, report(workspace, day), "text/markdown")

        def do_POST(self):
            # Read the body before any early reply: on Windows, closing a socket with unread data sends a
            # reset, so a rejected client would see a dropped connection instead of the 401/404.
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True
                return self._send(413, {"error": "body too large"})
            raw = self.rfile.read(length)
            if not self._authorized():
                return
            if urlsplit(self.path).path != "/run":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "body must be JSON"})
            workspace, limit = body.get("workspace", ""), body.get("limit")
            if not isinstance(workspace, str) or not _WORKSPACE_ID.match(workspace):
                return self._send(400, {"error": "invalid workspace"})
            if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
                return self._send(400, {"error": "limit must be a positive integer"})
            if not busy.acquire(blocking=False):
                return self._send(409, {"error": "a run is already in progress"})
            try:
                self._send(200, pipeline(workspace, limit))
            except Exception as e:  # n8n gets a 500 and alerts; the run's own rows are already stored
                self._send(500, {"ok": False, "alerts": [f"pipeline crashed: {type(e).__name__}: {e}"]})
            finally:
                busy.release()

        def log_message(self, fmt, *args):  # quieter than the default stderr line per request
            pass

    return ThreadingHTTPServer((host, port), Handler)

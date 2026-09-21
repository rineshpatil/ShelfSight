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

from datetime import date

from shelfsight.config import Settings, Workspace
from shelfsight.store import Store

MEANING = {
    "winning": "Recommended in the top 3",
    "recommended_not_top3": "Recommended; rank is below 3 or unknown",
    "named_not_recommended": "Named; recommendation is false or unknown; reason unknown",
    "not_named": "Not named; no search queries reported; reason unknown",
    "cited_not_named": "Brand URL reported as cited; brand not named; influence unknown",
    "probe_missing": "Independent search probe unavailable; retrieval unknown",
    "eligible_not_cited": "Found in independent search results; no brand citation reported; reason unknown",
    "not_eligible": "Not found in sampled independent search results; engine retrieval unknown",
}

LIMITATIONS = (
    "Observations: returned answers and provider-reported citations. Mentions, recommendations and ranks "
    "are extractor interpretations, not verified product facts. Proxy: independent search results "
    "approximate retrieval; they do not reveal the engine's candidate set. Hypotheses about content, "
    "authority or selection require separate evidence. Missing citations do not establish training-data "
    "reliance. No calibrated confidence or causal effect is claimed. API answers may differ from consumer apps. "
    "Visibility and citations are separate from referral traffic and purchases: business impact has not been measured."
)


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
    return "\n".join(lines) + "\n\n" + LIMITATIONS + "\n"

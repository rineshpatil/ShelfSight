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

# ShelfSight — Design Spec

2026-09-21 · Status: sections 1–3 discussed with the product owner; sections 4–5 are drafted defaults, not yet reviewed.

## 1. Product

ShelfSight is an AEO **diagnosis** tool for agencies. It tells an agency *why* a client brand is or isn't recommended by AI assistants, by measuring the retrieval chain the assistant ran — not only the answer it produced — and then verifies whether the agency's fixes worked.

- Replaces the blueprint's "open version of Profound/Peec" framing. Share of voice stays as a supporting metric only.
- User: one agency operator running several client workspaces. No signup, auth flow or billing.
- Seed workspace: Dot & Key, Indian sunscreen, 9 tracked brands.
- Not building: hallucination monitoring, pre-publish simulation, content generation, multi-turn (a later axis), self-serve SaaS.

Market check (2026-09-20): share-of-voice tracking, hallucination monitoring (Miru, Ooky, MaxAEO) and pre-publish simulation (RankScale `/simulate`) already ship. No product found that runs the model's own search queries against an independent index and joins the result to what the model cited.

## 2. The funnel

Two paths, not one line:

- Retrieval path (pages): Indexed → Eligible → Cited
- Answer path (brand): Named → Recommended → Top-3

| Stage | Measured by |
|---|---|
| Indexed | Weekly SearxNG check of client pages (Plan 2) |
| Eligible | Brand URL in the top `eligible_top_n` (default 10) SearxNG results for any of the model's own `webSearchQueries` |
| Cited | Brand URL in Gemini `groundingChunks` |
| Named / Recommended / Top-3 | Extractor (dictionary pass + LLM pass) |

Diagnosis ladder, per prompt × engine × mode × brand. The first matching row wins:

| Diagnosis | Condition | What to fix |
|---|---|---|
| winning | recommended and rank ≤ 3 | — |
| recommended_not_top3 | recommended, rank > 3 or null | Strengthen the claim |
| named_not_recommended | named, not recommended | The claim the buyer asked about is missing |
| not_named | no retrieval (no_web mode, or web mode where the model didn't search), not named | Training-memory gap; no retrieval to diagnose |
| cited_not_named | our page cited, brand not named | Our page feeds a rival's answer |
| probe_missing | web mode, not cited or named, probe failed | Unknown; re-probe |
| eligible_not_cited | in the candidate set, not cited | Selection: format, authority, extractability |
| not_eligible | not in the candidate set | Content gap: no page for the question |

A brand that is named but not cited is being recalled from training data. That position is fragile. It shows up as a named_* diagnosis with `is_cited = false`.

## 3. Constraints and decisions

- Answer engines: Gemini free tier with Google Search grounding, the only engine that returns `webSearchQueries`, plus Groq free tier as a no-web baseline. No scraping of consumer chat apps. Google AI Overviews SERP parsing is an optional later collector: the least reliable part and ToS-gray.
- Pin every model id. Newer Gemini ids (`gemini-flash-latest`, `gemini-3.1-pro-preview`) were reported to omit `groundingChunks`. `scripts/smoke_gemini.py` verifies the pinned id before any real run.
- Collection and extraction share one Gemini free-tier daily cap. A full run is about 100 grounded + 150 extraction calls. Use `collect --limit` until you know the account's real cap.
- Hosting: AWS plus n8n, per the product owner. This drops the earlier "must be free" goal. Expected cost is $6–13/month, almost all of it the EC2 box.
- Use an AWS **Paid Plan** account with an AWS Budgets alarm. A Free Plan account closes itself when its credits or its 6 months run out, and that would delete the time series.
- **Revision to the architecture discussed:** the collector runs as a container on the EC2 box, not in Lambda. A paced run (Gemini free tier ≈ 10 RPM, plus SearxNG probes) takes 15–25 minutes, which is over Lambda's 15-minute limit. Fanning out in parallel would break per-key pacing. Running on the box also gives SearxNG and the collector the same stable IP. Lambda and SQS are dropped.
- Storage: append-only Parquet at `{root}/{table}/workspace={w}/run_date={d}/{uuid}.parquet`, read with DuckDB. A local directory in Plan 1, S3 in Plan 3. No materialized metrics table, no Athena, no Glue.
- Raw answers are never modified. Derived tables (mentions, funnel) carry `extractor_version` and `joiner_version`. Readers filter on the current version, so history can be re-derived without re-collecting.
- Workspaces and brands are YAML in git. Prompts live in a Google Sheet, with a seed CSV in Plan 1.
- Logic lives in Python. n8n handles scheduling, retries, alerts, Sheet sync and digests, and holds no metric logic.
- Secrets: env vars locally, SSM Parameter Store (Standard) on AWS. API keys go in headers, never in URLs.

## 4. Fix verification (drafted, not yet reviewed)

The agency records each shipped change in `interventions`: url, shipped_at, kind, target_prompts, control_prompts, target_diagnosis, hypothesis.

- Metric: the per-day rate at which the client brand reaches the targeted stage (for example, cited) across the target prompts, in web mode.
- Effect = (target post − target pre) − (control post − control pre). The pre window is the 14 days before shipped_at. The post window is days 7–28 after it.
- Verdict: `clear` if |effect| > 2σ of the control's daily rate and at least 7 post days exist. `directional` if it's above 1σ. Otherwise `inconclusive`. No p-values; the sample is too small to support them.
- Time-to-effect: days from shipped_at to the first run where the intervention URL is indexed, then eligible, then cited.
- Output: a monthly client deliverable (Markdown/PDF) with prompts grouped by diagnosis and ranked by how many high-priority prompts each fix would unblock, plus a verdict on each earlier intervention.

## 5. Dashboard (drafted, not yet reviewed)

A static Next.js export using DuckDB-WASM to read Parquet through CloudFront. Views:

- Funnel overview
- Prompt explorer: answer text next to the candidate set and the cited set
- Interventions
- Sources: domains that are often eligible but rarely cited

Client data must not be public, so a CloudFront Function enforces basic auth per workspace.

## 6. Delivery plan

| Plan | Delivers | Depends on |
|---|---|---|
| 1. Core funnel engine | Local Python pipeline: collect → probe → extract → funnel → report | — |
| 2. Fix verification | page_index, interventions, difference-in-differences, monthly deliverable | 1 |
| 3. AWS + n8n | Terraform (EC2 + EIP, S3, SSM, SNS, Budgets); S3 store backend; collector container; n8n workflows for daily run, Sheet sync, weekly digest, library check, backup outside AWS | 1 |
| 4. Dashboard | Static Next.js + DuckDB-WASM on S3/CloudFront with auth | 1, 3 |
| Later | AI Overviews collector, bring-your-own paid keys, multi-turn axis | — |

## 7. Caveats (carry these into client reports)

- API answers are a proxy for the consumer apps.
- SearxNG results approximate Gemini's retrieval; they don't come from the same index.
- Answers vary from run to run. Use 7-day rolling views. Free tiers allow one sample per call.
- Models update silently. Model ids are pinned and recorded on every row.
- The extractor can be wrong. There's a labelled test set, a target of ≥ 95% brand recall, and versioned output.

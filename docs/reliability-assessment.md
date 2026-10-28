# ShelfSight reliability assessment — 2026-10-29

## Source and evidence boundary

Authoritative editable source was found through `git worktree list` at
`E:/Project/ShelfSight/.claude/worktrees/core-funnel-engine`, branch `feat/page-lookup`,
starting commit `673d796`. The outer checkout is the documentation branch `docs/shelfsight-spec-and-plan`.
No code was rebuilt from `cdk.out`; generated assets were used only to identify their packaging role.
No applicable AGENTS.md or CLAUDE.md was found in the inspected ancestor/worktree paths.
The old implementation plan was read as historical design, not an instruction to rebuild its completed tasks.

Read source, configuration, tests, the design and implementation plan, README, the existing untracked
verification runbook and AEO brief, and local lake metadata. Existing `.impeccable/`,
`docs/verification-runbook.md`, and `docs/aeo-brief-dot-key-vitamin-c-e-spf50.md` remain unchanged.
The outer Testing/product-spec.md and testsprite-mcp-test-report.md identify CardRadar and were excluded.

Local historical lake records inspected: September 22, 2026, 14 successful responses (6 Gemini, 8 Nova),
one Gemini error and one skipped quota call; extractor versions ext-1 and ext-2. This does not verify the
brief's September 25/27 claims of 38 answers, page prices, formulation, citation totals or claimed causes.
No external page, deployment, account or analytics state was inspected. No existing lake files were changed.

## Verified gaps, by priority

| Priority | Verified gap before changes | What is now implemented |
|---|---|---|
| P0 | Reports label independent search results as the model's candidate set and infer content/authority/training-memory causes | Explicit observations, proxy measurements and causal hypotheses in derived rows; cautious report/UI wording; no calibrated confidence claim |
| P0 | Empty successful searches indistinguishable from failed probes; unresolved Gemini links can become invented homepage citations | Explicit probe status; unresolved links retained; provider modelVersion retained when returned |
| P0 | Only two positive labelled examples; evaluator counts recall but not false positives, excludes missed brands from rank denominator, always uses Gemini despite configured Nova | Reviewed synthetic examples plus ambiguous, refusal, negative, comparison and missing-field cases; precision/recall, all-label rank accuracy and error records; configured live extractor behind explicit switch |
| P1 | Fix-verification exists only in the design; no approval/publication ledger or executable outcome calculation | Local transactional proposal/approval/publication records, exact supported copy, repeated matched outcome assessment and executable fixture |
| P1 | Raw rows omit exact question text and probe configuration; extraction stores only a version string | Collection settings and question text retained; extraction prompt/schema/model/output/answer hash/brand dictionary retained; version bump preserves old rows |
| P1 | Proposed clear/directional thresholds imply effectiveness without validating causal assumptions | Complete-cell descriptive differences only; missingness and model/settings changes exposed; causal verdict inconclusive |
| P2 | Visibility gains implicitly treated as commercial gains in product positioning | Visibility/recommendations/citations separated; referral traffic, purchases and revenue explicitly not measured |

Existing collection, SearxNG probing, extraction, funnel, Markdown report, page lookup and infrastructure
code are implemented. Indexed-page checks, scheduling for intervention plans, real website publishing,
analytics ingestion, statistically defensible causal inference and the planned dashboard are not established
by this work. Checked-in infrastructure is not proof of deployment.

## Changes and validation

Baseline: 116 offline tests passed. Final: 133 passed in 33.07 seconds; `git diff --check` passed.
The end-to-end example and the outcome CLI both completed successfully. Saved local output is in
`data/reliability-example-verified/`, including `extractor-evaluation-v2.json` and `assessment-cli.json`.
Added checks cover false positives and missed ranks, citation mismatches, typed reads of old Parquet,
successful empty probes, evidence-to-answer links, unsupported product claims, approval/state ordering,
complete end-to-end fixture results, insufficient repeated days, missing/failed/duplicate runs,
model/settings changes and missing extraction. Existing provider-parser tests remain offline.

`python scripts/eval_replay.py --output OUTPUT.json` replays seven synthetic cases, including the two original
labelled examples, and a saved Gemini provider response. Expected result: mention precision/recall 11/11,
recommendation positive precision/recall 6/6, rank accuracy 11/11 including expected nulls, and two returned
citation URLs parsed correctly. One missing recommendation flag is surfaced as unknown (not converted to
false). These are synthetic contract metrics, not live model accuracy or calibrated probabilities.
The citation check includes an unresolved URL; correct preservation is not verification of its destination
or whether cited material supports the answer. Empty citation examples exercise the no-citation case.

Error review: the old evaluator's false-positive blind spot and missed-rank denominator are fixed. Missing
recommendation fields still remain unknown deliberately. The comparison prompt no longer invents preference
orders for non-recommended alternatives. Unresolved citations no longer acquire a fabricated homepage.
A representative real, independently reviewed evaluation set and fresh saved model outputs are still needed.

## Local example and interfaces

Run from this worktree using the existing environment:

```powershell
.venv\Scripts\python.exe scripts/intervention_example.py --output data/new-example
.venv\Scripts\python.exe scripts/eval_replay.py --output data/new-example/extractor-evaluation.json
.venv\Scripts\python.exe -m pytest -q
```

The example requires a new directory and writes proposal.json, interventions.sqlite, assessment.json,
README.md and a separate fixture lake. It uses actual collection, extraction and funnel code with fake
engines; all product specifications and actors are labelled synthetic. No API calls occur.

Observation: a baseline fixture answer omits the brand. Supporting evidence: the retained synthetic
brand specification supports only `SPF 50.` on the exact target product page. Proposed fix: copy that
verified wording into the local content artifact. Approval and local publication retain actors, times,
content hashes and actual before/after text. Unknown ingredients, suitability and other benefits stay unknown.
Seven target baseline and seven post answers move from 0/7 to 7/7; the separate comparison stays at 3/7.
The difference is +1.0 for visibility, recommendation and reported page citations, separately. The result is
inconclusive about causality and has no measured business impact. Constructed fixture outcomes are not evidence
that the copy change works. IDs and capture timestamps vary on replay; the semantic results are reproducible.

The module CLIs described in README expose propose, approve, publish-local, show and assess. A proposal's
observation must match an actual target-question response in the supplied local lake. Publication creates
an immutable content entry inside SQLite, not a file on a brand website. Changing copy requires a new
proposal ID and approval. Direct editing of the SQLite file is outside the interface's guarantees.

## Remaining limitations by type

**Missing implementation:** no real website publishing connector, authenticated multi-user approvals,
rejection/revision UI, tamper-proof audit service, intervention scheduler, index/re-crawl checks, same-day
replicate design, model-stratified aggregation, analytics adapter, uncertainty intervals or causal inference.
The local publisher intentionally supports exact verified fact quotations, not arbitrary content generation.
Outcome collection is one planned observation per prompt/day; duplicate cells are excluded rather than pooled.
Comparison page stability and spillover require human review. Page diagnosis remains brand-level while
citation outcomes use exact normalized URLs. Raw records preserve evidence but do not certify its truth.

**Missing access / not exercised:** no authorized live analytics data, production website publishing access
or independent brand verification was supplied/exercised. No external model calls were made. Credentials
may exist locally but were neither read nor used; absence of credentials is not asserted. Production deployment
and account state are unverified. The live evaluator may be billable and requires separate authorization.

**Insufficient evidence:** the two original labelled answers plus synthetic cases cannot establish production
accuracy, real product suitability, intervention efficacy, traffic uplift or revenue attribution. Existing
historical rows lack new provenance fields and are not retroactively repaired. Model IDs can hide silent
changes; provider-reported citation absence cannot prove retrieval absence or training-data reliance.
A larger prompt library alone does not make an observational conclusion causal or statistically sound.

Version defaults are now ext-3-evidence and join-2-evidence. Current reports do not silently mix historical
versions; re-extraction is an explicit operation and may require authorized model calls. The historical
spec is marked superseded, and untracked sample briefs/runbook remain preserved as originally found.

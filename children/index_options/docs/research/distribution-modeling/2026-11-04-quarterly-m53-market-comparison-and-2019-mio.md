## Question

How did the frozen quarterly90 M53 forecasts compare with market, and can the same three MIO policies be extended to each 2019 quarter?

## Finding

The preselected seed 11 quarterly M53 reduces threshold-weighted CRPS by **0.546% versus calibrated market** over 2020–2023; the pointwise 95% two-way cluster interval is **0.342%–0.750%**. Absolute mean losses are 0.525926727 versus 0.528813919. Annual point improvements are positive in all four years:2020 +0.844%,2021 +0.766%,2022 +0.330%,2023 +0.248%.

Against raw market, the primary improvement is **0.718%**, but its95% interval **−0.213% to+1.650%** includes zero. Annual improvements are+0.630%,+3.371%,−1.733%,+0.502%;2022 is worse. Exact full-distribution CRPS improves 0.429% versus calibrated market (95% interval 0.234%–0.623%). This supports a modest incremental predictive benefit; it does not establish tradable profit, superiority to the original annual M53, or a new promotion.

Seeds29/47 give calibrated-market weighted improvements 0.577%/0.558%, respectively. Every seed has positive annual calibrated-market point improvements. The fixed-seed event-loss average is+0.560% (95% interval 0.354%–0.767%). It averages losses before clustered inference; it is not an ensemble forecast and does not triple the sample size. Seed11 remains primary; no seed or recipe was selected after outcomes.

### Evaluation scope and verification

The owner's later question explicitly opened predictive comparison of the frozen2020–2023 caches. MIO economic results remain unopened and policies unranked.2019 remains unranked warmup under A0003/A0004/A0008;2024–2025 outcomes remain sealed and 2026 permanently sealed. The quarterly retrain variant is owner-authorized, separately named, with Path unchanged.

All 48 caches (16 quarters×3 seeds) were processed without refitting or weight changes. Each seed contains 712,556 cached identities;450,563 are market-eligible paired events and 261,993 are market-ineligible. No additional protected-settlement rows were excluded at evaluation. This is common market-eligible support, not a claim over every cached row or the distinct fixed115 MIO cohort. All methods and seeds use identical event keys. There are 110 scored symbols and 209 expiry clusters.

A0002's13-knot weighted CRPS, ordinary grid CRPS and exact full CRPS use the existing public ResidualCDFStudy scorer, weights and finite-tail convention (width 4). Labels join one-to-one on symbol/quote-date/expiry with matching settlement and reference scale. Both origin and settlement are before 2024. Paired intervals use expiry+symbol−intersection covariance and Student-t(minimum cluster count−1); intervals are pointwise, without multiplicity correction. Subperiod tables are descriptive.

Two independent pre-execution reviews passed on frozen protocol6b811c93 and runner59110946. The single WSL run completed in 622.8 seconds with peak RSS 1,436.7 MiB. Independent verification rehashed48 score shards, checked84 windows,1,512 loss means and 1,008 intervals against independent formulas (maximum absolute error9.06e-14). No actual forecasts or labels were rescored for verification. Three disclosed defensive-tooling minor findings did not arise in this fresh run; retained review receipts define their scope.

The original annual M53's earlier+0.690% calibrated-market result belongs to a different study. These new quarterly results must not be subtracted from that number as a paired model comparison. Retrospective settlement-date availability, inherited feature/cohort timing limits, and repeatedly exposed validation still apply. No holdout opening, regime/cell promotion, new trading recommendation or live-readiness claim follows.

### 2019 extension: all four quarters retained, none executed

Q1–Q3 overlap the already disposed2019 development packet (2019-01-02..2019-10-01). The existing public registry refuses partial overlap; its re_exposed route admits exact nondevelopment twins, not subdivisions of this development packet. No registry was reset, duplicated, widened, reimported or edited to evade that refusal.

Q1 additionally crosses GE's February 26 mixed/cash deliverable and FCEL's May9 reverse split in its settlement tail, and lacks a pre-2019 OOS blend bank. Q2 includes FCEL's reverse split. Q3 has no separately cleared execution route while the registry overlap persists. Genuine support requires a bounded approved lifecycle extension; unsupported corporate accounting requires its own approved design.

A causal Q4 producer candidate was prepared and passed two independent contract reviews. It uses the original 90 features, seed 11,23 epochs pinned to the pre-2019 monitor, and the original annual2019 out-of-sample bank filtered to origins and settlements beforeOctober1. No real fit or forecast was launched. The candidate remains parked without a dispatch lock.

Additive Q4 market preparation retained all 115 names, but the date-only audit found **LLY missing all 64 entry sessions and 45 tail sessions**. Original LLY2019 data endsAugust23;2020 resumesMarch9. The empty-forecast adapter's ability to refuse entries cannot override the owner's whole-quarter persistent-gap gate. All three Q4 policies are blocked. The prior conditional Q4-support review is explicitly superseded by its retained amendment. No name was silently removed and no quotes were fabricated.

Bounded existing-source recovery checked original and broad2019 pins and raw acquisition provenance. The broad2019 LLY archive is byte-identical; further projected raw acquisitions contain no missing-period rows. Recover genuine compatible history through onboarding before new source/forecast versions are proposed. Preserve original pins; data repair is not permission to overwrite the closed caches.

### Correction to previously delivered MIO coverage

New directly related evidence requires withdrawing the2020 Q1 full-cohort support claim: LLY is missing45 of 62 entry sessions, January 2–March 6. The original and prepared sources independently confirm the same gap. The completed run and its three accounting-attested reports are retained as historical execution evidence, but quarantined from admissible coverage. This does not assert an accounting error or actual LLY holdings; economics remain unopened.

The independent date-only review of 2021 Q1 found complete115-name bar calendars over 106 entry/tail sessions and no persistent chain gap. GME lacks two isolated entry dates (January 28 andFebruary24); all tail sessions are present. Disclose those missing dates rather than invent a new threshold or describe the inputs as complete.

Current original coverage is **2 executed,1 admissible of 16**: three admissible method reports, three quarantined reports and 14 never-executed blocked quarters. The four newly requested2019 quarters add **0 executed/4**. Combined requested coverage is **1 admissible/20**, with two historical quarter executions. Preserve the old frozen manifests and registry revision41; correction is additive evidence and current-status annotation, not a rewrite of historical claims.

### Compute and next bounded work

All new preparation, scoring, logs and verified results stayed inWSL. The Mac was online but ran no task in this extension: the small sequential scoring run used the already compatible WSL runtime; MIO's shared registry requires serial lifecycle order and all 2019 quarters are blocked. Resource probes checked shared Windows/WSL RAM and physicalC: headroom before/after compute; no other session was interrupted.

The next work is genuine LLY source recovery, an approved bounded2019 subinterval re-exposure design, and separately approved mixed/cash-deliverable accounting. Until those exist, do not dispatch the parked MIO drafts or claim2019 completion. Retain M53 and seed 11 for the frozen protocol; this comparison does not authorize tuning on target quarters.

## Sources

- [Frozen comparison config](../../../configs/quarterly-market-comparison-20261104.json) and [parked2019 candidate](../../../configs/quarterly90-2019-extension-20261104.json).
- Evidence root: `/home/russell/data/index_options/quarterly-prediction-cache-20261104`.
- `market-comparison/comparison.json` SHA25655340df9a2ea52518168fd87f7fffef88da42cbb5d675684574ff0d554f82324; `completion.json`, `evaluation-lock.json`, both pre-execution reviews and `result-integration-review.json`.
- `2019-extension/design-review.json`, `design-review-amendment-01.json`, both producer reviews, retained raw-recovery evidence and `request-status.json`.
- `mio/coverage-correction-review.json`, date-only projection evidence and additive correction disposition. Original execution and support receipts remain unchanged.
- [GE distribution notice](https://www.miaxglobal.com/alerts/2019/02/25/miax-options-and-miax-pearl-corporate-action-alert-general-electric-company-ge), [GE OCC44559](https://www.miaxglobal.com/sites/default/files/alert-files/GE_Distribution_44559.pdf), [FCEL reverse-split notice](https://www.miaxglobal.com/alerts/2019/05/08/miax-options-pearl-and-emerald-corporate-action-alert-fuelcell-energy-inc-fcel-1), [FCEL OCC45006](https://www.miaxglobal.com/sites/default/files/alert-files/FCEL_revsplit_45006.pdf).
- [Prior blinded delivery](2026-11-04-blinded-quarterly90-cache-and-mio.md), [locked Path](../../decisioning/path.csv), and `/home/russell/.cursor/projects/home-russell-dskit/canvases/M53-quarterly-cache.canvas.tsx`.

# GPD decision-region screen

## TL;DR

The raw `center_gpd_05_prior25` CDF was audited on actual eligible ETF strike
wings from a fixed 60-entry development screen. Its refined proper wing score
was 0.117494, slightly worse than the center incumbent's 0.117432. This is a
date-only, after-close diagnostic—not a model promotion, feature selection, or
tradable decision.

## Contract

ADR-0205 authorizes only offline CDF research. The source forecast is the
completed ADR-0203 Phase-F development partition: 2016--2018 training and
validation history, 18,616 possible forecast identities, with no 2019--2025
score used here. JSON
`configs/run-cdf-gpd-decision-regions-screen-r4.json` fixes 20 evenly spaced,
lexicographically ordered identities per SPY, QQQ and IWM (60 total, equal
weight within each symbol), 0.5-dollar price steps, a 0.125-dollar refinement,
positive floor 0.1, zero W1 radius, and 30-minute/6-GiB process limits.

The annual ETF chain archive has dates but not source quote timestamps. Each
snapshot is treated as complete after its date's close and is non-executable.
Only raw `GridCurve` archives are admitted: their tied knots are integrated as
atoms for expected wing loss. The ADR-0203 feature forecasts are composite
curves, so they were refused rather than approximated; no feature forward
selection ran.

## Run evidence

The first 2x-refinement JSON (`run-cdf-gpd-decision-regions-screen.json`) made
the intended conservative refusal: its 0.002 weighted-score error tolerance
was exceeded before an output artifact was written. It remains a failed,
immutable numerical attempt. The fresh r4 JSON completed `prepare`,
`evaluate`, and `report` in WSL2. Evaluation took 17.6 seconds and stayed well
inside both caps.

Preparation selected 60 forecasts and 9,566 matching chain rows. Evaluation
produced 180 paired model rows, 41,730 candidate/model rows, and 1,824 unique
wing templates after within-forecast deduplication. SPY/QQQ have 120 rows with
an evaluated American-short charge; IWM's 60 rows retain unknown dividend
windows, so regret is withheld there.

| Raw model | refined wing score | strike Brier | condor loss MSE |
|---|---:|---:|---:|
| horizon empirical | 0.117826 | 0.124394 | 3.429611 |
| center incumbent | 0.117432 | 0.124013 | 3.325326 |
| center GPD prior 25 | 0.117494 | 0.124063 | 3.333382 |

The coarse-to-refined absolute wing-score error averaged 0.00143 across all
three models, below the predeclared 0.05 bound; refined and coarse rankings
matched. By index, the GPD curve was worse than the center incumbent for IWM
(0.122540 versus 0.122493), QQQ (0.146694 versus 0.146614), and SPY (0.083249
versus 0.083189).

## Decision and limits

No model changes. This fixed screen is not the full 18,616-identity panel and
does not support a model-selection claim. The full archive would exceed the
configured 100,000-chain-row bound, so any full decision study needs a new
approved, bounded data-reduction design. Tail deviations remain acceptance
constraints, not evidence that an added feature family is bad. A future
feature workflow must first emit raw GridCurve forecasts or gain separately
reviewed composite-curve proper-score/integral support; the center-only route
also must prove exact 5%/95% anchor preservation.

## Reproducibility and handoff

Run from `children/index_options` in WSL2 with the project CDF environment:
`python -m index_options.cdf_study configs/run-cdf-gpd-decision-regions-screen-r4.json --decision-stage prepare`, then `evaluate` and `report`. Ignored
artifacts are in `pipeline_runs/cdf_gpd_decision_regions_screen_r4_20260930`.
The frozen config SHA-256 is
`29fd8e329c774f7b23af2d58424e6107606ceb79cdd8386faebdf2a77afa61a9`.

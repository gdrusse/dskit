## Question

Does the previously selected global probability correction improve unchanged M53 on 2020–2023 without retuning? The user explicitly authorized this comparison after the pre-2020 chronological check failed. This is prediction-only research under A0001/A0002/A0003/A0008; no production, MIO, Path or base-model change.

## Finding

Do not adopt the correction. On 449,498 identical eligible fitted forecasts across 110 stocks and 209 expiries, primary twCRPS increases from 0.525580619400 to 0.525767729380. Improvement versus unchanged M53 is −0.0356006%, with two-way clustered pointwise 95% interval [−0.0773655%, +0.0061643%]. The interval includes zero: no demonstrated benefit, not proof of a large deterioration.

Full CRPS increases from 0.661248864230 to 0.661264922752, an improvement of −0.0024285%, interval [−0.0501651%, +0.0453081%]. Pooled mean absolute mean-CDF gap is effectively unchanged: 0.7031394 versus 0.7037838 percentage points. Equal-DTE gap increases from 0.7392934 to 0.7484048 points.

Primary improvements by forecast-origin year are 2020 −0.0337583%, 2021 −0.0073397%, 2022 −0.0646991%, and 2023 −0.0360025%. The correction is also worse on pooled primary loss in all eight DTE bands. Year-by-DTE scores and intervals are retained, not used to select a favorable subset. Calibration improves in some years/bands while proper prediction loss does not improve.

The selected correction fails all five predeclared descriptive guards: positive pooled primary improvement, nonworse full CRPS, improved pooled and equal-DTE calibration, and positive primary improvement in at least three years. No research lead or production promotion follows. This does not prove unchanged M53 is conditionally calibrated: its annual mean probability gaps are 1.7570, 2.0314, 3.6759, and 0.7922 points, larger than the pooled gap.

## Frozen procedure and boundaries

Before reading the 2020–2023 evaluation outcomes, fit the existing 21-knot _PitMap once on all 77,581 matched eligible M53 fold-1 validation forecasts whose quotes and settlements precede 2020. Persist the map and freeze lock. Apply this identical map to the saved fold-2–5 forecasts; their existing annual M53 refits are common to both arms. No correction fit, tuning, window choice or segmentation uses evaluation outcomes.

This is the same global recipe chosen in the prior study, now refitted on all allowed pre-2020 evidence. It is not the exact map that was learned before October 2019 and lost 4.66% in Q4. The all-2019 map differs from identity by at most 1.352264 percentage points at its knots; the small later effect is consistent with that limited correction.

All evaluation quotes and settlements are before 2024. The 2020–2022 annual tables group by forecast-origin year and include respectively 6,203, 6,632 and 6,658 late-year quotes settling in the next year. These are evaluation observations, never learning data for the fixed map. This follows the saved expanding-forecast convention: a December origin may be scored in its earlier fold while its later settlement is purged from the next fit's training. All 2023 outcomes settle before 2024. No 2024–2025 or 2026 outcomes were read.

## Verification and limits

Primary scoring, PIT mapping, and the represented CDF use the existing owner implementations. The exact-pinned previously reviewed research helper definitions are reused without executing that prior experiment. Every stored corrected CDF is finite, bounded and monotone. Primary and whole-line CRPS use the same 13 knots and linear tail closure at Z = −7/+7. The frozen map hash is asserted unchanged after evaluation. Row-level scores are saved for paired replay; DTE score tables are computed from those same rows.

Independent correctness/authority and tests/integration reviews, input/code/result hashes, and report checks are retained. Work ran in WSL2 with two numerical threads; the main analysis took about 19 seconds and peaked at 1,350 MiB RSS. No full test suite was run because no core behavior changed. The canvas is typechecked; its interactive HTML counterpart is inspected for correct year/DTE/model controls, tables, curves and uncertainty bars.

These validation years have repeatedly informed earlier research; they are not pristine confirmation. Confidence intervals are pointwise, unadjusted for model selection or multiplicity, and two-way expiry/symbol clustering may not capture all serial dependence from overlapping horizons. Matching a mean CDF does not establish individual conditional calibration; no equivalence tolerance was specified. The correction can become stale as the annual base model changes. Source-clock and universe assumptions remain inherited and uncertified. No A0005 multi-reference/seed promotion gate was claimed.

Reviewer-only same-year-settlement sensitivity changes the tiny 2021 primary gain from −0.00734% to +0.0102%, with the other three years remaining negative. The prespecified origin-year analysis is unchanged; its annual signs are cohort-dependent, and the three-of-four-years guard still fails under this alternative. Exact sensitivity evidence is retained with the correctness review.

## Sources

/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/protocol.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/candidate-lock.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/freeze-lock.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/frozen-map.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/results.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/dte-scores.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/all_2020_2023-scoredrows.parquet
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/correctness-review.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/tests-review.json
/home/russell/data/index_options/mlp-learning-audit-20261103/frozen-cdf-calibration-2020-2023/presentation-verification.json
/home/russell/.cursor/projects/home-russell-dskit/canvases/CDF-calibration-2020-2023.canvas.tsx
http://localhost:8765/CDF-calibration-2020-2023.html

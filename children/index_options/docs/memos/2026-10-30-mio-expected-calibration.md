## TL;DR

The MIO objective `expected` (the predicted dollar profit that selects books) does not predict what the books earned. Across 394 filled books in two 2019 cohorts and 2021 Q1, the slope of realized on expected is about 0 in broad212 and Q1 and negative in limited36, and within a week higher `expected` goes with lower realized P&L. This measures the predictive model's dollar tails; it is not an optimizer change. Largest caveat: the Q1 cohort has only 23 filled books on 9 decision dates, so its band is wide.

## Execution contract

- ADR-0285 item 1, owner approved 2026-10-30. Read-only over three attested MIO results; no fit, replay, solver, provider pull, registry write, 2020 label, 2024+ or holdout read.
- Document `children/index_options/configs/mio-calibration-20261030/expected-calibration-2019-2021q1.json`, document hash `963a76b2f067a784f569a4c6dee4a46e7946b605731cf89780b419c4ca018289`.
- Run `mio-expected-calibration-2019-2021q1-20261030`, as-of 2021-03-31, run hash `c261541f` (`c261541fc75e51d82565332e9c5cb77ad4fc586d70ce4bdb53ba5dc5b8b858fa`), exit 0, all 22 nodes ok. Root `/home/russell/data/index_options/mio-calibration-2019-2021q1-20261030/`.
- Inputs (SHA256): broad212 `9ed7b614b71a06bae7b297df85673ac51a9aa79858a689b005cf4b86d2a21a4b`, limited36 `df911d4c89ba65ff9b858ce28d3726cf886ceba16ebdb9534c49bd2a81f351ed`, Q1 `ce39e2062c378d6576af97f673f795df04b724b4f696729c677a43cdd94399ac`.
- Statistics live in `carry.json` (SHA256 `79fec153b7b6f391c77d5464fc16e950ba02b68651f5088de01e29ba8f4fb554`), not `result.json` (`cd34b09b07a1802f100abe5e4e1c79325b9a2d1d7abc48570197abbb26ccb2d0`), which prints no verdicts.
- The code that ran was uncommitted (no code pin in the run); it is committed with this memo. A fresh re-run reproduced `carry.json` byte for byte (integration review).

## Results (gates fixed before the bands were computed)

G1 is the slope b of realized P&L on `expected_matched` (expected scaled by the filled fraction), two-way (decision date, symbol) cluster-robust, 95% Student-t band. b = 1 would be calibrated.

| Cohort | n | b | 95% band | Reading |
|---|---|---|---|---|
| 2019 broad212 | 296 | 0.004 | [-0.41, 0.41] | no magnitude information |
| 2019 limited36 | 75 | -0.31 | [-0.43, -0.19] | inverted |
| 2021 Q1 | 23 | -0.002 | [-0.52, 0.51] | no information, wide band |

- G2, fitted vs fallback cells: broad212 -0.08 (141 books) vs 0.04 (155); limited36 -0.32 vs -0.43; Q1 fitted has 4 books, uninformative.
- G3, within-week rank IC of expected vs realized: -0.43 (t -5.3) over 49 weeks of filled books; -0.46 (t -8.0) over all 1,090 decided books with refused books settled counterfactually. The reviewer's reading, which I adopt: anti-ranking (higher expected, lower realized), not "rank usable". The ordering owner's `usable: true` only means the cross-section was wide enough. The pre-registered text was silent on sign.
- G4, 56 Q1 books refused at fill time, settled at decision prices: `nominal_lp` -$30,949.35 (27 books), `w1_003_lp` +$1,113.95 (14), `w1_003_shock10_lp` +$6,964.75 (15). The 20 fitted-cell books refused for a missing fill quote lost -$28,239.90 together (0 wins) across 15 symbols; 7 are GME (-$21,661.35).

## Caveats

- CR1 has no leverage correction. Leave-one-symbol-out slope ranges (-0.13 to +0.11 broad212, -0.34 to -0.28 limited36, -0.32 to +0.26 Q1) flip no verdict.
- G3 counts a book chosen by several methods once per method (43 filled rows in 19 duplicate groups). G1 is unaffected.
- Raw `expected` slopes (-0.009 / -0.215 / -0.011) differ in sign from the matched broad212 slope; both are indistinguishable from 0.
- Exploratory: the naive OLS slopes were seen before the gates were fixed (stated in the document notes).

## Implementation evidence, failures and unrun work

- Code: `clustered_calibration_slope` / `ClusteredCalibrationSlope` (dskit), `student_t_critical` moved to `stats`, `MioBookOutcomes` (child). Tests: 77 in the two new files, 666 in the stats/ordering/mean_interval/kinds_stats selection, 4 in `tests/docs`; all pass.
- Reviews, two independent Sonnet lenses, both C0/M0 with Minors: `reviews/sonnet-correctness.md` (SHA256 `3cecf9c69171b892c2364348f7defa17c1844c027ef32bb2d815f0ba58076624`) and `reviews/sonnet-integration.md` (SHA256 `c948cd35b8cca6f6fd04462dd7d1a5725feb171c2ad8173aca983e1190726c92`), under the evidence root.
- Applied after review: CGM citation, `NO_VARIANCE_RTOL` zero test, filled-book-without-close counted unsettled, a hand-worked slope test, inventory docs.
- Not applied (Minor/Nit, deferred): dead branch and lazy import in `ordering.py`, `match=` in one test, a per-book dedup sensitivity for G3, a split check at the expiry day, a singleton-cluster test.
- Unrelated suite failures seen by the integration lens (`test_purity`, `test_study_protocol`) were not classified against a clean `main`.
- Not run: any dedup-by-book G3, any change to the optimizer.

## Reproducibility and handoff

- Per-book table and gate summary: `children/index_options/docs/reports/mio-calibration-2019-2021q1-20261030/` (`books.csv`, `summary.md`, `artifact-index.json`). `books.csv` was re-derived by re-running `MioBookOutcomes` over the digest-verified results (1,090 = 831 + 180 + 79 rows, 0 unsettled, max filled recompute error 0.0, bars read to 2019-12-31 and 2021-03-31).
- Next authorized action: none. The owner decides whether the finding changes the predictive-model work.

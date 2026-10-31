# MIO expected-profit calibration audit, 2019 and 2021 Q1 (ADR-0285 item 1)

This reads the predictive model's dollar tails, not an optimizer change. Run
`mio-expected-calibration-2019-2021q1-20261030`, as-of 2021-03-31, document hash
`963a76b2f067a784f569a4c6dee4a46e7946b605731cf89780b419c4ca018289`, run hash
`c261541f` (full `c261541fc75e51d82565332e9c5cb77ad4fc586d70ce4bdb53ba5dc5b8b858fa`).
Read-only over three attested MIO results; no fit, replay or provider pull.

**Verdict: higher `expected` goes with lower realized P&L (anti-ranking), and the
magnitude carries no information.** G3 is not "rank usable": its `usable` flag only
says the cross-section was wide enough to measure.

## Gate table

G1 is the slope of realized P&L on `expected_matched` over all filled books, two-way
(decision date, symbol) cluster-robust, 95% Student-t band. Slope 1 would be calibrated.

| Cohort | Filled books (n) | Slope | 95% band | Reading |
|---|---|---|---|---|
| 2019 broad212 | 296 | 0.004 | [-0.41, 0.41] | no magnitude information |
| 2019 limited36 | 75 | -0.31 | [-0.43, -0.19] | inverted |
| 2021 Q1 top3 | 23 | -0.002 | [-0.52, 0.51] | no information (band is wide: 23 books, 9 dates) |

| Gate | Result |
|---|---|
| G2 fitted vs fallback cells, broad212 | -0.08 (141 books) vs 0.04 (155 books); both about 0 |
| G2 limited36 | fitted -0.32 [-0.43, -0.20]; fallback -0.43 [-0.64, -0.23] |
| G2 Q1 | fallback 0.02; fitted n = 4, 2 df, uninformative |
| G3 within-week rank IC, filled books | -0.43 (t -5.3) over 49 weeks; broad212 -0.40 (t -3.6); limited36 -0.45 (t -2.67); Q1 -0.49 (t -1.65) |
| G3b all 1,090 decided books, refused settled counterfactually | -0.46 (t -8.0); broad212 -0.43 (t -5.7) |

G4, the 56 Q1 books refused at fill time, settled at expiry at their decision prices
(fees included):

| Method | Refused books | Counterfactual P&L |
|---|---|---|
| `nominal_lp` | 27 | -$30,949.35 |
| `w1_003_lp` | 14 | +$1,113.95 |
| `w1_003_shock10_lp` | 15 | +$6,964.75 |

The 20 fitted-cell books refused for a missing fill quote lost -$28,239.90 together
(0 wins; 16 are `nominal_lp` books). GME alone is 7 of them
(-$21,661.35); GME and TSLA together are 8 (-$24,352.40). The refusal avoided losses
for `nominal_lp`: these were not missed gains.

## Caveats

- CR1 has no leverage correction. Leave-one-symbol-out slope ranges do not flip any
  verdict: -0.13 to +0.11 (broad212), -0.34 to -0.28 (limited36), -0.32 to +0.26 (Q1).
- Q1 two-way variance fell back to the wider one-way for the expiry-by-symbol
  sensitivity (`max_one_way`, 4 df). The main G1 Q1 band is two-way.
- G3 counts a book chosen by several methods once per method (43 filled rows in 19
  duplicate groups), which inflates name counts and ties ranks. G1 is not affected:
  the (date, symbol) clustering absorbs duplicates.
- Raw `expected` (not scaled to the filled size) gives slopes -0.009 / -0.215 / -0.011;
  broad212 changes sign against the matched 0.004 but both are indistinguishable from 0.
- The broad212 per-book audit is too large for `carry.json`. Re-running the same
  `MioBookOutcomes` node over the pinned results for `books.csv` gave: 296 filled and 535
  refused books, 0 unsettled, max filled P&L reproduction error 0.0, max credit error
  7.3e-12, bars read to 2019-12-31. Q1 bars were read to 2021-03-31.
- The run's code was uncommitted when it ran; it is committed together with this report.
  The gate verdicts are not printed in `result.json`; they follow from `carry.json`
  and the document's `notes`.
- The brief's phrase "20 fitted-cell GME/TSLA books" does not match the data: the 20
  books span 15 symbols, so the figure here is the fitted, missing-quote group as a whole.

## Files

- `books.csv`: one row per decided book (1,090 = 831 + 180 + 79), re-derived by re-running
  `MioBookOutcomes` on the three digest-verified results; the last column is the refusal
  reason. `outcome` is the saved P&L for filled books and the decision-price
  counterfactual for refused ones.
- `artifact-index.json`: SHA256 of every file here, plus the run and document hashes.
- Evidence: `/home/russell/data/index_options/mio-calibration-2019-2021q1-20261030/`
  (`carry.json` holds every statistic; reviews in `reviews/`).
- Memo: `../../memos/2026-10-30-mio-expected-calibration.md`.

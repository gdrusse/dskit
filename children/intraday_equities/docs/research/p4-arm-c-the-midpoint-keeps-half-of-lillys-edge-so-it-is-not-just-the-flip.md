# P4 arm C: the midpoint keeps half of Lilly's edge, so it is not just the flip

Date: 2026-09-03. Reads nothing at or after 2026-03-01.

## Question

Our only win is Lilly, one and two minutes ahead. The price we use is the
last trade of the minute, which jumps between the buyer's and the
seller's price even when nothing changed. Is the win that jump?

## Finding

No. Eight runs: Lilly and Exxon, one and two minutes ahead, two models,
each done twice — once on the last traded price, once on the midpoint of
the buy and sell prices — over the same sixteen months, the same twelve
test periods and the same rows, with any minute lacking a two-sided
quote dropped from both sides first.

| ahead | model | traded price | midpoint |
|---|---|---|---|
| 1 | simple | pass +0.758% | pass +0.309% |
| 1 | tree | pass +0.834% | fail +0.447% |
| 2 | simple | fail +0.250% | pass +0.261% |
| 2 | tree | fail -0.480% | fail -0.284% |

Lilly still beats a flat average guess on the midpoint in two settings of
four, one of them a setting the traded price itself fails. The midpoint
number is about two fifths of the traded-price one at a minute ahead:
the jump inflates the win, it does not create all of it. Exxon fails on
both prices; the pair together fails everywhere.

The caveat is the window. Buy and sell prices only exist back to November
2024, so these eight runs carry about half the evidence of the long grid.
Read the four pairs against each other and against nothing else.

## Sources

- `RESULT-P1-grid.md`, the eight `p4mid` rows and the section after them.
- `configs/run-p4mid-h{01,02}-{ridge,lgbm}-{close,mid}.json`; ADR-0070.
- `docs/research/p4-result-the-one-minute-flip-is-in-the-print-not-the-value.md`,
  the flip measurement this run tests.

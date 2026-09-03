# RESULT — P1 grid: row spacing crossed with look-ahead

One line per cell. `s` is the row spacing in minutes, `h` the look-ahead
in minutes. Every cell is scored on the SAME 30-minute instants, over the
same 20 folds, the same five stocks and the same features, so only the
spacing, the look-ahead and the model differ (ADR-0065).

Per stock and for the group the entry is `verdict r2oos% slope`:

- **verdict** — ADR-0067's honest test: PASS needs BOTH the pooled
  Diebold-Mariano t >= 1.645 and the across-fold t >= 1.729 against the
  fold's own training mean. Anything else is `fail`.
- **r2oos%** — out-of-sample skill against that mean, in percent.
  Positive means the forecast beat a flat average guess.
- **slope** — ADR-0068's calibration slope of outcome on forecast.
  About 1 means the predicted size is usable; about 0 means only the
  order is. (The group column has no slope of its own.)

`xs_ic` is ADR-0068's cross-stock ordering score: the rank correlation
across the five stocks WITHIN each instant, with its t.

These verdicts are P5's rule alone. The many-attempts bar (ADR-0069) is
applied to the whole family at the end of the grid and is stricter.

`price` is the price the label and every lag return are built from:
`close` is the last trade of the minute, `mid` the midpoint of the last
two-sided NBBO quote inside it. `window` is the walk's first fold cutoff,
its last validation date and the fold count. A row is comparable ONLY
with rows sharing its window: the mid rows and their close twins sit on
the shortened quoted window and must never be read against a long-window
number.

| s | h | model | price | window | AAPL | JPM | LLY | WMT | XOM | GROUP | xs_ic | wall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 1 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail +0.0435 +0.74 | fail +0.0616 +1.10 | **PASS** +0.2951 +2.29 | fail +0.0635 +1.00 | fail -0.0690 -0.03 | **PASS** +0.0799 | +0.0380 (t 7.85) | 395s |
| 5 | 1 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail +0.0202 +0.98 | fail +0.0210 +0.73 | **PASS** +0.7521 +1.58 | fail +0.1271 +0.82 | fail -0.0209 +0.47 | **PASS** +0.1839 | +0.0397 (t 8.12) | 370s |
| 5 | 2 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.0785 +0.19 | fail +0.0083 +0.44 | **PASS** +0.1043 +1.57 | fail +0.0113 +0.80 | fail -0.0712 -0.58 | fail -0.0011 | +0.0229 (t 4.51) | 373s |
| 5 | 2 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail -0.0126 +0.60 | fail +0.0062 +0.36 | **PASS** +0.2156 +1.11 | fail +0.0221 +0.94 | fail -0.0336 +0.23 | fail +0.0482 | +0.0186 (t 3.76) | 380s |
| 5 | 3 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.0600 +0.14 | fail +0.0347 +1.02 | fail +0.0733 +1.94 | fail +0.0271 +0.66 | fail -0.0526 -0.81 | fail +0.0043 | +0.0156 (t 3.13) | 375s |
| 5 | 3 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail +0.0273 +0.95 | fail +0.0643 +0.26 | fail +0.0508 +0.53 | fail -0.0688 +0.09 | fail -0.0509 +0.37 | fail +0.0055 | +0.0138 (t 2.86) | 363s |
| 5 | 5 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.1006 -0.19 | fail -0.0258 -0.08 | fail -0.0905 -0.50 | fail -0.0482 -0.09 | fail -0.0737 -0.47 | fail -0.0686 | +0.0007 (t 0.13) | 367s |
| 5 | 5 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail -0.1200 +0.55 | fail -0.0921 -0.47 | fail +0.1738 +1.22 | fail +0.0276 +0.20 | fail -0.0709 -0.28 | fail -0.0204 | +0.0147 (t 3.00) | 363s |
| 5 | 10 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.1863 -0.34 | fail +0.0625 +0.80 | fail -0.2069 -0.61 | fail -0.1122 -0.00 | fail -0.0756 -0.23 | fail -0.1050 | -0.0057 (t -1.13) | 380s |
| 5 | 10 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail -0.2631 -0.12 | fail +0.0565 +0.99 | fail +0.0478 +1.00 | fail -0.0356 -0.48 | fail -0.0636 -0.20 | fail -0.0603 | +0.0093 (t 1.85) | 373s |
| 5 | 1 | ridge | close | 2025-05-05→2026-02-22 (12f) | — | — | **PASS** +0.7584 +1.57 | — | fail -0.7715 -0.24 | fail -0.0175 | unusable (2 names) | — |
| 5 | 1 | ridge | mid | 2025-05-05→2026-02-22 (12f) | — | — | **PASS** +0.3089 +2.08 | — | fail -0.0661 +0.09 | fail +0.0991 | unusable (2 names) | 169s |
| 5 | 1 | lgbm | close | 2025-05-05→2026-02-22 (12f) | — | — | **PASS** +0.8340 +0.94 | — | fail -0.3545 -0.08 | fail +0.2091 | unusable (2 names) | 173s |
| 5 | 1 | lgbm | mid | 2025-05-05→2026-02-22 (12f) | — | — | fail +0.4472 +0.82 | — | fail -0.3821 -0.13 | fail -0.0019 | unusable (2 names) | 167s |
| 5 | 2 | ridge | close | 2025-05-05→2026-02-22 (12f) | — | — | fail +0.2496 +1.09 | — | fail -0.5410 -0.75 | fail -0.1713 | unusable (2 names) | 166s |
| 5 | 2 | ridge | mid | 2025-05-05→2026-02-22 (12f) | — | — | **PASS** +0.2610 +2.24 | — | fail -0.2156 -0.20 | fail -0.0039 | unusable (2 names) | 168s |
| 5 | 2 | lgbm | close | 2025-05-05→2026-02-22 (12f) | — | — | fail -0.4796 +0.08 | — | fail -0.4026 -0.36 | fail -0.4437 | unusable (2 names) | 168s |
| 5 | 2 | lgbm | mid | 2025-05-05→2026-02-22 (12f) | — | — | fail -0.2839 +0.28 | — | fail -0.5326 -0.49 | fail -0.4177 | unusable (2 names) | 166s |
| 5 | 20 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.2841 -0.20 | fail -0.0400 +0.41 | fail -0.1938 -0.06 | fail -0.1825 +0.11 | fail -0.1986 -0.44 | fail -0.1809 | +0.0038 (t 0.74) | 429s |
| 5 | 20 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail -0.2658 -0.29 | fail +0.1084 +1.37 | fail -0.0511 +0.30 | fail -0.1439 -0.19 | fail +0.0270 +0.54 | fail -0.0645 | +0.0032 (t 0.66) | 374s |
| 5 | 30 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.1785 -0.42 | fail +0.0543 +0.74 | fail -0.0461 +0.36 | fail +0.0199 +1.06 | fail -0.1747 -0.38 | fail -0.0321 | +0.0041 (t 0.82) | 387s |
| 5 | 30 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail -0.2214 -0.16 | fail -0.1207 +0.30 | fail -0.3748 +0.20 | fail +0.0167 +0.28 | fail -0.4301 -0.32 | fail -0.1638 | +0.0017 (t 0.33) | 393s |
| 5 | 60 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.2325 -0.49 | fail +0.1710 +1.05 | fail -0.0522 +0.49 | fail -0.0508 +0.86 | fail -0.0836 +0.47 | fail -0.0098 | +0.0065 (t 1.06) | 404s |
| 5 | 60 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail -0.4530 -0.22 | fail +0.2095 +0.78 | fail -0.3410 +0.29 | fail -0.3427 +0.06 | fail -0.4493 +0.16 | fail -0.2462 | +0.0087 (t 1.48) | 388s |


---

## The midpoint test (P4 arm C), read against its own control

Eight cells above, `p4mid`. Lilly and Exxon are the only two names with
a full minute-by-minute record of the buy and sell prices, so only they
are scored. Each of the four settings — one and two minutes ahead, the
simple model and the tree model — was run twice: once on the last traded
price of the minute, once on the midpoint of the buy and sell prices,
over the same shortened dates, the same twelve test periods and the same
rows. A minute with no usable two-sided quote is dropped from both sides
before anything is built, so the two prices are judged on exactly the
same instants.

| ahead | model | last traded price | midpoint |
|---|---|---|---|
| 1 | simple | **pass**, +0.758% | **pass**, +0.309% |
| 1 | tree | **pass**, +0.834% | fail, +0.447% |
| 2 | simple | fail, +0.250% | **pass**, +0.261% |
| 2 | tree | fail, -0.480% | fail, -0.284% |

**The answer is no: the win is not simply the price flip.** Lilly's edge
survives on the midpoint in two of the four settings — including one, two
minutes ahead with the simple model, where the traded price itself fails.
It is smaller on the midpoint (about two fifths of the traded-price
number one minute ahead), which is what the flip diagnostic predicted:
the flip inflates the measured win, it does not create all of it.

Exxon fails on both prices at both look-aheads, as it did on the long
window. The group of two fails everywhere.

One caveat that cuts both ways: the quoted window is sixteen months, so
every one of these eight cells has about half the evidence the long-window
grid rows have. Read the four pairs against each other, never against a
row from the twenty-fold table.

## The many-attempts bar, applied to everything run tonight

Twenty-four walks, 120 cells (one per name and for the group), all of
them entered in the attempt ledger. Every family is resampled together by
flipping whole trading days, so two neighbouring look-aheads cost barely
more than one attempt. The mark is the 95th percentile of the best cell
under pure luck, floored at a t of 3.

Three cells clear it, all of them one minute ahead, on five-minute rows,
on the traded price, over the twenty-fold window:

| unit | cell | t | adjusted p | skill [lower band] |
|---|---|---|---|---|
| LLY | s05 h1 tree | +5.14 | 0.0001 | +0.750% [+0.510%] |
| LLY | s05 h1 simple | +4.38 | 0.0001 | +0.293% [+0.183%] |
| GROUP | s05 h1 tree | +4.02 | 0.0008 | +0.184% [+0.109%] |

Nothing else does — not two minutes ahead, not any longer look-ahead, and
**not one midpoint cell**. The best midpoint cell reaches a t of about 2,
below the mark of 3; so does its own traded-price control on the same
short window. That is a statement about the sixteen-month window, not
about the midpoint: the shortened runs simply do not carry enough
evidence to clear a bar built for 120 attempts. The midpoint question is
settled by the paired table above; the "is there an edge at all"
question is not settled by these eight runs.

Per family: LLY c* 2.800 (24 cells, worth about 20 independent tries),
GROUP c* 2.795, XOM c* 2.773, JPM c* 2.661, WMT c* 2.658, AAPL c* 2.627.
The ledger is `children/intraday_equities/docs/decisioning/attempts.jsonl`,
one line per distinct cell.

## One-minute rows do not fit this machine

The next block of the grid — a feature row every minute — is unrun. It
is not slow, it is fatal: the run holds every minute of the tape rather
than every fifth, and it takes the whole virtual machine down before it
finishes a single test period. It did so twice, once earlier tonight
(which is the run that vanished without a directory) and once on a
measured retry, which ended in a WSL service failure needing a full
restart. Nothing in the one-minute block is reportable and nothing should
be attempted again on this box until the run reads the tape in pieces
instead of all at once.

---

## The feature blocks, run against their own control

Every row here is a five-stock walk over the twenty post-COVID folds,
scored on the same half-hour instants as the grid above. `blocks` names
the switchable input blocks that were on: `none` is the control, `tod`
the clock and calendar block, `bar` the bar-derived block, `cross` the
market and sector block, `all` the three together (ADR-0071). Only
`blocks` and the look-ahead move within a model.

These rows are NOT comparable with the grid above on the two clock
columns: ADR-0071 also fixed `tod_sin`/`tod_cos`, which used to wrap a
whole circle so the open and the close sat at the same point. That is
why the control is re-run rather than read off the earlier table.

| blocks | s | h | model | price | window | AAPL | JPM | LLY | WMT | XOM | GROUP | xs_ic | wall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | 5 | 3 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.0608 +0.05 | fail +0.0353 +1.29 | fail +0.0418 +1.55 | fail +0.0256 +0.65 | fail -0.0544 -0.70 | fail -0.0020 | +0.0134 (t 2.65) | 306s |
| tod | 5 | 3 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.0886 -0.12 | fail +0.0232 +1.26 | fail +0.0795 +1.81 | fail +0.0353 +1.03 | fail -0.1076 -1.07 | fail -0.0117 | +0.0134 (t 2.62) | 316s |
| bar | 5 | 3 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.0443 +0.36 | fail +0.0372 +1.13 | fail +0.0717 +2.01 | fail +0.0258 +0.51 | fail -0.1122 -0.98 | fail +0.0020 | +0.0115 (t 2.20) | 313s |
| cross | 5 | 3 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.0572 +0.09 | fail +0.0281 +1.14 | fail +0.0449 +1.54 | fail +0.0206 +0.59 | fail -0.0583 -0.68 | fail -0.0036 | +0.0134 (t 2.66) | 304s |
| all | 5 | 3 | ridge | close | 2022-05-06→2025-10-17 (20f) | fail -0.0838 -0.17 | fail +0.0066 +1.51 | fail +0.0968 +2.05 | fail +0.0426 +0.75 | fail -0.1845 -1.63 | fail -0.0232 | +0.0142 (t 2.72) | 327s |
| none | 5 | 3 | lgbm | close | 2022-05-06→2025-10-17 (20f) | fail +0.0233 +0.94 | fail +0.0644 +0.26 | fail +0.0725 +0.60 | fail -0.0496 +0.23 | fail -0.0551 +0.35 | fail +0.0104 | +0.0139 (t 2.84) | 299s |

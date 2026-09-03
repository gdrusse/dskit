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

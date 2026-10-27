## TL;DR

The companion [CSV](2026-10-27-2019-h1-h2-ticker-dte-performance.csv) lists the selected 65-input M53 forecast's **2019 H1 and 2019 H2 loss against raw market, calibrated market, and a per-name empirical forecast** for every ticker and days-to-expiry (DTE) band in the 214-name training cohort. A deliberately descriptive screen finds **21 ticker–DTE cells across 15 tickers** with enough rows and positive model and blend edges in both halves. These are research follow-ups, **not investable units**: 2019 is development/warm-up, H2 was previously viewed, and no cell-level multiplicity or trading-economics gate has passed.

## Execution contract

Path [A0002](../decisioning/path.csv) makes threshold-weighted CRPS (twCRPS) the primary proper forecast loss; **lower is better**. Path A0003 sets development in 2017–2019, scored validation in 2020–2023, and keeps 2024–2025 and 2026 sealed. Paths A0004/A0008 keep 2019 warm-up unranked for official promotion; A0005 requires paired skill against market and empirical references over full validation and at least three of four years, with two-way clustered intervals. This report does not alter those decisions. The [matched-universe memo](2026-10-27-2019-h2-matched-universe-comparison.md) explains the different historical 2020–2023 result and why its edge cannot be subtracted from this 2019 score.

This is the **broad-trained 214-name, OI-free 65-input, seed-29 MLP64 plus M53 rolling-dispersion blend** in both halves, not the earlier 90-input seed-11 validation winner or the 117-name H2 comparison fit. The frozen panel SHA-256 is `75b37d0bccb375321ca8074797a5bb87d34c36e044d1f0fbfaa212750a6a96a2`. H1 quote dates are 2019-01-02–2019-06-28 with settlements before 2019-07-01; H2 quote dates are 2019-07-01–2019-12-31 with settlements before 2020. The H1 M53 result has 76,033 market-comparable rows on 123 quote dates across 166 tickers; H2 has 82,242 rows on 125 dates across 164 tickers. The same 214 tickers, panel, 65 inputs, architecture, seed and optimizer settings were used; H2's later chronological fit includes H1 as training evidence. H1 helped select M53 from the fixed method menu, and H2 had already been viewed in earlier research. Neither is an independent test of a tradable rule.

## Derivation and CSV fields

On 2026-10-27 I joined the two saved M53 `ticker_dte.csv` score tables on `(symbol, calendar-DTE band)`, selecting only whole-distribution `twcrps` rows for raw market (`market`), calibrated market (`market_cal`), per-name horizon empirical (`baseline`), and standalone fitted model (`model`). I read **aggregate score tables only**, not market or Parquet rows. The output has one row for each of **214 training tickers × 8 DTE bands = 1,712 cells**, including blanks where a half has no market-comparable score. A blank is **not zero loss or proof of absent option history**. Its `coverage_status` is `both`, `h1_only`, `h2_only`, or `neither`; there are respectively **1,171, 49, 42, and 450** cells. One-row source cells have blank `t` values because their standard error is undefined; the CSV preserves that blank.

For each half, `*_m53_twcrps` and the three named comparator losses are means on **the same row set**. The `*_m53_vs_*_pct` columns copy the source edge percentage and `*_m53_vs_*_t` copy its **unadjusted descriptive** t ratio. `*_base_model_twcrps` is the standalone model comparator loss on those rows; its three edge columns are newly calculated from that loss and the same market/empirical means. A positive edge means lower forecast loss. The CSV does not contain option prices, position returns, liquidity, fills or fees.

The arithmetic is `edge % = 100 × (comparator mean twCRPS − forecast mean twCRPS) / comparator mean twCRPS`. For example, pooled H2 at **43–49 DTE** has M53 loss `0.482117295851499` and calibrated-market loss `0.4858420016110503` across **7,836 rows**, so `100 × (0.4858420016110503 − 0.482117295851499) / 0.4858420016110503 = +0.76665%`. This is a relative forecast-loss reduction, not an investment return.

## What the scores show

The following pooled DTE rows come from each run's saved `dte_region.csv`, not an average of cell percentages. Values are twCRPS edge **percent**, positive for M53; `n` is market-comparable score rows.

| Calendar DTE | H1 n | H1 raw | H1 calibrated | H2 n | H2 raw | H2 calibrated |
|---|---:|---:|---:|---:|---:|---:|
| 1–7 | 7,123 | +1.086 | −0.194 | 7,959 | +0.618 | −0.042 |
| 8–14 | 11,374 | +1.012 | −0.128 | 12,520 | +0.742 | +0.054 |
| 15–21 | 11,834 | +1.202 | −0.163 | 13,061 | +1.081 | +0.150 |
| 22–28 | 12,163 | +1.222 | −0.166 | 13,183 | +1.191 | +0.171 |
| 29–35 | 11,299 | +1.418 | −0.090 | 12,135 | +1.494 | +0.214 |
| 36–42 | 11,004 | +1.288 | +0.026 | 11,682 | +1.506 | +0.259 |
| 43–49 | 7,631 | +1.791 | +0.430 | 7,836 | +2.537 | +0.767 |
| 50–60 | 3,605 | +1.162 | +0.403 | 3,866 | +2.538 | +0.339 |

The **43–49 DTE** band has the strongest pooled calibrated edge in H2, with a positive H1 edge too; 1–7 DTE is negative against the calibrated market in both halves. Pooled DTE performance does **not** mean every ticker in the band is better modeled. The CSV exposes the ticker-level differences and missing cells.

For a conservative **research sorting rule**, `at_least_50_rows_each_half` requires at least 50 comparable rows in H1 and H2; this is a coverage heuristic, **not** a statistical power guarantee or an official Path gate. Among the **733** cells meeting it, `m53_positive_vs_raw_cal_empirical_both_halves` marks **36** whose M53 loss is below all three references in both halves. `base_model_positive_vs_raw_cal_both_halves` additionally asks that the standalone model beat both market references in both halves. **21** of the 36 pass both descriptions (`research_screen=model_supported_consistent`); **15** have a blend-only consistent edge (`blend_positive_base_not_consistent`); the other **697** covered cells have no consistent M53 edge. Another **438** scored in both halves but have fewer than 50 rows in at least one; **541** lack a score in one or both halves. A cell can have a positive edge without meeting this deliberately narrower screen.

The 21 **model-supported research cells**, grouped by calendar DTE, are: **8–14:** CRM, NEM, PNC, TEAM, VFC; **15–21:** BX, PNC, QCOM; **22–28:** BX, PNC; **29–35:** APO, BLK, CME, RCL; **36–42:** BLK, CME; **43–49:** BLK, BSX, CHTR, DIS, TGT. None fall in 1–7 or 50–60. For example, APO at 29–35 DTE has 61 H1 and 97 H2 rows and a calibrated-market M53 edge of +4.535% and +4.607%; its H2 per-name empirical edge is only +0.504%. That example shows why the full set of comparators matters more than a single large percentage. These names are **priorities for a later, independent check**, not an order list.

## Limits, checks, and handoff

There are **1,220 observed H1** and **1,213 observed H2** ticker–DTE cells; the report tests many overlapping cells. No multiplicity-adjusted error budget or dependence model was preassigned, and no A0005 two-way clustered intervals were calculated. The per-cell t ratios are therefore descriptive, not per-cell significance certificates. H1 was used for method selection; H2 was previously viewed; H2's fit uses more recent training than H1. The halves should not be treated as two independent confirmations. Even a reliable forecast-score edge would still need strikes, bid/ask, fills, costs, risk limits, and an approved MIO decision test before any investment use. This derivation did not re-audit underlying quote quality or certify a ticker's full history.

The first CSV build attempt stopped on blank one-row `t` values; it wrote no output. The corrected pass preserved those blanks, checked every source comparator's matching rows/dates/method mean and edge arithmetic, and confirmed the H1/H2 row totals. No fit, HPO, pull, registration, market-record read, holdout opening or trading run occurred for this report. The CSV is SHA-256 `3b922c5e25db844b229392a66e1df6ca1d6beed72881c48bf07febc4ee123139` (1,712 data rows).

For reproduction, the H1 source is `/home/russell/data/index_options/theta-broad-2019-feature-zoo-20261027/scores/m53/tables/ticker_dte.csv` (SHA-256 `b3b9229a38248e896443408f2b3ba400c5de2fea23cfb9d1af8756490d9bead8`); H2 is `/home/russell/data/index_options/theta-broad-2019-feature-h2-20261027/scores/m53/tables/ticker_dte.csv` (SHA-256 `f63d3e3f69392ce91138d1c023ba5ea150899f20075850cf1fcf6eb233894b78`). The pooled DTE sources have SHA-256 `46541640c74a0cf750ac4c1dbb5d299f1a34191041f7b5e3341b96e6911868e4` (H1) and `6f0022c065af0572cefe640ec0633a5526b2d838ad60b5564a1f9b4a1496fe2a` (H2). The H2 comparison's config and panel pins are in the preceding matched-universe memo. A separately registered follow-up would test these descriptive cells under the official 2020–2023 yearly validation and A0005/A0007 rules, with source quality and execution economics handled separately; this memo makes no promotion decision.

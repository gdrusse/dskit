# 15-minute kill test: development-segment run

## TL;DR

On the development period (25,270 scored rows per model), none of the three
fair-value models beat the Kalshi market's own mid-price: every Brier and
log-loss difference is positive (worse than the market), by 4.5 to 6.1 standard
errors on Brier. The buy-at-the-touch rule does not make money after fees
(-1.20 cents per trade for the implied-vol model; about zero for the other two).
Nothing here justifies reading the held-out segment. This is a development read
only: held-out was deliberately not enabled, the profit figures are an upper
bound, and the scope is narrow (three specific formulas, one regime of about 45
days, and one comparator, the market mid).

In plain terms: we asked "can a simple formula predict whether Bitcoin or Ether
ends a 15-minute window up or down better than the betting market already does?"
On the data we are allowed to look at, the answer is no.

## Execution contract

- Operation: `python -m dskit.pipeline run children/crypto_trading/configs/run-features-15m.json --asof 2026-10-08`,
  exit code 0, all 16 nodes `ok` (`result.json`, `report.md`).
- Run directory: `children/crypto_trading/pipeline_runs/crypto-features-15m-2026-10-08-d2cf6aff/`
  (`report.md`, `artifacts/kill_test/binary_score.md`, `binary_score.json`, `resolved.json`).
- Identity: document hash
  `1ade11a68c305b059fecce2117b198e2780c2d31427cf6c00485e8f0f941199b`; run hash
  `d2cf6afff4cbfe5e240494f4953481129369cb301bad2c1a161db58452fd9094`; first run
  of the series (no previous run). Worktree branch `crypto-killtest-20261007`,
  base commit `a2efd652`; the config file is unmodified.
- Date: as-of 2026-10-08. The run was journaled at 2026-10-08T23:26:58Z
  (`docs/decisioning/actions.csv`, A0028).
- Settings (owner-confirmed, read back from the config and `binary_score.json`):

| setting | value | meaning |
|---|---|---|
| store root | `/home/russell/data/crypto_trading/ob` | where the pulled data lives |
| decision leads | 2, 5, 10 minutes before close | one row per market and lead |
| development / held-out cut | `end_ms` 1789430400000 = 2026-09-15 00:00 UTC | decided before looking; development is earlier than the cut |
| take-rule margin | 0.02 | edge must exceed fee + 2 cents |
| execution lag | 5 s | order fills 5 s after the information instant |
| strike-known lag | 30 s | the strike is usable 30 s after market open |
| standard-error blocks | 86,400 s of settlement time | clustering for the "se" values |
| reported segments | `["development"]` | held-out NOT enabled |

- Data cut (`resolved.json` fingerprint): 12,908 settled market rows; 96,960 BTC and
  97,005 ETH Kalshi candle rows; 6 fee-schedule rows; Binance kline and BVOL
  stream manifests for both assets.
- Not done by design: no commit, no config edit, no new data pull, and held-out
  scores were not enabled or read (the held-out rows were still built into the
  table and counted; see the held-out coverage item below).

## Implementation evidence (did it run correctly?)

These establish that the machinery worked. They say nothing about whether the
models are any good.

- Pipeline: 16 of 16 nodes ok; total node time under 6 s.
- Output table: 38,724 rows, 61,245,889 bytes, written to
  `~/data/crypto_trading/features-15m/decision_features-crypto-features-15m-2026-10-08-d2cf6aff.jsonl`
  (file line count 38,724 confirmed). Published as onboarding source
  `features-15m` (acquisition `20261008T232708Z-backfill-d2705939`, actions
  A0029 and A0030).
- Scoring census (`binary_score.json`): 38,724 rows read; 410 not eligible;
  13,044 in the unreported held-out segment; 25,270 scored per model. Within
  the development segment, zero rows were lost to a missing forecast, fee,
  quote, market value, settle instant, or cluster, for all three models. That
  statement is development-only; see the held-out coverage hole below.
- The 410 not-eligible rows (checked in the published table): all have
  `two_sided = False` (no two-sided quote at the information instant) and all
  are lead-2 rows: 314 in development (3.7% of the 8,528 development lead-2
  rows) and 96 in held-out. Lead 2 is therefore scored on a slightly narrower,
  more liquid set of markets than leads 5 and 10 (8,214 rows against 8,528),
  which matters for reading the lead-2 P&L below.
- Held-out coverage hole (checked in the published table): 1,182 rows have
  `fair_*_status = no_spot` for all three models (1,178 eligible held-out rows
  plus 4 ineligible ones). Their close dates run from 2026-10-06 to 2026-10-08,
  but the Binance klines and BVOL files in the store end on 2026-10-05, so no
  spot exists for them. Of the 13,044 eligible held-out rows, 11,866 have all
  three forecasts (11,958 counting the ineligible ones). Before any B5 read,
  re-pull stage A to cover the missing days, or explicitly accept the gap;
  otherwise the held-out sample silently excludes the newest days.
- Stage A pulls (log: scratchpad `stageA.log`; `docs/decisioning/actions.csv`
  A0012 to A0027, 2026-10-08 01:11 to 04:00 UTC): Kalshi fee schedules and
  markets; Binance BTCUSDT and ETHUSDT 1-minute bars; Binance BTC and ETH BVOL
  (an implied-volatility index); Kalshi 1-minute candles for BTC and ETH. Every
  validation gate passed or warned; none failed.
  - Warnings: `markets-strike-type-set` 10 failing rows (markets stream), and
    `files-all-ok` 26 failing files in each BVOL stream (BTC and ETH). These
    are warn-level, so the pull continued. The 26 BVOL files were not
    investigated. The 10 markets: `artifacts/markets/excluded.json` lists 10
    markets excluded as `no_strike` (3 KXBTC15M, 7 KXETH15M, all August 2026),
    and `artifacts/markets/census.json` shows 12,918 market rows in, 12,908
    kept. The count matches the 10 strike-type warnings, but that these are the
    same rows is not proven.
  - Store size: reported 3.4 GB; `du` on 2026-10-08 shows 3.6 GB (likely
    includes the later published feature table).
- Publication check: the stage A verify reported `snapshots_checked: 8`,
  `problems: []`. After publishing, `python -m dskit.onboarding verify --root
  /home/russell/data/crypto_trading/ob` was re-run for this memo (read-only):
  `snapshots_checked: 9`, `problems: []`.
- BVOL cadence peek: about 1 row per second (86,400 rows on 2026-10-05), so the
  one-row-per-second assumption holds. The 365-day-year assumption is still
  unverified.
- Test suite (operator-reported; not re-run for this memo): child `pytest`
  642 passed, 9 skipped, 1 failed. The Binance-vision tests ran without skips.

## Failures, skips and deliberately unrun work

| item | status |
|---|---|
| `tests/test_runbook.py::test_the_sizing_copy_runs_as_pasted_and_is_repeatable_after_a_failed_attempt` | FAILED: "No module named 'dskit'" inside the test's temporary `bin/python`. This is the hourly-sizing test and looks environment-related, but the cause was not investigated. Owner said proceed. |
| 9 skipped tests | reasons not examined |
| 15M-contract trades pull (runbook 7e) | unrun; held |
| hourly history pulls (7a to 7d) | unrun |
| hourly document (B6) | unrun |
| live order books, Coinbase, Deribit | unrun |
| held-out read (B5) | not enabled. The held-out rows (13,044 eligible) were read into the table and counted, but not scored or reported; the labelled rows are in the published `features-15m` table. 1,178 of them have no forecast (coverage hole above) |
| BVOL 365-day-year assumption | unverified |
| `docs/decisioning/README.md` and `actions.csv` | modified in the worktree by the run and acquires (28 added lines, A0003 to A0030, including the register-source rows A0003 to A0011); not committed |

## Empirical results (development segment only)

Setup in plain words. The "market" probability is the Kalshi mid-price for
"YES" at the decision instant. Each model produces its own probability that YES
happens: `fair_rms` uses recent realised volatility (60-minute window),
`fair_ewma` a faster-reacting realised volatility (30-minute half-life), and
`fair_bvol` Binance's implied-volatility index. The score is Brier: the average
squared miss between a probability and what happened (1 if YES, else 0). Lower is
better, and always saying 50% scores 0.25.

Headline table (n = 25,270 per model, 45 clustering blocks; "se" is the block
standard error). Negative difference would mean the model beat the market.

| model | Brier model | Brier market | Brier diff (se) | log-loss diff (se) | trades | mean P&L per trade after fee (se) |
|---|---|---|---|---|---|---|
| fair_bvol | 0.1435 | 0.1286 | +0.0149 (0.0024) | +0.0450 (0.0067) | 18,197 | -0.0120 (0.0046) |
| fair_ewma | 0.1317 | 0.1286 | +0.0030 (0.0007) | +0.0137 (0.0028) | 13,159 | -0.0005 (0.0048) |
| fair_rms | 0.1320 | 0.1286 | +0.0034 (0.0007) | +0.0170 (0.0034) | 13,340 | -0.0006 (0.0043) |

Calibration in the large (mean model probability minus the share of YES): all
three models are within +/-0.004 of the base rate overall and at every lead
(range -0.0039 to +0.0028). This test checks only the average. It shows the
models are not biased high or low on average; it does not show they are
well calibrated. The spread of the probabilities differs: standard deviation
of the probability is 0.346 for the market, 0.340 for `fair_rms`, 0.337 for
`fair_ewma` and 0.283 for `fair_bvol` (computed on the 25,270 scored rows in
the published table). The implied-vol model is also miscalibrated in the tails
(`binary_score.json`, all leads pooled): where the market mid is in [0, 0.1),
`fair_bvol` averages 0.121 against a base rate of 0.032; in [0.1, 0.25) it
averages 0.286 against 0.173.

By lead. `artifacts/kill_test/binary_score.md` itself only has the by-lead
calibration table, not Brier or log-loss by lead. The by-lead Brier,
log-loss and P&L numbers below are from `artifacts/kill_test/binary_score.json`
(group `lead_minutes=N`, bucket `all`), which the same run wrote.

| lead (min) | n | model | Brier diff (se) | log-loss diff (se) | trades | mean P&L (se) |
|---|---|---|---|---|---|---|
| 2 | 8,214 | fair_bvol | +0.0148 (0.0029) | +0.0494 (0.0089) | 5,499 | +0.0128 (0.0045) |
| 2 | 8,214 | fair_ewma | +0.0039 (0.0011) | +0.0215 (0.0051) | 3,582 | +0.0133 (0.0070) |
| 2 | 8,214 | fair_rms | +0.0044 (0.0011) | +0.0268 (0.0059) | 3,544 | +0.0073 (0.0065) |
| 5 | 8,528 | fair_bvol | +0.0177 (0.0028) | +0.0531 (0.0074) | 6,652 | -0.0123 (0.0053) |
| 5 | 8,528 | fair_ewma | +0.0037 (0.0007) | +0.0152 (0.0033) | 5,033 | -0.0073 (0.0055) |
| 5 | 8,528 | fair_rms | +0.0039 (0.0007) | +0.0183 (0.0044) | 5,103 | -0.0055 (0.0049) |
| 10 | 8,528 | fair_bvol | +0.0121 (0.0019) | +0.0327 (0.0047) | 6,046 | -0.0341 (0.0070) |
| 10 | 8,528 | fair_ewma | +0.0016 (0.0006) | +0.0047 (0.0019) | 4,544 | -0.0038 (0.0069) |
| 10 | 8,528 | fair_rms | +0.0018 (0.0006) | +0.0062 (0.0021) | 4,693 | -0.0014 (0.0068) |

What the numbers say:

- Fair value versus mid: the model is worse than the market on Brier and
  log-loss in every model and at every lead. In `binary_score.json`, all 84
  model-by-group-by-mid-bucket cells (3 models x [all + 3 leads] x [all + 6
  buckets]) have a positive Brier difference (smallest +0.0003) and a positive
  log-loss difference (smallest +0.0002); a few small cells (4 of 84) are
  within one standard error of zero, and 27 are within two, so the claim is
  about direction, not each cell.
- The implied-vol model is clearly the worst (its pooled Brier gap is 4.4 to 4.9
  times that of the realised-vol models). The two realised-vol models are close to each other
  and closest to the market at the 10-minute lead.
- Take rule, pooled: `fair_bvol` loses 1.20 cents per trade (2.6 standard
  errors below zero). `fair_ewma` and `fair_rms` lose about 0.05 and 0.06 cents,
  which is statistically indistinguishable from zero; the 95% upper bound on
  `fair_ewma` is about +0.9 cents. So "not positive" is established for
  `fair_bvol` and unproven either way for the other two, and none shows a
  profit.
- Lead-2 P&L is positive for all three models (+0.7 to +1.3 cents per trade,
  1.1 to 2.8 standard errors). The strongest cell is `fair_bvol`: +0.0128 per
  trade, z = 0.01277 / 0.00452 = 2.82 on the block se (about 3.1 on the event
  se). A Bonferroni bar for the 9 model-by-lead cells at a 0.05 error budget is
  2.77 (two-sided, 0.05 / 9), so that one cell only just clears it, and the
  family is arguably larger since the lead split was looked at after the
  pooled result. This is an exploratory subgroup seen after the fact. The cell
  has three further problems: the models are still worse than the market on
  Brier there; fills are priced at the information-instant quote, so the
  profit is an upper bound; and all 410 not-eligible rows (no two-sided quote)
  are lead-2 rows, so lead 2 is scored on a narrower, more liquid set whose
  selection was not studied. It is not evidence of an edge and does not
  change the conclusion; it is flagged so nobody rediscovers it and mistakes
  it for a result, and so it is clear that "no positive P&L" is a pooled
  statement.

### Math layer 1: one Brier difference

Symbols: `p_i` model probability for row `i`; `m_i` market mid for row `i`;
`y_i` outcome (1 if YES settled, else 0); `n` = 25,270 rows.

    Brier_model  = (1/n) * sum (p_i - y_i)^2
    Brier_market = (1/n) * sum (m_i - y_i)^2
    diff = Brier_model - Brier_market      (negative = model better)

For `fair_bvol` (unrounded values from `binary_score.json`):

    diff = 0.143475 - 0.128625 = +0.014850

The comparison that matters is against zero. With block standard error 0.002447:

    z = 0.014850 / 0.002447 = 6.07
    95% interval = 0.014850 +/- 1.96 * 0.002447 = [0.0100, 0.0196]

Zero is well outside the interval, on the wrong side for the model. The same
calculation gives `fair_ewma` +0.00303 / 0.000669 = 4.53 (interval [0.0017,
0.0043]) and `fair_rms` +0.00336 / 0.000680 = 4.94 (interval [0.0020, 0.0047]).
Reading: the models are reliably worse than the market on this data, though
by small amounts (about 0.003 on a 0.13 scale for the realised-vol models,
roughly 2%). Three models were tested against one comparator; a Bonferroni
split of 0.05 into three gives a 2.4-sigma bar, which all three clear. The
normal approximation with only 45 blocks is rough, but the smallest z is 4.5,
which stays decisive under a heavier-tailed t distribution (44 degrees of
freedom).

### Math layer 2: why the standard-error choice matters

The report gives two standard errors for the same difference. Rows within one
calendar day (a block) are not independent: a single volatile day moves many
markets at once. Clustering by 86,400 s block respects that; clustering only by
event does not.

    fair_bvol:  block se = 0.002447, event se = 0.000722   ratio = 3.39
    fair_ewma:  block se = 0.000669, event se = 0.000509   ratio = 1.31

Using the event se would give z = 0.01485 / 0.000722 = 20.6 for `fair_bvol`,
overstating confidence by a factor of 3.4. The block se is the conservative
figure and is the one the decision uses. Plain-language reading: 25,270 rows
behave more like 45 days of weather than like 25,270 independent coin flips, and
`fair_bvol` has its errors bunched by day (inference from the ratio; the
mechanism was not tested). Note that even this conservative figure leaves the
conclusion unchanged.

## Caveats that bind the interpretation

Carried from the runbook (`docs/plans/2026-10-06-wsl-data-pull-runbook.md`, B5 and
Known issues) and not removed by this run:

- Fills are priced at the quote at the information instant. A real order fills 5 s
  later, so reported profits are an upper bound. Every P&L here is therefore
  optimistic, and the negative results are not weakened by this.
- The fee schedule used is today's; fees on historical days may have differed.
- The 60-second settlement average is not fully modelled (the "Jensen gap").
- Time to settlement (`tau`) is taken from the event data (`E`).
- For these 15-minute up/down markets, the strike drops out of the moneyness
  term (it equals the opening index level, anchored via `anchors`).
- Latency slippage is not modelled.
- Development is one period of 45 blocks (about 45 days); one regime. A
  different volatility regime could give different ratios.
- The models share one test sample and one comparator; this is predictive
  evidence only, not an explanation of why they underperform.

## Reproducibility and handoff

- Reproduce: same command above with `--asof 2026-10-08`, against the same
  store. Expect the same document hash (`1ade11a68c30...`, full hash above)
  and, with the stage A data unchanged, the same run hash. Data fingerprints are
  in `resolved.json`: markets sha256
  `715e4faf257cd3155b6c77f89e091d5499c93bfcc6fbfaf96a0563aa925ae7f7` (12,908 rows),
  BTC candles `adb35becb77736cdf7b40e9e35f6d29376cda72a7c70794a418c5792b3db53f0`,
  ETH candles `531b1a4ac8eaa943ee24b562c5b51dde9b973d3e4e252f0640d9a69fac41d261`,
  fee schedules `cbb15ebbda1fd2f99860a23d896c2cf491619fedb06a6b59af8ea416c3927a7b`.
  The written table's sha256 begins `5ee576854947` (from the run log).
- Git state: nothing committed. Uncommitted changes are the journal edits to
  `children/crypto_trading/docs/decisioning/README.md` and `actions.csv`, plus
  this memo (new). The pipeline run directory and the data store are ignored
  artifacts, not tracked.
- Held-out status: read into the table and counted, but not scored or reported
  (13,044 eligible rows after 2026-09-15; the labelled rows are in the
  published `features-15m` table, 1,178 of them without forecasts, see the
  coverage hole above). To read it per runbook B5: add `"heldout"` to
  `kill_test.params.report_segments`, commit that edit, run once, read once,
  never move the cut or retune. Runbook B5 (lines 305 to 311) says only a
  negative held-out Brier difference with a positive after-fee profit,
  each by a few block-clustered standard errors, survives. That is runbook
  text. That reading step is not warranted is this memo's own judgement: on
  the pooled development numbers no model shows a negative Brier difference
  or a positive after-fee profit. The lead-2 P&L cells are positive, but not
  alongside a better Brier. Before any B5, close the held-out coverage hole.
- Open items: investigate the failing runbook test (`No module named 'dskit'`
  in the temp `bin/python`); decide whether the 10 strike-type and 26 BVOL-file
  warnings need a look; verify the BVOL 365-day year before any hourly work.
- Next authorized action: none requested. Any move to the hourly document or to
  the held-out read is an owner decision.

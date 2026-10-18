# RE-ENTRY — crypto_trading

Updated: 2026-10-18 Eastern by GPT-6.
The replaced note is archived at docs/archive/re-entry/crypto_trading.md.

## Now

- Focused follow-up to the 57-method aligned study completed: 20 settings
  (16 perturbations and 4 exact controls), 40 JSON runs and 240 output files.
  Complete 4,000 asset/lead/bin cells, 200 leads, 400 asset/leads and
  2,000 calibration rows, plus forecasts and contract scores.
- Parents 24/48/44/38: correction strength; ARD feature pruning; fixed
  volatility regime boundaries. No new architecture or feature source.
- Same causal warm-up February 19-April 19: 52,999 forecasts / 11,213 contracts.
  Validation May 2-July 31: 84,728 forecasts / 17,158 contracts.
  All correction/comparator fits freeze at May 2 with a one-day embargo.
  Inherited GRU inputs retain their prior causal update schedule.
- Warm-up stability selection was committed at ba43561f before validation:
  4 global settings and 8 model/pocket pairs across 3 distinct regions.
  Four global choices are all slightly worse than their exact parents.
- All 8 pockets lower mean validation log loss versus raw/calibrated market;
  7 retain raw-score gains in both validation halves. None passes the
  daily or robust selected-cell screens against any comparator.
- Best fixed local research lead: ETH / 11m / 10-20% market probability.
  All four families selected it; both validation halves improve versus
  raw market and each parent. 689 contracts, market YES 15.41%, actual 14.22%.
  This remains tentative; no validated pocket or promoted tuned model.
- Retain original method 24 as reference. Stop more strength/pruning/boundary
  tuning on the same history. Exact controls reproduce the earlier predictions.
- Candidate 32fe1853; review receipts in memo/evidence.
  Deferred Minor: 16 JSON probability-node notes retain old 0.5 wording;
  actual .25/.75 expressions, setting names and tested outputs are correct.
  127 focused tests, six synthetic stability probes and Ruff passed.
  Author audit checked all 240 outputs and 501 fit censuses.
- One initial writer attempt failed because the results directory was absent.
  Failed record retained; retry used unused placement. 40 completed runs.
- Market baseline is the one-minute candle-midpoint proxy, Kalshi BTC/ETH only.
  Dates were previously inspected; historical replication is not untouched
  confirmation. Original source generation and GRU training were not rebuilt.
- Artifacts: /home/russell/data/crypto_trading/research/pocket-tuning-20261018
  Previous aligned study: sibling warmup20-20261018.
- Separate dirty /home/russell/dskit-crypto-prew4-oos remains untouched.

## Next

- Preserve the ETH 11m / 10-20% region and its settings if following it up.
  Do not pick another setting from the full validation map.
- Next useful investigation: decision-time probability quality and separately
  timed information, then a fixed comparison on unexamined outcomes.
  A new parameter sweep on this history does not resolve the uncertainty.
- Legacy frozen forward protocol remains unchanged at January 15, 2027 UTC;
  it was not required for the completed historical comparison. No automation.

## Blocked / owner decisions

- No blocker to completed study; no trading or production promotion.
- Reproduction requires local acquired data and retained analysis artifacts.
- Prior owner-only decisions and Path rows remain unchanged.

## Read first

- docs/memos/2026-10-18-pocket-tuning.md
- docs/memos/2026-10-18-pocket-tuning-evidence.json
- docs/memos/2026-10-18-warmup20-aligned-methods.md
- configs/run-pocket-tuning-validation-03-p24-strength-075.json
- docs/research/pocket-tuning/2026-10-18-synthesis.md

## Do not

- Read protected outcomes, change frozen protocols or retune on validation.
- Treat consistent means or a frequency gap as established prediction edge.
- Infer profitability, trade, deploy or promote an unvalidated model.
- Hand-edit generated journals, Path rows or another session's checkout.
- Overwrite completed outputs or discard the initial failed attempt.

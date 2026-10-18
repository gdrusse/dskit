# RE-ENTRY — crypto_trading

Updated: 2026-10-18 Eastern by GPT-6.
The replaced note is archived at docs/archive/re-entry/crypto_trading.md.

## Now

- Completed 20 new approaches (38-57) and realigned all 37 earlier experiments:
  114 JSON stage runs, 684 output files, 11,400 cells, 570 leads, 1,140 asset/leads,
  5,700 calibration rows, plus every forecast and contract score.
- Warm-up choices use February 19-April 19 only: 52,999 forecasts / 11,213 contracts.
  Validation May 2-July 31: 84,728 forecasts / 17,158 contracts / 91 days. All candidate
  and comparator fits freeze at May 2 on 105,023 earlier rows with a 1-day embargo.
  Inherited GRU inputs retain their original causal update schedule.
  Market benchmark is the acquired one-minute candle midpoint proxy.
- Selection was committed before validation: 24/05/22 and 44/38/48, plus 18 cells.
  All 6 pass daily global raw/calibrated-market screens; none beats prior sparse
  models clearly. No selected cell passes. Histories were previously explored.
- Keep 24 as reference; 48 volatility-expert correction is the new follow-up.
  Validation deltaLL vs market=-.000791 (0.174% lower); weekly adjusted p=.0492
  is borderline. New 44/38 are close alternatives, not established replacements.
- Full 5,700-cell scan:02 and 57 hit ETH 5m 90-100 daily, both fail weekly.
  02 keeps the same improvement direction across stages; 57 was worse in warm-up.
  Earlier ETH 5/8 upper-tail gaps persist descriptively, without a robust local
  prediction-score claim. Earlier combined-period recommendations superseded.
- Candidate c505e7e1; independent review receipts are in memo/evidence.
  238 focused tests/Ruff passed; all 684 outputs and 1,971 fit censuses audited.
  No full suite or protected forward-data read.
- Artifacts: /home/russell/data/crypto_trading/research/warmup20-20261018
  Earlier batches: sibling predictability15-20261017 and calibration-next20-20261017.
- Separate dirty /home/russell/dskit-crypto-prew4-oos remains untouched.

## Next

- Continue narrowly with 24 as reference and 48 as challenger; stop broad
  architecture sweeps on this same history.
- If asked to continue, test cell-selection stability across warm-up subperiods
  and separate calibration-only opportunities from extra-information claims.
  Keep subsequent validation separate; reused periods are not untouched tests.
- Legacy frozen predictive protocol remains unchanged, with its existing
  January 15, 2027 UTC endpoint. It is not a prerequisite for this completed
  historical training/validation comparison. No new automation was started.

## Blocked / owner decisions

- No blocker to completed research. No production promotion or trading.
- Source data/local artifacts are required for reproducing the JSON runs.
- Earlier owner decisions remain archived: ADR-0261, tail wings, BVOL missing
  handling and owner-only Path rows.

## Read first

- docs/memos/2026-10-18-warmup20-aligned-methods.md
- docs/memos/2026-10-18-warmup20-evidence.json
- configs/run-warmup20-validation-48-volatility-expert-newton.json
- configs/run-warmup20-validation-24-newton-logloss-sparse.json
- docs/research/warmup20/2026-10-18-synthesis.md

## Do not

- Read protected forward outcomes or modify frozen protocols.
- Promote post-validation cell discoveries as preselected successes.
- Infer profitability, deploy or replace a model from these historical scores.
- Hand-edit generated journals, Path rows or another session's checkout.
- Bypass onboarding or overwrite existing outputs.

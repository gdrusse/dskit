# RE-ENTRY — crypto_trading

Updated: 2026-10-18 Eastern by GPT-6.
The replaced note is archived at docs/archive/re-entry/crypto_trading.md.

## Now

- Prediction-only research now includes 37 experiments: original 15, 20 additional
  approaches and 2 penalty-scale controls. New 22 JSONs saved 132 complete outputs:
  2200 asset/lead/bin cells, 110 lead rows, 220 asset/leads, 1100 calibration rows,
  plus forecasts and contract scores.
- Same historical cohort: 137,737 forecasts / 28,373 Kalshi BTC/ETH contracts,
  February-July 2026, leads 2/5/8/11/14. No Polymarket or profitability claim.
- New methods: 10 beat raw market overall; 6 beat simple recalibration; 0 clearly
  beat previous sparse ARD under exploratory cumulative 37-method screens.
- Best new mean:method 24 sparse Newton log-loss correction, deltaLL=-0.000981
  versus market and -0.000177 versus prior ARD. Incremental adjusted p=.2069
  (weekly=.1115), so it is a research challenger, not an established replacement.
- New cell screens: 14 raw-market hits, 4 survive weekly sensitivity; 0 against
  recalibration or prior ARD. Cell family 3,700. Full 100-cell market-gap map saved.
- ETH 5m/8m in 90-100% market bins have the clearest market-only calibration gaps.
  Their actual YES frequencies are 97.23%/96.50% vs market 95.77%/94.07%.
- Candidate 89ca6c39; independent review receipts in memo/evidence.
  236 focused tests and Ruff passed; full suite deliberately not run.
- Artifacts: /home/russell/data/crypto_trading/research/calibration-next20-20261017
  Previous batch: /home/russell/data/crypto_trading/research/predictability15-20261017
- Earlier GRU research and the frozen predictive protocol remain in the separate
  /home/russell/dskit-crypto-prew4-oos checkout. It was not modified.
  Earlier W5/maker failures and collection jobs remain unchanged.

## Next

- Preserve the existing frozen predictive confirmation, one read January 15, 2027
  UTC. No future test or automation was started by either prediction batch.
- If that protocol succeeds, separately preregister one method 24 versus method 05
  versus market-calibration comparison on a NEW untouched interval, with the
  proposed ETH regions fixed before reading outcomes.
- If it fails its own criterion, stop this modeling line; repeated-history
  significance screens cannot supply independent confirmation.

## Blocked / owner decisions

- No blocker to completed research. No candidate production promotion.
- Historical/adaptive reuse is the main limitation. Source-data and local
  research artifacts are required to reproduce these runs on another machine.
- Earlier owner decisions remain archived: ADR-0261, tail-loss wings, BVOL missing
  handling and owner-only Path rows.

## Read first

- docs/memos/2026-10-17-calibration-next22-methods.md
- docs/memos/2026-10-17-calibration-next20-evidence.json
- docs/memos/2026-10-17-predictability-15-methods.md
- configs/run-calibration-next20-24-newton-logloss-sparse.json
- docs/research/calibration-next22/2026-10-18-synthesis.md

## Do not

- Read protected forward outcomes or modify the frozen protocols.
- Infer profitability, trade, deploy or replace the incumbent from these scores.
- Treat the two technical method 16 failures as completed experiments.
- Hand-edit generated journal files, Path rows or another session's checkout.
- Bypass onboarding or overwrite existing output runs.

# RE-ENTRY — crypto_trading

Updated: 2026-10-17 Eastern / 2026-10-18 UTC by GPT-6.
The replaced note is archived at docs/archive/re-entry/crypto_trading.md.

## Now

- Fifteen new prediction-only methods completed: runnable JSONs and 90 output
  files, covering 137,737 forecasts / 28,373 Kalshi BTC/ETH contracts, Feb-Jul 2026.
- All 15 use the same causal cohort and compare with raw midpoint and a fitted
  market-only calibration benchmark. Saved: 1,500 asset/lead/bin cells, 75 lead,
  150 asset/lead and 750 calibration rows, plus forecast/contract scores.
- Four methods improve beyond calibration overall in exploratory historical
  screens: Bayesian ridge, ARD, elastic net and Huber boosting. Best new ARD
  log-loss difference: -0.000804 vs raw market, -0.000583 vs recalibration.
- Zero cells establish incremental improvement beyond recalibration after the
  1,500-cell screen. Reused history and adaptive iteration prevent confirmation.
- No Polymarket results, trading evaluation, incumbent replacement or deployment.
- Candidate f6857cb0; independent review receipt is in the memo/evidence packet.
  167 focused tests and Ruff passed; full suite deliberately not run.
- Full run artifacts and preparation scripts:
  /home/russell/data/crypto_trading/research/predictability15-20261017
- Existing broad context: W1/W2 GRU forecasting gains; W5 blend and maker lines
  failed. Original October 12 forward test and collection jobs remain unchanged.
  October 16/17 newer research remains in /home/russell/dskit-crypto-prew4-oos;
  that separate dirty checkout was read without alteration.

## Next

- Stop expanding the model zoo. Preserve the EXISTING frozen predictive test
  whose one-read endpoint is January 15, 2027 UTC; no new test was started here.
- Its preregistration is in the separate prew4 worktree:
  children/crypto_trading/docs/memos/2026-10-16-market-residual-preregistration.md.
- If it fails its existing predictive criterion, stop this modeling line. If
  it passes, consider one frozen ARD challenger on a newly declared untouched
  interval, compared with recalibrated market and the successful incumbent.

## Blocked / owner decisions

- No blocker to this completed study. Do not confuse historical screens with
  confirmation or promote any candidate to production.
- Earlier unresolved items remain in the archived note, including ADR-0261,
  tail-loss wings, BVOL missing handling and owner-only Path rows.

## Read first

- docs/memos/2026-10-17-predictability-15-methods.md
- docs/memos/2026-10-17-predictability15-evidence.json
- docs/research/predictability-15/2026-10-18-synthesis.md
- configs/run-predictability15-05-ard-sparse-residual.json
- docs/memos/2026-10-12-w5-audit-and-forward-setup.md

## Do not

- Read protected future outcomes, alter frozen protocols or infer profit from
  prediction-score differences.
- Compare historical rows with incumbent states trained on those rows.
- Count the zero-output initial method-03 attempt as a completed method.
- Hand-edit journal ledgers/README, Path rows or another session's checkout.
- Bypass onboarding or overwrite existing result runs.

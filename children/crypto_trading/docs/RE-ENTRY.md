# Re-entry: crypto_trading

- **Status:** research only. Nothing trades or is deployed; the live-venue
  seams (`execution`, `approvals`, `coordination`, `accounting`) are
  fail-closed templates. No jobs running.
- **Continue from:** branch `claude/crypto-windows-20261009` (this page and the
  plan on top of `crypto-completion-20261008` @ `35bbca56`, which is not yet in
  `main`). Another session's checkout `/home/russell/wt/crypto-completion-20261008`
  holds uncommitted journal rows A0077-A0081 (Kalshi history sources
  registered, not acquired); expect an ID collision on merge.
- **Question:** can a simple formula beat the Kalshi 15-minute BTC/ETH
  up/down mid-price after fees?
- **Evidence so far:** no, on development data (2026-08-01 to 2026-09-14,
  25,270 rows per model): all three volatility models score worse than the
  market (Brier +0.003 to +0.015, 4.5 to 6.1 standard errors) and do not
  profit after fees. Held-out (from 2026-09-15) built, not scored. Memo:
  `docs/memos/2026-10-09-stage-a-refresh-and-15m-rerun.md` (and the 2026-10-08 one).
- **Locked decisions:** data windows in `docs/plans/2026-10-09-data-windows.md`
  (also Path row on `docs/decisioning/path.csv`).
- **Data:** store `/home/russell/data/crypto_trading/ob` (11 GB); tables
  `/home/russell/data/crypto_trading/features-15m/`. Binance 1-minute bars
  2023-06-20 to 2026-10-08; Kalshi 15-minute markets 2026-08-01 to 2026-10-09.
- **Open issues:** Binance 2026-10-09 klines and 2026-10-08 BVOL not yet
  published; one failing runbook test (`No module named 'dskit'`); config
  retire transitions not journaled; fee lookup is latest-version, not as-of;
  BVOL 365-day year unverified; child README still says nothing ran on real
  data; Kalshi start date unverified (see plan).
- **Next bounded step:** the predictive component on W1: zoo, feature
  selection, HPO, per the plan. Needs its own approved design first.

# RE-ENTRY archive — crypto_trading

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

# Active handoff — 2026-10-07: crypto_trading child + additive dskit packs, merged to main

**Landed on main.** New child `children/crypto_trading` and nine additive dskit modules (ADR-0239..0247; ADR-0241
withdrawn). Our ADRs were renumbered at merge from 0236..0244 because main took 0236..0238. Packs:
`kalshi_history`, `restwindow`, `zipcsv`, `parquet_series`, `kinds_run_write`, `vol_estimators`,
`binary_pricing`, `binary_scoring` and `fee_mechanics` (venue-neutral; Kalshi's fee mapping stays in the child).
No pre-existing dskit code changed. Child: Kalshi, Binance Vision (research licence only), Coinbase and Deribit
sources with suites; `run-features-15m.json` and `run-features-hourly.json` (point-in-time features, BRTI basis,
60 s-average lognormal fair value, fee, kill test with day-block SEs, held-out only on an explicit edit).
Child suite: 643 passed, 9 skipped.

**Why.** Journal A0001 (ranked focus) and A0002 (sources + live arbitrage snapshot). Problem: a calibrated
short-horizon BTC/ETH settlement distribution, used to quote Kalshi crypto ladders as maker.

**Review.** Sonnet builders; Sonnet skeptic loops per stream, then two-lens final loops. 0 Critical/Major on
`07e68ca` (phase-3 round 2). After the main merge, the dskit suites' FAILED set equals origin/main's
(environment: missing optional libraries). Open Minors/Nits:
- the runbook's rerun-after-kill notes (7d/7e) and the 7c memory bound;
- runbook length;
- `CeilToTick(guard_decimals=0)` snaps before the ceiling;
- restated helpers forced by "additive only" (`kalshi_history`/`restwindow`);
- one duplicate `validate_inputs`;
- for 15M markets the strike drops out of moneyness;
- fills priced at the information-instant quote, so profit is an upper bound;
- tau runs from E, not I.

The per-ADR backlog is in `TODO.md`.

**Owner decisions pending.**
- Ratify leads [2,5,10], the held-out cut 2026-09-15, margin 0.02, exec lag 5 s and strike lag 30 s.
- Set the store path `$OB`.
- Optional ADRs: a date bound for `kalshi_history` pulls, an open-to-close duration filter for the hourly
  document, and a memory budget refusal for hourly runs. The runbook's B6 gives about 9.7 KB a decision row.

**Next (on WSL).** `git pull` main. Follow `children/crypto_trading/docs/plans/2026-10-06-wsl-data-pull-runbook.md`
in order:
1. Stage A acquisitions.
2. The 15-minute features run (B1-B5).
3. Read the development kill-test report.
4. Only then decide on held-out.

Pull the 15-minute trades (about 6-8 days, 52 GB) and the hourly history last.


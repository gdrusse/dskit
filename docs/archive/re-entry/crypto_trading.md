## Archived on 2026-10-18 UTC — replaced by fifteen-method prediction study

# RE-ENTRY — crypto_trading

Updated: 2026-10-12 by Claude Opus 5.5. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/crypto_trading.md` (dskit repo).

## Now

- The predictive line (BTC/ETH minute panel, per-horizon CDF heads) is built end to end
  on dskit's panel packs (`array_panel`, `horizon_cdf`, `panel_score`, `panel_torch`;
  ADR-0260). Earlier 15-minute Kalshi kill test (2026-10-08): no fair-value model beats
  the mid after fees; development read only.
- **W1 feature selection done.** Tabular class: 13 columns (vol, clock, BVOL @60).
  Sequence class: 10 columns (vol, clock @1440). Keep rule (z = 1 SE of the paired
  per-fold difference) is ADR-0261, PROPOSED, not approved.
- **W1 zoo done**, 66 lanes. Pick: `gru-crps_tail-pooled`, -1.1% weighted CRPS vs the
  empirical baseline, better on 14 of 14 folds.
- **HPO on warm-up folds 1-4:** the default configuration won; none of the a-d bundles
  was better. No HPO change adopted.
- **W2 single read done** (stride-1 retrain in 2 blocks): 2.2177 vs 2.2448 empirical
  (-1.2%). Models saved under `/home/russell/data/crypto_trading/production/models`.
- **Seed check** (seeds 11, 29, 47): GRU beats PatchTST and 1D-CNN on 14 of 14 folds; the
  pick stands. **Wider HPO** (21 candidates incl. 16 seeded draws): no out-of-sample gain
  (tuned - default = -0.00017, SE 0.00043); default kept.
- **W4 take test done** (owner-authorized; W3 refit to 2026-07-30, panel `panel-minute-w4`):
  predictor -1.4% weighted CRPS vs empirical on W4; take-at-the-touch P&L is not
  significant (best: 5c margin, +$53 on 5,638 one-contract trades, t = 1.22). GRU is the
  best of the four fair values at every margin. W5 not read.
- **Market vs model (W4 only):** a logistic blend of mid + GRU, refit daily, out of sample at
  leads 8-14 (`run-w4-leads.json`), 2c margin: +5.2c x 2,939 trades (t 4.7); leads 2-7 weak.
  Exploratory (lead range chosen on W4); memo addendum has the checks. Rerunnable:
  `run-w4-blend.json` (ADR-0264 `SklearnExpandingRefit`): leads 8-14, 2c, +4.2c x 4,209 (t 3.8).
- Memos: `docs/memos/2026-10-11-zoo-w1-selection-and-model-pick.md`,
  `docs/memos/2026-10-11-w2-predictor-check.md`,
  `docs/memos/2026-10-12-seed-check-wide-hpo-and-w4-take-test.md`.
- Pipelines: `configs/workflow-hpo.json`, `configs/workflow-production.json` (also
  `workflow-select-{tabular,sequence}.json`, `workflow-zoo-{w1,w2}.json`) and
  `run-w4-take.json` (runbook B8). Production refuses past W2 without `allow_past_w2`
  (W4 only); W5 never runs.

## Next

W5 read once (pre-registered, `docs/memos/2026-10-12-w5-preregistration.md`): the blend rule FAILED
(leads 8-14, 2c: -0.15c x 1,874 trades, t -0.10); half-Kelly lost 45%. Rule closed; W5 is now
development data. W5 audit: no bug (`docs/memos/2026-10-12-w5-audit-and-forward-setup.md`).
- Forward test pre-registered (`2026-10-12-forward-preregistration.md`): overnight blend, leads 9-14,
  markets closing 2026-10-12..11-11; read once on 2026-11-11 (W5 configs with the cut moved; the
  holdout guard is `configs/holdout-forward.json`).
- Maker simulation on the real W5 tape (86.9M trades, 2026-10-15): no maker edge. Adverse selection (about 1.5-1.8c)
  eats the half-spread and the biases; move-with is +0.25c (t 0.3) under queue fills. Maker line closed (memo, last section).
  Research scripts: `data/crypto_trading/research/` (leads, w5, maker).
- Running ops (outside the repo, `data/crypto_trading/logs/`; heavy jobs held by `host-disk-ok.sh` when C: has < 20 GB): `books-recorder.sh` (15 s books,
  @reboot cron), `forward-pull.sh` (daily 02:15 cron, pinned worktree `wt/crypto-pull`).
  Forward GRU refit (B9) saved to `data/crypto_trading/production-fwd`.

## Blocked / owner decisions

- ADR-0261 keep rule (approve, amend or reject).
- Tail-loss wings are provisional.
- BVOL `missing: mean` handling.
- Graduating `TapeRows` to dskit.
- Path rows for the two workflow JSONs (owner-only file; pending).
- Prior kill-test items: held-out cut 2026-09-15 and whether to read it.

## Gotchas

- Data enters through onboarding: the panel is the `panel-minute` blob source
  (`configs/source-panel-minute.json`); read it by source name, never by path.
- `docs/decisioning/README.md` and `actions.csv` are hook-generated; merge through
  `tools/merge/` drivers.
- No GPU job is running. The W4 refit model is under this child's git-ignored
  `pipeline_runs/zoo-production-w4` in the W4 worktree, copied to `/home/russell/data/crypto_trading/production-w4` (pinned by `state_hash` in `run-w4-take.json`). Check `docs/decisioning/actions.csv` for the latest rows.


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


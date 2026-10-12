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
- Memos: `docs/memos/2026-10-11-zoo-w1-selection-and-model-pick.md`,
  `docs/memos/2026-10-11-w2-predictor-check.md`,
  `docs/memos/2026-10-12-seed-check-wide-hpo-and-w4-take-test.md`.
- Pipelines: `configs/workflow-hpo.json`, `configs/workflow-production.json` (also
  `workflow-select-{tabular,sequence}.json`, `workflow-zoo-{w1,w2}.json`) and
  `run-w4-take.json` (runbook B8). Production refuses past W2 without `allow_past_w2`
  (W4 only); W5 never runs.

## Next

Owner's choice: confirm the 5c take rule once on W5; or acquire book/trade data to test
maker quoting; or improve the 2-minute-lead forecast. A Kelly MIO waits for a take-rule edge.

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
  `pipeline_runs/zoo-production-w4` in the W4 worktree (pinned by `state_hash` in `run-w4-take.json`). Check `docs/decisioning/actions.csv` for the latest rows.

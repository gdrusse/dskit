# RE-ENTRY — crypto_trading

Updated: 2026-10-11 by Claude Sonnet 5.5. Rewritten whole at every `/wrap`; the note
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
- Memos: `docs/memos/2026-10-11-zoo-w1-selection-and-model-pick.md`,
  `docs/memos/2026-10-11-w2-predictor-check.md`.
- Pipelines: `configs/workflow-hpo.json`, `configs/workflow-production.json` (also
  `workflow-select-{tabular,sequence}.json`, `workflow-zoo-{w1,w2}.json`). W4 and W5 are
  locked: production refuses to run past W2 without an explicit flag, and W5 never runs.

## Next

Owner-gated: the MIO step and W4. Nothing runs until the owner rules on the items below.

## Blocked / owner decisions

- ADR-0261 keep rule (approve, amend or reject).
- Tail-loss wings are provisional.
- BVOL `missing: mean` handling.
- Whether W4 counts as "already scored".
- Graduating `TapeRows` to dskit.
- Path rows for the two workflow JSONs (owner-only file; pending).
- Prior kill-test items: held-out cut 2026-09-15 and whether to read it.

## Gotchas

- Data enters through onboarding: the panel is the `panel-minute` blob source
  (`configs/source-panel-minute.json`); read it by source name, never by path.
- `docs/decisioning/README.md` and `actions.csv` are hook-generated; merge through
  `tools/merge/` drivers.
- No GPU job is running. Check `docs/decisioning/actions.csv` for the latest rows.

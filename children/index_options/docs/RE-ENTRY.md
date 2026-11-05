# RE-ENTRY - index_options

Updated: 2026-11-04 by Claude Opus 5.5. Predictive recipe locked (A0008, ADR-0294); MIO runnable quarters complete; all MIO economics unopened.

## Now

- **Predictive lock.** Four-component M53 with causal quarterly weights; 90-input MLP at fixed epoch 1, no per-fit selection; quantile average of seeds 11/29/47 in the model slot. Post-exposure development on 2020-2023, not validation. Neither market arm blended (seed 11 only) passed. Owner also ruled: promotion and holdout retrain quarterly (A0004), and the warmup is refit at epoch 1 (A0008).
- **Skill (frozen seed 11 at 2/45/1/1, not the locked recipe, which has never been scored).** Beats horizon_empirical and market_cal; versus raw market +0.72% (-0.21, 1.65), carried by 2021. Edge over market_cal fades by year: .84, .77, .33, .25. The MLP adds 0.089% in the blend (bag from existing fits; 60-day block interval includes zero).
- **Exact-recipe caches do not exist yet.** Needed: epoch-1 warmup refits for seeds 11 and 47, seed 11's 8 quarters of 2020-2021, then all blends. The other 40 quarterly fits are already at epoch 1.
- **MIO.** Same three methods on 17 quarters run on 2026-11-04 plus retained 2021Q1, all closed and reviewed. They use the frozen seed 11 forecasts with old epoch pins (23 for 2019Q2-Q4, then 2/45/1/1), so they predate the lock. Stock lists vary by quarter under an exclusion rule whose principle is the owner's; its thresholds were agent-set.
- **Registries.** Prior coverage-v2 at revision 118; linked registry, owner-approved for repeat windows, at revision 40. The 2020Q1 original stays quarantined; use the linked re-run. 2021Q1 stays as run.
- **Not run.** 2019Q1 (no blend history before 2019). 2023Q4 (tail reaches sealed 2024).

## Next

- Produce forecast caches for the exact locked recipe, score them against A0005 and A0007 on 2020-2023 (post-exposure), and pin their hash in A0008. Then decide whether to re-run MIO on them.
- Owner decides when to open MIO economics; nothing has been opened.
- Sentinel clip needs an ADR (TorchCDF has no knob): 5 dead IV inputs, no measurable score gain.

## Blocked / owner decisions

- Universe: 68 of 118 names were ranked on data ending 2026-10-02, cut alphabetically at LLY. Results apply only to these survivors; not in the Path, and the owner has not ruled. Scores cover 110 names and 450,563 of 712,556 rows. A point-in-time universe needs a new panel.
- Open MIO economics or not. Holdout stays sealed unless the owner promotes; 2026 sealed forever.
- Whether to replace the MIO stock-exclusion rule with trade-when-quoted plus prepared splits: owner chose to keep it for now.

## Read first

- [Predictive lock](research/distribution-modeling/2026-11-05-m53-predictive-lock.md).
- [Locked Path](decisioning/path.csv).
- [Prior quarterly comparison](research/distribution-modeling/2026-11-04-quarterly-m53-market-comparison-and-2019-mio.md).

## Do not

- Open MIO economics or protected outcomes without the owner; never tail MIO logs, which contain account lines.
- Call 2020-2023 results validation, or select seeds or epochs by score.
- Run concurrent heavy jobs without a RAM budget: an out-of-memory kill hit MIO and a recorder on 2026-11-04. RAM is the limit on this desktop, and WSL writes can grow the disk file on C:.
- Rewrite closed receipts, reset registries, or re-expose a development window in the prior registry.

Evidence: `/home/russell/data/index_options/learning-diagnostics-20261104` (reports), `/home/russell/data/index_options/quarterly-prediction-cache-20261104/2019-extension` (MIO receipts) and `/home/russell/data/index_options/quarterly-prediction-cache-20261104/mio/eligibility-amendment-20261104` (amendments).

# RE-ENTRY - index_options

Updated: 2026-11-04 by Claude. Predictive recipe locked (A0008, ADR-0294); MIO runnable quarters complete; all MIO economics unopened.

## Now

- **Predictive lock.** Four-component M53 with causal quarterly weights; 90-input MLP at fixed epoch 1, no per-fit selection; quantile average of seeds 11/29/47 in the model slot. Post-exposure development on 2020-2023, not validation. No recipe change passed in the blend.
- **Skill.** Beats horizon_empirical and market_cal; versus raw market +0.72% (-0.21, 1.65), carried by 2021. Edge over market_cal fades by year: .84, .77, .33, .25. The MLP adds 0.089% in the blend.
- **Exact-recipe caches do not exist yet.** The bag was scored from existing fits, and epoch-1 refits were run for seed 11 only with CDFs deleted.
- **MIO.** Same three methods on 17 quarters run on 2026-11-04 plus retained 2021Q1, all closed and reviewed. They use the frozen seed 11 forecasts with annual epoch pins (2/45/1/1), so they predate the lock. Stock lists vary by quarter under the owner's exclusion rule.
- **Registries.** Prior coverage-v2 at revision 118; linked registry, owner-approved for repeat windows, at revision 40. The 2020Q1 original stays quarantined; use the linked re-run. 2021Q1 stays as run.
- **Not run.** 2019Q1 (no blend history before 2019). 2023Q4 (tail reaches sealed 2024).

## Next

- Produce forecast caches for the exact locked recipe and pin their hash in A0008, then decide whether to re-run MIO on them.
- Owner decides when to open MIO economics; nothing has been opened.
- Sentinel clip needs an ADR (TorchCDF has no knob): 5 dead IV inputs, no measurable score gain.

## Blocked / owner decisions

- Universe: 68 of 118 names were ranked on data ending 2026-10-02, cut alphabetically at LLY. Recorded as "conditional on survivors"; the owner has not separately ruled on it. A point-in-time universe needs a new panel.
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

Evidence: `/home/russell/data/index_options/learning-diagnostics-20261104` (reports) and `/home/russell/data/index_options/quarterly-prediction-cache-20261104/2019-extension` (MIO receipts, amendments under `mio/eligibility-amendment-20261104`).

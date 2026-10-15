# RE-ENTRY — index_options

Updated: 2026-10-15 by Claude Sonnet 5.5. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/index_options.md` (dskit repo).

## Now

The forecast question stays open and negative (no edge in 2024–25: T2, region, HAR-vs-implied
all failed; A1136–A1152). Power was the limit (~95 dates), so the owner bought **ThetaData
Options Standard on 2026-10-12 (about one month, to ~2026-11-11)** for 2016–2025 daily chains.
Last commit on main: `89bbb1d0`. Nothing real has been tested on it yet.

- **ADRs** (all approved): 0263 (the pull), 0265 (research program: H-census, H0–H7 pre-registered,
  split dev 2016–20 / validation 2021–25 run ONCE / 2026 sealed; Amendments 1–5, run book in 5).
- **Built and reviewed (Sonnet writes, Haiku reviews)**: `ThetaDataConnector` (+ `option_quote_at`,
  index underlyings), `SnapshotParquetExport` + `ThetaOptionEodExport` (per-snapshot Parquet, ~72 B/row,
  quality gates), `ChainCensus`, `ChainDayFeatures` (own IV, rates, stale-spot guard), `ChainPanelBuild`,
  `ParquetChainRows`, `RootStitch`, `ReuseDetector`, `ChainEventDetector`, `ChainStructureOrders`,
  `OptionStructureLedger`, `QuantileSortBook`; 16 H0–H7 documents + twins (`run-theta-h*.json`).
- **Pull** (in progress, runs outside the repo): driver `/home/russell/data/thetadata/driver/`
  (`start_all.sh` starts the Terminal + chain; `watchdog.sh` restarts on transient failure; `pull.log`;
  `ALERT.txt` = a hard alarm, disk stop or too many restarts). Order: tested 414 → index 12 → core2 303 →
  rest1000. Tested was 67/414 on 2026-10-15 (~2.5 units/s; ~2–3 days left for tested). Data:
  `options-{tested,index,core2,rest1000}` (onboarding stores) and `parquet-v2/<tier>` (the working copy,
  verify.jsonl beside it). Observation gz copies of verified snapshots are deleted (raw + Parquet kept).
- **Findings**: Standard has 2016-01-04 as the floor; flat files need Professional; vendor `underlying_price`
  can be stale (AAPL 2019-09-11); tickers are REUSED (AI, B: declare breaks in `root-renames.json`);
  quotes start per root (ABBV 2017); 214748.36 int-overflow sentinels in vendor IV/d1/d2.
- **Host**: WSL's disk file lives on C:, which ran full (I/O errors, two crashes). The pull now stops below
  30 GB free on C:. Gears 5 was removed by the owner's order. `fstrim` returns space only if C: is compacted
  (`wsl --manage Ubuntu-24.04 --set-sparse true` after `wsl --shutdown`).

## Next

1. Let the tested tier land, then run the **census**, `freeze-universe`, build the panel (`run book` in
   ADR-0265 Amendment 5) — nothing runs before the owner resolves the open decisions.
2. Owner decisions **D1–D11** (Amendment 5 §d, each with a recommended default): H0 partial-IC primary,
   H1 signs/costs, H2(a) positive by construction, H2(b) selection on the IV crush, unverified earnings
   labels, H3 mid-fill reading, H5 one primary cell, H6 horizon alignment, tercile timing, H7 pass rule,
   H4 weekly-vs-monthly (not built).
3. Declare reuse breaks (AI done; B = Barnes→Barrick pending) and stitch FB→META etc. once those roots land.
4. After the daily tiers: pull `option_quote_at` (15:45 on ~150 roots, 09:45 2021–25) and index prices;
   the gz-copy offload for the other tiers (`driver/offload_observations.py <tier> --apply`).

## Blocked / owner decisions

- D1–D11 above; whether to prune old run outputs (`dskit-cdf-*` ~44 GB, ADR-referenced) for disk.
- The tier-2 pull will hit the disk guard; Standard lapses ~2026-11-11 (renewal is the owner's call).
- Licence: keeping the files after cancelling is unconfirmed (ask the vendor in writing).

## Read first

- ADR-0263 and ADR-0265 (Amendments 1–5) in `docs/architecture/decision-log.md`
- `docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md`
- `children/index_options/index_options/theta_hypotheses.py` (generator; `check`, `freeze-universe`)

## Do not

- No 2026 data (sealed; every document has `end_before`), no fits, no real-data hypothesis run before
  the census, the frozen universe and the owner's D-decisions. Validation runs once, unchanged.
- Never write under `/home/russell/data/thetadata/options-*` while the pull runs; never restart the
  driver while C: is under ~30 GB free. Do not delete raw snapshots or Parquet.
- Run with `PYTHONPATH=<this worktree>`; kill pull processes with anchored `pkill -f '^/bin/bash ...'`
  (an unanchored pattern kills your own shell).

# 2021 Q1 validation MIO execution and expiry stress (ADR-0280 amendment, ADR-0285 item 4)

Fixed-selection replay of the three Q1 top-3 accounts (`w1_003_shock10_lp`, `nominal_lp`,
`w1_003_lp`) under the seven ADR-0280 scenarios. Selections are frozen; each scenario changes only
the fill price, the fill size or the expiry price. Fills are simulated at end-of-day displayed depth,
never broker fills: this is a depth proxy, not fill probability or decision evidence. Nothing here
is a strategy claim; accounts are separate and are never summed.

- Document `children/index_options/configs/mio-2021q1-stress-20261030/q1-execution-stress.json`, SHA256 `84046a6544f552e090ab111538aee999a5c053e10774f3e76c85314c49174838` (schema `mio-execution-stress/v5`).
- Results pins `q1-results-pins.json` SHA256 `d4e7843e40d328572be127ea737e92195570076b1a60525253ef5b0a2423bddd`; source result SHA256 `ce39e2062c378d6576af97f673f795df04b724b4f696729c677a43cdd94399ac`.
- Replay checkout commit `639b1c35f7bcb85fb892aff9632fbde720e5a322`. Evidence root `/home/russell/data/index_options/mio-2021q1-stress-20261030/`.
- The three controls reproduce the saved marked P&L exactly: -$107.50, -$3,827.85, -$207.55.

## Net P&L by scenario (USD; change against control in parentheses)

Scenario parameters (`mio_execution_stress.py`): `adverse_price` fills each leg beyond the touch
by 0.25 of the quoted spread (buys above the ask, sells below the bid); `half_size` halves the
displayed depth available to fill (integer half), not the selected order; `expiry_down_15` /
`expiry_up_15` multiply the pinned bar close on each selected book's expiry day, for its symbol, by 0.85 /
1.15; `joint_*` apply all three.

| Scenario | w1_003_shock10_lp | nominal_lp | w1_003_lp |
|---|---:|---:|---:|
| control | -107.50 | -3,827.85 | -207.55 |
| adverse_price | -309.50 (-202.00) | -3,999.60 (-171.75) | -384.05 (-176.50) |
| half_size | 1,640.10 (+1,747.60) | -2,728.60 (+1,099.25) | 1,271.70 (+1,479.25) |
| expiry_down_15 | -1,799.15 (-1,691.65) | -3,192.25 (+635.60) | -2,423.15 (-2,215.60) |
| expiry_up_15 | 2,271.45 (+2,378.95) | 64,676.35 (+68,504.20) | 2,057.25 (+2,264.80) |
| joint_down_15 | 1,359.60 (+1,467.10) | -2,226.25 (+1,601.60) | 554.10 (+761.65) |
| joint_up_15 | 2,210.20 (+2,317.70) | 50,181.92 (+54,009.77) | 1,908.00 (+2,115.55) |

All 21 cells are `complete`. The filled-book count moves with the scenario (3 to 13 books against 4 to 13 in control for the
same frozen selections), so a change is a change of the whole account, not of one book. The
`nominal_lp` expiry-up cases (+$64,676.35, +$50,181.92) are far from the other methods (about +$2,000);
which positions drive them is in `dispositions.csv` and has not been analyzed here.

## Files

- `report.md`: generated table with max drawdown, selected / filled / refused counts and links to the 21 daybooks.
- `summary.csv`: every scenario x method row with fees, profit factor, worst trade, refusal reasons.
- `dispositions.csv`: each attempted selected book with quotes, fills, reserves, expiry payoff, cash impact.
- `daily.csv`: every account session. `attempts.csv`: every fill attempt.
- `q1_top3/<scenario>/<method>.md`: the 21 daybooks (`q1_top3/control/nominal_lp.md` and so on).
- `artifact-index.json`: SHA256 and size of every file here. The matching JSON twins (about 11 MB) stay in the evidence root and are not mirrored.
- Memo: `../../memos/2026-10-30-mio-2021q1-stress-replay.md`.

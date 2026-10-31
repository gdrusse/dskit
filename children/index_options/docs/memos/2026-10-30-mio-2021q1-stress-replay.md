## TL;DR

The Q1 2021 top-3 MIO accounts were replayed with their selections frozen under seven execution and expiry scenarios. All 21 scenario-by-method cells completed and the three controls reproduce the saved marked P&L exactly. Halving displayed depth turns two of the three accounts positive; a 15% higher expiry price lifts all three, `nominal_lp` to +$64,676.35. The largest caveat: these are depth-proxy fills, so they are neither fill probability nor decision evidence.

## Execution contract

- ADR-0285 item 4 under the ADR-0280 any-range amendment (schema `mio-execution-stress/v5`); owner approved 2026-10-30. Read-only over the attested Q1 result; no MIO re-run, fit, provider pull, registry write or 2024+ read.
- Document `children/index_options/configs/mio-2021q1-stress-20261030/q1-execution-stress.json`, SHA256 `84046a6544f552e090ab111538aee999a5c053e10774f3e76c85314c49174838`; results pins SHA256 `d4e7843e40d328572be127ea737e92195570076b1a60525253ef5b0a2423bddd`; source result SHA256 `ce39e2062c378d6576af97f673f795df04b724b4f696729c677a43cdd94399ac`.
- Replay checkout: a detached git worktree at commit `639b1c35f7bcb85fb892aff9632fbde720e5a322` (`replay-checkout.commit`). Evidence root `/home/russell/data/index_options/mio-2021q1-stress-20261030/stress/`; the table, daybooks and CSVs are mirrored in `children/index_options/docs/reports/mio-execution-stress-2021q1-20261030/` (with `artifact-index.json`).
- Scenarios: `adverse_price` fills 0.25 of the spread beyond the touch; `half_size` halves displayed depth; `expiry_down_15` / `expiry_up_15` scale the expiry-day bar close by 0.85 / 1.15; `joint_*` combine them. Each account is separate and is never summed.

## Results (net profit after fees, USD; account contributions $16,000)

| Scenario | w1_003_shock10_lp | nominal_lp | w1_003_lp |
|---|---:|---:|---:|
| control | -107.50 | -3,827.85 | -207.55 |
| adverse_price | -309.50 | -3,999.60 | -384.05 |
| half_size | 1,640.10 | -2,728.60 | 1,271.70 |
| expiry_down_15 | -1,799.15 | -3,192.25 | -2,423.15 |
| expiry_up_15 | 2,271.45 | 64,676.35 | 2,057.25 |
| joint_down_15 | 1,359.60 | -2,226.25 | 554.10 |
| joint_up_15 | 2,210.20 | 50,181.92 | 1,908.00 |

- **half_size** (control to scenario): `w1_003_shock10_lp` +$1,747.60, `nominal_lp` +$1,099.25, `w1_003_lp` +$1,479.25. Filled books fall from 4 / 13 / 6 to 3 / 12 / 4, so halved depth removes some filled books and the account changes with them; this memo does not attribute the gain to a particular book.
- **joint_up_15**: all three positive (+$2,210.20, +$50,181.92, +$1,908.00). `nominal_lp`'s up cases are far from the other methods (about +$2,000); which positions drive them is in `dispositions.csv` and was not analyzed.
- **adverse_price** costs $172 to $202 per account. **expiry_down_15** costs `w1_003_shock10_lp` $1,691.65 and `w1_003_lp` $2,215.60 but helps `nominal_lp` by $635.60 (its filled-book count is unchanged at 13; the books behind this were not analyzed). Max drawdown ranges -9.3% to -40.7% (`report.md`).
- A count of fills is not evidence of profitability: Q1 is one quarter, 4 to 13 filled books per account, and scenario P&L is conditional on frozen future selections.

## Controls, failures and unrun work

- Controls reproduce the saved marked P&L exactly: -$107.50 / -$3,827.85 / -$207.55 (the Q1 close receipt `ce39e206...`). Every method's control is exact before any stressed case, as the v5 gate requires.
- First launch failed only because the driver needs a git checkout: an archive export of the replay tree is not a repository, so the driver's `git rev-parse HEAD` returned status 128. A detached git worktree of the same commit replaced it, and the replay then ran. `stress.log` also records two later re-launch attempts refused by the fresh-output-root guard; neither overwrote the completed output.
- Not run: any other cohort or period, any broker-liquidity or fill-probability model, any change to the selection or to the optimizer.

## Verification

- Two independent Sonnet lenses, both PASS-with-minors, no Critical or Major; 97 of 97 tests pass in `test_mio_execution_stress.py`: `reviews/v5-code-correctness-authority.md` (SHA256 `6ab81060a4e78848d5cb6497f1b1df9395714ce7dab0d24994a8201023331d47`) and `reviews/v5-code-tests-integration.md` (SHA256 `ce853308874907e37884459ddeef77518e84e397769e60d94405ddca17426078`), under `/home/russell/data/index_options/mio-2021q1-stress-20261030/reviews/`.
- Open Minors: bars are read whole rather than bounded by `end_before` (the reviewer checked the AAPL bars file, which ends 2021-03-31); cross-cohort control ordering is untested with one cohort; LLY and SMCI quote indexes are zero-row files that were not exercised, and neither symbol is in a selected book; a duplicate strict ISO-date validator; a value pinned twice is not cross-checked.
- I verified the mirrored CSV copies against the source (`cmp`) and the scenario meanings against `mio_execution_stress.py`; I did not re-run the replay.

## Reproducibility and handoff

- Reproduce with `python -m index_options.mio_execution_stress <document>` from a git checkout at the commit above, with a fresh `output_root`.
- Next authorized action: none. The owner decides whether this stress changes the Q1 reading in `2026-10-30-mio-2021q1-top3.md`.

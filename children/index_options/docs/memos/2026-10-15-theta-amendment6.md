## TL;DR

Phase A is in progress. No real-data hypothesis, census, panel, validation or training run has occurred in this session.

## Execution contract

Owner: Russell. Writer: GPT-6 Astra (gpt-6-astra), confirmed by owner; skeptics Terra/Luna.
Base: bd7c6612d356b0980e53dea24d5353b33af13eb5. Worktree: /home/russell/wt/theta-amendment6-20261015.
Approved contract: ADR-0263 and ADR-0265 Amendments 1-6; Amendment 6 predates code and is not edited.
Scope: generator, generated configs, focused tests, AAPL label provenance, this memo and handoff.
Acceptance: exact D3/D4/D7/D9/D10/D11 implementation, all documents check/validate/plan, two focused test files, two fresh independent final lenses, rebase and push main.
No fits, pulls, driver mutation, port 25503, writes below thetadata/options-*, deletions below data, or 2026 data reads.
Disk check: /mnt/c 104 GB available before work (minimum 30 GB).

## Inventory and preflight

Sweep completed across 74 refs and 72 other worktrees: theta_hypotheses event_gap capital_at_risk fee_sensitivity census_asof theta-amendment6.
Existing owners: generator; OptionStructureLedger fee_per_contract, max_loss_usd and max_loss_unbounded; ComputeFields; StampSeriesStats. No new package or generic mechanism is needed.
Initial sweep hit its 8-second budget; rerun with a temporary 60-second config completed (exit 0); full output /tmp/theta-amendment6-sweep.txt.
The panel rate source currently parses 2026 macro rows before filtering. No run may use that path under this session's strict seal; a verified bounded existing source must be found before Phase B.

## AAPL labels (D5)

All 20 existing AAPL announcements, 2016-2020, match the dated Apple earnings releases. Zero dropped; confidence and date values unchanged. Per-row source_url and verification date retain the evidence. Other names remain exploratory/unverified.

## Checks and independent review

Synthetic tests only. No empirical pass/fail exists yet.

- RED: `python -m pytest tests/test_theta_hypotheses.py -k amendment6 -q`: 5 expected failures, 1 pass on the unchanged generator (H2 old primary; H5 ten hurdle-bearing cells; panel/census not generated under D9).
- First requested two-file run: 775 passed, 2 failed (verification date in data-bound test; new memo absent from exact manifest). Both corrected.
- Added fee/risk behavior checks initially accessed persisted output references as rows; corrected to read the synthetic audit files. No runtime rule changed in that correction.
- `python -m index_options.theta_hypotheses check`: passed. All 23 `run-theta-*.json` documents: validate + plan, 46 checks passed, zero data execution. Logs: `/tmp/theta-amendment6-validation.json`.
- Targeted ruff: unchanged base 16 findings; current 14, all pre-existing (docstrings plus lambda/variable naming). Two edited docstrings corrected; no ignore or check disabled. Logs: `/tmp/theta-amendment6-ruff{,-base}.txt`.
- Requested two-file rerun pending at candidate freeze; `/tmp/theta-amendment6-final-tests.txt`.

### Design review and fixed contract

Inventory context `/root/design_skeptic` supplied no approval. Independent GPT-5.6 Terra (gpt-5.6-terra), `/root/terra_design`, reviewed before candidate freeze: 0 Critical, 6 Major, 1 Minor on the original generator; all mapped to this scope.

1. D3: existing event_gap expression promoted unchanged; by-event-source mean/t and original half groups occur before ledger-completion selection. Thirty-day gap descriptive. Weekly buckets and h_steps 1 unchanged.
2. D4/D5: a separate labelled-only primary-population stream feeds H2(b)'s measurement. Only high-confidence AAPL labels enter; all verified. The detected-only validation cannot close H2(b).
3. D7: sole hurdle-bearing stream is zero-fee SPX put_write, notional return. Other cells and .65 USD fees have no hurdle. Same orders, fills, bootstrap and original .5 hurdle.
4. Capital risk: ledger maximum loss, fees included; finite positive maximum loss only, unbounded trades remain in the complete audit and are excluded with filter counts from ROC. Short puts are finite (strike x 100 minus credit); short-call strangles are unbounded. Fee applies to each contract/leg on entry or quoted exit; intrinsic settlement fee stays zero.
5. D9: Terra explicitly confirmed filtering only tercile-dependent streams from 2019 is contract-consistent and needs no owner clarification. Overall/half statistics keep 2016-2020 and 2018-07-01; validation keeps 2021-2025 and 2023-07-01. Census liquidity and panel asof both 2019-01-01. H7 per-name spread table does not use terciles and keeps the full window.
6. Historical verbatim ADR text remains visibly historical; Amendment 6 controls current notes. H7 descriptive; H4 weekly comparison dropped, monthly behavior unchanged.

Review matrix: (a) unchanged statistics/signs/thresholds/half splits outside D3/D7; (b) H2 source selection and pre-ledger half coverage; (c) exact H5 fee deductions, finite/unbounded risk, only one primary hurdle; (d) 2018/2019 tercile boundary and 2016-2018 census; (e) label provenance/hash pins; (f) generated twins/check/plan/synthetic integration; (g) no real-data runs or driver changes. Rate-source safety is an explicit Phase B blocker, not executable approval.

## Reproducibility and handoff

Use /home/russell/dskit/.venv/bin/python with PYTHONPATH pointing at this worktree; run from children/index_options. Phase B remains gated on completed tested-tier export, safe bounded inputs and committed frozen universe/pins.

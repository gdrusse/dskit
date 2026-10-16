## TL;DR

Phase A is release-approved after the sealed-rate correction; both fresh final reviews are clean. Rebase/push verification follows below. No real-data hypothesis, census, panel, validation or training run has occurred in this session.

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
The original panel rate source parsed 2026 macro rows before filtering (now replaced by an executable gate; see correction below). No run may use that path under this session's strict seal; a verified bounded existing source must be found before Phase B.

## AAPL labels (D5)

All 20 existing AAPL announcements, 2016-2020, match the dated Apple earnings releases. Zero dropped; confidence and date values unchanged. Per-row source_url and verification date retain the evidence. Other names remain exploratory/unverified.

## Checks and independent review

Synthetic tests only. No empirical pass/fail exists yet.

- RED: `python -m pytest tests/test_theta_hypotheses.py -k amendment6 -q`: 5 expected failures, 1 pass on the unchanged generator (H2 old primary; H5 ten hurdle-bearing cells; panel/census not generated under D9).
- First requested two-file run: 775 passed, 2 failed (verification date in data-bound test; new memo absent from exact manifest). Both corrected.
- Added fee/risk behavior checks initially accessed persisted output references as rows; corrected to read the synthetic audit files. No runtime rule changed in that correction.
- `python -m index_options.theta_hypotheses check`: passed. All 23 `run-theta-*.json` documents: validate + plan, 46 checks passed, zero data execution. Logs: `/tmp/theta-amendment6-validation.json`.
- Targeted ruff: unchanged base 16 findings; current 14, all pre-existing (docstrings plus lambda/variable naming). Two edited docstrings corrected; no ignore or check disabled. Logs: `/tmp/theta-amendment6-ruff{,-base}.txt`.
- Requested two-file rerun: 776 passed, sole failure numeric memo count548->549. Correction7c364d76; the exact manifest test then passed (1 passed0.63s). All777 requested cases pass across these runs; no further full rerun needed for the count-only correction. Final generator check and all46 validate/plan checks pass. `/tmp/theta-amendment6-final-tests.txt`.

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

Read-only readiness check: cursor updated 2026-10-15T23:41:32+00:00 (19:41 ET), 76 roots present,75 reached oldest<=2016-01-04 of414 required. CHTR oldest2023-07-06; BRUN/CBRS no_chain. No ALERT.txt. Latest logged export alarms=[], verify OK; historical cumulative alarms3 (date_coverage2, rows_per_date_jump1) remain recorded, not erased. Raw-only warnings list113 snapshots (known observation-copy offload); no assumption that warnings prove full tier complete. C:103GB free. No Phase B run started; H-census and H0-H7 are NOT RUN, not failed. No new run ledger row is appropriate for non-execution.


## Retained final-lens round 1 (superseded, not release approval)

GPT-5.6 Terra `/root/terra_final_correctness`, candidate7c364d76 against basebd7c6612:

> Finding - Major, unresolved (1): Phase B is not technically blocked. The two panel configs only state that Phase B is blocked. Yet rates_raw still uses unbounded ObservationRows; its keep_values() is None, so it calls scan_stream and materializes the stream before downstream rates drops2026. A real panel run can therefore parse sealed2026 rates. Consequence: the strict no-read seal depends on prose. Add an executable refusal/bounded pre-2026 source and regression test before any real run. This review approves no real runs.
>
> All other assigned invariants passed inspection: D3 source/week/halves; D4 labelled population; D7 one primary and descriptive fee/ROC; D9 cutoff/original halves; D10/D11; historical ADR clearly marked; label file digest matches. Check and diff check passed; Amendment6 tests10 passed, manifest1 passed. Critical0; Major1 unresolved; Minor0; Nit0.

GPT-6 Luna `/root/luna_final_tests`, same candidate7c364d76:

> Independent tests/integration lens on candidate7c364d76 against basebd7c6612: no proven findings;0 unresolved Critical/Major. The D3 event-gap source/week/half wiring, D4 labelled-only H2(b) population, D7 sole zero-fee SPX put-write hurdle and descriptive fee/capital streams, D9 cutoff with original halves, and D10/D11 behavior match the stated contract. H5 tests exercise synthetic ledger output. I also checked all20 AAPL source pages against their recorded dates. Verification: two scoped test files777 passed with278 deprecation warnings. Generator check and all23 validate+23 plan passed. No empirical data was run; D3/D4/D9 conclusions remain synthetic or config-level.

### Batched correction

Terra's Major is accepted; Luna's clean lens is superseded for the changed candidate. No severity downgrade. The ADR implementation-safeguard paragraph was written before code; it changes no statistical contract. Both panel rates_raw nodes now use the existing ParquetStreamRows with end_before2026-01-01, source theta-rates-pre2026 and an explicit unresolved zero pin. The old unbounded observation source is absent from those graphs. `set-pin rates` records the future vetted input. No source was acquired or data read. The safe artifact remains a Phase B prerequisite.

RED on unchanged reader: both panel tests failed because ObservationRows was still configured (2 failed). GREEN: 20 targeted checks passed (rate refusal, pin update, node validation); rate-refusal tests use an empty temporary store and spies that fail on any legacy scan or Arrow dataset scan. An unresolved pin refuses before either scanner is invoked. Corrected candidate69cb3c6adeff782a7549144716dd132344158e2d: full requested two-file run779 passed,278 deprecation warnings,89.63s. Generator check and all23 validate+23 plan checks passed. Logs: /tmp/theta-amendment6-safe-tests.txt and /tmp/theta-amendment6-safe-validation.json. Current scoped lint13 pre-existing findings versus16 on base; no new findings or disabled checks (/tmp/theta-amendment6-ruff-final.txt).


## Retained final-lens round 2

GPT-5.6 Terra (gpt-5.6-terra), fresh correctness/authority lens `/root/terra_corrected_final`, exact candidate69cb3c6adeff782a7549144716dd132344158e2d vs basebd7c6612d356b0980e53dea24d5353b33af13eb5:

> CLEAN. Proven findings: Critical0, Major0, Minor0, Nit0; unresolved C/M0. D3 event_gap, week, h=1, original halves pre-ledger; D4 high/AAPL labelled-only H2b primary measurement and detected validation cannot close; D7 sole zero-fee SPX put_write notional .5 hurdle, .65/finite-positive ROC descriptive; D9 2016-18+2019 cutoff retains2018-07-01 halves; D10/D11, historical ADR marking, generated twins, pins/provenance all pass. Rates correction: both panels use dedicated theta-rates-pre2026 zero pin + ParquetStreamRows end_before2026; unresolved pin fails before legacy or Arrow scan (2 panels). Evidence: retained779 passed/278 warnings;23 validate+23 plan all exit0; my generator check passed; synthetic subset12 passed. Limits: synthetic/config review only; no real data, no rate artifact, Phase B remains blocked (75/414). Candidate code equals HEAD; only allowed unstaged re-entry/archive docs exist.


GPT-6 Luna (gpt-6-luna), fresh tests/integration lens `/root/luna_corrected_final`, same exact candidate and base:

> Clean;0 Critical,0 Major,0 Minor,0 Nit;0 unresolved C/M. I independently reran the two focused test files:779 passed, with278 deprecation warnings, and the generator check passed. The retained memo records all23 validate and23 plan checks passing. The D3/D4/D7/D9/D10/D11 wiring and sealed-rate refusal align with the contract; no real data was read. Phase B remains blocked pending the safe rate artifact and tested-tier completion.

## Candidate lock and delivery

Locked candidate69cb3c6adeff782a7549144716dd132344158e2d against basebd7c6612d356b0980e53dea24d5353b33af13eb5. Contract: immutable ADR-0265 Amendment6 plus implementation-only strict-rate safeguard. Matrix: the seven families above plus pre-scan rate refusal. Dependencies: unchanged dskit libraries at the base; no external rate artifact/panel pin is accepted by this lock. Both fresh independent lenses above have zero unresolved findings. Pre-existing scoped lint debt13 and278 deprecation warnings are disclosed; no fixes after lock. No empirical run approval is implied.

Reviewed Git object identities (config directory tree includes every generated document and pin):

```
children/index_options/index_options/theta_hypotheses.py: 65e72f481be42ef6d75797b7602c47dfa2e4b76a
children/index_options/tests/test_theta_hypotheses.py: 02f1747627a92091abeec7408456ae931e1a2346
children/index_options/tests/test_configs.py: e9d706b593cf2869c908a56c75a4eb9279041771
children/index_options/configs: 192fae2155e2ef8a83b360246a8481a64445dfd9
docs/architecture/decision-log.md: 08c130c01e0bb676c8bf2b1188f9cc7d0b20aeed
```

Only memo/re-entry/archive evidence changes follow this candidate. Documentation budget checks4 passed0.10s; git diff --check passed. C:103GB free. Final fetch/rebase must preserve the reviewed base dependencies; push must be verified against the remote. The eventual landing commit is recorded below after verification.

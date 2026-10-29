# RE-ENTRY - index_options

Updated: 2026-10-29 by GPT-6 Astra. Ordinary MIO sizing wiring reviewed and integrated; fresh 2019 reruns authorized.

## Now

- ADR-0281/0282 Amendment 1: domain.params.fill_sizing=available_size enables single-leg depth clipping and atomic whole-number multi-leg ratio scaling in ordinary MIO and stress v4. Missing knob retains legacy. Quote, cap, exit and expiry policies remain fixed.
- Locked candidate: 922b53d00dc0a49797f350fa0f300343a3ddf717, based on 05b6ff23. Runtime wiring 3b5164bc and test-only corrections ed55dc1f/922b53d0 by Claude Opus 5.5; GPT-5.6 Sol governed actual remote-control sessions. Original single-leg build 3948a845 was by GPT-6 Sol; Fable 5.1 supplied the multi-leg approach.
- Four fresh final reviews on 922b53d0 report C0/M0: two Claude Sonnet 5.5 lenses plus GPT-6 Luna wiring_final_luna_correctness_r3 and wiring_final_luna_integrity_r3. Actual Sonnet outputs are retained at the links below; Luna outputs are retained in the task.
- Independent focused validation: 334 passed in 21.30s using /home/russell/dskit/.venv/bin/python. Each fresh final lens also ran 91 focused tests. A demonstrated selected-credit cash mutant is now rejected by independent hand-calculated fill/expiry cash, fees and reserve oracles. No full-suite claim.
- The earlier v3 diagnostic did not wire sizing into fresh MIO or joint stress. This amendment closes that gap; the v3 result remains immutable and is not this new rerun.
- New JSONs: configs/mio-2019-{limited36,broad212}-available-size-20261029.json. All six study copies are identical; normalized JSONs exactly match originals except the approved sizing knob, refreshed 266 imported-source pins, name, notes and output root. Dependency versions and data/model parameters remain unchanged.
- Both metadata plans are runnable with no missing inputs; shared registry last observed rev 47, inactive. Code and JSONs are published at aa46736146845f9b5cd9ea493099da0b29366fac. Limited36 started at 2026-10-29 00:02 EDT and is processing decisions under dskit-mio-2019-available-size-limited36-20261029.service; broad212 and stress follow serially.
- Durable execution state: /home/russell/data/index_options/mio-2019-available-size-20261029/state.json. It records current jobs, reviews, config hashes, exact receipts and next stages. Native thread follow-up finish-2019-mio-sizing-rerun checks every 30 minutes.
- Preserve original results, published v1 report, v3 diagnostic, private viewer, Path A0003/A0008 and TDD. This remains 2019 development evidence with original lookback/tail-padding metadata bounds and saved forecasts.

## Next

- Verify the running limited36 packet, then run broad212 serially through the existing pipeline and shared registry. Verify all four methods, recorded-decision replay and complete attestation before each claim disposition.
- Export the new saved trades, refusals, selected/executed legs and daily accounts. Pin source results/configs/quote indexes/CSVs in a distinct results-pins document.
- Run v4's seven existing scenarios against fresh saved selections. All eight zero-shock controls must exactly reproduce the new MIO results before the 48 stressed cases. Verify 56 total cases and preserve unsupported outcomes visibly.
- Publish a distinct readable report/daybooks/CSV mirror after verification. Do not overwrite earlier reports. Follow state.json rather than launching duplicate jobs.

## Blocked / owner decisions

- No unresolved in-scope Critical/Major. Owner authorized the reruns and autonomous follow-through; no further approval is pending.
- Deferred Minor coverage: legacy v1-v3 invalid fill_sizing typo handling; explicit 56-row execute assertion; further shocked multi-leg numeric stress_extra/reserve-floor and summary-output assertions. Required real control/output verification remains in the run gate.

## Read first

- ../../../docs/architecture/decision-log.md - ADR-0281/0282 Amendment 1.
- ../configs/mio-2019-limited36-available-size-20261029.json and ../configs/mio-2019-broad212-available-size-20261029.json.
- https://claude.ai/code/session_01YFrQurSeJW9SRjF5Y9auyx - Opus implementation and test corrections.
- https://claude.ai/code/session_01YFrQut91TKthQtucn9V6oT - final Sonnet correctness, C0/M0/m1/n1.
- https://claude.ai/code/session_01YFrQuvLQsA3PqPWgjEta8H - final Sonnet integration, C0/M0/m3/n2.

## Do not

- Expand quote screens, caps, early exits, strategies, scenarios or years; refit models or pull providers.
- Change Path/TDD or remove original evidence, data, viewer, checkouts or transcripts.
- Present simulated displayed-depth fills as broker liquidity or proven profitability.

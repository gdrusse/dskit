# 2026-10-26 — MIO speed candidate and the beta-shock control, tested on 2020 with fitted M51

## TL;DR

The weekly option optimizer now certifies every week in seconds instead of timing out, because zero-reserve parity books are screened out and sold legs carry a capital charge. On the 2020 validation year with the fitted M51 forecast, adding the new beta-shock rows turned a **−$8,908** year into **+$4,197** on $36,000 contributed, while buying the same 17 stocks equal-weight would have made **+$7,354**. The largest caveat: the gain comes from a few Boeing books, and in 2020 the shock rows mostly acted as a 10%-of-funding loss budget rather than a correlation hedge.

## Execution contract

- Worktree branch `claude/index-options-mio-fable-20261026`, candidate `bf39ae97` (commits `d1d5af96`, `d0011600`, `93dbb4ea`, `f229edb7`, `536140b3`, `bf39ae97`), ADR-0278 amendment of 2026-10-26 ("build now") in `docs/architecture/decision-log.md`.
- Study: `WeeklyOptionPortfolioStudy` as in the v11 run (17 pre-2019 names, $10,000 initial, $1,000 every 14 days, weekly fraction 0.25, decision DTE 0–60, $50 absolute gap, 900-second cap, timeout policy return, one thread), with `group_screen` "lp" and `short_notional_fraction` 0.2 on both methods; the second method adds `shock` {moves ±5%, ±10%; loss_fraction 0.1; beta_lookback 60}. Resolved parameters: `data/2026-10-26-shock-rows-2020/params-2020.json`.
- Data: A0003 validation year 2020-01-02 to 2020-12-31 (fold 2, band val), never the 2024–25 holdout. Forecasts `scores/edge-method-51-qa-mlp64-by-dispersion/forecasts.parquet` (M51: fitted rows where its dispersion gate fitted, 21,959 of 28,960 rows across the 17 names, its wide-MLP64 fallback elsewhere, `require_fitted` false); contexts `strike-region-scores-wf-wide-mlp64/rows.parquet`; Theta archive `options_2020.parquet`; daily closes `prices/index_daily`. Study identity (params plus provenance counts) `1d0146f2404a70f82a2a828203679ab7f1d910ff98b4c8762c2d424df246d35b`.
- Command: `run2020.py` (copied to the data directory) under `/home/russell/dskit/.venv/bin/python -B`, `OMP_NUM_THREADS=1`, PYTHONPATH worktree plus the child, 3,427 s wall for both methods and their in-process replays; log `run2020.log`.
- No canvas, v11 artifact, config directory or Path row was edited. The 2020 run document exists only as the resolved params JSON in the memo data directory.

## Implementation evidence

What changed, all configuration-driven and default-off:

1. **LP group screen** (`TransportPortfolioSolve.group_screen`): each candidate book's own LP relaxation bounds anything an integer book in that group can earn; groups within `parity_tolerance` of zero are dropped, keep-one fallback, replay re-derives the screen.
2. **Capital charges** (`reserve_per_contract`, `short_notional_fraction`): reserve floors in solver rows, frozen books, fills and the ledger.
3. **Joint rows** (`joint_rows` input): one linear bound across groups, audited in the incumbent, the round-trip cleanup proof and recorded replay.
4. **Shock policy** (child method key `shock`): for each common move m, every name's terminal price is spot × (1 + beta × m), beta the trailing 60-session slope on the equal-weight universe basket (1.0 until 20 returns exist); the selection's terminal loss net of premium at that move may not exceed loss_fraction × funding. Each decision records betas, limit and realized losses.

Benchmark on the eight v11 weeks that were rebuilt from the frozen intake (2019, same $50 gap): v11 solve times 50–940 s with five timeouts; with the screen alone 2–487 s; with screen plus short-notional 0.2, 0.4–10.8 s on every week, objectives from $2.7k–36k down to $0–5.2k (the parity books' modeled credit was the timeout cause; 90% of those books were unfillable).

Reviews: two independent Sonnet lenses per slice. Group screen and charges: one Major (vacuous positive-radius fixture) fixed in `93dbb4ea`. Joint rows and shock: three Majors fixed. The round-trip cleanup ignored joint rows, so a pair that loosened a row was cleaned into an audited violation; now the proof reads every joint row (`536140b3`). The screen with joint rows is a candidate restriction, not an exact reduction: a group without standalone value is excluded even where a negative coefficient would let it hedge another group; documented in the docstring, README and ADR, pinned by test. Betas were never exercised inside the rows; a hand-priced unit test and an end-to-end two-name run now recompute every recorded loss leg by leg (`bf39ae97`). Re-reviews PASS; carried Minors: lookback, `max(0, funding)`, min-pairs threshold and timeout-None are unbound by tests; no upper bound on moves; the restricted certificate reads "optimal" without marking the restriction.

Tests: 627 passed in the focused files (`tests/pipeline_libs/test_pyomo.py`, `children/index_options/tests/test_ledger_studies.py`, `tests/docs`). Not run: the full suite. Known unrelated failure: `test_the_module_runs_as_a_command_and_reports_exit_codes` (the venv's installed dskit lacks `option_structures`). Pre-existing lint E702 at `test_ledger_studies.py:1878`.

## Empirical results, 2020

Both methods replayed exactly from their saved records in process (`REPLAY ... True`); the saved JSON also replayed exactly under the candidate head `bf39ae97` (no solve, full audit, 626 s and 649 s).

| | no shock | shock rows |
|---|---|---|
| Contributions | $36,000 | $36,000 |
| Net P&L | −$8,908.05 | +$4,197.20 |
| Stress P&L (extra spread) | −$9,698.80 | +$3,212.70 |
| Final equity | $27,091.95 | $40,197.20 |
| Time-weighted return | −29.61% | +12.14% |
| Max marked drawdown | −75.52% | −37.62% |
| Weeks selected / cash | 31 / 22 | 32 / 21 |
| Books selected / filled / unfilled | 35 / 15 / 20 | 38 / 18 / 20 |
| Contracts, fees | 77, $50.05 | 92, $59.80 |
| Timeouts | 0 | 0 |
| Solve seconds total / max | 221 / 19.8 | 645 / 119 |
| Mean candidate books per week | 113 | 113 |

Unfilled reasons were the same in both: 17 missing next-session quotes, 1–2 insufficient size, 1–2 repriced over a shared cap.

Per name, filled books:

| no shock | books | contracts | net P&L | stress P&L |
|---|---:|---:|---:|---:|
| ABBV | 5 | 17 | 34.95 | −120.30 |
| BA | 7 | 31 | −2,376.15 | −2,891.65 |
| GILD | 1 | 7 | 9.45 | −43.30 |
| GM | 2 | 22 | −6,576.30 | −6,643.55 |

| shock rows | books | contracts | net P&L | stress P&L |
|---|---:|---:|---:|---:|
| ABBV | 5 | 25 | 76.75 | −134.25 |
| BA | 7 | 27 | 4,526.45 | 4,090.20 |
| CAT | 1 | 5 | 13.75 | −21.50 |
| GILD | 4 | 33 | 111.55 | −164.20 |
| GM | 1 | 2 | −531.30 | −557.55 |

The worst no-shock books were BA 2020-06-22 (−$8,157, 3 contracts), BA 2020-06-29 (−$6,309), GM 2020-09-21 (−$6,045, 20 contracts); the best was BA 2020-04-27 (+$13,174, 7 contracts). Under the shock rows the same dates appear at a fraction of the size: BA 2020-06-22 −$2,338, BA 2020-04-27 +$4,351, and two further BA wins of about +$2,600 each. BA held 61% of selected reserve without the rows and 45% with them.

How the rows acted: in 9 of the 32 selected weeks the maximum shocked loss sat within 2% of the limit, and in 14 weeks the loss was the same at every move, meaning the selected books were debit structures whose loss is the premium under any move. In 2020 the control therefore worked mostly as a per-week loss budget of 10% of funding, not as a cross-name hedge; the screen excludes negative-value hedge groups by design (see the restriction above).

Market comparison, same bars, equal-weight 17 names rebalanced daily:

| 2020 | TWR | max drawdown | net on the study's deposit schedule |
|---|---|---|---|
| Buy and hold, equal weight | +16.6% | −36.6% (low 2020-03-23) | +$7,354 |
| Study, shock rows | +12.1% | −37.6% | +$4,197 |
| Study, no shock | −29.6% | −75.5% | −$8,908 |

BA, the name the optimizer traded most, was the universe's worst stock (−36%); AMZN led (+72%) and was traded once.

## Math

Shock row for move m and name s with beta b_s, spot S_s and multiplier M = 100: for each leg j with sign σ_j (+1 bought, −1 sold), strike K_j and credit c_j (premium received net of fee, negative for a debit),
loss_j(m) = −σ_j · M · payoff_j(S_s · (1 + b_s · m)) − c_j, and the row is Σ_j loss_j(m) · q_j ≤ f · max(0, funding).
Week 2020-04-13: f = 0.1, funding $16,921 → limit $1,692; recorded losses 1,692 at all four moves, so the row bound and the selection was a debit book at the budget. Buy-and-hold net on the deposit schedule: units bought at each deposit's NAV, 36,000 contributed, final 43,354, net 7,354.

## Limits

- One validation year, 18 filled books; year totals ride on seven BA books. Not promotion evidence under A0005.
- The LP screen with joint rows restricts candidates to standalone-value books; a hedge group is dropped. An exact screen would keep every candidate (all have long legs) and return the 900-second timeouts.
- Betas are 1.0 for the first 20 sessions of the year (no pre-window bars in the intake).
- American assignment, broker margin and intraday fills remain unmodeled; the short-notional 0.2 charge is a stand-in for margin, not a broker rule.
- No 2019 rerun with the new MIO yet; the 2019 v11 artifact is unchanged.

## Reproducibility and handoff

- Evidence: `children/index_options/docs/memos/data/2026-10-26-shock-rows-2020/` (result JSON, resolved params, run and analysis scripts, run log).
- Replay: `WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=saved_method)` with the saved params and the same intake; `replay2020.py` in the session scratchpad did this under `bf39ae97`.
- Owner decisions: merge the candidate; where the 2020 run document should live in `configs/`; whether to let hedge groups past the screen; a 2019 rerun with screen, charge and shock rows; loss_fraction and move calibration.

## Publication integration — 2026-10-26

Owner request: “Can you push all”. Integration base: published main
64f9575593bf65fab1cd40bf64894419de696868; incoming Fable head
1fd8d7aceeaf789e76653c6b11970b513e68b63b (reviewed runtime bf39ae97).
Working checkout: /home/russell/wt/index-options-fable-publish-20261026.

The bounded task preserves both implementations and the saved study evidence,
resolves the handoff documentation, verifies the affected interfaces, and
publishes the combined main plus Fable's branch. Merge edits are limited to
root/lane re-entry, their archive, this evidence addendum and the exact file
manifest correction below. Runtime, dependency, configuration, saved-result
and other test files must equal their owning parent blob. The combined READMEs must retain both parents' additions.

Acceptance matrix: preserve the optional MIO APIs and historical defaults;
retain the scenario repricer and its numeric refusals; import and exercise
both under the same checkout; keep original records and Path unchanged;
reconcile the handoff without widening execution authorization; push by
fast-forward and verify both remote refs. No new backtest, holdout access,
full suite, trading rule or other-worktree edit is authorized by this task.
Final integration lenses and focused checks are pending.


The first combined focused run returned 1,447 passed, 13 skipped and one
failure in test_exact_manifest_and_agent_parity (53.62 seconds). The same
isolated test failed on unchanged main 64f95755 (two old memos omitted) and
Fable 1fd8d7ac (those plus the new memo and five evidence files). The correction
adds those eight explicit approved paths to the existing expected set. The
strict equality assertion and ignored directories remain unchanged; the
companion count was independently stale (763 versus 765 entries already in
the expected set) and now pins the complete 773-file inventory. Other test
code is unchanged. No production code or study result changed.


### Final integration closure

Frozen candidate: 254f4ae435ed8a1aec871b0e43bdf53eeaea62a9, including merge
2fb22537640b543906cc11eb1398c3ca30469770 and the manifest correction.
Both fresh independent GPT-6 reviews completed on this candidate:
`/root/fable_publish_correctness` (correctness/authority) and
`/root/fable_publish_integration` (test-quality/integration). Their retained
outputs are in Codex task 01a1655d-cf03-7e33-bbd0-fdaed4505ea4.
Each reports PASS, with zero new unresolved Critical/Major/Minor/Nit findings.
The seven inherited Minor items remain deferred: four Fable coverage gaps,
shock-move bound, restricted-certificate label and tiny scenario-shock rounding.

Correctness lens independently verified parent blob preservation, both README
contracts, original memo prefix, Path, archive histories and unchanged existing
pricing definitions. Same-checkout in-memory probes covered scenario loss and
expiry/wire refusals, absent charges/shocks, default LP-screen policy, valid
joint rows and unknown-group rejection. No optimization or market reads.

Integration lens independently verified 3,268 nonconflicting parent paths,
both README additions and both archived handoffs. Its 21 targeted tests passed
together in 2.79 seconds: imports, scenario document, exact inventory, charges
and defaults, beta-shock rows, LP screen, joint-row integer oracle/cleanup,
scenario pricing and numeric refusals. Six in-memory inventory probes
accepted the exact inventory and ignored directories, and rejected an extra
file, missing evidence, equal-count substitution and agent-parity mismatch.
Two initial probe-harness attempts could not handle dynamic set expressions;
the corrected harness passed. Candidate and worktree stayed unchanged.

Parent checks: the original combined focused run passed 1,447 tests and
skipped 13, with the inventory failure described above. After correction,
the exact manifest plus docs checks passed all five. This verifies 1,448
distinct focused tests across the two runs; the full batch was not repeated
for a list/count-only correction. No full suite or historical replay was run.
Interpreter /home/russell/dskit/.venv/bin/python -B; WSL Ubuntu-24.04;
PYTHONPATH this checkout plus children/index_options; numerical threads 1.
Dependencies: Pyomo 6.10.1, HiGHS 1.15.1, SciPy 1.18.1, NumPy 2.5.2,
pytest 9.1.1.

Retained SHA256 identities: pyomo.py
cb414c1251d7089d241c23cea8362308208b033476edd6dda93a145976213ec8;
ledger_studies.py
38f4fc629c82da1a1a9f00070d91f535a4c342389ec79586c090791fb1e3695f;
option_pricing.py
775e3455012be4bb513ab37be7b618a3bb3f4c6b80ab2af7bbdf4ccfbdce63be;
pyproject.toml
c217adef026f3b611663810400c600bd1bb835c38aa5f4f78a01381ccfaa47a0.
The publication closeout changes only this evidence addendum and root/lane
handoff text; code, tests, contracts, configs, dependencies and saved evidence
are compared against the reviewed candidate before the final commit.

The owner-authorized targets are main with both parent histories and
claude/index-options-mio-fable-20261026 at its original 1fd8d7ac head.
The Fable branch and its working checkout are retained for continuation.
Publishing uses a normal atomic push, followed by remote-ref verification;
no force update, protected check bypass or other branch publication.

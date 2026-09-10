# Decisioning

CSV is the store (`actions.csv`, `path.csv`). This README is **generated**
— do not edit it. Append a CSV row or run `python -m dskit.journal promote`.

## Process

Many things get tried. The Actions table is the full tape. Path to
Production is the owner-selected linear chain (a subset of those IDs).

```
acquire  →  research  →  execute  →  production
 pull         finding      fit          live
```

1. **Acquire** — `python -m dskit.onboarding` `register-source` /
   `acquire --mode backfill|live` / `validate` / `certify` / `publish`.
   `watch` is one row per process, not per pull. **Automatic.**
2. **Research** — only
   `python -m dskit.journal research "TITLE" --topic T --name N --body-file <draft>`.
   Writes `docs/research/<topic>/<YYYY-MM-DD>-<name>.md` and the row
   together. Default name is `synthesis`. No markdown in the research
   root. Never write that folder by hand. Skills: `record-research`
   and `deep-research` (Cursor, Claude, OpenCode).
3. **Execute** — `python -m dskit.pipeline run|walkforward`.
   **Automatic** after RECORD. Walk-forward is one row, not per fold.
4. **Production** — wrap `live.main` in
   `dskit.journal.hooks.production`. One row per process, not per tick.

The ledger is CSV, not a database. **Database Location** is a pointer
to that action's artifacts (onboarding root, run dir, research file).
MLflow / the asset store hold their own records when used.

**Path to Production** is human-owner-only: only the owner may add or edit a
row, including **Current Work**. Agents and hooks never write it. Every row
has a short label, purpose, relevant evidence files (pipeline run, research
markdown, or other material evidence), and **LOCKED** (`Y` / `N`). Pytest
does not record. A child without `journal.json` refuses acquire / run / live.

## Actions (latest 10)

Display only: `actions.csv` remains the complete, append-only journal.

| ID | Category | Step | Execution Date | Relevant Inputs | Relevant Outputs | Database Location | Notes |
|---|---|---|---|---|---|---|---|
| A18861 | research | Gate 5a: replay conformance/hook discovery | 2026-09-09T23:05:48+00:00 |  | tests/production/test_loop.py,tests/production/test_executor.py,tests/production/test_feed.py,tests/production/test_clock.py,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle2.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle3.md |  | PASS-EXISTING on items 1-5 and crash/restart positions/cash/NAV. Stopped after two correction cycles: pending-intent Recovery unknown order_event advances economic_seq (pinned) and moves pending→working (unpinned). No production hook. Reviewer 2 not run. path.csv untouched; replay.py not written. |
| A18862 | research | Gate 5a closed: replay conformance/hook discovery | 2026-09-10T00:10:37+00:00 |  | tests/production/test_loop.py,tests/production/test_executor.py,tests/production/test_feed.py,tests/production/test_clock.py,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle4.md,docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-architecture.md |  | PASS-EXISTING on all six Gate 5a items. No production hook. Cycle-4 conformance PASS (0C 0M 1N); architecture/governance PASS (0C 0M 1N). Pending-intent Recovery unknown order_event: economic_seq +N, n_working +N, n_pending -N pinned. path.csv untouched; replay.py not written. No §11 item inferred. |
| A18863 | execute | Gate 2 deliverable 3 method correction cycle 5 | 2026-09-10T03:29:57+00:00 | skeptic cycle 6 report; NoInformationScan estimator resolution contract | TDD regression and fail-closed resolved-estimator guard |  | GPT-5.6 Sol: universe scan estimators remain valid; evidence mode rejects empty, non-string, and non-importable effective estimators before fitting |
| A18864 | execute | Gate 2 deliverable 3 review-closed | 2026-09-10T03:44:23+00:00 | method/API PASS 7c0ba3d; architecture/governance PASS d61b86a | docs/memos/2026-09-10-final-model-replay-gate2-deliverable3-closeout.md; docs/RE-ENTRY.md |  | GPT-5.6 Sol closeout: 0C/0M/1 accepted minor method; 0C/0M/0m architecture. Atomic content-addressed HPO evidence, strict resolver, and fail-closed estimator/data checks verified. No full suite, real HPO, market-data run, run-final-refit.json, push, or path.csv edit. Next: Gate 2 deliverable 4 run-final-refit.json. |
| A18865 | execute | Gate 2 deliverable 4 pending final-refit contract | 2026-09-10T04:04:30+00:00 | ADR-0114; ADR-0115; final-HPO config identity 2db8e95a | configs/run-final-refit.json; intraday_equities/final_model.py; intraday_equities/nodes.py; focused tests |  | GPT-5.6 Sol: PENDING template refuses plan until ten real content-addressed HPO evidence manifests and frozen schema exist. Synthetic contract only; no market data, HPO, refit, pre-March read, or push. Focused child suites: 265 passed, 11 skipped, 5 documented baseline failures; Ruff/diff clean. |
| A18866 | execute | Gate 2 deliverable 4 correction cycle 2 fail-closed contract | 2026-09-10T04:31:19+00:00 | method skeptic 8038910; ADR-0114/0116; generic driver record/carry contracts | configs/run-final-refit.json; intraday_equities/final_model.py; tests/test_final_model.py; tests/test_configs.py; AGENTS.md |  | GPT-5.6 Sol: TDD correction. Mutable HPO sidecars and asserted source/cache/window hashes are not attestations; FinalRefit now remains non-executable even with filled pins until driver-owned run and content-derived ten-wire input identity contracts exist. No market data, HPO, refit, path.csv edit, or push. Focused child/direct generic tests and validate/plan only. |
| A18867 | execute | Gate 2 deliverable 4 review-closed fail-closed contract | 2026-09-10T11:30:26+00:00 | method/API PASS 4a7a28c; architecture/governance PASS a8f177b | docs/memos/2026-09-10-final-model-replay-gate2-deliverable4-closeout.md; ../../docs/RE-ENTRY.md |  | GPT-5.6 Sol closeout: D4 is a truthful PENDING, unconditionally fail-closed FinalRefit contract, not an enabled refit or bundle. Immutable per-run HPO attestations, content-derived data/cache/window identities, and ten labelled real input wires remain required. No market data, HPO, refit, bundle write, path.csv edit, push, or pipeline run. |
| A18868 | execute | Gate 5: development replay + fill-policy (ADR-0117 proposed) | 2026-09-10T12:40:24+00:00 | docs/plans/2026-09-08-final-model-replay-and-monitoring.md; docs/memos/2026-09-09-final-model-replay-gate5a-skeptic-conformance-cycle4.md; ../../docs/architecture/decision-log.md ADR-0114 | intraday_equities/replay.py; configs/fill-policy.json; configs/run-development-replay.json; tests/test_replay.py; ../../docs/architecture/decision-log.md ADR-0117 |  | TDD pass. ADR-0117 proposed (not accepted): mixed-horizon overlap + config-driven next-bar-open fill. Synthetic/development-only caps, deployment_eligible=false. No dskit.production hook (Gate 5a). Focused: test_replay 16 passed; test_nodes_capital 59 passed; test_configs 40 passed + 5 documented baseline failures. ruff clean; validate/plan hash fa648c6b.... path.csv untouched. No skeptic this pass. |
| A18869 | execute | Gate 5 skeptic round 1 FAIL (method+architecture) | 2026-09-10T13:16:25+00:00 | c286897; ADR-0117 proposed | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture.md |  | Grok skeptics, sequential lenses. Method FAIL 2C/4M/2m. Architecture FAIL 1C/2M/3n. Parallel bar-walk; silent off-tape; halt/expiry; sticker knobs. path.csv untouched. |
| A18870 | execute | Gate 5 cycle-2: fail-closed tape/halt/knobs (skeptic round 1) | 2026-09-10T13:20:29+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture.md | intraday_equities/replay.py; tests/test_replay.py; configs/run-development-replay.json; ../../docs/architecture/decision-log.md |  | TDD. Observable refusals (unknown_symbol, fill_bar_past_tape, expiry_past_tape, lead). Halt skips exits+entries, == compare, catch-up on next non-halt. Knobs read (fill_price_field, overlap/expiry/halt/order). ReplayClock not TestClock. Honest: not ServeLoop. ADR-0117 still proposed. test_replay 22 passed. path.csv untouched. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A2822 | Gate 1: stock modelability | Lock the stock-modelability selection gate | children/intraday_equities/pipeline_runs/p12-63-asset-modelability-staged-2026-02-28-2d203f5c/stages/gate1.json; children/intraday_equities/configs/program-calendar.json; docs/decisioning/framework.md | Y |  | execute | staged gate1 | empirical |  |
| A2850 | Gate 2: HFDR in MIO | Replace the retired Bonferroni screen with an MIO constraint on false-signal gross capital | docs/architecture/decision-log.md#ADR-0088; docs/decisioning/framework.md | Y |  | research | HFDR constrained in MIO | judgemental | docs/architecture/decision-log.md |
| A2851 | ~~Gate 3: shuffle refit~~ | Audit Gate-1 selections against a session-scramble refit null | docs/research/gate3-lower-compute-null-design.md; docs/architecture/decision-log.md#ADR-0089 | N | Investigating a faster valid shuffle-training solution | research | gate3-lower-compute-null-design | empirical | docs/research/gate3-lower-compute-null-design.md |
| A2887 | Gate 3: fail-fast scramble refit | Audit every Gate-1 passer against whole-session scramble refits, stopping at the first null that matches or beats the real result | docs/architecture/decision-log.md#ADR-0092; docs/architecture/decision-log.md#ADR-0093; docs/architecture/decision-log.md#ADR-0094; children/intraday_equities/docs/memos/p12-gate3-recovery-results.md; children/intraday_equities/pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2/stages/gate3_recovery.json; children/intraday_equities/configs/program-calendar.json | Y |  | execute | Gate 3: fail-fast scramble audit over asset-local walks | empirical | docs/architecture/decision-log.md |
| A18039 | Per-signal pi estimator | Estimate calibrated posterior false-signal probability from out-of-fold and Gate-3 null evidence | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-local-fdr-pi | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md |
| A18040 | Conservative pi HFDR | Bound false-signal gross capital with conservative pi inside the MIO | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md |
| A18041 | Mean-alpha confidence intervals | Estimate uncertainty in net conditional mean alpha under temporal dependence | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md |
| A18042 | Realized-return uncertainty | Calibrate dependent predictive intervals and joint return scenarios separately from mean alpha | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md |
| A18044 | Joint U_pi set | Represent joint uncertainty in false-signal probabilities for robust HFDR constraints | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-pi | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md |
| A18046 | Joint U_mu set | Represent joint uncertainty in expected net alpha for conservative optimization | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-mu | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md |
| A18047 | Joint U_r scenarios | Represent dependent joint net-return outcomes for CVaR drawdown and Kelly risk | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-r | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md |
| A18256 | Final model MIO bundle | Require every promoted final model to publish the complete versioned fail-closed MIO forecast bundle before capital eligibility | docs/research/hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle.md; configs/run-p13-model-zoo.json; docs/architecture/decision-log.md#ADR-0088 | Y |  | research | hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle.md |
| A18623 | Predictive program calendar | Lock one temporal source of truth for modelability, model-zoo selection, finalist HPO/refit, uncertainty calibration, simulation, and production | children/intraday_equities/configs/program-calendar.json; children/intraday_equities/docs/research/predictive-program-calendar/2026-09-05-synthesis.md; docs/architecture/decision-log.md#ADR-0098 | Y |  | research | predictive-program-calendar/2026-09-05-synthesis | empirical | docs/research/predictive-program-calendar/2026-09-05-synthesis.md |
| A12635 | ~~Official model zoo protocol~~ | Compare 13 individualized model families over all 25 Gate-3-approved asset-horizon pairs on one reviewed paired outer-fold protocol | children/intraday_equities/configs/run-p13-model-zoo.json; children/intraday_equities/configs/program-calendar.json; children/intraday_equities/docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md; children/intraday_equities/docs/research/predictive-program-calendar/2026-09-05-synthesis.md; docs/architecture/decision-log.md#ADR-0097; docs/architecture/decision-log.md#ADR-0098; docs/architecture/decision-log.md#ADR-0099 | N | Superseded by the proposed pooled LightGBM/Torch-MLP zoo | research | post-gate3-predictor-output/2026-09-05-synthesis | empirical | docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
- [hstar-go.md](hstar-go.md)

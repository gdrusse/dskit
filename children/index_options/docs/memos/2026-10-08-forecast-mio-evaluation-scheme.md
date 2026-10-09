# Evaluation scheme: forecasting development through MIO research replay

## TL;DR

Feature selection and HPO used three 2021–2022 development windows; frozen settings passed a 2023 confirmation gate and were evaluated through November 2025. Those forecasting runs are complete.

The proposed MIO scheme calibrates on February–December 2024 forecasts, waits for settlement and publication, then evaluates entries from February–November 2025. The numerical and source-data launch gates remain open; implementation checks are recorded below. These already-inspected years support retrospective research, not independent production qualification.

## Sources, status and what “warmup” means

Read the [completed forecasting report](2026-10-08-restored-universe-patchtst.md), [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json), [finite feature configuration](../../configs/universe-feature-selection-20261008.json), [MIO draft](../../configs/run-equity-condor-robust-backtest.json), [dual memo](2026-10-08-robust-condor-dual.md) and [Claude handoff](2026-10-08-claude-calibration-backtest-handoff.md). The latest owner-frozen scope and reviewed ADR-0255 take precedence over older, broader handoff suggestions.

There are three different warmups:

1. **Feature history:** entry-known lagged returns and rolling statistics must exist before a forecast can be produced. The selected model consumes 42 base features, with a 22-return sequence and feature windows up to 66 observations. These are observations, not 66 calendar days. Per-ticker source coverage, gaps and saved preprocessing govern admission; there is no invented common raw-history start for all tickers.
2. **Model development:** the saved fold table calls the three 2021–2022 scored development folds “warmup.” These were feature/HPO selection windows, not an independent validation set. Their preceding training and early-stopping periods are listed below.
3. **MIO calibration accumulation:** the proposed 2024 period gathers settled forecast/payoff evidence to choose the uncertainty rule. It is not another PatchTST fit or HPO search.

Date ranges below are the **observed inclusive minima/maxima in saved fit metadata**, not promises that every intervening date or ticker is present. Training expands from all eligible older rows; 1997-06-17 is the pooled earliest training origin, not coverage for the entire universe. Eligible COVID history remains in training; no dedicated COVID tuning fold was used.

Each refit starts with fresh weights. Training-only medians/scalers and ticker-head mappings are saved. A purged last-40-origin early-stopping band selects the checkpoint epoch; the configured ceiling is 500 epochs with patience 20. “40 origins” is not 40 calendar days. Early-stopping labels influence the model and cannot be counted as independent calibration evidence.

## 1. Feature selection and HPO — completed

The five feature arms were base, minus momentum, minus liquidity, minus directional features and minus long volatility. Each ran three folds: 15 fits. The paired development comparison selected all 42 base features.

These same date bands supported all six HPO candidates:
- **Fold 1**, fit 90e034c20bb2: training origins 1997-06-17–2020-07-21, training settlements through 2020-08-21; early-stop origins 2020-08-24–2020-11-30, labels 2020-09-24–2020-12-31; scored forecast origins **2021-01-04–2021-08-31**, settlements 2021-02-04–2021-10-01.
- **Fold 2**, fit 943b5e247a86: training origins 1997-06-17–2021-03-19, training settlements through 2021-04-19; early-stop origins 2021-04-20–2021-07-30, labels 2021-05-21–2021-08-30; scored forecast origins **2021-09-03–2022-04-26**, settlements 2021-10-04–2022-05-27.
- **Fold 3**, fit 390c695ae427: training origins 1997-06-17–2021-11-12, training settlements through 2021-12-13; early-stop origins 2021-12-14–2022-03-29, labels 2022-01-14–2022-04-29; scored forecast origins **2022-05-02–2022-11-29**, settlements 2022-06-02–2022-12-30.

The six combinations were learning rates 0.0001, 0.0003 and 0.001 crossed with fusion dropout 0 or 0.1. The minimum summed paired weighted-loss ratio selected trial3: PatchTST32/head2, learning rate 0.0003, dropout 0.1. It was the included default and scored +0.878415% weighted-CRPS skill over 86,328 paired forecasts. HPO selected the default; it did not improve that development objective.

Fifteen HPO fits were new; three exact same-rerun Stage1 packages supplied the default references. No old85 checkpoint was reused. Feature selection and HPO used labels settling before 2023-01-01, not the later confirmation/evaluation scores. Full trial losses and epoch histories are in the [HPO appendix](../reports/production-universe-rerun-20261008/hpo-appendix.json).

This is one bounded three-fold development search, not a new hyperparameter search at each later retraining.

## 2. Confirmation — completed, settings frozen

Confirmation asked whether the frozen winner beat its paired empirical baseline in the next three windows for **each** prespecified seed 11, 29 and 47. It was a continuation gate, not another HPO search. The condition was strictly positive aggregate paired weighted-CRPS skill for every seed, not every individual fold.

- **Fold 1**, fit 598347343768: training origins 1997-06-17–2022-07-22, training settlements through 2022-08-22; early-stop origins 2022-08-23–2022-11-29, labels 2022-09-23–2022-12-30; scored forecast origins **2023-01-03–2023-04-25**, settlements 2023-02-03–2023-05-26.
- **Fold 2**, fit 520d4bb97f0e: training origins 1997-06-17–2022-11-08, training settlements through 2022-12-09; early-stop origins 2022-12-12–2023-03-28, labels 2023-01-12–2023-04-28; scored forecast origins **2023-05-01–2023-08-29**, settlements 2023-06-01–2023-09-29.
- **Fold 3**, fit 86c44ca94229: training origins 1997-06-17–2023-03-17, training settlements through 2023-04-17; early-stop origins 2023-04-18–2023-07-31, labels 2023-05-19–2023-08-31; scored forecast origins **2023-09-01–2023-12-29**, settlements 2023-10-02–2024-01-29.

All nine fits completed. Seed11/29/47 skills were +1.331748%, +0.818424% and +1.122986%, with 51,228 paired forecasts per seed. The gate passed. Seed11 stayed nominal; no best-seed selection followed. The final confirmation origins are in 2023, but their last outcomes settle on 2024-01-29. Calling it “2023 confirmation” must not conceal this label-maturity tail.

The [confirmation appendix](../reports/production-universe-rerun-20261008/confirmation-appendix.json) retains all training and monitoring histories and ticker aggregates. Gate use makes these observations developmental evidence, not untouched final qualification.

## 3. Forecast evaluation and saved checkpoint schedule — completed

Nine fresh expanding-history refits per seed produced 27 evaluation fits. The following are the **nominal seed11** packages. The other two seeds share these date bands and provide stability evidence.

- **Fold 1**, fit 7ee86d998055: training origins 1997-06-17–2023-08-21, training settlements through 2023-09-21; early-stop origins 2023-09-22–2024-01-05, labels 2023-10-23–2024-02-05; scored forecast origins **2024-02-06–2024-04-22**, settlements 2024-03-08–2024-05-23.
- **Fold 2**, fit 082bb1698812: training origins 1997-06-17–2023-11-03, training settlements through 2023-12-04; early-stop origins 2023-12-05–2024-03-22, labels 2024-01-05–2024-04-22; scored forecast origins **2024-04-23–2024-07-08**, settlements 2024-05-24–2024-08-08.
- **Fold 3**, fit 37ff05f7ecb3: training origins 1997-06-17–2024-01-23, training settlements through 2024-02-23; early-stop origins 2024-02-26–2024-06-07, labels 2024-03-28–2024-07-08; scored forecast origins **2024-07-09–2024-09-20**, settlements 2024-08-09–2024-10-21.
- **Fold 4**, fit 14cd6abdb2e8: training origins 1997-06-17–2024-04-12, training settlements through 2024-05-13; early-stop origins 2024-05-14–2024-08-20, labels 2024-06-14–2024-09-20; scored forecast origins **2024-09-23–2024-12-02**, settlements 2024-10-24–2025-01-02.
- **Fold 5**, fit fefe4747ada9: training origins 1997-06-17–2024-06-24, training settlements through 2024-07-25; early-stop origins 2024-07-26–2024-11-01, labels 2024-08-26–2024-12-02; scored forecast origins **2024-12-03–2025-02-21**, settlements 2025-01-03–2025-03-24.
- **Fold 6**, fit 5ed4a9c3ed19: training origins 1997-06-17–2024-09-09, training settlements through 2024-10-10; early-stop origins 2024-10-11–2025-01-21, labels 2024-11-11–2025-02-21; scored forecast origins **2025-02-24–2025-05-09**, settlements 2025-03-27–2025-06-09.
- **Fold 7**, fit cb52650eb821: training origins 1997-06-17–2024-11-26, training settlements through 2024-12-27; early-stop origins 2024-12-30–2025-04-08, labels 2025-01-30–2025-05-09; scored forecast origins **2025-05-12–2025-07-28**, settlements 2025-06-12–2025-08-28.
- **Fold 8**, fit 74a7b6af77c6: training origins 1997-06-17–2025-02-10, training settlements through 2025-03-13; early-stop origins 2025-03-14–2025-06-27, labels 2025-04-14–2025-07-28; scored forecast origins **2025-07-29–2025-10-10**, settlements 2025-08-29–2025-11-10.
- **Fold 9**, fit 7336c15cbb9e: training origins 1997-06-17–2025-04-29, training settlements through 2025-05-30; early-stop origins 2025-06-02–2025-09-09, labels 2025-07-03–2025-10-10; scored forecast origins **2025-10-13–2025-11-28**, settlements 2025-11-13–2025-12-29.

Evaluation produced 99,225 paired forecasts per seed. Weighted skills were +0.278858%, +0.240921% and +0.254481% for seeds11/29/47. Nominal95% interval coverage was only91.616%; neither positive skill nor that interval is a calibrated MIO uncertainty set. See the complete [evaluation summary](../reports/production-universe-rerun-20261008/evaluation-summary.json) and [ticker/training appendix](../reports/production-universe-rerun-20261008/evaluation-appendix.json).

All66 unique fitted packages are retained:15 feature +15 new HPO +9 confirmation +27 evaluation. The three HPO references add no fits. The conservative cumulative charge is133/216 including the prior67 charges; the closed cycle does not authorize spending its unused balance.

**Historical inference rule:** choose the verified seed11 package whose archived forecast interval contains the decision date, after all training AND monitoring labels plus required availability have matured. Otherwise skip. For example, fold9's training labels end2025-05-30 but its monitoring labels end2025-10-10; it cannot be used for June2025 decisions. Its archived forecast interval starts2025-10-13.

The inventory contains full fit/checkpoint/file identities and original/backup locations; the short identifiers above are lookup aids only. The saved nominal seed11 schedule and exact curves have now been verified and onboarded as described in the current preparation status below. Final strike-basis inputs remain pending. Load using TorchCDF.load_checkpoint and the archived recovery recipe; retain saved ordering, medians, head encoding and scaler/mask exactly once. No new fits are proposed in this build.

## 4. MIO calibration and evaluation calendar — proposed, not executed

The draft fixes exact DTE31 and the following entry-date windows, subject to verified matched coverage and availability:

- **Calibration forecast origins: 2024-02-06–2024-12-31.** Use the matching historical checkpoints (folds1–5 as applicable), eligible option chains and later settled outcomes. Do not replay these dates with the final checkpoint.
- **Calibration maturity tail: through 2025-01-31**, the last possible nominal 31-day expiry for that origin window, then the approved publication lag. Missing/late settlement observations must satisfy the bounded settlement rule and the actual update cutoff; the date endpoint is not proof all labels exist.
- **No new MIO entries in the gap 2025-01-01–2025-02-03.** This is a settlement/publication gap for this proposed entry experiment, not a ban on necessary past feature history or permitted matured labels.
- **Evaluation entries: 2025-02-04–2025-11-28.** Use nominal forecast folds5–9 according to their valid intervals. Hold each eligible condor to expiry.
- **Final nominal expiry: 2025-12-29.** Any allowed confirming settlement observation must also be strictly before2026-01-01. No protected2026 access is authorized to complete missing outcomes.

The draft publication rule adds one exchange session and requires publication strictly before the first entry date of the update month. For a label settled Friday2025-01-31, an assumed one-session lag gives Monday2025-02-03, before Tuesday2025-02-04. This illustrates the configured assumption, not verified source publication timestamps; the lag rule is still an owner choice.

The design handoff reported48 DTE31 calibration dates and43 entry dates before full admission. Those are prior reported coverage counts, **not** a new census or final eligible counts. G1 must verify protected intake, coverage, basis/actions and contract terms. Many tickers on one date do not supply that many independent time observations. Unsupported calibration must remain visible.

## 5. What gets tuned for the MIO, and where

**Forecast model:** no more feature selection, HPO, seed choice or performance-based checkpoint selection on these MIO windows. Scheduled inference changes checkpoint by historical availability, not by later profit.

**Radius rule:** owner approved expected-loss calibration on fixed audit templates and the finite-grid/smallest-passing procedure on2026-10-08. Grid:0,0.001,0.0025,0.005,0.01 of spot. Compute each template/radius loss once for matching identities; bootstrap reuses cached residuals, and rho0 permits direct nominal expectation. No passing value means unsupported, with no automatic expansion. This evidence covers the audit-template population, not every optimizer-selected trade. Monthly calibration-plus-entry updates and direct strike-grid calibration without an added projection offset are approved. Projection diagnostics remain separate; numerical settings are unresolved. No empirical rho has been computed.

The draft template matches short-put q=0.1 and short-call1-q quantiles, then one outward strike per long wing, using explicit baseline liquidity. Its calibration claim concerns that template population; later selected MILP trades require their own audit. Do not infer protection for every candidate from one template's result.

**Dependence/support:** keep same-date tickers together. The draft uses31-calendar-day primary blocks,62-day sensitivity,500 resamples, seed1900, alpha0.05 and at least8 dates. Eight dates alone are insufficient: the exact sampler must also cover every target date with nondegenerate support; otherwise refuse. The full observed residual mean must be nonpositive for a passing rule. These are proposed approximate stress diagnostics, not a95% conditional-CDF confidence guarantee. Longer sensitivity does not select a better radius after results are seen. The older60/30-trading-date forecast-comparison proposal is a different study, not this calibration sampler.

**Monthly updates:** owner approved calibration and entry-phase history under a frozen rule. Use the same fixed audit-template population, including dates when the optimizer did not trade; actual selected-trade outcomes do not replace it. At each monthly reference admit only outcomes already settled and published strictly before that reference. Reuse identity-matching loss values and recompute the bound from eligible past residuals. Never use future monthly results, change the rule on full2025 performance or revise past decisions. Retain each update's data identities, cutoff, support and radius.

**Bands and numerical settings:** Qlo/Qhi remain separately validated restrictions on the same cumulative probability vector, not values automatically produced by rho. Bands are disabled initially. Freeze numerical mesh/score-weight normalization, tie/solver tolerances and no-trade/refusal rules before evaluating choices. No fitted band widths or numerical calibrated radius currently exist.

**Costs and sensitivity arms:** entry-date VWAP, assumed liquidity-tier haircuts and the provisional0.65USD entry fee are disclosed research inputs. Base liquidity is trade_count>=5, volume>=1, pending the owner's listed threshold decision. Compare the eight declared one-factor arms: nominalrho0, base calibratedrho, haircut multipliers0/0.5/2 relative to base1, and trade-count thresholds3/10/20 relative to base5. They share baseline calibration; no Cartesian expansion or recalibration per arm. Report every arm rather than selecting a winner from2025 profits.

## 6. Validation and claims

Before any real replay, complete owner decisions/ADR approval, source relocation approval and protected census, exact checkpoint/profile onboarding, and tests-first implementation. Prove MILP versus enumerated primal-LP parity on small fixtures, rho0 and no-trade behavior, band feasibility, units, feature/forecast parity and accounting reconciliation. These implementation checks do not require training or a trading backtest.

For the later research evaluation, retain every decision, no-trade, refusal, candidate policy, forecast/checkpoint, calibration update and settlement. Report template/selected-policy loss diagnostics separately from one-condor-per-decision net P&L, with date/ticker/arm counts and cost assumptions. Reconcile zero-intrinsic expiry records through WindowBook and use the existing stage report; do not report four legs as four independent trades or infer portfolio returns from this single-trade design.

The protocol still needs frozen numeric evaluation acceptance criteria before execution. Unsupported radius, sparse effective blocks, unavailable quotes or basis uncertainty are findings, not invitations to expand search, use a later checkpoint or fabricate a fallback. Changes made after inspecting evaluation outcomes create another development iteration and must be labeled accordingly.

Already-inspected2021–2025, retrospective universe admission and assumed daily-bar fills preclude independent production qualification. A later truly untouched calibration/validation stage needs separate authorization and adequate history;2026 remains closed. The earlier126-session calibration/252-session validation collection targets are proposed future durations with no authorized dates here, not the present2024/2025 replay windows.

## Reproducibility and handoff

This memo was assembled on2026-10-08 from published aggregate metadata/configuration only. All HPO date bands were checked against the selected base folds; confirmation/evaluation seed date bands were checked for equality. No market records, data copying, new fits, calibration search, MIO solve or trading replay ran for this document.

Inventory SHA256: 49e22ce58cc52b0d5379386600551121ab2b526b51b4eccde2fcdb85cbd33492.
Reviewed design candidate:3f8ea439c96afa14f5776346738fa12746d82c54.
The forecasting artifact report is authoritative for completed outcomes; ADR-0255 and its draft JSON own the proposed replay. This memo changes neither. The current preparation status below supersedes this document's original design-only pickup.

## Current preparation status and performance-report contract

The owner subsequently authorized completing the implementation, launching the
bounded research replay, evaluating MIO performance and writing its result memo.
This authorizes no new model fits, data acquisition or protected2026 market
data. Main merge remains subject to the task's explicit approval rule.

As of candidate a103b701, the saved seed11 preparation has recovered99,225 exact
curves:49,623 calibration-window records,45,023 entry-window records and4,579
outside those windows. These are forecast counts, not admitted option trades.
The source is condor-archived-forecasts-20261008-v2/forecasts under the existing
/home/russell/data/index_options/ob root. Its record hash is
ec9b4e1584740d2bc132790a2772c31d7c5d3720516e92f2eb2143bd2783ff90.
The source spot remains split-adjusted. It cannot yet be used against historical
option strikes. The final four input streams deliberately remain unbound.

The private forecast backup recovered every record with original input roots
denied. Receipt: /home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008/condor-inputs-v2-recovery-receipt.json.
Backup: /home/russell/data/backups/index_options/production-universe-rerun-20261008/condor-inputs-v2.
It is a second copy on the same disk, not off-device disaster recovery.
The9.2GB original options store was copied and its two snapshots verified;
the original worktree-hosted copy remains intact. No mixed-year market records
were decoded by this preparation.

Adapter candidate a103b701 corrects calendar-span/count confusion, arm meaning,
missing calibration tiers, publication timing and forecast settlement agreement.
All1,413 affected cases pass, including five CLI checks rerun with an absolute
checkout PYTHONPATH after an initial wrong-checkout import. The full suite was
not run. Independent round2 reviews now close with zero Critical/Major. One Minor permanent-test coverage gap is deferred: positive settlement with a matching carried label. The math reviewer independently checked that path and reconciled197.40USD. This scoped lock does not close source or mesh launch gates.

**Not run:** historical projection, empirical radius calibration, real MIO
selection and settlement, or performance aggregation. No P&L, empirical rho or
backtest completion is claimed. Date-only mixed-JSON screening and corporate-
action metadata access remain unanswered protected-data exceptions. Two mesh
admission thresholds remain unset. These are launch gates, not a reason to
substitute adjusted prices or discard unknown action histories silently.

After those gates close and the exact resolved config is reviewed, execute the
single frozen eight-arm pipeline once. Preserve run/config/dependency/source
identities, solver certificates, every refusal and all raw outcomes privately.
The evaluation memo must report:

- Coverage by stage, date, ticker and arm, including forecast counts, eligible
  chains, missing templates, unsupported monthly rho, solver failures, no trade,
  actual trades and settled outcomes. Keep each denominator explicit.
- Monthly primary/sensitivity radius evidence, support counts and mean residuals;
  distinguish fixed-template calibration from selected-trade performance.
- Per-arm and full per-ticker condor outcome aggregates: trade count, total and
  mean net USD per one-lot trade, median, loss frequency, empirical lower tail,
  gross expiry result and fees/haircuts. An empty arm is unavailable, not zero
  risk or zero-return success. Compare arms on their shared admitted dates and
  also disclose differing eligibility.
- Primary/secondary solver status, gap and accounting reconciliation. Count
  one condor once; four legs and their closing fills are not independent trades.
- The same-day look-ahead and ignored early-assignment assumptions, price-basis
  evidence, action exclusions and residual data gaps. No portfolio return,
  deployability, confidence guarantee or independent qualification claim.

Use the existing stage artifacts and aggregate-only publication. A zero or
unsupported radius result must be reported, not replaced by a profitable
post-hoc threshold or a larger unapproved search.

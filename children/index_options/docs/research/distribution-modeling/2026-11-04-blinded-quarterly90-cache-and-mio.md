## Question

Which existing predictive recipe should produce reusable quarterly forecasts without opening a new performance evaluation, and how much of the requested quarterly MIO matrix can the existing accounting engine execute faithfully?

## Finding

Retain the original 90-input M53 family: trained MLP64, calibrated market, pooled horizon empirical and per-symbol horizon empirical. Main and the branch/worktree inventory were audited before selection. Earlier completed replacement, residual and initialization/removal studies support retention. The quarterly90 variant is separately identified; it does not claim identity with original annual M53 or the selected65-input quarterly MIO variant.

All 48 forecasts completed and passed structural verification:16 quarters,2020 Q1–2023 Q4, with seeds 11/29/47. All 16 primary seed 11 native caches are prepared. No forecast values or predictive performance were inspected, no forecasts were compared with target actuals, and no new loss/PIT tables were produced.

MIO completed for 2 of 16 requested quarters:2020 Q1 and2021Q1. Each used its latest strictly prior-data seed 11 model and all three frozen policies: `w1_003_shock10_lp`, `nominal_lp`, and `w1_003_lp`. Six standard reports are stored without policy ranking or economic-result inspection. Exact close/publication, all six accounting gates and all six report nodes are attested; both packets were reviewed/disposed serially. The shared exposure registry is inactive at revision 41. This is partial MIO coverage, not all-quarter completion.

### Frozen learning and dates

Reuse the original annual 2019 out-of-sample component bank as unranked warmup, trained on 2017–2018. Refit each quarter on full mature prior history. Both origin and settlement must precede the quarter boundary to teach any learned state. Annual epoch counts come from strictly prior monitor metadata; patience is disabled and full mature history is refitted. The neural state, market calibration and blend weights remain fixed within each quarter. Blend learning uses the original 2019 bank and earlier same-seed quarterly out-of-sample components with their original calibration states.

Forecast inference projects feature/identity fields without target outcomes. Origins determine the quarter, so quarter-crossing settlements remain forecast targets. The forecast-only blend refuses actuals/PIT evaluation and dynamic component recalibration, uses one fit-band state, and emits no evaluation tables. Structural CDF validity is checked mechanically without inspecting values.

COVID March–December 2020 remains included under locked A0007 stress coverage. A0003/A0004/A0008 warmup and maturity boundaries remain; the explicit quarterly instruction authorizes a separately identified variant without editing Path. The compatible panel ends inDecember 2023. 2017–2018 is training history and 2019 is warmup. 2024–2025 remains sealed and unexecuted because no compatible feature-only panel was admitted;2026 remains permanently sealed. The approved settlement-date availability assumption and retrospective panel/corporate-action limitations do not establish point-in-time tradability.

### MIO accounting and coverage

The fixed 115 universe is the prior 2019 admission, excluding AXTI/COHR/ECHO from the configured 118. No additional names or entries were removed. Each quarter/method starts an independent account with unchanged accepted account, cost, solver and risk settings. Calendar coverage includes the full 67-day settlement tail:2020 Q1 has 108 sessions through 2020-06-07 exclusive;2021 Q1 has 106 through 2021-06-07 exclusive. Exact replay parity and full account calendars passed. The APH forward split in2021Q1 uses the reviewed pinned standard adjustment.

Unsupported cash distributions, mixed deliverables and persistent root gaps block whole quarters for every method. The conservative gate also retains unresolved standard-series resumption and unprepared split evidence as explicit gaps. No new corporate-action accounting or identity-only padding clearance was introduced. The 14 blocked quarters follow below; the frozen support manifest records each boundary, memo and evidence identity.

- 2020 Q2: unsupported action: DVN (2020-08-13).
- 2020 Q3: unsupported action: DVN (2020-08-13), COST (2020-12-01).
- 2020 Q4: unsupported action: COST (2020-12-01).
- 2021 Q2: unsupported action: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06).
- 2021 Q3: unsupported action: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04); split not prepared: ISRG (2021-10-05), ANET (2021-11-18).
- 2021 Q4: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09); split not prepared: ISRG (2021-10-05), ANET (2021-11-18); unsupported action: IBM (2021-11-04), EOG (2021-12-14).
- 2022 Q1: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); unsupported action: EOG (2022-03-14); split not prepared: AMZN (2022-06-06).
- 2022 Q2: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); split not prepared: AMZN (2022-06-06), FTNT (2022-06-23), GOOG (2022-07-18), GOOGL (2022-07-18), GME (2022-07-22); unsupported action: EOG (2022-06-14), AMC (2022-08-22).
- 2022 Q3: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); split not prepared: GOOG (2022-07-18), GOOGL (2022-07-18), GME (2022-07-22); unsupported action: AMC (2022-08-22), EOG (2022-09-14).
- 2022 Q4: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); unsupported action: EOG (2022-12-14), GE (2023-01-04), F (2023-02-10).
- 2023 Q1: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); unsupported action: GE (2023-01-04), F (2023-02-10), EOG (2023-03-15).
- 2023 Q2: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); unsupported action: FISV (2023-06-07), AMC (2023-08-23).
- 2023 Q3: resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); unsupported action: FISV (2023-06-07), AMC (2023-08-23), DHR (2023-10-02).
- 2023 Q4: protected tail: 2024-03-08; resumption unverified: EOG (2021-07-15), GE (2021-08-02), COF (2021-08-06), DKS (2021-09-09), IBM (2021-11-04), EOG (2021-12-14); unsupported action: FISV (2023-06-07), DHR (2023-10-02), EOG (2023-12-14), COST (2023-12-27).

### Execution and review evidence

All research data, caches, logs and results remain physically in WSL. Forecast fitting/inference and MIO ran in WSL; no Mac or native Windows execution is claimed for this cache/MIO stage. Resource probes checked both host and guest disk plus shared desktop memory. Shared-registry ordering left no eligible independent MIO worker. Frozen methods were not altered to increase throughput.

Forecast closure retained 491 passing checks and 2 skips, with the earlier 944 dependency checks and bounded correction evidence. MIO adapter closure retained 507 passing checks and 2 skips, followed by independent generator and exact-native configuration reviews. Both native lenses passed with zero blocking findings; integration retained 91 metadata checks and all three report dependency checks. Two editorial nits remain: inherited freeze notes refer to an earlier integration/publication context, and a report caveat mentions a removed quarter-boundary table. Runtime identities are correct and no stale result rows remain. Earlier closed-study calendar wording and causal-admission test-coverage notes remain disclosed in `release-review-reconciliation.json`; they are unchanged, not new blocking findings.

Failed attempts remain in the evidence root: WSL restart, a refused truncated component cache followed by identical verified inference, and a launcher TypeError before any MIO claim. The launcher was corrected to use the public loader's documented return; frozen model, policy, source and configuration identities were unchanged.

### Identity pins

- Source panel SHA256: `186def50f18e23523365807942ab4862c135c6dac1361445711daeb384c25144`.
- Forecast manifest SHA256: `3562e35b43a278ca386db8b859051b3200a64d4bd0090941a6daded320af9945`.
- Forecast candidate: `52ebd89ae1201e0781a3134af90f3df8d2d6a887`; MIO adapter candidate: `8a23c1da9c5fc321f3f67420f2d9232d696c1cb4`.
- MIO execution lock SHA256: `feb14b1589dbfd562b09aee98d46b3e74dd3ce9d2c74da00c614ec5ae2f0f9b3`.
- Final structural delivery receipt SHA256: `5984079a09c6300abeb69c0d8ea896794b2790e62470292c22a3cdb8bbed2960`.

## Sources

Evidence root: `/home/russell/data/index_options/quarterly-prediction-cache-20261104`.

- `repository-audit.json`, `execution-manifest.json`, `candidate-lock.json`, `job-receipts/`, and `coverage-verification.json`: forecast selection, frozen procedure, reviews and all 48 verification.
- `mio/candidate-lock.json`, `mio/execution-preparation-lock.json`, `mio/execution-lock.json`, and their pinned independent review reports: adapter, generators and exact protocol closure.
- `mio/quarter-support-manifest.json`, `mio/corporate-support-inventory.json`, `mio/notice-census.json`, and pinned original onboarding: exact blocked gaps and bounded corporate-action evidence.
- `mio/completion/`, each `publish-*-receipt.json`, `report-*-receipt.json`, `review-*-receipt.json`, and `delivery-verification.json`: attested calendar/accounting/report closure. Reports stay unread under `mio/reports/`.
- `release-review-reconciliation.json` and final `request-status.json`: retained approval coverage and delivery commits/remote verification.

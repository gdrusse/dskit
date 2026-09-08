# P16 LightGBM feature-mask and final-gate results

## TL;DR

The lean mask had the best mean development score, `0.0065199151`, and was the simplest candidate not detectably worse than the best. Every one of 90 stock/horizon cells beat its training-only mean, 51 passed the corrected skill test and all four seasons, and the contiguous rule serves 44 horizons across 11 stocks. This is development evidence reused after mask selection, so it does **not** authorize deployment.

## Execution contract

Execution ran on 2026-09-07 through 2026-09-08 with data cut `2026-02-28`. The approved config was `configs/run-p16-feature-mask-zoo.json`, identity `986356010e3661192b3758d53d03a3cd060392dafc2c6ac3af4563dbd140f13d`, inventory approval `e8bdc03ce62626b6104932a3b2051b1454bfda91aa22c041fb79efc32740d23c`.

The experiment compared five fixed feature subsets of the same tuned pooled LightGBM model. Each candidate used 20 ordered development walk-forward folds, the same 25-stock cohort, the same direct horizons, sources, labels, HPO space, seeds, and scoring rule. The only intended difference was the feature mask. No candidate could read data on or after 2026-03-01.

Primary score: equal weight per stock and equal weight within each stock's approved horizon, using training-scale-normalized squared-error improvement over that stock's training-only mean. Selection rule: among candidates not detectably different from the best after all 10 paired comparisons were Bonferroni-controlled, choose the lowest declared compute rank. Selection never auto-promotes.

## Implementation evidence

Before execution, the walk-forward contract was strengthened so `walkforward.json` seals every prediction file and each fold's `carry.json`. `BenchmarkRun` verifies the seal. Comparison reads each artifact once, hashes those exact bytes, and scores only a private snapshot. The final gate inventory re-verifies the original run seal and the gate scorer again snapshots verified prediction bytes.

Bugbot reviewed this in a loop. Its last provenance review and its last family-aggregation review both reported no critical or major issues. The statistical skeptic reported no critical or major issue in the gate math or P16 aggregation. The related test set ended at 363 passing tests; Ruff and `git diff --check` passed. The full repository suite was deliberately not run because the touched code was covered by the focused pipeline, statistics, prediction, stage, conquest, walk-forward, benchmark, and child model-zoo tests.

## What ran

The feature-mask staged pipeline completed all seven stages. All five candidates completed 20/20 folds, for 100/100 total. The first comparison attempt halted after training with `family aggregation is missing an approved pair`: the generic family summary assumed one variant per family/group. The fold evidence remained intact. The comparator was fixed and regression-tested to average variants within each family/group, rank those family means, then equal-weight groups. Resuming completed comparison without retraining.

The gate inventory pipeline then pinned the selected candidate, its document, its walk-forward summary, 20 fold directories, 20 carry files, and all prediction files. The final gate pipeline consumed only that pinned inventory and completed.

## Candidate comparison

| Candidate | Mean path score | Fold SD | Compute rank | Selected |
|---|---:|---:|---:|---|
| lean | 0.0065199151 | 0.0023114253 | 1 | yes |
| short-lags | 0.0064755824 | 0.0023053706 | 2 | no |
| full | 0.0064005602 | 0.0023578345 | 5 | no |
| core-scales | 0.0063976225 | 0.0023437521 | 3 | no |
| no-cal-tail | 0.0062911158 | 0.0023209266 | 4 | no |

The selected candidate is `lean-pooled-h10`, document hash `0f483677cf7aea5c16563c7db8439cdc8e34736b381431c4c556816e45c7f7a5`. Its mask drops the union of the short-lag tail, multi-day scale families, and calendar tail: 33 columns.

## Guardrail calculation

For stock (s) and horizon (h), pooled performance is (R^2_{OOS}=1-SSE_{model}/SSE_{mean}), where each squared error is in the label's native unit and `mean` is learned from training only. A cell beats the mean when (R^2_{OOS}>0).

The skill test produces a pooled-time probability `p_pool` and an across-fold probability `p_fold`. The conservative raw value is (p_{raw}=max(p_{pool},p_{fold})). The family contains all (m=90) approved stock/horizon cells, so Bonferroni gives (p_{adj}=min(1,90p_{raw})). Skill passes only when the underlying test passes and (p_{adj}<0.05). No independence assumption is required for Bonferroni control.

Each cell also needs positive (R^2_{OOS}) separately in winter, spring, summer, and fall, using New York calendar months. A stock is served only through the longest uninterrupted ladder starting at horizon 1. Thus an isolated later pass is reported but cannot jump over an earlier failure.

Observed denominators: 90/90 beat the mean; 51/90 passed corrected skill; 79/90 were positive in all four seasons; 51/90 passed all three cell checks. The contiguous ladder retained 44 horizons and served 11/25 stocks. Zero observed failures in the first check is not proof that its future failure rate is zero.

## Contiguous serving caps

`ADBE 4; CIEN 5; LITE 3; LLY 2; LRCX 10; LULU 2; MSTR 6; NOW 5; PANW 1; TER 3; XLK 3.`

The other 14 stocks stop at zero because horizon 1 failed corrected skill or seasonality: `ANET, BAC, BIDU, DAL, FCX, INTC, IWM, MET, NRG, QQQ, SMH, XBI, XLE, XLF`.

## Complete stock/horizon gate matrix

Notation is `horizon: R2 / adjusted-p / weakest-season-R2 / result`. `PASS` clears mean, corrected skill, and all four seasons. `SKILL` fails corrected skill only; `SKILL+SEASON` also has at least one non-positive season. All cells cleared the mean gate.

- `ADBE` — `1:.017647/.000175/.016848/PASS; 2:.010238/.000389/.008633/PASS; 3:.006947/.00310/.003986/PASS; 4:.004893/.0188/.003497/PASS; 5:.003143/.194/.001197/SKILL`
- `ANET` — `1:.012139/.154/.007279/SKILL; 2:.006113/1/-.002033/SKILL+SEASON; 3:.005332/.344/-.001545/SKILL+SEASON`
- `BAC` — `1:.002736/1/~0/SKILL`
- `BIDU` — `1:.002782/1/-.001141/SKILL+SEASON; 2:.001556/1/.000588/SKILL`
- `CIEN` — `1:.013940/.00142/.007493/PASS; 2:.009482/.00770/.006088/PASS; 3:.006087/.0341/.004203/PASS; 4:.005555/.00831/.002930/PASS; 5:.004102/.00246/.001069/PASS`
- `DAL` — `1:.001210/1/-.001845/SKILL+SEASON`
- `FCX` — `1:.000311/1/-.002351/SKILL+SEASON`
- `INTC` — `1:.004766/.138/.000845/SKILL`
- `IWM` — `1:.003609/.0643/.001277/SKILL; 2:.003334/.00765/.003091/PASS; 3:.001805/1/.000827/SKILL; 4:.002529/.0864/.001365/SKILL; 5:.002277/.0812/.001403/SKILL`
- `LITE` — `1:.016834/.0000713/.014237/PASS; 2:.010225/.00425/.004554/PASS; 3:.007640/.00783/.003782/PASS; 4:.005845/.0710/.002325/SKILL; 5:.005335/.0322/.002875/PASS`
- `LLY` — `1:.012416/.00206/.008663/PASS; 2:.007317/.0140/.003808/PASS; 3:.004626/.677/.000320/SKILL`
- `LRCX` — `1:.028307/.00459/.021594/PASS; 2:.016859/.00257/.013738/PASS; 3:.013273/.00132/.010546/PASS; 4:.010197/.00302/.008736/PASS; 5:.008631/.0107/.006144/PASS; 6:.007987/.00287/.005395/PASS; 7:.007092/.00178/.004694/PASS; 8:.006059/.00282/.003584/PASS; 9:.005784/.000790/.003716/PASS; 10:.003818/.00523/.001936/PASS`
- `LULU` — `1:.018362/.000566/.013032/PASS; 2:.011442/.00467/.005446/PASS; 3:.007258/.0636/.002482/SKILL; 4:.006426/.0761/.002090/SKILL; 5:.005059/.0252/.001908/PASS`
- `MET` — `1:.003144/1/-.000271/SKILL+SEASON`
- `MSTR` — `1:.028636/.0102/.024209/PASS; 2:.020964/.00493/.016407/PASS; 3:.013618/.0199/.010776/PASS; 4:.011083/.0390/.007839/PASS; 5:.008321/.0102/.006853/PASS; 6:.006787/.00925/.004824/PASS; 7:.005571/.119/.003793/SKILL; 8:.004836/.0512/.002619/SKILL; 9:.004067/.0901/.001330/SKILL; 10:.003469/.0478/.000970/PASS`
- `NOW` — `1:.036384/.0000000735/.033789/PASS; 2:.022724/.0000512/.019477/PASS; 3:.016873/.000177/.013521/PASS; 4:.013098/.000185/.009191/PASS; 5:.009163/.000133/.006236/PASS`
- `NRG` — `1:.004699/.851/.000259/SKILL`
- `PANW` — `1:.012002/.00386/.005481/PASS; 2:.007463/.0627/.003417/SKILL; 3:.005256/.288/.002966/SKILL; 4:.005178/.171/.004039/SKILL; 5:.004864/.0143/.002378/PASS`
- `QQQ` — `1:.002329/1/-.000298/SKILL+SEASON`
- `SMH` — `1:.005122/.160/.000654/SKILL; 2:.003064/.300/.000377/SKILL; 3:.003465/.556/.000504/SKILL; 4:.002518/1/-.000637/SKILL+SEASON; 5:.002204/1/-.000143/SKILL+SEASON`
- `TER` — `1:.020101/.000322/.015130/PASS; 2:.012586/.000352/.009212/PASS; 3:.009998/.00545/.006681/PASS; 4:.006760/.182/.004186/SKILL; 5:.006304/.00829/.005579/PASS`
- `XBI` — `1:.001054/1/-.000096/SKILL+SEASON`
- `XLE` — `1:.001148/1/-.000112/SKILL+SEASON`
- `XLF` — `1:.008300/.635/.005542/SKILL; 2:.003904/1/.001341/SKILL; 3:.002698/1/.001218/SKILL`
- `XLK` — `1:.011897/.00000973/.005549/PASS; 2:.007251/.000141/.004498/PASS; 3:.006378/.000191/.003865/PASS; 4:.002874/.259/.000039/SKILL; 5:.002499/.0280/.000644/PASS`

## Failures, limits, and deliberately unrun work

The first comparison attempt failed closed on repeated family variants; it was fixed, independently reviewed, and resumed. No fold failed, skipped, or retrained during recovery. Isolated late passes for `IWM h2`, `LITE h5`, `LULU h5`, `MSTR h10`, `PANW h5`, `TER h5`, and `XLK h5` are not served because an earlier horizon broke continuity.

The same 20 development folds selected the mask and supplied the final gates. That reuse makes the results optimistic relative to a truly untouched exam. `deployment_eligible=false` is therefore mandatory. No final refit, lockbox run, trading backtest, MIO feed, or production promotion was run.

## Reproducibility and handoff

Commands:

- `python -m dskit.pipeline staged configs/run-p16-feature-mask-zoo.json --asof 2026-02-28 --adapter intraday_equities`
- `python -m dskit.pipeline staged configs/run-p16-final-model-gate-inventory.json --asof 2026-02-28 --adapter intraday_equities`
- `python -m dskit.pipeline staged configs/run-p16-final-model-gates.json --asof 2026-02-28 --adapter intraday_equities`

Durable evidence:

- Benchmark run: `pipeline_runs/p16-feature-mask-zoo-staged-2026-02-28-98635601/stages/run.json`, SHA-256 `30310d89c4ea99fdc224ac497c535286c8a4e6cc9f3ee103338270cd99ca2d9a`.
- Comparison: `pipeline_runs/p16-feature-mask-zoo-staged-2026-02-28-98635601/stages/compare.json`, SHA-256 `f29bbfa51b1c84bf5e686d9c63c39637d0b1e8f70116ab2a69f6d894a729061c`.
- Gate inventory: `pipeline_runs/p16-final-model-gate-inventory-staged-2026-02-28-fa061189/stages/inventory.json`, SHA-256 `3c0741e3c2018718d73a2f532db2d6095ae48c478b89afd827b8f1e193e604c4`.
- Gate result: `pipeline_runs/p16-final-model-gates-staged-2026-02-28-77a7ab08/stages/gates.json`, SHA-256 `1fb4dec20a7ac32867f8a9cb20d1c96225b2818029034ab5ce7f1b2c872d8771`.

Recommended next authorized action: design or approve an untouched confirmation run for the lean mask and declared contiguous caps. Until that exists, treat this as the final development recommendation only.

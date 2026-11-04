## Question

Does M53 benefit from trained MLP weights beyond their random initialization?

## Finding

Yes. Across fixed seeds 11/29/47, training reduced primary weighted CRPS by
0.117438%, expiry/symbol paired 95% interval [0.052556%, 0.182319%].
Mean trained/untrained losses were 0.525851409/0.526469683 on the same
449,498 eligible events in 2020–2023. Full CRPS improved 0.114211%
[0.052421%, 0.176001%]. Average event losses precede inference: the three
seeds are neither extra observations nor a mixture forecast.

Original M53 seed 11 improved 0.205521% [0.104061%, 0.306980%]; seed 29
improved 0.113318% [0.040638%, 0.185998%]; seed 47 improved 0.033415%
[-0.015788%, 0.082617%]. Seeds 11/29 still exclude zero after Bonferroni
adjustment across the three seeds. Annual aggregate primary gains were
0.123473%, 0.104112%, 0.107316%, 0.135787%; annual inference is descriptive.
The standalone MLP improved 8.809965%, 5.536904%, 4.593087%, respectively.
All untrained blends had higher primary point-estimate loss than no MLP.

Training changes the saved tensors and improves forecasts. best_epoch=1 is
AFTER the first epoch's optimizer updates. This does not isolate conditional
feature learning from improved unconditional distribution shape. A control
that breaks input/outcome alignment is the next informative test, not yet run.

Original trained seed 11 was reused; five new four-component blends each
relearned 90 updates using matched histories and calibration. No ranked
fallbacks. All exits were zero. Initial tensors were reconstructed, not saved
historically; all 15 WSL/Mac initial hashes matched. Maximum trained-source
reproduction error was 4.41e-7 against 1e-6 tolerance. Nine seed-47 source rows
(three ranked) were one float64 step above probability one; a reviewed bounded
clip retained all rows and original source bytes. Larger/nonfinite violations
still refuse. Two fresh final reviews passed (176/127 checks, zero findings),
19 focused library tests passed, and 280 report consistency checks passed.

Mac inference and five blend jobs wrote only to verified WSL-backed storage;
WSL analysis exited zero in 385.60 seconds, peak 1,348.71 MiB. Intervals are
conditional on these fixed seeds and previously exposed years. Settlement
dates remain an approved availability assumption, not publication evidence.
2024+ outcomes stayed excluded. No neural refit, tuning, promotion or trading.

## Sources

- Evidence: `/home/russell/data/index_options/mlp-learning-audit-20261103/m53-initialization-study/`;
  `comparison.json`, `paired-scores.parquet`, `completion.json`,
  `prediction-freeze.json`, `final-lock.json`, `controls-verification.json`.
- Comparison SHA256: `2883d9e7c833814358ef73e8450c63058e2ac1246a3f9c46b5d7b86da7516150`.
- Completion SHA256: `21ff32844123f0119d8834487fc4268231ed972001bf0c3a96d4826b6a8864e0`.
- Scientific candidate: `14b767369eda16ea83037a7becbdd692a2ac29b5`.
- Locked Path A0002/A0003/A0005/A0008 retained. Owner authorized the comparison;
  no Path edit, protected-window access, shared-registry write, merge or push.

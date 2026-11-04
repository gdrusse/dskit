## Question

Does removing the one-standard-error simplicity preference improve standalone prediction when counts and Lasso penalties are chosen at the exact minimum historical inner-validation error?

## Finding

The user authorized this amendment on 3 November 2026. The only selection change is to minimize the exact pooled projected-CDF inner-validation score, with no standard-error tolerance, feature-count preference or stronger-penalty preference. Exact ties retain the original deterministic enumeration order. Standard errors remain descriptive. The existing 0–106 feature-count forward/backward paths and seven penalties (0.1, 0.03, 0.01, 0.003, 0.001, 0.0003, 0.0001) remain fixed. This is not exhaustive subset selection, and no smaller penalty was added. Three primary selections hit the weakest tested penalty.

Fifteen immutable inner-fold candidate bundles were reused after hash/input checks because fitting those candidates is independent of the selection rule. All 20 final models (four procedures, five annual folds including the excluded 2019 warmup) were refitted in WSL2 and frozen before scoring. No new remote worker was needed. Original study outputs were preserved.

On the same 449,498 observations in 2020–2023, selecting across procedures yields primary twCRPS 0.527352578350, a +0.355523% improvement versus calibrated market with expiry/symbol paired 95% interval [-0.062460%, +0.773506%]. Its direct change versus the prior one-SE primary is -0.057479% [-0.293036%, +0.178078%]. It selects 3, 25, 1 and 3 active features, using backward selection in 2021 and forward selection otherwise.

Forward-only selects 3, 47, 1 and 3 active features, with twCRPS 0.526995688300 and +0.422958% versus calibrated market [0.014245%, 0.831671%]. Its direct change versus prior one-SE forward selection is +0.010235% [-0.210750%, +0.231221%]. Backward-only gains +0.358695% versus calibrated market; unrestricted Lasso gains +0.337123%. These alternatives were frozen before scoring. The better annual forward-only result does not replace the prespecified cross-procedure primary after inspection.

The original ten-feature Lasso remains at twCRPS 0.526740719780, +0.471135% versus calibrated market. New primary versus that Lasso is -0.116159% [-0.380227%, +0.147908%]; new forward-only versus it is -0.048405% [-0.298916%, +0.202106%]. No clear improvement over the simpler prior approach is established.

The clearest generalization warning is 2021. Primary training improvement over the training-only constant increases from 2.252433% to 3.609422%, while the historical monitor gain deteriorates from +0.070063% to -1.514698%. These periods have different regimes, so the gap is not a pure measurement of overfitting. Neither nested rule selects on original monitor labels.

For absolute comparison, the 106-input MLP scores 0.527967918110 and M53's historical MLP alone scores 0.528599850348. Full historical M53 scores 0.525580619400; it is a rolling market/empirical/MLP blend, not a standalone annual fit. New primary versus full M53 is -0.337143% [-0.608105%, -0.066181%]. This comparison uses the same annual forecast identities.

Verification passed: each selection equals the independently recomputed candidate minimum, including synthetic cases where a 1e-10 lower score beats a much simpler model regardless of diagnostic SE. Annual primary-score reconstruction differs by at most 1.33e-15, finite monotone forecasts and all input/model/identity hashes pass, and original outputs are unchanged. Canvas TypeScript validation reports zero errors. Browser report controls and charts were checked, with no browser errors. The full test suite was not run.

The original annual results and minimum-CV diagnostics had already been viewed before this amendment. These are exploratory, pointwise intervals with no repeated-search correction. The historical input inventory retains unresolved source-time fields. No Greek or open-interest-named features are active in the cross-procedure primary; forward-only 2021 does contain such inputs. Protected 2024–2025 and 2026 remained sealed. No MIO, allocation, production promotion or model registry mutation occurred.

## Sources

- User instruction: remove the standard-error constraint and rerun.
- New frozen protocol: /home/russell/data/index_options/mlp-learning-audit-20261103/nested-minloss-study/protocol.json; SHA256 1c7e031cd28b0e8d8a0662fa7affb203a02e9542c28870dcc85fb8488630eaeb.
- Selection and annual scores: nested-minloss-study/selection-results.json and results.json under the same audit root.
- Direct paired comparisons and global-minimum assertions: nested-minloss-study/comparison.json.
- M53/MLP comparisons: nested-minloss-study/m53-comparison.json.
- Verification: nested-minloss-study/verification.json, minimum-rule-tests.json and parent-manifest.json.
- Previous frozen study: /home/russell/data/index_options/mlp-learning-audit-20261103/nested-selection-study.
- Interactive canvas: /home/russell/.cursor/projects/home-russell-dskit/canvases/feature-selection-minimum.canvas.tsx.
- Browser report and plot originals: nested-minloss-study/report.html, minimum-selection-curves.png and minimum-generalization.png.

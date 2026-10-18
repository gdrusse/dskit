## Question

Can focused tuning of the strongest four parent families establish stable local prediction improvements over market?

## Finding

Twenty settings (sixteen perturbations and four controls) completed both aligned stages: 40 JSON runs and 240 outputs. Warm-up-only stability selection fixed four global settings and eight model/pocket pairs. No local daily or robust screen passes against any comparator. All four selected global settings are slightly worse than their exact parents on validation mean log loss. The ETH 11m / 10-20% region has consistent directional gains across four families and both validation halves, but remains an unconfirmed fixed research lead. Retain method24; stop more parameter tuning on this history and prioritize probability/feature timing quality.

One initial writer attempt failed because the results directory was absent; its failed record is retained and its completed retry uses unused placement. All original controls reproduce prior predictions exactly. Author audit verified all240 outputs and501 fit censuses;127 focused tests passed. Independent review pending. Previously inspected history cannot establish untouched confirmation.

## Sources

- docs/memos/2026-10-18-pocket-tuning.md
- docs/memos/2026-10-18-pocket-tuning-evidence.json
- /home/russell/data/crypto_trading/research/pocket-tuning-20261018/selection-lock.json
- https://scikit-learn.org/stable/modules/cross_validation.html
- https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.ARDRegression.html

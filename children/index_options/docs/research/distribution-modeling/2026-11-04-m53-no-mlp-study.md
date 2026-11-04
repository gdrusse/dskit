## Question

What happens if M53 removes its MLP component and relearns the remaining weights?

## Finding

Keep M53. On the same 449,498 eligible forecasts (2020–2023), weighted CRPS
rose from 0.525580619 to 0.526294563: 0.135839% more loss, paired expiry/symbol
95% interval [0.020210%, 0.251468%]. Annual increases were 0.114230%,
0.118629%, 0.177637%, and 0.130851%. Full CRPS rose 0.142095%.
The reduced blend still beat calibrated market by 0.555437%, retaining 80.5%
of M53's observed improvement. This supports a small contribution to this
blend, without isolating learned features from the MLP's other properties.

One frozen ablation retained calibration, 21-day updates, dispersion groups,
199 quantiles, optimizer and prior-settled histories. All 90 blocks matched;
ranked fallback counts were zero in both runs. No neural refitting or tuning.
Mac exit 0: 612.63 seconds, 7.71 GiB peak. WSL verification reproduced original
scores, checked identities/CDFs/tables and recomputed paired differences.

An initial reporting assertion failed because both files also contain 261,687
non-ranked rows. A reviewed one-to-one join restored the predeclared cohort;
fitting, scoring and cohort definitions stayed unchanged. Both original
reviews and both affected follow-up reviews passed with zero findings.
Settlement dates are an availability assumption; these exposed historical
years do not establish tradability or fresh confirmation. 2024+ stayed sealed.

## Sources

- Evidence: `/home/russell/data/index_options/mlp-learning-audit-20261103/m53-no-mlp-study/`;
  `comparison.json`, `verification.json`, `protocol.json`, `analysis-repair.json`.
- Comparison SHA256: `559d4207347863e66b9d3e23096b51a2b2c104fad7e10640ad011c18c4339ae4`.
- Scientific candidate: `cde6b98e322226972637bed9b622efbf5c47e213`.
- Locked Path A0002/A0003/A0005/A0008 retained; current removal authorized by
  the owner question. No Path change, promotion, merge or push.

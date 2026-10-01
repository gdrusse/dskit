# Decision-region loss model zoo

## Decision

Do not promote a model. Training directly at actual listed strike cutoffs is
technically viable, but this first descriptive zoo does not establish local
predictive skill. The wide decision-weighted MLP had the best aggregate point
estimate, +1.173% equal-cell actual-strike Brier skill versus the horizon
empirical reference, but its paired 30-date interval was [-5.727%, +4.564%]
and its 60-date interval was [-5.103%, +4.511%]. Its apparent gain was SPY-only:
+2.396% by raw index mean, while QQQ was -4.376%.

## Model and loss

The target is standardized terminal log return. For each forecast identity,
the entry-date archive is joined by exact symbol, quote date, and expiry. The
existing eligible-condor owner enumerates the complete quotable fixed-width
candidate set. Every distinct listed strike used by an eligible put or call
wing becomes a threshold in exact coordinates
`log(strike / entry_spot) / reference_scale`. Thresholds are deduplicated and
put/call sides each receive half of a row's local weight.

The Torch challengers use an analytic Gaussian-mixture CDF and optimize
mixture negative log likelihood plus a positive fixed global-grid CDF Brier
term and an actual-strike CDF Brier term. Thus the distribution remains
globally identified while finite model capacity is concentrated at the
decision cutoffs. The boosted challenger is a separate monotone LightGBM
binary CDF model on expanded `(features, cutoff)` rows; it is not wrapped in
Torch. It predicts directly at each listed strike and is monotone in cutoff.

The six frozen candidates were horizon empirical, a global mixture-MLP
control, one-component `[16]`, three-component `[32,16]`, and three-component
`[64,32]` decision-weighted MLPs, and direct-CDF LightGBM. Raw versus
calibrated variants were selected using development actual-strike Brier; raw
won for every architecture.

## Procedure and sample

The panel retained 32,588 rows from 2011--2019. Chronological folds evaluated
2017 and 2018 for development choice and 2019 descriptively; every fit and
calibration boundary purged outcomes not settled before the boundary. The
later comparison contains 5,923 forecasts per model across 251 entry dates and
232 expiry series; 5,917 had eligible listed decision strikes. Every model saw
the same identities, features, thresholds, weights, and folds.

The primary descriptive endpoint is actual-strike Brier, aggregated by exact
index/day cell. Date-block bootstrap samples carry all same-date indexes and
horizons together. A cell absent from a sampled date panel is omitted from
that replicate rather than imputed as zero. Global CRPS and tail diagnostics
remain non-promotion safeguards, but this run deliberately has no
preregistered promotion gate.

## Results

| Model | Local skill vs empirical | 30-date interval | 60-date interval | Global CRPS skill |
|---|---:|---:|---:|---:|
| Global MLP control | +0.562% | [-6.096%, +3.777%] | [-5.022%, +3.309%] | -0.541% |
| Decision MLP `[16]` | -3.101% | [-11.335%, +1.054%] | [-11.395%, +1.439%] | -4.392% |
| Decision MLP `[32,16]` | +0.843% | [-5.866%, +4.170%] | [-4.682%, +3.598%] | -0.392% |
| Decision MLP `[64,32]` | **+1.173%** | [-5.727%, +4.564%] | [-5.103%, +4.511%] | -0.191% |
| Direct-CDF LightGBM | -1.503% | [-16.678%, +7.280%] | [-16.642%, +7.977%] | -2.792% |

By raw index mean, the wide MLP was +2.396% for SPY and -4.376% for QQQ.
Every neural challenger and LightGBM worsened QQQ actual-strike Brier versus
empirical. No challenger improved global CRPS. Therefore the local aggregate
lead is neither statistically resolved nor cross-index robust.

## Execution and review

The clean r5 run completed all 36 model/fold fits and reporting in 4m22s with
3.21 GiB maximum RSS. It ran in WSL2 on an RTX 5060 Ti inside a verified
transient cgroup with `MemoryMax=6G`, no swap, and `RuntimeMaxSec=1800`.
Focused tests passed 165. Two independent Terra skeptic loops finished at zero
Critical and zero Major findings. Earlier immutable attempts preserved useful
refusals: missing artifact binding, chain-row cap, CUDA/RLIMIT-AS incompatibility,
missing deterministic CUDA environment, sparse-cell bootstrap handling, and a
shutdown-only timer hazard. Each correction was standardized; no one-off
execution script was introduced.

Evidence is under
`pipeline_runs/predictive_cdf_decision_loss_zoo_r5_20260930`. The comparison,
scores, protocol, and provenance SHA256 values are respectively
`89be7239111a29286008e2e0900a40e836dbf9b46f0bf1f926857b5b3e1eb8e1`,
`acbb9e30433ceaab6ce60d2d777508e2c60d75fd7a86fa4364768b25a607c0ab`,
`8a765f9f284c5a222f13af22b2fe1ee1d417c5050cac86f5ef42a0f7bd9fb859`,
and `31784337f9d82db8d8fc1f4f38c84427a4b85f603d2c69cca8757d724d91fa62`.

## Next step

Do not widen this architecture zoo. The useful follow-up is a frozen,
index-specific ablation: determine whether SPY's modest local response survives
later date blocks while diagnosing why all learned models degrade QQQ. Only if
SPY clears a preregistered local interval plus global/tail noninferiority gate
should the CDF feed robust condor optimization.

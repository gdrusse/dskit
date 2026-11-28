# Correctness and chronology skeptic review

- Candidate: 9b476c1eb4b7a31c5329adfcc06330ad7d86b2a9
- Base: 13068c945a99c6e52b3ceb3754e54b785fb7b328
- Lens: fresh correctness, math, chronology, API and companion-notation review; reviewer did not author design or code.
- Contract: ADR-0331, package README, child path rows A0001-A0010 for any separate study.
- Candidate TeX SHA-256: efc199bb2cb2df4fefee852f0f00c76abaf861878d102774b6939357c66d5c7e
- Parent TeX SHA-256: 9596de611c5e44f468055ae8258b79d7df2c1ad957219de70532359be1cc9ef4
- Candidate PDF SHA-256: 102ca402f42707d277d0ac3d0c95767b4be0b6de7ab8fd80128e57d58a1a6b74

## Execution and result
Fresh storage admission status was allowed with no reservations. Julia ran through the prescribed guarded wrapper using the specified cache/output/tmp, RAM, Julia-thread and BLAS settings.

Command: julia --startup-file=no --project=julia/DskitCopulas julia/DskitCopulas/test/runtests.jl

All 99 focused assertions passed: causal fit; reference densities/prospective scoring; ragged support/order/clipping; PSD repair; malformed/numerical refusal; seeded joint draws/inverse-map order. First Julia slot released to parent/second lens.

## Matrix reviewed
- All three public entry points: fit_copula, score_copula, sample_copula. No alternate facade exists.
- Fit chronology: origin < cutoff and known_at <= cutoff. Prospective chronology: fit cutoff <= origin < score cutoff and known_at <= score cutoff. Exact boundaries and delayed labels are tested; clock types must match.
- Missingness and keys: pairs form only within the same origin. Duplicate eligible keys, malformed eligible PITs and insufficient coordinate/pair support refuse. Ragged rows and coordinate reordering are tested. Unselected coordinates are ignored by design because caller chooses a subset.
- Moments/repair: fixed-median signs, ties on lower side, same-origin overlap and unsupported-pair zero prior match ADR-0331. Repair h=max(shrinkage,(floor-b)/(1-b)) when needed preserves unit diagonal; Cholesky positive definiteness is checked. Indefinite repair is tested.
- Score: reviewed bivariate t/Gaussian density ratio and fixed-pair pooled normalization. The t bivariate normalizer -log(2*pi)-0.5log(1-r^2) is valid for every df since Gamma((nu+2)/2)/Gamma(nu/2)/(nu*pi)=1/(2*pi). Transforms, contributions and aggregates are checked finite. Candidates share pair-origin terms; per-edge zero coverage is reported; an empty panel refuses.
- df/controls: finite df in [1,200], exact Gaussian as Inf, tie ordering Gaussian then larger df. Independence diagnostic is zero. Reference tests cover finite candidates and Gaussian.
- Sampling: one normal vector and one shared chi-square radial variate per scenario, common scale across coordinates. Seeded batching and both joint tails tested. Invalid radial draws, nonfinite latent values and endpoint uniforms refuse. Output is coordinate rows by scenario columns.
- Marginal inverse: code, tests, README and TeX use inverse calibration then inverse raw forecast CDF; one cell price is shared by its options.
- Provenance boundary: known_at represents earliest usable availability. Issuance/map lineage, corrected-PIT construction and immutable origin mapping are caller-certified. The kernel cannot verify source forecast/map or settlement authenticity from the four-field tuple. This is the ADR-approved trust boundary, not a defect or a kernel-enforced provenance claim.
- TeX/code: checked moments, shrinkage, score equations, shared radial sampler, prospective cutoffs, inverse order and numerical limits. TeX/source/PDF pins match; parent retained clean visual QA for all nine pages.

## Findings and dispositions
No proven findings.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Major | 0 |
| Minor | 0 |
| Nit | 0 |

## Untested limits
- No production or historical market data; no data-study approval implied.
- Did not rerun the wide benchmark, repository suite or PDF renderer; focused Julia tests passed and parent retained PDF visual QA.
- Did not exhaustively enumerate Julia/Distributions versions, arbitrary record iterators or the entire 1,328-coordinate workload. Findings are scoped to this immutable candidate and API contract.
- Provenance authenticity remains outside the numerical kernel trust boundary above.

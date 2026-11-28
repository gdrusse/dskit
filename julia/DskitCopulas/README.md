# DskitCopulas

Fit, prospectively score and sample a Student t or Gaussian copula over issued,
settled PITs. The fit takes a caller-selected subset of coordinates and scores
specified pairs. Gaussian and independence controls use the same pair-origin
terms as the fitted family. See [ADR-0331](../../docs/architecture/decision-log.md#adr-0331--fit-and-sample-an-elliptical-copula-from-issued-settled-pits-2026-11-27).

Run from the repository root in WSL2 with Julia 1.13.

```sh
julia --startup-file=no --project=julia/DskitCopulas -e 'using Pkg; Pkg.instantiate()'
julia --startup-file=no --project=julia/DskitCopulas julia/DskitCopulas/test/runtests.jl
OPENBLAS_NUM_THREADS=2 julia --startup-file=no --project=julia/DskitCopulas julia/DskitCopulas/benchmark/run.jl
```

## Fit, score and draw

A coordinate is a unique nonblank string. The supplied order defines the rows
of the dispersion, factor and output draws. Dates, timestamps or integer
session indices share a consistent type. Supply each corrected PIT from its
issued map, with the first cutoff when its settled outcome became available.
Include one record per origin and coordinate and omit unavailable outcomes.
Fit reads PITs for origins before the fit cutoff whose outcomes are available
by that cutoff. Prospective scoring starts at the fit cutoff and uses outcomes
available by the later scoring cutoff.

```julia
using DskitCopulas, Random

cells = ["A/1", "B/1"]
training_pits = [.15 .10; .72 .65; .25 .75]
training = [(origin=a, coordinate=cells[j], known_at=a+1, pit=training_pits[a,j])
            for a in 1:3 for j in 1:2]

model, fit = fit_copula(training, 5, cells; score_pairs=[(1, 2)], min_overlap=2,
                       shrinkage=.2, eigen_floor=.05, df_grid=[4, 8], epsilon=1e-6)
@assert model.df == 4
@assert model.dispersion[1, 2] ≈ 0.4

subsequent = [(origin=5, coordinate="A/1", known_at=6, pit=.40),
              (origin=5, coordinate="B/1", known_at=7, pit=.70)]
scores = score_copula(model, subsequent, 7)
@assert scores.terms == 1
@assert scores.selected_score ≈ 0.01916251571503

uniforms = sample_copula(Xoshiro(321), model, 2000; batch_size=256)
@assert size(uniforms) == (2, 2000)
```

The sign estimator assumes calibrated continuous PIT margins. The overlap
matrix and side counts show coverage and median bias. Median ties count on
the lower side. A pair below the configured overlap uses a zero prior
coefficient when it is outside the scoring set. The fit rejects an unsupported
coordinate or scoring pair. Each candidate's composite score averages log
densities over the same observed pair-origin terms. The supplied finite degrees
of freedom must lie in [1, 200]. Exact Gaussian is always a candidate.
An exact tie selects Gaussian first and then larger finite degrees of freedom.

The reported clipping count covers distinct PIT observations used by scored
pairs. Clipping applies to the quantile passed to density evaluation. Shared
radial scaling makes finite-df t draws tail-dependent even when off-diagonal
dispersion coefficients are zero. The supplied RNG generates each scenario's
normal vector and then its one radial variate if the fit selected Student t.
The RNG sequence is independent of the batching parameter. Floating-point BLAS
results can vary with numerical settings and the package versions. Treat the
state and its arrays as read-only.

## Compose current marginal inverses in Julia

Each model row represents one currently forecast cell. Apply that cell's
inverse calibration map first and its raw forecast quantile second. All
contracts on that cell use the same price draw. The maps below are synthetic.

```julia
inverse_correction = (w -> w^2, w -> sqrt(w))
inverse_forecast = (u -> 90 + 20u, u -> 110 + 8u)
prices = [inverse_forecast[j](inverse_correction[j](uniforms[j, k]))
          for j in axes(uniforms, 1), k in axes(uniforms, 2)]
@assert prices[1, :] ≈ 90 .+ 20 .* uniforms[1, :] .^ 2
@assert prices[2, :] ≈ 110 .+ 8 .* sqrt.(uniforms[2, :])
```

The synthetic benchmark reports first-call compilation, warm time and
allocations for fitting, scoring, drawing and the two inverse maps. It
includes 1,070 origins, 1,328 coordinates, all 881,128 dispersion pairs and
64 declared score edges. Synthetic measurements establish engineering
performance. Calibration and portfolio risk require separate evidence.

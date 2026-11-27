using DskitCopulas
using Test

observations(values, labels; start=1) = [
    (origin=start+a-1, coordinate=labels[j], known_at=start+a, pit=values[a, j])
    for a in axes(values, 1) for j in axes(values, 2) if !ismissing(values[a, j])
]

@testset "A causal bivariate PIT fit" begin
    labels = ["A/1", "A/2"]
    training = observations([.21 .18; .66 .53; .14 .29; .85 .73; .79 .35; .42 .68], labels)
    fitted = fit_copula(training, 8, labels; score_pairs=[(1, 2)], min_overlap=2,
                       shrinkage=.2, eigen_floor=.01, df_grid=[1, 4, 8, 200], epsilon=1e-6)
    @test hasproperty(fitted, :model)
    @test hasproperty(fitted, :diagnostics)
end

using Dates: Date, Day
using Distributions: Chisq
using LinearAlgebra: Symmetric, eigmin
using Random

const OPTIONS = (score_pairs=[(1, 2)], min_overlap=2, shrinkage=.2, eigen_floor=.01,
                 df_grid=[1, 4, 8, 200], epsilon=1e-6)

fit(records, cutoff, labels; options...) = fit_copula(records, cutoff, labels; OPTIONS..., options...)
event(a, id, known, w) = (origin=a, coordinate=id, known_at=known, pit=w)

@testset "Reference densities and prospective scoring" begin
    labels = ["A/1", "A/2"]
    training = observations([.21 .18; .66 .53; .14 .29; .85 .73; .79 .35; .42 .68], labels)
    later = observations([.32 .27; .83 .79; .12 .75; .78 .93], labels; start=8)
    fitted = fit(training, 8, labels)
    model, diagnostics = fitted
    @test model.coordinates == labels
    @test model.dispersion ≈ [1.0 .4; .4 1.0]
    @test model.factor * model.factor' ≈ model.dispersion
    @test model.cutoff == 8
    @test model.df == 1
    @test diagnostics.overlap == [6 6; 6 6]
    @test diagnostics.terms == 6
    # Independent reference from SciPy 1.18.1 multivariate_t/normal and marginal logpdf.
    @test [item.df for item in diagnostics.candidate_scores] == [Inf, 200, 8, 4, 1]
    @test [item.score for item in diagnostics.candidate_scores] ≈
        [.12431655871568821, .12525352240671328, .14699327034383103,
         .16760402615778733, .23185599961264822] rtol=1e-9
    history = vcat(training, later, [event(7, labels[1], 9, .01), event(7, labels[2], 9, .99)])
    scored = score_copula(model, history, 12)
    @test scored.coverage == [4]
    @test scored.terms == 4
    @test scored.selected_df == model.df
    @test scored.selected_score ≈ .13951138634773141 rtol=1e-9
    @test scored.gaussian_score ≈ .09315172034742919 rtol=1e-9
    @test [item.score for item in scored.candidate_scores] ≈
        [.09315172034742919, .09343311588755798, .10184398671124528,
         .11226242855236279, .13951138634773141] rtol=1e-9
    @test scored.independence_score === 0.0
    @test fitted.diagnostics.independence_score === 0.0
    @test_throws ArgumentError score_copula(model, training, 9)
    @test_throws ArgumentError score_copula(model, later, 8)
    @test_throws ArgumentError score_copula(model, later, Date(2026, 1, 1))

    first_day = Date(2026, 1, 1)
    date_rows = [event(first_day + Day(r.origin), r.coordinate,
                       first_day + Day(r.known_at), r.pit) for r in training]
    dated = fit(date_rows, first_day + Day(8), labels)
    @test dated.model.cutoff == first_day + Day(8)
    @test dated.model.dispersion == model.dispersion
end

@testset "Availability, ragged support, order and clipping" begin
    labels = ["A/1", "A/2", "B/1"]
    training = observations([.5 .8 missing; .99 .2 missing; missing .1 .3;
                             missing .7 .8; missing missing 0.0], labels)
    extra = [(coordinate=labels[1], origin=7, known_at=8),
             (coordinate=labels[2], origin=6, known_at=8),
             (coordinate="other", unknown_metadata=1)]
    available_now = [event(5, labels[1], 7, 0.0), event(5, labels[2], 7, 1.0)]
    baseline = fit(vcat(training, available_now), 7, labels; score_pairs=[(3, 2), (2, 1)])
    fit_with_future = fit(reverse(vcat(training, extra, available_now)), 7, labels;
                          score_pairs=[(1, 2), (2, 3)])
    @test fit_with_future.diagnostics == baseline.diagnostics
    @test baseline.model.score_pairs == [(1, 2), (2, 3)]
    # Observed origins by ID are {1,2,5}, {1,2,3,4,5}, and {3,4,5}.
    @test baseline.diagnostics.overlap == [3 3 1; 3 5 3; 1 3 3]
    @test baseline.diagnostics.side_counts.ties == [1, 0, 0]
    @test baseline.diagnostics.side_counts.lower == [2, 2, 2]
    @test baseline.diagnostics.side_counts.upper == [1, 3, 1]
    @test baseline.diagnostics.prior_only_pairs == 1
    # Origin 5 contributes endpoints on all three IDs, A/2 only once.
    @test baseline.diagnostics.clipped_pits == 3
    unpaired = fit(training, 7, labels; score_pairs=[(1, 2), (2, 3)])
    @test unpaired.diagnostics.clipped_pits == 0  # B/1's endpoint has no partner at origin 5.
    @test baseline.model.dispersion[1, 3] == 0
    @test_throws ArgumentError fit(training, 7, labels; score_pairs=[(1, 3)])
    @test_throws ArgumentError fit(training, 7, [labels; "cold"])

    later = observations([.2 .15 missing; .22 .31 missing; missing .67 .83], labels; start=7)
    score = score_copula(baseline.model, later, 10)
    one = score_copula(baseline.model, later[1:4], 10)
    other = score_copula(baseline.model, later[5:end], 10)
    @test score.coverage == [2, 1]
    @test one.coverage == [2, 0]
    @test other.coverage == [0, 1]
    @test score.selected_score ≈ (2 * one.selected_score + other.selected_score) / 3
    @test score.gaussian_score ≈ (2 * one.gaussian_score + other.gaussian_score) / 3

    # Reorder coordinates while preserving the intended edges.
    order = [2, 3, 1]
    reordered = fit(vcat(training, available_now), 7, labels[order];
                    score_pairs=[(1, 2), (1, 3)])
    @test reordered.model.dispersion ≈ baseline.model.dispersion[order, order]
    @test reordered.diagnostics.overlap == baseline.diagnostics.overlap[order, order]
    labels[1] = "mutated input label"
    @test baseline.model.coordinates[1] == "A/1"
end

@testset "Indefinite pair moments are repaired" begin
    labels = ["A", "B", "C"]
    pits = [.2 .2 missing; .8 .8 missing; .2 missing .2; .8 missing .8;
            missing .2 .8; missing .8 .2]
    fitted = fit(observations(pits, labels), 8, labels;
                 shrinkage=0, eigen_floor=.2, score_pairs=[(1, 2), (1, 3), (2, 3)])
    @test fitted.diagnostics.raw_eigenmin ≈ -1
    @test fitted.diagnostics.effective_shrinkage ≈ .6
    @test fitted.model.dispersion ≈ [1 .4 .4; .4 1 -.4; .4 -.4 1]
    @test eigmin(Symmetric(fitted.model.dispersion)) ≈ .2
    @test fitted.model.factor * fitted.model.factor' ≈ fitted.model.dispersion
end

@testset "Malformed input and numerical envelope refuse" begin
    labels = ["A", "B"]
    rows = observations([.2 .3; .8 .1; .4 .7], labels)
    @test_throws ArgumentError fit([rows; first(rows)], 5, labels)
    @test_throws ArgumentError fit([rows; event(1, "A", 0, .5)], 5, labels)
    for value in (NaN, Inf, -.1, 1.1, true, missing, "0.9")
        @test_throws ArgumentError fit([rows; event(4, "A", 5, value)], 5, labels)
    end
    @test_throws ArgumentError fit(rows, true, labels)
    @test_throws ArgumentError fit(rows, 5, ["A"])
    @test_throws ArgumentError fit(rows, 5, ["A", "A"])
    for overrides in (
        (; score_pairs=[]), (; score_pairs=[(1, 2), (2, 1)]), (; score_pairs=[(1, 1)]),
        (; score_pairs=[(1, 3)]), (; score_pairs=[(true, 2)]), (; min_overlap=1),
        (; min_overlap=3.0), (; df_grid=[]), (; df_grid=[4, 4.0]), (; df_grid=[Inf]),
        (; df_grid=[nextfloat(0.0)]), (; df_grid=[nextfloat(200.0)]),
        (; epsilon=nextfloat(0.0)), (; epsilon=.5), (; shrinkage=NaN),
        (; shrinkage=1.1), (; eigen_floor=0), (; eigen_floor=1),
    )
        @test_throws ArgumentError fit(rows, 5, labels; overrides...)
    end
end

struct ScriptedRNG <: AbstractRNG
    normal::Float64
    radial::Float64
end

Random.randn!(rng::ScriptedRNG, out::AbstractArray{Float64}) = fill!(out, rng.normal)
Random.rand(rng::ScriptedRNG, ::Chisq) = rng.radial

@testset "Joint draws, seeded batches and inverse-map order" begin
    labels = ["A/1", "A/2"]
    tails = [.02 .03; .98 .97; .02 .97; .98 .03;
             .35 .40; .65 .60; .35 .60; .65 .40]
    mids = [.01 .49; .99 .51; .01 .51; .99 .49;
            .48 .02; .52 .98; .48 .98; .52 .02]
    student = fit(observations(tails, labels), 10, labels; df_grid=[4]).model
    gaussian = fit(observations(mids, labels), 10, labels; df_grid=[4]).model
    @test student.df == 4
    @test gaussian.df == Inf
    @test iszero(student.dispersion[1, 2])
    @test iszero(gaussian.dispersion[1, 2])
    pit = sample_copula(Xoshiro(827), student, 30000; batch_size=256)
    control = sample_copula(Xoshiro(827), gaussian, 30000; batch_size=256)
    @test size(pit) == (2, 30000)
    @test all(w -> 0 < w < 1, pit)
    @test all(abs.(sum(pit; dims=2) / size(pit, 2) .- .5) .< .012)
    @test pit == sample_copula(Xoshiro(827), student, 30000; batch_size=256)
    @test pit ≈ sample_copula(Xoshiro(827), student, 30000; batch_size=73) rtol=1e-13
    for tail in (w -> w < .02, w -> w > .98)
        coexceed(draws) = count(col -> all(tail, col), eachcol(draws))
        @test coexceed(pit) > 2 * coexceed(control)
    end

    # A unit latent normal and common chi-square draw of 2 produce
    # T4(sqrt(4/2)); the reference comes from SciPy's t(df=4).cdf(sqrt(2)).
    @test sample_copula(ScriptedRNG(1.0, 2.0), student, 1) ≈ fill(.8849001794597505, 2, 1)
    @test_throws ArgumentError sample_copula(ScriptedRNG(0.0, 0.0), student, 1)
    @test_throws ArgumentError sample_copula(ScriptedRNG(0.0, -1.0), student, 1)
    @test_throws ArgumentError sample_copula(ScriptedRNG(1e50, 2.0), student, 1)
    @test_throws ArgumentError sample_copula(ScriptedRNG(1e50, 2.0), gaussian, 1)
    @test_throws ArgumentError sample_copula(Xoshiro(1), student, 0)
    @test_throws ArgumentError sample_copula(Xoshiro(1), student, 1; batch_size=true)

    inverse_correction = (w -> w^2, w -> sqrt(w))
    inverse_forecast = (u -> 90 + 20u, u -> 110 + 8u)
    small = view(pit, :, 1:3)
    prices = [inverse_forecast[j](inverse_correction[j](small[j, k]))
              for j in axes(small, 1), k in axes(small, 2)]
    @test prices[1, :] ≈ 90 .+ 20 .* small[1, :] .^ 2
    @test prices[2, :] ≈ 110 .+ 8 .* sqrt.(small[2, :])
end

# Synthetic timing for the public Julia fit, prospective score, sample and price composition.
const LOAD_START = time_ns()
using DskitCopulas
using Distributions: TDist, Chisq, cdf
using LinearAlgebra: BLAS
using Random: Xoshiro, rand, randn

const LOAD_SECONDS = (time_ns() - LOAD_START) / 1e9

function synthetic_records(rng, coordinates, first, origins)
    marginal = TDist(4)
    radial = Chisq(4)
    Row = @NamedTuple{origin::Int, coordinate::String, known_at::Int, pit::Float64}
    rows = Row[]
    sizehint!(rows, length(coordinates) * origins)
    for origin in first:first+origins-1
        shared = randn(rng)
        scale = sqrt(4 / rand(rng, radial))
        for (j, coordinate) in enumerate(coordinates)
            rand(rng) < 0.05 && continue
            loading = isodd(j) ? 0.55 : -0.35
            noise = sqrt(1 - loading^2) * randn(rng)
            w = cdf(marginal, scale * (loading * shared + noise))
            push!(rows, (origin=origin, coordinate=coordinate, known_at=origin+mod(j, 4)+1, pit=w))
        end
    end
    return rows
end

function measure(job, name; repeats=3)
    GC.gc()
    first_call = @timed job()
    println(name, " first_seconds=", first_call.time, " first_allocated_bytes=", first_call.bytes,
            " compile_seconds=", first_call.compile_time)
    warm_seconds = Float64[]
    warm_bytes = Int[]
    for _ in 1:repeats
        GC.gc()
        trial = @timed job()
        push!(warm_seconds, trial.time)
        push!(warm_bytes, trial.bytes)
    end
    println(name, " warm_seconds=", warm_seconds, " warm_allocated_bytes=", warm_bytes)
    return first_call.value
end

function benchmark(origins, dimensions, later_origins, scenarios)
    println("\ncase origins=", origins, " coordinates=", dimensions, " later_origins=", later_origins,
            " scenarios=", scenarios, " batch_size=256")
    ids = ["cell-$j" for j in 1:dimensions]
    rng = Xoshiro(3089)
    rows = synthetic_records(rng, ids, 1, origins)
    fit_cutoff = origins + 5
    later = synthetic_records(rng, ids, fit_cutoff, later_origins)
    score_edges = [(j, j+1) for j in 1:min(64, dimensions-1)]
    println("train_records=", length(rows), " score_records=", length(later),
            " fit_pairs=", dimensions * (dimensions-1) ÷ 2, " score_edges=", length(score_edges),
            " finite_df_grid=[2,4,8,16,32]")
    fitted = measure("fit") do
        fit_copula(rows, fit_cutoff, ids; score_pairs=score_edges, min_overlap=10,
                   shrinkage=.2, eigen_floor=.025, df_grid=[2, 4, 8, 16, 32], epsilon=1e-7)
    end
    model = fitted.model
    result = measure("prospective_score") do
        score_copula(model, later, fit_cutoff + later_origins + 4)
    end
    uniforms = measure("draw") do
        sample_copula(Xoshiro(1867), model, scenarios; batch_size=256)
    end
    inverse_correction = [w -> w^2 for _ in ids]
    inverse_forecast = [u -> base + 20u for base in range(90.0, 100.0; length=dimensions)]
    prices = measure("price_inverse") do
        [inverse_forecast[j](inverse_correction[j](uniforms[j, k]))
         for j in axes(uniforms, 1), k in axes(uniforms, 2)]
    end
    @assert model.coordinates == ids && all(isfinite, prices)
    println("selected_df=", model.df, " train_terms=", fitted.diagnostics.terms,
            " later_terms=", result.terms, " effective_shrinkage=", fitted.diagnostics.effective_shrinkage,
            " median_ties=", sum(fitted.diagnostics.side_counts.ties),
            " prior_only_pairs=", fitted.diagnostics.prior_only_pairs)
end

function benchmark_gaussian()
    ids = ["one", "two"]
    rows = [
        (origin=i, coordinate=ids[j], known_at=i+1, pit=w)
        for (i, pair) in enumerate(((.01, .49), (.99, .51), (.01, .51), (.99, .49),
                                    (.48, .02), (.52, .98), (.48, .98), (.52, .02)))
        for (j, w) in enumerate(pair)
    ]
    model = fit_copula(rows, 10, ids; score_pairs=[(1, 2)], min_overlap=2, shrinkage=.2,
                       eigen_floor=.025, df_grid=[4], epsilon=1e-7).model
    @assert model.df == Inf
    println("\nGaussian draw control coordinates=2 scenarios=2048 batch_size=256")
    measure("gaussian_draw") do
        sample_copula(Xoshiro(1867), model, 2048; batch_size=256)
    end
end

println("Julia=", VERSION, " Distributions=", Base.pkgversion(parentmodule(TDist)),
        " julia_threads=", Threads.nthreads(), " blas_threads=", BLAS.get_num_threads(),
        " blas=", BLAS.get_config(), " package_load_seconds=", LOAD_SECONDS)
initial = @timed benchmark(200, 8, 60, 1024)
println("initial_case_total_seconds=", initial.time, " compile_seconds=", initial.compile_time,
        " includes_synthetic_input_setup_and_small_warm_repeats=true")
benchmark(1070, 1328, 240, 2048)
benchmark_gaussian()
println("process_peak_rss_bytes=", Sys.maxrss())

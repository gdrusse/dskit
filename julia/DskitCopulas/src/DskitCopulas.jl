"""
Causal fitting, prospective scoring and simulation of elliptical copulas for
already calibrated probability integral transforms (PITs). The caller supplies
validated issuance and settlement provenance and owns the marginal forecasts.
"""
module DskitCopulas

using Dates: Date, DateTime
using Distributions: Chisq, Normal, TDist, cdf, logpdf, params, quantile
using LinearAlgebra: LowerTriangular, Symmetric, cholesky, eigmin, isposdef, lmul!
using Random: AbstractRNG, rand, randn!

export fit_copula, score_copula, sample_copula

const MIN_DF = 1.0
const MAX_DF = 200.0
const LOG_2PI = log(2π)
const _Clock = Union{Integer,Date,DateTime}

struct _CopulaModel{T<:_Clock}
    cutoff::T
    coordinates::Vector{String}
    dispersion::Matrix{Float64}
    factor::LowerTriangular{Float64,Matrix{Float64}}
    df::Float64
    candidates::Vector{Float64}
    score_pairs::Vector{Tuple{Int,Int}}
    epsilon::Float64
end

function _finite_float(value, name)
    value isa Real && !(value isa Bool) || throw(ArgumentError("$name must be real"))
    number = Float64(value)
    isfinite(number) || throw(ArgumentError("$name must be finite"))
    return number
end

function _integer(value, minimum, name)
    value isa Integer && !(value isa Bool) && minimum <= value <= typemax(Int) ||
        throw(ArgumentError("$name must be an integer of at least $minimum"))
    return Int(value)
end

function _coordinates(ids)
    labels = String[]
    for id in ids
        id isa AbstractString && !isempty(strip(id)) ||
            throw(ArgumentError("coordinate IDs must be nonblank strings"))
        push!(labels, id)
    end
    length(labels) >= 2 && allunique(labels) ||
        throw(ArgumentError("at least two distinct coordinate IDs are required"))
    return labels
end

function _pairs(edges, p)
    pairs = Tuple{Int,Int}[]
    for edge in edges
        edge isa Tuple && length(edge) == 2 ||
            throw(ArgumentError("score pairs must be two-element index tuples"))
        first = _integer(edge[1], 1, "score pair index")
        second = _integer(edge[2], 1, "score pair index")
        first <= p && second <= p && first != second ||
            throw(ArgumentError("invalid score pair $edge for $p coordinates"))
        push!(pairs, minmax(first, second))
    end
    !isempty(pairs) && allunique(pairs) ||
        throw(ArgumentError("declare distinct unordered scoring pairs"))
    return sort!(pairs)
end

function _candidates(grid)
    finite = [_finite_float(df, "degrees of freedom") for df in grid]
    !isempty(finite) && allunique(finite) && all(df -> MIN_DF <= df <= MAX_DF, finite) ||
        throw(ArgumentError("declare distinct finite df values in [$MIN_DF, $MAX_DF]"))
    return sort!(push!(finite, Inf); rev=true)
end

function _checked_clock(value)
    value isa _Clock && !(value isa Bool) ||
        throw(ArgumentError("cutoff must be an integer, Date or DateTime"))
    return value
end

function _panel(records, cutoff::T, labels, first_origin=nothing) where {T}
    columns = Dict(label => j for (j, label) in enumerate(labels))
    origins = Dict{T,Int}()
    entries = Tuple{T,Int,Float64}[]
    for record in records
        hasproperty(record, :coordinate) && record.coordinate isa AbstractString ||
            throw(ArgumentError("each record requires a string coordinate"))
        j = get(columns, record.coordinate, 0)
        j == 0 && continue
        hasproperty(record, :origin) && hasproperty(record, :known_at) ||
            throw(ArgumentError("each selected coordinate requires origin and known_at"))
        a, known_at = record.origin, record.known_at
        a isa T && known_at isa T && !(a isa Bool) && !(known_at isa Bool) && a <= known_at ||
            throw(ArgumentError("inconsistent clocks for $(labels[j]): $a and $known_at"))
        a < cutoff && known_at <= cutoff && (isnothing(first_origin) || a >= first_origin) ||
            continue
        hasproperty(record, :pit) || throw(ArgumentError("missing PIT for $(labels[j]) at $a"))
        pit = record.pit
        pit isa Real && !(pit isa Bool) && 0 <= pit <= 1 ||
            throw(ArgumentError("PIT for $(labels[j]) at $a must be a real number in [0, 1]"))
        origins[a] = 0
        push!(entries, (a, j, Float64(pit)))
    end

    for (i, a) in enumerate(sort!(collect(keys(origins))))
        origins[a] = i
    end
    pits = fill(NaN, length(origins), length(labels))
    for (a, j, pit) in entries
        i = origins[a]
        isnan(pits[i, j]) || throw(ArgumentError("duplicate origin $a for $(labels[j])"))
        pits[i, j] = pit
    end
    return pits
end

function _moments(pits)
    observed = zeros(size(pits))
    signed = zeros(size(pits))
    upper = zeros(Int, size(pits, 2))
    ties = zeros(Int, size(pits, 2))
    for j in axes(pits, 2), i in axes(pits, 1)
        w = pits[i, j]
        isnan(w) && continue
        above = w > 0.5
        observed[i, j] = 1.0
        signed[i, j] = above ? 1.0 : -1.0
        upper[j] += above
        ties[j] += w == 0.5
    end
    counts = round.(Int, transpose(observed) * observed)
    lower = [counts[j, j] - upper[j] for j in axes(counts, 1)]
    return counts, transpose(signed) * signed, (lower=lower, upper=upper, ties=ties)
end

function _dispersion(counts, concordance, labels, pairs, minimum, shrinkage, floor)
    p = length(labels)
    for j in 1:p
        count = counts[j, j]
        count >= minimum ||
            throw(ArgumentError("coordinate $(labels[j]) has $count origins, need $minimum"))
    end
    for (j, k) in pairs
        count = counts[j, k]
        count >= minimum ||
            throw(ArgumentError("score pair $(labels[j]), $(labels[k]) has $count origins, need $minimum"))
    end

    raw = zeros(p, p)
    prior_only_pairs = 0
    for k in 1:p
        raw[k, k] = 1.0
        for j in 1:k-1
            count = counts[j, k]
            if count < minimum
                prior_only_pairs += 1
            else
                raw[j, k] = raw[k, j] = sinpi(concordance[j, k] / (2.0 * count))
            end
        end
    end
    raw_eigenmin = eigmin(Symmetric(raw))
    isfinite(raw_eigenmin) || throw(ArgumentError("the raw dispersion eigenvalue is nonfinite"))
    repair = raw_eigenmin < floor ? (floor - raw_eigenmin) / (1.0 - raw_eigenmin) : 0.0
    effective = max(shrinkage, repair)
    matrix = (1.0 - effective) .* raw
    for j in 1:p
        matrix[j, j] = 1.0
    end
    decomposition = cholesky(Symmetric(matrix, :L); check=false)
    isposdef(decomposition) ||
        throw(ArgumentError("dispersion is not numerically positive definite at shrinkage $effective"))
    diagnostics = (; raw_eigenmin, effective_shrinkage=effective, prior_only_pairs)
    return matrix, decomposition.L, diagnostics
end

function _score_mask(pits, pairs, epsilon)
    used = falses(size(pits))
    coverage = zeros(Int, length(pairs))
    for (edge, (j, k)) in enumerate(pairs), i in axes(pits, 1)
        if !isnan(pits[i, j]) && !isnan(pits[i, k])
            used[i, j] = used[i, k] = true
            coverage[edge] += 1
        end
    end
    terms = sum(coverage)
    terms > 0 || throw(ArgumentError("no eligible pair-origin terms"))
    clipped_pits = count(index -> used[index] && !(epsilon <= pits[index] <= 1.0 - epsilon),
                         eachindex(pits))
    return used, coverage, terms, clipped_pits
end

_distribution(df) = isinf(df) ? Normal() : TDist(df)
_log_kernel(::Normal, q) = 0.5 * q
_log_kernel(dist::TDist, q) = ((only(params(dist)) + 2.0) * 0.5) * log1p(q / only(params(dist)))

function _candidate_score(pits, used, pairs, matrix, dist, epsilon, terms, df)
    quantiles = zeros(size(pits))
    marginal = zeros(size(pits))
    for index in eachindex(pits)
        used[index] || continue
        z = quantile(dist, clamp(pits[index], epsilon, 1.0 - epsilon))
        density = logpdf(dist, z)
        isfinite(z) && isfinite(density) ||
            throw(ArgumentError("nonfinite marginal transform for df $df"))
        quantiles[index] = z
        marginal[index] = density
    end

    total = 0.0
    for (j, k) in pairs
        r = matrix[j, k]
        determinant = (1.0 - r) * (1.0 + r)
        constant = -LOG_2PI - 0.5 * log(determinant)
        for i in axes(pits, 1)
            if !isnan(pits[i, j]) && !isnan(pits[i, k])
                x, y = quantiles[i, j], quantiles[i, k]
                q = (x - r * y)^2 / determinant + y^2
                term = constant - _log_kernel(dist, q) - marginal[i, j] - marginal[i, k]
                isfinite(term) || throw(ArgumentError("nonfinite copula log density for df $df"))
                total += term
            end
        end
    end
    isfinite(total) || throw(ArgumentError("nonfinite composite score for df $df"))
    return total / terms
end

function _score_panel(pits, pairs, matrix, candidates, epsilon)
    used, coverage, terms, clipped_pits = _score_mask(pits, pairs, epsilon)
    candidate_scores = [(df=df, score=_candidate_score(pits, used, pairs, matrix, _distribution(df),
                                                       epsilon, terms, df)) for df in candidates]
    return (; candidate_scores, coverage, terms, clipped_pits, independence_score=0.0)
end

"""
    fit_copula(records, cutoff, coordinates; score_pairs, min_overlap,
               shrinkage, eigen_floor, df_grid, epsilon)

Fit a latent dispersion by fixed-median PIT sign concordance and choose degrees
of freedom by pooled bivariate log copula score. Each record supplies
`(origin, coordinate, known_at, pit)`. Clocks use the cutoff's concrete integer,
`Date`, or `DateTime` type. Only selected coordinates with
`origin < cutoff` and `known_at <= cutoff` are read as PITs. Missing outcomes are
absent records. Use the PIT computed with the correction map issued at its origin.

Supply distinct string coordinate IDs in result row order. `score_pairs` contains
distinct unordered, one-based index tuples. Require `min_overlap >= 2` for each
coordinate and scoring pair. Other unsupported pairs have zero latent dispersion
before shrinkage. Exact median values count on the lower side. `shrinkage` is in
[0, 1] and `eigen_floor` in (0, 1). Declare a distinct finite `df_grid` in
[1, 200]. The exact Gaussian (df Inf) also competes. Ties favor higher df.
`epsilon` is in (0, 0.5) and must leave `1-epsilon < 1` in Float64. It clips
the PITs used for numerical density evaluation only.

Return `(; model, diagnostics)`. The fitted state exposes its `cutoff`,
`coordinates`, `dispersion`, `factor`, `df` and scoring settings for inspection.
Treat the state and its arrays as read-only. Diagnostics report fit coverage, side
counts, repair, all candidate scores, their common term count and clipping count.
Malformed input or numerical failure raises `ArgumentError`.
"""
function fit_copula(records, cutoff, coordinates; score_pairs, min_overlap, shrinkage,
                    eigen_floor, df_grid, epsilon)
    _checked_clock(cutoff)
    labels = _coordinates(coordinates)
    pairs = _pairs(score_pairs, length(labels))
    candidates = _candidates(df_grid)
    minimum = _integer(min_overlap, 2, "min_overlap")
    s = _finite_float(shrinkage, "shrinkage")
    floor = _finite_float(eigen_floor, "eigen_floor")
    eps = _finite_float(epsilon, "epsilon")
    0 <= s <= 1 || throw(ArgumentError("shrinkage must lie in [0, 1]"))
    0 < floor < 1 || throw(ArgumentError("eigen_floor must lie in (0, 1)"))
    0 < eps < 0.5 && 1.0 - eps < 1.0 ||
        throw(ArgumentError("epsilon must be in (0, 0.5) and leave a strict Float64 upper bound"))

    pits = _panel(records, cutoff, labels)
    counts, concordance, side_counts = _moments(pits)
    matrix, factor, repair = _dispersion(counts, concordance, labels, pairs, minimum, s, floor)
    scores = _score_panel(pits, pairs, matrix, candidates, eps)
    selected = argmax(candidate -> candidate.score, scores.candidate_scores)
    model = _CopulaModel(cutoff, labels, matrix, factor, selected.df, candidates, pairs, eps)
    diagnostics = (; overlap=counts, side_counts, min_overlap=minimum, shrinkage=s,
                   eigen_floor=floor, repair..., scores...)
    return (; model, diagnostics)
end

"""
    score_copula(model, records, cutoff)

Score a frozen fit on forecasts with `model.cutoff <= origin < cutoff` and
`known_at <= cutoff`. Use the fitted coordinate order, scoring pairs, numerical
interior bound and dispersion. Return the selected-family, Gaussian and
independence scores with all training candidate df scores, counts for each edge
(including zeros), total pair-origin terms and the number of clipped unique
PITs. All candidate scores share the same terms and normalization.
An incompatible cutoff or empty eligible panel raises `ArgumentError`.
"""
function score_copula(model::_CopulaModel, records, cutoff)
    _checked_clock(cutoff)
    typeof(cutoff) === typeof(model.cutoff) && cutoff > model.cutoff ||
        throw(ArgumentError("scoring cutoff must use the fit clock and follow the fit cutoff"))
    pits = _panel(records, cutoff, model.coordinates, model.cutoff)
    scores = _score_panel(pits, model.score_pairs, model.dispersion, model.candidates, model.epsilon)
    return (; scores..., selected_df=model.df,
            selected_score=only(row.score for row in scores.candidate_scores if row.df == model.df),
            gaussian_score=first(scores.candidate_scores).score)
end

function _draw_block!(rng, draws, model, distribution, radial, scales)
    for j in axes(draws, 2)
        randn!(rng, view(draws, :, j))
        if radial !== nothing
            q = rand(rng, radial)
            isfinite(q) && q > 0 || throw(ArgumentError("unrepresentable radial draw for df $(model.df)"))
            scale = sqrt(model.df / q)
            isfinite(scale) && scale > 0 || throw(ArgumentError("unrepresentable radial scale for df $(model.df)"))
            scales[j] = scale
        end
    end
    lmul!(model.factor, draws)
    for j in axes(draws, 2), i in axes(draws, 1)
        latent = draws[i, j] * scales[j]
        isfinite(latent) || throw(ArgumentError("nonfinite scenario latent value"))
        w = cdf(distribution, latent)
        0.0 < w < 1.0 || throw(ArgumentError("scenario uniform is outside the representable interior"))
        draws[i, j] = w
    end
    return draws
end

"""
    sample_copula(rng, model, scenarios; batch_size=256)

Return a Float64 matrix with one row per fitted coordinate and one column per
scenario. For each column, draw its normal vector first, then a single
chi-square value shared across its rows for finite df. The Gaussian uses the
normal vector alone. A batch uses the cached Cholesky factor. The caller owns
the RNG. Repeatability assumes the same Julia and dependency versions and
numerical settings. Unrepresentable radial values or uniforms raise
`ArgumentError` before a result is returned.
"""
function sample_copula(rng::AbstractRNG, model::_CopulaModel, scenarios; batch_size=256)
    m = _integer(scenarios, 1, "scenarios")
    batch = min(m, _integer(batch_size, 1, "batch_size"))
    result = Matrix{Float64}(undef, length(model.coordinates), m)
    distribution = _distribution(model.df)
    radial = isinf(model.df) ? nothing : Chisq(model.df)
    scales = ones(batch)
    for first in 1:batch:m
        last = first + min(batch - 1, m - first)
        _draw_block!(rng, view(result, :, first:last), model, distribution, radial, scales)
    end
    return result
end

end # module

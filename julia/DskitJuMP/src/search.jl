# The search seam (ADR-0328 A6): a second pluggable type, `Search`, registered by name
# like `Program`. A search says only which variables to free each round (`neighbourhood`),
# whether a round's point replaces the incumbent (`accept`) and when it has finished
# (`done`). The base loop owns everything else, inside the worker against the live model:
# the first solve or the standing incumbent, fixing the rest at the incumbent, the solve
# under the round limits, freeing, the overall limits, the seeded draw, the trajectory,
# the search record, and restoring the caller's fixes and starts. A round therefore costs
# bound changes, start values and a solve; never a rebuild or a round trip.

"""
    Search

The abstract type of a neighbourhood search the worker can run on a live model.

A concrete `S <: Search` is a fresh object per request carrying its params and its own
counters. Required hooks:

- `search_name(::Type{S})::String`: the registry name.
- `S(raw::Dict{String,Any})`: the search from the request's `params`, or
  `Refusal("bad_request", ...)` when they do not fit.
- `neighbourhood(s, state::SearchState)::Vector{Int}`: the flat variable positions to
  free this round, drawn from `state.candidates` (the variables the caller did not fix),
  randomness only through `draw!(state, k)`.
- `accept(s, state, candidate::Candidate)::Bool`: whether the round's point becomes
  the incumbent (called only for a round that found a point).
- `done(s, state)::Bool`: whether the search has finished (checked before each round).

# Examples

    mutable struct Greedy <: Search
        free::Int
    end
    DskitJuMP.search_name(::Type{Greedy}) = "greedy"
    Greedy(raw::Dict{String,Any}) = Greedy(raw["free"])
    DskitJuMP.neighbourhood(s::Greedy, st) = draw!(st, s.free)
    DskitJuMP.accept(::Greedy, st, c) = improves(st, c.objective)
    DskitJuMP.done(::Greedy, st) = length(st.trajectory) >= 10
    register_search!(Greedy)
"""
abstract type Search end

"""The registry name of search type `S`."""
function search_name end

"""The flat variable positions search `s` frees this round."""
function neighbourhood end

"""Whether search `s` makes the round's `candidate` the incumbent."""
function accept end

"""Whether search `s` has finished."""
function done end

"""The searches this worker knows, by registry name."""
const SEARCHES = Dict{String,Type{<:Search}}()

"""
    register_search!(S)

Register search type `S` under `search_name(S)` (replacing one of the same name).
"""
function register_search!(S::Type{<:Search})
    SEARCHES[search_name(S)] = S
    return S
end

"""The registered search type named `name`, or the refusal `unknown_search` naming it."""
function lookup_search(name::String)
    S = get(SEARCHES, name, nothing)
    S === nothing && refuse("unknown_search", "no search named $(repr(name))")
    return S
end

# --- the seeded draw (the wire spec's SplitMix64 and partial Fisher-Yates) -------------

"""SplitMix64's increment and its two multipliers (the wire spec's constants)."""
const SPLITMIX_GAMMA = 0x9e3779b97f4a7c15
const SPLITMIX_MIX1 = 0xbf58476d1ce4e5b9
const SPLITMIX_MIX2 = 0x94d049bb133111eb

"""
    SplitMix64(seed)

The search's one generator, state = `seed`; `next!` gives the next 64-bit output.

# Examples

    g = SplitMix64(0)
    next!(g)  # 0xe220a8397b1dcdaf
"""
mutable struct SplitMix64
    state::UInt64
end

"""The next output of `g` (UInt64 arithmetic wraps mod 2^64, as the spec writes it)."""
function next!(g::SplitMix64)
    g.state += SPLITMIX_GAMMA
    z = g.state
    z = (z ⊻ (z >> 30)) * SPLITMIX_MIX1
    z = (z ⊻ (z >> 27)) * SPLITMIX_MIX2
    return z ⊻ (z >> 31)
end

"""
    draw!(g, candidates, k)

`k` of `candidates` by a partial Fisher-Yates over a copy: for j = 0 .. k-1, swap
`a[j]` with `a[j + (output mod (n - j))]`, one output per j; returns `a[0..k-1]`.
"""
function draw!(g::SplitMix64, candidates::Vector{Int}, k::Int)
    a = copy(candidates)
    n = length(a)
    for j in 0:k-1
        t = j + Int(next!(g) % UInt64(n - j))
        a[j+1], a[t+1] = a[t+1], a[j+1]
    end
    return a[1:k]
end

# --- the loop's state -----------------------------------------------------------------

"""
    Candidate(objective, values, record)

One round's result: the objective of its point, the point by flat variable position,
and the round's solve record.
"""
struct Candidate
    objective::Float64
    values::Vector{Float64}
    record::Dict{String,Any}
end

"""
    SearchState(rng, candidates, sense, objective, incumbent, trajectory)

What a search may read: the seeded generator, the positions it may free (the variables
the caller did not fix, in the spec's ascending key order), the objective sense, the
incumbent's objective and point, and the trajectory so far.
"""
mutable struct SearchState
    rng::SplitMix64
    candidates::Vector{Int}
    sense::MOI.OptimizationSense
    objective::Float64
    incumbent::Vector{Float64}
    trajectory::Vector{Dict{String,Any}}
end

"""`k` of the state's candidates (all when fewer remain) by the spec's seeded draw."""
draw!(st::SearchState, k::Int) = draw!(st.rng, st.candidates, min(k, length(st.candidates)))

"""A strict improvement must beat the incumbent by more than this, relative to max(1, |incumbent|)."""
const IMPROVEMENT_TOL = 1e-9

"""Whether `objective` is strictly better than the incumbent's under the model's sense."""
function improves(st::SearchState, objective::Float64)
    margin = IMPROVEMENT_TOL * max(1.0, abs(st.objective))
    st.sense == MOI.MIN_SENSE && return objective < st.objective - margin
    return objective > st.objective + margin
end

# --- the reference search --------------------------------------------------------------

"""
    FixAndReoptimise(free, stall)

The reference search, params exactly `{free, stall}` (integers >= 1): each round frees
`free` variables drawn from those the caller did not fix (all when fewer remain) and
accepts a strictly better objective; done after `stall` rounds in a row without one.

# Examples

    s = FixAndReoptimise(Dict{String,Any}("free" => 2, "stall" => 3))
"""
mutable struct FixAndReoptimise <: Search
    free::Int
    stall::Int
    misses::Int
end

"""The params `fix_and_reoptimise` takes, sorted."""
const FIX_AND_REOPTIMISE_PARAMS = ["free", "stall"]

search_name(::Type{FixAndReoptimise}) = "fix_and_reoptimise"

function FixAndReoptimise(raw::Dict{String,Any})
    ok = sort!(collect(keys(raw))) == FIX_AND_REOPTIMISE_PARAMS &&
         all(v -> v isa Int && v >= 1, values(raw))
    ok || refuse("bad_request", "fix_and_reoptimise takes exactly {free, stall}, integers >= 1")
    return FixAndReoptimise(raw["free"], raw["stall"], 0)
end

neighbourhood(s::FixAndReoptimise, st::SearchState) = draw!(st, s.free)

function accept(s::FixAndReoptimise, st::SearchState, c::Candidate)
    better = improves(st, c.objective)
    s.misses = better ? 0 : s.misses + 1
    return better
end

done(s::FixAndReoptimise, ::SearchState) = s.misses >= s.stall

# --- the request ------------------------------------------------------------------------

"""The overall limits a search takes."""
const SEARCH_LIMIT_KEYS = ("time_limit", "rounds")

"""A seed is an integer in [0, SEED_BOUND) (2^53: exact in a float64)."""
const SEED_BOUND = 2^53

"""A search record's `termination`: the search's stop reason, never a solver's."""
const SEARCH_DONE = "SEARCH_DONE"
const SEARCH_ROUND_LIMIT = "SEARCH_ROUND_LIMIT"
const SEARCH_TIME_LIMIT = "SEARCH_TIME_LIMIT"
const SEARCH_ROUND_NO_POINT = "SEARCH_ROUND_NO_POINT"
const SEARCH_NO_INCUMBENT = "SEARCH_NO_INCUMBENT"

"""
    SearchLimits(time_limit, rounds)

The overall limits of one search; `nothing` for a limit not given.
"""
struct SearchLimits
    time_limit::Union{Nothing,Float64}
    rounds::Union{Nothing,Int}
end

"""Parse a search's overall `limits`; `bad_request` for a wrong key, type or range."""
function parse_search_limits(obj)
    ok = obj isa Dict{String,Any} && all(k -> k in SEARCH_LIMIT_KEYS, keys(obj))
    ok || refuse("bad_request", "limits must be an object over $(collect(SEARCH_LIMIT_KEYS))")
    t = limit_number(get(obj, "time_limit", 1.0))
    r = get(obj, "rounds", 1)
    (t !== nothing && t > 0 && r isa Int && r >= 1) ||
        refuse("bad_request", "limits must be an object over $(collect(SEARCH_LIMIT_KEYS))")
    return SearchLimits(haskey(obj, "time_limit") ? t : nothing,
                        haskey(obj, "rounds") ? r : nothing)
end

"""The `search` reply body: run a registered search on the live model in place."""
function op_search(pool::Pool, req::Dict{String,Any})
    handle = req_name(req, "handle")
    lm = held_model(pool, handle)
    S = lookup_search(req_name(req, "search"))
    raw = req["params"]
    raw isa Dict{String,Any} || refuse("bad_request", "params must be an object")
    search = S(raw)
    seed = req["seed"]
    (seed isa Int && 0 <= seed < SEED_BOUND) ||
        refuse("bad_request", "seed must be an integer in [0, $(SEED_BOUND))")
    limits = parse_search_limits(req["limits"])
    round_limits = parse_limits(req["round_limits"])
    check_limits(lm.model, round_limits)
    out = run_search!(lm, handle, search, UInt64(seed), limits, round_limits)
    touch!(pool, lm.serial)
    out["timings"] = timings(pool, lm, "reused"; update=out["update_seconds"],
                             solve=out["solve_seconds"], accepted=out["accepted"])
    for k in ("update_seconds", "solve_seconds", "accepted")
        delete!(out, k)
    end
    return out
end

# --- the base loop ------------------------------------------------------------------------

"""The variables a search may free: every position the caller has not fixed, in the spec's
ascending key order (groups by name bytes, then keys element by element)."""
function search_candidates(lm::LiveModel)
    order = Tuple{Vector{UInt8},Key,Int}[]
    for g in lm.index.groups, (i, key) in enumerate(g.keys)
        pos = g.offset + i
        haskey(lm.fixed, pos) || push!(order, (Vector{UInt8}(codeunits(g.spec.name)), key, pos))
    end
    sort!(order; lt=(a, b) -> key_less((a[1], a[2]), (b[1], b[2])))
    return Int[last(t) for t in order]
end

"""Ascending order of `(group bytes, key)`: strings by UTF-8 bytes, integers numerically."""
function key_less(a, b)
    a[1] == b[1] || return a[1] < b[1]
    for (x, y) in zip(a[2], b[2])
        x == y && continue
        return x isa String ? codeunits(x) < codeunits(y) : x < y
    end
    return false
end

"""
    search_solve!(lm, limits)

One solve of the live model under `limits` (the search's own: it never touches the last
solution). Returns `(values or nothing, record, seconds)`.
"""
function search_solve!(lm::LiveModel, limits::Limits)
    model = lm.model
    seconds = with_limits(() -> run_optimize!(model), model, limits)
    point = has_point(model)
    record = solve_record(model, lm.solver, seconds, record_rows(lm.program, lm.state, lm.setup, point))
    values = point ? Float64[point_value(v) for v in lm.index.all] : nothing
    return values, record, seconds
end

"""Set every variable's start value to `values` (or clear them all for `nothing`)."""
function set_all_starts!(lm::LiveModel, values::Union{Nothing,Vector{Float64}})
    for (pos, v) in enumerate(lm.index.all)
        set_start_value(v, values === nothing ? nothing : values[pos])
    end
    return
end

"""The point reader of `values` (by flat position) for a program's `solution`, or `nothing`."""
function at_values(lm::LiveModel, values::Union{Nothing,Vector{Float64}})
    values === nothing && return nothing
    position = Dict{VariableRef,Int}(v => i for (i, v) in enumerate(lm.index.all))
    return v -> values[position[v]]
end

"""
    point_objective(record)

The objective of a solve that found a point, or the refusal `bad_data`: a search compares
objectives, and the solver reports none finite when a coefficient is at its infinity.
"""
function point_objective(record::Dict{String,Any})
    objective = record["objective"]
    objective === nothing &&
        refuse("bad_data", "the solver reports no finite objective at the point it found " *
                           "(a coefficient at or above the solver's infinity): a search " *
                           "cannot compare it")
    return objective::Float64
end

"""The search record's gap: |objective - bound| / |objective|, null when unknown."""
function search_gap(objective, bound)
    (objective === nothing || bound === nothing) && return nothing
    objective == bound && return 0.0
    objective == 0 && return nothing
    return abs(objective - bound) / abs(objective)
end

"""
    run_search!(lm, handle, search, seed, limits, round_limits)

The base loop. The last solution stands as the incumbent only when this handle found it
under the model's current state with no start values in effect; otherwise the first
solve runs on the whole model (the caller's fixes and the start values in effect, under
`round_limits`). Then, until `done`, the round limit, the time limit (checked in that
order before each round) or a round with no point: free the neighbourhood, fix the rest
at the incumbent, solve from the incumbent, free, `accept`. The caller's fixes and start
values are restored whatever happens; the incumbent becomes the last solution with the
search record. Returns the reply body with the seconds and the first solve's
`start_accepted` for the timings block.
"""
function run_search!(lm::LiveModel, handle::String, search::Search, seed::UInt64,
                     limits::SearchLimits, round_limits::Limits)
    began = time()
    solving = changing = 0.0
    accepted = nothing
    saved_start, saved_source = lm.start, lm.start_source
    kept = lm.last
    stands = kept !== nothing && lm.start === nothing && kept.handle == handle &&
             kept.version == lm.version
    local incumbent, found, bound
    trajectory = Dict{String,Any}[]
    stop = SEARCH_NO_INCUMBENT
    round_fixed = Int[]
    starts_moved = false
    try
        if stands
            incumbent, found = kept.values, kept.record
        else
            incumbent, found, seconds = search_solve!(lm, round_limits)
            solving += seconds
            lm.start_source === nothing || (accepted = start_accepted(lm.model))
        end
        bound = found["bound"]  # the whole model's, never a round's neighbourhood
        if incumbent !== nothing
            st = SearchState(SplitMix64(seed), search_candidates(lm), objective_sense(lm.model),
                             point_objective(found), incumbent, trajectory)
            while true
                done(search, st) && (stop = SEARCH_DONE; break)
                limits.rounds !== nothing && length(trajectory) >= limits.rounds &&
                    (stop = SEARCH_ROUND_LIMIT; break)
                limits.time_limit !== nothing && time() - began >= limits.time_limit &&
                    (stop = SEARCH_TIME_LIMIT; break)
                started = time()
                hood = Set(neighbourhood(search, st))
                round_fixed = Int[pos for pos in st.candidates if !(pos in hood)]
                apply_fixes!(lm, [pos => st.incumbent[pos] for pos in round_fixed])
                starts_moved = true
                set_all_starts!(lm, st.incumbent)
                changing += time() - started
                values, record, seconds = search_solve!(lm, round_limits)
                solving += seconds
                started = time()
                free_positions!(lm, round_fixed)
                empty!(round_fixed)
                changing += time() - started
                candidate = values === nothing ? nothing :
                            Candidate(point_objective(record), values, record)
                better = candidate !== nothing && accept(search, st, candidate)
                push!(trajectory, Dict{String,Any}(
                    "round" => length(trajectory) + 1, "objective" => record["objective"],
                    "bound" => record["bound"], "seconds" => seconds, "accepted" => better,
                    "neighbourhood" => length(hood)))
                candidate === nothing && (stop = SEARCH_ROUND_NO_POINT; break)
                if better
                    st.incumbent, st.objective, found = values, candidate.objective, record
                end
            end
            incumbent = st.incumbent
        end
    finally
        started = time()
        free_positions!(lm, round_fixed)
        if starts_moved
            set_all_starts!(lm, nothing)
            saved_start === nothing || apply_start!(lm, saved_start, saved_source)
        end
        changing += time() - started
    end
    objective = incumbent === nothing ? nothing : found["objective"]
    record = merge(found, Dict{String,Any}(
        "status" => incumbent === nothing ? "NO_SOLUTION" : "FEASIBLE_POINT",
        "termination" => stop, "objective" => objective, "bound" => bound,
        "gap" => search_gap(objective, bound), "seconds" => time() - began))
    if incumbent !== nothing
        lm.last = LastSolution(incumbent, record, handle, lm.version)
    end
    return Dict{String,Any}("handle" => handle,
                            "solution" => solution(lm.program, lm.state, lm.setup,
                                                   at_values(lm, incumbent)),
                            "record" => record, "trajectory" => trajectory,
                            "update_seconds" => changing, "solve_seconds" => solving,
                            "accepted" => accepted)
end

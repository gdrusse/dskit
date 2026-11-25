# The base lifecycle (ADR-0328 decision 1): optimizer resolution by name, option
# pass-through, per-solve limits, `optimize!` and the feasibility check. A program never
# calls `optimize!`; it only builds.

"""The solvers this environment provides, by wire name: the optimizer constructor."""
const SOLVERS = Dict{String,Any}("HiGHS" => HiGHS.Optimizer)

"""The solver a request names when it names the default (jump.py `DEFAULT_SOLVER`)."""
const DEFAULT_SOLVER = "HiGHS"

"""The per-solve limits a handle `solve` may carry, in wire order."""
const LIMIT_KEYS = ("time_limit", "node_limit", "mip_rel_gap", "solution_limit")

"""The optimizer constructor of `solver`, or the refusal `unknown_solver` naming it."""
function optimizer_of(solver::String)
    haskey(SOLVERS, solver) ||
        refuse("unknown_solver", "the environment provides no solver $(repr(solver))")
    return SOLVERS[solver]
end

"""Whether `v` is a solver option value the wire allows (a scalar)."""
option_value_ok(v) = v isa Bool || v isa String || v isa Int || v isa Float64

"""
    new_model(p, solver, options)

An empty, silent JuMP model for program `p` on `solver` with `options` set verbatim;
`direct_model` unless the program asks otherwise (see `direct_mode`), string names off.
An option the solver refuses is the refusal `bad_request` naming it.
"""
function new_model(p::Program, solver::String, options::Dict{String,Any})
    optimizer = optimizer_of(solver)
    model = direct_mode(p) ? direct_model(optimizer()) : Model(optimizer)
    set_string_names_on_creation(model, false)
    set_silent(model)
    for name in sort!(collect(keys(options)))
        value = options[name]
        option_value_ok(value) ||
            refuse("bad_request", "solver option $(repr(name)) must be a scalar")
        try
            set_attribute(model, name, value)
        catch err
            refuse("bad_request", "the solver refuses option $(repr(name)) = " *
                                  "$(repr(value)): $(first(sprint(showerror, err), 200))")
        end
    end
    return model
end

"""
    Limits(time_limit, node_limit, mip_rel_gap, solution_limit)

The per-solve limits of one handle `solve`; `nothing` for a limit not given.
"""
struct Limits
    time_limit::Union{Nothing,Float64}
    node_limit::Union{Nothing,Int}
    mip_rel_gap::Union{Nothing,Float64}
    solution_limit::Union{Nothing,Int}
end

"""A number for a limit: `Float64` from an `Int` or `Float64`, else `nothing`."""
limit_number(v) = v isa Float64 ? v : (v isa Int ? Float64(v) : nothing)

"""Parse a wire `limits` object; `bad_request` for a wrong key, type or range."""
function parse_limits(obj)
    obj isa Dict{String,Any} ||
        refuse("bad_request", "limits must be an object over $(collect(LIMIT_KEYS))")
    for k in keys(obj)
        k in LIMIT_KEYS ||
            refuse("bad_request", "limits must be an object over $(collect(LIMIT_KEYS))")
    end
    bad(k) = refuse("bad_request", "limit $(repr(k)) has the wrong type or range")
    t = get(obj, "time_limit", nothing)
    if t !== nothing
        t = limit_number(t)
        (t === nothing || !(t > 0)) && bad("time_limit")
    end
    g = get(obj, "mip_rel_gap", nothing)
    if g !== nothing
        g = limit_number(g)
        (g === nothing || !(g >= 0)) && bad("mip_rel_gap")
    end
    n = get(obj, "node_limit", nothing)
    (n === nothing || (n isa Int && n >= 1)) || bad("node_limit")
    s = get(obj, "solution_limit", nothing)
    (s === nothing || (s isa Int && s >= 1)) || bad("solution_limit")
    return Limits(t, n, g, s)
end

"""The MOI attribute and value of each limit that is set, in wire order."""
function limit_attributes(l::Limits)
    out = Pair{MOI.AbstractOptimizerAttribute,Any}[]
    l.time_limit === nothing || push!(out, MOI.TimeLimitSec() => l.time_limit)
    l.node_limit === nothing || push!(out, MOI.NodeLimit() => l.node_limit)
    l.mip_rel_gap === nothing || push!(out, MOI.RelativeGapTolerance() => l.mip_rel_gap)
    l.solution_limit === nothing || push!(out, MOI.SolutionLimit() => l.solution_limit)
    return out
end

"""
    with_limits(f, model, limits)

Run `f()` with `limits` set on `model`, then restore every one to what it was (the
model's `solver_options`). A limit the solver cannot apply is `bad_limits`, refused
before any is set.
"""
function with_limits(f::Function, model::Model, limits::Limits)
    attrs = limit_attributes(limits)
    for (attr, _) in attrs
        MOI.supports(backend(model), attr) ||
            refuse("bad_limits", "the solver cannot apply the limit $(nameof(typeof(attr)))")
    end
    saved = [attr => MOI.get(backend(model), attr) for (attr, _) in attrs]
    try
        for (attr, v) in attrs
            MOI.set(backend(model), attr, v)
        end
        return f()
    finally
        for (attr, v) in saved
            MOI.set(backend(model), attr, v)
        end
    end
end

"""
    guarded(f, what)

Run `f()` (a program's `build!` or `update!`); anything but a `Refusal` it raises, such
as the solver rejecting a coefficient it cannot take, is the refusal `bad_data`: the
data validated against the schema but the solver will not hold the model built from it.
"""
function guarded(f::Function, what::String)
    try
        return f()
    catch err
        err isa Refusal && rethrow()
        refuse("bad_data", "the solver refused the model this data gives ($what): " *
                           first(sprint(showerror, err), 300))
    end
end

"""
    run_optimize!(model)

`optimize!` the model and return the seconds it took; the optimizer raising is the
refusal `solve_failed`.
"""
function run_optimize!(model::Model)
    started = time()
    try
        optimize!(model)
    catch err
        refuse("solve_failed", "the optimizer raised: $(first(sprint(showerror, err), 300))")
    end
    return time() - started
end

"""
    start_accepted(model)

Whether the solver reported using the start values of the last solve: `nothing` when
it does not say. HiGHS.jl hands a start to `Highs_setSparseSolution` and exposes no
attribute reporting whether the solve used it, so for HiGHS this is always `nothing`,
never inferred (ADR-0328 A5).
"""
start_accepted(::Model) = nothing

"""
    one_shot_solve(p, cols, params, solver, options)

The v1 one-shot solve: build a private model, solve it, discard it; never the pool.
Returns `(solution, record)`.
"""
function one_shot_solve(p::Program, setup::Setup, solver::String, options::Dict{String,Any})
    model = new_model(p, solver, options)
    state = guarded(() -> build!(model, p, setup), "build")
    seconds = run_optimize!(model)
    point = has_point(model)
    rows = record_rows(p, state, setup, point)
    out = solution(p, state, setup, model, point), solve_record(model, solver, seconds, rows)
    MOI.empty!(backend(model))
    return out
end

"""The binding rows of every named constraint container of `p`, in record order."""
function record_rows(p::Program, state, setup::Setup, point::Bool)
    return Any[binding_row(name, refs, point) for (name, refs) in constraint_groups(p, state, setup)]
end

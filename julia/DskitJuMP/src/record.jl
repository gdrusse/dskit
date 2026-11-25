# The solve record (ADR-0328 decision 4): exactly the ten SolveRecord fields of the
# pyomo pack, MOI names for status and termination, every number finite or null, and
# one binding row per named constraint container.

"""The record's keys, in SolveRecord order (pinned against jump.py by the suite)."""
const RECORD_KEYS = ("solver", "status", "termination", "objective", "bound", "gap",
                     "seconds", "variables", "constraints", "binding")

"""The keys of one binding row."""
const BINDING_KEYS = ("name", "rows", "binding", "min_slack")

"""A row is binding when its slack's magnitude is at most this."""
const BINDING_TOL = 1e-9

"""`x` when it is a finite real, else `nothing` (JSON null)."""
finite_or_null(x::Real) = isfinite(x) ? Float64(x) : nothing
finite_or_null(::Any) = nothing

"""Whether the last `optimize!` of `model` left a primal point."""
has_point(model::Model) = primal_status(model) == MOI.FEASIBLE_POINT

"""
    point_value(v)

The value of variable `v` in the last solution, an integer variable's rounded to the
integer the solver's integrality tolerance stands for, so slacks and solutions read the
point the solver means.
"""
function point_value(v::VariableRef)
    x = value(v)
    return (is_binary(v) || is_integer(v)) ? round(x) : x
end

"""The slack of one row at the point: how far the row is from its bound, >= 0 when met."""
row_slack(f::AffExpr, s::MOI.LessThan{Float64}) = s.upper - value(point_value, f)
row_slack(f::AffExpr, s::MOI.GreaterThan{Float64}) = value(point_value, f) - s.lower
row_slack(f::AffExpr, s::MOI.EqualTo{Float64}) = s.value - value(point_value, f)

"""One binding row of the record for the constraint container `refs` named `name`."""
function binding_row(name::String, refs::AbstractVector{<:ConstraintRef}, point::Bool)
    count, least = 0, Inf
    if point
        for c in refs
            obj = constraint_object(c)
            s = row_slack(obj.func, obj.set)
            count += abs(s) <= BINDING_TOL
            least = min(least, s)
        end
    end
    return Dict{String,Any}("name" => name, "rows" => length(refs), "binding" => count,
                            "min_slack" => point ? finite_or_null(least) : nothing)
end

"""Terminations that prove the model has no point: a bound reported with one means nothing."""
const NO_POINT_PROVEN = (MOI.INFEASIBLE, MOI.INFEASIBLE_OR_UNBOUNDED, MOI.LOCALLY_INFEASIBLE)

"""
The record's `bound`: the solver's objective bound when it reports a finite one; null
after a solve that proved infeasibility (HiGHS reports 0 there, not a bound).
"""
function record_bound(model::Model)
    termination_status(model) in NO_POINT_PROVEN && return nothing
    try
        return finite_or_null(objective_bound(model))
    catch
        return nothing
    end
end

"""The record's `gap`: the solver's relative gap when it reports a finite one."""
function record_gap(model::Model)
    try
        return finite_or_null(relative_gap(model))
    catch
        return nothing
    end
end

"""
    solve_record(model, solver, seconds, rows)

The ten-field record of the last `optimize!` of `model`, `rows` the binding rows.
`objective` is null without a primal point; `variables` and `constraints` count the
model (variable bounds, integrality and fixes are not constraints).
"""
function solve_record(model::Model, solver::String, seconds::Float64, rows::Vector)
    point = has_point(model)
    return Dict{String,Any}(
        "solver" => solver,
        "status" => string(primal_status(model)),
        "termination" => string(termination_status(model)),
        "objective" => point ? finite_or_null(objective_value(model)) : nothing,
        "bound" => record_bound(model),
        "gap" => point ? record_gap(model) : nothing,
        "seconds" => seconds,
        "variables" => num_variables(model),
        "constraints" => num_constraints(model; count_variable_in_set_constraints=false),
        "binding" => rows,
    )
end

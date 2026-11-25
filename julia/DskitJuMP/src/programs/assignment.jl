# Reference program `assignment`: generalized assignment, the two-dimensional reference
# (wire spec, ADR-0328 amendment 1, "the reference programs"). Each item to at most one
# bin (`one_bin`), each bin's weight within its capacity (`capacity`); the optional
# component `max_per_bin` caps the items per bin. Maximise the value assigned.

"""The `assignment` program (see the file header)."""
struct Assignment <: Program end

const ASSIGNMENT_SCHEMA = Schema(
    [FieldSpec("items", "string", ["item"]), FieldSpec("bins", "string", ["bin"]),
     FieldSpec("value", "float64", ["item", "bin"]), FieldSpec("weight", "float64", ["item", "bin"]),
     FieldSpec("capacity", "float64", ["bin"])],
    ["item" => "items", "bin" => "bins"],
)

const ASSIGNMENT_GROUPS = [GroupSpec("x", ["item", "bin"], "binary")]

"""The optional component capping the items per bin."""
const MAX_PER_BIN = "max_per_bin"

"""The only param key `assignment` takes."""
const ASSIGNMENT_PARAMS = (MAX_PER_BIN,)

"""
    AssignmentData(items, bins, value, weight, capacity)

The typed data of `assignment`; `value` and `weight` are items x bins matrices.
"""
struct AssignmentData
    items::Vector{String}
    bins::Vector{String}
    value::Matrix{Float64}
    weight::Matrix{Float64}
    capacity::Vector{Float64}
end

"""
    AssignmentParams(max_per_bin)

The typed params: the per-bin item cap (`-1` when not given).
"""
struct AssignmentParams
    max_per_bin::Int
end

"""
    AssignmentState(x, one_bin, capacity, max_per_bin)

The live model's references: `x[item, bin]`, one `one_bin` row per item, one
`capacity` row per bin, one `max_per_bin` row per bin (empty when detached).
"""
mutable struct AssignmentState
    x::Matrix{VariableRef}
    one_bin::Vector{LessRef}
    capacity::Vector{LessRef}
    max_per_bin::Vector{LessRef}
end

program_name(::Assignment) = "assignment"
schema(::Assignment) = ASSIGNMENT_SCHEMA
variable_groups(::Assignment) = ASSIGNMENT_GROUPS
components(::Assignment) = [MAX_PER_BIN]
has_update(::Assignment) = true

"""A row-major flat column as an items x bins matrix."""
row_major_matrix(c::Column{Float64}) = permutedims(reshape(c.values, c.shape[2], c.shape[1]))

function typed_data(::Assignment, cols::Columns)
    return AssignmentData(column(cols, "items", String).values, column(cols, "bins", String).values,
                          row_major_matrix(column(cols, "value", Float64)),
                          row_major_matrix(column(cols, "weight", Float64)),
                          column(cols, "capacity", Float64).values)
end

function typed_params(::Assignment, raw::Dict{String,Any}, attached::Vector{String})
    for k in keys(raw)
        k in ASSIGNMENT_PARAMS || refuse("bad_request", "assignment params are at most {max_per_bin}")
    end
    cap = get(raw, MAX_PER_BIN, nothing)
    cap === nothing || (cap isa Int && cap >= 0) ||
        refuse("bad_request", "max_per_bin must be an integer >= 0")
    MAX_PER_BIN in attached && cap === nothing &&
        refuse("bad_request", "the max_per_bin component needs params.max_per_bin")
    return AssignmentParams(cap === nothing ? -1 : cap)
end

"""Add one `max_per_bin` row per bin: the items in it at most `cap`."""
function add_max_per_bin!(model::Model, x::Matrix{VariableRef}, cap::Int)
    rows = Vector{LessRef}(undef, size(x, 2))
    for b in axes(x, 2)
        expr = AffExpr(0.0)
        for i in axes(x, 1)
            add_to_expression!(expr, 1.0, x[i, b])
        end
        rows[b] = @constraint(model, expr <= cap)
    end
    return rows
end

function build!(model::Model, ::Assignment, setup::Setup{AssignmentData,AssignmentParams})
    d = setup.data
    ni, nb = length(d.items), length(d.bins)
    x = @variable(model, [1:ni, 1:nb], Bin)
    objective = AffExpr(0.0)
    sizehint!(objective.terms, ni * nb)
    for b in 1:nb, i in 1:ni
        add_to_expression!(objective, d.value[i, b], x[i, b])
    end
    @objective(model, Max, objective)
    one_bin = Vector{LessRef}(undef, ni)
    for i in 1:ni
        expr = AffExpr(0.0)
        for b in 1:nb
            add_to_expression!(expr, 1.0, x[i, b])
        end
        one_bin[i] = @constraint(model, expr <= 1)
    end
    capacity = Vector{LessRef}(undef, nb)
    for b in 1:nb
        expr = AffExpr(0.0)
        for i in 1:ni
            add_to_expression!(expr, d.weight[i, b], x[i, b])
        end
        capacity[b] = @constraint(model, expr <= d.capacity[b])
    end
    cap = MAX_PER_BIN in setup.attached ? add_max_per_bin!(model, x, setup.params.max_per_bin) :
          LessRef[]
    return AssignmentState(x, one_bin, capacity, cap)
end

function update!(model::Model, ::Assignment, state::AssignmentState,
                 old::Setup{AssignmentData,AssignmentParams},
                 new::Setup{AssignmentData,AssignmentParams})
    d0, d1, x = old.data, new.data, state.x
    for b in axes(x, 2)
        row = state.capacity[b]
        for i in axes(x, 1)
            d1.value[i, b] == d0.value[i, b] || set_objective_coefficient(model, x[i, b], d1.value[i, b])
            d1.weight[i, b] == d0.weight[i, b] || set_normalized_coefficient(row, x[i, b], d1.weight[i, b])
        end
        d1.capacity[b] == d0.capacity[b] || set_normalized_rhs(row, d1.capacity[b])
    end
    attached = MAX_PER_BIN in new.attached
    cap = new.params.max_per_bin
    if attached && isempty(state.max_per_bin)
        state.max_per_bin = add_max_per_bin!(model, x, cap)
    elseif !attached && !isempty(state.max_per_bin)
        delete(model, state.max_per_bin)
        state.max_per_bin = LessRef[]
    elseif attached && cap != old.params.max_per_bin
        for row in state.max_per_bin
            set_normalized_rhs(row, cap)
        end
    end
    return
end

variables(::Assignment, state::AssignmentState) = Any[state.x]

function constraint_groups(::Assignment, state::AssignmentState, setup::Setup)
    out = Pair{String,Vector{LessRef}}["one_bin" => state.one_bin, "capacity" => state.capacity]
    MAX_PER_BIN in setup.attached && push!(out, MAX_PER_BIN => state.max_per_bin)
    return out
end

function solution(::Assignment, state::AssignmentState, setup::Setup{AssignmentData}, ::Model,
                  point::Bool)
    point || return Dict{String,Any}("assigned" => nothing)
    d, x = setup.data, state.x
    assigned = Any[]
    for i in axes(x, 1), b in axes(x, 2)
        point_value(x[i, b]) > 0.5 && push!(assigned, [d.items[i], d.bins[b]])
    end
    return Dict{String,Any}("assigned" => assigned)
end

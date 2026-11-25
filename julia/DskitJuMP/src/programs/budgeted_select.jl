# Reference program `budgeted_select`: the 0/1 knapsack of the pyomo `BudgetedSelect`
# (wire spec, ADR-0328 amendment 1, "the reference programs"). Maximise the value of
# the selected items with their total cost within `params.budget`; the optional
# component `max_items` caps how many are selected.

"""The `budgeted_select` program (see the file header)."""
struct BudgetedSelect <: Program end

"""A scalar affine `<=` row's reference (the concrete type every container here holds)."""
const LessRef = ConstraintRef{Model,MOI.ConstraintIndex{MOI.ScalarAffineFunction{Float64},
                                                       MOI.LessThan{Float64}},ScalarShape}

const BUDGETED_SCHEMA = Schema(
    [FieldSpec("ids", "string", ["item"]), FieldSpec("cost", "float64", ["item"]),
     FieldSpec("value", "float64", ["item"])],
    ["item" => "ids"],
)

const BUDGETED_GROUPS = [GroupSpec("select", ["item"], "binary")]

"""The optional component capping the number selected."""
const MAX_ITEMS = "max_items"

"""
    BudgetedData(ids, cost, value)

The typed data of `budgeted_select`: parallel vectors over the items.
"""
struct BudgetedData
    ids::Vector{String}
    cost::Vector{Float64}
    value::Vector{Float64}
end

"""
    BudgetedParams(budget, max_items)

The typed params: the budget, and the item cap (`-1` when not given).
"""
struct BudgetedParams
    budget::Float64
    max_items::Int
end

"""
    BudgetedState(select, budget, max_items)

The live model's references: the selection variables, the budget row, and the
`max_items` row (empty when detached).
"""
mutable struct BudgetedState
    select::Vector{VariableRef}
    budget::Vector{LessRef}
    max_items::Vector{LessRef}
end

program_name(::BudgetedSelect) = "budgeted_select"
schema(::BudgetedSelect) = BUDGETED_SCHEMA
variable_groups(::BudgetedSelect) = BUDGETED_GROUPS
components(::BudgetedSelect) = [MAX_ITEMS]
has_update(::BudgetedSelect) = true

function typed_data(::BudgetedSelect, cols::Columns)
    return BudgetedData(column(cols, "ids", String).values, column(cols, "cost", Float64).values,
                        column(cols, "value", Float64).values)
end

function typed_params(::BudgetedSelect, raw::Dict{String,Any}, attached::Vector{String})
    budget = get(raw, "budget", nothing)
    ((budget isa Int || budget isa Float64)) ||
        refuse("bad_request", "params must carry a finite budget")
    cap = get(raw, MAX_ITEMS, nothing)
    if MAX_ITEMS in attached
        (cap isa Int && cap >= 0) ||
            refuse("bad_request", "the max_items component needs params.max_items, an integer >= 0")
    end
    return BudgetedParams(Float64(budget), cap isa Int ? cap : -1)
end

"""Add the `max_items` row: the number selected at most `cap`."""
function add_max_items!(model::Model, select::Vector{VariableRef}, cap::Int)
    expr = AffExpr(0.0)
    for x in select
        add_to_expression!(expr, 1.0, x)
    end
    return LessRef[@constraint(model, expr <= cap)]
end

function build!(model::Model, ::BudgetedSelect, setup::Setup{BudgetedData,BudgetedParams})
    d, q = setup.data, setup.params
    n = length(d.ids)
    select = @variable(model, [1:n], Bin)
    objective = AffExpr(0.0)
    spend = AffExpr(0.0)
    for i in 1:n
        add_to_expression!(objective, d.value[i], select[i])
        add_to_expression!(spend, d.cost[i], select[i])
    end
    @objective(model, Max, objective)
    budget = LessRef[@constraint(model, spend <= q.budget)]
    cap = MAX_ITEMS in setup.attached ? add_max_items!(model, select, q.max_items) : LessRef[]
    return BudgetedState(select, budget, cap)
end

function update!(model::Model, ::BudgetedSelect, state::BudgetedState,
                 old::Setup{BudgetedData,BudgetedParams}, new::Setup{BudgetedData,BudgetedParams})
    d0, d1 = old.data, new.data
    row = only(state.budget)
    for i in eachindex(d1.ids)
        x = state.select[i]
        d1.value[i] == d0.value[i] || set_objective_coefficient(model, x, d1.value[i])
        d1.cost[i] == d0.cost[i] || set_normalized_coefficient(row, x, d1.cost[i])
    end
    new.params.budget == old.params.budget || set_normalized_rhs(row, new.params.budget)
    attached = MAX_ITEMS in new.attached
    if attached && isempty(state.max_items)
        state.max_items = add_max_items!(model, state.select, new.params.max_items)
    elseif !attached && !isempty(state.max_items)
        delete(model, only(state.max_items))
        state.max_items = LessRef[]
    elseif attached && new.params.max_items != old.params.max_items
        set_normalized_rhs(only(state.max_items), new.params.max_items)
    end
    return
end

variables(::BudgetedSelect, state::BudgetedState) = Any[state.select]

function constraint_groups(::BudgetedSelect, state::BudgetedState, ::Setup)
    return Pair{String,Vector{LessRef}}["budget" => state.budget]
end

function solution(::BudgetedSelect, state::BudgetedState, setup::Setup{BudgetedData}, ::Model,
                  point::Bool)
    point || return Dict{String,Any}("selected" => nothing)
    chosen = String[setup.data.ids[i] for (i, x) in enumerate(state.select) if point_value(x) > 0.5]
    return Dict{String,Any}("selected" => sort!(chosen))
end

# Live models and the pool (ADR-0328 A3): models keyed by (program, signature, solver,
# solver_options), each held by exactly one open handle or idle, least recently used
# evicted past either cap. A handle to an evicted model refuses by name; nothing here
# ever rebuilds behind a caller.

"""The pool's default model cap (jump.py `DEFAULT_POOL_MODELS`)."""
const DEFAULT_POOL_MODELS = 4

"""The pool's default estimated-bytes cap (jump.py `DEFAULT_POOL_BYTES`)."""
const DEFAULT_POOL_BYTES = 2^32

"""The prefix of every handle; a decimal counter from 1 follows it."""
const HANDLE_PREFIX = "h"

"""
    VarGroup(spec, refs, keys, lookup, offset)

One addressable variable group of a live model: its declaration, its variables flat,
each variable's wire key, the key's position, and the group's offset in the model's
flat variable list.
"""
struct VarGroup
    spec::GroupSpec
    refs::Vector{VariableRef}
    keys::Vector{Key}
    lookup::Dict{Key,Int}
    offset::Int
end

"""
    VarIndex(groups, all)

Every addressable variable of a live model: by group, and flat (`all`, in group order),
so a fix, a start or a last solution is a position in `all`.
"""
struct VarIndex
    groups::Vector{VarGroup}
    all::Vector{VariableRef}
end

"""The variable index of a freshly built model: keys from the data's dimension keys."""
function make_index(p::Program, state, s::Schema, cols::Columns)
    dims = dimension_keys(s, cols)
    specs = variable_groups(p)
    arrays = variables(p, state)
    groups = VarGroup[]
    all_refs = VariableRef[]
    for (spec, arr) in zip(specs, arrays)
        refs = VariableRef[]
        keys = Key[]
        for ci in CartesianIndices(arr)
            push!(refs, arr[ci])
            push!(keys, KeyElem[dims[d][ci[j]] for (j, d) in enumerate(spec.index)])
        end
        lookup = Dict{Key,Int}(k => i for (i, k) in enumerate(keys))
        push!(groups, VarGroup(spec, refs, keys, lookup, length(all_refs)))
        append!(all_refs, refs)
    end
    return VarIndex(groups, all_refs)
end

"""The group named `name` of `index`, or the refusal `unknown_group`."""
function find_group(index::VarIndex, name::String)
    i = findfirst(g -> g.spec.name == name, index.groups)
    i === nothing && refuse("unknown_group", "no variable group named $(repr(name))")
    return index.groups[i]
end

"""
    FixInfo(value, lower, upper, equality)

A caller's fix and the program-owned domain it overlaid. An intrinsic equality
is distinct from coincident bounds and stays installed under a matching fix.
"""
struct FixInfo
    value::Float64
    lower::Union{Nothing,Float64}
    upper::Union{Nothing,Float64}
    equality::Union{Nothing,Float64}
end

"""
    LastSolution(values, record, handle, version)

A model's last feasible point (by flat variable position), the record of the solve that
found it, the handle that solved, and the model's state version at the time (the data,
params, components and fixes it was found under).
"""
struct LastSolution
    values::Vector{Float64}
    record::Dict{String,Any}
    handle::String
    version::Int
end

"""
    LiveModel{P,D,Q,S}

One live JuMP model with everything a handle can observe: the program, its key, the
model and the program's state over it, the setup and columns it was last given and
their digest, the variable index, fixes, start values and their source, the last
solution, a state version bumped by every change of data, params, components or fixes,
the holding handle (or `nothing` when idle), and its estimated bytes.

Concrete in all four parameters, so every op on it runs on concrete types.
"""
mutable struct LiveModel{P<:Program,D,Q,S}
    serial::Int
    key::String
    program::P
    solver::String
    options::Dict{String,Any}
    signature::String
    model::Model
    state::S
    setup::Setup{D,Q}
    columns::Columns
    digest::String
    index::VarIndex
    fixed::Dict{Int,FixInfo}
    start::Union{Nothing,Dict{Int,Float64}}
    start_source::Union{Nothing,String}
    last::Union{Nothing,LastSolution}
    version::Int
    holder::Union{Nothing,String}
    bytes::Int
end

"""The worker's estimate of one model's memory: `Base.summarysize` of the JuMP model."""
estimate_bytes(model::Model) = Base.summarysize(model)

"""
    Pool(pool_models, pool_bytes)

The live models by serial, their recency (least recent first), the open handles, the
handles whose model was evicted, and the counters (handles issued, serials, evictions).
"""
mutable struct Pool
    models::Dict{Int,LiveModel}
    recency::Vector{Int}
    handles::Dict{String,Int}
    evicted_handles::Set{String}
    issued::Int
    serials::Int
    evictions::Int
    pool_models::Int
    pool_bytes::Int
end

Pool(pool_models::Int=DEFAULT_POOL_MODELS, pool_bytes::Int=DEFAULT_POOL_BYTES) =
    Pool(Dict{Int,LiveModel}(), Int[], Dict{String,Int}(), Set{String}(), 0, 0, 0,
         pool_models, pool_bytes)

"""The pool's estimated bytes: the sum over its models."""
pool_bytes(pool::Pool) = isempty(pool.models) ? 0 : sum(lm.bytes for lm in values(pool.models))

"""Make model `serial` the most recently used."""
function touch!(pool::Pool, serial::Int)
    filter!(!=(serial), pool.recency)
    push!(pool.recency, serial)
    return
end

"""Add a new live model as the most recently used."""
function add!(pool::Pool, lm::LiveModel)
    pool.models[lm.serial] = lm
    push!(pool.recency, lm.serial)
    return lm
end

"""The least recently used IDLE model of `key`, or `nothing`."""
function idle_model(pool::Pool, key::String)
    for serial in pool.recency
        lm = pool.models[serial]
        lm.key == key && lm.holder === nothing && return lm
    end
    return nothing
end

"""Issue the next handle."""
next_handle!(pool::Pool) = HANDLE_PREFIX * string(pool.issued += 1)

"""
    evict!(pool, keep)

Evict least recently used models other than `keep` while the pool holds more than its
model cap or its byte cap; return how many were evicted. An evicted model's handle
answers `evicted` from then on.
"""
function evict!(pool::Pool, keep::Int)
    evicted = 0
    while length(pool.models) > pool.pool_models || pool_bytes(pool) > pool.pool_bytes
        i = findfirst(!=(keep), pool.recency)
        i === nothing && break
        serial = popat!(pool.recency, i)
        lm = pop!(pool.models, serial)
        if lm.holder !== nothing
            delete!(pool.handles, lm.holder)
            push!(pool.evicted_handles, lm.holder)
        end
        MOI.empty!(backend(lm.model))
        evicted += 1
        pool.evictions += 1
    end
    return evicted
end

"""
    held_model(pool, handle)

The live model `handle` holds, or the refusal `evicted` (its model was evicted) or
`unknown_handle` (never issued here, or closed).
"""
function held_model(pool::Pool, handle::String)
    handle in pool.evicted_handles &&
        refuse("evicted", "the model of handle $(repr(handle)) was evicted from the pool")
    serial = get(pool.handles, handle, nothing)
    serial === nothing && refuse("unknown_handle", "no open handle $(repr(handle))")
    return pool.models[serial]
end

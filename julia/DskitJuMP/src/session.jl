# The session ops (ADR-0328 A1, wire protocol v2): describe, open, update, fix, free,
# start, a handle solve, close. Each op validates everything before it changes
# anything, so a refusal leaves the worker exactly as it was; each successful op on a
# handle makes its model the most recently used.

# --- reading a request's fields -------------------------------------------------------

"""Field `k` of `req` as a non-empty string, or `bad_request`."""
function req_name(req::Dict{String,Any}, k::String)
    v = req[k]
    (v isa String && !isempty(v)) || refuse("bad_request", "$(repr(k)) must be a non-empty string")
    return v
end

"""Field `k` of `req` as an object, or `bad_request`."""
function req_object(req::Dict{String,Any}, k::String)
    v = req[k]
    v isa Dict{String,Any} || refuse("bad_request", "$(repr(k)) must be an object")
    return v
end

"""The `solver_options` of `req`: an object of name to scalar, or `bad_request`."""
function req_options(req::Dict{String,Any})
    options = req_object(req, "solver_options")
    for (k, v) in options
        option_value_ok(v) || refuse("bad_request", "solver option $(repr(k)) must be a scalar")
    end
    return options
end

"""The `components` of `req` checked against program `p`: name to bool."""
function req_components(req::Dict{String,Any}, p::Program)
    obj = req_object(req, "components")
    all(v -> v isa Bool, values(obj)) ||
        refuse("bad_request", "components must be an object of name to bool")
    known = components(p)
    for name in keys(obj)
        name in known || refuse("unknown_component", "no component named $(repr(name))")
    end
    return Dict{String,Bool}(k => v for (k, v) in obj)
end

"""The attached components after applying `changes` to `attached`, sorted."""
function attach(attached::Vector{String}, changes::Dict{String,Bool})
    out = Set(attached)
    for (name, on) in changes
        on ? push!(out, name) : delete!(out, name)
    end
    return sort!(collect(out))
end

"""
    make_setup(p, cols, params, attached)

The typed setup of program `p` (a function barrier: the typed data and params are
concrete from here on).
"""
function make_setup(p::Program, cols::Columns, params::Dict{String,Any}, attached::Vector{String})
    q = typed_params(p, params, attached)
    d = typed_data(p, cols)
    return Setup(d, q, canonical_json(params), attached)
end

# --- the timings block --------------------------------------------------------------

"""
    timings(pool, lm, path; build, update, transfer, solve, accepted, evicted)

The timings block of a reply: the path, the measured seconds, the start in effect on
the model, whether the solver reported using it, and the pool after the request.
"""
function timings(pool::Pool, lm::LiveModel, path::String; build::Float64=0.0,
                 update::Float64=0.0, transfer::Float64=0.0, solve::Float64=0.0,
                 accepted::Union{Nothing,Bool}=nothing, evicted::Int=0)
    return Dict{String,Any}(
        "path" => path, "build_seconds" => build, "update_seconds" => update,
        "transfer_seconds" => transfer, "solve_seconds" => solve,
        "start_source" => lm.start_source, "start_accepted" => accepted,
        "pool" => Dict{String,Any}("models" => length(pool.models), "bytes" => pool_bytes(pool),
                                   "evicted" => evicted, "evictions" => pool.evictions))
end

# --- describe -----------------------------------------------------------------------

"""The `describe` reply body of program `p`."""
function op_describe(req::Dict{String,Any})
    p = lookup_program(req_name(req, "program"))
    vars = Dict{String,Any}(g.name => Dict{String,Any}("index" => g.index, "domain" => g.domain)
                            for g in variable_groups(p))
    return Dict{String,Any}("program" => program_name(p), "schema" => schema_object(schema(p)),
                            "variables" => vars, "components" => components(p),
                            "update" => has_update(p))
end

# --- building, updating and rebuilding a live model --------------------------------------

"""The pool key of a model: program, signature, solver and options, canonical."""
pool_key(p::Program, sig::String, solver::String, options::Dict{String,Any}) =
    canonical_json(Any[program_name(p), sig, solver, options])

"""
    build_live(pool, p, setup, cols, sig, key, solver, options)

A new live model built from `setup` (a function barrier on the program's concrete
types). Returns the model and the seconds the build took.
"""
function build_live(pool::Pool, p::P, setup::Setup{D,Q}, cols::Columns, sig::String,
                    key::String, solver::String, options::Dict{String,Any}) where {P<:Program,D,Q}
    started = time()
    model = new_model(p, solver, options)
    state = guarded(() -> build!(model, p, setup), "build")
    index = make_index(p, state, schema(p), cols)
    seconds = time() - started
    lm = LiveModel{P,D,Q,typeof(state)}(pool.serials + 1, key, p, solver, options, sig, model,
                                        state, setup, cols, data_digest(schema(p), cols), index,
                                        Dict{Int,FixInfo}(), nothing, nothing, nothing, 0,
                                        nothing, estimate_bytes(model))
    pool.serials += 1
    return lm, seconds
end

"""
    change!(lm, setup, cols, digest; reopen=false)

Bring a held or idle model to the requested setup. A held edit keeps compatible
caller state. An idle reopen releases caller state. Returns path, build seconds
and update seconds.
"""
function change!(lm::LiveModel{P,D,Q,S}, setup::Setup{D,Q}, cols::Columns,
                 digest::String; reopen::Bool=false) where {P,D,Q,S}
    if digest == lm.digest && setup.params_json == lm.setup.params_json &&
       setup.attached == lm.setup.attached
        (!reopen || (isempty(lm.fixed) && lm.start === nothing)) && return "reused", 0.0, 0.0
        started = time()
        release!(lm)
        return "reused", 0.0, time() - started
    end
    fixes = reopen ? Dict{Int,FixInfo}() : lm.fixed
    start, source = reopen ? (nothing, nothing) : (lm.start, lm.start_source)
    if has_update(lm.program)
        started = time()
        next_fixes = with_recovery(lm, "update") do
            lift_fixes!(lm.index, lm.fixed, keys(lm.fixed))
            reopen && write_starts!(lm.index, lm.start, nothing)
            update!(lm.model, lm.program, lm.state, lm.setup, setup)
            restored = restore_fixes!(lm.index, fixes)
            write_starts!(lm.index, nothing, start)
            return restored
        end
        lm.fixed, lm.start, lm.start_source = next_fixes, start, source
        adopt!(lm, setup, cols, digest)
        return "updated", 0.0, time() - started
    end
    next, build, update = guarded(
        () -> prepare_replacement(lm, setup, cols, fixes, start, source), "rebuild")
    started = time()
    install_replacement!(lm, next)
    adopt!(lm, setup, cols, digest)
    return "rebuilt", build, update + (time() - started)
end

"""Commit the new setup and invalidate any standing incumbent."""
function adopt!(lm::LiveModel, setup::Setup, cols::Columns, digest::String)
    lm.setup, lm.columns, lm.digest = setup, cols, digest
    lm.version += 1
    return
end

"""Prepare a complete backend from the target columns, independently of the active model."""
function prepare_replacement(lm::LiveModel{P,D,Q,S}, setup::Setup{D,Q}, cols::Columns,
                             fixes, start, source) where {P,D,Q,S}
    began = time()
    model = new_model(lm.program, lm.solver, lm.options)
    state = build!(model, lm.program, setup)::S
    index = make_index(lm.program, state, schema(lm.program), cols)
    built = time()
    fixed = restore_fixes!(index, fixes)
    write_starts!(index, nothing, start)
    next = (; model, state, index, fixed, start, source, bytes=estimate_bytes(model))
    return next, built - began, time() - built
end

"""Install a prepared backend, then retire the previous backend."""
function install_replacement!(lm::LiveModel, next; bytes=next.bytes)
    old = lm.model
    lm.model, lm.state, lm.index = next.model, next.state, next.index
    lm.fixed, lm.start, lm.start_source, lm.bytes = next.fixed, next.start, next.source, bytes
    try
        MOI.empty!(backend(old))
    catch err
        # Replacement succeeded. A discarded backend cannot change the live model.
        @error "cannot clear the retired optimizer" exception=(err, catch_backtrace())
    end
    return
end

"""Recreate committed domains and caller state after a partial backend mutation."""
function restore_committed!(lm::LiveModel, fixes, start, source, cause)
    try
        next, build, update = prepare_replacement(lm, lm.setup, lm.columns, fixes, start, source)
        install_replacement!(lm, next; bytes=lm.bytes)
        return build + update
    catch err
        first_error = first(sprint(showerror, cause), 110)
        recovery_error = first(sprint(showerror, err), 110)
        error("cannot restore the committed model after $first_error; recovery raised $recovery_error")
    end
end

"""Protect committed state while an operation makes fallible physical changes."""
function with_recovery(f, lm::LiveModel, operation::String)
    try
        return f()
    catch err
        seconds = restore_committed!(lm, lm.fixed, lm.start, lm.start_source, err)
        note = "; the half-changed model was rebuilt from its previous data " *
               "($(round(seconds; digits=3)) s)"
        try
            guarded(() -> throw(err), operation)
        catch refusal
            throw(Refusal(refusal.kind, refusal.message * note))
        end
    end
end

"""Release caller overlays before handing an idle model to its new holder."""
function release!(lm::LiveModel)
    with_recovery(lm, "release") do
        lift_fixes!(lm.index, lm.fixed, keys(lm.fixed))
        write_starts!(lm.index, lm.start, nothing)
    end
    empty!(lm.fixed)
    lm.start, lm.start_source = nothing, nothing
    return
end

# --- addressing variables -------------------------------------------------------------

"""Whether `v` is a wire number (an integer or a finite float; never a bool)."""
is_number(v) = (v isa Integer && !(v isa Bool)) || v isa Float64

"""Return the program-owned bounds and intrinsic equality for one flat position."""
function declared_domain(index::VarIndex, fixes::Dict{Int,FixInfo}, pos::Int)
    info = get(fixes, pos, nothing)
    info === nothing || return info.lower, info.upper, info.equality
    v = index.all[pos]
    return (has_lower_bound(v) ? lower_bound(v) : nothing,
            has_upper_bound(v) ? upper_bound(v) : nothing,
            is_fixed(v) ? fix_value(v) : nothing)
end

"""Refuse a value outside the declared domain, naming its group and key."""
function check_value(index::VarIndex, fixes::Dict{Int,FixInfo}, g::VarGroup, pos::Int, value)
    x = Float64(value)
    if g.spec.domain == "binary" && !(x in (0.0, 1.0))
        refuse("bad_value", "$(g.spec.name) key $(canonical_json(g.keys[pos - g.offset])) is binary: $value is not 0 or 1")
    elseif g.spec.domain == "integer" && !isinteger(x)
        refuse("bad_value", "$(g.spec.name) key $(canonical_json(g.keys[pos - g.offset])) is integer: $value is not integral")
    end
    lower, upper, equality = declared_domain(index, fixes, pos)
    if equality !== nothing && x != equality
        refuse("bad_value", "$value conflicts with the program equality $equality at $(g.spec.name) key $(canonical_json(g.keys[pos - g.offset]))")
    elseif (lower !== nothing && x < lower) || (upper !== nothing && x > upper)
        refuse("bad_value", "$value is outside bounds [$lower, $upper] at $(g.spec.name) key $(canonical_json(g.keys[pos - g.offset]))")
    end
    return x
end

"""A wire key as a `Key`, or `bad_request` when it is not a list over the group's index."""
function wire_key(g::VarGroup, key)
    ok = key isa Vector{Any} && length(key) == length(g.spec.index) &&
         all(e -> e isa String || e isa Int, key)
    ok || refuse("bad_request", "a $(g.spec.name) key is a list over $(g.spec.index), got " *
                                "$(canonical_json(key))")
    return KeyElem[e for e in key]
end

"""
    addressed(lm, obj, with_values)

The flat positions (and values) a wire variables object addresses: `{group: {keys,
values}}` with values, `{group: {keys}}` without. Refuses `unknown_group`,
`unknown_key`, `bad_value` and `bad_request` (a wrong shape, a non-number value, a key
twice in one group).
"""
function addressed(lm::LiveModel, obj, with_values::Bool)
    obj isa Dict{String,Any} ||
        refuse("bad_request", "variables must be an object of group name to keys")
    out = Pair{Int,Float64}[]
    for name in sort!(collect(keys(obj)); by=codeunits)
        g = find_group(lm.index, name)
        gobj = obj[name]
        want = with_values ? ["keys", "values"] : ["keys"]
        (gobj isa Dict{String,Any} && sort!(collect(keys(gobj))) == want) ||
            refuse("bad_request", "a group must be exactly $(want)")
        ks = gobj["keys"]
        vs = with_values ? gobj["values"] : nothing
        (ks isa Vector{Any} && (!with_values || (vs isa Vector{Any} && length(vs) == length(ks)))) ||
            refuse("bad_request", "keys and values must be lists of the same length")
        seen = Set{Int}()
        for (i, key) in enumerate(ks)
            k = wire_key(g, key)
            local_pos = get(g.lookup, k, nothing)
            local_pos === nothing &&
                refuse("unknown_key", "$(g.spec.name) has no key $(canonical_json(key))")
            pos = g.offset + local_pos
            pos in seen && refuse("bad_request", "a key appears twice in $(g.spec.name)")
            push!(seen, pos)
            if with_values
                is_number(vs[i]) || refuse("bad_request", "a value must be a number, got " *
                                                          "$(canonical_json(vs[i]))")
                push!(out, pos => check_value(lm.index, lm.fixed, g, pos, vs[i]))
            else
                push!(out, pos => 0.0)
            end
        end
    end
    return out
end

# --- fixes and starts on the model ------------------------------------------------------

"""Apply physical overlays, then update the ledger after all writes succeed."""
function install_fixes!(index::VarIndex, fixes::Dict{Int,FixInfo}, pairs::Vector{Pair{Int,Float64}})
    planned = Pair{Int,FixInfo}[]
    for (pos, value) in pairs
        lower, upper, equality = declared_domain(index, fixes, pos)
        # The program equality also stays intact under a rounded search incumbent.
        push!(planned, pos => FixInfo(something(equality, value), lower, upper, equality))
    end
    for (pos, info) in planned
        info.equality === nothing && fix(index.all[pos], info.value; force=true)
    end
    for (pos, info) in planned
        fixes[pos] = info
    end
    return
end

"""Install prior caller values on the new domains, checking the entire set first."""
function restore_fixes!(index::VarIndex, prior::Dict{Int,FixInfo})
    isempty(prior) && return prior
    fixes = Dict{Int,FixInfo}()
    values = Pair{Int,Float64}[]
    for (pos, info) in prior
        g = only(g for g in index.groups if g.offset < pos <= g.offset + length(g.refs))
        push!(values, pos => check_value(index, fixes, g, pos, info.value))
    end
    install_fixes!(index, fixes, values)
    return fixes
end

"""Remove physical caller restrictions without changing the ledger."""
function lift_fixes!(index::VarIndex, fixes::Dict{Int,FixInfo}, positions)
    for pos in positions
        info = get(fixes, pos, nothing)
        (info === nothing || info.equality !== nothing) && continue
        v = index.all[pos]
        unfix(v)
        info.lower === nothing || set_lower_bound(v, info.lower)
        info.upper === nothing || set_upper_bound(v, info.upper)
    end
    return
end

"""Fix addressed variables as one caller operation."""
function apply_fixes!(lm::LiveModel, pairs::Vector{Pair{Int,Float64}})
    isempty(pairs) && return
    with_recovery(lm, "fix") do
        install_fixes!(lm.index, lm.fixed, pairs)
    end
    return
end

"""Release addressed caller fixes as one operation."""
function free_positions!(lm::LiveModel, positions::Vector{Int})
    isempty(positions) && return
    with_recovery(lm, "free") do
        lift_fixes!(lm.index, lm.fixed, positions)
    end
    for pos in positions
        delete!(lm.fixed, pos)
    end
    return
end

"""Write the physical start change while leaving its bookkeeping intact."""
function write_starts!(index::VarIndex, current, target)
    if current !== nothing
        for pos in keys(current)
            (target === nothing || !haskey(target, pos)) && set_start_value(index.all[pos], nothing)
        end
    end
    if target !== nothing
        for (pos, value) in target
            set_start_value(index.all[pos], value)
        end
    end
    return
end

"""Replace the caller's starts and publish their source after all writes succeed."""
function apply_start!(lm::LiveModel, start, source)
    with_recovery(lm, "start") do
        write_starts!(lm.index, lm.start, start)
    end
    lm.start, lm.start_source = start, source
    return
end

"""Clear the caller's start values."""
clear_start!(lm::LiveModel) = apply_start!(lm, nothing, nothing)

# --- the ops ----------------------------------------------------------------------------

"""The `open` reply body: build a model, or take the least recently used idle one of its key."""
function op_open(pool::Pool, req::Dict{String,Any})
    p = lookup_program(req_name(req, "program"))
    solver = req_name(req, "solver")
    optimizer_of(solver)
    started = time()
    data = req_object(req, "data")
    params = req_object(req, "params")
    options = req_options(req)
    attached = attach(String[], req_components(req, p))
    cols = check_full(schema(p), parse_columns(schema(p), data; partial=false))
    setup = make_setup(p, cols, params, attached)
    digest = data_digest(schema(p), cols)
    sig = signature(p, cols)
    transfer = time() - started
    return open_model(pool, p, setup, cols, digest, sig, solver, options, transfer)
end

"""`open` past validation: the pool lookup, the build or in-place change, eviction."""
function open_model(pool::Pool, p::Program, setup::Setup, cols::Columns, digest::String,
                    sig::String, solver::String, options::Dict{String,Any}, transfer::Float64)
    key = pool_key(p, sig, solver, options)
    lm = idle_model(pool, key)
    build = update = 0.0
    if lm === nothing
        lm, build = build_live(pool, p, setup, cols, sig, key, solver, options)
        add!(pool, lm)
        path = "built"
    else
        path, build, update = change!(lm, setup, cols, digest; reopen=true)
        lm.version += 1
        touch!(pool, lm.serial)
    end
    handle = next_handle!(pool)
    lm.holder = handle
    pool.handles[handle] = lm.serial
    evicted = evict!(pool, lm.serial)
    return Dict{String,Any}("handle" => handle, "signature" => lm.signature, "digest" => lm.digest,
                            "timings" => timings(pool, lm, path; build, update, transfer, evicted))
end

"""The `update` reply body: new data, params or components applied to the live model."""
function op_update(pool::Pool, req::Dict{String,Any})
    handle = req_name(req, "handle")
    lm = held_model(pool, handle)
    return update_model(pool, lm, handle, req)
end

function update_model(pool::Pool, lm::LiveModel, handle::String, req::Dict{String,Any})
    p, s = lm.program, schema(lm.program)
    started = time()
    data = req_object(req, "data")
    raw = req["params"]
    raw === nothing || raw isa Dict{String,Any} ||
        refuse("bad_request", "params must be an object or null")
    cols = merge(lm.columns, parse_columns(s, data; partial=true))
    check_full(s, cols)
    attached = attach(lm.setup.attached, req_components(req, p))
    params = raw === nothing ? parse_json(lm.setup.params_json)::Dict{String,Any} : raw
    setup = make_setup(p, cols, params, attached)
    signature(p, cols) == lm.signature ||
        refuse("shape_changed", "the update changes the model's shape: open a new handle")
    digest = data_digest(s, cols)
    transfer = time() - started
    path, build, update = change!(lm, setup, cols, digest)
    touch!(pool, lm.serial)
    return Dict{String,Any}("handle" => handle, "signature" => lm.signature, "digest" => lm.digest,
                            "timings" => timings(pool, lm, path; build, update, transfer))
end

"""The `fix` reply body: each addressed variable fixed at its value."""
function op_fix(pool::Pool, req::Dict{String,Any})
    handle = req_name(req, "handle")
    lm = held_model(pool, handle)
    pairs = addressed(lm, req["values"], true)
    started = time()
    apply_fixes!(lm, pairs)
    isempty(pairs) || (lm.version += 1)
    update = time() - started
    touch!(pool, lm.serial)
    return Dict{String,Any}("handle" => handle, "fixed" => length(lm.fixed),
                            "timings" => timings(pool, lm, "reused"; update))
end

"""The `free` reply body: the addressed variables (or all) released to their bounds."""
function op_free(pool::Pool, req::Dict{String,Any})
    handle = req_name(req, "handle")
    lm = held_model(pool, handle)
    targets = req["targets"]
    positions = if targets == "all"
        collect(keys(lm.fixed))
    elseif targets isa Dict{String,Any}
        Int[pos for (pos, _) in addressed(lm, targets, false)]
    else
        refuse("bad_request", "targets must be \"all\" or keys without values")
    end
    started = time()
    before = length(lm.fixed)
    free_positions!(lm, positions)
    length(lm.fixed) == before || (lm.version += 1)
    update = time() - started
    touch!(pool, lm.serial)
    return Dict{String,Any}("handle" => handle, "fixed" => length(lm.fixed),
                            "timings" => timings(pool, lm, "reused"; update))
end

"""What a `start` request's `source` may be."""
const START_SOURCES = ("last", "values", "none")

"""The `start` reply body: start values from the last solution, the wire, or none."""
function op_start(pool::Pool, req::Dict{String,Any})
    handle = req_name(req, "handle")
    lm = held_model(pool, handle)
    source, values = req["source"], req["values"]
    source in START_SOURCES ||
        refuse("bad_request", "source must be one of $(collect(START_SOURCES))")
    source != "values" && values != Dict{String,Any}() &&
        refuse("bad_request", "values must be {} unless source is \"values\"")
    source == "last" && lm.last === nothing &&
        refuse("no_solution", "the model has no last solution to start from")
    pairs = source == "values" ? addressed(lm, values, true) : Pair{Int,Float64}[]
    started = time()
    if source == "last"
        apply_start!(lm, Dict{Int,Float64}(i => v for (i, v) in enumerate(lm.last.values)), "last")
    elseif source == "values"
        apply_start!(lm, Dict{Int,Float64}(pairs), "values")
    else
        clear_start!(lm)
    end
    update = time() - started
    touch!(pool, lm.serial)
    count = lm.start === nothing ? 0 : length(lm.start)
    return Dict{String,Any}("handle" => handle, "started" => count,
                            "timings" => timings(pool, lm, "reused"; update))
end

"""The handle `solve` reply body: optimize the live model under per-call limits."""
function op_handle_solve(pool::Pool, req::Dict{String,Any})
    handle = req_name(req, "handle")
    lm = held_model(pool, handle)
    limits = parse_limits(req["limits"])
    solution_obj, record, seconds = solve_live(lm, handle, limits)
    accepted = lm.start_source === nothing ? nothing : start_accepted(lm.model)
    touch!(pool, lm.serial)
    return Dict{String,Any}("handle" => handle, "solution" => solution_obj, "record" => record,
                            "timings" => timings(pool, lm, "reused"; solve=seconds, accepted))
end

"""Solve `lm` under `limits`, keeping a feasible point as the last solution; return
`(solution, record, solve_seconds)` (a function barrier on the model's concrete types)."""
function solve_live(lm::LiveModel, handle::String, limits::Limits)
    model = lm.model
    seconds = with_limits(() -> run_optimize!(model), model, limits)
    point = has_point(model)
    rows = record_rows(lm.program, lm.state, lm.setup, point)
    record = solve_record(model, lm.solver, seconds, rows)
    extracted = solution(lm.program, lm.state, lm.setup, point ? point_value : nothing)
    if point
        lm.last = LastSolution(Float64[point_value(v) for v in lm.index.all], record, handle,
                               lm.version)
    end
    return extracted, record, seconds
end

"""The `close` reply body: release the handle; its model stays idle in the pool."""
function op_close(pool::Pool, req::Dict{String,Any})
    handle = req_name(req, "handle")
    if handle in pool.evicted_handles
        delete!(pool.evicted_handles, handle)
        return Dict{String,Any}("handle" => handle, "pooled" => false)
    end
    lm = held_model(pool, handle)
    delete!(pool.handles, handle)
    lm.holder = nothing
    touch!(pool, lm.serial)
    return Dict{String,Any}("handle" => handle, "pooled" => true)
end

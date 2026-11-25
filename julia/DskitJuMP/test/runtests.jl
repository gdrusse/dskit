# Unit tests of DskitJuMP on tiny instances: every hook and op, the pool, every error
# kind the worker sends, and the digest and default signature byte for byte against the
# Python reference (`tests/pipeline_libs/jump_fake_worker.py`).

using Test
using DskitJuMP
using JuMP
const D = DskitJuMP
const MOI = JuMP.MOI

"""A time limit on every solve these tests run (the host has crashed under load)."""
const TEST_TIME_LIMIT = 30.0

# --- a test-only program with no update! hook: every change rebuilds ---------------------

struct NoUpdateSelect <: D.Program end
D.program_name(::NoUpdateSelect) = "budgeted_select_rebuild"
for f in (:schema, :variable_groups, :components)
    @eval D.$f(::NoUpdateSelect) = D.$f(D.BudgetedSelect())
end
D.typed_data(::NoUpdateSelect, c::D.Columns) = D.typed_data(D.BudgetedSelect(), c)
D.typed_params(::NoUpdateSelect, r::Dict{String,Any}, a::Vector{String}) =
    D.typed_params(D.BudgetedSelect(), r, a)
D.build!(m::Model, ::NoUpdateSelect, s::D.Setup) = D.build!(m, D.BudgetedSelect(), s)
D.variables(::NoUpdateSelect, s) = D.variables(D.BudgetedSelect(), s)
D.constraint_groups(::NoUpdateSelect, s, u::D.Setup) = D.constraint_groups(D.BudgetedSelect(), s, u)
D.solution(::NoUpdateSelect, s, u::D.Setup, at) = D.solution(D.BudgetedSelect(), s, u, at)
register_program!(NoUpdateSelect())

# --- a test-only program counting its builds (a search round must never rebuild) ----------

const BUILDS = Ref(0)
struct CountingSelect <: D.Program end
D.program_name(::CountingSelect) = "budgeted_select_counting"
for f in (:schema, :variable_groups, :components, :has_update)
    @eval D.$f(::CountingSelect) = D.$f(D.BudgetedSelect())
end
D.typed_data(::CountingSelect, c::D.Columns) = D.typed_data(D.BudgetedSelect(), c)
D.typed_params(::CountingSelect, r::Dict{String,Any}, a::Vector{String}) =
    D.typed_params(D.BudgetedSelect(), r, a)
D.build!(m::Model, ::CountingSelect, s::D.Setup) = (BUILDS[] += 1; D.build!(m, D.BudgetedSelect(), s))
D.update!(m::Model, ::CountingSelect, st, a::D.Setup, b::D.Setup) = D.update!(m, D.BudgetedSelect(), st, a, b)
D.variables(::CountingSelect, s) = D.variables(D.BudgetedSelect(), s)
D.constraint_groups(::CountingSelect, s, u::D.Setup) = D.constraint_groups(D.BudgetedSelect(), s, u)
D.solution(::CountingSelect, s, u::D.Setup, at) = D.solution(D.BudgetedSelect(), s, u, at)
register_program!(CountingSelect())

# --- a test-only solver that applies no per-solve limit and whose optimize! raises ------

struct NoLimits <: MOI.AbstractOptimizer
    inner::MOI.Utilities.Model{Float64}
end
NoLimits() = NoLimits(MOI.Utilities.Model{Float64}())
MOI.is_empty(o::NoLimits) = MOI.is_empty(o.inner)
MOI.empty!(o::NoLimits) = MOI.empty!(o.inner)
MOI.supports(::NoLimits, ::MOI.Silent) = true
MOI.set(::NoLimits, ::MOI.Silent, ::Bool) = nothing
MOI.supports_constraint(o::NoLimits, F::Type{<:MOI.AbstractFunction}, S::Type{<:MOI.AbstractSet}) =
    MOI.supports_constraint(o.inner, F, S)
MOI.supports_add_constrained_variable(o::NoLimits, S::Type{<:MOI.AbstractScalarSet}) =
    MOI.supports_add_constrained_variable(o.inner, S)
MOI.supports(o::NoLimits, a::Union{MOI.ObjectiveSense,MOI.ObjectiveFunction}) = MOI.supports(o.inner, a)
MOI.add_variable(o::NoLimits) = MOI.add_variable(o.inner)
MOI.add_constrained_variable(o::NoLimits, s::MOI.AbstractScalarSet) = MOI.add_constrained_variable(o.inner, s)
MOI.add_constraint(o::NoLimits, f::MOI.AbstractFunction, s::MOI.AbstractSet) = MOI.add_constraint(o.inner, f, s)
MOI.set(o::NoLimits, a::Union{MOI.ObjectiveSense,MOI.ObjectiveFunction}, v) = MOI.set(o.inner, a, v)
MOI.get(o::NoLimits, a::MOI.AbstractModelAttribute) = MOI.get(o.inner, a)
MOI.get(o::NoLimits, a::MOI.AbstractConstraintAttribute, c::MOI.ConstraintIndex) = MOI.get(o.inner, a, c)
MOI.is_valid(o::NoLimits, i::MOI.Index) = MOI.is_valid(o.inner, i)
MOI.supports(::NoLimits, ::MOI.TimeLimitSec) = false
MOI.supports(::NoLimits, ::MOI.NodeLimit) = false
MOI.optimize!(::NoLimits) = error("the NoLimits optimizer always raises")
D.SOLVERS["NoLimits"] = NoLimits

# --- driving the worker in process --------------------------------------------------------

"""A worker and its next request id."""
mutable struct Session
    w::D.Worker
    id::Int
end

function Session(; models::Int=4, bytes::Int=2^32)
    s = Session(D.Worker(), 0)
    r = rpc(s, "hello"; settings=Dict("pool_models" => models, "pool_bytes" => bytes))
    @assert r["ok"] r
    return s
end

"""One request through the worker's own line handling, the reply re-read as JSON."""
function rpc(s::Session, op::String; fields...)
    s.id += 1
    req = Dict{String,Any}("id" => s.id, "op" => op)
    for (k, v) in fields
        req[string(k)] = v
    end
    reply = D.reply_to(s.w, D.canonical_json(req))
    return D.parse_json(D.canonical_json(reply))
end

"""The error kind of a reply, or `nothing` when it is ok."""
kind(r) = r["ok"] ? nothing : r["error"]["kind"]

const KS = Dict("ids" => ["a", "b", "c"], "cost" => [1.0, 2.0, 3.0], "value" => [3.0, 2.5, 4.0])
const GA = Dict("items" => ["i1", "i2"], "bins" => ["b1", "b2", "b3"],
                "value" => [[1.0, 2.0, -0.0], [4.0, 5.5, 6.0]],
                "weight" => [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]], "capacity" => [2.0, 2.5, 3.0])
const LIMITS = Dict("time_limit" => TEST_TIME_LIMIT)
const OPTS = Dict("mip_rel_gap" => 0, "threads" => 1, "random_seed" => 0)

open_ks(s; data=KS, params=Dict("budget" => 4.0), components=Dict(), program="budgeted_select",
        solver="HiGHS", options=OPTS) =
    rpc(s, "open"; program, data, params, solver, solver_options=options, components)
open_ga(s; data=GA, params=Dict(), components=Dict()) =
    rpc(s, "open"; program="assignment", data, params, solver="HiGHS", solver_options=OPTS,
        components)
solve_h(s, h; limits=LIMITS) = rpc(s, "solve"; handle=h, limits)
live(s::Session, h::String) = s.w.pool.models[s.w.pool.handles[h]]

const SEARCH_KS = Dict("ids" => ["A", "B", "C", "D", "E", "F"], "cost" => [4.0, 3.0, 3.0, 2.0, 2.0, 5.0],
                       "value" => [5.0, 4.0, 4.0, 3.0, 2.5, 6.0])
const FR = Dict("free" => 2, "stall" => 3)
search_h(s, h; params=FR, seed=7, limits=Dict("rounds" => 20), round_limits=LIMITS, search="fix_and_reoptimise") =
    rpc(s, "search"; handle=h, search, params, seed, limits, round_limits)
rows(r) = [(t["round"], t["objective"], t["accepted"], t["neighbourhood"]) for t in r["trajectory"]]

"""Make the point `chosen` the handle's standing last solution (the fake suite's {F})."""
function stand!(s, h, chosen)
    lm = live(s, h)
    values = Float64[k[1] in chosen ? 1.0 : 0.0 for g in lm.index.groups for k in g.keys]
    rec = Dict{String,Any}("objective" => sum(SEARCH_KS["value"][i] for (i, id) in enumerate(SEARCH_KS["ids"])
                                              if id in chosen),
                           "bound" => 12.0, "binding" => Any[], "solver" => "HiGHS", "variables" => 6,
                           "constraints" => 1, "status" => "FEASIBLE_POINT", "termination" => "NODE_LIMIT",
                           "gap" => nothing, "seconds" => 0.0)
    lm.last = D.LastSolution(values, rec, h, lm.version)
end

@testset "DskitJuMP" begin

@testset "digest and default signature match the Python reference byte for byte" begin
    # Values computed by tests/pipeline_libs/jump_fake_worker.py (data_digest,
    # shape_signature) for these exact inputs.
    function cols(s, data)
        D.check_full(s, D.parse_columns(s, D.parse_json(D.canonical_json(data)); partial=false))
    end
    ks = D.schema(D.BudgetedSelect())
    c = cols(ks, KS)
    @test D.data_digest(ks, c) == "9a0de6283e4634213cf43a2713c073449389ab28ee0a44b498c790cc7e660703"
    @test D.signature(D.BudgetedSelect(), c) ==
          "shape:89219c90693bc51313ceedf1d7b15438b94015fae1a780339d07195ef0f91355"
    ga = D.schema(D.Assignment())
    c = cols(ga, GA)
    @test D.data_digest(ga, c) == "461167e475eefb6cf1e2d089145abbf9f6c4786fab923d076dc631e5b0e5d490"
    @test D.signature(D.Assignment(), c) ==
          "shape:7ca1260f34ebd8016acb7242dae74fc23a714999afe6331eb0a8fc6948f09eff"
    empty = Dict("ids" => [], "cost" => [], "value" => [])
    c = cols(ks, empty)
    @test D.data_digest(ks, c) == "cbf44f30a25ba753c8367f090ecd60a284ef76538baefa5c2be0229f45baa7a3"
    @test D.shape_signature(ks, c) ==
          "shape:8119ec4f4fbe9805d9b17805660fc7eb274e6cf27d5925f719b09f9d9b6a0358"
    # Every element type, an unlabelled and an int64-labelled dimension, a non-ASCII
    # name sorted by its UTF-8 bytes, -0.0 written as +0.0, the int64 maximum.
    gs = D.Schema([D.FieldSpec("n", "int64", ["t"]), D.FieldSpec("flag", "bool", ["t"]),
                   D.FieldSpec("m", "float64", ["t", "k"]), D.FieldSpec("lab", "int64", ["k"]),
                   D.FieldSpec("name", "string", String[]), D.FieldSpec("é", "float64", String[])],
                  ["t" => nothing, "k" => "lab"])
    gd = Dict("n" => [-5, typemax(Int64), 0], "flag" => [true, false, true],
              "m" => [[0.1, 1e300], [-2.5, 0.0], [3.0, 7.0]], "lab" => [10, -3], "name" => "zé",
              "é" => -0.0)
    c = cols(gs, gd)
    @test D.data_digest(gs, c) == "201aabe99a686326e2f8ccdd1c42cd3910176ea6c272831eafcdad0cca6b2009"
    @test D.shape_signature(gs, c) ==
          "shape:288a72f6d5c33dd4ae8f898696b9912947b83e3fa30a322d63d0fa6833af7903"
end

@testset "strict JSON reading" begin
    bad(text) = try
        D.parse_json(text); nothing
    catch e
        e isa D.Refusal ? e.kind : rethrow()
    end
    @test bad("{\"a\":1,\"a\":2}") == "bad_request"
    @test bad("{\"a\":NaN}") == "bad_request"
    @test bad("{\"a\":Infinity}") == "bad_request"
    @test bad("{\"a\":1e999}") == "bad_request"
    @test bad("{\"a\":1" * "0"^400 * "}") == "bad_request"
    @test D.parse_json("{\"a\":123456789012345678901234567890}")["a"] isa BigInt
    @test bad("{\"a\":\"\\ud800\"}") == "bad_request"
    @test bad("{\"a\":\"\\udc00x\"}") == "bad_request"
    @test D.parse_json("\"\\ud83d\\ude00\"") == "\U1F600"
    @test bad("[" ^ 513 * "]" ^ 513) == "bad_request"
    @test D.parse_json("[" ^ 512 * "]" ^ 512) isa Vector
    @test bad("{} x") == "bad_request"
    @test bad("\"\xff\"") == "bad_request"
    @test D.parse_json("[1, -0.0, 2.5e-3, true, null, \"x\\n\"]") ==
          Any[1, -0.0, 0.0025, true, nothing, "x\n"]
end

@testset "hello, unknown ops, request exactness, ids" begin
    s = Session(models=3, bytes=1000)
    @test s.w.pool.pool_models == 3 && s.w.pool.pool_bytes == 1000
    w = D.Worker()
    r = D.parse_json(D.canonical_json(D.reply_to(w, "{\"id\":1,\"op\":\"hello\",\"settings\":{\"pool_models\":2,\"pool_bytes\":5}}")))
    @test sort(collect(keys(r))) == ["id", "julia", "ok", "packages", "protocol"]
    @test r["protocol"] == 2 && r["julia"] == string(VERSION) && r["packages"]["JuMP"] isa String
    @test kind(rpc(s, "hello"; settings=Dict("pool_models" => 0, "pool_bytes" => 5))) == "bad_request"
    @test kind(rpc(s, "hello"; settings=Dict("pool_models" => 1))) == "bad_request"
    @test kind(rpc(s, "frobnicate")) == "unknown_op"
    @test kind(rpc(s, "describe"; program="budgeted_select", extra=1)) == "bad_request"
    @test kind(rpc(s, "describe")) == "bad_request"
    @test kind(rpc(s, "describe"; program=true)) == "bad_request"
    r = D.reply_to(s.w, "{\"id\":1,\"op\":\"describe\",\"program\":\"assignment\"}")
    @test r["error"]["kind"] == "bad_request"   # ids strictly increase
    @test kind(rpc(s, "describe"; program="nope")) == "unknown_program"
    @test D.environment_problem() === nothing
    r = D.reply_to(s.w, "{\"id\":999,\"op\":\"describe\",\"program\":\"\\ud800\"}")
    @test r["error"]["kind"] == "bad_request" && r["id"] == 0
end

@testset "serve: one line per request, nothing else; shutdown and EOF end it" begin
    reqs = join(["{\"id\":1,\"op\":\"hello\",\"settings\":{\"pool_models\":4,\"pool_bytes\":4294967296}}",
                 "not json", "{\"id\":3,\"op\":\"shutdown\"}", "{\"id\":4,\"op\":\"hello\"}"], "\n")
    out = IOBuffer()
    serve(IOBuffer(reqs), out)
    lines = split(String(take!(out)), "\n"; keepempty=false)
    @test length(lines) == 3
    @test [D.parse_json(l)["ok"] for l in lines] == [true, false, true]
    out = IOBuffer()
    serve(IOBuffer("{\"id\":1,\"op\":\"describe\",\"program\":\"budgeted_select\"}\n"), out)
    @test length(split(String(take!(out)), "\n"; keepempty=false)) == 1
end

@testset "describe" begin
    s = Session()
    r = rpc(s, "describe"; program="budgeted_select")
    @test r["program"] == "budgeted_select" && r["update"] == true
    @test r["components"] == ["max_items"]
    @test r["variables"] == Dict("select" => Dict("index" => ["item"], "domain" => "binary"))
    @test r["schema"]["dims"] == Dict("item" => "ids")
    @test r["schema"]["fields"]["cost"] == Dict("type" => "float64", "dims" => ["item"])
    r = rpc(s, "describe"; program="assignment")
    @test r["variables"]["x"]["index"] == ["item", "bin"]
    @test r["schema"]["fields"]["value"]["dims"] == ["item", "bin"]
    @test rpc(s, "describe"; program="budgeted_select_rebuild")["update"] == false
end

@testset "one-shot solve (v1)" begin
    s = Session()
    r = rpc(s, "solve"; program="budgeted_select", data=merge(KS, Dict("pad" => 1)),
            params=Dict("budget" => 4.0), solver="HiGHS", solver_options=OPTS)
    @test r["solution"] == Dict("selected" => ["a", "c"])
    rec = r["record"]
    @test sort(collect(keys(rec))) == sort(collect(D.RECORD_KEYS))
    @test rec["termination"] == "OPTIMAL" && rec["status"] == "FEASIBLE_POINT"
    @test rec["objective"] == 7.0 && rec["gap"] == 0.0 && rec["variables"] == 3
    @test rec["constraints"] == 1
    @test rec["binding"] == [Dict("name" => "budget", "rows" => 1, "binding" => 1, "min_slack" => 0.0)]
    @test isempty(s.w.pool.models)
    r = rpc(s, "solve"; program="budgeted_select", data=KS, params=Dict("budget" => -1.0),
            solver="HiGHS", solver_options=OPTS)
    @test r["record"]["termination"] == "INFEASIBLE" && r["record"]["objective"] === nothing
    @test r["solution"] == Dict("selected" => nothing)
    @test r["record"]["binding"][1]["min_slack"] === nothing
    @test kind(rpc(s, "solve"; program="budgeted_select", data=KS, params=Dict("budget" => 4.0),
                   solver="Gurobi", solver_options=OPTS)) == "unknown_solver"
    @test kind(rpc(s, "solve"; program="budgeted_select", data=KS, params=Dict("budget" => 4.0),
                   solver="HiGHS", solver_options=Dict("no_such_option" => 1))) == "bad_request"
    @test kind(rpc(s, "solve"; program="budgeted_select", data=KS, params=Dict("budget" => 4.0),
                   solver="NoLimits", solver_options=Dict())) == "solve_failed"
end

@testset "a session: build once, change in place" begin
    s = Session()
    r = open_ks(s)
    @test r["ok"] && r["handle"] == "h1"
    @test r["digest"] == "9a0de6283e4634213cf43a2713c073449389ab28ee0a44b498c790cc7e660703"
    @test r["signature"] == "shape:89219c90693bc51313ceedf1d7b15438b94015fae1a780339d07195ef0f91355"
    t = r["timings"]
    @test t["path"] == "built" && t["build_seconds"] > 0 && t["update_seconds"] == 0
    @test t["solve_seconds"] == 0 && t["start_source"] === nothing && t["start_accepted"] === nothing
    @test t["pool"] == Dict("models" => 1, "bytes" => live(s, "h1").bytes, "evicted" => 0, "evictions" => 0)
    @test sort(collect(keys(t))) == sort(["path", "build_seconds", "update_seconds", "transfer_seconds",
                                          "solve_seconds", "start_source", "start_accepted", "pool"])
    model = live(s, "h1").model
    r = solve_h(s, "h1")
    @test r["solution"] == Dict("selected" => ["a", "c"]) && r["handle"] == "h1"
    @test r["timings"]["path"] == "reused" && r["timings"]["solve_seconds"] > 0
    @test r["timings"]["update_seconds"] == 0 && r["timings"]["build_seconds"] == 0

    # numbers only: updated in place, the same JuMP model object, a new answer
    r = rpc(s, "update"; handle="h1", data=Dict("value" => [3.0, 2.5, 1.0]), params=nothing,
            components=Dict())
    @test r["timings"]["path"] == "updated" && r["timings"]["build_seconds"] == 0
    @test r["signature"] == "shape:89219c90693bc51313ceedf1d7b15438b94015fae1a780339d07195ef0f91355"
    @test live(s, "h1").model === model
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["a", "b"])
    # nothing changed: reused
    r = rpc(s, "update"; handle="h1", data=Dict(), params=nothing, components=Dict())
    @test r["timings"]["path"] == "reused" && r["timings"]["update_seconds"] == 0
    # params and a component, in place
    r = rpc(s, "update"; handle="h1", data=Dict(), params=Dict("budget" => 1.0), components=Dict())
    @test r["timings"]["path"] == "updated"
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["a"])
    r = rpc(s, "update"; handle="h1", data=Dict("value" => [3.0, 2.5, 4.0]),
            params=Dict("budget" => 4.0, "max_items" => 1), components=Dict("max_items" => true))
    @test r["timings"]["path"] == "updated" && live(s, "h1").model === model
    r = solve_h(s, "h1")
    @test r["solution"] == Dict("selected" => ["c"]) && r["record"]["constraints"] == 2
    @test length(r["record"]["binding"]) == 1
    r = rpc(s, "update"; handle="h1", data=Dict(), params=nothing, components=Dict("max_items" => false))
    @test r["timings"]["path"] == "updated"
    r = solve_h(s, "h1")
    @test r["solution"] == Dict("selected" => ["a", "c"]) && r["record"]["constraints"] == 1
    @test live(s, "h1").model === model

    # fix, free and start
    r = rpc(s, "fix"; handle="h1", values=Dict("select" => Dict("keys" => [["b"]], "values" => [1])))
    @test r["fixed"] == 1 && r["timings"]["path"] == "reused" && r["timings"]["solve_seconds"] == 0
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["a", "b"])
    r = rpc(s, "fix"; handle="h1", values=Dict("select" => Dict("keys" => [["a"]], "values" => [0])))
    @test r["fixed"] == 2
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["b"])
    r = rpc(s, "free"; handle="h1", targets=Dict("select" => Dict("keys" => [["a"]])))
    @test r["fixed"] == 1
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["a", "b"])
    r = rpc(s, "free"; handle="h1", targets="all")
    @test r["fixed"] == 0
    r = solve_h(s, "h1")
    @test r["solution"] == Dict("selected" => ["a", "c"])
    @test r["record"]["objective"] == 7.0  # binaries kept their 0..1 bounds through fix/free
    r = rpc(s, "start"; handle="h1", source="last", values=Dict())
    @test r["started"] == 3 && r["timings"]["start_source"] == "last"
    r = solve_h(s, "h1")
    @test r["solution"] == Dict("selected" => ["a", "c"])
    @test r["timings"]["start_source"] == "last" && r["timings"]["start_accepted"] === nothing
    r = rpc(s, "start"; handle="h1", source="values",
            values=Dict("select" => Dict("keys" => [["b"]], "values" => [1.0])))
    @test r["started"] == 1 && r["timings"]["start_source"] == "values"
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["a", "c"])  # a start never changes the optimum
    r = rpc(s, "start"; handle="h1", source="none", values=Dict())
    @test r["started"] == 0 && r["timings"]["start_source"] === nothing

    # limits apply to one solve and are restored
    gap_before = get_attribute(live(s, "h1").model, MOI.RelativeGapTolerance())
    r = solve_h(s, "h1"; limits=Dict("time_limit" => 5, "node_limit" => 10, "mip_rel_gap" => 0.5,
                                     "solution_limit" => 3))
    @test r["ok"]
    @test get_attribute(live(s, "h1").model, MOI.RelativeGapTolerance()) == gap_before
    @test get_attribute(live(s, "h1").model, MOI.NodeLimit()) === nothing

    r = rpc(s, "close"; handle="h1")
    @test r == Dict("id" => s.id, "ok" => true, "handle" => "h1", "pooled" => true)
    @test kind(solve_h(s, "h1")) == "unknown_handle"
    @test kind(rpc(s, "close"; handle="h1")) == "unknown_handle"
end

@testset "assignment: the two-dimensional reference" begin
    s = Session()
    r = open_ga(s)
    @test r["digest"] == "461167e475eefb6cf1e2d089145abbf9f6c4786fab923d076dc631e5b0e5d490"
    r = solve_h(s, "h1")
    @test r["solution"] == Dict("assigned" => [["i1", "b2"], ["i2", "b3"]])
    rec = r["record"]
    @test rec["objective"] == 8.0 && rec["variables"] == 6 && rec["constraints"] == 5
    @test [row["name"] for row in rec["binding"]] == ["one_bin", "capacity"]
    @test [row["rows"] for row in rec["binding"]] == [2, 3]
    @test rec["binding"][1]["binding"] == 2 && rec["binding"][1]["min_slack"] == 0.0
    @test rec["binding"][2]["min_slack"] == 1.0
    model = live(s, "h1").model
    r = rpc(s, "update"; handle="h1", data=Dict("capacity" => [2.0, 2.5, 1.0]), params=nothing,
            components=Dict())
    @test r["timings"]["path"] == "updated" && live(s, "h1").model === model
    @test solve_h(s, "h1")["solution"] == Dict("assigned" => [["i1", "b1"], ["i2", "b2"]])
    r = rpc(s, "update"; handle="h1", data=Dict("weight" => [[1.0, 1.0, 1.0], [2.0, 3.0, 2.0]]),
            params=nothing, components=Dict())
    @test r["timings"]["path"] == "updated"
    # i2 fits only b1 (weight 2 <= 2) now: i2 b1 (4) + i1 b2 (2)
    @test solve_h(s, "h1")["solution"] == Dict("assigned" => [["i1", "b2"], ["i2", "b1"]])
    r = rpc(s, "update"; handle="h1", data=Dict("weight" => GA["weight"], "capacity" => GA["capacity"],
                                                "value" => [[1.0, 2.0, 0.0], [4.0, 5.5, 6.0]]),
            params=Dict("max_per_bin" => 0), components=Dict("max_per_bin" => true))
    @test r["timings"]["path"] == "updated" && live(s, "h1").model === model
    r = solve_h(s, "h1")
    @test r["solution"] == Dict("assigned" => []) && r["record"]["constraints"] == 8
    @test [row["name"] for row in r["record"]["binding"]] == ["one_bin", "capacity", "max_per_bin"]
    r = rpc(s, "update"; handle="h1", data=Dict(), params=Dict("max_per_bin" => 1), components=Dict())
    @test r["timings"]["path"] == "updated"
    @test solve_h(s, "h1")["solution"] == Dict("assigned" => [["i1", "b2"], ["i2", "b3"]])
    r = rpc(s, "fix"; handle="h1", values=Dict("x" => Dict("keys" => [["i2", "b3"]], "values" => [0])))
    @test r["fixed"] == 1
    @test solve_h(s, "h1")["solution"] == Dict("assigned" => [["i1", "b1"], ["i2", "b2"]])
    @test kind(rpc(s, "update"; handle="h1", data=Dict(), params=Dict("other" => 1),
                   components=Dict())) == "bad_request"
    @test kind(rpc(s, "update"; handle="h1", data=Dict(), params=Dict(),
                   components=Dict())) == "bad_request"  # max_per_bin attached, no cap
    @test kind(rpc(s, "update"; handle="h1", data=Dict("bins" => ["b1", "b2", "bX"]),
                   params=nothing, components=Dict())) == "shape_changed"
end

@testset "rebuild: a program with no update! hook, nothing lost" begin
    s = Session()
    r = open_ks(s; program="budgeted_select_rebuild")
    model = live(s, "h1").model
    rpc(s, "fix"; handle="h1", values=Dict("select" => Dict("keys" => [["b"]], "values" => [1])))
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["a", "b"])
    rpc(s, "start"; handle="h1", source="last", values=Dict())
    r = rpc(s, "update"; handle="h1", data=Dict("value" => [3.0, 2.5, 1.0]), params=nothing,
            components=Dict())
    t = r["timings"]
    @test t["path"] == "rebuilt" && t["build_seconds"] > 0 && t["start_source"] == "last"
    lm = live(s, "h1")
    @test lm.model !== model && length(lm.fixed) == 1 && lm.last.handle == "h1"
    @test is_fixed(lm.index.all[2]) && start_value(lm.index.all[1]) == 1.0
    @test solve_h(s, "h1")["solution"] == Dict("selected" => ["a", "b"])
end

@testset "the pool: keyed reuse, release, LRU eviction" begin
    s = Session(models=2)
    open_ks(s)                                              # h1, model 1
    rpc(s, "fix"; handle="h1", values=Dict("select" => Dict("keys" => [["c"]], "values" => [0])))
    solve_h(s, "h1")
    rpc(s, "close"; handle="h1")
    r = open_ks(s)                                          # h2: a pool hit, same data
    @test r["handle"] == "h2" && r["timings"]["path"] == "reused"
    @test r["timings"]["pool"]["models"] == 1
    lm = live(s, "h2")
    @test isempty(lm.fixed) && lm.start_source === nothing && lm.last.handle == "h1"
    @test rpc(s, "start"; handle="h2", source="last", values=Dict())["started"] == 3
    r = open_ks(s)                                          # h3: h2 holds model 1, so a build
    @test r["timings"]["path"] == "built" && r["timings"]["pool"]["models"] == 2
    rpc(s, "close"; handle="h3")
    r = open_ks(s; data=merge(KS, Dict("value" => [1.0, 1.0, 1.0])))  # h4: hit on model 2, updated
    @test r["timings"]["path"] == "updated" && r["timings"]["pool"]["models"] == 2
    # a new shape: model 3; least recent is model 1 (h2), which is evicted though held
    solve_h(s, "h4")                                        # h4's model is the most recent
    r = open_ks(s; data=Dict("ids" => ["x"], "cost" => [1.0], "value" => [1.0]))
    @test r["handle"] == "h5" && r["timings"]["pool"]["evicted"] == 1
    @test r["timings"]["pool"]["evictions"] == 1 && r["timings"]["pool"]["models"] == 2
    @test kind(solve_h(s, "h2")) == "evicted"
    @test kind(rpc(s, "fix"; handle="h2", values=Dict())) == "evicted"
    @test rpc(s, "close"; handle="h2")["pooled"] == false
    @test kind(rpc(s, "close"; handle="h2")) == "unknown_handle"
    # an error changes no recency: h4's failed op leaves h4 least recent
    solve_h(s, "h5")
    @test kind(rpc(s, "fix"; handle="h4", values=Dict("nope" => Dict("keys" => [])))) == "unknown_group"
    r = open_ks(s; data=Dict("ids" => ["x", "y"], "cost" => [1.0, 1.0], "value" => [1.0, 1.0]))
    @test r["timings"]["pool"]["evicted"] == 1
    @test kind(solve_h(s, "h4")) == "evicted"
    @test solve_h(s, "h5")["ok"]
    # the byte cap: every open evicts all others, never the one it serves
    b = Session(bytes=1)
    open_ks(b)
    r = open_ga(b)
    @test r["timings"]["pool"]["models"] == 1 && r["timings"]["pool"]["evicted"] == 1
    @test solve_h(b, "h2")["ok"] && kind(solve_h(b, "h1")) == "evicted"
    # the key includes the solver options, compared as an object
    o = Session()
    open_ks(o; options=Dict("threads" => 1, "random_seed" => 0)); rpc(o, "close"; handle="h1")
    @test open_ks(o; options=Dict("random_seed" => 0, "threads" => 1))["timings"]["path"] == "reused"
    rpc(o, "close"; handle="h2")
    @test open_ks(o; options=Dict("threads" => 1))["timings"]["path"] == "built"
end

@testset "every error kind, and an error changes nothing" begin
    s = Session()
    open_ks(s)
    lm = live(s, "h1")
    digest, version = lm.digest, lm.version
    @test kind(open_ks(s; program="nope")) == "unknown_program"
    @test kind(open_ks(s; solver="Gurobi")) == "unknown_solver"
    @test kind(open_ks(s; components=Dict("nope" => true))) == "unknown_component"
    @test kind(open_ks(s; components=Dict("max_items" => 1))) == "bad_request"
    @test kind(open_ks(s; components=Dict("max_items" => true))) == "bad_request"
    @test kind(open_ks(s; params=Dict())) == "bad_request"
    @test kind(open_ks(s; options=Dict("threads" => [1]))) == "bad_request"
    bad_data(data) = kind(open_ks(s; data))
    @test bad_data(Dict("ids" => ["a"], "cost" => [1.0])) == "bad_data"
    @test bad_data(merge(KS, Dict("extra" => [1]))) == "bad_data"
    @test bad_data(merge(KS, Dict("cost" => [1.0, 2.0]))) == "bad_data"
    @test bad_data(merge(KS, Dict("cost" => [1.0, "x", 2.0]))) == "bad_data"
    @test bad_data(merge(KS, Dict("cost" => [1.0, [2.0], 3.0]))) == "bad_data"
    @test bad_data(merge(KS, Dict("cost" => [1, 2^53 + 1, 3]))) == "bad_data"
    r = open_ks(s; data=merge(KS, Dict("value" => [1, 2^53, 3])))
    @test r["ok"] && rpc(s, "close"; handle=r["handle"])["pooled"]
    @test bad_data(merge(KS, Dict("cost" => [1, 2^53, 3]))) == "bad_data"  # HiGHS refuses 9e15
    # an update the solver refuses leaves the handle as it was
    m0 = live(s, "h1")
    @test kind(rpc(s, "update"; handle="h1", data=Dict("cost" => [1.0, 1e300, 3.0]), params=nothing,
                   components=Dict())) == "bad_data"
    @test live(s, "h1").digest == digest && live(s, "h1").version == version
    @test bad_data(merge(KS, Dict("ids" => ["a", "a", "c"]))) == "bad_data"
    @test bad_data(merge(KS, Dict("cost" => Dict("arrow" => Dict())))) == "bad_data"
    @test bad_data(merge(KS, Dict("budget" => Dict("arrow" => Dict())))) == "bad_data"
    @test kind(open_ga(s; data=merge(GA, Dict("value" => [[1.0, 2.0], [4.0, 5.5, 6.0]])))) == "bad_data"
    @test kind(rpc(s, "update"; handle="h1", data=Dict("ids" => ["a", "b", "z"]), params=nothing,
                   components=Dict())) == "shape_changed"
    @test kind(rpc(s, "update"; handle="h1", data=Dict("cost" => [1.0]), params=nothing,
                   components=Dict())) == "bad_data"
    @test kind(rpc(s, "update"; handle="h9", data=Dict(), params=nothing, components=Dict())) == "unknown_handle"
    fixv(v) = kind(rpc(s, "fix"; handle="h1", values=v))
    @test fixv(Dict("nope" => Dict("keys" => [], "values" => []))) == "unknown_group"
    @test fixv(Dict("select" => Dict("keys" => [["z"]], "values" => [1]))) == "unknown_key"
    @test fixv(Dict("select" => Dict("keys" => [["a"]], "values" => [0.5]))) == "bad_value"
    @test fixv(Dict("select" => Dict("keys" => [["a"]], "values" => [true]))) == "bad_request"
    @test fixv(Dict("select" => Dict("keys" => [["a"], ["a"]], "values" => [1, 1]))) == "bad_request"
    @test fixv(Dict("select" => Dict("keys" => ["a"], "values" => [1]))) == "bad_request"
    @test fixv(Dict("select" => Dict("keys" => [["a"]]))) == "bad_request"
    @test kind(rpc(s, "free"; handle="h1", targets="some")) == "bad_request"
    @test kind(rpc(s, "start"; handle="h1", source="last", values=Dict())) == "no_solution"
    @test kind(rpc(s, "start"; handle="h1", source="none",
                   values=Dict("select" => Dict("keys" => [], "values" => [])))) == "bad_request"
    @test kind(rpc(s, "start"; handle="h1", source="other", values=Dict())) == "bad_request"
    @test kind(solve_h(s, "h1"; limits=Dict("node_limit" => 0))) == "bad_request"
    @test kind(solve_h(s, "h1"; limits=Dict("time_limit" => 0))) == "bad_request"
    @test kind(solve_h(s, "h1"; limits=Dict("node_limit" => 1.5))) == "bad_request"
    @test kind(solve_h(s, "h1"; limits=Dict("gap" => 1))) == "bad_request"
    @test kind(rpc(s, "search"; handle="h1", search="nope", params=Dict(), seed=0,
                   limits=Dict(), round_limits=Dict())) == "unknown_search"
    @test lm.digest == digest && lm.version == version && isempty(lm.fixed) && lm.last === nothing
    # bad_limits and solve_failed: a solver that cannot apply a limit and always raises
    r = open_ks(s; solver="NoLimits", options=Dict())
    @test r["ok"]
    h = r["handle"]
    @test kind(solve_h(s, h; limits=Dict("node_limit" => 5))) == "bad_limits"
    @test kind(solve_h(s, h; limits=Dict())) == "solve_failed"
    # environment: a project with no resolved manifest
    dir = mktempdir()
    touch(joinpath(dir, "Project.toml"))
    old = Base.ACTIVE_PROJECT[]
    try
        Base.ACTIVE_PROJECT[] = joinpath(dir, "Project.toml")
        @test D.environment_problem() isa String
        @test kind(rpc(s, "hello"; settings=Dict("pool_models" => 1, "pool_bytes" => 1))) == "environment"
    finally
        Base.ACTIVE_PROJECT[] = old
    end
end

@testset "Arrow references: read whole, checked, the same digest as JSON" begin
    Arrow = D.Arrow
    dir = mktempdir()
    function arrow_ref(name, values; shape=[length(values)], file=true, column=:values, digest=nothing)
        path = joinpath(dir, name)
        open(path, "w") do io
            Arrow.write(io, NamedTuple{(column,)}((values,)); file)
        end
        sha = digest === nothing ? bytes2hex(D.sha256(read(path))) : digest
        return Dict("arrow" => Dict("path" => path, "sha256" => sha, "shape" => shape))
    end
    s = Session()
    # the same values by Arrow and by JSON: the same digest, signature and answer
    data = merge(KS, Dict("cost" => arrow_ref("cost.arrow", [1.0, 2.0, 3.0]),
                          "ids" => arrow_ref("ids.arrow", ["a", "b", "c"])))
    r = open_ks(s; data)
    @test r["ok"] && r["digest"] == "9a0de6283e4634213cf43a2713c073449389ab28ee0a44b498c790cc7e660703"
    @test r["signature"] == "shape:89219c90693bc51313ceedf1d7b15438b94015fae1a780339d07195ef0f91355"
    @test solve_h(s, r["handle"])["solution"] == Dict("selected" => ["a", "c"])
    @test isfile(joinpath(dir, "cost.arrow"))  # the worker never keeps, moves or deletes it
    # two dimensions, row-major (first dimension outermost), -0.0 as +0.0
    ga = merge(GA, Dict("value" => arrow_ref("v.arrow", [1.0, 2.0, -0.0, 4.0, 5.5, 6.0]; shape=[2, 3])))
    r = open_ga(s; data=ga)
    @test r["digest"] == "461167e475eefb6cf1e2d089145abbf9f6c4786fab923d076dc631e5b0e5d490"
    @test solve_h(s, r["handle"])["solution"] == Dict("assigned" => [["i1", "b2"], ["i2", "b3"]])
    # an update by Arrow
    h = r["handle"]
    r = rpc(s, "update"; handle=h, data=Dict("capacity" => arrow_ref("cap.arrow", [2.0, 2.5, 1.0])),
            params=nothing, components=Dict())
    @test r["timings"]["path"] == "updated" && r["timings"]["transfer_seconds"] >= 0
    @test solve_h(s, h)["solution"] == Dict("assigned" => [["i1", "b1"], ["i2", "b2"]])
    # every defect is bad_data naming the field, and changes nothing
    lm = live(s, h)
    digest, version = lm.digest, lm.version
    bad(ref) = kind(rpc(s, "update"; handle=h, data=Dict("capacity" => ref), params=nothing,
                        components=Dict()))
    good = arrow_ref("c.arrow", [1.0, 1.0, 1.0])
    with(f) = Dict("arrow" => merge(good["arrow"], f))
    @test bad(with(Dict("sha256" => "0"^64))) == "bad_data"
    @test bad(with(Dict("path" => "c.arrow"))) == "bad_data"                 # not absolute
    @test bad(with(Dict("path" => joinpath(dir, "missing.arrow")))) == "bad_data"
    @test bad(with(Dict("shape" => [3, 1]))) == "bad_data"                     # one length per dim
    @test bad(with(Dict("shape" => [-3]))) == "bad_data"
    @test bad(with(Dict("shape" => [2]))) == "bad_data"                        # 3 values, not 2
    @test bad(Dict("arrow" => merge(good["arrow"], Dict("extra" => 1)))) == "bad_data"
    @test bad(arrow_ref("s.arrow", [1.0, 1.0, 1.0]; file=false)) == "bad_data"  # a stream
    @test bad(arrow_ref("n.arrow", [1.0, 1.0, 1.0]; column=:other)) == "bad_data"
    @test bad(arrow_ref("i.arrow", [1, 1, 1])) == "bad_data"                   # int64, not float64
    @test bad(arrow_ref("m.arrow", [1.0, missing, 1.0])) == "bad_data"         # a null
    @test bad(arrow_ref("f.arrow", [1.0, Inf, 1.0])) == "bad_data"
    garbage = joinpath(dir, "g.arrow")
    write(garbage, "ARROW1\0\0garbageARROW1")
    @test bad(Dict("arrow" => Dict("path" => garbage, "sha256" => bytes2hex(D.sha256(read(garbage))),
                                   "shape" => [3]))) == "bad_data"
    r = rpc(s, "update"; handle=h, data=Dict("capacity" => with(Dict("sha256" => "0"^64))),
            params=nothing, components=Dict())
    @test occursin("capacity", r["error"]["message"])
    @test lm.digest == digest && lm.version == version
    # a scalar field takes no Arrow reference; a label field by Arrow is checked for duplicates
    @test kind(open_ks(s; data=merge(KS, Dict("ids" => arrow_ref("d.arrow", ["a", "a", "c"]))))) == "bad_data"
end

@testset "the seeded draw is the spec's SplitMix64 and partial Fisher-Yates" begin
    g = D.SplitMix64(0)
    @test [D.next!(g) for _ in 1:3] == [0xe220a8397b1dcdaf, 0x6e789e6aa1b965f4, 0x06c45d188009454f]
    # the neighbourhoods the Python suite pins for seed 7 over A..F, two per round
    g = D.SplitMix64(7)
    keys = collect(1:6)  # A..F
    @test [D.draw!(g, keys, 2), D.draw!(g, keys, 2)] == [[4, 6], [1, 5]]  # [D, F], [A, E]
    @test keys == collect(1:6)
    # ascending key order: groups by name bytes, strings by UTF-8 bytes, integers numerically
    @test D.key_less((UInt8[0x61], D.KeyElem["b"]), (UInt8[0x62], D.KeyElem["a"]))
    @test D.key_less((UInt8[0x61], D.KeyElem["Z"]), (UInt8[0x61], D.KeyElem["a"]))
    @test D.key_less((UInt8[0x61], D.KeyElem[2]), (UInt8[0x61], D.KeyElem[10]))
    @test D.key_less((UInt8[0x61], D.KeyElem["i", "b2"]), (UInt8[0x61], D.KeyElem["i", "b3"]))
    @test !D.key_less((UInt8[0x61], D.KeyElem["x"]), (UInt8[0x61], D.KeyElem["x"]))
end

@testset "search: the seeded trajectory, in place, the record's meaning" begin
    s = Session()
    BUILDS[] = 0
    r = open_ks(s; program="budgeted_select_counting", data=SEARCH_KS, params=Dict("budget" => 9.0))
    h = r["handle"]
    @test BUILDS[] == 1
    lm = live(s, h)
    model, nvars, ncons = lm.model, num_variables(lm.model), num_constraints(lm.model; count_variable_in_set_constraints=true)
    # the fake suite's fixture: the poor point {F} stands; seed 7 gives the pinned trajectory
    stand!(s, h, ["F"])
    r = search_h(s, h)
    @test r["ok"]
    @test r["solution"] == Dict("selected" => ["D", "E", "F"])
    @test rows(r) == [(1, 9.0, true, 2), (2, 11.5, true, 2), (3, 11.5, false, 2), (4, 11.5, false, 2),
                      (5, 11.5, false, 2)]
    rec = r["record"]
    @test rec["termination"] == "SEARCH_DONE" && rec["status"] == "FEASIBLE_POINT"
    @test rec["objective"] == 11.5 && rec["bound"] == 12.0          # the kept record's bound, never a round's
    @test rec["gap"] ≈ 0.5 / 11.5 && rec["solver"] == "HiGHS"
    @test rec["binding"][1]["name"] == "budget" && rec["binding"][1]["min_slack"] == 0.0  # at the incumbent
    t = r["timings"]
    @test t["path"] == "reused" && t["build_seconds"] == 0 && t["solve_seconds"] > 0
    @test t["update_seconds"] > 0 && t["start_accepted"] === nothing && t["start_source"] === nothing
    # a round costs bound changes and a solve, never a rebuild
    @test BUILDS[] == 1 && live(s, h).model === model && num_variables(model) == nvars
    @test num_constraints(model; count_variable_in_set_constraints=true) == ncons
    @test all(v -> !is_fixed(v) && start_value(v) === nothing, lm.index.all)
    # the incumbent is the last solution, found by this handle under this state: it stands
    @test lm.last.handle == h && lm.last.version == lm.version && lm.last.record == rec
    @test rpc(s, "start"; handle=h, source="last", values=Dict())["started"] == 6
    @test [start_value(v) for v in lm.index.all] == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    rpc(s, "start"; handle=h, source="none", values=Dict())
    r = search_h(s, h; params=Dict("free" => 2, "stall" => 1))
    @test rows(r)[1][2] == 11.5 && r["record"]["bound"] == 12.0  # stood: no first solve
    # the same seed, the same trajectory; a start in effect forbids standing, and is restored
    stand!(s, h, ["F"])
    r2 = search_h(s, h)
    rpc(s, "start"; handle=h, source="values", values=Dict("select" => Dict("keys" => [["F"]], "values" => [1])))
    r3 = search_h(s, h)
    @test rows(r3) == [(1, 12.0, false, 2), (2, 12.0, false, 2), (3, 12.0, false, 2)]
    @test r3["record"]["objective"] == 12.0 && r3["record"]["bound"] ≈ 12.0
    @test r3["timings"]["start_source"] == "values" && r3["timings"]["start_accepted"] === nothing
    @test lm.start_source == "values" && start_value(lm.index.all[6]) == 1.0
    @test count(v -> start_value(v) !== nothing, lm.index.all) == 1
    rpc(s, "start"; handle=h, source="none", values=Dict())
    # the caller's fixes are never freed by a round and survive the search
    rpc(s, "fix"; handle=h, values=Dict("select" => Dict("keys" => [["A"], ["B"]], "values" => [0, 1])))
    r = search_h(s, h; params=Dict("free" => 10, "stall" => 1))
    @test rows(r) == [(1, r["record"]["objective"], false, 4)]        # 6 - 2 caller-fixed
    @test r["solution"]["selected"][1] == "B" && !("A" in r["solution"]["selected"])
    @test length(lm.fixed) == 2 && is_fixed(lm.index.all[1]) && is_fixed(lm.index.all[2])
    @test count(is_fixed, lm.index.all) == 2
    # the round limit, then the time limit, each the stop reason
    r = search_h(s, h; limits=Dict("rounds" => 1))
    @test r["record"]["termination"] == "SEARCH_ROUND_LIMIT" && length(r["trajectory"]) == 1
    r = search_h(s, h; limits=Dict("time_limit" => 1e-9))
    @test r["record"]["termination"] == "SEARCH_TIME_LIMIT" && isempty(r["trajectory"])
    @test r["record"]["status"] == "FEASIBLE_POINT"
    # no incumbent: the first solve finds no point; no round runs
    rpc(s, "fix"; handle=h, values=Dict("select" => Dict("keys" => [["C"], ["F"]], "values" => [1, 1])))
    r = search_h(s, h)
    @test r["record"]["termination"] == "SEARCH_NO_INCUMBENT" && r["record"]["status"] == "NO_SOLUTION"
    @test r["record"]["objective"] === nothing && r["record"]["gap"] === nothing
    @test isempty(r["trajectory"]) && r["solution"] == Dict("selected" => nothing)
    @test BUILDS[] == 1 && live(s, h).model === model
    # every refusal changes nothing
    version, last = lm.version, lm.last
    @test kind(search_h(s, h; search="nope")) == "unknown_search"
    @test kind(search_h(s, h; params=Dict("free" => 1))) == "bad_request"
    @test kind(search_h(s, h; params=Dict("free" => 0, "stall" => 1))) == "bad_request"
    @test kind(search_h(s, h; params=Dict("free" => 1, "stall" => 1, "x" => 1))) == "bad_request"
    for seed in (-1, 2^53, 1.5, true)
        @test kind(search_h(s, h; seed)) == "bad_request"
    end
    @test kind(search_h(s, h; limits=Dict("rounds" => 0))) == "bad_request"
    @test kind(search_h(s, h; limits=Dict("time_limit" => 0))) == "bad_request"
    @test kind(search_h(s, h; limits=Dict("nodes" => 1))) == "bad_request"
    @test kind(search_h(s, h; round_limits=Dict("node_limit" => 0))) == "bad_request"
    @test kind(search_h(s, "h99")) == "unknown_handle"
    @test lm.version == version && lm.last === last && length(lm.fixed) == 4
    r = open_ks(s; solver="NoLimits", options=Dict())
    @test kind(search_h(s, r["handle"]; round_limits=Dict("node_limit" => 5))) == "bad_limits"
    @test kind(search_h(s, r["handle"]; round_limits=Dict())) == "solve_failed"
    # a two-dimensional group: keys in ascending order, element by element
    r = open_ga(s)
    g = r["handle"]
    lm2 = live(s, g)
    @test [lm2.index.groups[1].keys[p] for p in D.search_candidates(lm2)] ==
          [["i1", "b1"], ["i1", "b2"], ["i1", "b3"], ["i2", "b1"], ["i2", "b2"], ["i2", "b3"]]
    r = search_h(s, g; params=Dict("free" => 3, "stall" => 2))
    @test r["solution"] == Dict("assigned" => [["i1", "b2"], ["i2", "b3"]])
    @test r["record"]["termination"] == "SEARCH_DONE" && length(r["trajectory"]) == 2
end

end # testset DskitJuMP

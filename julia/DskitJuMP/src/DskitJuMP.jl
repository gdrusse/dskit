"""
    DskitJuMP

The Julia worker behind `dskit/pipeline/libs/jump.py` (ADR-0328 and its amendment 1).

A long-lived process reads one JSON request per line on stdin and answers one JSON line
per request on stdout (`serve`). It builds JuMP models once and changes them in place:
a live model is held by a handle, pooled by `(program, signature, solver,
solver_options)` when idle, and evicted least recently used past the pool's caps. The
wire protocol v2 is specified once, in `docs/architecture/decision-log.md` (ADR-0328
amendment 1); `tests/pipeline_libs/jump_fake_worker.py` is its executable reference and
this package matches it field for field.

A model is a `Program`: a concrete subtype declares its data schema, variable groups
and optional components, and implements `build!` and `solution` (plus, optionally,
`update!` so a change of numbers never rebuilds). The base lifecycle (`solve.jl`) owns
optimizer resolution, options, limits, `optimize!` and the solve record; a program never
calls `optimize!`. A `Search` (`search.jl`) is the second pluggable type: a
neighbourhood search whose base loop fixes, solves and frees the live model in place.

Why the build is fast: data is parsed once into a typed struct per program before any
model is touched; `build!` and `update!` run behind function barriers on concrete
types (`LiveModel{P,D,Q,S}`); models are `direct_model`s with string names off.
"""
module DskitJuMP

using Arrow
using JuMP
using HiGHS
using JSON
using SHA
using PrecompileTools: @setup_workload, @compile_workload

const MOI = JuMP.MOI

export Program, Search, register_program!, register_search!, serve

"""The wire protocol version this worker speaks (`PROTOCOL_VERSION` in jump.py)."""
const PROTOCOL_VERSION = 2

"""
    Refusal(kind, message)

A well-formed error reply: `kind` is one of the spec's error kinds, `message` a
non-empty sentence. Every op validates everything before it changes anything, so a
`Refusal` thrown before a change leaves the worker exactly as it was.
"""
struct Refusal <: Exception
    kind::String
    message::String
end

Base.showerror(io::IO, r::Refusal) = print(io, r.kind, ": ", r.message)

"""Throw a `Refusal` of `kind` with `message`."""
refuse(kind::AbstractString, message::AbstractString) = throw(Refusal(kind, message))

include("schema.jl")
include("program.jl")
include("record.jl")
include("solve.jl")
include("pool.jl")
include("session.jl")
include("search.jl")
include("worker.jl")
include("programs/budgeted_select.jl")
include("programs/assignment.jl")

function __init__()
    register_program!(BudgetedSelect())
    register_program!(Assignment())
    register_search!(FixAndReoptimise)
    return
end

@setup_workload begin
    @compile_workload begin
        register_program!(BudgetedSelect())
        register_program!(Assignment())
        register_search!(FixAndReoptimise)
        dir = mktempdir()
        path = joinpath(dir, "cost.arrow")
        Arrow.write(path, (values=[1.0, 2.0],))
        ref = Dict("arrow" => Dict("path" => path, "sha256" => bytes2hex(sha256(read(path))),
                                   "shape" => [2]))
        serve(IOBuffer(join(precompile_requests(ref), "\n") * "\n"), IOBuffer())
        rm(dir; recursive=true)
    end
end

end # module

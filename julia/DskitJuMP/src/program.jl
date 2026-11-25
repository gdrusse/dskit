# The Program contract: one abstract type, its hooks, and the registry by name.
#
# A concrete program is a singleton subtype of `Program`. It declares its contract
# (`program_name`, `schema`, `variable_groups`, `components`), parses wire values into
# its own typed structs (`typed_data`, `typed_params`), and builds into the one flat
# model it is handed (`build!`). Everything else has a default. The base never branches
# on a program's name: it calls these hooks and Julia dispatches.

"""
    Program

The abstract type of a JuMP program the worker can build.

Required hooks, for a concrete `P <: Program`:

- `program_name(p)::String`: the registry name.
- `schema(p)::Schema`: the declared data contract (field types and dimensions).
- `variable_groups(p)::Vector{GroupSpec}`: the addressable variable groups.
- `typed_data(p, columns::Columns)`: the validated typed data struct (`D`).
- `typed_params(p, raw::Dict{String,Any}, attached::Vector{String})`: the typed params
  (`Q`); `Refusal("bad_request", ...)` when the program cannot build from them.
- `build!(model, p, setup::Setup{D,Q})`: add variables, constraints and the objective;
  return the program's state `S` (its variable and constraint references).
- `solution(p, state, setup, at)`: the named values to return; `at(v)` is variable
  `v`'s value at the point (an integer variable's already rounded), `at === nothing`
  when there is no point. The point is the last solve's, or a search's incumbent.
- `variables(p, state)`: one array of `VariableRef` per group of `variable_groups`,
  in that order, each shaped by the group's index dimensions.
- `constraint_groups(p, state, setup)`: the named constraint containers, in record
  order, as `Pair{String, Vector{<:ConstraintRef}}`.

Optional hooks: `components(p)` (default none), `signature(p, columns)` (default: the
spec's shape digest), `has_update(p)` with `update!(model, p, state, old, new)`
(default: no in-place update, so a change rebuilds), `direct_mode(p)` (default true).

# Examples

    struct Tiny <: Program end
    DskitJuMP.program_name(::Tiny) = "tiny"
    # ... the other required hooks ...
    register_program!(Tiny())
"""
abstract type Program end

"""
    GroupSpec(name, index, domain)

A variable group as `describe` publishes it: its name, its index dimension names, and
its domain (`"binary"`, `"integer"` or `"continuous"`).
"""
struct GroupSpec
    name::String
    index::Vector{String}
    domain::String
end

"""The variable domains the wire knows."""
const DOMAINS = ("binary", "integer", "continuous")

"""
    Setup{D,Q}(data, params, params_json, attached)

What a model is built from: the typed data, the typed params, the params' canonical
JSON (for equality), and the attached optional components, sorted.
"""
struct Setup{D,Q}
    data::D
    params::Q
    params_json::String
    attached::Vector{String}
end

# --- required hooks --------------------------------------------------------------

"""The registry name of program `p`."""
function program_name end

"""The declared data contract of program `p` (a `Schema`)."""
function schema end

"""The addressable variable groups of program `p` (`Vector{GroupSpec}`)."""
function variable_groups end

"""Program `p`'s typed data struct from validated `columns`."""
function typed_data end

"""Program `p`'s typed params from the raw object and the attached components."""
function typed_params end

"""Build program `p` into `model` from `setup`; return the program's state."""
function build! end

"""The solution object of program `p` (JSON-able) at the point `at` reads (`nothing`: none)."""
function solution end

"""One `VariableRef` array per variable group of `p`, in declaration order."""
function variables end

"""The named constraint containers of `p` for the solve record, in record order."""
function constraint_groups end

# --- optional hooks with defaults ------------------------------------------------

"""The names of program `p`'s optional constraint groups (default none)."""
components(::Program) = String[]

"""Whether program `p` changes a live model in place (`update!`); default false."""
has_update(::Program) = false

"""
    update!(model, p, state, old::Setup, new::Setup)

Change the live `model` from `old` to `new` in place (numbers and components only; the
shape never changes here). Only called when `has_update(p)`.
"""
function update! end

"""
    direct_mode(p)

Whether program `p` builds into a `direct_model` (default true). Direct mode keeps no
second copy of the model in a cache, so every in-place edit reaches the solver at once
and the memory is the solver's alone; it has no bridges, so a program whose
constraints need a bridge the solver lacks returns false and gets a cached model.
"""
direct_mode(::Program) = true

"""
    signature(p, columns)

The shape key of program `p`'s data (default: the spec's `shape:` digest over every
dimension's name, length and labels).
"""
signature(p::Program, columns::Columns) = shape_signature(schema(p), columns)

# --- the registry ----------------------------------------------------------------

"""The programs this worker knows, by registry name."""
const PROGRAMS = Dict{String,Program}()

"""
    register_program!(p)

Register program `p` under `program_name(p)` (replacing one of the same name).
"""
function register_program!(p::Program)
    PROGRAMS[program_name(p)] = p
    return p
end

"""The registered program named `name`, or the refusal `unknown_program` naming it."""
function lookup_program(name::String)
    p = get(PROGRAMS, name, nothing)
    p === nothing && refuse("unknown_program", "no program named $(repr(name))")
    return p
end

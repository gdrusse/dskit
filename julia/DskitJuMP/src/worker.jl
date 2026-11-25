# The JSON-lines loop (ADR-0328 decision 2, wire protocol v2): one request per line on
# stdin, exactly one reply line per request on stdout, nothing else on stdout ever.
# Diagnostics go to stderr. The worker exits on `shutdown`, on stdin EOF and on a failed
# write to stdout. No exception escapes a request: every failure is a well-formed reply.

"""Arrays and objects one line may nest (the outermost counts as one)."""
const MAX_JSON_DEPTH = 512

"""The exact key set of each request, by op (`handle-solve` is a `solve` naming a handle)."""
const REQUEST_KEYS = Dict{String,Vector{String}}(
    "hello" => ["id", "op", "settings"],
    "describe" => ["id", "op", "program"],
    "open" => ["id", "op", "program", "data", "params", "solver", "solver_options", "components"],
    "update" => ["id", "op", "handle", "data", "params", "components"],
    "fix" => ["id", "op", "handle", "values"],
    "free" => ["id", "op", "handle", "targets"],
    "start" => ["id", "op", "handle", "source", "values"],
    "solve" => ["id", "op", "program", "data", "params", "solver", "solver_options"],
    "handle-solve" => ["id", "op", "handle", "limits"],
    "search" => ["id", "op", "handle", "search", "params", "seed", "limits", "round_limits"],
    "close" => ["id", "op", "handle"],
    "shutdown" => ["id", "op"],
)

"""The keys of a `hello` request's `settings`."""
const SETTINGS_KEYS = ["pool_bytes", "pool_models"]

"""The packages `hello` reports, with this package itself."""
const REPORTED_PACKAGES = (Arrow, JuMP, HiGHS, JSON, MOI, SHA)

# --- strict JSON reading ---------------------------------------------------------------

"""
    JsonReader(bytes)

A cursor over one request line. The reader is strict where JSON.jl is lenient, as the
wire spec requires: a duplicate key, a non-finite or overflowing number, nesting deeper
than `MAX_JSON_DEPTH`, a lone surrogate and invalid UTF-8 are all refused.
"""
mutable struct JsonReader
    bytes::Vector{UInt8}
    pos::Int
    depth::Int
end

"""Refuse the line as not protocol JSON (`bad_request`)."""
not_json(why::AbstractString) = refuse("bad_request", "the request is not protocol JSON: $why")

function skip_space!(r::JsonReader)
    while r.pos <= length(r.bytes) && r.bytes[r.pos] in (0x20, 0x09, 0x0a, 0x0d)
        r.pos += 1
    end
end

peek_byte(r::JsonReader) = r.pos <= length(r.bytes) ? r.bytes[r.pos] : 0x00

function expect!(r::JsonReader, b::UInt8)
    peek_byte(r) == b || not_json("expected $(repr(Char(b))) at byte $(r.pos)")
    r.pos += 1
end

function literal!(r::JsonReader, word::String, value)
    n = ncodeunits(word)
    r.pos + n - 1 <= length(r.bytes) && String(r.bytes[r.pos:r.pos+n-1]) == word ||
        not_json("bad literal at byte $(r.pos)")
    r.pos += n
    return value
end

function read_value!(r::JsonReader)
    skip_space!(r)
    b = peek_byte(r)
    b == UInt8('{') && return read_object!(r)
    b == UInt8('[') && return read_array!(r)
    b == UInt8('"') && return read_string!(r)
    b == UInt8('t') && return literal!(r, "true", true)
    b == UInt8('f') && return literal!(r, "false", false)
    b == UInt8('n') && return literal!(r, "null", nothing)
    (b == UInt8('-') || UInt8('0') <= b <= UInt8('9')) && return read_number!(r)
    not_json("unexpected byte at $(r.pos)")
end

function nest!(r::JsonReader)
    r.depth += 1
    r.depth > MAX_JSON_DEPTH && not_json("nested more than $MAX_JSON_DEPTH deep")
end

function read_object!(r::JsonReader)
    nest!(r)
    expect!(r, UInt8('{'))
    out = Dict{String,Any}()
    skip_space!(r)
    if peek_byte(r) == UInt8('}')
        r.pos += 1
        r.depth -= 1
        return out
    end
    while true
        skip_space!(r)
        peek_byte(r) == UInt8('"') || not_json("an object key must be a string")
        key = read_string!(r)
        haskey(out, key) && not_json("the key $(repr(key)) appears twice")
        skip_space!(r)
        expect!(r, UInt8(':'))
        out[key] = read_value!(r)
        skip_space!(r)
        b = peek_byte(r)
        r.pos += 1
        b == UInt8('}') && break
        b == UInt8(',') || not_json("expected ',' or '}' at byte $(r.pos - 1)")
    end
    r.depth -= 1
    return out
end

function read_array!(r::JsonReader)
    nest!(r)
    expect!(r, UInt8('['))
    out = Any[]
    skip_space!(r)
    if peek_byte(r) == UInt8(']')
        r.pos += 1
        r.depth -= 1
        return out
    end
    while true
        push!(out, read_value!(r))
        skip_space!(r)
        b = peek_byte(r)
        r.pos += 1
        b == UInt8(']') && break
        b == UInt8(',') || not_json("expected ',' or ']' at byte $(r.pos - 1)")
    end
    r.depth -= 1
    return out
end

function hex4!(r::JsonReader)
    r.pos + 3 <= length(r.bytes) || not_json("a short \\u escape")
    v = tryparse(UInt16, String(r.bytes[r.pos:r.pos+3]); base=16)
    v === nothing && not_json("a bad \\u escape")
    r.pos += 4
    return v
end

const ESCAPES = Dict{UInt8,UInt8}(UInt8('"') => UInt8('"'), UInt8('\\') => UInt8('\\'),
                                  UInt8('/') => UInt8('/'), UInt8('b') => 0x08,
                                  UInt8('f') => 0x0c, UInt8('n') => 0x0a,
                                  UInt8('r') => 0x0d, UInt8('t') => 0x09)

function read_string!(r::JsonReader)
    expect!(r, UInt8('"'))
    io = IOBuffer()
    while true
        r.pos <= length(r.bytes) || not_json("an unterminated string")
        b = r.bytes[r.pos]
        r.pos += 1
        if b == UInt8('"')
            break
        elseif b == UInt8('\\')
            e = peek_byte(r)
            r.pos += 1
            if e == UInt8('u')
                u = hex4!(r)
                if 0xd800 <= u <= 0xdbff
                    (peek_byte(r) == UInt8('\\') && r.pos + 1 <= length(r.bytes) &&
                     r.bytes[r.pos+1] == UInt8('u')) || not_json("a lone surrogate")
                    r.pos += 2
                    lo = hex4!(r)
                    0xdc00 <= lo <= 0xdfff || not_json("a lone surrogate")
                    print(io, Char(0x10000 + ((UInt32(u) - 0xd800) << 10) + (lo - 0xdc00)))
                elseif 0xdc00 <= u <= 0xdfff
                    not_json("a lone surrogate")
                else
                    print(io, Char(u))
                end
            else
                haskey(ESCAPES, e) || not_json("a bad escape")
                write(io, ESCAPES[e])
            end
        elseif b < 0x20
            not_json("a control character in a string")
        else
            write(io, b)
        end
    end
    return String(take!(io))
end

function read_number!(r::JsonReader)
    start = r.pos
    integral = true
    peek_byte(r) == UInt8('-') && (r.pos += 1)
    digits!() = (s = r.pos; while UInt8('0') <= peek_byte(r) <= UInt8('9'); r.pos += 1; end; r.pos > s)
    if peek_byte(r) == UInt8('0')
        r.pos += 1
    else
        digits!() || not_json("a bad number at byte $start")
    end
    if peek_byte(r) == UInt8('.')
        integral = false
        r.pos += 1
        digits!() || not_json("a bad number at byte $start")
    end
    if peek_byte(r) in (UInt8('e'), UInt8('E'))
        integral = false
        r.pos += 1
        peek_byte(r) in (UInt8('+'), UInt8('-')) && (r.pos += 1)
        digits!() || not_json("a bad number at byte $start")
    end
    text = String(r.bytes[start:r.pos-1])
    if integral
        v = tryparse(Int, text)
        v === nothing || return v
        big = parse(BigInt, text)
        abs(big) <= floatmax(Float64) || not_json("an integer too large for a float64")
        return big
    end
    v = tryparse(Float64, text)
    (v !== nothing && isfinite(v)) || not_json("a number that overflows a float64")
    return v
end

"""
    parse_json(text)

One JSON value from `text`, strictly (see `JsonReader`); `bad_request` when it is not
protocol JSON.
"""
function parse_json(text::AbstractString)
    isvalid(String, text) || not_json("not UTF-8 text")
    r = JsonReader(Vector{UInt8}(codeunits(text)), 1, 0)
    v = read_value!(r)
    skip_space!(r)
    r.pos > length(r.bytes) || not_json("trailing bytes after the value")
    return v
end

"""The canonical JSON text of `x`: keys sorted, no whitespace (equality and pool keys)."""
canonical_json(x) = JSON.json(x; sort_keys=true)

# --- the worker -------------------------------------------------------------------------

"""
    Worker(pool)

One worker process's state: the pool, the last request id, and whether `shutdown` came.
"""
mutable struct Worker
    pool::Pool
    last_id::Int
    stopping::Bool
end

Worker() = Worker(Pool(), 0, false)

"""The versions `hello` reports: package name to version."""
function package_versions()
    out = Dict{String,Any}()
    for m in (REPORTED_PACKAGES..., DskitJuMP)
        v = pkgversion(m)
        v === nothing || (out[string(nameof(m))] = string(v))
    end
    return out
end

"""
    environment_problem()

Why the active environment is not the resolved one this package pins, or `nothing`:
the manifest must exist beside the active project and name the running Julia version.
"""
function environment_problem()
    project = Base.active_project()
    project === nothing && return "no active project"
    manifest = joinpath(dirname(project), "Manifest.toml")
    isfile(manifest) || return "no Manifest.toml beside $(project): run Pkg.instantiate()"
    pinned = get(Base.parsed_toml(manifest), "julia_version", nothing)
    pinned == string(VERSION) ||
        return "the manifest was resolved for Julia $(pinned), this is Julia $(VERSION)"
    return nothing
end

"""The `hello` reply body; the pool takes the settings' caps."""
function op_hello(w::Worker, req::Dict{String,Any})
    settings = req["settings"]
    ok = settings isa Dict{String,Any} && sort!(collect(keys(settings))) == SETTINGS_KEYS &&
         all(v -> v isa Int && v >= 1, values(settings))
    ok || refuse("bad_request", "hello must carry settings {pool_models, pool_bytes}, integers >= 1")
    problem = environment_problem()
    problem === nothing || refuse("environment", problem)
    w.pool.pool_models = settings["pool_models"]
    w.pool.pool_bytes = settings["pool_bytes"]
    return Dict{String,Any}("protocol" => PROTOCOL_VERSION, "julia" => string(VERSION),
                            "packages" => package_versions())
end

"""The one-shot `solve` reply body (v1): a private model built, solved, discarded."""
function op_solve(req::Dict{String,Any})
    p = lookup_program(req_name(req, "program"))
    solver = req_name(req, "solver")
    optimizer_of(solver)
    data = req_object(req, "data")
    params = req_object(req, "params")
    options = req_options(req)
    s = schema(p)
    known = Dict{String,Any}(k => v for (k, v) in data if field_spec(s, k) !== nothing)
    cols = check_full(s, parse_columns(s, known; partial=false))
    setup = make_setup(p, cols, params, String[])
    solution_obj, record = one_shot_solve(p, setup, solver, options)
    return Dict{String,Any}("solution" => solution_obj, "record" => record)
end

"""The request kind of `req`: its op, or `handle-solve` for a solve naming a handle."""
request_kind(op::String, req::Dict{String,Any}) =
    op == "solve" && haskey(req, "handle") ? "handle-solve" : op

"""
    answer(w, req)

The reply body of one parsed request (without `id` and `ok`), or a `Refusal`.
"""
function answer(w::Worker, req::Dict{String,Any})
    op = get(req, "op", nothing)
    op isa String || refuse("bad_request", "a request carries op, a string")
    kind = request_kind(op, req)
    haskey(REQUEST_KEYS, kind) || refuse("unknown_op", "no op $(repr(op))")
    sort(collect(keys(req))) == sort(REQUEST_KEYS[kind]) ||
        refuse("bad_request", "a $(kind) request is exactly $(REQUEST_KEYS[kind])")
    kind == "hello" && return op_hello(w, req)
    kind == "describe" && return op_describe(req)
    kind == "solve" && return op_solve(req)
    kind == "open" && return op_open(w.pool, req)
    kind == "update" && return op_update(w.pool, req)
    kind == "fix" && return op_fix(w.pool, req)
    kind == "free" && return op_free(w.pool, req)
    kind == "start" && return op_start(w.pool, req)
    kind == "handle-solve" && return op_handle_solve(w.pool, req)
    kind == "close" && return op_close(w.pool, req)
    kind == "search" && return op_search(w.pool, req)
    w.stopping = true
    return Dict{String,Any}()
end

"""The reply to one request line: `{id, ok, ...}` or `{id, ok: false, error}`."""
function reply_to(w::Worker, line::AbstractString)
    id = 0
    try
        req = parse_json(line)
        req isa Dict{String,Any} || refuse("bad_request", "a request is a JSON object")
        raw = get(req, "id", nothing)
        raw isa Int && (id = raw)
        (raw isa Int && raw >= 1) || refuse("bad_request", "a request carries id, an integer >= 1")
        raw > w.last_id || refuse("bad_request", "request ids must strictly increase")
        w.last_id = raw
        body = answer(w, req)
        return merge!(Dict{String,Any}("id" => id, "ok" => true), body)
    catch err
        err isa Refusal || @error "unexpected worker error" exception = (err, catch_backtrace())
        kind, message = err isa Refusal ? (err.kind, err.message) :
                        ("internal", "unexpected worker error: $(first(sprint(showerror, err), 300))")
        return Dict{String,Any}("id" => id, "ok" => false,
                                "error" => Dict{String,Any}("kind" => kind, "message" => message))
    end
end

"""
    serve(input, output)

Answer every request line of `input` on `output` until `shutdown`, EOF, or a failed
write. `output` receives exactly one line per request and nothing else.
"""
function serve(input::IO, output::IO)
    w = Worker()
    while !eof(input)
        line = readline(input)
        isempty(strip(line)) && continue
        reply = reply_to(w, line)
        try
            write(output, canonical_json(reply), "\n")
            flush(output)
        catch err
            @error "cannot write a reply; exiting" exception = err
            return
        end
        w.stopping && return
    end
    return
end

"""
    serve()

The worker entry point: `serve(stdin, <the real stdout>)`, with the process's stdout
file descriptor pointed at stderr first, so nothing but replies (no solver banner, no
stray print, from Julia or C) can reach the reply channel.
"""
function serve()
    flush(stdout)
    replies = ccall(:dup, Cint, (Cint,), 1)
    replies < 0 && error("cannot duplicate stdout")
    ccall(:dup2, Cint, (Cint, Cint), 2, 1) < 0 && error("cannot point stdout at stderr")
    output = fdio(replies, true)
    redirect_stdout(stderr)
    serve(stdin, output)
    close(output)
    return
end

"""
    precompile_requests(arrow = nothing)

Requests of the precompile workload: every op on both programs, tiny instances; with an
Arrow reference `arrow` to a two-element float64 file, also an `open` reading it.
"""
function precompile_requests(arrow=nothing)
    ks = Dict("ids" => ["a", "b"], "cost" => [1.0, 2.0], "value" => [3.0, 1.0])
    gap = Dict("items" => ["i", "j"], "bins" => ["b"], "value" => [[2.0], [1.0]],
               "weight" => [[1.0], [1.0]], "capacity" => [1.0])
    opts = Dict("threads" => 1)
    reqs = Any[
        Dict("id" => 1, "op" => "hello", "settings" => Dict("pool_models" => 4, "pool_bytes" => 2^32)),
        Dict("id" => 2, "op" => "describe", "program" => "budgeted_select"),
        Dict("id" => 3, "op" => "solve", "program" => "budgeted_select", "data" => ks,
             "params" => Dict("budget" => 2.0), "solver" => DEFAULT_SOLVER, "solver_options" => opts),
        Dict("id" => 4, "op" => "open", "program" => "budgeted_select", "data" => ks,
             "params" => Dict("budget" => 2.0, "max_items" => 1), "solver" => DEFAULT_SOLVER,
             "solver_options" => opts, "components" => Dict("max_items" => true)),
        Dict("id" => 5, "op" => "solve", "handle" => "h1", "limits" => Dict("time_limit" => 10.0)),
        Dict("id" => 6, "op" => "update", "handle" => "h1", "data" => Dict("cost" => [2.0, 1.0]),
             "params" => nothing, "components" => Dict("max_items" => false)),
        Dict("id" => 7, "op" => "fix", "handle" => "h1",
             "values" => Dict("select" => Dict("keys" => [["a"]], "values" => [1]))),
        Dict("id" => 8, "op" => "start", "handle" => "h1", "source" => "last", "values" => Dict()),
        Dict("id" => 9, "op" => "solve", "handle" => "h1", "limits" => Dict()),
        Dict("id" => 10, "op" => "free", "handle" => "h1", "targets" => "all"),
        Dict("id" => 11, "op" => "close", "handle" => "h1"),
        Dict("id" => 12, "op" => "open", "program" => "assignment", "data" => gap,
             "params" => Dict(), "solver" => DEFAULT_SOLVER, "solver_options" => opts,
             "components" => Dict()),
        Dict("id" => 13, "op" => "solve", "handle" => "h2", "limits" => Dict("time_limit" => 10.0)),
        Dict("id" => 14, "op" => "update", "handle" => "h2", "data" => Dict("capacity" => [2.0]),
             "params" => Dict("max_per_bin" => 1), "components" => Dict("max_per_bin" => true)),
        Dict("id" => 15, "op" => "solve", "handle" => "h2", "limits" => Dict()),
        Dict("id" => 16, "op" => "fix", "handle" => "h2",
             "values" => Dict("x" => Dict("keys" => [["i", "b"]], "values" => [0]))),
        Dict("id" => 17, "op" => "start", "handle" => "h2", "source" => "values",
             "values" => Dict("x" => Dict("keys" => [["j", "b"]], "values" => [1]))),
        Dict("id" => 18, "op" => "free", "handle" => "h2",
             "targets" => Dict("x" => Dict("keys" => [["i", "b"]]))),
        Dict("id" => 19, "op" => "update", "handle" => "h2", "data" => Dict(),
             "params" => Dict(), "components" => Dict("max_per_bin" => false)),
        Dict("id" => 20, "op" => "close", "handle" => "h2"),
        Dict("id" => 21, "op" => "open", "program" => "assignment", "data" => gap,
             "params" => Dict(), "solver" => DEFAULT_SOLVER, "solver_options" => opts,
             "components" => Dict()),
        Dict("id" => 22, "op" => "search", "handle" => "h3", "search" => "fix_and_reoptimise",
             "params" => Dict("free" => 1, "stall" => 1), "seed" => 7,
             "limits" => Dict("rounds" => 2), "round_limits" => Dict("time_limit" => 10.0)),
        Dict("id" => 23, "op" => "close", "handle" => "h3"),
    ]
    if arrow !== nothing
        push!(reqs, Dict("id" => 24, "op" => "open", "program" => "budgeted_select",
                         "data" => merge(ks, Dict("cost" => arrow)), "params" => Dict("budget" => 2.0),
                         "solver" => DEFAULT_SOLVER, "solver_options" => opts, "components" => Dict()),
              Dict("id" => 25, "op" => "close", "handle" => "h4"))
    end
    push!(reqs, Dict("id" => 26, "op" => "shutdown"))
    return String[canonical_json(r) for r in reqs]
end

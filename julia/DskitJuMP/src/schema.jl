# The data contract (ADR-0328 A4): a program's declared schema, the typed columns a
# request's `data` is parsed into, and the canonical data digest and shape signature,
# byte for byte as the wire spec writes them (and as jump.py and the fake compute them).
# The Arrow handoff is part 2 of 2: an Arrow reference is refused by name here.

"""The schema's element types, each with its one-byte digest tag."""
const TYPE_TAGS = Dict("float64" => UInt8('f'), "int64" => UInt8('i'),
                       "string" => UInt8('s'), "bool" => UInt8('b'))

"""The Julia element type of each schema type."""
const ELEMENT_TYPES = Dict{String,DataType}("float64" => Float64, "int64" => Int64,
                                            "string" => String, "bool" => Bool)

"""The largest magnitude at which every integer is exact in a float64 (2^53)."""
const FLOAT64_EXACT = 2^53

"""The first bytes of the data digest's input."""
const DIGEST_MAGIC = "dskit-data-v1\n"

"""The first bytes of the default shape signature's input."""
const SHAPE_MAGIC = "dskit-shape-v1\n"

"""The prefix of the default signature."""
const SHAPE_PREFIX = "shape:"

"""
    FieldSpec(name, type, dims)

One declared data field: its name, its element type (a key of `TYPE_TAGS`) and its
dimension names (`[]` for a scalar).
"""
struct FieldSpec
    name::String
    type::String
    dims::Vector{String}
end

"""
    Schema(fields, dims)

A program's data contract: its fields, and each dimension's label field (`nothing` for
an unlabelled dimension, whose keys are the 0-based positions).

# Examples

    Schema([FieldSpec("ids", "string", ["item"]), FieldSpec("cost", "float64", ["item"])],
           ["item" => "ids"])
"""
struct Schema
    fields::Vector{FieldSpec}
    dims::Vector{Pair{String,Union{Nothing,String}}}
end

"""The field of `s` named `name`, or `nothing`."""
field_spec(s::Schema, name::AbstractString) =
    (i = findfirst(f -> f.name == name, s.fields); i === nothing ? nothing : s.fields[i])

"""The label field of dimension `dim`, or `nothing` when unlabelled."""
label_of(s::Schema, dim::String) = last(s.dims[findfirst(p -> first(p) == dim, s.dims)])

"""The JSON-able `describe` form of `s`: `{fields, dims}`."""
function schema_object(s::Schema)
    fields = Dict{String,Any}(f.name => Dict{String,Any}("type" => f.type, "dims" => f.dims)
                              for f in s.fields)
    dims = Dict{String,Any}(d => l for (d, l) in s.dims)
    return Dict{String,Any}("fields" => fields, "dims" => dims)
end

"""
    Column{T}(shape, values)

One field's values: its length per dimension and its elements flat, row-major (first
dimension outermost), exactly as the digest reads them.
"""
struct Column{T}
    shape::Vector{Int}
    values::Vector{T}
end

"""The typed data of one model: field name to column."""
const Columns = Dict{String,Column}

"""The column `name` of `cols` as a `Column{T}` (a type-stable accessor for `typed_data`)."""
column(cols::Columns, name::String, ::Type{T}) where {T} = cols[name]::Column{T}

"""A variable key element: a string label, or an integer label or position."""
const KeyElem = Union{String,Int}

"""A variable key: one element per index dimension (`[]` for a scalar group)."""
const Key = Vector{KeyElem}

# --- parsing wire values into columns ---------------------------------------------

"""`(shape, flat)` of `value` nested one list per dimension; `bad_data` naming `name` if not."""
function nested(name::String, value, ndim::Int)
    level = Any[value]
    shape = Int[]
    for _ in 1:ndim
        all(x -> x isa Vector{Any}, level) ||
            refuse("bad_data", "$(repr(name)) is not nested one list per dimension")
        lengths = unique(length.(level))
        length(lengths) > 1 && refuse("bad_data", "$(repr(name)) is ragged")
        push!(shape, isempty(lengths) ? 0 : only(lengths))
        level = Any[x for item in level for x in item]
    end
    return shape, level
end

"""One wire element as a `T`, or the refusal `bad_data` naming the field."""
function element(::Type{Float64}, name::String, v)
    v isa Float64 && return v
    v isa Int && abs(v) <= FLOAT64_EXACT && return Float64(v)
    if v isa Integer && !(v isa Bool)
        refuse("bad_data", "$(repr(name)): an integer of magnitude above 2^53 is not " *
                           "exactly a float64; write it as a float")
    end
    refuse("bad_data", "$(repr(name)) must hold float64 values")
end

function element(::Type{Int64}, name::String, v)
    v isa Int && return v
    refuse("bad_data", "$(repr(name)) must hold int64 values")
end

function element(::Type{String}, name::String, v)
    v isa String && return v
    refuse("bad_data", "$(repr(name)) must hold string values")
end

function element(::Type{Bool}, name::String, v)
    v isa Bool && return v
    refuse("bad_data", "$(repr(name)) must hold bool values")
end

"""Whether `value` is an Arrow reference (`{"arrow": ...}`)."""
is_arrow_ref(value) = value isa Dict{String,Any} && collect(keys(value)) == ["arrow"]

"""The column of one wire field value; `bad_data` naming the field when it does not fit."""
function parse_column(spec::FieldSpec, value)
    if !isempty(spec.dims) && is_arrow_ref(value)
        refuse("bad_data", "$(repr(spec.name)): Arrow references are read by part 2 of " *
                           "ADR-0328 amendment 1, not built in this worker yet")
    end
    shape, flat = nested(spec.name, value, length(spec.dims))
    return typed_column(ELEMENT_TYPES[spec.type], spec.name, shape, flat)
end

"""A `Column{T}` of `flat` under `shape` (a function barrier over the element type)."""
function typed_column(::Type{T}, name::String, shape::Vector{Int}, flat::Vector{Any}) where {T}
    out = Vector{T}(undef, length(flat))
    @inbounds for i in eachindex(flat)
        out[i] = element(T, name, flat[i])
    end
    return Column{T}(shape, out)
end

"""
    parse_columns(s, data; partial)

The columns of a request's `data` object: every field required unless `partial`, no
unknown field; `bad_data` naming the first offending field.
"""
function parse_columns(s::Schema, data::Dict{String,Any}; partial::Bool)
    for name in sort!(collect(keys(data)); by=codeunits)
        field_spec(s, name) === nothing && refuse("bad_data", "no field named $(repr(name))")
    end
    if !partial
        for f in sort(s.fields; by=f -> codeunits(f.name))
            haskey(data, f.name) || refuse("bad_data", "the field $(repr(f.name)) is required")
        end
    end
    cols = Columns()
    for (name, value) in data
        cols[name] = parse_column(field_spec(s, name), value)
    end
    return cols
end

"""Refuse (`bad_data`) full columns that disagree on a dimension or repeat a label."""
function check_full(s::Schema, cols::Columns)
    lengths = Dict{String,Int}()
    for name in sort!(collect(keys(cols)); by=codeunits)
        spec = field_spec(s, name)
        for (dim, n) in zip(spec.dims, cols[name].shape)
            get!(lengths, dim, n) == n ||
                refuse("bad_data", "$(repr(name)) disagrees on the length of $(repr(dim))")
        end
    end
    for (_, label) in s.dims
        label === nothing && continue
        values = cols[label].values
        length(unique(values)) == length(values) ||
            refuse("bad_data", "the label field $(repr(label)) must hold distinct values")
    end
    return cols
end

"""Each dimension's keys: its label field's values, or the positions 0..n-1."""
function dimension_keys(s::Schema, cols::Columns)
    out = Dict{String,Vector{KeyElem}}()
    for (dim, label) in s.dims
        if label !== nothing
            out[dim] = KeyElem[v for v in cols[label].values]
            continue
        end
        n = 0
        for f in s.fields
            i = findfirst(==(dim), f.dims)
            if i !== nothing && haskey(cols, f.name)
                n = cols[f.name].shape[i]
                break
            end
        end
        out[dim] = KeyElem[i for i in 0:n-1]
    end
    return out
end

# --- the canonical digests ----------------------------------------------------------

"""Write one element's canonical bytes."""
put_element(io::IO, v::Float64) = write(io, htol(reinterpret(UInt64, v == 0 ? 0.0 : v)))
put_element(io::IO, v::Int64) = write(io, htol(v))
put_element(io::IO, v::Bool) = write(io, v ? 0x01 : 0x00)
put_element(io::IO, v::String) = (put_name(io, v); nothing)

"""Write a name as its UTF-8 byte length (u32 LE) and bytes."""
function put_name(io::IO, text::String)
    write(io, htol(UInt32(ncodeunits(text))))
    write(io, text)
    return
end

"""Write every element of `c` (a function barrier over the element type)."""
function put_values(io::IO, c::Column{T}) where {T}
    for v in c.values
        put_element(io, v)
    end
    return
end

"""The lowercase hex sha256 of the full data's canonical form (the spec's `digest`)."""
function data_digest(s::Schema, cols::Columns)
    io = IOBuffer()
    write(io, DIGEST_MAGIC)
    for name in sort!(collect(keys(cols)); by=codeunits)
        spec = field_spec(s, name)
        c = cols[name]
        put_name(io, name)
        write(io, TYPE_TAGS[spec.type])
        write(io, htol(UInt32(length(c.shape))))
        for n in c.shape
            write(io, htol(UInt64(n)))
        end
        put_values(io, c)
    end
    return bytes2hex(sha256(take!(io)))
end

"""The default signature: `shape:` and the sha256 of every dimension's name, length, labels."""
function shape_signature(s::Schema, cols::Columns)
    keys_by_dim = dimension_keys(s, cols)
    io = IOBuffer()
    write(io, SHAPE_MAGIC)
    for (dim, label) in sort(s.dims; by=p -> codeunits(first(p)))
        put_name(io, dim)
        write(io, htol(UInt64(length(keys_by_dim[dim]))))
        if label === nothing
            write(io, 0x00)
        else
            write(io, 0x01)
            put_values(io, cols[label])
        end
    end
    return SHAPE_PREFIX * bytes2hex(sha256(take!(io)))
end

"""`base.py` — the shared mechanics every other production module reuses.

What is proved here is what the rest of the package is allowed to assume:
one error type that accumulates every problem before raising once; one
registry shape behind every `uses` site; ONE canonical-bytes recipe, so a
digest computed in `records.py` and a chain hash computed in `ledger.py`
cannot disagree; and ms/UTC helpers that refuse a naive stamp rather than
guessing a zone.

The hash tests deliberately RESTATE the §6 recipe with `hashlib` and
`json.dumps` instead of calling the module they check — an assertion
sourced from its subject asserts nothing (CLAUDE.md, "Duplication that
diverges": deliberate independent restatement is the exception that is
correct).
"""

import hashlib
import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dskit.production import base as production_base
from dskit.production.base import (
    ProductionError,
    Registry,
    canonical_bytes,
    canonical_hash,
    now_ms,
    parse_utc_ms,
    record_hash,
    reject_money_floats,
    reject_unknown_params,
    utc_iso,
)
from dskit.production.vocab import MONEY_FIELDS

#: The genesis link of every series chain (§6): 64 zeros, never a hash.
GENESIS = "0" * 64


# ---------------------------------------------------------------------------
# Independent restatement of the canonical recipe (§5.0, §6)
# ---------------------------------------------------------------------------


def _plain(obj):
    """The plan's rendering rules, restated: Decimal as its str, tuple as list."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (tuple, list)):
        return [_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def _canonical(obj):
    return json.dumps(
        _plain(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


# ---------------------------------------------------------------------------
# A registry family, for the Registry tests
# ---------------------------------------------------------------------------


class Fam(ABC):
    """A stand-in seam ABC."""

    @abstractmethod
    def go(self):
        ...


class GoodImpl(Fam):
    def go(self):
        return "went"


class OtherImpl(Fam):
    def go(self):
        return "also went"


class Unrelated:
    pass


@pytest.fixture()
def registry():
    reg = Registry("fam", Fam)
    reg.register("good", GoodImpl)
    return reg


# ---------------------------------------------------------------------------
# ProductionError
# ---------------------------------------------------------------------------


def test_production_error_accumulates_a_list_and_joins_it():
    err = ProductionError(["first problem", "second problem"])
    assert err.problems == ["first problem", "second problem"]
    assert str(err) == "first problem; second problem"


def test_production_error_is_a_value_error():
    """The same shape as `ConfigError` and `AssetError`, so a caller that
    already catches `ValueError` at a boundary keeps working."""
    assert issubclass(ProductionError, ValueError)
    with pytest.raises(ValueError):
        raise ProductionError(["boom"])


def test_production_error_reports_every_problem_not_the_first():
    """Validation accumulates: one raise carrying three problems, never
    three runs discovering one problem each."""
    err = ProductionError(["a", "b", "c"])
    assert len(err.problems) == 3
    assert "c" in str(err)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_exposes_its_family_name(registry):
    assert registry.family == "fam"


def test_registry_refuses_a_duplicate_name(registry):
    with pytest.raises(ProductionError):
        registry.register("good", OtherImpl)


def test_registry_refuses_a_non_subclass(registry):
    with pytest.raises(ProductionError):
        registry.register("unrelated", Unrelated)


def test_registry_resolves_a_registered_name(registry):
    assert registry.resolve("good") is GoodImpl


def test_registry_resolves_a_class_reference(registry):
    """The `pkg.module:Class` doorway (§4.3) — how a child supplies its own
    implementation without editing the package."""
    assert registry.resolve("tests.production.test_base:OtherImpl") is OtherImpl


def test_registry_refuses_a_class_reference_outside_the_family(registry):
    with pytest.raises(ProductionError):
        registry.resolve("tests.production.test_base:Unrelated")


def test_registry_refuses_an_unknown_name(registry):
    with pytest.raises(ProductionError):
        registry.resolve("nope")


def test_registry_refuses_an_unimportable_class_reference(registry):
    with pytest.raises(ProductionError):
        registry.resolve("no.such.module:Thing")


def test_registry_kinds_is_a_sorted_tuple(registry):
    registry.register("zzz", OtherImpl)
    registry.register("aaa", OtherImpl)
    assert registry.kinds() == ("aaa", "good", "zzz")


def test_registry_membership_reads_as_a_name_test(registry):
    assert "good" in registry
    assert "missing" not in registry


# ---------------------------------------------------------------------------
# canonical_bytes / canonical_hash
# ---------------------------------------------------------------------------


def test_canonical_bytes_sorts_keys_and_uses_compact_separators():
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonical_bytes_is_ascii_only():
    out = canonical_bytes({"k": "café"})
    assert out == b'{"k":"caf\\u00e9"}'
    out.decode("ascii")


def test_canonical_bytes_renders_decimal_as_its_string():
    """Money is `Decimal` and reaches JSON as a STRING — a float here is
    the rounding the whole package exists to avoid, and `"1.50"` must not
    become `"1.5"`."""
    assert canonical_bytes({"qty": Decimal("1.50")}) == b'{"qty":"1.50"}'


def test_canonical_bytes_renders_a_tuple_as_a_list():
    assert canonical_bytes(("a", 1)) == b'["a",1]'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_canonical_bytes_refuses_non_finite_numbers(bad):
    """A hash some writers can produce and others cannot is not an
    identity: NaN/Infinity are not JSON."""
    with pytest.raises(ProductionError):
        canonical_bytes({"v": bad})


def test_canonical_bytes_refuses_a_decimal_that_is_not_finite():
    with pytest.raises(ProductionError):
        canonical_bytes({"v": Decimal("NaN")})


@pytest.mark.parametrize(
    "bad", [{"v": {1, 2}}, {"v": datetime(2026, 9, 5, tzinfo=timezone.utc)}, {"v": b"x"}]
)
def test_canonical_bytes_refuses_an_unknown_type(bad):
    with pytest.raises(ProductionError):
        canonical_bytes(bad)


def test_canonical_hash_is_sha256_of_the_canonical_bytes():
    obj = {"kind": "tick", "seq": 3, "qty": Decimal("2.25")}
    assert canonical_hash(obj) == hashlib.sha256(_canonical(obj)).hexdigest()


def test_canonical_hash_does_not_strip_notes():
    """The assets recipe strips `notes` because documentation must not
    change what a CONFIG is. A record is not a config: two records that
    differ in any field — `notes` included — are different records."""
    assert canonical_hash({"a": 1, "notes": "why"}) != canonical_hash({"a": 1})


# ---------------------------------------------------------------------------
# Rendered — a value rendered once, spliced whole into enclosing renderings
# ---------------------------------------------------------------------------
#
# Canonical JSON is compositional (sorted keys, fixed separators, no
# indentation), so a value's rendering is an exact substring of every
# enclosing rendering. `Rendered` is that substring, kept; `Rendered.render`
# is `canonical_bytes` for a small shell that stands one in. These tests
# restate the recipe with `json.dumps` (`_canonical` above) and compare.


def _marks():
    """The first two markers the splice tries: values equal to them collide."""
    return [f"{production_base._MARK}{attempt}-0" for attempt in (0, 1)]


def _with_plain(shell, replacements):
    """`shell` with each Rendered leaf swapped for the value it was made from."""
    if isinstance(shell, production_base.Rendered):
        return replacements[id(shell)]
    if isinstance(shell, dict):
        return {k: _with_plain(v, replacements) for k, v in shell.items()}
    if isinstance(shell, (list, tuple)):
        return [_with_plain(v, replacements) for v in shell]
    return shell


VALUES = [
    {},
    [],
    "",
    0,
    None,
    True,
    {"b": 1, "a": [Decimal("1.50"), (1, 2.5), None], "é": "日本😀", "": {}},
    [1, [2, [3, {"z": "\u0000\u001f\"\\"}]]],
    {"big": 10**30, "neg0": -0.0, "tiny": 1e-300, "huge": 1e22, "t": True},
    "just a string",
]


@pytest.mark.parametrize("value", VALUES, ids=range(len(VALUES)))
def test_a_rendered_value_splices_into_the_bytes_the_plain_value_would_give(value):
    part = production_base.Rendered(value)
    shell = {"z": [1, part, {"k": part}], "a": "before", "m": (part,)}
    expected = _canonical(_with_plain(shell, {id(part): value}))
    assert production_base.Rendered.render(shell) == expected


@pytest.mark.parametrize("value", VALUES, ids=range(len(VALUES)))
def test_a_bare_rendered_value_renders_as_its_own_canonical_bytes(value):
    assert production_base.Rendered.render(production_base.Rendered(value)) == _canonical(
        value
    )


def test_two_different_rendered_values_keep_their_own_places():
    first, second = production_base.Rendered({"a": 1}), production_base.Rendered([2, 3])
    shell = {"y": second, "x": first, "w": [second, first]}
    expected = b'{"w":[[2,3],{"a":1}],"x":{"a":1},"y":[2,3]}'
    assert production_base.Rendered.render(shell) == expected


def test_a_shell_holding_no_rendered_value_renders_exactly_as_canonical_bytes():
    shell = {"b": [1, Decimal("2.50")], "a": ("x",)}
    assert production_base.Rendered.render(shell) == canonical_bytes(shell)


def test_a_rendered_value_is_a_snapshot_of_the_value_when_it_was_made():
    value = {"a": [1]}
    part = production_base.Rendered(value)
    value["a"].append(2)
    assert production_base.Rendered.render({"v": part}) == b'{"v":{"a":[1]}}'


def test_rendered_refuses_exactly_what_canonical_bytes_refuses():
    for bad in ({"a": [float("nan")]}, {"a": {1: 2}}, {"a": {1, 2}}, {"a": b"x"}):
        with pytest.raises(ProductionError) as plain:
            canonical_bytes(bad)
        with pytest.raises(ProductionError) as rendered:
            production_base.Rendered(bad)
        assert rendered.value.problems == plain.value.problems


def test_rendered_refuses_a_value_that_already_holds_a_rendered_one():
    with pytest.raises(ProductionError):
        production_base.Rendered({"a": production_base.Rendered(1)})


def test_a_shell_refuses_what_canonical_bytes_would_refuse_at_the_same_path():
    part = production_base.Rendered({"ok": 1})
    for bad, path in (
        ({"x": part, "n": float("inf")}, "$.n"),
        ({"x": [part, {1, 2}]}, "$.x[1]"),
        ({"x": part, 1: "k"}, "$"),
    ):
        with pytest.raises(ProductionError) as exc:
            production_base.Rendered.render(bad)
        assert exc.value.problems[0].startswith(path)


def test_only_the_rendered_seam_accepts_a_rendered_value():
    """`Rendered` is honoured where a caller asks for it by name and nowhere
    else: the plain recipes still refuse it, so no validator that walks a
    plain tree can be walked around by one."""
    shell = {"a": production_base.Rendered({MONEY_FIELDS[0]: 1.5})}
    for refuse in (
        lambda: canonical_bytes(shell),
        lambda: canonical_hash(shell),
        lambda: record_hash(GENESIS, shell),
    ):
        with pytest.raises(ProductionError):
            refuse()


def test_canonical_hash_and_record_hash_take_the_render_strategy_they_are_given():
    value = {"b": [Decimal("1.50"), 2], "a": "é"}
    part = production_base.Rendered(value)
    render = production_base.Rendered.render
    assert canonical_hash({"k": part}, render=render) == canonical_hash({"k": value})
    envelope = {"kind": "tick", "id": "x", "body": {"state": value}, "seq": 1}
    stood = {"kind": "tick", "id": "x", "body": {"state": part}, "seq": 1}
    assert record_hash(GENESIS, stood, render=render) == record_hash(GENESIS, envelope)
    assert record_hash(GENESIS, dict(stood, hash="c" * 64), render=render) == record_hash(
        GENESIS, envelope
    )


def test_the_default_render_strategy_is_canonical_bytes_itself():
    """Nothing changes for a caller that names no strategy."""
    obj = {"b": [1, Decimal("2.50")], "a": {"é": None}}
    assert canonical_hash(obj) == canonical_hash(obj, render=canonical_bytes)
    assert record_hash(GENESIS, obj) == record_hash(GENESIS, obj, render=canonical_bytes)


@pytest.mark.parametrize(
    "spelled",
    [
        lambda m: {"a": m[0]},
        lambda m: {"a": m[0], "b": m[1]},
        lambda m: {m[0]: 1, "a": [m[1]]},
        lambda m: {"a": 'x","' + m[0], "b": '"' + m[1] + '"'},
        lambda m: {"a": "x" + m[0], "b": m[0] + "x", "c": [m[0], m[0]]},
    ],
    ids=["value", "two-values", "key-and-list", "escaped-quotes", "near-misses"],
)
def test_data_that_spells_a_marker_never_takes_the_place_of_a_rendered_value(
    spelled, monkeypatch
):
    """The shell is rendered with a marker where the value goes and the marker
    is replaced. Data equal to (or containing, once escaped) that marker would
    be replaced instead: the renderer must notice and choose another."""
    part = production_base.Rendered({"inner": _marks()[0]})
    calls = []
    real = production_base.canonical_bytes

    def counting(obj):
        calls.append(obj)
        return real(obj)

    monkeypatch.setattr(production_base, "canonical_bytes", counting)
    data = spelled(_marks())
    shell = dict(data, zzz=part) if isinstance(data, dict) else [data, part]
    plain = dict(data, zzz={"inner": _marks()[0]})
    assert production_base.Rendered.render(shell) == _canonical(plain)
    assert len(calls) >= 2  # the first marker collided; a later one was used


def test_a_rendered_value_may_itself_spell_every_marker():
    """What is INSIDE a Rendered is never scanned: only the shell is."""
    inner = {m: [m, '"' + m + '"'] for m in _marks()}
    part = production_base.Rendered(inner)
    assert production_base.Rendered.render({"k": part}) == _canonical({"k": inner})


def test_rendered_repr_does_not_print_the_payload():
    text = repr(production_base.Rendered({"k": "x" * 5000}))
    assert len(text) < 80 and "xxx" not in text


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text()
    | st.decimals(allow_nan=False, allow_infinity=False),
    lambda children: st.lists(children, max_size=4)
    | st.tuples(children, children)
    | st.dictionaries(st.text(max_size=4), children, max_size=4),
    max_leaves=12,
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    value=json_values,
    other=json_values,
    text=st.text(),
    spelled=st.sampled_from([None, 0, 1]),
)
def test_splicing_any_value_matches_rendering_it_in_place(
    value, other, text, spelled
):
    text = text if spelled is None else _marks()[spelled]
    part = production_base.Rendered(value)
    shell = {"s": text, "v": part, "l": [other, part], "k" + text: 1}
    plain = _with_plain(shell, {id(part): value})
    assert production_base.Rendered.render(shell) == _canonical(plain)


# ---------------------------------------------------------------------------
# record_hash — the §6 chain
# ---------------------------------------------------------------------------


@pytest.fixture()
def envelope():
    """A §6 envelope, minus the `hash` the recipe computes."""
    return {
        "kind": "tick",
        "id": "tick-0001",
        "payload_digest": "a" * 64,
        "seq": 1,
        "series_id": "9f0e0b3a-0000-4000-8000-000000000001",
        "process_id": "proc-1",
        "release_hash": "b" * 64,
        "recorded_at_ms": 1_757_030_400_000,
        "schema_version": 1,
        "prev_hash": GENESIS,
    }


def test_record_hash_matches_the_section_six_recipe(envelope):
    """`hash = sha256(prev_hash + canonical(envelope − hash))`, computed
    here with `hashlib` rather than with the module under test."""
    expected = hashlib.sha256(GENESIS.encode() + _canonical(envelope)).hexdigest()
    assert record_hash(GENESIS, envelope) == expected


def test_record_hash_excludes_any_hash_key_already_present(envelope):
    """A record read back from the ledger carries its `hash`; re-hashing it
    must reproduce the same value or `verify()` could never be right."""
    signed = dict(envelope, hash="c" * 64)
    assert record_hash(GENESIS, signed) == record_hash(GENESIS, envelope)


def test_record_hash_chains_on_the_previous_hash(envelope):
    """The genesis link is 64 zeros; a record at any other position hashes
    differently for the same body, which is what makes an insert or a
    reorder detectable."""
    assert len(GENESIS) == 64 and set(GENESIS) == {"0"}
    other = record_hash("d" * 64, envelope)
    assert other != record_hash(GENESIS, envelope)


def test_record_hash_changes_when_any_envelope_field_changes(envelope):
    base_hash = record_hash(GENESIS, envelope)
    for field in envelope:
        moved = dict(envelope)
        moved[field] = 999_999 if isinstance(envelope[field], int) else "moved"
        assert record_hash(GENESIS, moved) != base_hash, field


def test_record_hash_is_hex_sha256(envelope):
    value = record_hash(GENESIS, envelope)
    assert len(value) == 64
    assert set(value) <= set("0123456789abcdef")


# ---------------------------------------------------------------------------
# reject_money_floats — the money rule, one owner (§5.4, §5.8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", MONEY_FIELDS)
def test_a_float_under_any_money_name_is_a_problem(field):
    problems = []
    reject_money_floats(problems, {field: 1.5}, "body")
    assert len(problems) == 1
    assert field in problems[0]


def test_a_float_under_any_other_name_is_a_legal_ratio():
    """§5.4/§5.5: dimensionless ratios ARE floats; only the money names
    are closed. A walk that refused every float would refuse a
    `confidence`."""
    problems = []
    reject_money_floats(
        problems, {"confidence": 0.61, "prediction": -0.9, "statistic": 1.5}, "body"
    )
    assert problems == []


def test_the_money_rule_reaches_any_depth():
    problems = []
    reject_money_floats(problems, {"detail": {"charges": {"fee": 0.25}}}, "body")
    assert problems and "body.detail.charges.fee" in problems[0]


def test_a_list_under_a_money_name_inherits_it():
    """`{"price": [1.0]}` is a price that is a float. The list carries the
    name down, which is the semantics the ledger's own suite pins and the
    reason both callers must share ONE walk."""
    problems = []
    reject_money_floats(problems, {"price": [1.0, 2]}, "body")
    assert len(problems) == 1
    assert "body.price[0]" in problems[0]


def test_a_list_under_a_ratio_name_does_not_inherit_money():
    problems = []
    reject_money_floats(problems, {"weights": [0.25, 0.75]}, "body")
    assert problems == []


def test_every_offending_float_is_reported_not_just_the_first():
    problems = []
    reject_money_floats(problems, {"fee": 0.1, "legs": [{"qty": 2.0}]}, "body")
    assert len(problems) == 2


def test_a_decimal_an_int_and_a_string_under_a_money_name_pass():
    problems = []
    reject_money_floats(problems, {"price": "1.50", "qty": 3, "fee": None}, "body")
    assert problems == []


# ---------------------------------------------------------------------------
# ms / UTC helpers
# ---------------------------------------------------------------------------


def test_now_ms_is_an_integer_millisecond_stamp():
    """Instants are epoch-ms ints, never floats (§5.4)."""
    before = int(time.time() * 1000)
    value = now_ms()
    after = int(time.time() * 1000)
    assert isinstance(value, int) and not isinstance(value, bool)
    assert before - 1000 <= value <= after + 1000


def test_utc_iso_and_parse_utc_ms_round_trip():
    ms = 1_757_030_400_123
    text = utc_iso(ms)
    assert isinstance(text, str)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parse_utc_ms(text) == ms


def test_parse_utc_ms_refuses_a_naive_stamp():
    """A stamp with no zone is a guess, and a guess in a ledger is a lie."""
    with pytest.raises(ProductionError):
        parse_utc_ms("2026-09-05T04:00:00")


def test_parse_utc_ms_refuses_a_malformed_stamp():
    with pytest.raises(ProductionError):
        parse_utc_ms("not-a-time")


def test_parse_utc_ms_accepts_an_explicit_offset():
    assert parse_utc_ms("2026-09-05T05:00:00+01:00") == parse_utc_ms(
        "2026-09-05T04:00:00+00:00"
    )


# ---------------------------------------------------------------------------
# Re-exports — one owner per rule (CLAUDE.md)
# ---------------------------------------------------------------------------


def test_reject_unknown_params_is_the_pipeline_function_itself():
    """Default-deny has ONE implementation. A copy here would drift the
    moment the engine loosens or tightens it."""
    from dskit.pipeline.node import reject_unknown_params as owner

    assert reject_unknown_params is owner


def test_the_assets_checkers_are_re_exported_by_identity():
    """The same re-export idiom `dskit/onboarding/base.py` uses (§5.0)."""
    from dskit.assets import base as assets_base
    from dskit.production import base as production_base

    for name in ("_check_str", "_check_dict", "_check_unknown", "_raise_if"):
        assert getattr(production_base, name) is getattr(assets_base, name), name


def test_the_money_walk_has_one_owner_that_both_callers_import():
    """`records.py` validating an opaque venue payload and `ledger.py`
    validating a record body must not each walk the rule: a second copy
    is how the two came to disagree about a list under a money key
    (CLAUDE.md, "a function is never repeated across modules")."""
    from dskit.production import base as production_base
    from dskit.production import ledger, records

    assert ledger.reject_money_floats is production_base.reject_money_floats
    assert records.reject_money_floats is production_base.reject_money_floats


def test_the_directory_barrier_has_one_owner_in_onboarding():
    """The ledger fsyncs its directory after CREATING a segment, which is
    not the rename `durable_write_json` already covers; it imports the
    onboarding barrier by its public name rather than reaching for a
    private one or copying the walk."""
    from dskit.onboarding import base as onboarding_base
    from dskit.production import ledger

    assert ledger.fsync_dir is onboarding_base.fsync_dir
    assert "fsync_dir" in onboarding_base.__all__


def test_the_re_exported_private_checkers_are_not_public_api():
    """They are re-exported for sibling modules, not exported: `__all__`
    plus the `_` prefix is the API contract."""
    from dskit.production import base as production_base

    assert production_base.__all__
    assert not [n for n in production_base.__all__ if n.startswith("_")]

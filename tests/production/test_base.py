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

from dskit.production.base import (
    ProductionError,
    Registry,
    canonical_bytes,
    canonical_hash,
    now_ms,
    parse_utc_ms,
    record_hash,
    reject_unknown_params,
    utc_iso,
)

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


def test_the_re_exported_private_checkers_are_not_public_api():
    """They are re-exported for sibling modules, not exported: `__all__`
    plus the `_` prefix is the API contract."""
    from dskit.production import base as production_base

    assert production_base.__all__
    assert not [n for n in production_base.__all__ if n.startswith("_")]

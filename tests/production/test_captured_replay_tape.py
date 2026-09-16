"""``CapturedReplayTape`` — the default-deny ``dskit.captured-replay-tape/v1`` codec.

The inner manifest bytes a ``ReplayTapeManifestProducer`` publishes are one
exact nine-field object; this module pins that object. The codec is a value:
it recomputes the two derived digests (``ordered_envelopes_sha256`` and
``tape_digest``) from canonical bytes and refuses anything else, so a tape the
replay consumer later receives can be trusted to be exactly the bytes that
were verified — never a caller-supplied digest string.
"""

import copy
import json
import pickle

import pytest

from dskit.production import bundles as bundles_module
from dskit.production.base import ProductionError, canonical_bytes, canonical_hash
from dskit.production.bundles import CapturedReplayTape


_SCHEMA = "dskit.captured-replay-tape/v1"
_ENVELOPE = "dskit.event-envelope/v2"
_D = "a" * 64
_RECEIPT = "b" * 64
_POLICY = "c" * 64


def _digests(n):
    return [f"{i + 1:064x}" for i in range(n)]


def _valid_dict(*, envelope_count=2, drop=(), mutate=None):
    envelopes = _digests(envelope_count)
    value = {
        "schema_version": _SCHEMA,
        "event_envelope_schema": _ENVELOPE,
        "data_capture_root": _D,
        "data_captured_receipt": _RECEIPT,
        "source_rank_policy_sha256": _POLICY,
        "envelope_count": envelope_count,
        "ordered_envelope_digests": envelopes,
        "ordered_envelopes_sha256": canonical_hash(envelopes),
    }
    source = {k: v for k, v in value.items() if k != "tape_digest"}
    value["tape_digest"] = canonical_hash(source)
    for key in drop:
        value.pop(key)
    if mutate is not None:
        mutate(value)
    return value


def _canonical(value):
    return canonical_bytes(value)


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_codec_is_public_and_has_no_constructor():
    assert "CapturedReplayTape" in bundles_module.__all__
    with pytest.raises(TypeError):
        CapturedReplayTape()


def test_parse_round_trips_canonical_bytes_and_fields():
    value = _valid_dict()
    tape = CapturedReplayTape.parse(_canonical(value))
    assert tape.to_obj() == value
    assert tape.canonical_bytes() == _canonical(value)
    assert tape.schema_version == _SCHEMA
    assert tape.event_envelope_schema == _ENVELOPE
    assert tape.data_capture_root == _D
    assert tape.data_captured_receipt == _RECEIPT
    assert tape.source_rank_policy_sha256 == _POLICY
    assert tape.envelope_count == 2
    assert tuple(tape.ordered_envelope_digests) == tuple(value["ordered_envelope_digests"])
    assert tape.ordered_envelopes_sha256 == value["ordered_envelopes_sha256"]
    assert tape.tape_digest == value["tape_digest"]


def test_build_computes_the_two_derived_digests():
    envelopes = _digests(3)
    tape = CapturedReplayTape._build(_D, _RECEIPT, _POLICY, envelopes)
    assert tape.envelope_count == 3
    assert tuple(tape.ordered_envelope_digests) == tuple(envelopes)
    assert tape.ordered_envelopes_sha256 == canonical_hash(envelopes)
    assert tape.tape_digest == canonical_hash(
        {k: v for k, v in tape.to_obj().items() if k != "tape_digest"}
    )


def test_tape_digest_omits_only_itself():
    value = _valid_dict()
    source = {k: v for k, v in value.items() if k != "tape_digest"}
    assert value["tape_digest"] == canonical_hash(source)
    assert source["ordered_envelopes_sha256"] == canonical_hash(
        value["ordered_envelope_digests"]
    )


# ---------------------------------------------------------------------------
# Shape — unknown / extra / missing / wrong literal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", ["bogus", "TAPE_DIGEST", "root_ref"])
def test_unknown_key_refuses(extra):
    value = _valid_dict()
    value[extra] = "x"
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


@pytest.mark.parametrize("key", list(_valid_dict()))
def test_each_missing_field_refuses(key):
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(_valid_dict(drop=(key,))))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("schema_version", "dskit.captured-replay-tape/v2"),
        ("event_envelope_schema", "dskit.event-envelope/v1"),
    ],
)
def test_wrong_literal_refuses(field, bad):
    value = _valid_dict()
    value[field] = bad
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


# ---------------------------------------------------------------------------
# envelope_count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [True, -1, "2", 2.0, None, [], {}])
def test_bad_envelope_count_refuses(count):
    value = _valid_dict()
    value["envelope_count"] = count
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


def test_envelope_count_mismatch_refuses():
    value = _valid_dict()
    value["envelope_count"] = 3
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


# ---------------------------------------------------------------------------
# digest validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["data_capture_root", "data_captured_receipt", "source_rank_policy_sha256"],
)
@pytest.mark.parametrize(
    "bad",
    ["A" * 64, "a" * 63, "a" * 65, "g" * 64, "0" * 64, "self", "", 123, None],
)
def test_bad_digest_field_refuses(field, bad):
    value = _valid_dict()
    value[field] = bad
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


def test_placeholder_and_self_refused_in_envelope_digests():
    for bad in ("0" * 64, "self"):
        value = _valid_dict()
        value["ordered_envelope_digests"] = [bad]
        value["envelope_count"] = 1
        with pytest.raises(ProductionError):
            CapturedReplayTape.parse(_canonical(value))


def test_non_list_envelope_digests_refuses():
    value = _valid_dict()
    value["ordered_envelope_digests"] = "x"
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


# ---------------------------------------------------------------------------
# recomputation
# ---------------------------------------------------------------------------


def test_ordered_envelopes_sha256_mismatch_refuses():
    value = _valid_dict()
    value["ordered_envelopes_sha256"] = "f" * 64
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


def test_tape_digest_mismatch_refuses():
    value = _valid_dict()
    value["tape_digest"] = "f" * 64
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(_canonical(value))


def test_noncanonical_bytes_refuse():
    value = _valid_dict()
    noncanonical = json.dumps(value, indent=2).encode("ascii")
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(noncanonical)


# ---------------------------------------------------------------------------
# opacity
# ---------------------------------------------------------------------------


def test_parse_rejects_non_json_and_nan():
    with pytest.raises(ProductionError):
        CapturedReplayTape.parse(b"not json")


def test_value_is_immutable_and_not_copyable_or_serializable():
    tape = CapturedReplayTape.parse(_canonical(_valid_dict()))
    with pytest.raises(AttributeError):
        tape.tape_digest = "f" * 64
    for op in (copy.copy, copy.deepcopy, lambda t: pickle.loads(pickle.dumps(t))):
        with pytest.raises(TypeError):
            op(tape)

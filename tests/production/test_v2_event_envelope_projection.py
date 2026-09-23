"""ADR-0171: bounded pure raw-event/v2 to event-envelope/v2 projection."""

import ast
import builtins
import hashlib
import inspect
import os
from pathlib import Path
import random
import textwrap
import time
from types import MappingProxyType

import pytest

import dskit.production
from dskit.pipeline.event_wire import RAW_EVENT_FIELDS
from dskit.production import bundles


_RAW_SCHEMA = "dskit.raw-event/v2"
_POLICY_SCHEMA = "dskit.source-rank-policy/v1"


class _IntSubclass(int):
    pass


class _StrSubclass(str):
    pass


class _EqualSourceIdKey:
    def __hash__(self):
        return hash("source_id")

    def __eq__(self, other):
        return other == "source_id"


class _RaisingEqualKey:
    def __init__(self, target):
        self.target = target

    def __hash__(self):
        return hash(self.target)

    def __eq__(self, _other):
        raise RuntimeError("hostile equality callback")


class _ActiveDict(dict):
    def __init__(self, value):
        super().__init__(value)
        self.calls = 0

    def _called(self):
        self.calls += 1
        raise RuntimeError("active mapping callback")

    def __iter__(self):
        return self._called()

    def __getitem__(self, key):
        return self._called()

    def get(self, key, default=None):
        return self._called()

    def keys(self):
        return self._called()

    def items(self):
        return self._called()

    def values(self):
        return self._called()


def _event(**changes):
    value = {
        "schema_version": _RAW_SCHEMA,
        "source_id": "alpha",
        "event_id": "event-0",
        "source_sequence": 0,
        "availability_ms": 1_000,
        "payload_sha256": hashlib.sha256(b"payload-0").hexdigest(),
        "exchange_ms": 900,
        "receive_ms": 950,
        "source_provenance_tag": "synthetic:fixture-a",
        "source_timezone_tag": "America/New_York",
        "correction_position": 0,
        "corrects_event_id": None,
    }
    value.update(changes)
    return MappingProxyType(value)


def _policy(rows=(("alpha", 0), ("beta", 1)), *, digest=None, extra=None):
    source_values = [{"source_id": source_id, "rank": rank} for source_id, rank in rows]
    preimage = {"schema_version": _POLICY_SCHEMA, "sources": source_values}
    actual = hashlib.sha256(bundles._canonical_bytes(preimage)).hexdigest()
    value = {
        "schema_version": _POLICY_SCHEMA,
        "sources": tuple(MappingProxyType(row) for row in source_values),
        "policy_sha256": actual if digest is None else digest,
    }
    if extra is not None:
        value[extra] = "refuse"
    return MappingProxyType(value)


def _project(events, policy=None):
    return bundles._project_v2_event_envelopes(
        events, _policy() if policy is None else policy
    )


def test_projection_is_private_positional_only_and_not_reexported():
    function = bundles._project_v2_event_envelopes
    signature = inspect.signature(function)
    assert tuple(signature.parameters) == ("events", "source_rank_policy")
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        for parameter in signature.parameters.values()
    )
    assert "_project_v2_event_envelopes" not in bundles.__all__
    assert not hasattr(dskit.production, "_project_v2_event_envelopes")
    with pytest.raises(TypeError):
        function(events=(_event(),), source_rank_policy=_policy())


def test_projection_derives_orders_and_round_trips_every_field():
    policy = _policy()
    alpha_original = _event()
    beta = _event(
        source_id="beta",
        event_id="beta-0",
        source_sequence=0,
        payload_sha256=hashlib.sha256(b"beta").hexdigest(),
    )
    alpha_correction = _event(
        event_id="event-1",
        source_sequence=1,
        availability_ms=1_100,
        payload_sha256=hashlib.sha256(b"payload-1").hexdigest(),
        exchange_ms=1_000,
        receive_ms=1_050,
        correction_position=1,
        corrects_event_id="event-0",
    )
    alpha_second = _event(
        event_id="event-2",
        source_sequence=2,
        availability_ms=1_200,
        payload_sha256=hashlib.sha256(b"payload-2").hexdigest(),
        exchange_ms=1_100,
        receive_ms=1_150,
        correction_position=2,
        corrects_event_id="event-1",
    )

    output = _project((alpha_second, beta, alpha_correction, alpha_original), policy)
    assert type(output) is tuple
    assert all(type(raw) is bytes for raw in output)
    parsed = tuple(bundles._parse_event_envelope(raw) for raw in output)
    assert [row["event_id"] for row in parsed] == [
        "event-0", "beta-0", "event-1", "event-2"
    ]
    assert [row["source_rank"] for row in parsed] == [0, 1, 0, 0]
    assert all(row["schema_version"] == "dskit.event-envelope/v2" for row in parsed)
    assert all(
        row["source_rank_policy_sha256"] == policy["policy_sha256"]
        for row in parsed
    )
    assert parsed[0]["prior_envelope_sha256"] is None
    assert parsed[1]["prior_envelope_sha256"] is None
    assert parsed[2]["prior_envelope_sha256"] == hashlib.sha256(output[0]).hexdigest()
    assert parsed[3]["prior_envelope_sha256"] == hashlib.sha256(output[2]).hexdigest()

    raw_by_id = {row["event_id"]: row for row in (
        alpha_original, beta, alpha_correction, alpha_second
    )}
    event_fields = set(RAW_EVENT_FIELDS[_RAW_SCHEMA]) - {"schema_version"}
    for envelope in parsed:
        event = raw_by_id[envelope["event_id"]]
        for field in event_fields:
            assert envelope[field] == event[field]


def test_projection_is_permutation_invariant_and_uses_exact_six_term_order():
    events = (
        _event(event_id="z", source_sequence=2, availability_ms=5,
               payload_sha256="f" * 64),
        _event(event_id="a", source_sequence=2, availability_ms=5,
               payload_sha256="e" * 64),
        _event(source_id="beta", event_id="b", source_sequence=1,
               availability_ms=5, payload_sha256="d" * 64),
        _event(event_id="early", source_sequence=99, availability_ms=4,
               payload_sha256="c" * 64),
    )
    forward = _project(events)
    reverse = _project(tuple(reversed(events)))
    assert forward == reverse
    parsed = [bundles._parse_event_envelope(raw) for raw in forward]
    assert [row["event_id"] for row in parsed] == ["early", "a", "z", "b"]
    assert [bundles._event_envelope_order_key(row) for row in parsed] == sorted(
        bundles._event_envelope_order_key(row) for row in parsed
    )


@pytest.mark.parametrize("events", [(), [], {}, iter((_event(),))])
def test_projection_refuses_every_nonclosed_events_container(events):
    with pytest.raises(bundles.ProductionError):
        _project(events)


def test_projection_refuses_non_mapping_proxy_event_members():
    with pytest.raises(bundles.ProductionError):
        _project((dict(_event()),))


def test_projection_refuses_every_missing_raw_field_and_unknown_fields():
    base = dict(_event())
    for field in RAW_EVENT_FIELDS[_RAW_SCHEMA]:
        malformed = dict(base)
        malformed.pop(field)
        with pytest.raises(bundles.ProductionError):
            _project((MappingProxyType(malformed),))
    malformed = dict(base, unexpected="no")
    with pytest.raises(bundles.ProductionError):
        _project((MappingProxyType(malformed),))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "dskit.raw-event/v1"},
        {"schema_version": _StrSubclass(_RAW_SCHEMA)},
        {"source_id": ""},
        {"source_id": _StrSubclass("alpha")},
        {"source_id": 1},
        {"event_id": ""},
        {"event_id": _StrSubclass("event-0")},
        {"event_id": 1},
        {"source_sequence": True},
        {"source_sequence": _IntSubclass(0)},
        {"source_sequence": -1},
        {"availability_ms": True},
        {"availability_ms": _IntSubclass(1_000)},
        {"availability_ms": -1},
        {"payload_sha256": "A" * 64},
        {"payload_sha256": _StrSubclass("a" * 64)},
        {"payload_sha256": "not-a-digest"},
        {"exchange_ms": True},
        {"exchange_ms": _IntSubclass(900)},
        {"exchange_ms": -1},
        {"receive_ms": True},
        {"receive_ms": -1},
        {"exchange_ms": 951, "receive_ms": 950},
        {"source_provenance_tag": ""},
        {"source_provenance_tag": _StrSubclass("synthetic:fixture-a")},
        {"source_timezone_tag": ""},
        {"source_timezone_tag": _StrSubclass("America/New_York")},
        {"correction_position": True},
        {"correction_position": _IntSubclass(0)},
        {"correction_position": -1},
        {"corrects_event_id": 1},
        {"corrects_event_id": "event-x"},
        {"correction_position": 1, "corrects_event_id": None},
        {"correction_position": 1, "corrects_event_id": "event-0"},
    ],
)
def test_projection_refuses_mistyped_negative_digest_and_correction_events(changes):
    with pytest.raises(bundles.ProductionError):
        _project((_event(**changes),))


def test_projection_refuses_duplicate_event_ids():
    with pytest.raises(bundles.ProductionError):
        _project((_event(), _event(source_sequence=1)))


@pytest.mark.parametrize(
    "events",
    [
        (_event(event_id="correction", correction_position=1,
                corrects_event_id="missing"),),
        (_event(), _event(event_id="skip", source_sequence=1,
                          correction_position=2, corrects_event_id="event-0")),
        (_event(event_id="later", availability_ms=2_000),
         _event(event_id="first", availability_ms=1_000, correction_position=1,
                corrects_event_id="later")),
    ],
)
def test_projection_refuses_missing_skipped_and_later_correction_targets(events):
    with pytest.raises(bundles.ProductionError):
        _project(events)


@pytest.mark.parametrize(
    "policy",
    [
        {},
        MappingProxyType({}),
        _policy(digest="0" * 64),
        _policy(rows=(("beta", 0), ("alpha", 1))),
        _policy(rows=(("alpha", 0), ("alpha", 1))),
        _policy(rows=(("alpha", 1), ("beta", 0))),
        _policy(rows=()),
        _policy(extra="unknown"),
    ],
)
def test_projection_refuses_malformed_forged_reordered_and_gapped_policy(policy):
    with pytest.raises(bundles.ProductionError):
        _project((_event(),), policy)


def test_projection_refuses_every_missing_policy_field_and_wrong_schema():
    good = _policy()
    for field in ("schema_version", "sources", "policy_sha256"):
        malformed = dict(good)
        malformed.pop(field)
        with pytest.raises(bundles.ProductionError):
            _project((_event(),), MappingProxyType(malformed))
    for change in (
        {"schema_version": "dskit.source-rank-policy/v0"},
        {"schema_version": _StrSubclass(_POLICY_SCHEMA)},
        {"policy_sha256": "A" * 64},
        {"policy_sha256": _StrSubclass(good["policy_sha256"])},
    ):
        with pytest.raises(bundles.ProductionError):
            _project((_event(),), MappingProxyType({**dict(good), **change}))


def test_projection_accumulates_heterogeneous_unknown_keys_as_production_error():
    event = MappingProxyType({**dict(_event()), "extra": 1, 2: "extra"})
    with pytest.raises(bundles.ProductionError):
        _project((event,))

    policy = MappingProxyType({**dict(_policy()), "extra": 1, 2: "extra"})
    with pytest.raises(bundles.ProductionError):
        _project((_event(),), policy)

    good = _policy()
    row = MappingProxyType({
        "source_id": "alpha", "rank": 0, "extra": 1, 2: "extra"
    })
    malformed = MappingProxyType({
        "schema_version": _POLICY_SCHEMA,
        "sources": (row,),
        "policy_sha256": good["policy_sha256"],
    })
    with pytest.raises(bundles.ProductionError):
        _project((_event(),), malformed)


def test_projection_refuses_keys_that_masquerade_as_exact_schema_fields():
    for false_key in (_StrSubclass("source_id"), _EqualSourceIdKey()):
        event = MappingProxyType({
            (false_key if key == "source_id" else key): value
            for key, value in dict(_event()).items()
        })
        with pytest.raises(bundles.ProductionError):
            _project((event,))

    policy = _policy()
    false_policy = MappingProxyType({
        (_StrSubclass("schema_version") if key == "schema_version" else key): value
        for key, value in dict(policy).items()
    })
    with pytest.raises(bundles.ProductionError):
        _project((_event(),), false_policy)

    row = MappingProxyType({
        _StrSubclass("source_id"): "alpha",
        "rank": 0,
    })
    false_row_policy = MappingProxyType({
        "schema_version": _POLICY_SCHEMA,
        "sources": (row,),
        "policy_sha256": hashlib.sha256(bundles._canonical_bytes({
            "schema_version": _POLICY_SCHEMA,
            "sources": [{"source_id": "alpha", "rank": 0}],
        })).hexdigest(),
    })
    with pytest.raises(bundles.ProductionError):
        _project((_event(),), false_row_policy)


@pytest.mark.parametrize("level", ["event", "policy", "source"])
def test_projection_refuses_hostile_equal_keys_without_invoking_them(level):
    if level == "event":
        value = dict(_event())
        original = value.pop("source_id")
        value[_RaisingEqualKey("source_id")] = original
        events = (MappingProxyType(value),)
        policy = _policy()
    elif level == "policy":
        value = dict(_policy())
        original = value.pop("schema_version")
        value[_RaisingEqualKey("schema_version")] = original
        events = (_event(),)
        policy = MappingProxyType(value)
    else:
        row = {"rank": 0, _RaisingEqualKey("source_id"): "alpha"}
        good = _policy(rows=(("alpha", 0),))
        policy = MappingProxyType({
            "schema_version": _POLICY_SCHEMA,
            "sources": (MappingProxyType(row),),
            "policy_sha256": good["policy_sha256"],
        })
        events = (_event(),)

    with pytest.raises(bundles.ProductionError):
        _project(events, policy)


@pytest.mark.parametrize("level", ["event", "policy", "source"])
def test_projection_refuses_active_mapping_backing_without_callbacks(level):
    if level == "event":
        active = _ActiveDict(dict(_event()))
        events = (MappingProxyType(active),)
        policy = _policy()
    elif level == "policy":
        active = _ActiveDict(dict(_policy()))
        events = (_event(),)
        policy = MappingProxyType(active)
    else:
        active = _ActiveDict({"source_id": "alpha", "rank": 0})
        good = _policy(rows=(("alpha", 0),))
        policy = MappingProxyType({
            "schema_version": _POLICY_SCHEMA,
            "sources": (MappingProxyType(active),),
            "policy_sha256": good["policy_sha256"],
        })
        events = (_event(),)

    with pytest.raises(bundles.ProductionError):
        _project(events, policy)
    assert active.calls == 0


def test_projection_refuses_policy_container_entry_and_field_shape_defects():
    good = _policy()
    with pytest.raises(bundles.ProductionError):
        _project((_event(),), MappingProxyType({**dict(good), "sources": list(good["sources"])}))
    with pytest.raises(bundles.ProductionError):
        _project((_event(),), MappingProxyType({**dict(good), "sources": (dict(good["sources"][0]),)}))
    for row in (
        MappingProxyType({"source_id": "alpha"}),
        MappingProxyType({"source_id": "alpha", "rank": 0, "extra": 1}),
        MappingProxyType({"source_id": "", "rank": 0}),
        MappingProxyType({"source_id": _StrSubclass("alpha"), "rank": 0}),
        MappingProxyType({"source_id": "alpha", "rank": True}),
        MappingProxyType({"source_id": "alpha", "rank": _IntSubclass(0)}),
    ):
        value = MappingProxyType({
            "schema_version": _POLICY_SCHEMA,
            "sources": (row,),
            "policy_sha256": good["policy_sha256"],
        })
        with pytest.raises(bundles.ProductionError):
            _project((_event(),), value)


def test_projection_refuses_event_source_absent_from_policy():
    with pytest.raises(bundles.ProductionError):
        _project((_event(source_id="ghost"),))


def test_projection_does_not_mutate_inputs():
    events = (_event(),)
    policy = _policy()
    event_before = dict(events[0])
    policy_before = {
        "schema_version": policy["schema_version"],
        "sources": tuple(dict(row) for row in policy["sources"]),
        "policy_sha256": policy["policy_sha256"],
    }
    _project(events, policy)
    assert dict(events[0]) == event_before
    assert tuple(dict(row) for row in policy["sources"]) == policy_before["sources"]
    assert policy["policy_sha256"] == policy_before["policy_sha256"]


def test_projection_ast_and_runtime_are_pure(monkeypatch):
    function = bundles._project_v2_event_envelopes
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    banned_names = {"open", "print", "getenv", "urandom", "sleep"}
    assert not {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } & banned_names
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))

    def refused(*_args, **_kwargs):
        raise AssertionError("ambient effect attempted")

    monkeypatch.setattr(builtins, "open", refused)
    monkeypatch.setattr(os, "getenv", refused)
    monkeypatch.setattr(time, "time", refused)
    monkeypatch.setattr(random, "random", refused)
    monkeypatch.setattr(Path, "read_bytes", refused)
    assert len(_project((_event(),))) == 1


def test_projection_calls_existing_validator_encoder_and_parser(monkeypatch):
    calls = {"check": 0, "encode": 0, "parse": 0}
    original_check = bundles._check_event_envelope
    original_encode = bundles._canonical_bytes
    original_parse = bundles._parse_event_envelope

    def check(value):
        calls["check"] += 1
        return original_check(value)

    def encode(value):
        calls["encode"] += 1
        return original_encode(value)

    def parse(raw):
        calls["parse"] += 1
        return original_parse(raw)

    monkeypatch.setattr(bundles, "_check_event_envelope", check)
    monkeypatch.setattr(bundles, "_canonical_bytes", encode)
    monkeypatch.setattr(bundles, "_parse_event_envelope", parse)
    assert len(_project((_event(),))) == 1
    assert calls["check"] >= 1
    assert calls["encode"] >= 2
    assert calls["parse"] == 2

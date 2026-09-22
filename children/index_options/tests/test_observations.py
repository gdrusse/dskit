"""Thin projections keep the parent's reader and refuse unsupported domain rows."""

import pytest

from index_options.observations import ContractRows, QuoteRows, SettlementRows


@pytest.mark.parametrize("cls", [ContractRows, QuoteRows, SettlementRows])
def test_knobs_are_narrowed_and_serving_refused(cls):
    assert cls._PARAMS == ("root", "source", "as_of_acquisition_ms")
    assert cls.serving_effect({}, {}) == "forbidden"

import copy
import json
import os
from datetime import datetime, timedelta, timezone

from dskit.onboarding import AssetError, load_suite, run_suite
from dskit.pipeline.base import ConfigError


@pytest.mark.parametrize("cls, stream, count, keys", [
    (ContractRows, "contracts", 4, ("corpus_id", "contract_id", "row_version")),
    (QuoteRows, "quotes", 4, ("corpus_id", "contract_id", "effective_at", "row_version")),
    (SettlementRows, "settlements", 3, ("corpus_id", "settlement_id", "expiry", "row_version")),
])
def test_accessors_and_real_projection(rows, store_factory, cls, stream, count, keys):
    store = store_factory(rows)
    acquired = store.acquire(stream)
    assert acquired["records"] == count
    node = cls(stream, store.node_params())
    assert node.stream() == stream
    assert node.key_fields() == keys
    assert node.ts_field() == "effective_at"
    assert node.ts_unit() == "iso"
    assert node.ts_out() == "observation_ms"
    assert node.shared_fields() == ()
    assert node.since_ms() is None
    result = node.run(None, {})["records"]
    assert len(result) == count
    assert all(type(row["observation_ms"]) is int for row in result)
    assert all(row["known_at_basis"] == "synthetic" for row in result)
    assert len({tuple(row[k] for k in keys) for row in rows[stream]}) == count


@pytest.mark.parametrize("cls", [ContractRows, QuoteRows, SettlementRows])
@pytest.mark.parametrize("knob", [
    "stream", "key_fields", "ts_field", "ts_unit", "ts_out", "shared_fields", "since_ms", "surprise",
])
def test_fixed_or_unknown_knobs_refuse(cls, knob, tmp_path):
    with pytest.raises(ConfigError, match=knob):
        cls("rows", {"root": str(tmp_path), "source": "index-fixture", knob: "wrong"})


@pytest.mark.parametrize("cls, stream", [
    (ContractRows, "contracts"), (QuoteRows, "quotes"), (SettlementRows, "settlements"),
])
@pytest.mark.parametrize("field, value", [
    ("provenance", "real"), ("known_at_basis", "guessed"),
    ("known_at", "2026-01-01"), ("schema_version", "unknown"),
])
def test_domain_projection_refuses_all_stream_families(rows, cls, stream, field, value):
    rows[stream][0][field] = value
    node = cls(stream, {"root": ".", "source": "index-fixture"})
    with pytest.raises(ValueError):
        node.project(rows[stream])


@pytest.mark.parametrize("multiplier", [0, -100, True])
def test_contract_projection_refuses_nonpositive_or_bool_multiplier(rows, multiplier):
    rows["contracts"][0]["multiplier"] = multiplier
    with pytest.raises(ValueError, match="multiplier"):
        ContractRows("contracts", {"root": ".", "source": "index-fixture"}).project(rows["contracts"])


@pytest.mark.parametrize("cls, stream", [
    (ContractRows, "contracts"), (QuoteRows, "quotes"), (SettlementRows, "settlements"),
])
def test_parent_refuses_existing_output_timestamp(rows, store_factory, cls, stream):
    rows[stream][0]["observation_ms"] = 1
    store = store_factory(rows)
    store.acquire(stream)
    with pytest.raises(AssetError, match="observation_ms"):
        cls(stream, store.node_params()).fingerprint()


def test_parent_identical_repeat_and_conflicting_winning_tie(rows, store_factory):
    rows["quotes"].append(copy.deepcopy(rows["quotes"][0]))
    store = store_factory(rows)
    store.acquire("quotes")
    assert len(QuoteRows("q", store.node_params()).run(None, {})["records"]) == 4
    rows["quotes"][-1]["ask"] = "2.61"
    conflicting = store_factory(rows, "conflicting")
    conflicting.acquire("quotes")
    with pytest.raises(AssetError):
        QuoteRows("q", conflicting.node_params()).fingerprint()


def test_read_vintage_and_later_winner_do_not_rewrite_known_at(rows, store_factory, monkeypatch):
    import dskit.onboarding.acquire as acquisition

    store = store_factory(rows)
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-01T00:00:00Z")
    store.acquire("quotes")
    first = QuoteRows("q", store.node_params())
    before = first.fingerprint()
    assert next(r for r in first.run(None, {})["records"] if r["contract_id"] == "p480")["ask"] == "2.60"
    rows["quotes"][0]["ask"] = "2.61"
    store.write("quotes", rows["quotes"])
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-02T00:00:00Z")
    store.acquire("quotes", "live")  # separate parent cursor, not backdated resume capture
    midpoint = (datetime(2026, 6, 1, 12, tzinfo=timezone.utc) -
                datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)
    old = QuoteRows("q", store.node_params(as_of_acquisition_ms=midpoint)).run(None, {})["records"]
    new_node = QuoteRows("q", store.node_params())
    new = new_node.run(None, {})["records"]
    assert next(r for r in old if r["contract_id"] == "p480")["ask"] == "2.60"
    assert next(r for r in new if r["contract_id"] == "p480")["ask"] == "2.61"
    assert {r["known_at"] for r in new} == {"2026-01-16T20:45:00Z"}
    assert first.fingerprint() == before
    assert new_node.fingerprint() != before
    # Event/known_at precede this cutoff, but actual ingestion does not.
    assert QuoteRows("q", store.node_params(as_of_acquisition_ms=midpoint - 86400000)).run(
        None, {}
    )["records"] == []


def test_snapshot_is_frozen_but_fresh_instance_hashes_changed_bytes(rows, store_factory):
    store = store_factory(rows)
    store.acquire("quotes")
    node = QuoteRows("q", store.node_params())
    fingerprint = node.fingerprint()
    frozen = copy.deepcopy(node.run(None, {})["records"])
    paths = store.members("quotes")
    assert len(paths) == 1
    path = paths[0]
    stat = path.stat()
    envelopes = [json.loads(line) for line in path.read_text().splitlines()]
    envelopes[0]["data"]["ask"] = "2.61"
    path.write_text("".join(json.dumps(row) + "\n" for row in envelopes))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert node.fingerprint() == fingerprint
    assert node.run(None, {})["records"] == frozen
    assert QuoteRows("q", store.node_params()).fingerprint() != fingerprint


def test_revisions_stay_distinct_and_strict_cursor_is_not_revision_capture(rows, store_factory):
    rows["quotes"].append({**rows["quotes"][0], "row_version": "v2", "ask": "2.61"})
    store = store_factory(rows)
    store.acquire("quotes")
    output = QuoteRows("q", store.node_params()).run(None, {})["records"]
    assert len(output) == 5
    rows["quotes"].append({**rows["quotes"][0], "row_version": "v3"})
    store.write("quotes", rows["quotes"])
    assert store.acquire("quotes")["records"] == 0
    assert len(QuoteRows("q", store.node_params()).run(None, {})["records"]) == 5


def test_suite_block_is_not_a_hidden_read_gate(rows, store_factory, child_root, monkeypatch):
    import dskit.onboarding.acquire as acquisition

    original = copy.deepcopy(rows["quotes"])
    rows["quotes"][0]["provenance"] = "unknown"
    store = store_factory(rows)
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-01T00:00:00Z")
    acquired = store.acquire("quotes")
    suite = load_suite(str(child_root / "configs/suite-fixture.json"))
    assert run_suite(store.root, store.registry, suite, acquired["snapshot"])["gating"] == "block"
    with pytest.raises(ValueError, match="synthetic"):
        QuoteRows("q", store.node_params()).fingerprint()
    store.write("quotes", original)
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-02T00:00:00Z")
    store.acquire("quotes", "live")
    # No suite was run on the new snapshot: valid winning rows read anyway.
    assert len(QuoteRows("q", store.node_params()).run(None, {})["records"]) == 4


def test_ordinary_subclass_inherits_domain_rules(rows, store_factory):
    class ResearchQuotes(QuoteRows):
        pass

    store = store_factory(rows)
    store.acquire("quotes")
    assert len(ResearchQuotes("q", store.node_params()).run(None, {})["records"]) == 4
    rows["quotes"][0]["condition_valid"] = False
    with pytest.raises(ValueError):
        ResearchQuotes("q", store.node_params()).project(rows["quotes"])

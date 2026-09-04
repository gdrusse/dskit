"""The observations pack's suite: behaviour + conformance over a REAL
acquired root (ADR-0077).

The fixture root is built the way a child builds one —
``OnboardingRoot.create``, a registered ``localfiles`` source,
``run_acquisition`` — so the kind is tested over acquire's own on-disk
shape, never a hand-written imitation of it. The probe's ``grow()`` is a
SECOND acquisition (the live puller landing rows between resolve and
execute); its ``move()`` rewrites one value inside the committed stream
member in place, mtimes restored, so only a content-reading fingerprint
can notice.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from dskit.onboarding import AssetError, OnboardingRoot, run_acquisition
from dskit.onboarding.codec import resolve_stream_file
from dskit.onboarding.observations import stream_dir
from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs.observations import (
    DEFAULT_TS_OUT,
    DEFAULT_TS_UNIT,
    NODE_KINDS,
    TS_UNITS,
    ObservationRows,
    register,
)
from dskit.pipeline.node import DEFAULT_NODE_KINDS, NodeContext, NodeKindRegistry
from dskit.pipeline.records import ASOF_FIELD

SOURCE = "local"
STREAM = "bars"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ms(day):
    """Exact epoch ms of an ISO date at midnight UTC — integer arithmetic,
    never the float ``timestamp()`` round-trip the seam refuses."""
    at = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return (at - _EPOCH) // timedelta(milliseconds=1)


#: The source rows. JSONL, so ``ts`` stays an INT on disk (the
#: ``ts_unit: "ms"`` path needs a number the connector never stringified);
#: ``date`` is the connector's effective field AND the ``iso`` path's
#: ``ts_field``. Unsorted on purpose: the read seam orders.
ROWS = (
    {"date": "2026-01-03", "sym": "B", "value": 4.0},
    {"date": "2026-01-02", "sym": "A", "value": 1.0},
    {"date": "2026-01-03", "sym": "A", "value": 3.0},
    {"date": "2026-01-02", "sym": "B", "value": 2.0},
)
#: What the live puller lands: a later day, so the cursor admits it.
GROWTH = {"date": "2026-01-04", "sym": "A", "value": 5.0}


def _with_ts(row):
    return {**row, "ts": _ms(row["date"])}


def _data_file(tmp_path, name="ob"):
    return tmp_path / f"{name}-data" / f"{STREAM}.jsonl"


def _write_rows(path, rows, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(_with_ts(row), sort_keys=True) + "\n")


def _acquire(root):
    return run_acquisition(root, root.registry(), SOURCE, STREAM, "backfill")


def _root(tmp_path, rows=ROWS, name="ob"):
    """The fixture root: created, registered and acquired ONCE per
    ``tmp_path``; a later call reopens it (the conformance suite asks
    for its probes several times within one test)."""
    path = tmp_path / name
    if os.path.isfile(path / "store" / "store.json"):
        return OnboardingRoot(str(path))
    _write_rows(_data_file(tmp_path, name), rows)
    root = OnboardingRoot.create(str(path))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": SOURCE,
        "catalog_source": f"{SOURCE}-src",
        "connector": "localfiles",
        "config": {
            "path": str(_data_file(tmp_path, name).parent),
            "effective_field": "date",
        },
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    out = run_acquisition(root, registry, SOURCE, STREAM, "backfill")
    assert out["records"] == len(rows)
    return root


def _params(root_dir, **over):
    params = {
        "root": root_dir, "source": SOURCE, "stream": STREAM,
        "key_fields": ["sym", "date"], "ts_field": "date",
    }
    params.update(over)
    return {k: v for k, v in params.items() if v is not ...}


def _ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-10", run_dir=str(tmp_path / "run"))


def _members(root):
    """Every committed member of the stream, in acquisition-dir name order."""
    base = stream_dir(root.root, SOURCE)
    found = []
    for name in sorted(os.listdir(base)):
        path = resolve_stream_file(os.path.join(base, name), STREAM)
        if path is not None:
            found.append(path)
    return found


def _rewrite_first_value(path, delta=1.5):
    """Bump one row's ``value`` IN PLACE — same keys, same count, same mtime."""
    stat = os.stat(path)
    with open(path, encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    lines[0]["data"]["value"] = lines[0]["data"]["value"] + delta
    with open(path, "w", encoding="utf-8") as fh:
        for row in lines:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    os.utime(path, (stat.st_atime, stat.st_mtime))


@pytest.fixture
def acquired(tmp_path):
    """A fresh root holding ``ROWS`` in one acquisition."""
    return _root(tmp_path)


# ---------------------------------------------------------------------------
# Params — default-deny, the required four, the closed unit vocabulary
# ---------------------------------------------------------------------------


class TestParams:
    def test_the_reference_params_validate_clean(self):
        assert ObservationRows.validate_params(_params("/ob")) == []
        assert ObservationRows.validate_params(
            _params("/ob", ts_field=..., since_ms=...)
        ) == []

    @pytest.mark.parametrize("missing", ["root", "source", "stream", "key_fields"])
    def test_the_required_knobs_refuse_by_name(self, missing):
        params = {k: v for k, v in _params("/ob").items() if k != missing}
        problems = ObservationRows.validate_params(params)
        assert any(missing in p for p in problems), problems

    @pytest.mark.parametrize("keys", [[], "sym", ["sym", 1], ["sym", ""], None, {}])
    def test_key_fields_must_be_a_non_empty_list_of_names(self, keys):
        problems = ObservationRows.validate_params(_params("/ob", key_fields=keys))
        assert any("key_fields" in p for p in problems), problems

    def test_unknown_knobs_are_refused_by_name(self):
        problems = ObservationRows.validate_params(_params("/ob", key_field=["sym"]))
        assert any("key_field" in p for p in problems)
        with pytest.raises(ConfigError, match="key_field"):
            ObservationRows("obs", _params("/ob", key_field=["sym"]))

    def test_the_unit_vocabulary_and_defaults_are_the_documented_ones(self):
        # Deliberate restatement: documents spell these, so the test does
        # not read them from the module it checks.
        assert TS_UNITS == ("iso", "ms")
        assert DEFAULT_TS_UNIT == "iso"
        assert DEFAULT_TS_OUT == "asof_ms"
        # ...and the stamp field IS the envelope's decision-instant name,
        # imported — the field this kind writes is the one splits cut on.
        assert DEFAULT_TS_OUT is ASOF_FIELD
        for unit in TS_UNITS:
            assert ObservationRows.validate_params(_params("/ob", ts_unit=unit)) == []

    @pytest.mark.parametrize("unit", ["s", "ISO", "", None, 1, ["ms"]])
    def test_ts_unit_is_a_closed_vocabulary(self, unit):
        problems = ObservationRows.validate_params(_params("/ob", ts_unit=unit))
        assert any("ts_unit" in p for p in problems), problems

    def test_since_ms_needs_a_ts_field_and_a_non_negative_int(self):
        problems = ObservationRows.validate_params(
            _params("/ob", ts_field=..., since_ms=5)
        )
        assert any("since_ms" in p and "ts_field" in p for p in problems), problems
        for bad in (-1, True, 1.5, "5"):
            problems = ObservationRows.validate_params(_params("/ob", since_ms=bad))
            assert any("since_ms" in p for p in problems), (bad, problems)
        assert ObservationRows.validate_params(_params("/ob", since_ms=0)) == []

    @pytest.mark.parametrize("bad", [None, "", 5, []])
    def test_ts_field_and_ts_out_must_be_names(self, bad):
        if bad is not None:
            problems = ObservationRows.validate_params(_params("/ob", ts_field=bad))
            assert any("ts_field" in p for p in problems), problems
        problems = ObservationRows.validate_params(_params("/ob", ts_out=bad))
        assert any("ts_out" in p for p in problems), problems

    @pytest.mark.parametrize("bad", ["sym", [1], None, {"sym": 1}])
    def test_shared_fields_must_be_a_list_of_names(self, bad):
        problems = ObservationRows.validate_params(_params("/ob", shared_fields=bad))
        assert any("shared_fields" in p for p in problems), problems


# ---------------------------------------------------------------------------
# The scan — iso vs ms, since_ms, ts_out, data_edge, fingerprint, memo
# ---------------------------------------------------------------------------


class TestScan:
    def test_iso_stamps_asof_ms_and_orders_by_instant_then_key(self, acquired, tmp_path):
        out = ObservationRows("obs", _params(acquired.root)).run(_ctx(tmp_path), {})
        records = out["records"]
        assert [(r["sym"], r["date"], r["value"]) for r in records] == [
            ("A", "2026-01-02", 1.0),
            ("B", "2026-01-02", 2.0),
            ("A", "2026-01-03", 3.0),
            ("B", "2026-01-03", 4.0),
        ]
        assert [r["asof_ms"] for r in records] == [
            _ms("2026-01-02"), _ms("2026-01-02"), _ms("2026-01-03"), _ms("2026-01-03")
        ]
        assert all(type(r["ts"]) is int for r in records)  # JSONL kept the int

    def test_ms_unit_copies_an_epoch_field_onto_ts_out(self, acquired, tmp_path):
        node = ObservationRows(
            "obs",
            _params(acquired.root, key_fields=["sym", "ts"], ts_field="ts", ts_unit="ms"),
        )
        records = node.run(_ctx(tmp_path), {})["records"]
        assert len(records) == len(ROWS)
        assert all(r["asof_ms"] == r["ts"] == _ms(r["date"]) for r in records)
        assert node.data_edge() == _ms("2026-01-03")

    def test_ms_unit_refuses_a_non_numeric_stamp_by_name(self, acquired):
        node = ObservationRows("obs", _params(acquired.root, ts_unit="ms"))  # date: str
        with pytest.raises(ValueError, match="'date'"):
            node.fingerprint()

    def test_ms_unit_copies_an_integral_float_exactly_and_refuses_a_fraction(
        self, tmp_path
    ):
        # JSON spells an integer count as ``1.7e12`` freely: that is exact
        # and lands as an int. A FRACTIONAL count is a unit mistake (seconds
        # with a fraction, say) — ``int()`` would truncate it silently.
        rows = [
            {**row, "t_float": float(_ms(row["date"])), "t_frac": _ms(row["date"]) + 0.5}
            for row in ROWS
        ]
        root = _root(tmp_path, rows=rows, name="floats")
        node = ObservationRows(
            "obs",
            _params(root.root, key_fields=["sym", "date"], ts_field="t_float", ts_unit="ms"),
        )
        records = node.run(_ctx(tmp_path), {})["records"]
        assert [type(r["asof_ms"]) for r in records] == [int] * len(ROWS)
        assert all(r["asof_ms"] == _ms(r["date"]) for r in records)
        node = ObservationRows(
            "obs",
            _params(root.root, key_fields=["sym", "date"], ts_field="t_frac", ts_unit="ms"),
        )
        with pytest.raises(ValueError, match=r"'t_frac' must be an integer epoch-ms"):
            node.fingerprint()

    def test_the_accessor_seam_is_held_to_the_same_refusals_as_the_params(
        self, acquired
    ):
        # A subclass answers the scan's shape from accessors the plan-time
        # gate never sees: an off-vocabulary unit must not ride the ``ms``
        # branch by default, and a ``since_ms`` with no instant field must
        # not vanish (the ``iso`` seam already refuses it; ``ms`` did not).
        class Seconds(ObservationRows):
            _PARAMS = tuple(k for k in ObservationRows._PARAMS if k != "ts_unit")

            def ts_unit(self):
                return "s"

        params = {k: v for k, v in _params(acquired.root).items() if k != "ts_unit"}
        with pytest.raises(ValueError, match=r"ts_unit\(\).*'s'"):
            Seconds("obs", params).fingerprint()

        class NoInstant(ObservationRows):
            _PARAMS = tuple(k for k in ObservationRows._PARAMS if k != "ts_field")

            def ts_field(self):
                return None

        params = _params(acquired.root, ts_field=..., ts_unit="ms", since_ms=5)
        assert NoInstant.validate_params(params) == []  # the gate cannot see it
        with pytest.raises(ValueError, match="since_ms=5"):
            NoInstant("obs", params).fingerprint()

    @pytest.mark.parametrize("unit", TS_UNITS)
    def test_since_ms_keeps_rows_at_or_after_the_bound(self, acquired, tmp_path, unit):
        field = "date" if unit == "iso" else "ts"
        node = ObservationRows(
            "obs",
            _params(
                acquired.root,
                key_fields=["sym", field],
                ts_field=field,
                ts_unit=unit,
                since_ms=_ms("2026-01-03"),
            ),
        )
        records = node.run(_ctx(tmp_path), {})["records"]
        assert [(r["sym"], r["date"]) for r in records] == [
            ("A", "2026-01-03"), ("B", "2026-01-03")
        ]
        assert node.fingerprint()["rows"] == 2
        assert node.data_edge() == _ms("2026-01-03")

    def test_ts_out_names_the_stamp_field(self, acquired, tmp_path):
        node = ObservationRows("obs", _params(acquired.root, ts_out="t_ms"))
        records = node.run(_ctx(tmp_path), {})["records"]
        assert all("t_ms" in r and "asof_ms" not in r for r in records)
        assert node.data_edge() == _ms("2026-01-03")

    def test_data_edge_is_none_without_a_ts_field(self, acquired, tmp_path):
        node = ObservationRows("obs", _params(acquired.root, ts_field=...))
        assert node.data_edge() is None
        records = node.run(_ctx(tmp_path), {})["records"]
        assert all("asof_ms" not in r for r in records)
        assert [r["sym"] for r in records] == ["A", "A", "B", "B"]  # by key

    def test_ms_unit_refuses_to_clobber_a_field_the_data_already_carries(self, tmp_path):
        # The seam refuses this under ``iso`` ("never a silent clobber");
        # the ``ms`` path must refuse it too — same knob, same promise.
        rows = [{**row, "asof_ms": 1} for row in ROWS]
        root = _root(tmp_path, rows=rows, name="clobber")
        node = ObservationRows(
            "obs", _params(root.root, key_fields=["sym", "ts"], ts_field="ts", ts_unit="ms")
        )
        with pytest.raises(ValueError, match="asof_ms"):
            node.fingerprint()
        with pytest.raises(AssetError, match="asof_ms"):
            ObservationRows("obs", _params(root.root)).fingerprint()
        # Declaring the carried field AS the instant is the lawful spelling.
        node = ObservationRows(
            "obs",
            _params(root.root, key_fields=["sym", "ts"], ts_field="asof_ms", ts_unit="ms"),
        )
        assert all(r["asof_ms"] == 1 for r in node.run(_ctx(tmp_path), {})["records"])
        assert node.data_edge() == 1

    def test_a_missing_source_refuses_loudly_never_reads_empty(self, acquired):
        node = ObservationRows("obs", _params(acquired.root, source="ghost"))
        with pytest.raises(AssetError, match="observations"):
            node.fingerprint()

    def test_the_scan_reads_root_source_and_stream_through_the_accessors(self, acquired):
        """A subclass pins its stream by overriding ``stream()`` (ADR-0077):
        the scan must read the hook, never ``params["stream"]`` directly —
        the defect the accessor exists to remove."""

        class Pinned(ObservationRows):
            _PARAMS = tuple(k for k in ObservationRows._PARAMS if k != "stream")

            def stream(self):
                return "ghost"

        params = {k: v for k, v in _params(acquired.root).items() if k != "stream"}
        node = Pinned("obs", params)
        assert (node.root(), node.source(), node.stream()) == (
            acquired.root, params["source"], "ghost"
        )
        # The seam reads an unacquired stream as EMPTY (never a refusal), so
        # zero rows here is the proof the scan asked for ``ghost``, while the
        # same params through the base kind still see the acquired stream.
        assert node.fingerprint()["rows"] == 0
        assert ObservationRows("obs", _params(acquired.root)).fingerprint()["rows"] == len(ROWS)

    def test_fingerprint_is_stable_on_reread_and_moves_on_content(self, acquired):
        first = ObservationRows("obs", _params(acquired.root)).fingerprint()
        again = ObservationRows("obs", _params(acquired.root)).fingerprint()
        assert first == again
        assert first["kind"] == "ObservationRows" and first["rows"] == len(ROWS)
        node = ObservationRows("obs", _params(acquired.root))
        assert node.fingerprint() == node.fingerprint() == first
        _rewrite_first_value(_members(acquired)[0])
        moved = ObservationRows("obs", _params(acquired.root)).fingerprint()
        assert moved["rows"] == first["rows"]
        assert moved["sha256"] != first["sha256"]
        assert node.fingerprint() == first  # the memo is the instance's

    def test_run_emits_the_memoized_list(self, acquired, tmp_path):
        node = ObservationRows("obs", _params(acquired.root))
        before = node.fingerprint()
        first = node.run(_ctx(tmp_path), {})
        second = node.run(_ctx(tmp_path), {})
        assert first["records"] is second["records"]
        assert set(first) == {"records"}
        assert node.fingerprint() == before

    def test_project_override_shapes_the_records(self, acquired, tmp_path):
        class TupleRows(ObservationRows):
            def project(self, records):
                return [(r["sym"], r["asof_ms"], r["value"]) for r in records]

        node = TupleRows("obs", _params(acquired.root))
        out = node.run(_ctx(tmp_path), {})
        assert out["records"] == [
            ("A", _ms("2026-01-02"), 1.0),
            ("B", _ms("2026-01-02"), 2.0),
            ("A", _ms("2026-01-03"), 3.0),
            ("B", _ms("2026-01-03"), 4.0),
        ]
        assert out["records"] is node.run(_ctx(tmp_path), {})["records"]  # once
        fp = node.fingerprint()
        assert fp["kind"] == "TupleRows" and fp["rows"] == len(ROWS)
        # The digest is over the deduplicated ROWS, before projection.
        base = ObservationRows("obs", _params(acquired.root)).fingerprint()
        assert fp["sha256"] == base["sha256"]

    def test_a_subclass_narrows_the_knobs_its_domain_decides(self, acquired, tmp_path):
        class FixedKeys(ObservationRows):
            _PARAMS = tuple(k for k in ObservationRows._PARAMS if k != "key_fields")

            def key_fields(self):
                return ("sym", "date")

        params = _params(acquired.root, key_fields=...)
        assert FixedKeys.validate_params(params) == []
        refused = FixedKeys.validate_params(_params(acquired.root))
        assert any("key_fields" in p for p in refused)
        records = FixedKeys("obs", params).run(_ctx(tmp_path), {})["records"]
        assert len(records) == len(ROWS)


# ---------------------------------------------------------------------------
# Pack shape
# ---------------------------------------------------------------------------


class TestPackShape:
    def test_the_kind_and_its_contract(self):
        assert NODE_KINDS == (("observations", ObservationRows),)
        assert ObservationRows.role == "data"
        assert ObservationRows.outputs == ("records",)

    def test_importing_the_pack_registers_nothing(self):
        assert "observations" not in DEFAULT_NODE_KINDS

    def test_register_is_explicit_and_idempotent(self):
        private = NodeKindRegistry()
        register(private)
        assert "observations" in private
        register(private)  # second call skips, never shadows
        cls, owned = private.get("observations")
        assert cls is ObservationRows and owned is False


# ---------------------------------------------------------------------------
# Conformance — the toolkit bar over the acquired root
# ---------------------------------------------------------------------------


def probes(tmp_path):
    root = _root(tmp_path)
    params = _params(root.root)

    def move():
        _rewrite_first_value(_members(root)[0])

    def grow():
        # The live puller: one more source row, one more ACQUISITION —
        # the cursor keeps the durable rows out, so the new member holds
        # exactly the new row.
        _write_rows(_data_file(tmp_path), [GROWTH], mode="a")
        assert _acquire(root)["records"] == 1

    return {
        "observations": NodeProbe(
            params=dict(params),
            required=("root", "source", "stream", "key_fields"),
            make=lambda: ObservationRows("obs", dict(params)),
            move=move,
            grow=grow,
            size=lambda out: len(out["records"]),
            runnable=True,
        ),
    }


TestObservationsConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.observations",
    probes=probes,
    expected_roles={"observations": "data"},
    name="TestObservationsConformance",
)

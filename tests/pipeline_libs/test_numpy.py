"""The tier-2 numpy pack (docs/25 §2): lifting, writeback, feature rows,
and — the load-bearing part — the CAUSALITY GUARD, proven by breakers.

Fixture/in-memory data only: every record is hand-built or synthetic;
no file is read. The conformance suite runs over a private pairs table
of the pack's concrete reference subclasses, and the integration tests
wire those subclasses BY IMPORT PATH through the real planner and
driver — the pack's whole wiring story (no registration, subclass +
path) exercised end to end.
"""

from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass

import numpy as np
import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import NodeSpec, PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.numpy import (
    NODE_KINDS,
    ArrayFeatures,
    ArrayMap,
    LogMid,
    TrailingReturns,
    _cut_points,
)
from dskit.pipeline.node import NodeContext, node_class_errors, resolve_uses
from dskit.pipeline.planner import plan
from dskit.pipeline.records import MarketRecord

EXAMPLE = (
    pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "numpy-features.json"
)

BASE_MS = 1_700_000_000_000
MINUTE_MS = 60_000

#: Ramping mids per instrument — prefix mean != full mean, so the global
#: breakers genuinely differ under truncation.
AAA_MIDS = [0.30 + 0.04 * i for i in range(8)]
BBB_MIDS = [0.60 - 0.03 * i for i in range(8)]


def rec(instrument, i, mid, **overrides):
    fields = dict(
        venue="testvenue",
        instrument=instrument,
        contract=f"{instrument}-{i:03d}",
        asof_ms=BASE_MS + i * MINUTE_MS,
        usable=True,
        reason="ok",
        mid=mid,
    )
    fields.update(overrides)
    return MarketRecord(**fields)


def stream(*, scramble=False, none_mid_at=None):
    """Two instruments interleaved in time order; optionally scrambled
    (stream order != time order) and with one AAA mid missing."""
    records = []
    for i in range(8):
        aaa_mid = None if none_mid_at == i else AAA_MIDS[i]
        records.append(rec("AAA", i, aaa_mid))
        records.append(rec("BBB", i, BBB_MIDS[i]))
    if scramble:
        records[1], records[8] = records[8], records[1]
    return records


def ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / "run"))


def features_node(fn, params=None):
    """An ArrayFeatures stub whose apply is ``fn(arrays)`` — the cheap way
    to probe the apply contract and the guard."""

    class _Stub(ArrayFeatures):
        def apply(self, arrays, params):
            return fn(arrays)

    return _Stub("stub", dict(params or {}))


def map_node(fn, params=None):
    class _Stub(ArrayMap):
        def apply(self, arrays, params):
            return fn(arrays)

    return _Stub("stub", dict(params or {}))


class _CaptureMap(ArrayMap):
    """Identity map that records the arrays each apply call saw."""

    def __init__(self, key, params=None, *, mode=None, artifact=""):
        super().__init__(key, params, mode=mode, artifact=artifact)
        self.seen = []

    def apply(self, arrays, params):
        self.seen.append({name: arr.copy() for name, arr in arrays.items()})
        return {"mid": arrays["mid"]}


# ---------------------------------------------------------------------------
# The breakers and their clean twins (module docstring: the guard is the
# load-bearing part, so it is proven by code that TRIES to leak).
# ---------------------------------------------------------------------------


class _GlobalZScore(ArrayFeatures):
    """BREAKER: the classic leak — mean/std over the WHOLE array, so the
    value at t reads every row after t."""

    def apply(self, arrays, params):
        mid = arrays["mid"]
        with np.errstate(invalid="ignore", divide="ignore"):
            return {"z": (mid - np.nanmean(mid)) / np.nanstd(mid)}


class _CenteredMean(ArrayFeatures):
    """BREAKER: a centered rolling mean — the window looks one row into
    the future."""

    def apply(self, arrays, params):
        mid = arrays["mid"]
        out = np.full(mid.shape, np.nan)
        for t in range(1, len(mid) - 1):
            out[t] = (mid[t - 1] + mid[t] + mid[t + 1]) / 3.0
        return {"cmean": out}


class _GlobalMeanMid(ArrayMap):
    """BREAKER (ArrayMap side): every output is the whole-array mean."""

    def apply(self, arrays, params):
        mid = arrays["mid"]
        with np.errstate(invalid="ignore"):
            return {"mid": np.full(mid.shape, np.nanmean(mid))}


class _TrailingMean(ArrayFeatures):
    """CLEAN TWIN of _CenteredMean: same window width, backward only."""

    def apply(self, arrays, params):
        mid = arrays["mid"]
        out = np.full(mid.shape, np.nan)
        for t in range(2, len(mid)):
            out[t] = (mid[t - 2] + mid[t - 1] + mid[t]) / 3.0
        return {"tmean": out}


#: The tail-leak fixture length: long enough that the OLD interior cuts
#: (n//2 = 6, 3n//4 = 9) sit strictly below the contaminated position
#: n-2 = 10, so only the n-1 tail cut can expose the leak.
_FULL_N = 12


def long_stream(n=_FULL_N):
    """One instrument, ``n`` records, strictly ramping mids — adjacent
    values always differ, so a tail contamination is visible drift."""
    return [rec("AAA", i, 0.30 + 0.02 * i) for i in range(n)]


class _TailOnlyLeak(ArrayFeatures):
    """BREAKER for the guard's old blind spot (S1 #2): causal everywhere
    EXCEPT that on the FULL-length pass the second-to-last output copies
    the LAST row's mid — the shape of an off-by-one at the array tail
    that only manifests at production size. Interior cuts compare only
    positions below themselves and their truncated re-runs never reach
    the trigger length, so cuts at n//2 and 3n//4 wave this through; the
    n-1 tail cut is the one that holds position n-2 against a
    future-free prefix."""

    def apply(self, arrays, params):
        mid = arrays["mid"]
        out = mid.copy()
        if len(out) == _FULL_N:
            out[-2] = mid[-1]
        return {"leaky": out}


# ---------------------------------------------------------------------------
# Lifting
# ---------------------------------------------------------------------------


class TestLifting:
    def test_arrays_are_per_instrument_time_sorted_and_aligned(self, tmp_path):
        node = _CaptureMap("cap", {"causality_check": False})
        node.run(ctx(tmp_path), {"records": stream(scramble=True)})
        assert len(node.seen) == 2  # sorted instrument order: AAA then BBB
        aaa, bbb = node.seen
        assert set(aaa) == {"asof_ms", "bid", "ask", "mid", "lead_frac"}
        assert aaa["asof_ms"].dtype == np.int64
        assert list(aaa["asof_ms"]) == sorted(aaa["asof_ms"])  # ascending
        # scrambled stream order, but the lifted mids are in TIME order
        np.testing.assert_allclose(aaa["mid"], AAA_MIDS)
        np.testing.assert_allclose(bbb["mid"], BBB_MIDS)

    def test_missing_numeric_fields_lift_as_nan(self, tmp_path):
        node = _CaptureMap("cap", {"causality_check": False})
        node.run(ctx(tmp_path), {"records": stream(none_mid_at=2)})
        aaa = node.seen[0]
        assert np.isnan(aaa["bid"]).all()  # never set on the fixtures
        assert np.isnan(aaa["mid"][2]) and not np.isnan(aaa["mid"][3])

    def test_unliftable_records_pass_through_untouched(self, tmp_path):
        records = [
            {"contract": "X-0", "asof_ms": BASE_MS, "mid": 0.5},  # no instrument
            {"instrument": "AAA", "contract": "A-0", "asof_ms": "soon", "mid": 0.5},
            rec("AAA", 1, 0.4),
        ]
        out = LogMid("lm", {}).run(ctx(tmp_path), {"records": records})["records"]
        assert out[0] is records[0] and out[1] is records[1]
        assert out[2].mid == pytest.approx(math.log1p(0.4))


# ---------------------------------------------------------------------------
# ArrayMap writeback
# ---------------------------------------------------------------------------


class TestArrayMapWriteback:
    def test_logmid_rewrites_mid_and_preserves_stream_order(self, tmp_path):
        records = stream(scramble=True, none_mid_at=2)
        out = LogMid("lm", {}).run(ctx(tmp_path), {"records": records})["records"]
        assert [r.contract for r in out] == [r.contract for r in records]
        for before, after in zip(records, out):
            if before.mid is None:
                assert after is before  # nothing writable: passed through
            else:
                assert after.mid == pytest.approx(math.log1p(before.mid))
                assert after.bid is None and after.ask is None  # untouched

    def test_dict_records_are_copied_not_mutated(self, tmp_path):
        records = [
            {
                "instrument": "AAA",
                "contract": f"A-{i}",
                "asof_ms": BASE_MS + i,
                "mid": m,
            }
            for i, m in enumerate((0.2, 0.4, 0.6))
        ]
        out = LogMid("lm", {}).run(ctx(tmp_path), {"records": records})["records"]
        assert [r["mid"] for r in records] == [0.2, 0.4, 0.6]  # originals intact
        assert out[1]["mid"] == pytest.approx(math.log1p(0.4))
        assert out[1]["contract"] == "A-1"

    def test_unwritable_values_leave_the_field_unchanged(self, tmp_path):
        # positions alternate legal (0.5) and illegal (1.5) lead_frac
        node = map_node(
            lambda arrays: {
                "lead_frac": np.where(np.arange(len(arrays["mid"])) % 2 == 0, 0.5, 1.5)
            }
        )
        records = [rec("AAA", i, 0.4) for i in range(4)]
        out = node.run(ctx(tmp_path), {"records": records})["records"]
        assert [r.lead_frac for r in out] == [0.5, None, 0.5, None]

    def test_non_rewritable_column_is_refused_by_name(self, tmp_path):
        node = map_node(lambda arrays: {"zscore": np.zeros(len(arrays["mid"]))})
        with pytest.raises(ValueError, match="zscore.*not rewritable"):
            node.run(ctx(tmp_path), {"records": stream()})

    def test_non_numeric_outputs_never_write(self, tmp_path):
        node = map_node(lambda arrays: {"mid": np.array(["x"] * len(arrays["mid"]))})
        records = [rec("AAA", i, 0.4) for i in range(4)]
        out = node.run(ctx(tmp_path), {"records": records})["records"]
        assert all(after is before for after, before in zip(out, records))

    def test_dataclass_without_the_field_passes_through(self, tmp_path):
        @dataclass(frozen=True)
        class MiniRec:
            instrument: str
            contract: str
            asof_ms: int
            mid: float

        records = [
            MiniRec("AAA", f"A-{i}", BASE_MS + i, 0.4 + 0.1 * i) for i in range(3)
        ]
        wrote = map_node(lambda a: {"mid": a["mid"] * 2.0}).run(
            ctx(tmp_path), {"records": list(records)}
        )["records"]
        assert wrote[1].mid == pytest.approx(1.0)
        skipped = map_node(lambda a: {"lead_frac": np.full(len(a["mid"]), 0.5)}).run(
            ctx(tmp_path), {"records": list(records)}
        )["records"]
        assert all(after is before for after, before in zip(skipped, records))

    def test_inconsistent_schema_across_instruments_is_refused(self, tmp_path):
        node = map_node(
            lambda arrays: (
                {"mid": arrays["mid"]}
                if len(arrays["mid"]) % 2 == 0
                else {"bid": arrays["mid"]}
            ),
            params={"causality_check": False},
        )
        records = [rec("AAA", i, 0.4) for i in range(4)]  # even -> mid
        records += [rec("BBB", i, 0.5) for i in range(3)]  # odd -> bid
        with pytest.raises(ValueError, match="schema must not depend on the data"):
            node.run(ctx(tmp_path), {"records": records})

    def test_empty_stream_is_a_clean_no_op(self, tmp_path):
        assert LogMid("lm", {}).run(ctx(tmp_path), {"records": []}) == {"records": []}

    def test_validate_inputs_refuses_one_shot_iterables_by_name(self):
        node = LogMid("lm", {})
        problems = node.validate_inputs({"records": (r for r in ())})
        assert problems and "one-shot" in problems[0]

    def test_validate_inputs_refuses_unrebuildable_records_by_index(self):
        node = LogMid("lm", {})
        problems = node.validate_inputs({"records": [rec("AAA", 0, 0.4), "junk"]})
        assert problems and "records[1]" in problems[0]
        assert node.validate_inputs({"records": stream()}) == []
        assert node.validate_inputs({"records": [{"instrument": "AAA"}]}) == []


# ---------------------------------------------------------------------------
# The causality guard
# ---------------------------------------------------------------------------


class TestCausalityGuard:
    def test_a_global_zscore_is_caught(self, tmp_path):
        with pytest.raises(ValueError) as excinfo:
            _GlobalZScore("z", {}).run(ctx(tmp_path), {"records": stream()})
        message = str(excinfo.value)
        assert (
            "apply() is not causal — output at t changed when the future "
            "was removed" in message
        )
        assert "column 'z'" in message

    def test_a_centered_window_is_caught(self, tmp_path):
        with pytest.raises(ValueError, match="not causal"):
            _CenteredMean("c", {}).run(ctx(tmp_path), {"records": stream()})

    def test_an_arraymap_global_stat_is_caught_too(self, tmp_path):
        with pytest.raises(ValueError, match="not causal"):
            _GlobalMeanMid("g", {}).run(ctx(tmp_path), {"records": stream()})

    def test_the_trailing_twins_pass(self, tmp_path):
        # same window width as the centered breaker, backward only
        out = _TrailingMean("t", {}).run(ctx(tmp_path), {"records": stream()})
        assert len(out["rows"]) == 16
        returns = TrailingReturns("r", {"window": 2}).run(
            ctx(tmp_path), {"records": stream()}
        )
        assert returns["metrics"]["n_rows"] == 16

    def test_warmup_nans_compare_nan_equal(self, tmp_path):
        # cuts at 4 and 6 overlap the two warm-up NaNs of each instrument;
        # the guard must treat them as equal, not as drift
        rows = TrailingReturns("r", {"window": 2}).run(
            ctx(tmp_path), {"records": stream()}
        )["rows"]
        aaa = [row for row in rows if row["instrument"] == "AAA"]
        assert aaa[0]["trailing_return"] is None
        assert aaa[1]["trailing_return"] is None
        expected = AAA_MIDS[2] / AAA_MIDS[0] - 1.0
        assert aaa[2]["trailing_return"] == pytest.approx(expected)

    def test_turning_the_knob_off_is_a_decision_that_skips_the_guard(self, tmp_path):
        out = _GlobalZScore("z", {"causality_check": False}).run(
            ctx(tmp_path), {"records": stream()}
        )
        assert out["metrics"]["n_rows"] == 16  # the leak runs, unchecked

    def test_an_apply_that_refuses_prefixes_is_named(self, tmp_path):
        def refuser(arrays):
            if len(arrays["mid"]) < 6:
                raise RuntimeError("too short")
            return {"x": np.zeros(len(arrays["mid"]))}

        with pytest.raises(ValueError, match="truncated prefix"):
            features_node(refuser).run(ctx(tmp_path), {"records": stream()})

    def test_a_column_set_that_changes_under_truncation_is_caught(self, tmp_path):
        def shifter(arrays):
            n = len(arrays["mid"])
            columns = {"a": np.zeros(n)}
            if n > 6:
                columns["b"] = np.ones(n)
            return columns

        with pytest.raises(ValueError, match="column set changed"):
            features_node(shifter).run(ctx(tmp_path), {"records": stream()})

    def test_single_record_groups_give_the_guard_no_leverage(self, tmp_path):
        # documented limit: the guard needs a strict prefix, so a length-1
        # group passes even a global stat (whose z here is 0/0 -> None row)
        records = [rec("AAA", 0, 0.4), rec("BBB", 0, 0.6)]
        out = _GlobalZScore("z", {}).run(ctx(tmp_path), {"records": records})
        assert [row["z"] for row in out["rows"]] == [None, None]

    def test_a_leak_only_at_the_final_row_is_caught_by_the_tail_cut(self, tmp_path):
        # S1 #2: pre-fix cuts (n//2, 3n//4) compare no position past
        # 3n//4, so this breaker sailed through the guard untouched.
        with pytest.raises(ValueError, match="not causal"):
            _TailOnlyLeak("t", {}).run(ctx(tmp_path), {"records": long_stream()})

    def test_declared_cuts_replace_the_default_grid(self, tmp_path):
        # Interior-only declared cuts cannot see the tail leak — the knob
        # really is the grid, not a supplement...
        out = _TailOnlyLeak("t", {"cuts": [2, 6]}).run(
            ctx(tmp_path), {"records": long_stream()}
        )
        assert out["metrics"]["n_rows"] == _FULL_N  # blind cuts: the leak rides
        # ...and a declared tail cut catches it on its own.
        with pytest.raises(ValueError, match="not causal"):
            _TailOnlyLeak("t", {"cuts": [_FULL_N - 1]}).run(
                ctx(tmp_path), {"records": long_stream()}
            )

    def test_cuts_beyond_a_groups_length_are_skipped(self, tmp_path):
        # Documented no-leverage limit, knob form: cuts are kept to strict
        # prefixes PER INSTRUMENT, so a cut past a group's length checks
        # nothing there — even a global z-score rides when every declared
        # cut misses (the 8-row stream has no 40-row prefix).
        out = _GlobalZScore("z", {"cuts": [40]}).run(
            ctx(tmp_path), {"records": stream()}
        )
        assert out["metrics"]["n_rows"] == 16

    def test_cut_points_are_deterministic_strict_prefixes(self):
        # Re-pinned for S1 #2: the default grid densified from the old
        # {n//2, 3n//4} to quarter/half/three-quarter PLUS the n-1 tail
        # cut that catches last-row leaks.
        assert _cut_points(1) == []
        assert _cut_points(2) == [1]
        assert _cut_points(4) == [1, 2, 3]
        assert _cut_points(8) == [2, 4, 6, 7]
        assert _cut_points(12) == [3, 6, 9, 11]
        # a declared grid is deduped and kept to strict prefixes
        assert _cut_points(8, (7, 1, 7, 40)) == [1, 7]


# ---------------------------------------------------------------------------
# The apply contract
# ---------------------------------------------------------------------------


class TestApplyContract:
    @pytest.mark.parametrize(
        ("fn", "message"),
        [
            (lambda arrays: [1, 2], "must return a dict"),
            (lambda arrays: {}, "returned no columns"),
            (lambda arrays: {1: np.zeros(1)}, "non-string column name"),
            (
                lambda arrays: {"x": np.zeros((len(arrays["mid"]), 2))},
                "must be 1-D",
            ),
            (
                lambda arrays: {"x": np.zeros(len(arrays["mid"]) + 1)},
                "one value per input row",
            ),
        ],
    )
    def test_malformed_returns_are_refused_by_name(self, tmp_path, fn, message):
        with pytest.raises(ValueError, match=message):
            features_node(fn).run(ctx(tmp_path), {"records": stream()})

    def test_the_base_apply_is_abstract(self):
        class _Passthrough(ArrayFeatures):
            def apply(self, arrays, params):
                return super().apply(arrays, params)

        with pytest.raises(NotImplementedError):
            _Passthrough("stub", {}).apply({}, {})


# ---------------------------------------------------------------------------
# ArrayFeatures rows
# ---------------------------------------------------------------------------


class TestArrayFeaturesRows:
    def test_rows_carry_identity_and_follow_stream_order(self, tmp_path):
        records = stream()
        rows = TrailingReturns("r", {"window": 2}).run(
            ctx(tmp_path), {"records": records}
        )["rows"]
        assert [row["contract"] for row in rows] == [r.contract for r in records]
        assert set(rows[0]) == {
            "instrument",
            "contract",
            "asof_ms",
            "group",
            "trailing_return",
        }

    def test_group_rides_into_the_rows(self, tmp_path):
        records = [rec("AAA", i, 0.4, group="AAA:g0") for i in range(3)]
        rows = TrailingReturns("r", {"window": 1}).run(
            ctx(tmp_path), {"records": records}
        )["rows"]
        assert {row["group"] for row in rows} == {"AAA:g0"}

    def test_a_record_without_a_contract_feeds_arrays_but_yields_no_row(self, tmp_path):
        records = [
            {"instrument": "AAA", "contract": "A-0", "asof_ms": BASE_MS, "mid": 0.40},
            {"instrument": "AAA", "asof_ms": BASE_MS + 1, "mid": 0.50},  # no id
            {
                "instrument": "AAA",
                "contract": "A-2",
                "asof_ms": BASE_MS + 2,
                "mid": 0.55,
            },
        ]
        out = TrailingReturns("r", {"window": 1}).run(
            ctx(tmp_path), {"records": records}
        )
        assert [row["contract"] for row in out["rows"]] == ["A-0", "A-2"]
        # A-2's trailing return is measured AGAINST the id-less record,
        # proving it participated in the arrays it has no row in.
        assert out["rows"][1]["trailing_return"] == pytest.approx(0.55 / 0.50 - 1.0)

    def test_identity_collisions_are_refused_by_name(self, tmp_path):
        node = features_node(lambda arrays: {"asof_ms": np.zeros(len(arrays["mid"]))})
        with pytest.raises(ValueError, match="asof_ms.*collide"):
            node.run(ctx(tmp_path), {"records": stream()})

    def test_string_columns_ride_and_nan_lands_as_none(self, tmp_path):
        node = features_node(
            lambda arrays: {"label": np.array(["flat"] * len(arrays["mid"]))}
        )
        rows = node.run(ctx(tmp_path), {"records": stream()})["rows"]
        assert rows[0]["label"] == "flat"

    def test_metrics_are_the_numeric_summary(self, tmp_path):
        out = TrailingReturns("r", {"window": 2}).run(
            ctx(tmp_path), {"records": stream()}
        )
        assert out["metrics"] == {
            "n_rows": 16,
            "n_records": 16,
            "n_instruments": 2,
            "n_columns": 1,
        }

    def test_an_unliftable_record_yields_no_row(self, tmp_path):
        records = [{"contract": "X-0", "asof_ms": BASE_MS, "mid": 0.5}]  # no instrument
        records += [rec("AAA", i, 0.4) for i in range(2)]
        out = TrailingReturns("r", {"window": 1}).run(
            ctx(tmp_path), {"records": records}
        )
        assert [row["instrument"] for row in out["rows"]] == ["AAA", "AAA"]

    def test_empty_stream_yields_empty_rows(self, tmp_path):
        out = TrailingReturns("r", {"window": 2}).run(ctx(tmp_path), {"records": []})
        assert out["rows"] == [] and out["metrics"]["n_rows"] == 0


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------


class TestParams:
    def test_unknown_knobs_are_refused_by_name(self):
        assert any("bogus" in p for p in LogMid.validate_params({"bogus": 1}))
        assert any(
            "bogus" in p
            for p in TrailingReturns.validate_params({"window": 2, "bogus": 1})
        )

    def test_causality_check_must_be_a_bool(self):
        assert LogMid.validate_params({"causality_check": True}) == []
        assert any(
            "causality_check" in p
            for p in LogMid.validate_params({"causality_check": "yes"})
        )
        # a $-reference is legal wiring; the materialized value re-validates
        assert LogMid.validate_params({"causality_check": "$knobs.flag"}) == []

    def test_cuts_must_be_distinct_ints_at_least_one(self):
        # the S1 #2 knob: ints, in-range (>= 1; the per-instrument upper
        # bound is only knowable at run), duplicates refused by name
        assert LogMid.validate_params({"cuts": [1, 5, 9]}) == []
        assert LogMid.validate_params({"cuts": "$knobs.cuts"}) == []
        for bad in ("mid", [], 3, [0], [-2], [True], [1.5], ["7"], [None]):
            assert any("cuts" in p for p in LogMid.validate_params({"cuts": bad})), bad
        assert any(
            "cuts repeats [3]" in p for p in LogMid.validate_params({"cuts": [3, 5, 3]})
        )

    def test_window_is_required_and_checked(self):
        assert any(
            "window is required" in p for p in TrailingReturns.validate_params({})
        )
        for bad in ("NaN", 0, -1, True, 2.5):
            assert any(
                "window" in p for p in TrailingReturns.validate_params({"window": bad})
            ), bad
        assert TrailingReturns.validate_params({"window": 3}) == []
        assert TrailingReturns.validate_params({"window": "$grid.window"}) == []

    def test_construction_enforces_the_validator(self):
        with pytest.raises(ConfigError, match="window is required"):
            TrailingReturns("r", {})


# ---------------------------------------------------------------------------
# Referencing — the wiring story
# ---------------------------------------------------------------------------


class TestReferencing:
    def test_the_bases_are_abstract_and_refused_at_resolve(self):
        for name in ("ArrayMap", "ArrayFeatures"):
            with pytest.raises(ValueError, match="abstract"):
                resolve_uses(f"dskit.pipeline.libs.numpy:{name}")

    def test_the_reference_subclasses_resolve_by_import_path(self):
        assert resolve_uses("dskit.pipeline.libs.numpy:LogMid").cls is LogMid
        assert node_class_errors(TrailingReturns, "here") == []

    def test_the_pack_registers_nothing(self):
        assert NODE_KINDS == ()


# ---------------------------------------------------------------------------
# Conformance — the pack held to the toolkit bar (docs/25 §1, F-222)
# ---------------------------------------------------------------------------


def _probe_stream():
    return stream()


def _probes(tmp_path):
    return {
        "numpy-log-mid": NodeProbe(
            params={},
            inputs={"records": _probe_stream()},
            stream_ports=("records",),
            runnable=True,
        ),
        "numpy-trailing-returns": NodeProbe(
            params={"window": 2},
            required=("window",),
            inputs={"records": _probe_stream()},
            stream_ports=("records",),
            runnable=True,
        ),
    }


TestNumpyPackConformance = conformance_suite(
    registry=(
        ("numpy-log-mid", LogMid),
        ("numpy-trailing-returns", TrailingReturns),
    ),
    module="dskit.pipeline.libs.numpy",
    probes=_probes,
    expected_roles={
        "numpy-log-mid": "transform",
        "numpy-trailing-returns": "tensor",
    },
    name="TestNumpyPackConformance",
)


# ---------------------------------------------------------------------------
# Integration — import-path wiring through the real planner and driver
# ---------------------------------------------------------------------------


def _document(tmp_path):
    return PipelineDocument(
        name="numpy-int",
        pipeline={
            "dataset": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthEvents",
                params={"n_events": 24, "n_instruments": 2, "seed": 3},
            ),
            "log_mid": NodeSpec(
                uses="dskit.pipeline.libs.numpy:LogMid",
                inputs={"records": "$dataset.events"},
            ),
            "features": NodeSpec(
                uses="dskit.pipeline.libs.numpy:TrailingReturns",
                inputs={"records": "$log_mid.records"},
                params={"window": 2},
            ),
        },
        outputs=OutputsConfig(run_root=str(tmp_path / "runs")),
    )


class TestIntegration:
    def test_import_path_wiring_plans_with_the_real_planner(self, tmp_path):
        resolved = plan(_document(tmp_path))
        assert tuple(resolved.order) == ("dataset", "log_mid", "features")
        assert resolved.role_of("log_mid") == "transform"
        assert resolved.role_of("features") == "tensor"

    def test_the_document_runs_exit_zero(self, tmp_path):
        result = run_document(_document(tmp_path), asof="2026-01-01")
        assert result.state == "ran" and result.exit_code == 0
        events = result.outputs["dataset"]["events"]
        mapped = result.outputs["log_mid"]["records"]
        assert mapped[0]["mid"] == pytest.approx(math.log1p(events[0]["mid"]))
        rows = result.outputs["features"]["rows"]
        assert len(rows) == 48
        assert sum(1 for row in rows if row["trailing_return"] is None) == 4


# ---------------------------------------------------------------------------
# The shipped example — executable, not decorative
# ---------------------------------------------------------------------------


class TestShippedExample:
    def test_loads_and_hashes_stably(self):
        doc = load_document(str(EXAMPLE))
        assert doc.name == "numpy-features"
        assert doc.hash == load_document(str(EXAMPLE)).hash

    def test_plans_so_every_import_path_is_real(self):
        resolved = plan(load_document(str(EXAMPLE)))
        assert tuple(resolved.order) == ("dataset", "log_mid", "features")

    def test_runs_exit_zero(self, tmp_path):
        obj = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        obj["outputs"]["run_root"] = str(tmp_path / "runs")
        result = run_document(PipelineDocument.from_obj(obj), asof="2026-01-01")
        assert result.state == "ran" and result.exit_code == 0
        assert result.outputs["features"]["metrics"]["n_rows"] == 96

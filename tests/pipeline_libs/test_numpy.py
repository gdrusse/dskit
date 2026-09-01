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
import logging
import math
import pathlib
import sys
import tracemalloc
import types
from dataclasses import dataclass

import numpy as np
import pytest

from dskit.pipeline.base import ConfigError, OutputsConfig
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import NodeSpec, PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs.numpy import (
    NODE_KINDS,
    RETURN_KINDS,
    ArrayFeatures,
    ArrayMap,
    LogMid,
    ReturnWindows,
    TrailingReturns,
    _accessor_owner,
    _cut_points,
    _prefix_equal,
    accessor_narrowing_problems,
    lag,
    lead,
    log_return,
    narrow_params,
    pct_return,
    rolling_max,
    rolling_min,
    rolling_std,
    rolling_sum,
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

    def test_the_guards_default_has_ONE_name(self, tmp_path, monkeypatch):
        """``causality_check``'s default was a bare ``True`` written in
        ``validate_params`` AND on the run path — the exact "a default
        belongs to ONE name" shape, in the module whose docstring now
        advertises defaults-named-once as its discipline.

        Both sites are pinned by rebinding the constant: the run path
        must stop screening, and the knob gate must judge the new value.
        Nothing else in this module can tell the two readers apart.
        """
        import dskit.pipeline.libs.numpy as pack

        monkeypatch.setattr(pack, "DEFAULT_CAUSALITY_CHECK", False)
        out = _GlobalZScore("z", {}).run(ctx(tmp_path), {"records": stream()})
        assert out["metrics"]["n_rows"] == 16, "the run path read the constant"

        monkeypatch.setattr(pack, "DEFAULT_CAUSALITY_CHECK", "yes")
        assert any(
            "causality_check" in p for p in _GlobalZScore.validate_params({})
        ), "the knob gate read the constant"

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

    def test_a_group_the_envelope_could_not_hold_rides_as_absent(self, tmp_path):
        """The row's ``group`` is the CLUSTER id, and the envelope's rule
        for one is a non-empty string or nothing.

        A dict record is interchangeable with an envelope everywhere
        else in this pack, so a dict carrying ``group: 5`` must land the
        same way ``MarketRecord`` would have it — absent, not 5. It is
        not cosmetic: ``RandomSplitConfig.split_of`` hashes
        ``f"{seed}:{cluster}"``, so ``"0:5"`` and ``"0:None"`` put the
        row in different buckets, and ``distinct_by="group"`` counts it
        differently.
        """
        records = [
            {"instrument": "AAA", "contract": f"A-{i}", "asof_ms": BASE_MS + i,
             "mid": 1.0 + i, "group": 5}
            for i in range(3)
        ]
        rows = TrailingReturns("r", {"window": 1}).run(
            ctx(tmp_path), {"records": records}
        )["rows"]
        assert {row["group"] for row in rows} == {None}

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
            "n_dropped": 0,
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


# ---------------------------------------------------------------------------
# ADR-0040 — declared lifting fields, the narrowing rule, gap-aware
# framing, the ops, and the serving call
# ---------------------------------------------------------------------------


def bars(symbol, minutes, price=100.0, field="px"):
    """A foreign-vocabulary stream: no envelope field name anywhere."""
    return [
        {"sym": symbol, "t": BASE_MS + m * MINUTE_MS, field: price + m}
        for m in minutes
    ]


#: The whole vocabulary declared away from the envelope's, which is what
#: a keyed time series with other names needs in order to enter at all.
FOREIGN = {
    "group_field": "sym",
    "order_field": "t",
    "fields": ["px"],
    "carry_fields": ["sym", "t"],
    "require_fields": [],
}


class _Passthrough(ArrayFeatures):
    """Emits the lifted value back, pointwise — the framing under test,
    with nothing of its own to get wrong."""

    def apply(self, arrays, params):
        return {"value": arrays[self.fields()[0]] * 1.0}


class _PricedWindows(ReturnWindows):
    """ReturnWindows carrying the domain rule the pack cannot know: a
    position with no usable price is not a position, so it is compacted
    out and the survivors chain."""

    def keep_mask(self, arrays):
        px = arrays[self.fields()[0]]
        return np.isfinite(px) & (px > 0.0)


class _CountingWindows(ReturnWindows):
    """ReturnWindows that records the length of every array apply saw."""

    def __init__(self, key, params=None, *, mode=None, artifact=""):
        super().__init__(key, params, mode=mode, artifact=artifact)
        self.calls = []

    def apply(self, arrays, params):
        self.calls.append(len(arrays[self.fields()[0]]))
        return super().apply(arrays, params)


class TestDeclaredLiftingFields:
    def test_a_foreign_vocabulary_stream_lifts_through_the_knobs(self, tmp_path):
        rows = _Passthrough("p", FOREIGN).run(
            ctx(tmp_path), {"records": bars("XYZ", range(4))}
        )["rows"]
        assert [row["sym"] for row in rows] == ["XYZ"] * 4
        assert [row["value"] for row in rows] == [100.0, 101.0, 102.0, 103.0]

    def test_the_order_array_rides_under_its_declared_name(self, tmp_path):
        seen = {}

        class _Peek(_Passthrough):
            def apply(self, arrays, params):
                seen.update({name: len(arr) for name, arr in arrays.items()})
                return super().apply(arrays, params)

        _Peek("p", FOREIGN).run(ctx(tmp_path), {"records": bars("XYZ", range(3))})
        assert sorted(seen) == ["px", "t"]

    def test_a_finite_float_order_value_lifts_and_a_bool_does_not(self, tmp_path):
        records = [
            {"sym": "A", "t": 1.5, "px": 1.0},
            {"sym": "A", "t": 2.5, "px": 2.0},
            {"sym": "A", "t": True, "px": 3.0},
            {"sym": "A", "t": float("inf"), "px": 4.0},
        ]
        rows = _Passthrough("p", FOREIGN).run(ctx(tmp_path), {"records": records})[
            "rows"
        ]
        assert [row["t"] for row in rows] == [1.5, 2.5]

    def test_an_all_unlifted_stream_refuses_by_name(self, tmp_path):
        # The typo case: a declared field matching NOTHING passes through
        # record by record, so it used to emit zero rows and exit 0.
        node = _Passthrough("p", {**FOREIGN, "group_field": "symbol"})
        with pytest.raises(ValueError, match="unlifted"):
            node.run(ctx(tmp_path), {"records": bars("XYZ", range(4))})

    def test_an_empty_stream_refuses_nothing(self, tmp_path):
        assert (
            _Passthrough("p", FOREIGN).run(ctx(tmp_path), {"records": []})["rows"] == []
        )

    def test_the_declared_field_knobs_are_checked_at_plan(self):
        for bad in (
            {"group_field": ""},
            {"order_field": 7},
            {"fields": []},
            {"fields": ["a", "a"]},
            {"max_gap": 0},
            {"max_gap": "soon"},
            {"carry_fields": [""]},
            {"drop_incomplete": "yes"},
        ):
            assert _Passthrough.validate_params(bad), bad


class TestAccessorNarrowing:
    """The rule in three directions (ADR-0040)."""

    def test_overriding_an_accessor_while_keeping_the_knob_is_refused(self):
        class _Broken(ArrayFeatures):
            def fields(self):
                return ("mid",)

            def apply(self, arrays, params):
                return {"x": arrays["mid"]}

        problems = _Broken.validate_params({})
        assert any("fields" in p and "_PARAMS" in p for p in problems), problems
        with pytest.raises(ConfigError, match="NARROWS"):
            _Broken("b", {})

    def test_the_shipped_subclasses_narrowed_the_knob_they_hardcode(self):
        # The live hole this closes: both index arrays["mid"] by name, so
        # `fields: ["bid"]` validated clean and died at execute with a
        # bare KeyError.
        for cls in (LogMid, TrailingReturns):
            assert "fields" not in cls._PARAMS
            assert any(
                "fields" in p
                for p in cls.validate_params({"window": 2, "fields": ["bid"]})
            )
        assert LogMid.validate_params({}) == []

    def test_a_narrowed_subclass_is_not_refused_for_the_knob_it_dropped(self):
        class _Priced(ReturnWindows):
            _PARAMS = narrow_params(ReturnWindows._PARAMS, "fields")

            def fields(self):
                return ("px",)

        # ReturnWindows REQUIRES fields; without the `if knob in _PARAMS`
        # guard that requirement would refuse every narrowing subclass —
        # which is why the guard is load-bearing, not style.
        assert _Priced.validate_params({"lookback": 2}) == []
        assert any(
            "fields" in p for p in ReturnWindows.validate_params({"lookback": 2})
        )

    def test_narrow_params_refuses_a_stale_name(self):
        with pytest.raises(ValueError, match="stale"):
            narrow_params(ArrayFeatures._PARAMS, "no_such_knob")

    def test_the_rule_is_DERIVED_from_the_classes_not_a_listed_table(self):
        """A knob nobody wrote down is narrowed all the same.

        The refusal used to read a hand-maintained knob -> owner table,
        so it covered exactly the knobs someone remembered to add: a
        knob that GAINED an accessor without an entry got no refusal at
        all, which is the ``fields: ["bid"]`` hole (validate clean, die
        at execute with a bare KeyError) reopened for the new knob. It
        also never covered a SUBCLASS's own knobs — the shape every
        child writes. Deriving the owner from the MRO closes both:
        nothing to keep in step, so nothing to forget.
        """
        class _Scaled(ReturnWindows):
            _PARAMS = ReturnWindows._PARAMS + ("scale",)

            def scale(self):
                return self.params.get("scale", 1.0)

        class _Hardcoded(_Scaled):
            def scale(self):
                return 2.0

        assert accessor_narrowing_problems(_Scaled) == [], "the owner is fine"
        problems = accessor_narrowing_problems(_Hardcoded)
        assert any("scale" in p and "_PARAMS" in p for p in problems), problems
        with pytest.raises(ConfigError, match="NARROWS"):
            _Hardcoded("h", {"lookback": 2})
        # And a narrowing subclass of the same knob validates clean.
        class _Narrowed(_Scaled):
            _PARAMS = narrow_params(_Scaled._PARAMS, "scale")

            def scale(self):
                return 2.0

        assert accessor_narrowing_problems(_Narrowed) == []

    def test_every_pack_knob_with_an_accessor_is_covered(self):
        """The census the old table claimed to be, derived instead: for
        each shipped class, every ``_PARAMS`` name resolving to a method
        is one the rule can see. A param with NO accessor
        (``TrailingReturns.window``) is untouched by it."""
        for cls in (ArrayMap, ArrayFeatures, LogMid, TrailingReturns,
                    ReturnWindows):
            for knob in cls._PARAMS:
                if callable(getattr(cls, knob, None)):
                    assert _accessor_owner(cls, knob) is not None, (cls, knob)
        assert "window" in TrailingReturns._PARAMS
        assert _accessor_owner(TrailingReturns, "window") is None


class TestTierOneTruthIsImported:
    def test_the_writeback_rules_ARE_the_envelope_s(self):
        """HIGH-2: the pack's private price/lead copies are gone."""
        import dskit.pipeline.libs.numpy as pack
        from dskit.pipeline import records as core

        assert not hasattr(pack, "_price_ok") and not hasattr(pack, "_lead_ok")
        assert pack._WRITEBACK["mid"] is core.price_ok
        assert pack._WRITEBACK["lead_frac"] is core.lead_frac_ok

    def test_the_decision_instant_field_has_ONE_home(self):
        """``asof_ms`` is the ENVELOPE's decision instant, so the packs
        that default to it read the envelope's name rather than each
        typing the literal.

        Two modules added by ADR-0040 default to this field — the numpy
        pack (what a stream is ordered by) and ``fitted.py`` (what a
        fitted transform's split cuts on) — and they are the same fact
        about the same rows. Unpinned, retuning one leaves a fitted
        transform downstream of a window node cutting on a field the
        rows no longer carry, and the refusal names the wrong cause.
        """
        import dskit.pipeline.libs.numpy as pack
        from dskit.pipeline import fitted
        from dskit.pipeline.records import ASOF_FIELD

        assert pack.DEFAULT_ORDER_FIELD == ASOF_FIELD
        assert fitted.DEFAULT_ORDER_FIELD == ASOF_FIELD

    def test_the_identity_a_ROW_carries_is_the_one_a_SPLIT_reads(self, tmp_path):
        """The pack carries the identity; the fitted family cuts on it.

        Three names had to agree and only two were pinned: the pack
        wrote ``"contract"`` as a literal in two constants while its
        sibling read the identity through the envelope's own rule. If
        the envelope renames the field, ``ArrayFeatures`` must stop
        emitting it and ``cluster_of`` must stop finding it in the SAME
        change — which is what reading one name buys.
        """
        import dskit.pipeline.libs.numpy as pack
        from dskit.pipeline import records as core

        assert core.CONTRACT_FIELD in pack.DEFAULT_CARRY_FIELDS
        assert pack.DEFAULT_REQUIRE_FIELDS == (core.CONTRACT_FIELD,)
        # And the value the pack CARRIES is one `cluster_of` reads back.
        rows = TrailingReturns("w", {"window": 1}).run(
            ctx(tmp_path), {"records": [rec("AAA", i, 0.4 + i * 0.01)
                                        for i in range(4)]}
        )["rows"]
        assert rows, "well-formed records must still carry the required id"
        row = rows[-1]
        assert core.cluster_of(row) == row[core.CONTRACT_FIELD]
        assert core.cluster_of({**row, core.CLUSTER_FIELD: "EV-1"}) == "EV-1"

    def test_the_row_cluster_rule_is_the_envelope_s(self):
        """The pack normalizes a carried ``group`` by the envelope's own
        cluster rule, imported — never a second copy of "a non-empty
        string or nothing"."""
        import dskit.pipeline.libs.numpy as pack
        from dskit.pipeline import records as core

        assert pack.cluster_ok is core.cluster_ok
        assert core.CLUSTER_FIELD in pack.DEFAULT_CARRY_FIELDS
        assert core.cluster_ok("day-1") and not core.cluster_ok(5)
        assert not core.cluster_ok("") and not core.cluster_ok(None)
        # The envelope is the predicate's other reader, so loosening the
        # rule moves both, exactly as it does for price/lead_frac.
        with pytest.raises(ValueError, match="group must be"):
            rec("AAA", 0, 0.4, group=5)

    @pytest.mark.parametrize("value", ["A", "", None, 5, True, 0, 3.5])
    def test_ONE_identity_rule_answers_for_every_place_the_pack_reads_one(
        self, value, tmp_path
    ):
        """Three sites read "is this a usable identity"; one rule owns it.

        The pack asks the question in three places — the GROUP key a
        record is lifted under, a ``require_fields`` id a row must carry,
        and the CLUSTER column it carries onward — and each one used to
        answer it in its own words. Unpinned, the standing resolution
        for admitting a wider id ("change ``cluster_ok``, tier-1, one
        place") moves only the third: the same record is carried by one
        site, refused as a group key by the second, and dropped by the
        third. One record, three answers.

        So the expectation here is COMPUTED from ``cluster_ok`` rather
        than written out: loosen the tier-1 rule and this test moves with
        it, and any site that restated it fails.
        """
        import dskit.pipeline.libs.numpy as pack
        from dskit.pipeline import records as core

        usable = core.cluster_ok(value)

        # 1. the group key: a record whose key is unusable cannot be placed.
        groups, unlifted = pack._lift(
            [{"sym": value, "t": 0, "px": 1.0}], "sym", "t", ("px",)
        )
        assert (not unlifted) is usable, (value, groups, unlifted)

        # 2. a required identity field: a row missing one is no row.
        rows = _Passthrough(
            "p", {**FOREIGN, "require_fields": ["tag"]}
        ).run(ctx(tmp_path), {"records": [
            {"sym": "A", "t": BASE_MS, "px": 1.0, "tag": value}
        ]})["rows"]
        assert bool(rows) is usable, (value, rows)

        # 3. the carried cluster column, already the envelope's rule.
        assert (pack._carried_column(core.CLUSTER_FIELD, [value])[0]
                is not None) is usable

    @pytest.mark.parametrize("value", [
        5, -3, 0, 2.5, -0.5, True, False, None, "7", [],
        float("nan"), float("inf"), float("-inf"), 10**30,
    ])
    def test_ONE_number_rule_answers_wherever_a_REAL_NUMBER_is_read(self, value):
        """"A non-bool int, or a finite float" had four authors.

        ADR-0040 added two more statements of it — the pack's order
        predicate and ``fitted.py``'s instant/feature reader — beside the
        two the envelope already carried inside ``price_ok`` and
        ``lead_frac_ok``. Every one of them decides whether a cell is a
        number the toolkit can use, so they are one rule; unpinned, the
        day a widened bound admits (say) a ``Decimal`` instant, the pack
        would lift a record the fitted family then refuses to cut, and
        the refusal names the rows.

        The expectation is COMPUTED from the tier-1 owner rather than
        written out, so loosening the rule moves this test with it and
        any site that restated it fails instead.
        """
        import dskit.pipeline.libs.numpy as pack
        from dskit.pipeline import fitted
        from dskit.pipeline import records as core

        accepted = core.number_ok(value)
        assert (pack._order_value(value) is not None) is accepted, value
        assert (fitted._numeric(value) is not None) is accepted, value
        assert (not math.isnan(pack._num(value))) is accepted, value
        # The envelope's own two are SPECIALIZATIONS: neither may accept
        # a value the shared rule rejects.
        assert not (core.price_ok(value) and not accepted), value
        assert not (core.lead_frac_ok(value) and not accepted), value

    def test_the_envelope_itself_uses_the_same_predicates(self):
        # Deliberate second reader: loosening the core bound must move
        # BOTH the envelope and the pack, or the pack drifts again.
        from dskit.pipeline.records import lead_frac_ok, price_ok

        assert price_ok(0.5) and not price_ok(0.0) and not price_ok(True)
        assert lead_frac_ok(0.5) and not lead_frac_ok(1.0)
        with pytest.raises(ValueError, match="finite and > 0"):
            rec("AAA", 0, float("inf"))


class TestGapAwareFraming:
    def test_absent_max_gap_is_one_segment_per_group(self, tmp_path):
        node = _CountingWindows(
            "w", {**FOREIGN, "lookback": 2, "causality_check": False}
        )
        node.run(ctx(tmp_path), {"records": bars("A", [0, 1, 2, 40, 41])})
        assert node.calls == [5], "absent max_gap must reproduce today exactly"

    def test_a_gap_wider_than_the_bound_splits_the_group(self, tmp_path):
        node = _CountingWindows(
            "w",
            {
                **FOREIGN,
                "lookback": 2,
                "max_gap": 5 * MINUTE_MS,
                "causality_check": False,
            },
        )
        node.run(ctx(tmp_path), {"records": bars("A", [0, 1, 2, 40, 41])})
        assert node.calls == [3, 2]

    def test_a_gap_exactly_at_the_bound_does_not_split(self, tmp_path):
        node = _CountingWindows(
            "w",
            {
                **FOREIGN,
                "lookback": 2,
                "max_gap": 5 * MINUTE_MS,
                "causality_check": False,
            },
        )
        node.run(ctx(tmp_path), {"records": bars("A", [0, 5, 10])})
        assert node.calls == [3], "a step EQUAL to max_gap must chain"

    def test_no_return_lag_or_label_spans_a_boundary(self, tmp_path):
        prices = {0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0,
                  40: 200.0, 41: 202.0, 42: 204.0, 43: 206.0}
        records = [
            {"sym": "A", "t": BASE_MS + m * MINUTE_MS, "px": p}
            for m, p in prices.items()
        ]
        rows = ReturnWindows(
            "w",
            {
                **FOREIGN,
                "lookback": 2,
                "max_gap": 5 * MINUTE_MS,
                "drop_incomplete": True,
            },
        ).run(ctx(tmp_path), {"records": records})["rows"]
        # One complete row per segment, and every value in the second
        # comes from the second segment alone — nothing reaches back
        # across the 37-minute hole.
        assert [row["t"] for row in rows] == [
            BASE_MS + 2 * MINUTE_MS,
            BASE_MS + 42 * MINUTE_MS,
        ]
        assert rows[1]["lag_0"] == pytest.approx(math.log(204.0 / 202.0))
        assert rows[1]["lag_1"] == pytest.approx(math.log(202.0 / 200.0))
        assert rows[1]["label"] == pytest.approx(math.log(206.0 / 204.0))

    def test_a_segment_shorter_than_the_window_yields_no_row(self, tmp_path):
        records = bars("A", [0, 1, 2]) + bars("A", [40, 41])
        rows = ReturnWindows(
            "w",
            {
                **FOREIGN,
                "lookback": 2,
                "max_gap": 5 * MINUTE_MS,
                "drop_incomplete": True,
            },
        ).run(ctx(tmp_path), {"records": records})["rows"]
        assert rows == []

    def test_a_one_record_segment_gives_the_guard_no_leverage_and_no_row(
        self, tmp_path
    ):
        records = bars("A", [0, 1, 2, 3, 4]) + bars("A", [90])
        rows = ReturnWindows(
            "w",
            {
                **FOREIGN,
                "lookback": 2,
                "max_gap": 5 * MINUTE_MS,
                "drop_incomplete": True,
            },
        ).run(ctx(tmp_path), {"records": records})["rows"]
        assert [row["t"] for row in rows] == [
            BASE_MS + 2 * MINUTE_MS,
            BASE_MS + 3 * MINUTE_MS,
        ]


class TestOps:
    def test_lag_reads_the_past_and_leads_the_future(self):
        values = np.array([1.0, 2.0, 4.0, 8.0])
        assert _prefix_equal(lag(values, 0), values)
        assert _prefix_equal(lag(values, 2), np.array([np.nan, np.nan, 1.0, 2.0]))
        assert _prefix_equal(lead(values, 1), np.array([2.0, 4.0, 8.0, np.nan]))

    def test_a_shift_past_the_end_is_all_nan(self):
        values = np.array([1.0, 2.0])
        assert np.isnan(lag(values, 5)).all() and np.isnan(lead(values, 5)).all()

    def test_a_negative_shift_is_refused_by_name(self):
        with pytest.raises(ValueError, match="lead"):
            lag(np.array([1.0]), -1)
        with pytest.raises(ValueError, match="lag"):
            lead(np.array([1.0]), -1)

    def test_rolling_ops_match_window_reductions(self):
        from numpy.lib.stride_tricks import sliding_window_view

        rng = np.random.default_rng(0)
        x = rng.normal(size=180)
        x[::7] = np.nan
        width = 11
        want_std = np.full(x.size, np.nan)
        want_std[width - 1:] = np.nanstd(
            sliding_window_view(x, width), axis=1, ddof=0
        )
        np.testing.assert_allclose(
            rolling_std(x, width), want_std, equal_nan=True, rtol=1e-10, atol=1e-10,
        )
        finite = rng.normal(size=180)
        want_sum = np.full(finite.size, np.nan)
        want_sum[width - 1:] = np.sum(sliding_window_view(finite, width), axis=1)
        want_max = np.full(finite.size, np.nan)
        want_max[width - 1:] = np.max(sliding_window_view(finite, width), axis=1)
        want_min = np.full(finite.size, np.nan)
        want_min[width - 1:] = np.min(sliding_window_view(finite, width), axis=1)
        np.testing.assert_allclose(rolling_sum(finite, width), want_sum)
        np.testing.assert_allclose(rolling_max(finite, width), want_max)
        np.testing.assert_allclose(rolling_min(finite, width), want_min)
        with_nan = finite.copy()
        with_nan[20] = np.nan
        got_max = rolling_max(with_nan, width)
        want_nan_max = np.full(with_nan.size, np.nan)
        want_nan_max[width - 1:] = np.max(
            sliding_window_view(with_nan, width), axis=1
        )
        np.testing.assert_allclose(
            got_max, want_nan_max, equal_nan=True,
        )

    def test_rolling_std_does_not_materialize_the_window(self):
        n, width = 80_000, 1170
        x = np.linspace(1.0, 2.0, n)
        x[::17] = np.nan
        tracemalloc.start()
        try:
            out = rolling_std(x, width)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert out.shape == (n,)
        assert np.isfinite(out[width - 1:]).any()
        # A dense (n, width) copy is ~750 MB; O(n) helpers stay well under.
        assert peak < 80 * 1024 * 1024, f"peak {peak / 1024 / 1024:.1f} MB"

    def test_a_non_positive_rolling_width_is_refused_by_name(self):
        with pytest.raises(ValueError, match="width"):
            rolling_sum(np.array([1.0]), 0)

    def test_the_return_ops_warm_up_with_nan(self):
        values = np.array([100.0, 110.0, 121.0])
        got_pct = pct_return(values)
        assert math.isnan(got_pct[0])
        assert list(got_pct[1:]) == pytest.approx([0.1, 0.1])
        got = log_return(values)
        assert math.isnan(got[0])
        assert got[1] == pytest.approx(math.log(1.1))

    def test_a_non_positive_price_answers_absent_not_infinite(self):
        # -inf would ride into a row as a number no reader can use
        assert np.isnan(log_return(np.array([0.0, 5.0]))).all()

    def test_the_return_vocabulary_is_a_registry_not_a_branch(self):
        assert sorted(RETURN_KINDS) == ["log", "pct"]
        assert RETURN_KINDS["log"] is log_return


class TestLookaheadIsDeclaredNotExempt:
    def test_the_declared_label_survives_the_guard(self, tmp_path):
        # causality_check ON: the label reads forward BY CONSTRUCTION and
        # is held to its declared horizon rather than waved through.
        node = ReturnWindows("w", {**FOREIGN, "lookback": 2, "label_lead": 1})
        rows = node.run(ctx(tmp_path), {"records": bars("A", range(8))})["rows"]
        assert node.params.get("causality_check", True) is True
        assert len(rows) == 8

    def test_n_ahead_emits_declared_forward_columns(self, tmp_path):
        node = ReturnWindows(
            "w", {**FOREIGN, "lookback": 2, "label_lead": 1, "n_ahead": 3}
        )
        rows = node.run(ctx(tmp_path), {"records": bars("A", range(8))})["rows"]
        assert node.lookahead_columns() == {
            "y_ahead_1": 1, "y_ahead_2": 2, "y_ahead_3": 3,
        }
        filled = next(row for row in rows if row.get("y_ahead_3") is not None)
        assert "y_ahead_1" in filled and "label" not in filled

    def test_an_undeclared_forward_column_is_still_refused(self, tmp_path):
        class _Sneaky(_Passthrough):
            def apply(self, arrays, params):
                return {"value": lead(arrays["px"], 1)}

        with pytest.raises(ValueError, match="not causal"):
            _Sneaky("s", FOREIGN).run(ctx(tmp_path), {"records": bars("A", range(8))})

    def test_a_leak_in_a_lag_column_is_still_refused(self, tmp_path):
        class _Leaky(ReturnWindows):
            def apply(self, arrays, params):
                columns = super().apply(arrays, params)
                columns["lag_0"] = columns["lag_0"] * 0 + np.nanmean(arrays["px"])
                return columns

        with pytest.raises(ValueError, match="lag_0"):
            _Leaky("w", {**FOREIGN, "lookback": 2}).run(
                ctx(tmp_path), {"records": bars("A", range(8))}
            )


class TestTheLabelMayNotTakeALagColumnsName:
    """The collision that put a FORWARD value in a PAST column.

    ``apply`` writes ``lookback`` lags under ``f"{lag_prefix}{step}"`` and
    then the label under ``label_name``, into ONE dict. With
    ``lag_prefix="f"`` and ``label_name="f0"`` the label OVERWROTE lag 0,
    so a consumer reading a past-return feature got the NEXT bar's return;
    the row silently lost a column; and the causality guard could not
    catch it, because ``lookahead_columns`` reports ``f0`` as forward and
    the guard then held the overwritten column to a forward horizon and
    passed. So the combination is refused BY NAME at plan — a leak that
    cannot be expressed is the only kind that cannot ship.
    """

    def test_a_label_named_like_a_lag_column_refuses_at_plan(self):
        problems = ReturnWindows.validate_params(
            {**FOREIGN, "lookback": 4, "lag_prefix": "f", "label_name": "f0"}
        )
        assert problems, "the collision planned clean"
        assert any(
            "label_name" in p and "lag_prefix" in p and "lookback" in p
            for p in problems
        ), problems

    def test_the_colliding_document_cannot_be_built_at_all(self):
        with pytest.raises(ConfigError, match="label_name"):
            ReturnWindows(
                "w", {**FOREIGN, "lookback": 4, "lag_prefix": "f", "label_name": "f0"}
            )

    @pytest.mark.parametrize(
        "params",
        [
            {"lookback": 4, "lag_prefix": "f", "label_name": "f3"},
            {"lookback": 1, "lag_prefix": "f", "label_name": "f0"},
            # multi-digit: the index that IS the last lag, and one that
            # is merely shorter than the bound — a length-blind string
            # comparison gets exactly these two wrong.
            {"lookback": 100, "lag_prefix": "f", "label_name": "f99"},
            {"lookback": 100, "lag_prefix": "f", "label_name": "f9"},
            {"lookback": 12, "lag_prefix": "ret_lag_", "label_name": "ret_lag_11"},
        ],
    )
    def test_every_lag_index_inside_the_window_collides(self, params):
        assert any(
            "label_name" in p
            for p in ReturnWindows.validate_params({**FOREIGN, **params})
        ), params

    @pytest.mark.parametrize(
        "params",
        [
            # the neighbour ONE step past the window: lags are f0..f3
            {"lookback": 4, "lag_prefix": "f", "label_name": "f4"},
            # multi-digit neighbours on both sides of the bound's LENGTH
            {"lookback": 100, "lag_prefix": "f", "label_name": "f100"},
            {"lookback": 9, "lag_prefix": "f", "label_name": "f10"},
            # a label sharing the prefix but not a lag INDEX
            {"lookback": 4, "lag_prefix": "f", "label_name": "fwd"},
            # a zero-padded index is not a name lag() ever writes
            {"lookback": 4, "lag_prefix": "f", "label_name": "f00"},
            # different prefixes never meet
            {"lookback": 99, "lag_prefix": "lag_", "label_name": "y_next"},
            # the pack's own defaults, stated and omitted
            {"lookback": 4},
            {"lookback": 4, "lag_prefix": "lag_", "label_name": "label"},
        ],
    )
    def test_a_non_colliding_neighbour_still_plans(self, params):
        assert ReturnWindows.validate_params({**FOREIGN, **params}) == [], params

    def test_the_check_defers_on_a_reference_and_never_explodes(self):
        # $-refs materialize later, and the totality bar says a validator
        # RETURNS problems rather than raising on a substituted value.
        assert ReturnWindows.validate_params(
            {**FOREIGN, "lookback": 4, "label_name": "$knobs.name"}
        ) == []
        for bad in (None, {}, True, 2.5, "no", 0, -3):
            ReturnWindows.validate_params(
                {**FOREIGN, "lookback": bad, "lag_prefix": "f", "label_name": "f0"}
            )
            ReturnWindows.validate_params(
                {**FOREIGN, "lookback": 4, "lag_prefix": bad, "label_name": bad}
            )
        # A column NAME is a string, not an int: converting a five-thousand
        # digit one would raise inside the validator (CPython caps int<->str
        # at 4300 digits) where the bar is to RETURN problems.
        assert ReturnWindows.validate_params(
            {**FOREIGN, "lookback": 4, "lag_prefix": "f", "label_name": "9" * 5000}
        ) == []

    def test_a_huge_lookback_is_answered_without_building_the_names(self):
        # O(1), not O(lookback): a validator that materialized every lag
        # name would hang the plan on a document nobody would ever run.
        problems = ReturnWindows.validate_params(
            {**FOREIGN, "lookback": 10**9, "lag_prefix": "f", "label_name": "f999999"}
        )
        assert any("label_name" in p for p in problems), problems

    def test_a_narrowed_subclass_is_not_refused_for_knobs_it_dropped(self):
        # The pack's guard rule: a class that hardcodes its spellings
        # dropped those knobs, so the cross-knob check has nothing to
        # read and must stay silent rather than refuse the class.
        class _Fixed(ReturnWindows):
            _PARAMS = narrow_params(
                ReturnWindows._PARAMS, "label_name", "lag_prefix"
            )

            def label_name(self):
                return "y_next"

            def lag_prefix(self):
                return "ret_lag_"

        assert _Fixed.validate_params({**FOREIGN, "lookback": 4}) == []


class TestKeepMask:
    def test_a_masked_position_is_compacted_out_and_the_survivors_chain(
        self, tmp_path
    ):
        class _PricedOnly(ReturnWindows):
            def keep_mask(self, arrays):
                price = arrays["px"]
                return np.isfinite(price) & (price > 0.0)

        records = [
            {"sym": "A", "t": BASE_MS + m * MINUTE_MS, "px": p}
            for m, p in {0: 100.0, 1: 101.0, 2: None, 3: 103.0, 4: 104.0}.items()
        ]
        rows = _PricedOnly(
            "w",
            {
                **FOREIGN,
                "lookback": 2,
                "max_gap": 5 * MINUTE_MS,
                "drop_incomplete": True,
            },
        ).run(ctx(tmp_path), {"records": records})["rows"]
        # The survivors become adjacent: t3 reads THROUGH the priceless
        # minute, and the gap bound then judges the two-minute step.
        assert [row["t"] for row in rows] == [BASE_MS + 3 * MINUTE_MS]
        assert rows[0]["lag_0"] == pytest.approx(math.log(103.0 / 101.0))

    def test_a_masked_position_is_COUNTED_and_logged(self, tmp_path, caplog):
        """"Everything is counted and logged" has to include this one.

        A position the domain rule rejects was compacted away with no
        counter anywhere: the log reported ``unlifted`` and ``no_row``
        only, and the three bars a vendor outage zeroed appeared in
        neither — an operator watching this line for exactly that outage
        saw nothing. The warm-up positions are NOT the same number and
        must not be mistaken for it.
        """
        node = _PricedWindows("w", {**FOREIGN, "lookback": 2,
                                    "max_gap": 5 * MINUTE_MS,
                                    "drop_incomplete": True})
        records = bars("A", range(20))
        for i in (5, 9, 13):
            records[i] = {**records[i], "px": 0.0}
        with caplog.at_level(logging.INFO):
            out = node.run(ctx(tmp_path), {"records": records})

        assert out["metrics"]["n_dropped"] == 3
        assert out["metrics"]["n_records"] == 20
        # 17 survivors, of which the warm-up/label ends carry no row —
        # a different count, which is why one cannot stand in for the other.
        assert out["metrics"]["n_dropped"] != 20 - out["metrics"]["n_rows"]
        line = "\n".join(r.getMessage() for r in caplog.records)
        assert "3 dropped" in line, line


class TestLatestRows:
    def test_the_newest_row_per_group_carries_no_label(self, tmp_path):
        node = ReturnWindows("w", {**FOREIGN, "lookback": 2,
                                   "max_gap": 5 * MINUTE_MS,
                                   "drop_incomplete": True})
        records = bars("A", range(5)) + bars("B", range(5), price=50.0)
        latest = node.latest_rows(records)

        assert sorted(latest) == ["A", "B"]
        assert "label" not in latest["A"]
        assert latest["A"]["t"] == BASE_MS + 4 * MINUTE_MS

    def test_a_serving_row_equals_the_training_row_for_the_same_key(self, tmp_path):
        node = ReturnWindows("w", {**FOREIGN, "lookback": 2,
                                   "max_gap": 5 * MINUTE_MS,
                                   "drop_incomplete": True})
        records = bars("A", range(6))
        trained = {row["t"]: row
                   for row in node.run(ctx(tmp_path), {"records": records})["rows"]}
        # The newest LABELLED row is one bar back; serve on that prefix
        # and the two must agree feature for feature.
        serving = node.latest_rows(records[:-1])["A"]
        training = trained[serving["t"]]
        assert {k: v for k, v in training.items() if k != "label"} == serving

    def test_an_incomplete_newest_position_serves_nothing(self, tmp_path):
        node = ReturnWindows("w", {**FOREIGN, "lookback": 3,
                                   "max_gap": 5 * MINUTE_MS,
                                   "drop_incomplete": True})
        assert node.latest_rows(bars("A", [0, 1, 2])) == {}
        # a fresh session leaves the newest bar with no window behind it
        assert node.latest_rows(bars("A", [0, 1, 2, 3, 4, 40])) == {}

    def test_a_masked_out_newest_position_serves_nothing(self, tmp_path):
        """The newest position is taken AS-IS — mask included.

        ``keep_mask`` compacts a dropped position out indices and all,
        so the newest SURVIVOR is not the newest position. Serving that
        survivor hands the live loop a stale feature vector wearing a
        stale stamp, with no signal that it is stale — which is the one
        thing this method's contract forbids.
        """
        node = _PricedWindows("w", {**FOREIGN, "lookback": 2,
                                    "max_gap": 5 * MINUTE_MS,
                                    "drop_incomplete": True})
        records = bars("A", range(6))
        assert node.latest_rows(records)["A"]["t"] == BASE_MS + 5 * MINUTE_MS

        # The newest minute prints no usable price: absent, never stale.
        records.append({"sym": "A", "t": BASE_MS + 6 * MINUTE_MS, "px": 0.0})
        assert node.latest_rows(records) == {}

    def test_the_serving_contract_holds_WITHOUT_the_drop_incomplete_knob(
        self, tmp_path
    ):
        """``drop_incomplete`` is a TRAINING emission knob, not a serving
        one — and the serving contract is unconditional.

        The docstring promises the newest COMPLETE row or absence, but
        completeness used to be enforced only when a caller happened to
        set the knob (its default is False). A consumer building a live
        loop on the pack's published serving API with default params was
        handed a partially-warm vector — ``lag_2`` None — where the
        contract promises nothing at all.
        """
        node = ReturnWindows("w", {**FOREIGN, "lookback": 3,
                                   "max_gap": 5 * MINUTE_MS})
        assert node.drop_incomplete() is False, "the default this pins"
        assert node.latest_rows(bars("A", [0, 1, 2])) == {}
        # A fully warm newest position still serves, knob or no knob.
        served = node.latest_rows(bars("A", range(5)))["A"]
        assert served["t"] == BASE_MS + 4 * MINUTE_MS
        assert all(served[f"lag_{i}"] is not None for i in range(3))

    def test_the_training_emission_still_keeps_its_warm_up_rows(self, tmp_path):
        """The other half: serving completeness must not leak into what
        ``run`` emits, which is what the knob actually governs."""
        rows = ReturnWindows("w", {**FOREIGN, "lookback": 3,
                                   "max_gap": 5 * MINUTE_MS}).run(
            ctx(tmp_path), {"records": bars("A", range(5))}
        )["rows"]
        assert len(rows) == 5
        assert rows[0]["lag_2"] is None

    def test_a_masked_out_position_further_back_still_serves(self, tmp_path):
        """The rule is about the NEWEST position, not any dropped one —
        a hole behind the newest bar is exactly what the survivors chain
        exists to read through."""
        node = _PricedWindows("w", {**FOREIGN, "lookback": 2,
                                    "max_gap": 5 * MINUTE_MS,
                                    "drop_incomplete": True})
        records = bars("A", [0, 1, 3, 4, 5])
        records.insert(2, {"sym": "A", "t": BASE_MS + 2 * MINUTE_MS, "px": 0.0})
        assert node.latest_rows(records)["A"]["t"] == BASE_MS + 5 * MINUTE_MS


def _session_bars(symbols=3, sessions=40, minutes=390):
    """A benchmark stream shaped like the child's: minute bars, one
    session-length gap a day, prices that never repeat."""
    day = 24 * 60 * MINUTE_MS
    out = []
    for s in range(symbols):
        price = 100.0 + s
        for d in range(sessions):
            base = d * day + 9 * 60 * MINUTE_MS
            for m in range(minutes):
                price += ((m * 7 + d * 13 + s) % 11 - 5) * 0.001
                out.append({"sym": f"S{s}", "t": base + m * MINUTE_MS,
                            "px": price})
    return out


def _per_row_windows(records, lookback, gap_ms):
    """The per-row Python chain the pack's array build replaced.

    Deliberately independent — a benchmark whose reference is the thing
    under test measures nothing. This is the pre-ADR-0040 child node's
    algorithm, trimmed to the same output.
    """
    by_group = {}
    for row in records:
        sym, t, px = row.get("sym"), row.get("t"), row.get("px")
        if (not isinstance(sym, str) or not sym
                or isinstance(t, bool) or not isinstance(t, int)
                or isinstance(px, bool) or not isinstance(px, (int, float))
                or px <= 0):
            continue
        by_group.setdefault(sym, []).append((t, float(px)))
    out = []
    for sym in sorted(by_group):
        series = sorted(by_group[sym])
        chains, chain = [], []
        for i in range(1, len(series)):
            if series[i][0] - series[i - 1][0] > gap_ms:
                if chain:
                    chains.append(chain)
                chain = []
                continue
            chain.append((series[i][0],
                          math.log(series[i][1] / series[i - 1][1])))
        if chain:
            chains.append(chain)
        for chain in chains:
            for i in range(lookback - 1, len(chain) - 1):
                row = {"sym": sym, "t": chain[i][0], "label": chain[i + 1][1]}
                for step in range(lookback):
                    row[f"lag_{step}"] = chain[i - step][1]
                out.append(row)
    return out


class _CountingNumpy(types.ModuleType):
    """``numpy``, with every attribute the pack reaches for tallied.

    The pack imports numpy INSIDE each function (the tier rule keeps the
    dependency out of import time), so every ``np.<op>`` it performs is a
    fresh ``sys.modules`` lookup followed by one attribute read on the
    module. Standing in for the module therefore counts the pack's
    whole-array operations, and nothing else — no clock, no scheduler.
    """

    def __init__(self, real):
        super().__init__(real.__name__)
        self._real = real
        self.touches = 0

    def __getattr__(self, name):
        self.touches += 1
        return getattr(self._real, name)


def _numpy_ops(fn):
    """Count the numpy operations one call performs (int)."""
    import numpy as real

    proxy = _CountingNumpy(real)
    saved = sys.modules["numpy"]
    sys.modules["numpy"] = proxy
    try:
        fn()
    finally:
        sys.modules["numpy"] = saved
    return proxy.touches


class TestVectorization:
    """The 2M-bar benchmark, measured — not asserted (ADR-0037's spirit).

    Measured by OPERATION COUNT, not by the clock. A wall-clock ratio
    against a different workload (the per-row chain) is a load detector
    rather than a regression detector: the array build loses more to
    memory and CPU contention than a pure-Python loop does, so a bound
    tight enough to catch the 1.2x first cut of this port failed in 6 of
    6 concurrent runs on this box while passing 5 of 5 alone, and a bound
    loose enough to survive contention could no longer catch it. The
    property the card actually asked for — whole-array work, not per-row
    work — is exactly expressible as a count that does not move when the
    rows do, and that count is the same on a busy box as on an idle one.

    For the record, wall clock WAS measured while these were written:
    0.79-0.82x the per-row chain here and 0.79x on the child's own 468k
    -bar shape, with the screen at ~1.42x the unscreened build.
    """

    def test_apply_runs_once_per_segment_not_once_per_row(self, tmp_path):
        node = _CountingWindows(
            "w",
            {
                **FOREIGN,
                "lookback": 30,
                "max_gap": 5 * MINUTE_MS,
                "causality_check": False,
            },
        )
        minutes = [m for m in range(2000) if m % 500 >= 10]  # ten-minute holes
        node.run(ctx(tmp_path), {"records": bars("A", minutes)})
        assert len(node.calls) == 4, node.calls
        assert sum(node.calls) == len(minutes)

    def test_the_window_build_holds_arrays_not_python_floats(self, tmp_path):
        n_rows = 60_000
        records = bars("A", range(n_rows))
        node = ReturnWindows(
            "w",
            {
                **FOREIGN,
                "lookback": 30,
                "max_gap": 5 * MINUTE_MS,
                "drop_incomplete": True,
                "causality_check": False,
            },
        )
        tracemalloc.start()
        try:
            rows = node.run(ctx(tmp_path), {"records": records})["rows"]
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert len(rows) == n_rows - 31
        # The rows themselves are 31 keys of dict each and dominate the
        # RESIDENT cost (~1.7 kB/row, measured); the budget is about what
        # the build holds ON TOP of them. Measured 2.27 kB/row peak with
        # whole-array ops; the per-cell version this replaced measured
        # ~3x the wall time for the same peak, and a per-row Python chain
        # over 2M bars is the defect ADR-0037 costed at 650 B/row.
        assert peak / n_rows < 2800, f"peak {peak / n_rows:.0f} B/row"
        assert _current / n_rows < 2000, f"resident {_current / n_rows:.0f} B/row"

    def test_the_build_s_numpy_work_counts_SEGMENTS_not_ROWS(self, tmp_path):
        """The vectorization property itself, as a number.

        A whole-array build performs a FIXED set of operations per
        segment however long that segment is; a per-row chain performs
        one per row. So the count is taken at two sizes forty times
        apart and must be IDENTICAL — the regression this replaces a
        stopwatch with, and the one the first cut of this port (1.2x the
        loop it replaced) would have failed on the spot.

        The second half keeps the counter honest: it must move when the
        SEGMENT count moves, or a counter stuck at zero would pass the
        first half forever.
        """
        node = ReturnWindows(
            "w",
            {**FOREIGN, "lookback": 30, "max_gap": 5 * MINUTE_MS,
             "drop_incomplete": True, "causality_check": False},
        )
        run_ctx = ctx(tmp_path)

        def ops(minutes):
            records = bars("A", minutes)
            return _numpy_ops(lambda: node.run(run_ctx, {"records": records}))

        small, large = ops(range(500)), ops(range(20_000))
        assert small == large, f"{small} ops at 500 rows, {large} at 20000"

        # Four sessions of the same total length: four times the framing,
        # and a count that noticed.
        four = ops([m for m in range(20_000) if m % 500 >= 10])
        assert four > large, f"{four} ops over 4 segments vs {large} over 1"

    def test_the_build_computes_the_windows_the_per_row_chain_did(
        self, tmp_path
    ):
        """Same windows as the loop it replaced, value for value.

        The reference is deliberately independent — a compact
        restatement of the child's pre-ADR-0040 per-row chain — so this
        is the one test that would catch the port computing something
        FASTER and different.
        """
        records = _session_bars(symbols=3, sessions=4)
        node = ReturnWindows(
            "w",
            {**FOREIGN, "lookback": 30, "max_gap": 5 * MINUTE_MS,
             "drop_incomplete": True, "causality_check": False},
        )
        rows = node.run(ctx(tmp_path), {"records": records})["rows"]
        reference = _per_row_windows(records, 30, 5 * MINUTE_MS)
        assert len(rows) == len(reference) > 0
        for row, want in zip(rows, reference):
            assert (row["sym"], row["t"]) == (want["sym"], want["t"])
            assert row["label"] == pytest.approx(want["label"])
            assert [row[f"lag_{i}"] for i in range(30)] == pytest.approx(
                [want[f"lag_{i}"] for i in range(30)]
            )

    def test_the_causality_screen_costs_a_FACTOR_not_an_order(self, tmp_path):
        """The screen is the one cost the ADR added, so it is priced.

        It re-runs ``apply`` on the four default cut prefixes per
        segment, which is real work and not a defect. What would be a
        defect is that work growing with the ROWS — an accidental
        quadratic there would only ever show on the 2M-bar run. So the
        factor is counted, not timed: five ``apply`` calls per segment
        where the unscreened build makes one, a numpy-operation count
        under five times the unscreened build's, and both of them flat
        in the number of rows.
        """
        knobs = {**FOREIGN, "lookback": 30, "max_gap": 5 * MINUTE_MS,
                 "drop_incomplete": True}
        run_ctx = ctx(tmp_path)

        def ops(node, minutes):
            records = bars("A", minutes)
            return _numpy_ops(lambda: node.run(run_ctx, {"records": records}))

        screened = ReturnWindows("w", knobs)
        plain = ReturnWindows("w", {**knobs, "causality_check": False})
        small, large = ops(screened, range(500)), ops(screened, range(20_000))
        assert small == large, "the screen must be flat in the rows too"
        assert large < 5 * ops(plain, range(20_000))

        # And the exact factor, at the seam the screen actually re-runs:
        # one apply per segment plus one per default cut point.
        counting = _CountingWindows("w", knobs)
        counting.run(run_ctx, {"records": bars("A", range(500))})
        assert len(counting.calls) == 5, counting.calls

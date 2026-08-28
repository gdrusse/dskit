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
import tracemalloc
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
    _cut_points,
    _prefix_equal,
    lag,
    lead,
    log_return,
    narrow_params,
    pct_return,
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


class TestVectorization:
    """The 2M-bar benchmark, measured — not asserted (ADR-0037's spirit)."""

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

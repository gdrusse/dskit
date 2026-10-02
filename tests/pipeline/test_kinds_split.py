"""ADR-0215 (numbered 0222 in the log): ``holdout-cut`` and ``rolling-origin-plan``.

Two tier-1 kinds that turn a dated cohort into the evaluation protocol:
the holdout is locked FIRST (the last ``ceil(fraction x dates)`` dates,
with every dev row whose label reaches it purged), and the dev dates
are then cut into rolling-origin folds sized by DATE COUNT, the oldest
``warmup_folds`` of them the warm-up (the only place a choice is made)
and the rest scored (a clean simulation with every choice frozen).

What is pinned here, by the ADR's own list:

* the contract: roles, outputs, the closed knob tables, ``__all__``,
  the ``forbidden`` serving class, registration;
* every refusal, each naming node, row and field;
* the cut sizes where float and decimal arithmetic disagree, the purge
  boundary, the total order of the emitted rows;
* the fold geometry (sizes, end-anchoring, the embargo as the strict
  before of ``driver._fold_splits``, disjoint windows), the roles, the
  UNION-counted ``warmup_weeks`` and the warm-up/scored seam;
* one literal golden fold table, and the whole chain under
  ``run_document``.

Rows here are small in-memory dicts on a daily calendar. The committed
QQQ rows are the child slice's business, not the toolkit's.
"""

import json
import random
from datetime import date, timedelta

import pytest

import dskit.pipeline as pipeline
from dskit.pipeline import kinds_split
from dskit.pipeline.base import ConfigError, OutputsConfig
from dskit.pipeline.document import NodeSpec, PipelineDocument, WalkForwardSpec
from dskit.pipeline.driver import _fold_splits, run_document
from dskit.pipeline.kinds_flow import register as register_flow
from dskit.pipeline.kinds_split import (
    HoldoutCut,
    RollingOriginPlan,
    _DatedCohort,
    label_reaches,
    register,
)
from dskit.pipeline.kinds_table import register as register_table
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry

ASOF = "2026-01-01"
DAY_MS = 24 * 60 * 60 * 1000
FIRST_DAY = date(2026, 1, 1)

#: The two kinds this module ships, by registered name.
SPLIT_KINDS = ("holdout-cut", "rolling-origin-plan")

#: The plan's knobs; the cut's are the first two plus ``fraction``.
PLAN_KNOBS = (
    "date_field",
    "embargo_days",
    "end_field",
    "holdout_start",
    "step_n",
    "train_n",
    "val_n",
    "warmup_folds",
)
CUT_KNOBS = ("date_field", "end_field", "fraction")


def day(n, start=FIRST_DAY):
    """The ISO date ``n`` calendar days after ``start``."""
    return (start + timedelta(days=n)).isoformat()


def daily_rows(n, lag=0, start=FIRST_DAY):
    """``n`` rows on consecutive calendar days, each labelled ``lag`` days on."""
    return [
        {"quote_date": day(i, start), "settle": day(i + lag, start), "x": i}
        for i in range(n)
    ]


def cut_params(**over):
    """A legal ``holdout-cut`` params dict."""
    return {"date_field": "quote_date", "end_field": "settle", "fraction": 0.2, **over}


def plan_params(**over):
    """A legal ``rolling-origin-plan`` params dict (the golden table's)."""
    return {
        "date_field": "quote_date",
        "end_field": "settle",
        "holdout_start": "2026-02-10",
        "embargo_days": 2,
        "step_n": 5,
        "train_n": 10,
        "val_n": 5,
        "warmup_folds": 2,
        **over,
    }


@pytest.fixture
def ctx(tmp_path):
    """A minimal run frame; neither kind reads it."""
    return NodeContext(name="split", asof=ASOF, run_dir=str(tmp_path))


def run_cut(rows, ctx, **over):
    """Run ``holdout-cut`` on ``rows`` and return its outputs."""
    return HoldoutCut("cut", cut_params(**over)).run(ctx, {"records": rows})


def run_plan(rows, ctx, **over):
    """Run ``rolling-origin-plan`` on ``rows`` and return its outputs."""
    return RollingOriginPlan("plan", plan_params(**over)).run(ctx, {"records": rows})


def plan_folds(n, ctx, lag=0, **over):
    """The fold table of a daily panel of ``n`` dates, ``lag``-day labels."""
    return run_plan(daily_rows(n, lag=lag), ctx, **over)["records"]


def iso_weeks(iso_dates):
    """Distinct (ISO year, ISO week) of ISO date strings, written independently."""
    return len({date.fromisoformat(d).isocalendar()[:2] for d in iso_dates})


# ---------------------------------------------------------------------------
# the one purge rule
# ---------------------------------------------------------------------------


class TestLabelReaches:
    def test_an_end_on_the_start_reaches_it(self):
        assert label_reaches("2026-01-05", "2026-01-05") is True

    def test_an_end_a_day_earlier_does_not(self):
        assert label_reaches("2026-01-04", "2026-01-05") is False

    def test_a_later_end_reaches_it(self):
        assert label_reaches("2026-02-01", "2026-01-05") is True

    def test_date_objects_follow_the_same_rule(self):
        assert label_reaches(date(2026, 1, 5), date(2026, 1, 5)) is True
        assert label_reaches(date(2026, 1, 4), date(2026, 1, 5)) is False


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


class TestContract:
    def test_the_module_exports_exactly_its_surface(self):
        assert set(kinds_split.__all__) == {
            "HoldoutCut",
            "RollingOriginPlan",
            "label_reaches",
            "register",
        }

    @pytest.mark.parametrize("cls", [HoldoutCut, RollingOriginPlan])
    def test_each_kind_is_a_transform_with_records_and_metrics(self, cls):
        assert cls.role == "transform"
        assert cls.outputs == ("records", "metrics")
        assert cls.__module__ == "dskit.pipeline.kinds_split"

    def test_the_knob_tables_are_closed(self):
        assert set(HoldoutCut._PARAMS) == set(CUT_KNOBS)
        assert set(RollingOriginPlan._PARAMS) == set(PLAN_KNOBS)

    @pytest.mark.parametrize("cls", [HoldoutCut, RollingOriginPlan])
    def test_serving_is_forbidden_for_every_params_and_evidence(self, cls):
        # a development-time protocol node: a served tick never re-cuts
        assert cls.serving_effect({}, {}) == "forbidden"
        evidence = {"mode": "load", "artifact_pinned": True}
        assert cls.serving_effect({}, evidence) == "forbidden"

    def test_the_shared_base_is_abstract(self):
        class Incomplete(_DatedCohort):
            """Defines only run: the base's hook is missing."""

            def run(self, ctx, inputs):
                return {}

        with pytest.raises(TypeError):
            Incomplete("k", cut_params())

    def test_both_kinds_share_the_one_base(self):
        assert issubclass(HoldoutCut, _DatedCohort)
        assert issubclass(RollingOriginPlan, _DatedCohort)
        assert issubclass(_DatedCohort, Node)

    @pytest.mark.parametrize(
        "make", [lambda: HoldoutCut("cut", cut_params()), lambda: RollingOriginPlan("p", plan_params())]
    )
    def test_the_records_port_must_be_a_list_or_tuple(self, make):
        node = make()
        assert node.validate_inputs({"records": [{}]}) == []
        assert node.validate_inputs({"records": ({},)}) == []
        for bad in (None, "rows", {"a": 1}, (r for r in [{}])):
            assert node.validate_inputs({"records": bad})


class TestRegister:
    def test_registers_exactly_the_two_unowned(self):
        reg = register(NodeKindRegistry())
        assert set(reg.kinds()) == set(SPLIT_KINDS)
        assert reg.get("holdout-cut") == (HoldoutCut, False)
        assert reg.get("rolling-origin-plan") == (RollingOriginPlan, False)

    def test_idempotent_and_never_shadows(self):
        reg = NodeKindRegistry()
        reg.register("holdout-cut", _Source)  # someone got there first
        register(reg)
        register(reg)  # a second call does not raise a duplicate
        assert reg.get("holdout-cut") == (_Source, False)
        assert reg.get("rolling-origin-plan") == (RollingOriginPlan, False)

    def test_defaults_to_the_global_registry(self, monkeypatch):
        private = NodeKindRegistry()
        monkeypatch.setattr(kinds_split, "DEFAULT_NODE_KINDS", private)
        assert register() is private
        assert set(private.kinds()) == set(SPLIT_KINDS)


class TestReachableFromThePackage:
    def test_importing_the_package_registers_both_kinds(self):
        assert set(SPLIT_KINDS) <= set(pipeline.DEFAULT_NODE_KINDS.kinds())
        assert pipeline.DEFAULT_NODE_KINDS.get("holdout-cut") == (HoldoutCut, False)
        assert pipeline.DEFAULT_NODE_KINDS.get("rolling-origin-plan") == (
            RollingOriginPlan,
            False,
        )

    def test_the_package_exports_the_two_classes(self):
        assert pipeline.HoldoutCut is HoldoutCut
        assert pipeline.RollingOriginPlan is RollingOriginPlan
        assert "HoldoutCut" in pipeline.__all__
        assert "RollingOriginPlan" in pipeline.__all__


# ---------------------------------------------------------------------------
# holdout-cut: params
# ---------------------------------------------------------------------------


class TestHoldoutCutParams:
    def test_the_legal_params_validate_clean(self):
        assert HoldoutCut.validate_params(cut_params()) == []

    @pytest.mark.parametrize("knob", CUT_KNOBS)
    def test_every_knob_is_required_and_named(self, knob):
        params = {k: v for k, v in cut_params().items() if k != knob}
        problems = HoldoutCut.validate_params(params)
        assert any(knob in p for p in problems), problems

    def test_an_unknown_knob_is_refused_by_name(self):
        problems = HoldoutCut.validate_params(cut_params(typo_fraction=0.2))
        assert any("typo_fraction" in p for p in problems)

    @pytest.mark.parametrize("bad", [0, 0.0, 1, 1.0, -0.1, 1.5, True, "0.2", None, float("nan"), float("inf"), [0.2]])
    def test_fraction_must_be_strictly_between_zero_and_one(self, bad):
        problems = HoldoutCut.validate_params(cut_params(fraction=bad))
        assert any("fraction" in p for p in problems), (bad, problems)

    @pytest.mark.parametrize("good", [0.2, 0.5, 0.999, 1e-05])
    def test_a_fraction_inside_the_open_interval_is_legal(self, good):
        assert HoldoutCut.validate_params(cut_params(fraction=good)) == []

    @pytest.mark.parametrize("knob", ["date_field", "end_field"])
    @pytest.mark.parametrize("bad", ["", 5, True, ["a"]])
    def test_the_field_names_are_non_empty_strings(self, knob, bad):
        problems = HoldoutCut.validate_params(cut_params(**{knob: bad}))
        assert any(knob in p for p in problems)

    def test_a_reference_passes_plan_time_and_is_rechecked_when_materialized(self):
        params = cut_params(fraction="$cfg.table.fraction")
        assert HoldoutCut.validate_params(params) == []
        # the materialized value goes through the same validator at construction
        with pytest.raises(ConfigError, match="fraction"):
            HoldoutCut("cut", cut_params(fraction=1.5))


# ---------------------------------------------------------------------------
# holdout-cut: sizes (decimal arithmetic, never the binary float)
# ---------------------------------------------------------------------------


class TestHoldoutCutSizes:
    @pytest.mark.parametrize(
        ("fraction", "n_dates", "size"),
        [
            (0.07, 100, 7),  # 0.07 * 100 == 7.000000000000001 in binary: 8
            (0.14, 150, 21),  # float gives 22
            (0.55, 100, 55),  # float gives 56
            (0.2, 1497, 300),  # non-integral: ceil(299.4)
            (0.5, 4, 2),
            (0.9, 10, 9),
            (0.001, 1000, 1),
        ],
    )
    def test_the_size_is_the_ceiling_of_the_decimal_product(self, fraction, n_dates, size, ctx):
        out = run_cut(daily_rows(n_dates), ctx, fraction=fraction)
        metrics = out["metrics"]
        assert metrics["holdout_dates"] == size
        assert metrics["holdout_start"] == day(n_dates - size)
        assert metrics["holdout_end"] == day(n_dates - 1)
        assert metrics["dev_dates"] == n_dates - size
        assert len(out["records"]) == n_dates - size

    def test_a_float_subclass_with_a_numpy_style_repr_still_cuts_exactly(self, ctx):
        class NumpyLike(float):
            def __repr__(self):
                return f"np.float64({float.__repr__(self)})"

        out = run_cut(daily_rows(100), ctx, fraction=NumpyLike(0.07))
        assert out["metrics"]["holdout_dates"] == 7

    def test_the_size_counts_distinct_dates_not_rows(self, ctx):
        rows = [r for r in daily_rows(10) for _ in range(3)]  # 3 rows per date
        out = run_cut(rows, ctx, fraction=0.3)
        assert out["metrics"]["holdout_dates"] == 3
        assert out["metrics"]["dev_dates"] == 7
        assert len(out["records"]) == 21

    def test_every_dev_date_precedes_the_holdout_start(self, ctx):
        out = run_cut(daily_rows(30), ctx, fraction=0.3)
        start = out["metrics"]["holdout_start"]
        assert all(r["quote_date"] < start for r in out["records"])


# ---------------------------------------------------------------------------
# holdout-cut: the purge
# ---------------------------------------------------------------------------


class TestHoldoutCutPurge:
    def test_a_label_ending_on_the_holdout_start_is_purged(self, ctx):
        # 10 dates, 3 held out (day 7..9). Every label ends one day on.
        out = run_cut(daily_rows(10, lag=1), ctx, fraction=0.3)
        assert out["metrics"]["holdout_start"] == day(7)
        kept_dates = [r["quote_date"] for r in out["records"]]
        assert day(6) not in kept_dates  # its label ends on day 7: purged
        assert day(5) in kept_dates  # its label ends on day 6: a day earlier, kept
        assert out["metrics"]["purged_rows"] == 1
        assert out["metrics"]["dev_dates"] == 6

    def test_a_fully_purged_date_leaves_dev_dates_and_counts_its_rows(self, ctx):
        rows = daily_rows(10, lag=1)
        rows.append({"quote_date": day(6), "settle": day(7), "x": "twin"})
        out = run_cut(rows, ctx, fraction=0.3)
        assert out["metrics"]["purged_rows"] == 2  # both day-6 rows
        assert out["metrics"]["dev_dates"] == 6  # day 6 is no longer a dev date
        assert day(6) not in {r["quote_date"] for r in out["records"]}

    def test_a_partly_purged_date_stays_a_dev_date(self, ctx):
        rows = daily_rows(10, lag=1)
        rows.append({"quote_date": day(6), "settle": day(6), "x": "short"})
        out = run_cut(rows, ctx, fraction=0.3)
        assert out["metrics"]["purged_rows"] == 1
        assert out["metrics"]["dev_dates"] == 7
        assert [r["x"] for r in out["records"] if r["quote_date"] == day(6)] == ["short"]

    def test_the_purge_never_looks_at_a_holdout_rows_label(self, ctx):
        rows = daily_rows(10, lag=0)
        rows[8]["settle"] = day(500)  # a held-out row with a distant label
        out = run_cut(rows, ctx, fraction=0.3)
        assert out["metrics"]["purged_rows"] == 0

    def test_a_purge_that_leaves_no_dev_rows_refuses(self, ctx):
        # 2 dates, 1 held out; date 1's label reaches date 2.
        rows = [
            {"quote_date": day(0), "settle": day(1), "x": 0},
            {"quote_date": day(1), "settle": day(1), "x": 1},
        ]
        with pytest.raises(ValueError, match="cut: .*no dev rows"):
            run_cut(rows, ctx, fraction=0.5)

    def test_a_cut_that_holds_out_every_date_refuses(self, ctx):
        with pytest.raises(ValueError, match="cut: .*no dev rows"):
            run_cut(daily_rows(1), ctx, fraction=0.5)


# ---------------------------------------------------------------------------
# holdout-cut: the emitted rows and metrics
# ---------------------------------------------------------------------------


class TestHoldoutCutOutput:
    def test_the_metrics_are_exactly_the_dates_and_counts_values(self, ctx):
        out = run_cut(daily_rows(20), ctx, fraction=0.25)
        assert set(out["metrics"]) == {
            "holdout_start",
            "holdout_end",
            "holdout_dates",
            "holdout_weeks",
            "dev_dates",
            "purged_rows",
            "holdout_rows",
            "panel_rows",
        }
        assert set(out) == {"records", "metrics"}

    def test_row_counts_cover_the_holdout_and_the_whole_panel(self, ctx):
        out = run_cut(daily_rows(20), ctx, fraction=0.25)
        assert out["metrics"]["panel_rows"] == 20
        assert out["metrics"]["holdout_rows"] == out["metrics"]["holdout_dates"]

    def test_rows_come_out_sorted_by_date_then_canonical_json(self, ctx):
        rows = [
            {"quote_date": day(1), "settle": day(1), "x": 2},
            {"quote_date": day(0), "settle": day(0), "x": 9},
            {"quote_date": day(1), "settle": day(1), "x": 1},
            {"quote_date": day(0), "settle": day(0), "x": 10},
            *daily_rows(10, start=date(2026, 3, 1)),
        ]
        out = run_cut(rows, ctx, fraction=0.5)
        keys = [
            (r["quote_date"], json.dumps(r, sort_keys=True, separators=(",", ":")))
            for r in out["records"]
        ]
        assert keys == sorted(keys)
        # canonical JSON puts "x":10 before "x":9 (text order): the total order
        day0 = [r["x"] for r in out["records"] if r["quote_date"] == day(0)]
        assert day0 == [10, 9]

    def test_shuffled_duplicate_date_input_gives_equal_rows_and_metrics(self, ctx):
        base = []
        for i in range(40):
            for twin in range(3):
                base.append(
                    {"quote_date": day(i), "settle": day(i + 1), "x": i, "twin": twin}
                )
        expected = run_cut(base, ctx, fraction=0.25)
        for seed in range(5):
            shuffled = list(base)
            random.Random(seed).shuffle(shuffled)
            assert run_cut(shuffled, ctx, fraction=0.25) == expected

    def test_rows_are_passed_through_untouched(self, ctx):
        rows = daily_rows(10)
        snapshot = json.dumps(rows)
        out = run_cut(rows, ctx, fraction=0.3)
        assert json.dumps(rows) == snapshot
        assert all(any(r is source for source in rows) for r in out["records"])

    def test_no_holdout_row_value_reaches_an_output(self, ctx):
        rows = daily_rows(10)
        for row in rows[7:]:
            row["secret"] = "HOLDOUT-ONLY-VALUE"
        out = run_cut(rows, ctx, fraction=0.3)
        assert "HOLDOUT-ONLY-VALUE" not in json.dumps(out)

    @pytest.mark.parametrize(
        ("held_out", "weeks"),
        [
            ([("2020-12-31"), ("2021-01-01")], 1),  # both ISO 2020-W53
            ([("2020-12-31"), ("2021-01-01"), ("2021-01-04")], 2),  # + 2021-W01
        ],
    )
    def test_holdout_weeks_are_iso_weeks_across_a_year_boundary(self, held_out, weeks, ctx):
        dev = ["2020-12-01", "2020-12-02", "2020-12-03", "2020-12-04", "2020-12-07"]
        rows = [{"quote_date": d, "settle": d, "x": i} for i, d in enumerate(dev + held_out)]
        out = run_cut(rows, ctx, fraction=len(held_out) / len(rows) - 0.01)
        assert out["metrics"]["holdout_dates"] == len(held_out)
        assert out["metrics"]["holdout_weeks"] == weeks

    def test_it_logs_through_the_node_logger(self, ctx, caplog):
        with caplog.at_level("INFO", logger="dskit.pipeline.cut"):
            run_cut(daily_rows(10), ctx, fraction=0.3)
        assert any("holdout" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# the shared row rules (both kinds): a refusal names node, row and field
# ---------------------------------------------------------------------------


def _both(rows, ctx):
    """Run both kinds over ``rows``; yield (kind key, thunk) pairs."""
    return (
        ("cut", lambda: run_cut(rows, ctx)),
        ("plan", lambda: run_plan(rows, ctx)),
    )


class TestSharedRowRefusals:
    @pytest.mark.parametrize(
        ("mutate", "field"),
        [
            (lambda r: r.pop("quote_date"), "quote_date"),
            (lambda r: r.pop("settle"), "settle"),
            (lambda r: r.update(quote_date="2026-02-30"), "quote_date"),
            (lambda r: r.update(quote_date=20260105), "quote_date"),
            (lambda r: r.update(quote_date="20260105"), "quote_date"),
            (lambda r: r.update(quote_date="2026-1-5"), "quote_date"),
            (lambda r: r.update(quote_date=None), "quote_date"),
            (lambda r: r.update(quote_date="2026-01-05T00:00:00"), "quote_date"),
            (lambda r: r.update(settle="not-a-date"), "settle"),
            (lambda r: r.update(settle=None), "settle"),
            (lambda r: r.update(settle=date(2026, 1, 5)), "settle"),
        ],
    )
    def test_a_bad_row_field_names_node_row_and_field(self, mutate, field, ctx):
        rows = daily_rows(12)
        mutate(rows[3])
        for key, thunk in _both(rows, ctx):
            with pytest.raises(ValueError) as caught:
                thunk()
            message = str(caught.value)
            assert message.startswith(f"{key}: "), message
            assert "row 3" in message, message
            assert repr(field) in message, message

    def test_a_label_ending_before_its_entry_is_the_defect(self, ctx):
        rows = daily_rows(12)
        rows[5]["settle"] = day(4)  # ends a day BEFORE its own entry date
        for key, thunk in _both(rows, ctx):
            with pytest.raises(ValueError, match=rf"{key}: row 5 .*'settle'"):
                thunk()

    def test_a_same_session_label_is_lawful(self, ctx):
        rows = daily_rows(12, lag=0)  # end == date
        assert run_cut(rows, ctx)["metrics"]["dev_dates"] > 0

    def test_a_non_mapping_row_is_refused_by_index(self, ctx):
        rows = daily_rows(12)
        rows[2] = ("2026-01-03", "2026-01-03")
        for key, thunk in _both(rows, ctx):
            with pytest.raises(ValueError, match=rf"{key}: row 2 .*not a mapping"):
                thunk()

    def test_an_empty_cohort_refuses(self, ctx):
        with pytest.raises(ValueError, match="cut: "):
            run_cut([], ctx)
        with pytest.raises(ValueError, match="plan: "):
            run_plan([], ctx)

    def test_the_first_offending_row_is_the_one_named(self, ctx):
        rows = daily_rows(12)
        rows[4]["quote_date"] = None
        rows[9]["quote_date"] = None
        with pytest.raises(ValueError, match="cut: row 4 "):
            run_cut(rows, ctx)

    def test_an_unserializable_dev_row_is_refused_by_index(self, ctx):
        rows = daily_rows(10)
        rows[2]["x"] = {1, 2}  # a set has no JSON form: no canonical order
        with pytest.raises(ValueError, match="cut: row 2 "):
            run_cut(rows, ctx, fraction=0.3)


# ---------------------------------------------------------------------------
# rolling-origin-plan: params
# ---------------------------------------------------------------------------


class TestPlanParams:
    def test_the_legal_params_validate_clean(self):
        assert RollingOriginPlan.validate_params(plan_params()) == []

    @pytest.mark.parametrize("knob", PLAN_KNOBS)
    def test_every_knob_is_required_and_named(self, knob):
        params = {k: v for k, v in plan_params().items() if k != knob}
        problems = RollingOriginPlan.validate_params(params)
        assert any(knob in p for p in problems), problems

    def test_an_unknown_knob_is_refused_by_name(self):
        problems = RollingOriginPlan.validate_params(plan_params(fold_count=3))
        assert any("fold_count" in p for p in problems)

    @pytest.mark.parametrize("knob", ["embargo_days", "warmup_folds"])
    @pytest.mark.parametrize("bad", ["x", True, False, -1, 2.5, None, [1]])
    def test_the_zero_floor_counts_refuse_non_counts(self, knob, bad):
        problems = RollingOriginPlan.validate_params(plan_params(**{knob: bad}))
        assert any(knob in p for p in problems), (bad, problems)

    @pytest.mark.parametrize("knob", ["step_n", "train_n", "val_n"])
    @pytest.mark.parametrize("bad", ["x", True, 0, -3, 2.5, None])
    def test_the_one_floor_counts_refuse_non_counts(self, knob, bad):
        params = plan_params(**{knob: bad})
        problems = RollingOriginPlan.validate_params(params)
        assert any(knob in p for p in problems), (bad, problems)

    @pytest.mark.parametrize("knob", ["embargo_days", "step_n", "train_n", "val_n", "warmup_folds"])
    def test_an_integral_float_is_a_legal_count(self, knob):
        # 5.0 everywhere keeps step_n >= val_n: only the type is on trial
        assert RollingOriginPlan.validate_params(plan_params(**{knob: 5.0})) == []

    def test_a_step_shorter_than_the_val_window_refuses(self):
        problems = RollingOriginPlan.validate_params(plan_params(step_n=4, val_n=5))
        assert any("step_n" in p and "val_n" in p for p in problems)

    def test_a_step_equal_to_or_wider_than_the_window_is_legal(self):
        assert RollingOriginPlan.validate_params(plan_params(step_n=5, val_n=5)) == []
        assert RollingOriginPlan.validate_params(plan_params(step_n=10, val_n=5)) == []

    @pytest.mark.parametrize("bad", ["", "2026-02-30", "20260210", 20260210, None, True])
    def test_holdout_start_must_be_a_real_iso_date(self, bad):
        problems = RollingOriginPlan.validate_params(plan_params(holdout_start=bad))
        assert any("holdout_start" in p for p in problems)

    def test_references_pass_plan_time_and_are_rechecked_when_materialized(self):
        wired = plan_params(
            holdout_start="$cut.metrics.holdout_start",
            embargo_days="$dte.table.all",
            step_n="$cfg.table.step",
        )
        assert RollingOriginPlan.validate_params(wired) == []
        with pytest.raises(ConfigError, match="embargo_days"):
            RollingOriginPlan("plan", plan_params(embargo_days=-1))
        with pytest.raises(ConfigError, match="holdout_start"):
            RollingOriginPlan("plan", plan_params(holdout_start="2026-02-30"))

    def test_no_knob_has_a_default_in_code(self):
        # every knob is REQUIRED: an empty block names all eight
        problems = RollingOriginPlan.validate_params({})
        for knob in PLAN_KNOBS:
            assert any(knob in p for p in problems), knob


# ---------------------------------------------------------------------------
# rolling-origin-plan: the golden fold table
# ---------------------------------------------------------------------------


GOLDEN_FOLDS = [
    {
        "fold": 1,
        "role": "warmup",
        "train_start": "2026-01-04",
        "train_end": "2026-01-13",
        "val_start": "2026-01-16",
        "val_end": "2026-01-20",
        "train_dates": 10,
        "val_dates": 5,
        "train_weeks": 3,
        "val_weeks": 2,
        "purged": 2,
    },
    {
        "fold": 2,
        "role": "warmup",
        "train_start": "2026-01-09",
        "train_end": "2026-01-18",
        "val_start": "2026-01-21",
        "val_end": "2026-01-25",
        "train_dates": 10,
        "val_dates": 5,
        "train_weeks": 2,
        "val_weeks": 1,
        "purged": 2,
    },
    {
        "fold": 3,
        "role": "scored",
        "train_start": "2026-01-14",
        "train_end": "2026-01-23",
        "val_start": "2026-01-26",
        "val_end": "2026-01-30",
        "train_dates": 10,
        "val_dates": 5,
        "train_weeks": 2,
        "val_weeks": 1,
        "purged": 2,
    },
]


class TestGoldenFoldTable:
    def test_thirty_daily_dates_make_the_literal_table(self, ctx):
        out = run_plan(daily_rows(30, lag=2), ctx)
        assert out["records"] == GOLDEN_FOLDS
        assert out["metrics"] == {
            "folds": 3,
            "scored": 1,
            "warmup_weeks": 2,  # the UNION: W03 and W04 (the rows sum to 3)
            "scored_start": "2026-01-26",
        }
        assert set(out) == {"records", "metrics"}

    def test_the_table_is_oldest_first_and_one_based(self, ctx):
        folds = run_plan(daily_rows(30, lag=2), ctx)["records"]
        assert [f["fold"] for f in folds] == [1, 2, 3]
        assert [f["val_start"] for f in folds] == sorted(f["val_start"] for f in folds)


# ---------------------------------------------------------------------------
# rolling-origin-plan: the geometry
# ---------------------------------------------------------------------------


GEOMETRIES = [
    # (n_dates, train_n, val_n, step_n, embargo)
    (60, 10, 5, 5, 0),
    (60, 10, 5, 5, 3),
    (73, 20, 5, 5, 7),
    (80, 15, 6, 10, 4),  # step_n wider than val_n: val windows leave gaps
    (50, 7, 7, 7, 0),
    (41, 12, 4, 8, 2),  # step_n == 2 x val_n
]


class TestFoldGeometry:
    @pytest.mark.parametrize(("n", "train_n", "val_n", "step_n", "embargo"), GEOMETRIES)
    def test_windows_hold_the_declared_counts_and_step_apart(
        self, n, train_n, val_n, step_n, embargo, ctx
    ):
        folds = plan_folds(
            n, ctx, lag=embargo, embargo_days=embargo, train_n=train_n,
            val_n=val_n, step_n=step_n, warmup_folds=0,
            holdout_start=day(n + embargo + 5),
        )
        assert folds, "the geometry yields at least one fold"
        for f in folds:
            assert f["train_dates"] == train_n
            assert f["val_dates"] == val_n
        # the last val window ends at the last date: END-anchored
        assert folds[-1]["val_end"] == day(n - 1)
        starts = [date.fromisoformat(f["val_start"]) for f in folds]
        assert [(b - a).days for a, b in zip(starts, starts[1:])] == [step_n] * (len(folds) - 1)

    @pytest.mark.parametrize(("n", "train_n", "val_n", "step_n", "embargo"), GEOMETRIES)
    def test_the_dropped_remainder_is_the_oldest_history(
        self, n, train_n, val_n, step_n, embargo, ctx
    ):
        folds = plan_folds(
            n, ctx, lag=embargo, embargo_days=embargo, train_n=train_n,
            val_n=val_n, step_n=step_n, warmup_folds=0,
            holdout_start=day(n + embargo + 5),
        )
        # one more fold further back would have lacked train_n train dates
        oldest_val = (date.fromisoformat(folds[0]["val_start"]) - FIRST_DAY).days
        older_val = oldest_val - step_n
        assert older_val - embargo < train_n  # the next-older window is short

    @pytest.mark.parametrize(("n", "train_n", "val_n", "step_n", "embargo"), GEOMETRIES)
    def test_train_always_ends_before_val_starts_and_windows_are_disjoint(
        self, n, train_n, val_n, step_n, embargo, ctx
    ):
        folds = plan_folds(
            n, ctx, lag=embargo, embargo_days=embargo, train_n=train_n,
            val_n=val_n, step_n=step_n, warmup_folds=0,
            holdout_start=day(n + embargo + 5),
        )
        for f in folds:
            assert f["train_end"] < f["val_start"]
        for a, b in zip(folds, folds[1:]):
            assert a["val_end"] < b["val_start"]  # disjoint, ascending

    def test_a_window_with_too_few_train_dates_is_not_a_fold(self, ctx):
        # 30 dates, train_n 14, embargo 2: val idx 25 (23 dates before the
        # cutoff) and 20 (18) qualify; idx 15 (13 < 14) does not
        folds = plan_folds(30, ctx, lag=2, train_n=14, warmup_folds=0)
        assert [f["val_start"] for f in folds] == [day(20), day(25)]

    def test_the_embargo_excludes_the_cutoff_date_and_keeps_the_day_before(self, ctx):
        folds = plan_folds(30, ctx, lag=2, warmup_folds=0)
        for f in folds:
            val_start = date.fromisoformat(f["val_start"])
            cutoff = val_start - timedelta(days=2)
            assert date.fromisoformat(f["train_end"]) == cutoff - timedelta(days=1)

    @pytest.mark.parametrize("embargo", [0, 1, 4])
    def test_the_train_edge_equals_walkforwards_fold_splits_on_a_daily_panel(
        self, embargo, ctx
    ):
        n = 40
        folds = plan_folds(
            n, ctx, lag=embargo, embargo_days=embargo, warmup_folds=0,
            holdout_start=day(n + embargo + 5),
        )
        for f in folds:
            spec = WalkForwardSpec(
                objective="$score.metrics.loss",
                val_days=5,
                folds=[f["val_start"]],
                embargo_days=embargo,
            )
            splits = _fold_splits(spec, f["val_start"])
            stamp = lambda iso: (date.fromisoformat(iso) - date(1970, 1, 1)).days * DAY_MS
            # the last train date trains; the next cohort date does not
            assert stamp(f["train_end"]) <= splits.train_end_ms
            following = date.fromisoformat(f["train_end"]) + timedelta(days=1)
            assert stamp(following.isoformat()) > splits.train_end_ms

    def test_purged_counts_the_cohort_dates_between_train_and_val(self, ctx):
        folds = plan_folds(30, ctx, lag=3, embargo_days=3, warmup_folds=0)
        assert [f["purged"] for f in folds] == [3] * len(folds)

    def test_a_sparse_panel_counts_dates_not_calendar_days(self, ctx):
        # weekday-only dates: windows hold DATES, so they span weekends
        weekdays = [
            day(i) for i in range(60) if (FIRST_DAY + timedelta(days=i)).weekday() < 5
        ]
        rows = [{"quote_date": d, "settle": d, "x": i} for i, d in enumerate(weekdays)]
        out = run_plan(
            rows, ctx, embargo_days=0, holdout_start=day(80), warmup_folds=0,
            train_n=10, val_n=5, step_n=5,
        )
        for f in out["records"]:
            assert f["train_dates"] == 10 and f["val_dates"] == 5
        assert out["records"][-1]["val_end"] == weekdays[-1]

    def test_duplicate_rows_on_a_date_count_the_date_once(self, ctx):
        once = run_plan(daily_rows(30, lag=2), ctx)
        thrice = run_plan([r for r in daily_rows(30, lag=2) for _ in range(3)], ctx)
        assert thrice == once

    def test_the_result_does_not_depend_on_input_order(self, ctx):
        rows = daily_rows(30, lag=2)
        expected = run_plan(rows, ctx)
        shuffled = list(rows)
        random.Random(11).shuffle(shuffled)
        assert run_plan(shuffled, ctx) == expected

    def test_the_input_rows_are_not_mutated(self, ctx):
        rows = daily_rows(30, lag=2)
        snapshot = json.dumps(rows)
        run_plan(rows, ctx)
        assert json.dumps(rows) == snapshot

    def test_an_integral_float_embargo_is_the_int(self, ctx):
        as_int = plan_folds(30, ctx, lag=2, embargo_days=2, warmup_folds=0)
        as_float = plan_folds(30, ctx, lag=2, embargo_days=2.0, warmup_folds=0)
        assert as_float == as_int

    def test_a_float_embargo_of_seven_is_exactly_seven(self, ctx):
        folds = plan_folds(
            60, ctx, lag=7, embargo_days=7.0, warmup_folds=0, holdout_start=day(80)
        )
        assert folds and [f["purged"] for f in folds] == [7] * len(folds)


# ---------------------------------------------------------------------------
# rolling-origin-plan: the mechanical lock and the embargo bound
# ---------------------------------------------------------------------------


class TestPlanRefusals:
    def test_a_row_dated_on_the_holdout_start_refuses(self, ctx):
        rows = daily_rows(12)
        with pytest.raises(ValueError, match=r"plan: row 6 .*'quote_date'.*holdout_start"):
            run_plan(rows, ctx, holdout_start=day(6), embargo_days=0)

    def test_a_row_dated_after_the_holdout_start_refuses(self, ctx):
        rows = daily_rows(5) + [{"quote_date": day(8), "settle": day(8), "x": 8}]
        with pytest.raises(ValueError, match=r"plan: row 5 .*'quote_date'.*holdout_start"):
            run_plan(rows, ctx, holdout_start=day(6), embargo_days=0)

    def test_a_label_ending_on_the_holdout_start_refuses(self, ctx):
        rows = daily_rows(12, lag=1)
        # row 10's label ends on day 11 == holdout_start
        rows = rows[:11]
        with pytest.raises(ValueError, match=r"plan: row 10 .*'settle'.*holdout_start"):
            run_plan(rows, ctx, holdout_start=day(11), embargo_days=1)

    def test_a_label_ending_a_day_before_the_holdout_start_is_accepted(self, ctx):
        rows = daily_rows(30, lag=2)  # last label ends day 31
        out = run_plan(rows, ctx, holdout_start=day(32))
        assert out["metrics"]["folds"] == 3

    def test_a_label_longer_than_the_embargo_refuses(self, ctx):
        rows = daily_rows(30, lag=2)
        rows[7]["settle"] = day(7 + 3)  # one day over the 2-day embargo
        with pytest.raises(ValueError, match=r"plan: row 7 .*embargo_days"):
            run_plan(rows, ctx)

    def test_a_label_exactly_the_embargo_long_is_accepted(self, ctx):
        run_plan(daily_rows(30, lag=2), ctx)  # end - date == embargo_days == 2

    def test_a_same_session_label_is_accepted_at_zero_embargo(self, ctx):
        out = run_plan(daily_rows(30, lag=0), ctx, embargo_days=0)
        assert out["metrics"]["folds"] >= 3

    def test_too_few_folds_for_the_warmup_refuses(self, ctx):
        # the golden panel makes 3 folds: warmup_folds 3 would leave none scored
        with pytest.raises(ValueError, match=r"plan: .*3 fold.*warmup_folds"):
            run_plan(daily_rows(30, lag=2), ctx, warmup_folds=3)

    def test_a_cohort_shorter_than_one_fold_refuses(self, ctx):
        # an AMZN-shaped cohort: 90 dates against a 450-date train window
        with pytest.raises(ValueError, match=r"plan: .*0 fold"):
            run_plan(
                daily_rows(90),
                ctx,
                train_n=450,
                val_n=40,
                step_n=40,
                embargo_days=0,
                holdout_start=day(200),
            )

    def test_a_materialized_holdout_start_that_is_not_a_date_refuses_at_construction(self):
        with pytest.raises(ConfigError, match="holdout_start"):
            RollingOriginPlan("plan", plan_params(holdout_start="soon"))


# ---------------------------------------------------------------------------
# rolling-origin-plan: roles, the warm-up weeks, and the seam
# ---------------------------------------------------------------------------


class TestRolesAndSeam:
    @pytest.mark.parametrize("warmup", [0, 1, 2])
    def test_the_oldest_folds_are_the_warmup(self, warmup, ctx):
        out = run_plan(daily_rows(30, lag=2), ctx, warmup_folds=warmup)
        roles = [f["role"] for f in out["records"]]
        assert roles == ["warmup"] * warmup + ["scored"] * (3 - warmup)
        assert out["metrics"]["folds"] == 3
        assert out["metrics"]["scored"] == 3 - warmup

    def test_warmup_folds_equal_to_the_fold_count_refuses(self, ctx):
        with pytest.raises(ValueError, match="warmup_folds"):
            run_plan(daily_rows(30, lag=2), ctx, warmup_folds=3)

    @pytest.mark.parametrize("warmup", [0, 1, 2])
    def test_scored_start_is_the_first_scored_folds_val_start(self, warmup, ctx):
        out = run_plan(daily_rows(30, lag=2), ctx, warmup_folds=warmup)
        folds = out["records"]
        first_scored = next(f for f in folds if f["role"] == "scored")
        assert out["metrics"]["scored_start"] == first_scored["val_start"]
        assert all(
            f["val_end"] < out["metrics"]["scored_start"]
            for f in folds
            if f["role"] == "warmup"
        )

    def test_at_zero_warmup_the_seam_is_fold_ones_val_start(self, ctx):
        out = run_plan(daily_rows(30, lag=2), ctx, warmup_folds=0)
        assert out["metrics"]["scored_start"] == out["records"][0]["val_start"]
        assert out["metrics"]["warmup_weeks"] == 0

    def test_at_a_step_twice_the_val_window_the_seam_still_separates(self, ctx):
        out = run_plan(
            daily_rows(60, lag=2), ctx, val_n=4, step_n=8, train_n=10,
            warmup_folds=2, holdout_start=day(80),
        )
        folds = out["records"]
        assert len(folds) >= 4
        warm = [f for f in folds if f["role"] == "warmup"]
        scored = [f for f in folds if f["role"] == "scored"]
        assert len(warm) == 2
        assert max(f["val_end"] for f in warm) < min(f["val_start"] for f in scored)
        assert out["metrics"]["scored_start"] == min(f["val_start"] for f in scored)

    def test_every_warmup_fold_precedes_every_scored_one(self, ctx):
        folds = run_plan(daily_rows(60, lag=2), ctx, warmup_folds=2, holdout_start=day(80))["records"]
        warm = [f for f in folds if f["role"] == "warmup"]
        scored = [f for f in folds if f["role"] == "scored"]
        assert max(f["fold"] for f in warm) < min(f["fold"] for f in scored)
        assert max(f["train_end"] for f in warm) < min(f["val_start"] for f in scored)

    @pytest.mark.parametrize("step_n", [5, 10])
    def test_the_val_sets_are_disjoint(self, step_n, ctx):
        folds = run_plan(
            daily_rows(80, lag=2), ctx, step_n=step_n, warmup_folds=1, holdout_start=day(90)
        )["records"]
        seen = set()
        for f in folds:
            span = {
                day(i)
                for i in range(100)
                if f["val_start"] <= day(i) <= f["val_end"]
            }
            assert not (span & seen)
            seen |= span

    def test_warmup_weeks_is_the_union_not_the_sum(self, ctx):
        out = run_plan(daily_rows(30, lag=2), ctx, warmup_folds=2)
        warm = [f for f in out["records"] if f["role"] == "warmup"]
        assert sum(f["val_weeks"] for f in warm) == 3  # W04 counted twice
        assert out["metrics"]["warmup_weeks"] == 2
        # recomputed independently from the val calendar
        dates = [
            day(i) for i in range(30)
            if any(f["val_start"] <= day(i) <= f["val_end"] for f in warm)
        ]
        assert out["metrics"]["warmup_weeks"] == iso_weeks(dates)

    def test_warmup_weeks_across_a_year_boundary(self, ctx):
        # 2020-12-21 .. 2021-01-14: three folds, the oldest val window is
        # 2020-12-31 .. 2021-01-04 (2020-W53 then 2021-W01)
        start = date(2020, 12, 21)
        rows = daily_rows(25, start=start)
        params = {
            "embargo_days": 0, "train_n": 10, "val_n": 5, "step_n": 5,
            "holdout_start": "2021-02-01",
        }
        one = run_plan(rows, ctx, warmup_folds=1, **params)
        assert one["records"][0]["val_start"] == "2020-12-31"
        assert one["records"][0]["val_end"] == "2021-01-04"
        assert one["records"][0]["val_weeks"] == 2
        assert one["metrics"]["warmup_weeks"] == 2
        two = run_plan(rows, ctx, warmup_folds=2, **params)
        # fold 2's window (01-05..01-09) lies inside 2021-W01: the union adds nothing
        assert two["metrics"]["warmup_weeks"] == 2
        assert sum(f["val_weeks"] for f in two["records"] if f["role"] == "warmup") == 3

    def test_the_iso_week_of_new_years_day_belongs_to_the_old_year(self, ctx):
        rows = daily_rows(25, start=date(2020, 12, 21))
        out = run_plan(
            rows, ctx, embargo_days=0, train_n=10, val_n=5, step_n=5,
            holdout_start="2021-02-01", warmup_folds=0,
        )
        first = out["records"][0]
        assert first["val_weeks"] == iso_weeks(
            [day(i, date(2020, 12, 21)) for i in range(10, 15)]
        )


# ---------------------------------------------------------------------------
# the whole chain under the driver
# ---------------------------------------------------------------------------


class _Source(Node):
    """A test source: emits the rows its params carry."""

    role = "transform"
    outputs = ("records",)

    @classmethod
    def validate_params(cls, params):
        return [] if set(params) == {"rows"} else ["rows is the only param"]

    def run(self, ctx, inputs):
        return {"records": [dict(r) for r in self.params["rows"]]}


def split_registry():
    """A private registry: the flow verbs, both split kinds, the writers."""
    registry = NodeKindRegistry()
    registry.register("dated-rows", _Source)
    return register_table(register(register_flow(registry)))


def chained_document(tmp_path, n_dates=100, lag=7):
    """source -> cut -> (group, max DTE, keyby) -> plan -> records-write."""
    rows = [
        {"quote_date": r["quote_date"], "settle": r["settle"], "dte": lag, "x": r["x"]}
        for r in daily_rows(n_dates, lag=lag)
    ]
    pipeline_ = {
        "source": NodeSpec(uses="dated-rows", params={"rows": rows}),
        "cut": NodeSpec(
            uses="holdout-cut",
            inputs={"records": "$source.records"},
            params=cut_params(),
        ),
        "group": NodeSpec(
            uses="derive",
            inputs={"records": "$cut.records"},
            params={"field": "grp", "cases": [{"when": [], "value": "all"}]},
        ),
        "longest": NodeSpec(
            uses="groupby",
            inputs={"records": "$group.records"},
            params={"keys": ["grp"], "aggregates": {"dte": {"op": "max", "field": "dte"}}},
        ),
        "dte": NodeSpec(
            uses="keyby",
            inputs={"records": "$longest.records"},
            params={"key": "grp", "value": "dte"},
        ),
        "plan": NodeSpec(
            uses="rolling-origin-plan",
            inputs={"records": "$cut.records"},
            params=plan_params(
                holdout_start="$cut.metrics.holdout_start",
                embargo_days="$dte.table.all",
                train_n=20,
                val_n=5,
                step_n=5,
                warmup_folds=2,
            ),
        ),
        "folds": NodeSpec(
            uses="records-write",
            inputs={"records": "$plan.records"},
            params={
                "path": str(tmp_path / "folds.jsonl"),
                "source": "the fold table of the chained e2e",
            },
        ),
    }
    return PipelineDocument(
        name="split-chain",
        pipeline=pipeline_,
        outputs=OutputsConfig(run_root=str(tmp_path / "runs")),
    ), rows


class TestChainUnderTheDriver:
    def run(self, tmp_path, **kw):
        document, rows = chained_document(tmp_path, **kw)
        result = run_document(document, asof=ASOF, registry=split_registry())
        return result, rows

    def test_the_chain_runs_with_both_references_wired(self, tmp_path):
        result, _ = self.run(tmp_path)
        assert result.state == "ran" and result.exit_code == 0, result.error
        cut = result.outputs["cut"]["metrics"]
        assert cut["holdout_dates"] == 20
        assert cut["holdout_start"] == day(80)
        assert result.outputs["dte"]["table"] == {"all": 7}

    def test_every_fold_date_precedes_the_holdout_and_shares_no_date_with_it(self, tmp_path):
        result, rows = self.run(tmp_path)
        start = result.outputs["cut"]["metrics"]["holdout_start"]
        holdout_dates = {r["quote_date"] for r in rows if r["quote_date"] >= start}
        folds = result.outputs["plan"]["records"]
        assert folds
        for f in folds:
            assert f["val_end"] < start
            assert f["train_end"] < start
            span = {
                r["quote_date"]
                for r in rows
                if f["train_start"] <= r["quote_date"] <= f["val_end"]
            }
            assert not (span & holdout_dates)

    def test_the_embargo_is_the_keyed_value_not_a_literal(self, tmp_path):
        result, _ = self.run(tmp_path, lag=7)
        assert {f["purged"] for f in result.outputs["plan"]["records"]} == {7}
        (tmp_path / "three").mkdir()
        result3, _ = self.run(tmp_path / "three", lag=3)
        assert {f["purged"] for f in result3.outputs["plan"]["records"]} == {3}

    def test_the_written_bytes_match_the_digest_in_metrics(self, tmp_path):
        import hashlib

        result, _ = self.run(tmp_path)
        written = (tmp_path / "folds.jsonl").read_bytes()
        assert hashlib.sha256(written).hexdigest() == result.outputs["folds"]["metrics"]["sha256"]
        lines = [json.loads(line) for line in written.decode("utf-8").splitlines()]
        assert lines == result.outputs["plan"]["records"]

    def test_the_roles_and_seam_survive_the_write(self, tmp_path):
        result, _ = self.run(tmp_path)
        folds = result.outputs["plan"]["records"]
        metrics = result.outputs["plan"]["metrics"]
        assert [f["role"] for f in folds[:2]] == ["warmup", "warmup"]
        assert all(f["role"] == "scored" for f in folds[2:])
        assert metrics["scored_start"] == folds[2]["val_start"]
        assert metrics["folds"] == len(folds)

    def test_a_cohort_too_small_for_one_fold_stops_the_run(self, tmp_path):
        result, _ = self.run(tmp_path, n_dates=30)
        assert result.state != "ran"
        assert result.exit_code == 1
        assert result.node_states["plan"] == "error"
        assert "fold" in (result.error or "")

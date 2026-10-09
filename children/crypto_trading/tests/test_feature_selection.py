"""Family and scale ablation selection: the keep rule, the group-to-column grammar, the node, and the config pins.

Synthetic data only. Expectations are restated here in plain Python (an assertion read from the subject asserts
nothing): the scale-bearing column names, the W1 end, the loss, the arm lists.
"""

import copy
import json
import math
import os
import statistics
from importlib.util import find_spec
from types import SimpleNamespace

import pytest

from crypto_trading.feature_selection import AblationSelect, FeatureGroups, FoldScores, KeepRule
import test_minute_features as tmf

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
OBJECTIVE = "$score.metrics.weighted_crps"
FAMILIES = ("ret", "trend", "vol", "range", "shock", "flow", "clock", "holiday", "xasset", "bvol")
GRID = (5, 15, 60, 240, 1440, 10080)
W1_END = "2026-05-01"  # restated on purpose: the owner-approved end of W1, exclusive
W1_END_MS = 1_777_593_600_000
W1_START_MS = 1_687_219_200_000  # 2023-06-20 00:00 UTC
DAY_MS = 86_400_000
CLASSES = ("sequence", "tabular")


def _config(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# synthetic fold scores
# ---------------------------------------------------------------------------


def cutoffs(k):
    return tuple(f"2025-{month:02d}-01" for month in range(1, k + 1))


def folds_of(scores, k=None, objective=OBJECTIVE, select="min"):
    k = k or len(scores)
    return FoldScores(cutoffs=cutoffs(k), scores=tuple(scores), objective=objective, select=select, path="", sha256="0")


def shifted(reference, d):
    return folds_of([r + x for r, x in zip(reference.scores, d)])


REFERENCE = folds_of([1.0 + 0.01 * i for i in range(10)])
HURTS = [0.10, 0.12, 0.09, 0.11, 0.10, 0.13, 0.08, 0.11, 0.10, 0.12]  # mean 0.106, SE 0.0049
NOISY = [0.30, -0.25, 0.22, -0.20, 0.28, -0.27, 0.21, -0.18, 0.26, -0.24]  # mean 0.013, SE 0.0807
HELPS = [-0.10, -0.12, -0.09, -0.11, -0.10, -0.13, -0.08, -0.11, -0.10, -0.12]


def test_dropping_that_hurts_beyond_noise_is_kept():
    verdict = KeepRule(z=1.0, min_folds=8).judge(REFERENCE, shifted(REFERENCE, HURTS))
    assert verdict["keep"] is True
    assert verdict["n"] == 10 and verdict["mean"] == pytest.approx(statistics.mean(HURTS))
    assert verdict["se"] == pytest.approx(statistics.stdev(HURTS) / math.sqrt(10))
    assert verdict["t"] == pytest.approx(verdict["mean"] / verdict["se"])


def test_dropping_that_hurts_only_within_noise_is_dropped():
    verdict = KeepRule(z=1.0, min_folds=8).judge(REFERENCE, shifted(REFERENCE, NOISY))
    assert 0 < verdict["mean"] < verdict["se"]
    assert verdict["keep"] is False


def test_dropping_that_helps_is_dropped_whatever_the_noise():
    verdict = KeepRule(z=1.0, min_folds=8).judge(REFERENCE, shifted(REFERENCE, HELPS))
    assert verdict["mean"] < 0 and verdict["keep"] is False
    assert KeepRule(z=0.0, min_folds=8).judge(REFERENCE, shifted(REFERENCE, HELPS))["keep"] is False


def test_the_threshold_is_z_standard_errors_and_the_boundary_keeps():
    base = KeepRule(z=1.0, min_folds=8).judge(REFERENCE, shifted(REFERENCE, NOISY[:8] + [0.30, 0.25]))
    ratio = base["mean"] / base["se"]
    arm = shifted(REFERENCE, NOISY[:8] + [0.30, 0.25])
    assert KeepRule(z=ratio * 0.999, min_folds=8).judge(REFERENCE, arm)["keep"] is True
    assert KeepRule(z=ratio * 1.001, min_folds=8).judge(REFERENCE, arm)["keep"] is False


def test_zero_z_keeps_any_positive_mean():
    assert KeepRule(z=0.0, min_folds=8).judge(REFERENCE, shifted(REFERENCE, NOISY))["keep"] is True


def test_no_spread_keeps_iff_the_mean_is_positive():
    rule = KeepRule(z=1.0, min_folds=8)
    flat = lambda x: rule.judge(REFERENCE, shifted(REFERENCE, [x] * 10))  # noqa: E731
    assert flat(0.02)["keep"] is True and flat(0.02)["t"] is None
    assert flat(0.0)["keep"] is False
    assert flat(-0.02)["keep"] is False


def test_fewer_folds_than_the_floor_refuse():
    short = folds_of([1.0 + 0.01 * i for i in range(7)])
    with pytest.raises(ValueError, match="at least 8 folds"):
        KeepRule(z=1.0, min_folds=8).judge(short, shifted(short, [0.1] * 7))
    KeepRule(z=1.0, min_folds=7).judge(short, shifted(short, [0.1] * 6 + [0.2]))  # the floor is a param


def test_arms_scored_on_different_folds_refuse():
    other = FoldScores(cutoffs=cutoffs(10)[::-1], scores=REFERENCE.scores, objective=OBJECTIVE, select="min",
                       path="", sha256="0")
    with pytest.raises(ValueError, match="cutoffs"):
        KeepRule(z=1.0, min_folds=8).judge(REFERENCE, other)


def test_arms_with_another_objective_or_direction_refuse():
    with pytest.raises(ValueError, match="objective"):
        KeepRule(z=1.0, min_folds=8).judge(REFERENCE, folds_of(REFERENCE.scores, objective="$other.x"))
    with pytest.raises(ValueError, match="select"):
        KeepRule(z=1.0, min_folds=8).judge(REFERENCE, folds_of(REFERENCE.scores, select="max"))


@pytest.mark.parametrize("bad", [{"z": -1.0, "min_folds": 8}, {"z": "1", "min_folds": 8}, {"z": 1.0, "min_folds": 1},
                                 {"z": 1.0, "min_folds": 2.5}, {"z": float("nan"), "min_folds": 8}])
def test_the_rule_refuses_nonsense_knobs(bad):
    with pytest.raises(ValueError):
        KeepRule(**bad)


def test_worse_than_all_by_more_than_z_se_is_flagged():
    rule = KeepRule(z=1.0, min_folds=8)
    assert rule.worse(REFERENCE, shifted(REFERENCE, HURTS))["worse"] is True
    assert rule.worse(REFERENCE, shifted(REFERENCE, NOISY))["worse"] is False
    assert rule.worse(REFERENCE, shifted(REFERENCE, HELPS))["worse"] is False
    assert rule.worse(REFERENCE, shifted(REFERENCE, [0.0] * 10))["worse"] is False
    assert rule.worse(REFERENCE, shifted(REFERENCE, [0.01] * 10))["worse"] is True  # no spread, positive


# ---------------------------------------------------------------------------
# the group-to-column grammar, against the real node's output
# ---------------------------------------------------------------------------

SMALL_SCALES = [2, 5, 10]
SMALL_FAMILIES = [f for f in FAMILIES if f != "holiday"]  # the calendar library is not needed to test the grammar
SMALL_MF = {k: v for k, v in tmf.SMALL.items() if k not in ("scales", "history_minutes")}


def make_arms(families=SMALL_FAMILIES, scales=SMALL_SCALES):
    arms = {"all": list(families)}
    arms.update({f"minus_{f}": [x for x in families if x != f] for f in families})
    return {"notes": "synthetic", "arms": arms,
            "scale_arms": {f"minus_scale_{s}": [x for x in scales if x != s] for s in scales}}


def real_columns(families, scales, **windows):
    out = tmf.run(tmf.world(80), bvol_rows=tmf.bvol("AAA", 80, 1) + tmf.bvol("BBB", 80, 2), families=list(families),
                  scales=list(scales), **{"history_minutes": 31, **windows})
    return {c for c in out["records"][0] if c.split("__", 1)[0] in FAMILIES}


# windows that ARE minutes of the scale grid (as the shipped defaults all are), restated here on purpose
GRID_WINDOWS = {"ewma_half_lives": [5, 10], "range_windows": [5, 10], "taker_windows": [2, 5]}
WINDOW_KNOBS = tuple(GRID_WINDOWS)
WINDOW_MF = {**SMALL_MF, **GRID_WINDOWS}


# SMALL's own windows (none is a minute of the grid [2, 5, 10]); the check windows are all the taker windows
SMALL_WINDOWS = {"ewma_half_lives": [3], "range_windows": [4, 8], "taker_windows": [3, 6], "taker_check_windows": [3, 6]}


@pytest.fixture(scope="module")
def groups():
    return FeatureGroups.from_probe(make_arms(), SMALL_MF)


def test_the_probe_finds_exactly_the_columns_the_node_emits(groups):
    assert set(groups.columns) == real_columns(SMALL_FAMILIES, SMALL_SCALES)
    assert groups.columns == tuple(dict.fromkeys(groups.columns)), "no duplicate column"


def test_a_scale_owns_the_columns_of_ret_trend_vol_and_xasset_that_carry_it(groups):
    expected = {"ret__r5", "trend__ma_5", "vol__rms_5", "vol__hl_5", "vol__jump_5",
                "xasset__r5", "xasset__rms_5", "xasset__hl_5", "xasset__jump_5", "xasset__pair_chg_5"}
    assert set(groups.columns_of_scale(5)) == expected


def test_a_window_that_is_not_a_scale_belongs_to_no_scale(groups):
    # the ewma half-life 3 and the range windows 4 and 8 sit in names like a scale does, but are other knobs
    assert not set(groups.columns_of_scale(2)) & {"vol__ewma_3", "range__hi_4", "range__lo_8", "flow__taker_3"}
    for scale in SMALL_SCALES:
        assert not {"vol__ewma_3", "range__hi_4", "range__lo_4", "range__hi_8", "range__lo_8", "flow__taker_3",
                    "flow__taker_6", "xasset__pair_log", "xasset__ewma_3"} & set(groups.columns_of_scale(scale))


@pytest.fixture(scope="module")
def wgroups():
    return FeatureGroups.from_probe(make_arms(), WINDOW_MF)


def test_a_scale_owns_the_window_columns_named_for_its_minutes(wgroups):
    # the review finding: minus_scale_N must remove every N-minute column, not only the `scales` knob's
    assert {"vol__ewma_5", "xasset__ewma_5", "range__hi_5", "range__lo_5", "flow__taker_5"} <= set(wgroups.columns_of_scale(5))
    assert {"vol__ewma_10", "xasset__ewma_10", "range__hi_10", "range__lo_10"} <= set(wgroups.columns_of_scale(10))
    assert "flow__taker_2" in wgroups.columns_of_scale(2) and "vol__ewma_5" not in wgroups.columns_of_scale(2)
    assert not {"flow__log_volume", "flow__log_trades", "xasset__pair_log"} & set(wgroups.columns_of_scale(5))


def test_selecting_without_a_scale_drops_its_window_columns_too(wgroups):
    chosen = set(wgroups.select_columns(SMALL_FAMILIES, [2, 10]))
    assert not chosen & {"vol__ewma_5", "xasset__ewma_5", "range__hi_5", "range__lo_5", "flow__taker_5", "ret__r5"}
    assert {"vol__ewma_10", "range__hi_10", "flow__taker_2", "ret__r2"} <= chosen


def test_an_arm_sets_the_scales_and_the_windows_it_keeps(wgroups):
    full = {"families": SMALL_FAMILIES, "scales": SMALL_SCALES, **GRID_WINDOWS, "taker_check_windows": [2, 5]}
    assert wgroups.arm_params("all") == full
    assert wgroups.arm_params("minus_flow") == {**full, "families": [f for f in SMALL_FAMILIES if f != "flow"]}
    assert wgroups.arm_params("minus_scale_5") == {
        "families": SMALL_FAMILIES, "scales": [2, 10], "ewma_half_lives": [10], "range_windows": [10],
        "taker_windows": [2], "taker_check_windows": [2, 5]}
    assert wgroups.arm_params("minus_scale_2")["taker_windows"] == [5]
    assert wgroups.arm_params("minus_scale_2")["ewma_half_lives"] == [5, 10]


def test_a_window_that_is_not_in_the_grid_stays_in_every_arm(groups):
    # SMALL's ewma 3, range 4 and 8, taker 3 and 6 are not minutes of the grid [2, 5, 10]
    params_ = groups.arm_params("minus_scale_5")
    assert params_["ewma_half_lives"] == [3] and params_["range_windows"] == [4, 8] and params_["taker_windows"] == [3, 6]


@pytest.mark.parametrize("arm_scale", SMALL_SCALES)
def test_a_scale_arm_removes_exactly_its_columns_in_the_real_node_windows_included(wgroups, arm_scale):
    arm = f"minus_scale_{arm_scale}"
    params_ = wgroups.arm_params(arm)
    removed = real_columns(SMALL_FAMILIES, SMALL_SCALES, **GRID_WINDOWS) - real_columns(**params_)
    assert removed == set(wgroups.columns_of_scale(arm_scale))
    assert set(real_columns(**params_)) == set(wgroups.select_columns(SMALL_FAMILIES, params_["scales"]))


def test_every_arm_keeps_the_same_rows_in_the_real_node_even_with_a_bad_taker_bar(wgroups):
    klines = tmf.world(80)
    next(r for r in klines if r["asset"] == "AAA" and r["open_time_ms"] == tmf.START + 40 * tmf.BAR)["taker_buy_volume"] = -4.0
    rows = {}
    for arm in ("all", "minus_scale_2", "minus_scale_5", "minus_scale_10", "minus_flow"):
        out = tmf.run(klines, bvol_rows=tmf.bvol("AAA", 80, 1) + tmf.bvol("BBB", 80, 2), history_minutes=31,
                      **wgroups.arm_params(arm))
        rows[arm] = {(r["asset"], r["t_ms"]) for r in out["records"]}
    assert all(rows[arm] == rows["all"] for arm in rows), {arm: len(r) for arm, r in rows.items()}


def test_a_scale_arm_that_would_empty_a_window_list_refuses_naming_the_arm():
    with pytest.raises(ValueError, match="minus_scale_5"):
        FeatureGroups.from_probe(make_arms(), {**SMALL_MF, "taker_windows": [5], "ewma_half_lives": [10],
                                               "range_windows": [10]})


def test_the_probe_refuses_the_taker_check_in_the_feature_params():
    with pytest.raises(ValueError, match="taker_check_windows"):
        FeatureGroups.from_probe(make_arms(), {**SMALL_MF, "taker_check_windows": [5]})


@pytest.mark.parametrize("scale", SMALL_SCALES)
def test_dropping_a_scale_removes_exactly_its_columns_in_the_real_node(groups, scale):
    kept = [s for s in SMALL_SCALES if s != scale]
    removed = real_columns(SMALL_FAMILIES, SMALL_SCALES) - real_columns(SMALL_FAMILIES, kept)
    assert removed == set(groups.columns_of_scale(scale))


@pytest.mark.parametrize("family", SMALL_FAMILIES)
def test_dropping_a_family_removes_exactly_its_columns_in_the_real_node(groups, family):
    kept = [f for f in SMALL_FAMILIES if f != family]
    removed = real_columns(SMALL_FAMILIES, SMALL_SCALES) - real_columns(kept, SMALL_SCALES)
    assert removed == set(groups.columns_of_family(family))


def test_selecting_columns_combines_the_kept_families_and_scales(groups):
    chosen = groups.select_columns(families=["ret", "clock"], scales=[2, 10])
    assert set(chosen) == {"ret__r2", "ret__r10"} | {c for c in groups.columns if c.startswith("clock__")}
    assert list(chosen) == [c for c in groups.columns if c in set(chosen)], "the node's column order is kept"


def test_each_arm_names_the_one_group_it_drops(groups):
    arms = make_arms()
    assert groups.dropped_by("minus_ret") == ("family", "ret")
    assert groups.dropped_by("minus_scale_5") == ("scale", 5)
    assert {groups.dropped_by(a) for a in arms["arms"] if a != "all"} == {("family", f) for f in SMALL_FAMILIES}
    assert {groups.dropped_by(a) for a in arms["scale_arms"]} == {("scale", s) for s in SMALL_SCALES}


def test_an_arm_that_drops_two_groups_or_none_refuses():
    bad = make_arms()
    bad["arms"]["minus_ret"] = bad["arms"]["minus_trend"][:-1]
    with pytest.raises(ValueError, match="minus_ret"):
        FeatureGroups.from_probe(bad, SMALL_MF)
    same = make_arms()
    same["scale_arms"]["minus_scale_5"] = list(SMALL_SCALES)
    with pytest.raises(ValueError, match="minus_scale_5"):
        FeatureGroups.from_probe(same, SMALL_MF)


def test_the_probe_refuses_families_or_scales_in_the_feature_params():
    for key, value in (("families", ["ret"]), ("scales", [2, 5])):
        with pytest.raises(ValueError, match=key):
            FeatureGroups.from_probe(make_arms(), {**SMALL_MF, key: value})


@pytest.mark.skipif(find_spec("exchange_calendars") is None, reason="the shipped arms name the holiday family, which needs exchange_calendars")
def test_the_shipped_arms_and_default_grid_pin_to_the_real_node():
    arms = _config("features-arms.json")
    assert tuple(arms["arms"]["all"]) == FAMILIES and set(arms["scale_arms"]) == {f"minus_scale_{s}" for s in GRID}
    shipped = FeatureGroups.from_probe(arms, {"assets": ["BTC", "ETH"]})
    assert {c.split("__", 1)[0] for c in shipped.columns} == set(FAMILIES)
    assert set(shipped.columns_of_scale(10080)) == {
        "ret__r10080", "trend__ma_10080", "vol__rms_10080", "vol__hl_10080", "vol__jump_10080",
        "xasset__r10080", "xasset__rms_10080", "xasset__hl_10080", "xasset__jump_10080", "xasset__pair_chg_10080"}
    assert shipped.scales == GRID and shipped.families == FAMILIES


@pytest.mark.skipif(find_spec("exchange_calendars") is None, reason="the shipped arms name the holiday family, which needs exchange_calendars")
def test_the_shipped_minus_scale_60_drops_every_sixty_minute_column():
    # the review finding, on the real grid: the ewma half-life, the range window and the taker window of 60 minutes
    shipped = FeatureGroups.from_probe(_config("features-arms.json"), {"assets": ["BTC", "ETH"]})
    sixty = {"vol__ewma_60", "xasset__ewma_60", "range__hi_60", "range__lo_60", "flow__taker_60"}
    assert sixty <= set(shipped.columns_of_scale(60))
    chosen = set(shipped.select_columns(FAMILIES, [s for s in GRID if s != 60]))
    assert not chosen & sixty and not chosen & set(shipped.columns_of_scale(60))
    assert chosen >= {"vol__ewma_15", "range__hi_240", "flow__taker_15", "ret__r15"}
    assert shipped.arm_params("minus_scale_60") == {
        "families": list(FAMILIES), "scales": [5, 15, 240, 1440, 10080], "ewma_half_lives": [15],
        "range_windows": [240, 1440], "taker_windows": [15], "taker_check_windows": [15, 60]}


# ---------------------------------------------------------------------------
# reading a walk-forward summary
# ---------------------------------------------------------------------------


def write_summary(root, arm, scores, *, cutoffs_=None, state="ran", objective=OBJECTIVE, select="min",
                  stamp="2026-05-01-1a2b3c4d", fold_states=None, name="select-sequence"):
    cut = cutoffs_ or cutoffs(len(scores))
    folds = [{"cutoff": c, "run_dir": f"/runs/{c}", "state": (fold_states or {}).get(i, "ran"), "score": s}
             for i, (c, s) in enumerate(zip(cut, scores))]
    directory = os.path.join(root, arm, f"{name}-{arm}-walkforward-{stamp}")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "walkforward.json"), "w", encoding="utf-8") as handle:
        json.dump({"name": f"{name}-{arm}", "asof": "2026-05-01", "document_hash": "h", "objective": objective,
                   "select": select, "state": state, "folds": folds, "aggregate": {}, "evidence": {}}, handle)
    return directory


def test_a_summary_is_read_off_the_driver_layout(tmp_path):
    write_summary(tmp_path, "all", [1.0, 2.0, 3.0])
    scores = FoldScores.read(str(tmp_path / "all"))
    assert scores.cutoffs == cutoffs(3) and scores.scores == (1.0, 2.0, 3.0)
    assert scores.objective == OBJECTIVE and scores.select == "min"
    assert len(scores.sha256) == 64 and scores.path.endswith("walkforward.json")


@pytest.mark.parametrize("kwargs, text", [
    ({"state": "halted"}, "state"),
    ({"select": "max"}, "select"),
    ({"fold_states": {1: "error"}}, "fold"),
])
def test_an_incomplete_or_wrong_direction_summary_refuses(tmp_path, kwargs, text):
    write_summary(tmp_path, "all", [1.0, 2.0, 3.0], **kwargs)
    with pytest.raises(ValueError, match=text):
        FoldScores.read(str(tmp_path / "all"))


def test_a_missing_or_null_or_nonfinite_score_refuses(tmp_path):
    write_summary(tmp_path, "a", [1.0, None, 3.0])
    with pytest.raises(ValueError, match="score"):
        FoldScores.read(str(tmp_path / "a"))
    write_summary(tmp_path, "b", [1.0, True, 3.0])
    with pytest.raises(ValueError, match="score"):
        FoldScores.read(str(tmp_path / "b"))


def test_no_summary_or_two_summaries_refuse(tmp_path):
    os.makedirs(tmp_path / "all")
    with pytest.raises(ValueError, match="no walk-forward summary"):
        FoldScores.read(str(tmp_path / "all"))
    write_summary(tmp_path, "all", [1.0, 2.0], stamp="2026-05-01-aaaaaaaa")
    write_summary(tmp_path, "all", [1.0, 2.0], stamp="2026-05-02-bbbbbbbb")
    with pytest.raises(ValueError, match="exactly one"):
        FoldScores.read(str(tmp_path / "all"))


def test_the_reader_matches_the_real_driver_output(tmp_path):
    # one real walk-forward through dskit's driver with a scripted score node: the layout and fields the reader
    # relies on are the driver's own, not this file's restatement of them
    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.driver import run_walk_forward
    from dskit.pipeline.node import Node, NodeKindRegistry

    class Metric(Node):
        role = "transform"
        outputs = ("metrics",)

        def run(self, ctx, inputs):
            return {"metrics": {"weighted_crps": 1.0 + 0.5 * ctx.fold_index}}

    registry = NodeKindRegistry()
    registry.register("metric", Metric)
    document = PipelineDocument.from_obj({
        "name": "probe", "pipeline": {"score": {"uses": "metric"}},
        "walkforward": {"objective": OBJECTIVE, "select": "min", "first": "2025-01-01", "step_days": 30, "count": 3,
                        "val_days": 30, "embargo_days": 1},
        "outputs": {"run_root": str(tmp_path / "all")}})
    run_walk_forward(document, asof="2026-05-01", registry=registry)
    scores = FoldScores.read(str(tmp_path / "all"))
    assert scores.cutoffs == ("2025-01-01", "2025-01-31", "2025-03-02")
    assert scores.scores == (1.0, 1.5, 2.0) and scores.objective == OBJECTIVE and scores.select == "min"


# ---------------------------------------------------------------------------
# the node, on synthetic summaries
# ---------------------------------------------------------------------------

K = 10
BASE = [1.0 + 0.01 * i for i in range(K)]
FILES = {"sequence": "selected-features-sequence.json", "tabular": "selected-features-tabular.json"}
DISAGREEMENTS = "feature-selection-disagreements.json"
SEQUENCE_EFFECTS = {"minus_ret": HURTS, "minus_flow": NOISY, "minus_scale_5": HURTS}
TABULAR_EFFECTS = {"minus_ret": HURTS, "minus_flow": HURTS, "minus_scale_5": NOISY, "minus_scale_10": HELPS}


def build(tmp_path, effects_by_class, arms=None, k=K):
    """Write one summary per arm and class, base scores plus the arm's per-fold shift (zero when unlisted)."""
    arms = arms or make_arms()
    names = list(arms["arms"]) + list(arms["scale_arms"])
    for cls, effects in effects_by_class.items():
        for arm in names:
            d = effects.get(arm, [0.0] * k)
            write_summary(str(tmp_path / cls / "runs"), arm, [b + x for b, x in zip(BASE[:k], d)],
                          cutoffs_=cutoffs(k), name=f"select-{cls}")
    path = tmp_path / "features-arms.json"
    path.write_text(json.dumps(arms))
    return str(path)


def params(tmp_path, arms_path, classes=CLASSES, **extra):
    return {"arms_file": arms_path, "classes": {c: {"runs": str(tmp_path / c / "runs" / "{arm}")} for c in classes},
            "minute_features": SMALL_MF, **extra}


def run_node(tmp_path, **kw):
    node = AblationSelect("select", kw.pop("node_params"))
    ctx = SimpleNamespace(run_dir=str(tmp_path / "run"), asof="2026-05-01")
    return node, node.run(ctx, {}), ctx


@pytest.fixture
def world_two_classes(tmp_path):
    arms_path = build(tmp_path, {"sequence": SEQUENCE_EFFECTS, "tabular": TABULAR_EFFECTS})
    return tmp_path, params(tmp_path, arms_path)


def test_each_class_keeps_what_dropping_hurts_beyond_noise(world_two_classes):
    tmp_path, p = world_two_classes
    _, out, _ = run_node(tmp_path, node_params=p)
    sequence, tabular = out["selected"]["sequence"], out["selected"]["tabular"]
    assert sequence["families"] == {"kept": ["ret"], "dropped": [f for f in SMALL_FAMILIES if f != "ret"]}
    assert sequence["scales"] == {"kept": [5], "dropped": [2, 10]}
    assert tabular["families"]["kept"] == ["ret", "flow"]
    assert sequence["selected_arm"] == {"families": ["ret"], "scales": [5], **SMALL_WINDOWS}


def test_a_class_with_no_kept_scale_keeps_the_whole_scale_axis_and_says_so(world_two_classes):
    tmp_path, p = world_two_classes
    _, out, _ = run_node(tmp_path, node_params=p)
    tabular = out["selected"]["tabular"]
    assert tabular["scales"]["kept"] == [2, 5, 10] and "no_scale_kept" in tabular["flags"]
    assert "no_family_kept" not in tabular["flags"]


def test_selected_columns_follow_the_kept_groups(world_two_classes):
    tmp_path, p = world_two_classes
    node, out, _ = run_node(tmp_path, node_params=p)
    groups = FeatureGroups.from_probe(make_arms(), SMALL_MF)
    assert out["selected"]["sequence"]["columns"] == list(groups.select_columns(["ret"], [5]))
    assert out["selected"]["sequence"]["columns"] == ["ret__r5"]
    assert out["selected"]["tabular"]["columns"] == list(groups.select_columns(["ret", "flow"], [2, 5, 10]))


def test_every_group_leaves_its_evidence_with_the_pair_that_decided_it(world_two_classes):
    tmp_path, p = world_two_classes
    _, out, _ = run_node(tmp_path, node_params=p)
    evidence = out["selected"]["sequence"]["evidence"]
    assert set(evidence) == {f"family:{f}" for f in SMALL_FAMILIES} | {f"scale:{s}" for s in SMALL_SCALES}
    assert evidence["family:ret"]["keep"] is True and evidence["family:ret"]["arm"] == "minus_ret"
    assert evidence["family:ret"]["mean"] == pytest.approx(statistics.mean(HURTS))
    assert evidence["family:ret"]["se"] == pytest.approx(statistics.stdev(HURTS) / math.sqrt(K))
    assert evidence["family:flow"]["keep"] is False
    assert evidence["family:clock"]["mean"] == 0.0 and evidence["family:clock"]["keep"] is False


def test_groups_kept_by_only_one_class_are_listed_with_both_pairs_and_never_reconciled(world_two_classes):
    tmp_path, p = world_two_classes
    _, out, _ = run_node(tmp_path, node_params=p)
    listed = {row["group"]: row for row in out["disagreements"]}
    assert set(listed) == {"family:flow", "scale:5"}  # ret is kept by both; scale 10 and 2 by neither
    flow = listed["family:flow"]
    assert flow["kept"] == {"sequence": False, "tabular": True}
    pairs = flow["by_class"]
    assert pairs["sequence"]["mean"] == pytest.approx(statistics.mean(NOISY))
    assert pairs["tabular"]["mean"] == pytest.approx(statistics.mean(HURTS))
    assert pairs["sequence"]["se"] == pytest.approx(statistics.stdev(NOISY) / math.sqrt(K))
    assert pairs["tabular"]["se"] == pytest.approx(statistics.stdev(HURTS) / math.sqrt(K))
    assert listed["scale:5"]["kept"] == {"sequence": True, "tabular": False}
    assert "family:flow" not in out["selected"]["sequence"]["families"]["kept"]  # reported, not merged


def test_files_are_written_to_the_run_with_the_pinned_names(world_two_classes):
    tmp_path, p = world_two_classes
    node, out, ctx = run_node(tmp_path, node_params=p)
    folder = tmp_path / "run" / "artifacts" / "select"
    assert sorted(os.listdir(folder)) == sorted([*FILES.values(), DISAGREEMENTS])
    for cls, name in FILES.items():
        assert json.loads((folder / name).read_text()) == json.loads(json.dumps(out["selected"][cls]))
    assert json.loads((folder / DISAGREEMENTS).read_text()) == json.loads(json.dumps(out["disagreements"]))


def test_sources_are_recorded_by_hash(world_two_classes):
    tmp_path, p = world_two_classes
    _, out, _ = run_node(tmp_path, node_params=p)
    sources = out["selected"]["sequence"]["sources"]
    assert set(sources) == set(make_arms()["arms"]) | set(make_arms()["scale_arms"]) | {"arms_file"}
    assert all(len(v["sha256"]) == 64 for v in sources.values())
    assert out["selected"]["sequence"]["rule"] == {"z": 1.0, "min_folds": 8, "reference_arm": "all",
                                                   "objective": OBJECTIVE}
    assert out["selected"]["sequence"]["folds"] == {"n": K, "cutoffs": list(cutoffs(K))}


def test_without_a_selected_arm_the_check_is_pending(world_two_classes):
    tmp_path, p = world_two_classes
    _, out, _ = run_node(tmp_path, node_params=p)
    assert out["selected"]["sequence"]["check"] == {"status": "pending", "arm": "selected"}
    assert out["selected"]["sequence"]["fell_back_to_all"] is False


def test_a_selected_arm_worse_than_all_falls_back_to_all_and_is_flagged(world_two_classes):
    tmp_path, p = world_two_classes
    write_summary(str(tmp_path / "sequence" / "runs"), "selected", [b + x for b, x in zip(BASE, HURTS)],
                  cutoffs_=cutoffs(K), name="select-sequence")
    write_summary(str(tmp_path / "tabular" / "runs"), "selected", [b + x for b, x in zip(BASE, HELPS)],
                  cutoffs_=cutoffs(K), name="select-tabular")
    _, out, _ = run_node(tmp_path, node_params=p)
    sequence, tabular = out["selected"]["sequence"], out["selected"]["tabular"]
    assert sequence["fell_back_to_all"] is True and "selected_arm_worse_than_all" in sequence["flags"]
    assert sequence["check"]["status"] == "fell_back" and sequence["check"]["mean"] > 0
    assert sequence["families"]["kept"] == SMALL_FAMILIES and sequence["scales"]["kept"] == SMALL_SCALES
    assert sequence["selected_arm"] == {"families": SMALL_FAMILIES, "scales": SMALL_SCALES, **SMALL_WINDOWS}
    assert len(sequence["columns"]) == len(FeatureGroups.from_probe(make_arms(), SMALL_MF).columns)
    assert sequence["selection_before_fallback"]["families"] == ["ret"]
    assert tabular["fell_back_to_all"] is False and tabular["check"]["status"] == "passed"
    assert tabular["families"]["kept"] == ["ret", "flow"]


def test_nothing_kept_on_the_family_axis_falls_back_to_every_family_and_says_so(tmp_path):
    arms_path = build(tmp_path, {"sequence": {}})
    _, out, _ = run_node(tmp_path, node_params=params(tmp_path, arms_path, classes=("sequence",)))
    sequence = out["selected"]["sequence"]
    assert sequence["families"]["kept"] == SMALL_FAMILIES and "no_family_kept" in sequence["flags"]
    assert out["disagreements"] == []


def test_arms_on_different_folds_across_classes_refuse(tmp_path):
    arms_path = build(tmp_path, {"sequence": SEQUENCE_EFFECTS})
    other = build(tmp_path, {"tabular": TABULAR_EFFECTS}, k=9)
    assert other == arms_path
    with pytest.raises(ValueError, match="folds|cutoffs"):
        run_node(tmp_path, node_params=params(tmp_path, arms_path, min_folds=2))


def test_fewer_folds_than_the_floor_refuse_through_the_node(tmp_path):
    arms_path = build(tmp_path, {"sequence": SEQUENCE_EFFECTS}, k=7)
    with pytest.raises(ValueError, match="at least 8 folds"):
        run_node(tmp_path, node_params=params(tmp_path, arms_path, classes=("sequence",)))


def test_a_missing_arm_summary_refuses_naming_the_arm(tmp_path):
    arms_path = build(tmp_path, {"sequence": SEQUENCE_EFFECTS})
    import shutil
    shutil.rmtree(tmp_path / "sequence" / "runs" / "minus_vol")
    with pytest.raises(ValueError, match="minus_vol"):
        run_node(tmp_path, node_params=params(tmp_path, arms_path, classes=("sequence",)))


def test_the_arms_file_must_name_the_reference_arm(tmp_path):
    arms = make_arms()
    del arms["arms"]["all"]
    build(tmp_path, {"sequence": SEQUENCE_EFFECTS}, arms=make_arms())
    (tmp_path / "bad.json").write_text(json.dumps(arms))
    with pytest.raises(ValueError, match="all"):
        run_node(tmp_path, node_params=params(tmp_path, str(tmp_path / "bad.json"), classes=("sequence",)))


@pytest.mark.parametrize("bad", [
    {"surprise": 1}, {"z": -1}, {"min_folds": 1}, {"classes": {}}, {"classes": {"a": {"runs": ""}}},
    {"classes": {"a": {"runs": "x"}}}, {"classes": {"a": {"runs": "x/{arm}/{other}"}}}, {"classes": {"a": {"runs": "x/{"}}},
    {"classes": {"a": {"runs": "x/{arm}", "extra": 1}}}, {"classes": {"Bad Name": {"runs": "x/{arm}"}}},
    {"arms_file": ""}, {"minute_features": {"families": ["ret"]}},
    {"minute_features": {"assets": ["A", "B"], "taker_check_windows": [5]}},
])
def test_params_are_default_deny(tmp_path, bad):
    good = {"arms_file": "a.json", "classes": {"sequence": {"runs": "r/{arm}"}}, "minute_features": {"assets": ["A", "B"]}}
    assert AblationSelect.validate_params(good) == []
    assert AblationSelect.validate_params({**good, **bad}) != []


def test_the_node_is_a_report_node_that_serves_nothing():
    assert AblationSelect.role == "report"
    assert AblationSelect.outputs == ("selected", "disagreements")
    assert AblationSelect.serving_effect({}, {}) == "forbidden"


def test_the_rule_defaults_are_the_approved_ones():
    from crypto_trading.feature_selection import DEFAULT_MIN_FOLDS, DEFAULT_Z

    assert (DEFAULT_Z, DEFAULT_MIN_FOLDS) == (1.0, 8)  # restated on purpose: the owner-approved rule


# ---------------------------------------------------------------------------
# config pins: arms, folds, seeds, loss, W1, the select document
# ---------------------------------------------------------------------------

# the node's default windows, restated on purpose (each is a minute of the scale grid)
SHIPPED_WINDOWS = {"ewma_half_lives": [15, 60], "range_windows": [60, 240, 1440], "taker_windows": [15, 60]}
MANIFESTS = {"sequence": "workflow-select-sequence.json", "tabular": "workflow-select-tabular.json"}
TEMPLATES = {"sequence": "select-template-sequence.json", "tabular": "select-template-tabular.json"}


def manifests():
    return {c: _config(n) for c, n in MANIFESTS.items()}


def test_the_lanes_are_the_seventeen_arms_of_features_arms_json():
    features = _config("features-arms.json")
    expected = list(features["arms"]) + list(features["scale_arms"])
    assert len(expected) == 17 and expected[0] == "all"
    for cls, manifest in manifests().items():
        args = manifest["args"]
        assert manifest["lanes"]["key"] == "arms" and manifest["lanes"]["keyed"] == ["arm"], cls
        assert args["arms"] == expected, cls
        assert set(args["arm"]) == set(expected), cls
        for arm in features["arms"]:
            assert args["arm"][arm] == {"families": features["arms"][arm], "scales": list(GRID), **SHIPPED_WINDOWS,
                                        "taker_check_windows": SHIPPED_WINDOWS["taker_windows"]}, (cls, arm)
        for arm, kept in features["scale_arms"].items():
            (dropped,) = set(GRID) - set(kept)
            assert args["arm"][arm] == {
                "families": features["arms"]["all"], "scales": kept,
                "ewma_half_lives": [w for w in SHIPPED_WINDOWS["ewma_half_lives"] if w != dropped],
                "range_windows": [w for w in SHIPPED_WINDOWS["range_windows"] if w != dropped],
                "taker_windows": [w for w in SHIPPED_WINDOWS["taker_windows"] if w != dropped],
                "taker_check_windows": SHIPPED_WINDOWS["taker_windows"]}, (cls, arm)


def test_the_arm_lists_cannot_drift_from_the_nodes_declared_families_and_scales():
    from crypto_trading.minute_features import DEFAULT_SCALES, FAMILIES as NODE_FAMILIES

    for cls, manifest in manifests().items():
        arm = manifest["args"]["arm"]
        assert tuple(arm["all"]["families"]) == NODE_FAMILIES == FAMILIES, cls
        assert tuple(arm["all"]["scales"]) == DEFAULT_SCALES == GRID, cls
        assert {a for a in arm if a.startswith("minus_") and not a.startswith("minus_scale_")} == \
            {f"minus_{f}" for f in NODE_FAMILIES}, cls
        assert {a for a in arm if a.startswith("minus_scale_")} == {f"minus_scale_{s}" for s in DEFAULT_SCALES}, cls


def test_the_two_manifests_differ_only_in_their_template_and_work_dir():
    seq, tab = manifests()["sequence"], manifests()["tabular"]
    assert seq["args"]["work_dir"] != tab["args"]["work_dir"]
    assert seq["registry"]["fit"]["template"] == TEMPLATES["sequence"]
    assert tab["registry"]["fit"]["template"] == TEMPLATES["tabular"]

    def stripped(manifest):
        manifest = copy.deepcopy(manifest)
        manifest["args"].pop("work_dir")
        manifest["registry"]["fit"].pop("template")
        manifest.pop("notes")
        manifest["registry"]["fit"].pop("notes", None)
        return manifest

    assert stripped(seq) == stripped(tab)


def test_w1_ends_on_the_first_of_may_exclusive_and_starts_on_june_20():
    for cls, manifest in manifests().items():
        window = manifest["args"]["window"]
        assert window["start"] == "2023-06-20" and window["end"] == W1_END, cls
        assert window["start_ms"] == W1_START_MS and window["end_ms"] == W1_END_MS, cls
        assert window["end_ms"] - window["start_ms"] == 1046 * DAY_MS, cls


def test_the_folds_are_expanding_embargoed_one_day_and_end_inside_w1():
    import datetime

    for cls, manifest in manifests().items():
        folds = manifest["args"]["folds"]
        assert folds["embargo_days"] == 1, cls
        assert folds["count"] >= 8, "the rule refuses fewer folds than its floor"
        first = datetime.date.fromisoformat(folds["first"])
        last_validation_end = first + datetime.timedelta(days=(folds["count"] - 1) * folds["step_days"]
                                                         + folds["val_days"])
        assert last_validation_end <= datetime.date.fromisoformat(W1_END), cls
        assert first > datetime.date.fromisoformat("2023-06-20") + datetime.timedelta(days=folds["embargo_days"]), cls


def test_the_loss_is_weighted_crps_and_the_seeds_are_fixed_in_both_manifests():
    for cls, manifest in manifests().items():
        training = manifest["args"]["training"]
        assert training["losses"] == [{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": 1.0}], cls
        assert training["seeds"] == sorted(set(training["seeds"])) and len(training["seeds"]) >= 1, cls
        assert manifest["args"]["grid"]["horizons"] == list(range(1, 16)), cls


def test_the_horizons_agree_with_the_feature_node_labels():
    from crypto_trading.minute_features import MinuteFeatures

    horizons = manifests()["sequence"]["args"]["grid"]["horizons"]
    assert horizons == list(range(1, MinuteFeatures.DEFAULTS["max_horizon"] + 1))


def test_the_templates_share_every_node_that_is_not_the_model():
    docs = {c: _config(n) for c, n in TEMPLATES.items()}
    seq, tab = docs["sequence"]["pipeline"], docs["tabular"]["pipeline"]
    for node in ("bars", "features", "train_rows", "val_rows", "score"):
        assert seq[node] == tab[node], node
    assert seq["w1"]["params"] == tab["w1"]["params"] and seq["w1"]["uses"] == tab["w1"]["uses"]
    assert tab["w1"]["inputs"] == {"records": "$features.records"}
    assert seq["w1"]["inputs"] == {"records": "$windows.records"}, "the raw channels join before any row is cut"
    assert docs["sequence"]["walkforward"] == docs["tabular"]["walkforward"]
    assert docs["sequence"]["walkforward"]["objective"] == OBJECTIVE
    assert docs["sequence"]["walkforward"]["select"] == "min"
    assert set(docs["sequence"]["pipeline"]) - set(docs["tabular"]["pipeline"]) == {"windows"}
    assert set(docs["tabular"]["pipeline"]) - set(docs["sequence"]["pipeline"]) == set()


def test_the_templates_take_the_arm_and_the_w1_bounds_from_placeholders():
    for cls, name in TEMPLATES.items():
        doc = _config(name)
        features = doc["pipeline"]["features"]
        assert features["uses"] == "crypto_trading.minute_features:MinuteFeatures"
        assert features["params"]["families"] == "${arm.families}" and features["params"]["scales"] == "${arm.scales}"
        for knob in (*SHIPPED_WINDOWS, "taker_check_windows"):
            assert features["params"][knob] == "${arm." + knob + "}", (cls, knob)
        assert "history_minutes" not in features["params"], "left unset so every arm keeps the same rows"
        assert doc["pipeline"]["w1"]["params"]["where"] == [
            {"field": "t_ms", "op": ">=", "value": "${window.start_ms}"},
            {"field": "t_ms", "op": "<", "value": "${window.end_ms}"}], "W1 is start-inclusive, end-EXCLUSIVE"
        walk = doc["walkforward"]
        for key, ref in (("first", "folds.first"), ("step_days", "folds.step_days"), ("count", "folds.count"),
                         ("val_days", "folds.val_days"), ("embargo_days", "folds.embargo_days")):
            assert walk[key] == "${" + ref + "}", (cls, key)


def test_the_fit_nodes_use_the_declared_model_classes():
    seq, tab = (_config(TEMPLATES[c])["pipeline"]["fit"] for c in CLASSES)
    seq_est, tab_est = seq["params"]["estimator"], tab["params"]["estimator"]
    assert seq_est["class"] == tab_est["class"] == "dskit.pipeline.libs.predictive_cdf:TorchCDF"
    assert tab_est["params"]["encoder"] == {"kind": "mlp"} and tab_est["params"]["components"] == 3
    adapter = seq_est["params"]["encoder"]["adapter"]
    assert seq_est["params"]["encoder"]["kind"] == "external"
    assert adapter["class"] == "dskit.pipeline.libs.transformers:HFSequenceEncoder"
    assert adapter["params"]["model"] == "PatchTST"
    assert seq_est["params"]["components"] == tab_est["params"]["components"] == 3
    for fit in (seq, tab):
        assert fit["params"]["losses"] == "${training.losses}" and fit["params"]["seeds"] == "${training.seeds}"


def test_every_config_object_explains_itself():
    docs = {**{n: _config(n) for n in TEMPLATES.values()}, **{n: _config(n) for n in MANIFESTS.values()},
            "select-features.json": _config("select-features.json")}
    for name, doc in docs.items():
        assert doc.get("notes"), name
        for key, node in doc.get("pipeline", {}).items():
            assert node.get("notes"), (name, key)
        for section in ("walkforward", "outputs", "lanes"):
            if section in doc:
                assert doc[section].get("notes"), (name, section)
        for key, value in doc.get("args", {}).items():
            if isinstance(value, dict) and key != "arm":
                assert value.get("notes"), (name, "args", key)
        for key, entry in doc.get("registry", {}).items():
            assert entry.get("notes"), (name, "registry", key)
        for key, step in doc.get("steps", {}).items():
            assert step.get("notes"), (name, "steps", key)


def test_the_manifests_plan_with_seventeen_lanes_and_name_what_the_zoo_build_must_fill():
    from dskit.pipeline.workflow import run_workflow

    for cls, name in MANIFESTS.items():
        lines = []
        code = run_workflow(os.path.join(CONFIGS, name), plan=True, out=lines.append)
        assert code == 0, (cls, lines)
        assert sum(1 for line in lines if line.startswith("lane ")) == 17, cls
        unfilled = {line.split()[-1].split(".")[1] for line in lines if line.startswith("unfilled arg")}
        assert unfilled <= {"zoo", "grid", "window"}, (cls, unfilled)
        assert any(line.startswith("unfilled arg") for line in lines), "the zoo kinds are not built yet: say so"


def test_each_lane_expands_its_arm_into_the_feature_node():
    from dskit.pipeline.workflow import lane_flows

    features = _config("features-arms.json")
    for cls, name in MANIFESTS.items():
        manifest = _config(name)
        flows = lane_flows(manifest, CONFIGS)
        assert [flow.lane for flow in flows] == manifest["args"]["arms"]
        for flow in flows:
            doc = flow.expanded("fit", strict=False, rnd=0)
            params_ = doc["pipeline"]["features"]["params"]
            arm = flow.lane
            assert params_["families"] == (features["arms"].get(arm) or features["arms"]["all"]), (cls, arm)
            assert params_["scales"] == (features["scale_arms"].get(arm) or list(GRID)), (cls, arm)
            assert {k: params_[k] for k in (*SHIPPED_WINDOWS, "taker_check_windows")} == {
                k: v for k, v in manifest["args"]["arm"][arm].items() if k not in ("families", "scales")}, (cls, arm)
            assert doc["walkforward"]["count"] == manifest["args"]["folds"]["count"]
            assert doc["walkforward"]["embargo_days"] == 1
            lanes_runs = {flow.path("fit", "runs") for flow in flows}
            assert len(lanes_runs) == 17


def test_the_select_document_reads_where_the_manifests_write():
    doc = _config("select-features.json")
    node = doc["pipeline"]["select"]
    assert node["uses"] == "crypto_trading.feature_selection:AblationSelect"
    assert AblationSelect.validate_params(node["params"]) == []
    from dskit.pipeline.workflow_batch import DEFAULT_WORK_DIR_PATTERN

    for cls, manifest in manifests().items():
        # the batch gives lane L the work dir `{root}/{L}` (root = the manifest's work_dir), and the layout puts
        # the arm's runs at `{W}/runs/{A}`: the selection reads that, with {arm} standing for both L and A
        lane_work_dir = DEFAULT_WORK_DIR_PATTERN.format(root=manifest["args"]["work_dir"], lane="{arm}")
        runs = manifest["layout"]["fit"]["runs"].format(W=lane_work_dir, A="{arm}")
        assert node["params"]["classes"][cls]["runs"] == runs, cls
    assert set(node["params"]["classes"]) == set(CLASSES)
    assert node["params"]["minute_features"]["assets"] == manifests()["sequence"]["args"]["window"]["assets"]
    assert node["params"]["arms_file"] == "configs/features-arms.json"
    assert os.path.isfile(os.path.join(CHILD_ROOT, node["params"]["arms_file"]))
    assert node["params"].get("z", 1.0) == 1.0 and node["params"].get("min_folds", 8) == 8


def test_the_select_document_loads_as_a_pipeline_document():
    from dskit.pipeline.document import load_document

    document = load_document(os.path.join(CONFIGS, "select-features.json"))
    assert document.hash and set(document.pipeline) == {"select"}


def test_the_selected_files_are_run_outputs_not_tracked_files():
    found = []
    for base, dirs, files in os.walk(CHILD_ROOT):
        dirs[:] = [d for d in dirs if d not in ("pipeline_runs", "__pycache__")]
        found += [os.path.join(base, f) for f in files if f.startswith("selected-features")]
    assert found == [], "selected-features-*.json are written by the run (pipeline_runs is gitignored), never committed"

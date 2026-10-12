"""SklearnExpandingRefit (ADR-0264): block-wise, known-time-guarded refit.

Imports without scikit-learn: only the tests that RUN the node call
``pytest.importorskip("sklearn")``; the validation half plans without it.
Every expectation about predictions is restated here by an independent
per-block refit (plain loops over the rows, the library called directly),
never read back from the node.
"""

from __future__ import annotations

import json
import math
import os
import random

import pytest

from dskit.pipeline.libs.sklearn import (
    FEATURE_TRANSFORMS,
    NODE_KINDS,
    FeatureTransform,
    SklearnExpandingRefit,
)
from dskit.pipeline.node import NodeContext

ASOF = "2026-01-01"
LOGREG = "sklearn.linear_model.LogisticRegression"

BLOCK_MS = 1000
ORIGIN_MS = 5000
MIN_BLOCKS = 3
N_BLOCKS = 9
ROWS_PER_BLOCK = 24
CLIP = 1e-4

PARAMS = {
    "estimator": LOGREG,
    "estimator_params": {"C": 1.0, "max_iter": 2000},
    "features": [
        {"field": "mid", "transform": "logit", "clip": CLIP},
        {"field": "model", "transform": "logit", "clip": CLIP},
    ],
    "label": "label",
    "time_field": "t",
    "known_field": "known",
    "block_ms": BLOCK_MS,
    "block_origin_ms": ORIGIN_MS,
    "min_train_blocks": MIN_BLOCKS,
    "output_field": "blend",
}


def _logit(p):
    held = min(max(p, CLIP), 1 - CLIP)
    return math.log(held / (1 - held))


def make_rows(seed=3, groups=None):
    """Deterministic rows: ``N_BLOCKS`` blocks from the origin, each label
    drawn from a logistic of the two logits, known 0.4-1.6 blocks late."""
    rng = random.Random(seed)
    rows = []
    for block in range(N_BLOCKS):
        for _ in range(ROWS_PER_BLOCK):
            t = ORIGIN_MS + block * BLOCK_MS + rng.randrange(BLOCK_MS)
            mid = rng.uniform(0.05, 0.95)
            model = min(max(mid + rng.uniform(-0.2, 0.2), 0.02), 0.98)
            score = 1.5 * _logit(mid) + 0.8 * _logit(model)
            group = None if groups is None else rng.choice(groups)
            slope = 1.0 if group in (None, groups[0] if groups else None) else -1.0
            label = 1.0 if rng.random() < 1 / (1 + math.exp(-slope * score)) else 0.0
            row = {
                "t": t,
                "known": t + int(BLOCK_MS * rng.uniform(0.4, 1.6)),
                "mid": mid,
                "model": model,
                "label": label,
            }
            if groups is not None:
                row["lead"] = group
            rows.append(row)
    return rows


def reference_predictions(rows, group_of=lambda row: None, **overrides):
    """Independent restatement of the ADR: for each block from the
    ``MIN_BLOCKS``-th, refit on rows known strictly before the block start
    (same group), then predict that block's rows. Returns {row index: p}."""
    from sklearn.linear_model import LogisticRegression

    def feats(row):
        return [_logit(row["mid"]), _logit(row["model"])]

    out = {}
    for block in range(MIN_BLOCKS, N_BLOCKS):
        start = ORIGIN_MS + block * BLOCK_MS
        for group in {group_of(r) for r in rows}:
            train = [
                r for r in rows
                if r["known"] < start and group_of(r) == group
            ]
            target = [
                i for i, r in enumerate(rows)
                if start <= r["t"] < start + BLOCK_MS and group_of(r) == group
            ]
            if not target or len({r["label"] for r in train}) < 2:
                continue
            model = LogisticRegression(C=1.0, max_iter=2000)
            model.fit([feats(r) for r in train], [r["label"] for r in train])
            proba = model.predict_proba([feats(rows[i]) for i in target])
            for i, row_proba in zip(target, proba):
                out[i] = row_proba[1]
    return out


def _ctx(tmp_path, name="run"):
    return NodeContext(name="t", asof=ASOF, run_dir=str(tmp_path / name))


def _run(tmp_path, rows, **overrides):
    pytest.importorskip("sklearn")
    params = {**PARAMS, **overrides}
    node = SklearnExpandingRefit("blend", params)
    ctx = _ctx(tmp_path)
    return node.run(ctx, {"records": rows}), ctx


def _statuses(out):
    return [r["blend_status"] for r in out["records"]]


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------


def test_predictions_equal_independent_block_refit(tmp_path):
    rows = make_rows()
    out, _ = _run(tmp_path, rows)
    expected = reference_predictions(rows)
    assert expected, "the fixture must produce predictions"
    for i, row in enumerate(out["records"]):
        if i in expected:
            assert row["blend_status"] == "ok"
            assert row["blend"] == pytest.approx(expected[i], abs=1e-9)
        else:
            assert row["blend_status"] == "warmup"
            assert row["blend"] is None


def test_records_keep_input_order_and_are_copies(tmp_path):
    rows = make_rows()
    snapshot = json.dumps(rows, sort_keys=True)
    out, _ = _run(tmp_path, rows)
    assert json.dumps(rows, sort_keys=True) == snapshot  # input untouched
    assert [r["t"] for r in out["records"]] == [r["t"] for r in rows]
    assert all(a is not b for a, b in zip(out["records"], rows))


def test_regression_predict_method_returns_plain_prediction(tmp_path):
    rows = make_rows()
    out, _ = _run(
        tmp_path,
        rows,
        estimator="sklearn.linear_model.Ridge",
        estimator_params={"alpha": 1.0},
        predict_method="predict",
    )
    assert "ok" in _statuses(out)
    assert all(
        isinstance(r["blend"], float) for r in out["records"] if r["blend_status"] == "ok"
    )


def test_seed_is_applied_to_each_fresh_estimator(tmp_path):
    rows = make_rows()
    a, _ = _run(tmp_path, rows, seed=11)
    b, _ = _run(tmp_path, rows, seed=11)
    assert [r["blend"] for r in a["records"]] == [r["blend"] for r in b["records"]]


# ---------------------------------------------------------------------------
# The known-time guard
# ---------------------------------------------------------------------------


def test_row_known_after_block_start_never_trains_that_block(tmp_path):
    rows = make_rows()
    # A block-0 row whose outcome only became known inside block 6.
    victim = 0
    rows[victim]["known"] = ORIGIN_MS + 6 * BLOCK_MS + 10
    flipped = [dict(r) for r in rows]
    flipped[victim]["label"] = 1.0 - rows[victim]["label"]

    base, _ = _run(tmp_path, rows)
    other, _ = _run(tmp_path, flipped)

    for i, (a, b) in enumerate(zip(base["records"], other["records"])):
        if a["t"] < ORIGIN_MS + 7 * BLOCK_MS:
            assert a["blend"] == b["blend"], f"row {i} saw a label from the future"
    late = [
        (a["blend"], b["blend"])
        for a, b in zip(base["records"], other["records"])
        if a["t"] >= ORIGIN_MS + 7 * BLOCK_MS
    ]
    assert any(x != y for x, y in late), "the row must train blocks after it is known"

    by_block = {f["block_start_ms"]: f["n_train"] for f in base["fits"]}
    expected = reference_predictions(rows)
    assert set(base["records"][i]["blend"] for i in expected)  # sanity
    block_6 = ORIGIN_MS + 6 * BLOCK_MS
    assert by_block[block_6] == sum(1 for r in rows if r["known"] < block_6)


def test_known_equal_to_block_start_is_not_known_yet(tmp_path):
    rows = make_rows()
    start = ORIGIN_MS + 5 * BLOCK_MS
    rows[1]["known"] = start
    out, _ = _run(tmp_path, rows)
    by_block = {f["block_start_ms"]: f["n_train"] for f in out["fits"]}
    assert by_block[start] == sum(1 for r in rows if r["known"] < start)


# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------


def test_warmup_rows_are_the_first_min_train_blocks(tmp_path):
    rows = make_rows()
    out, _ = _run(tmp_path, rows)
    cut = ORIGIN_MS + MIN_BLOCKS * BLOCK_MS
    for row in out["records"]:
        assert (row["blend_status"] == "warmup") == (row["t"] < cut)


def test_rows_before_the_origin_are_warmup(tmp_path):
    rows = make_rows()
    rows.append({**rows[0], "t": ORIGIN_MS - 1, "known": ORIGIN_MS})
    out, _ = _run(tmp_path, rows)
    assert out["records"][-1]["blend_status"] == "warmup"


def test_no_train_rows_when_nothing_is_known_yet(tmp_path):
    rows = make_rows()
    for row in rows:
        row["known"] = ORIGIN_MS + 10 * N_BLOCKS * BLOCK_MS
    out, _ = _run(tmp_path, rows)
    assert set(_statuses(out)) == {"warmup", "no_train_rows"}
    assert all(r["blend"] is None for r in out["records"])
    assert {f["status"] for f in out["fits"]} == {"no_train_rows"}


def test_one_class_when_training_labels_are_constant(tmp_path):
    rows = make_rows()
    for row in rows:
        row["label"] = 0.0
    out, _ = _run(tmp_path, rows)
    assert set(_statuses(out)) == {"warmup", "one_class"}
    assert all(r["blend"] is None for r in out["records"])


def test_one_class_is_not_applied_to_a_regression_method(tmp_path):
    rows = make_rows()
    for row in rows:
        row["label"] = 0.5
    out, _ = _run(
        tmp_path,
        rows,
        estimator="sklearn.linear_model.Ridge",
        estimator_params={},
        predict_method="predict",
    )
    assert "one_class" not in _statuses(out)
    assert "ok" in _statuses(out)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.pop("mid"),
        lambda row: row.update(mid=None),
        lambda row: row.update(mid=float("nan")),
        lambda row: row.update(mid=float("inf")),
        lambda row: row.update(mid="0.5"),
        lambda row: row.update(mid=1.5),
        lambda row: row.update(model=-0.1),
    ],
)
def test_bad_feature_rows_are_named_and_never_trained_or_predicted(tmp_path, mutation):
    rows = make_rows()
    # Block 7 row: its label would be known before block 8 starts.
    bad = next(i for i, r in enumerate(rows) if r["t"] >= ORIGIN_MS + 7 * BLOCK_MS)
    rows[bad]["known"] = ORIGIN_MS + 7 * BLOCK_MS
    mutation(rows[bad])

    out, _ = _run(tmp_path, rows)
    assert out["records"][bad]["blend_status"] == "bad_feature"
    assert out["records"][bad]["blend"] is None

    clean = [r for i, r in enumerate(rows) if i != bad]
    expected = reference_predictions(clean)
    kept = [r for i, r in enumerate(out["records"]) if i != bad]
    for i, row in enumerate(kept):
        if i in expected:
            assert row["blend"] == pytest.approx(expected[i], abs=1e-9)


def test_row_with_missing_label_is_predicted_but_not_trained_on(tmp_path):
    rows = make_rows()
    target = next(i for i, r in enumerate(rows) if r["t"] >= ORIGIN_MS + 7 * BLOCK_MS)
    rows[target]["label"] = None
    out, _ = _run(tmp_path, rows)
    assert out["records"][target]["blend_status"] == "ok"
    train_rows = [r for i, r in enumerate(rows) if i != target]
    start = ORIGIN_MS + 8 * BLOCK_MS
    n_known = sum(1 for r in train_rows if r["known"] < start)
    n_fit = {f["block_start_ms"]: f["n_train"] for f in out["fits"]}[start]
    assert n_fit == n_known


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def test_group_field_fits_each_group_on_its_own_rows(tmp_path):
    rows = make_rows(seed=5, groups=["g1", "g2"])
    out, _ = _run(tmp_path, rows, group_field="lead")
    expected = reference_predictions(rows, group_of=lambda r: r["lead"])
    assert expected
    for i, expect in expected.items():
        assert out["records"][i]["blend"] == pytest.approx(expect, abs=1e-9)

    pooled, _ = _run(tmp_path, rows)
    assert any(
        a["blend"] != b["blend"]
        for a, b in zip(out["records"], pooled["records"])
        if a["blend_status"] == "ok"
    )
    assert {f["group"] for f in out["fits"]} == {"g1", "g2"}
    per_block = {}
    for f in out["fits"]:
        per_block.setdefault(f["block_start_ms"], set()).add(f["group"])
    assert all(groups == {"g1", "g2"} for groups in per_block.values())


def test_missing_group_value_is_refused_by_node_name(tmp_path):
    rows = make_rows(groups=["g1", "g2"])
    del rows[2]["lead"]
    pytest.importorskip("sklearn")
    node = SklearnExpandingRefit("blend", {**PARAMS, "group_field": "lead"})
    with pytest.raises(ValueError, match=r"blend: row 2"):
        node.run(_ctx(tmp_path), {"records": rows})


# ---------------------------------------------------------------------------
# Fits output
# ---------------------------------------------------------------------------


def test_fits_records_and_fits_json(tmp_path):
    rows = make_rows()
    out, ctx = _run(tmp_path, rows)
    fits = out["fits"]
    assert [f["block_start_ms"] for f in fits] == [
        ORIGIN_MS + b * BLOCK_MS for b in range(MIN_BLOCKS, N_BLOCKS)
    ]
    first = fits[0]
    assert first["group"] is None
    assert first["cutoff_ms"] == first["block_start_ms"]
    assert first["n_train"] == sum(1 for r in rows if r["known"] < first["block_start_ms"])
    assert first["status"] == "ok" and first["n_predicted"] > 0
    assert len(first["coef"]) == 2 and isinstance(first["intercept"], float)

    path = os.path.join(ctx.run_dir, "artifacts", "blend", "fits.json")
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == fits


def test_coefficients_match_an_independent_fit(tmp_path):
    from sklearn.linear_model import LogisticRegression

    rows = make_rows()
    out, _ = _run(tmp_path, rows)
    last = out["fits"][-1]
    train = [r for r in rows if r["known"] < last["block_start_ms"]]
    model = LogisticRegression(C=1.0, max_iter=2000).fit(
        [[_logit(r["mid"]), _logit(r["model"])] for r in train],
        [r["label"] for r in train],
    )
    assert last["coef"] == pytest.approx(model.coef_[0].tolist(), abs=1e-9)
    assert last["intercept"] == pytest.approx(float(model.intercept_[0]), abs=1e-9)


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def test_transform_registry_is_a_closed_mapping_of_subclasses():
    assert set(FEATURE_TRANSFORMS) == {"identity", "logit"}
    assert all(issubclass(c, FeatureTransform) for c in FEATURE_TRANSFORMS.values())
    assert all(name == c.name for name, c in FEATURE_TRANSFORMS.items())


def test_feature_transform_is_abstract():
    with pytest.raises(TypeError):
        FeatureTransform({"field": "x", "transform": "x"})

    class Incomplete(FeatureTransform):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete({"field": "x"})


def test_identity_and_logit_values_and_clip():
    identity = FEATURE_TRANSFORMS["identity"]({"field": "x", "transform": "identity"})
    assert identity.apply(2.5) == 2.5
    logit = FEATURE_TRANSFORMS["logit"](
        {"field": "x", "transform": "logit", "clip": 0.01}
    )
    assert logit.apply(0.5) == pytest.approx(0.0)
    assert logit.apply(0.75) == pytest.approx(math.log(3))
    held = math.log(0.99 / 0.01)
    assert logit.apply(1.0) == pytest.approx(held)
    assert logit.apply(0.0) == pytest.approx(-held)
    assert logit.apply(0.001) == pytest.approx(-held)  # inside the clip: held
    assert logit.apply(1.01) is None and logit.apply(-0.01) is None


def test_logit_clip_defaults_to_one_named_constant():
    from dskit.pipeline.libs.sklearn import DEFAULT_LOGIT_CLIP

    rule = FEATURE_TRANSFORMS["logit"]({"field": "x", "transform": "logit"})
    assert rule.apply(0.0) == pytest.approx(
        math.log(DEFAULT_LOGIT_CLIP / (1 - DEFAULT_LOGIT_CLIP))
    )


@pytest.mark.parametrize("clip", [0, 0.5, 0.7, -0.1, True, "0.1", None])
def test_logit_clip_must_lie_in_open_interval(clip):
    feature = {"field": "mid", "transform": "logit", "clip": clip}
    problems = SklearnExpandingRefit.validate_params({**PARAMS, "features": [feature]})
    assert any("clip" in p for p in problems)


def test_identity_refuses_a_clip_it_never_reads():
    feature = {"field": "mid", "transform": "identity", "clip": 0.1}
    problems = SklearnExpandingRefit.validate_params({**PARAMS, "features": [feature]})
    assert any("takes no clip" in p for p in problems)


def test_a_registered_subclass_is_usable_without_touching_the_node(tmp_path, monkeypatch):
    class Square(FeatureTransform):
        name = "square"

        def apply(self, value):
            return value * value

    monkeypatch.setitem(FEATURE_TRANSFORMS, "square", Square)
    features = [{"field": "mid", "transform": "square"}, PARAMS["features"][1]]
    assert SklearnExpandingRefit.validate_params({**PARAMS, "features": features}) == []
    out, _ = _run(tmp_path, make_rows(), features=features)
    assert "ok" in _statuses(out)


# ---------------------------------------------------------------------------
# Params and refusals
# ---------------------------------------------------------------------------


def test_canonical_params_validate_clean_without_sklearn():
    assert SklearnExpandingRefit.validate_params(dict(PARAMS)) == []


def test_pack_registers_no_kind_for_it():
    assert SklearnExpandingRefit not in [cls for _, cls in NODE_KINDS]


def test_unknown_param_is_refused():
    problems = SklearnExpandingRefit.validate_params({**PARAMS, "blok_ms": 5})
    assert any("blok_ms" in p for p in problems)


@pytest.mark.parametrize("name", SklearnExpandingRefit._REQUIRED)
def test_each_required_param_is_required(name):
    params = {k: v for k, v in PARAMS.items() if k != name}
    problems = SklearnExpandingRefit.validate_params(params)
    assert any(f"{name} is required" in p for p in problems)


@pytest.mark.parametrize(
    "override",
    [
        {"block_ms": 0},
        {"block_ms": 1.5},
        {"block_ms": True},
        {"block_origin_ms": "2026-08-01"},
        {"min_train_blocks": 0},
        {"time_field": ""},
        {"known_field": 3},
        {"output_field": None},
        {"group_field": ""},
        {"features": []},
        {"features": ["mid"]},
        {"features": [{"field": "mid"}]},
        {"features": [{"field": "mid", "transform": "nope"}]},
        {"features": [{"field": "mid", "transform": "identity", "extra": 1}]},
        {"features": [PARAMS["features"][0], PARAMS["features"][0]]},
        {"estimator": "LogisticRegression"},
        {"estimator_params": [1]},
        {"seed": -1},
        {"seed": 1, "estimator_params": {"random_state": 1}},
        {"predict_method": "decision_function"},
    ],
)
def test_bad_params_are_refused(override):
    assert SklearnExpandingRefit.validate_params({**PARAMS, **override}) != []


def test_same_field_may_appear_under_two_transforms():
    features = [
        {"field": "mid", "transform": "identity"},
        {"field": "mid", "transform": "logit"},
    ]
    assert SklearnExpandingRefit.validate_params({**PARAMS, "features": features}) == []


def test_run_refusals_name_the_node_key(tmp_path):
    pytest.importorskip("sklearn")
    node = SklearnExpandingRefit("blend", dict(PARAMS))
    ctx = _ctx(tmp_path)
    rows = make_rows()

    no_time = [dict(rows[0])] + rows[1:]
    del no_time[0]["t"]
    clash = [dict(rows[0], blend=1.0)] + rows[1:]
    for bad, pattern in (
        ({"records": "x"}, r"^blend: records must be a list"),
        ({"records": [1]}, r"^blend: .*row 0"),
        ({"records": no_time}, r"^blend: row 0 has no finite 't'"),
        ({"records": clash}, r"^blend: row 0 already carries"),
    ):
        with pytest.raises(ValueError, match=pattern):
            node.run(ctx, bad)


def test_estimator_problems_are_refused_by_node_name(tmp_path):
    pytest.importorskip("sklearn")
    rows = make_rows()
    for override, pattern in (
        ({"estimator": "sklearn.linear_model.NoSuchThing"}, r"^blend: .*NoSuchThing"),
        ({"estimator_params": {"bogus": 1}}, r"^blend: .*rejected estimator_params"),
        (
            {"estimator": "sklearn.linear_model.LinearRegression", "seed": 1,
             "estimator_params": {}, "predict_method": "predict"},
            r"^blend: seed=1 .*random_state",
        ),
    ):
        node = SklearnExpandingRefit("blend", {**PARAMS, **override})
        with pytest.raises(ValueError, match=pattern):
            node.run(_ctx(tmp_path), {"records": rows})


def test_multiclass_predict_proba_is_refused(tmp_path):
    rows = make_rows()
    for i, row in enumerate(rows):
        row["label"] = float(i % 3)
    pytest.importorskip("sklearn")
    node = SklearnExpandingRefit("blend", dict(PARAMS))
    with pytest.raises(ValueError, match=r"^blend: predict_proba returned 3 classes"):
        node.run(_ctx(tmp_path), {"records": rows})

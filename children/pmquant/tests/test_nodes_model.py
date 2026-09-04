"""The six model kinds under the toolkit's conformance bar, plus behaviour.

The fixture is synthetic: six settled two-rung events across two series,
one hour of leads each on a three-fraction grid, cut by a real
``TimeSplitConfig`` handed in through ``ctx.splits``. A tiny transformer
(``d_model`` 16, one epoch) is trained ONCE per module and reused by every
probe, so the suite stays fast while ``mode="load"`` restores a real
artifact.
"""

import copy
import hashlib
import json
import os
import tempfile

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from dskit.pipeline.base import TimeSplitConfig
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs.torch import LOADER_PARAMS
from dskit.pipeline.node import NodeContext
from dskit.pipeline.split_policy import EventBounds

from pmquant.books import DecisionEpochRecord, market_record_from_epoch
from pmquant.ladder.protocols import LEAD_FRACS, lead_key
from pmquant.nodes_model import (
    CLAIMS_MONITOR,
    LADDER_ADAPTER_REF,
    LADDER_MODULE_REF,
    NODE_KINDS,
    PRED_ROW_KEYS,
    Ensemble,
    LadderPanels,
    LadderPredict,
    LadderTrain,
    MarketImplied,
    SignalQhat,
)

HOUR = 3_600_000
FRACS = [0.9, 0.5, 0.1]
YES_BIDS = ((0.40, 10), (0.35, 5))
NO_BIDS = ((0.55, 12),)
SPLITS = TimeSplitConfig(
    train_end_ms=250 * HOUR, val_end_ms=350 * HOUR, test_end_ms=500 * HOUR,
    cal_start_ms=320 * HOUR,
)
#: event -> (series, close hour, strike geometry)
EVENTS = {
    "KXB-0": ("KXB", 50, "partition"),
    "KXA-1": ("KXA", 100, "threshold"),
    "KXA-2": ("KXA", 200, "threshold"),
    "KXB-1": ("KXB", 300, "partition"),
    "KXB-2": ("KXB", 330, "partition"),
    "KXA-3": ("KXA", 400, "threshold"),
}
MODULE_PARAMS = {"d_model": 16, "n_time_layers": 1, "k_lvl": 5}
TRAIN_PARAMS = {
    "adapter": LADDER_ADAPTER_REF,
    "module": LADDER_MODULE_REF,
    "module_params": dict(MODULE_PARAMS),
    "epochs": 1,
    "lr": 0.05,
    "monitor": CLAIMS_MONITOR,
    "loader": {"batch_size": 4, "shuffle": True, "seed": 7},
}
PREDICT_PARAMS = {
    "adapter": LADDER_ADAPTER_REF,
    "module": LADDER_MODULE_REF,
    "module_params": dict(MODULE_PARAMS),
    "block": "val",
    "leads": "all",
}
PANEL_PARAMS = {"lead_fracs": FRACS, "k_lvl": 5, "drop": None, "min_contracts": 2}

EXPECTED_ROLES = {
    "pmquant-ladder-panels": "transform",
    "pmquant-ladder-train": "train",
    "pmquant-ladder-predict": "signal",
    "pmquant-ensemble": "transform",
    "pmquant-signal-qhat": "signal",
    "pmquant-market-implied": "signal",
}


def lead_record(series, event, contract, frac, close_ms, *, yes=YES_BIDS, no=NO_BIDS):
    ts = int(close_ms - frac * HOUR)
    two_sided = bool(yes and no)
    rec = DecisionEpochRecord(
        series=series, event_ticker=event, contract_ticker=contract, epoch_kind="lead",
        lead_frac=frac, epoch_ts_ms=ts, source="pit", yes_levels=tuple(yes),
        no_levels=tuple(no), p_mid=0.5 * (yes[0][0] + 1.0 - no[0][0]) if two_sided else None,
        staleness_ms=1000, admissible=True, quality_ok=two_sided, usable=two_sided,
        reason="ok" if two_sided else "no_book",
    )
    return market_record_from_epoch("kalshi", rec)


def universe():
    """Records, markets rows and outcomes for :data:`EVENTS`."""
    records, markets, outcomes = [], [], {}
    for event, (series, hour, geometry) in EVENTS.items():
        close = hour * HOUR
        if geometry == "threshold":
            rows = [("greater", 50.0, None), ("greater", 60.0, None)]
            ys = [True, False]
        else:
            rows = [("between", 40.0, 50.0), ("between", 50.0, 60.0)]
            ys = [False, True]
        for i, ((st, floor, cap), y) in enumerate(zip(rows, ys)):
            ticker = f"{event}-R{i}"
            markets.append({
                "ticker": ticker, "event_ticker": event, "series_ticker": series,
                "strike_type": st, "floor_strike": floor, "cap_strike": cap,
                "close_ms": close, "open_ms": close - 24 * HOUR,
            })
            outcomes[ticker] = y
            for frac in FRACS:
                # one unseen cell per event, so visibility is exercised
                if i == 1 and frac == 0.9:
                    records.append(lead_record(series, event, ticker, frac, close, yes=(), no=()))
                else:
                    records.append(lead_record(series, event, ticker, frac, close))
    return records, markets, outcomes


def panel_ctx(run_dir, splits=SPLITS):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(run_dir), splits=splits)


def run_panels(run_dir, family=("KXA", "KXB"), params=None, splits=SPLITS):
    records, markets, outcomes = universe()
    node = LadderPanels("panels", dict(params or PANEL_PARAMS))
    return node.run(
        panel_ctx(run_dir, splits),
        {"records": records, "outcomes": outcomes, "markets": markets, "family": list(family)},
    )


def flipped(items):
    """The OPPOSITE labels — a silent refit on these lands elsewhere."""
    out = []
    for item in items:
        clone = dict(item)
        clone["y"] = (1.0 - item["y"]).astype(np.float32)
        out.append(clone)
    return out


_FIXTURE = {}


def fixture():
    """Train ONCE per module: the panels, the artifact, and the val frame."""
    if _FIXTURE:
        return _FIXTURE
    root = tempfile.mkdtemp(prefix="pmquant-nodes-model-")
    blocks = run_panels(os.path.join(root, "panels"))
    train = LadderTrain("fit", dict(TRAIN_PARAMS))
    fit = train.run(
        NodeContext(name="t", asof="2026-01-01", run_dir=os.path.join(root, "fit")),
        {"rows": blocks["train_rows"], "val_rows": blocks["val_rows"]},
    )
    predict = LadderPredict("pred", dict(PREDICT_PARAMS))
    frame = predict.run(
        NodeContext(name="t", asof="2026-01-01", run_dir=os.path.join(root, "pred")),
        {"panel_rows": blocks["val_rows"], "artifact_path": fit["artifact_path"]},
    )
    _FIXTURE.update(root=root, blocks=blocks, fit=fit, frame=frame)
    return _FIXTURE


def same_rows(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if set(x) != set(y):
            return False
        for key in x:
            if key == "q":
                if abs(x[key] - y[key]) > 1e-9:
                    return False
            elif x[key] != y[key] and not (x[key] is None and y[key] is None):
                return False
    return True


def probes(tmp_path):
    fx = fixture()
    blocks, fit, frame = fx["blocks"], fx["fit"], fx["frame"]
    artifact = fit["artifact_path"]
    probe_record = {"contract": "KXA-1-R0", "lead_frac": 0.5}
    expected = fit["signal"].predict(probe_record)
    assert expected is not None

    def restored(out):
        signal = out["signal"]
        got = signal.predict(probe_record)
        return (
            bool(getattr(signal, "loaded", False))
            and signal.artifact_path == artifact
            and out["artifact_path"] == artifact
            and got is not None
            and abs(got - expected) < 1e-9
        )

    def frame_restored(out):
        return same_rows(out["pred_rows"], frame["pred_rows"]) and out["metrics"] == frame["metrics"]

    member_a = frame["pred_rows"]
    member_b = [{**row, "q": min(row["q"] + 0.01, 1.0), "state_hash": "b" * 64} for row in member_a]
    records, markets, outcomes = universe()
    return {
        "pmquant-ladder-panels": NodeProbe(
            params=dict(PANEL_PARAMS),
            inputs={"records": records, "outcomes": outcomes, "markets": markets,
                    "family": ["KXA", "KXB"]},
            stream_ports=("records",),
            runnable=True,
            ctx=panel_ctx(tmp_path / "panels-run"),
        ),
        "pmquant-ladder-train": NodeProbe(
            params=dict(TRAIN_PARAMS),
            required=("adapter", "module"),
            inputs={"rows": flipped(blocks["train_rows"]), "val_rows": flipped(blocks["val_rows"])},
            stream_ports=("rows", "val_rows"),
            runnable=True,
            load_artifact=artifact,
            verify_loaded=restored,
        ),
        "pmquant-ladder-predict": NodeProbe(
            params=dict(PREDICT_PARAMS),
            required=("module", "block"),
            inputs={"panel_rows": blocks["val_rows"], "artifact_path": artifact},
            stream_ports=("panel_rows",),
            runnable=True,
            load_artifact=artifact,
            verify_loaded=frame_restored,
        ),
        "pmquant-ensemble": NodeProbe(
            params={"require": 2},
            inputs={"member_0": member_a, "member_1": member_b},
            stream_ports=("member_0", "member_1"),
            runnable=True,
        ),
        "pmquant-signal-qhat": NodeProbe(
            params={"price_field": "mid"},
            inputs={"pred_rows": member_a},
            stream_ports=("pred_rows",),
            runnable=True,
        ),
        "pmquant-market-implied": NodeProbe(
            params={},
            inputs={},
            stream_ports=(),
            runnable=True,
        ),
    }


TestConformance = conformance_suite(
    registry=NODE_KINDS,
    module="pmquant.nodes_model",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestConformance",
)


# --- panels -------------------------------------------------------------------


def test_panels_are_cut_into_blocks_by_the_documents_split(tmp_path):
    out = run_panels(tmp_path)
    by_block = {
        name: sorted(item["event"] for item in out[f"{name}_rows"])
        for name in ("train", "val", "cal", "test")
    }
    assert by_block == {
        "train": ["KXA-1", "KXA-2", "KXB-0"],
        "val": ["KXB-1"],
        "cal": ["KXB-2"],
        "test": ["KXA-3"],
    }
    assert out["vocab"] == {"KXA": 0, "KXB": 1}
    assert {item["market_id"] for item in out["train_rows"] if item["series"] == "KXB"} == {1}
    assert all(item["eligible"] for item in out["train_rows"])
    assert out["train_rows"][0]["lead_fracs"] == tuple(FRACS)
    m = out["metrics"]
    assert (m["n_train_events"], m["n_val_events"], m["n_cal_events"], m["n_test_events"]) == (3, 1, 1, 1)
    assert m["n_markets"] == 2 and m["n_leads"] == 3
    assert m["n_skipped_unsettled"] == 0 and m["n_skipped_min_contracts"] == 0
    assert m["n_off_grid_rows"] == 0 and m["n_unassigned_events"] == 0
    # the family flag rides on every item
    scoped = run_panels(tmp_path / "scoped", family=("KXA",))
    assert {item["eligible"] for item in scoped["train_rows"] if item["series"] == "KXB"} == {False}


def test_panels_without_a_cal_band_yield_an_empty_cal_block(tmp_path):
    no_cal = TimeSplitConfig(train_end_ms=250 * HOUR, val_end_ms=350 * HOUR, test_end_ms=500 * HOUR)
    out = run_panels(tmp_path, splits=no_cal)
    assert out["cal_rows"] == [] and len(out["val_rows"]) == 2


def test_panels_under_event_close_agree_with_the_record_cut(tmp_path):
    records, _, _ = universe()
    bounds = {}
    for record in records:
        b = bounds.get(record.group)
        bounds[record.group] = EventBounds(
            open_ms=min(record.asof_ms, b.open_ms) if b else record.asof_ms,
            close_ms=EVENTS[record.group][1] * HOUR,
        )
    policy = TimeSplitConfig(
        train_end_ms=250 * HOUR, val_end_ms=350 * HOUR, test_end_ms=500 * HOUR,
        cal_start_ms=320 * HOUR, policy="event-close",
    ).with_event_bounds(bounds)
    a = run_panels(tmp_path / "a")
    b = run_panels(tmp_path / "b", splits=policy)
    for name in ("train", "val", "cal", "test"):
        assert [i["event"] for i in a[f"{name}_rows"]] == [i["event"] for i in b[f"{name}_rows"]]
    assert b["metrics"]["split_policy"] == "event-close"


def test_panels_refuse_without_splits_and_with_an_empty_train_block(tmp_path):
    records, markets, outcomes = universe()
    node = LadderPanels("panels", dict(PANEL_PARAMS))
    inputs = {"records": records, "outcomes": outcomes, "markets": markets, "family": ["KXA"]}
    with pytest.raises(ValueError, match="splits"):
        node.run(NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path)), inputs)
    late = TimeSplitConfig(train_end_ms=10 * HOUR, val_end_ms=350 * HOUR, test_end_ms=500 * HOUR)
    with pytest.raises(ValueError, match="train_rows"):
        node.run(panel_ctx(tmp_path, late), inputs)
    assert node.validate_inputs({**inputs, "records": iter(records)})
    assert node.validate_inputs({**inputs, "family": "KXA"})


def test_panels_params_default_to_the_shared_grid_and_refuse_bad_knobs():
    assert LadderPanels.validate_params({}) == []
    node = LadderPanels("panels", {})
    assert node.grid().lead_fracs == LEAD_FRACS
    assert any("lead_fracs" in p for p in LadderPanels.validate_params({"lead_fracs": [0.5, 0.9]}))
    assert any("drop" in p for p in LadderPanels.validate_params({"drop": ["nope"]}))
    assert any("drop" in p for p in LadderPanels.validate_params({"drop": "context"}))
    assert LadderPanels.validate_params({"drop": ["context"]}) == []
    assert any("k_lvl" in p for p in LadderPanels.validate_params({"k_lvl": 0}))
    assert any("min_contracts" in p for p in LadderPanels.validate_params({"min_contracts": 0}))


# --- train --------------------------------------------------------------------


def test_train_pins_the_ladder_adapter_and_module_and_refuses_features():
    assert LadderTrain.validate_params(dict(TRAIN_PARAMS)) == []
    wrong_adapter = LadderTrain.validate_params({**TRAIN_PARAMS, "adapter": "x.y:Z"})
    assert any("adapter" in p and LADDER_ADAPTER_REF in p for p in wrong_adapter)
    no_adapter = LadderTrain.validate_params({k: v for k, v in TRAIN_PARAMS.items() if k != "adapter"})
    assert any("adapter" in p for p in no_adapter)
    wrong_module = LadderTrain.validate_params({**TRAIN_PARAMS, "module": "torch.nn.Linear"})
    assert any("module" in p and LADDER_MODULE_REF in p for p in wrong_module)
    for knob in ("features", "label"):
        problems = LadderTrain.validate_params({**TRAIN_PARAMS, knob: ["x"]})
        assert any(knob in p for p in problems), problems
    assert any("applies_loss" in p for p in LadderTrain.validate_params(
        {**TRAIN_PARAMS, "loss": "torch.nn.functional:mse_loss"}))
    assert any("monitor" in p for p in LadderTrain.validate_params({**TRAIN_PARAMS, "monitor": "sharpe"}))
    assert CLAIMS_MONITOR in LadderTrain._MONITORS
    assert set(LOADER_PARAMS) >= set(TRAIN_PARAMS["loader"])


def test_train_refuses_a_missing_or_empty_val_block_under_the_claims_monitor():
    node = LadderTrain("fit", dict(TRAIN_PARAMS))
    rows = fixture()["blocks"]["train_rows"]
    assert any("val_rows" in p for p in node.validate_inputs({"rows": rows}))
    assert any("val_rows" in p for p in node.validate_inputs({"rows": rows, "val_rows": []}))
    assert node.validate_inputs({"rows": rows, "val_rows": rows}) == []
    relaxed = LadderTrain("fit", {k: v for k, v in TRAIN_PARAMS.items() if k != "monitor"})
    assert relaxed.validate_inputs({"rows": rows}) == []


def test_train_selects_the_epoch_with_the_minimum_claims_val_event_ll(tmp_path, monkeypatch):
    from pmquant.models import LadderPanelAdapter

    blocks = fixture()["blocks"]
    scripted = iter([0.9, 0.3, 0.7])
    monkeypatch.setattr(
        LadderPanelAdapter, "event_logloss", lambda self, *a, **k: next(scripted)
    )
    node = LadderTrain("fit", {**TRAIN_PARAMS, "epochs": 3})
    out = node.run(
        NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path)),
        {"rows": blocks["train_rows"], "val_rows": blocks["val_rows"]},
    )
    m = out["metrics"]
    assert m["monitor"] == CLAIMS_MONITOR and m["selected_epoch"] == 2
    assert m["monitor_value"] == pytest.approx(0.3) and m["epochs_run"] == 3
    curve = json.load(open(os.path.join(node.artifact_dir(
        NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))), "training_curve.json")))
    assert [row[CLAIMS_MONITOR] for row in curve["epochs"]] == [0.9, 0.3, 0.7]
    assert [row["best"] for row in curve["epochs"]] == [True, True, False]


def test_train_selected_epoch_is_the_argmin_of_the_real_curve(tmp_path):
    blocks = fixture()["blocks"]
    node = LadderTrain("fit", {**TRAIN_PARAMS, "epochs": 3, "lr": 0.2})
    ctx = NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))
    out = node.run(ctx, {"rows": blocks["train_rows"], "val_rows": blocks["val_rows"]})
    curve = json.load(open(os.path.join(node.artifact_dir(ctx), "training_curve.json")))
    values = [row[CLAIMS_MONITOR] for row in curve["epochs"]]
    assert all(v is not None for v in values)
    assert out["metrics"]["selected_epoch"] == 1 + int(np.argmin(values))
    assert out["metrics"]["monitor_value"] == pytest.approx(min(values))
    # the claims statistic averages ELIGIBLE events only: a val block whose
    # only event is out of family leaves nothing to select on
    blind = [{**item, "eligible": False} for item in blocks["val_rows"]]
    with pytest.raises(ValueError, match="ELIGIBLE val events"):
        LadderTrain("fit", dict(TRAIN_PARAMS)).run(
            NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / "blind")),
            {"rows": blocks["train_rows"], "val_rows": blind},
        )


# --- predict ------------------------------------------------------------------


def test_predict_emits_every_visible_cell_of_the_eligible_events_only(tmp_path):
    fx = fixture()
    frame = fx["frame"]
    rows = frame["pred_rows"]
    assert rows and all(set(row) == set(PRED_ROW_KEYS) for row in rows)
    val_items = fx["blocks"]["val_rows"]
    n_visible = sum(int(item["visible"].sum()) for item in val_items)
    assert len(rows) == n_visible == 5  # 2 rungs x 3 leads minus the one unseen cell
    assert frame["metrics"] == {"n_rows": 5, "n_events": 1, "n_eligible_events": 1, "block": "val"}
    sidecar = json.load(open(os.path.splitext(fx["fit"]["artifact_path"])[0] + ".json"))
    assert {row["state_hash"] for row in rows} == {sidecar["state_hash"]}
    assert {row["block"] for row in rows} == {"val"} and {row["store_ver"] for row in rows} == {None}
    first = rows[0]
    assert (first["series"], first["event"], first["step"], first["rung"]) == ("KXB", "KXB-1", 0, 0)
    assert first["lead"] == FRACS[0] and first["contract"] == "KXB-1-R0"
    assert first["partition"] is True and first["y"] == 0.0
    assert first["q"] == 1.0  # a partition softmax over the ONE visible rung at step 0
    assert 0.0 < rows[1]["q"] < 1.0 and rows[1]["q"] + rows[2]["q"] == pytest.approx(1.0)
    assert first["ask"] == pytest.approx(0.45) and first["ask_no"] == pytest.approx(0.60)
    assert first["ask_sz"] == pytest.approx(12.0) and first["bid_sz"] == pytest.approx(10.0)
    # the unseen cell (rung 1 at step 0) is not a row; rung 1 appears from step 1 on
    assert [(r["step"], r["rung"]) for r in rows] == [(0, 0), (1, 0), (1, 1), (2, 0), (2, 1)]
    # the serving table and the frame agree cell for cell
    for row in rows:
        assert fx["fit"]["signal"].predict({"contract": row["contract"], "lead_frac": row["lead"]}) == pytest.approx(row["q"])
    # out-of-family events never leave
    node = LadderPredict("pred", {**PREDICT_PARAMS, "block": "test"})
    out = node.run(
        NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path)),
        {"panel_rows": [{**item, "eligible": False} for item in val_items],
         "artifact_path": fx["fit"]["artifact_path"]},
    )
    assert out["pred_rows"] == [] and out["metrics"] == {
        "n_rows": 0, "n_events": 1, "n_eligible_events": 0, "block": "test"}


def test_predict_refuses_a_block_label_the_runs_split_contradicts(tmp_path):
    """``block`` and the wired ``$panels.<block>_rows`` are two spellings of
    one fact; with the run's split on the context the label is verified,
    so a pred_cal node wired to the test rows cannot mislabel its frame."""
    fx = fixture()
    inputs = {"panel_rows": fx["blocks"]["val_rows"], "artifact_path": fx["fit"]["artifact_path"]}
    wrong = LadderPredict("pred", {**PREDICT_PARAMS, "block": "test"})
    with pytest.raises(ValueError, match="'KXB-1'.*'val'"):
        wrong.run(panel_ctx(tmp_path / "wrong"), inputs)
    right = LadderPredict("pred", dict(PREDICT_PARAMS))
    out = right.run(panel_ctx(tmp_path / "right"), inputs)
    assert out["metrics"]["block"] == "val" and out["metrics"]["n_rows"] == 5


def test_predict_params_are_pinned_and_default_deny():
    assert LadderPredict.validate_params(dict(PREDICT_PARAMS)) == []
    assert any("block" in p for p in LadderPredict.validate_params(
        {k: v for k, v in PREDICT_PARAMS.items() if k != "block"}))
    assert any("block" in p for p in LadderPredict.validate_params({**PREDICT_PARAMS, "block": "holdout"}))
    assert any("leads" in p for p in LadderPredict.validate_params({**PREDICT_PARAMS, "leads": [0.5]}))
    assert any("epochs" in p for p in LadderPredict.validate_params({**PREDICT_PARAMS, "epochs": 2}))
    assert any("module" in p for p in LadderPredict.validate_params({**PREDICT_PARAMS, "module": "torch.nn.Linear"}))
    assert any("features" in p for p in LadderPredict.validate_params({**PREDICT_PARAMS, "features": ["x"]}))
    assert LadderPredict.outputs == ("pred_rows", "metrics")
    node = LadderPredict("pred", dict(PREDICT_PARAMS))
    assert any("panel_rows" in p for p in node.validate_inputs({"artifact_path": "x/model.pt"}))
    assert any("artifact_path" in p for p in node.validate_inputs({"panel_rows": [], "artifact_path": ""}))


# --- ensemble -----------------------------------------------------------------


def members():
    base = fixture()["frame"]["pred_rows"]
    other = [{**row, "q": min(row["q"] + 0.02, 1.0), "state_hash": "b" * 64} for row in base]
    return base, other


def test_ensemble_merges_q_by_mean_and_stamps_the_ensemble_id(tmp_path):
    a, b = members()
    node = Ensemble("ens", {"require": 2})
    ctx = NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))
    out = node.run(ctx, {"member_0": a, "member_1": b})
    rows = out["pred_rows"]
    assert len(rows) == len(a) and out["metrics"] == {"n_members": 2, "n_cells": len(a)}
    for row, ra, rb in zip(rows, a, b):
        assert row["q"] == pytest.approx((ra["q"] + rb["q"]) / 2)
        assert "state_hash" not in row and (row["series"], row["event"], row["lead"], row["rung"]) == (
            ra["series"], ra["event"], ra["lead"], ra["rung"])
    digest = hashlib.sha256(json.dumps(sorted([a[0]["state_hash"], "b" * 64])).encode()).hexdigest()[:16]
    assert {row["ensemble_id"] for row in rows} == {digest}
    panel = json.load(open(os.path.join(node.artifact_dir(ctx), "seed_panel.json")))
    assert panel["members"] == ["member_0", "member_1"] and len(panel["cells"]) == len(a)
    assert panel["cells"][0]["q"] == pytest.approx([a[0]["q"], b[0]["q"]])


def test_ensemble_is_a_loud_merge(tmp_path):
    a, b = members()
    ctx = NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))
    node = Ensemble("ens", {"require": 2})
    with pytest.raises(ValueError, match="member_1"):
        node.run(ctx, {"member_0": a, "member_1": b[:-1]})  # a differing cell set
    with pytest.raises(ValueError, match="member_1"):
        node.run(ctx, {"member_0": a, "member_1": [{**b[0], "y": 1.0 - b[0]["y"]}] + b[1:]})
    with pytest.raises(ValueError, match="member_1"):
        node.run(ctx, {"member_0": a, "member_1": [{**b[0], "ask": 0.01}] + b[1:]})
    with pytest.raises(ValueError, match="member_0"):
        node.run(ctx, {"member_0": a + a[:1], "member_1": b + b[:1]})  # duplicate cells
    with pytest.raises(ValueError, match="state_hash"):
        node.run(ctx, {"member_0": a, "member_1": [{**b[0], "state_hash": "c" * 64}] + b[1:]})
    with pytest.raises(ValueError, match="wired twice"):
        node.run(ctx, {"member_0": a, "member_1": list(a)})  # one checkpoint, two ports
    assert any("require" in p for p in node.validate_inputs({"member_0": a}))
    assert any("member_" in p for p in node.validate_inputs({"member_0": a, "seed_1": b}))
    assert any("member_1" in p for p in node.validate_inputs({"member_0": a, "member_1": iter(b)}))
    assert any("require" in p for p in Ensemble.validate_params({"require": 0}))
    assert Ensemble.validate_params({}) == []


# --- signals ------------------------------------------------------------------


def test_signal_qhat_looks_up_the_frame_and_declines_uncovered_cells(tmp_path):
    rows = fixture()["frame"]["pred_rows"]
    node = SignalQhat("sig", {"price_field": "mid"})
    ctx = NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))
    signal = node.run(ctx, {"pred_rows": rows})["signal"]
    row = rows[0]  # a partition step with ONE visible rung: q is exactly 1.0
    assert row["q"] == 1.0
    served = min(max(row["q"], 1e-6), 1.0 - 1e-6)  # the clip, restated
    record = {"contract": row["contract"], "lead_frac": row["lead"], "mid": 0.5, "asof_ms": 1}
    assert signal.predict(record) == pytest.approx(served, abs=1e-12)
    interior = rows[1]
    assert 0.0 < interior["q"] < 1.0
    assert signal.predict({**record, "contract": interior["contract"], "lead_frac": interior["lead"]}) == pytest.approx(interior["q"])
    assert signal.predict({**record, "lead_frac": 0.77}) is None
    assert signal.predict({**record, "contract": "NOPE"}) is None
    assert signal.predict({**record, "mid": None}) is None  # no price, no answer
    assert signal.q_hat(row["contract"], 0.5, 1, row["lead"]) == pytest.approx(served, abs=1e-12)
    assert signal.q_hat("NOPE", 0.5, 1, row["lead"]) == 0.5  # uncovered: trust the market
    clipped = SignalQhat("sig", {}).run(ctx, {"pred_rows": [{**row, "q": 1.0}]})["signal"]
    assert clipped.predict(record) == pytest.approx(1.0 - 1e-6)
    with pytest.raises(ValueError, match="load"):
        SignalQhat("sig", {}, mode="load", artifact="x").run(ctx, {"pred_rows": rows})
    with pytest.raises(ValueError, match="duplicate"):
        node.run(ctx, {"pred_rows": rows + rows[:1]})
    assert any("pred_rows" in p for p in node.validate_inputs({"pred_rows": iter(rows)}))
    assert any("price_field" in p for p in SignalQhat.validate_params({"price_field": ""}))
    assert lead_key(row["lead"]) == row["lead"]


def test_market_implied_is_the_stated_ask(tmp_path):
    node = MarketImplied("n0", {})
    ctx = NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))
    signal = node.run(ctx, {})["signal"]
    record = lead_record("KXA", "KXA-1", "KXA-1-R0", 0.5, 100 * HOUR)
    assert signal.predict(record) == pytest.approx(0.45)
    assert signal.predict({"contract": "X", "ask": 0.3}) == pytest.approx(0.3)
    assert signal.predict(lead_record("KXA", "KXA-1", "KXA-1-R0", 0.5, 100 * HOUR, no=())) is None
    assert signal.q_hat("X", 0.42, 1, 0.5) == pytest.approx(0.42)
    with pytest.raises(ValueError, match="load"):
        MarketImplied("n0", {}, mode="load", artifact="x").run(ctx, {})
    assert any("inputs" in p for p in node.validate_inputs({"records": []}))
    assert any("unknown" in p for p in MarketImplied.validate_params({"x": 1}))


def test_the_kind_names_pin_their_classes():
    import pmquant.models as models

    from dskit.pipeline.base import import_ref

    assert import_ref(LADDER_ADAPTER_REF) is models.LadderPanelAdapter
    assert import_ref(LADDER_MODULE_REF) is models.LadderQhatModule
    assert models.LadderPanelAdapter.requires_features is False  # what _features_required pins
    assert copy.deepcopy(PANEL_PARAMS) == PANEL_PARAMS

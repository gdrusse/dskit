"""ADR-0182 S5/S6: the forecast publisher and the per-tick MIO decider on a synthetic walk.

The fixture is a miniature P16 walk written with the real writers
(``PredictionWriter``, ``write_feature_cache``) and the real universe
file: three contiguous four-day folds, four modeled names at leads 1..3,
and a gate artifact admitting LLY and LRCX at h2 and NOW at h1 (ANET is
modeled but not admitted). Its stored ``y`` is the fold label's own
value, so the publisher's sigma/beta can be checked against it.
"""

import hashlib
import json
import math
import os
import shutil
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.false_signal import GrenanderLocalFdr
from dskit.pipeline.node import DEFAULT_NODE_KINDS, NodeContext, class_ref
from dskit.pipeline.predictions import PredictionWriter
from dskit.pipeline.uncertainty_intake import DecisionDemand, admission_problems

from intraday_equities.feature_cache import write_feature_cache
from intraday_equities.final_gates import DEVELOPMENT_EVIDENCE_SCOPE
from intraday_equities.forecast_bundle import ConfirmedCaps, ForecastBundle
from intraday_equities.nodes import Universe, _child_root, _label_from_params, _tapes_from_bars
from intraday_equities.nodes_capital import REQUIRED_INTAKES, EquityKellyMIO
from intraday_equities.replay import CashFlowPolicy, EquityReplay, FillPolicy
from intraday_equities.simulation import ForecastPublisher, MioDecider, MioDeciderNode, _Walk

KIND = "intraday_equities-forecast-publisher"
FIRST = date(2022, 5, 6)
STEP = 4
COUNT = 3
NAMES = ("ANET", "LLY", "LRCX", "NOW")
CAPS = {"ANET": 0, "LLY": 2, "LRCX": 2, "NOW": 1}
LEADS = (1, 2, 3)
LABEL = {"label_scale": "vol", "label_residual": "SPY", "label_residual_self": "raw"}
UNIVERSE = os.path.join(_child_root(), "configs", "universe-p13-pooled.json")
OPEN_MS = (14 * 60 + 30) * 60_000
DOC = "e" * 64


def _ms(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _dump(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)


def _tape():
    """One-minute sessions from 16 days before the first cutoff to past the last fold."""
    rng = np.random.default_rng(7)
    days = [FIRST - timedelta(days=16 - i) for i in range(16 + STEP * COUNT + 2)]
    stamps = np.array(
        [_ms(day) + OPEN_MS + i * 60_000 for day in days for i in range(390)], dtype=np.int64
    )
    market = rng.normal(0.0, 8e-4, stamps.size)
    prices = {"SPY": 400.0 * np.exp(np.cumsum(market))}
    for name in NAMES:
        prices[name] = 100.0 * np.exp(np.cumsum(0.8 * market + rng.normal(0.0, 6e-4, stamps.size)))
    return stamps, prices


def _splits(index):
    cutoff = FIRST + timedelta(days=STEP * index)
    embargo = cutoff - timedelta(days=1)
    return {
        "kind": "time",
        "train_start_ms": _ms(embargo - timedelta(days=10)),
        "train_end_ms": _ms(embargo) - 1,
        "val_start_ms": _ms(cutoff),
        "val_end_ms": _ms(cutoff + timedelta(days=STEP)) - 1,
        "test_end_ms": _ms(cutoff + timedelta(days=STEP)),
    }


def _write_predictions(fx):
    for (index, lead), block in sorted(fx["data"].items()):
        directory = os.path.join(fx["runs"][index], "artifacts", f"scan_h{lead:02d}")
        shutil.rmtree(directory, ignore_errors=True)
        with PredictionWriter(directory, list(NAMES), fold=index, period_minutes=30) as writer:
            for name in NAMES:
                stamps, y, yhat = block[name]
                writer.append(name, lead, list(stamps), list(y), list(yhat), 0.01)


def _write_inventory(fx):
    folds = []
    for index, run_dir in enumerate(fx["runs"]):
        paths = sorted(
            os.path.realpath(os.path.join(run_dir, "artifacts", f"scan_h{lead:02d}", "predictions.parquet"))
            for lead in LEADS
        )
        folds.append(
            {
                "cutoff": (FIRST + timedelta(days=STEP * index)).isoformat(),
                "run_dir": run_dir,
                "predictions": [{"path": p, "sha256": _sha(p)} for p in paths],
            }
        )
    manifest = {
        "schema_version": 1,
        "evidence_scope": DEVELOPMENT_EVIDENCE_SCOPE,
        "winner": {"id": "fixture-model"},
        "expected_units": [{"symbol": n, "horizon": h} for n in NAMES for h in LEADS],
        "folds": folds,
    }
    _dump(fx["inventory"], {"state": "ran", "stage": "inventory", "outputs": {"manifest": manifest}})
    fx["params"]["inventory_manifest_sha256"] = _sha(fx["inventory"])


def _repin(fx, mutate):
    for (index, lead), block in fx["data"].items():
        for name in NAMES:
            stamps, y, yhat = block[name]
            block[name] = (stamps, *mutate(index, name, lead, stamps, y, yhat))
    _write_predictions(fx)
    _write_inventory(fx)


@pytest.fixture
def fx(tmp_path):
    """A pinned three-fold walk, its gate artifact, calendar and a run dir."""
    return _build_fx(str(tmp_path))


def _build_fx(root):
    """Write the pinned three-fold walk under ``root``; return its handles."""
    stamps, prices = _tape()
    cache = os.path.join(root, "walk", "pipeline_cache", "a")
    frames = [
        {"symbol": s, "names": ["f0"], "asof_ms": stamps[:5], "close": prices[s][:5], "X": np.zeros((5, 1))}
        for s in ("SPY",) + NAMES
    ]
    tapes = [
        {"symbol": s, "asof_ms": stamps, "close": prices[s], "price_field": "close"}
        for s in ("SPY",) + NAMES
    ]
    cache_sha = write_feature_cache(cache, {"records": frames, "tape": tapes}, {"fixture": True})
    spec = Universe("universe", {"path": UNIVERSE}).run(None, {})["spec"]
    universe_sha = Universe("universe", {"path": UNIVERSE}).fingerprint()["sha256"]
    rng = np.random.default_rng(11)
    fx = {"runs": [], "data": {}, "tape": (stamps, prices), "inventory": os.path.join(root, "inv", "stages", "inventory.json")}
    for index in range(COUNT):
        splits = _splits(index)
        cutoff = FIRST + timedelta(days=STEP * index)
        run_dir = os.path.join(root, "runs", f"fixture-wf-{cutoff.isoformat()}")
        pipeline = {
            "universe": {"uses": "intraday_equities-universe", "params": {"path": UNIVERSE}},
            "features_a": {
                "uses": "intraday_equities-session-feature-cache",
                "params": {"path": "./pipeline_cache/a", "manifest_sha256": cache_sha},
            },
        }
        for lead in LEADS:
            pipeline[f"scan_h{lead:02d}"] = {
                "uses": "intraday_equities-no-information-scan",
                "params": {**LABEL, "lead_start": lead, "val_end_ms": "$splits.val_end_ms"},
            }
        config = {"name": f"fixture-wf-{cutoff.isoformat()}", "pipeline": pipeline, "splits": splits}
        resolved = {
            "document_hash": PipelineDocument.from_obj(config).hash,
            "run_hash": hashlib.sha256(run_dir.encode()).hexdigest(),
            "splits": splits,
            "data_fingerprint": {
                "features_a": {"manifest_sha256": cache_sha},
                "universe": {"sha256": universe_sha},
            },
        }
        _dump(os.path.join(run_dir, "config.json"), config)
        _dump(os.path.join(run_dir, "resolved.json"), resolved)
        fx["runs"].append(run_dir)
        arrays = _tapes_from_bars(tapes, "close", splits["val_end_ms"])
        label = _label_from_params(LABEL, arrays, int(spec["period_ms"]))
        lattice = np.array(
            [_ms(cutoff + timedelta(days=d)) + OPEN_MS + m * 1_800_000 for d in range(STEP) for m in range(1, 13)],
            dtype=np.int64,
        )
        loc = np.searchsorted(stamps, lattice)
        for lead in LEADS:
            block = {}
            for name in NAMES:
                y = label.values(name, loc, loc + lead)
                keep = np.isfinite(y)
                assert keep.all()
                yhat = 0.5 * y + rng.normal(0.0, 0.8, y.size)
                block[name] = (lattice.tolist(), y.tolist(), yhat.tolist())
            fx["data"][(index, lead)] = block
    fx["params"] = {
        "inventory_manifest": fx["inventory"],
        "gates": os.path.join(root, "gates", "stages", "gates.json"),
        "program_calendar": os.path.join(root, "calendar.json"),
        "fold_schedule": "development_outer",
        "walk_root": os.path.join(root, "walk"),
        "first_fold": 2,
        "last_fold": 2,
        "calibration_window_days": 30,
        "uncertainty": {"n_scenarios": 8, "coverage": 0.6, "window_blocks": 2, "null_draws": 199, "seed": 0},
    }
    _write_predictions(fx)
    _write_inventory(fx)
    caps = [{"unit": n, "capped_horizon": CAPS[n]} for n in NAMES]
    _dump(
        fx["params"]["gates"],
        {
            "state": "ran",
            "outputs": {
                "caps": caps,
                "metrics": {
                    "manifest_artifact": fx["inventory"],
                    "evidence_scope": DEVELOPMENT_EVIDENCE_SCOPE,
                    "deployment_eligible": False,
                },
            },
        },
    )
    fx["params"]["gates_sha256"] = _sha(fx["params"]["gates"])
    with open(os.path.join(_child_root(), "configs", "program-calendar.json"), encoding="utf-8") as handle:
        calendar = json.load(handle)
    calendar["fold_schedules"]["development_outer"].update(
        {"first": FIRST.isoformat(), "step_days": STEP, "count": COUNT, "val_days": STEP,
         "embargo_days": 1, "train_days": 10,
         "last_validation_end_exclusive": (FIRST + timedelta(days=STEP * COUNT)).isoformat()}
    )
    _dump(fx["params"]["program_calendar"], calendar)
    run = os.path.join(root, "run")
    _dump(os.path.join(run, "resolved.json"), {"document_hash": DOC})
    fx["ctx"] = NodeContext(name="sim", asof="2022-06-01", run_dir=run)
    return fx


def _run(fx):
    return ForecastPublisher("publish", fx["params"]).run(fx["ctx"], {})


def _canon(value):
    return json.dumps(value, sort_keys=True)


def test_segment_artifacts_ignore_rows_at_or_after_cutoff(fx):
    before = _run(fx)
    cutoff = before["releases"][0]["segment_start_ms"]

    def late(index, name, lead, stamps, y, yhat):
        return (
            [v * 5.0 + 1.0 if s >= cutoff else v for s, v in zip(stamps, y)],
            [-3.0 * v if s >= cutoff else v for s, v in zip(stamps, yhat)],
        )

    _repin(fx, late)
    after = _run(fx)
    assert _canon(after["releases"]) == _canon(before["releases"])
    assert _canon(after["bundles"]) != _canon(before["bundles"])

    _repin(fx, lambda i, n, lead, s, y, yhat: ([v + 0.5 for v in y] if i == 1 else y, yhat))
    assert _canon(_run(fx)["releases"]) != _canon(before["releases"])


def test_tick_path_never_reads_realized_y(fx, monkeypatch):
    import pyarrow.parquet as pq

    walk = _Walk(fx["params"])
    requested = []
    real = pq.read_table

    def spy(path, **kwargs):
        requested.append(kwargs.get("columns"))
        return real(path, **kwargs)

    try:
        monkeypatch.setattr(pq, "read_table", spy)
        assert walk.yhat(2)
    finally:
        monkeypatch.undo()
        walk.close()
    assert requested and all(cols is not None and "y" not in cols for cols in requested)

    before = _run(fx)
    _repin(fx, lambda i, n, lead, s, y, yhat: ([v * -7.0 for v in y] if i == 2 else y, yhat))
    after = _run(fx)
    assert _canon(after["bundles"]) == _canon(before["bundles"])
    assert _canon(after["releases"]) == _canon(before["releases"])
    _repin(fx, lambda i, n, lead, s, y, yhat: (y, [v + 0.25 for v in yhat] if i == 2 else yhat))
    assert _canon(_run(fx)["bundles"]) != _canon(before["bundles"])


def test_fold_train_end_after_cutoff_refuses(fx):
    path = os.path.join(fx["runs"][1], "resolved.json")
    with open(path, encoding="utf-8") as handle:
        resolved = json.load(handle)
    resolved["splits"]["train_end_ms"] = resolved["splits"]["val_start_ms"]
    _dump(path, resolved)
    with pytest.raises(ValueError, match="train_end_ms .* is not before its cutoff"):
        _run(fx)


def test_non_contiguous_folds_refuse(fx):
    # The calendar's validation window is one day shorter than its step, so
    # every fold's own geometry matches the calendar but fold k+1 does not
    # start where fold k ended.
    with open(fx["params"]["program_calendar"], encoding="utf-8") as handle:
        calendar = json.load(handle)
    calendar["fold_schedules"]["development_outer"].update(
        {"val_days": STEP - 1,
         "last_validation_end_exclusive": (FIRST + timedelta(days=STEP * COUNT - 1)).isoformat()}
    )
    _dump(fx["params"]["program_calendar"], calendar)
    for index, run_dir in enumerate(fx["runs"]):
        path = os.path.join(run_dir, "resolved.json")
        with open(path, encoding="utf-8") as handle:
            resolved = json.load(handle)
        cutoff = FIRST + timedelta(days=STEP * index)
        resolved["splits"]["val_end_ms"] = _ms(cutoff + timedelta(days=STEP - 1)) - 1
        _dump(path, resolved)
    with pytest.raises(ValueError, match="fold 1 does not start where fold 0 ended"):
        _run(fx)


def test_release_fold_rows_before_its_cutoff_never_reach_it(fx):
    # Fold 2's stored rows also carry the lattice day BEFORE its cutoff.
    # Those rows belong to no earlier release and to no tick of segment 2:
    # they must not enter its bundles, its outcome band (folds < k only) or
    # its false-signal estimate (folds < k only).
    day = FIRST + timedelta(days=STEP * 2 - 1)
    pre = [_ms(day) + OPEN_MS + m * 1_800_000 for m in range(1, 13)]
    for lead in LEADS:
        block = fx["data"][(2, lead)]
        for name in NAMES:
            stamps, y, yhat = block[name]
            block[name] = (pre + stamps, [0.3] * len(pre) + y, [0.9] * len(pre) + yhat)
    _write_predictions(fx)
    _write_inventory(fx)
    before = _run(fx)
    cutoff = before["releases"][0]["segment_start_ms"]
    assert max(pre) < cutoff
    assert before["bundles"] and all(b["decision_ts"] >= cutoff for b in before["bundles"])
    marked = set(pre)
    _repin(
        fx,
        lambda i, n, lead, s, y, yhat: (
            [-5.0 if i == 2 and t in marked else v for t, v in zip(s, y)],
            [7.0 if i == 2 and t in marked else v for t, v in zip(s, yhat)],
        ),
    )
    after = _run(fx)
    assert _canon(after["releases"]) == _canon(before["releases"])
    assert _canon(after["bundles"]) == _canon(before["bundles"])


def test_prediction_pin_mismatch_refuses(fx):
    for block in fx["data"].values():
        stamps, y, yhat = block["LLY"]
        block["LLY"] = (stamps, y, [v + 1.0 for v in yhat])
    _write_predictions(fx)
    with pytest.raises(ValueError, match="prediction artifact hash changed"):
        _run(fx)


def test_sigma_reproduces_the_stored_label(fx):
    node = ForecastPublisher("publish", fx["params"])
    walk = _Walk(fx["params"])
    try:
        release, scenarios = node._release(walk, 2, DOC)
        ticks = node._tick_rows(walk, 2, release, scenarios)
    finally:
        walk.close()
    stamps, prices = fx["tape"]
    checked = 0
    for (stamp, lead), rows in ticks.items():
        for row in rows:
            loc = int(np.searchsorted(stamps, stamp))
            own = math.log(prices[row["entity"]][loc + lead] / prices[row["entity"]][loc])
            ref = math.log(prices["SPY"][loc + lead] / prices["SPY"][loc])
            y = (own - row["beta_t"] * ref) / (row["sigma_t"] * math.sqrt(lead))
            stored_stamps, stored_y, _ = fx["data"][(2, lead)][row["entity"]]
            stored = stored_y[stored_stamps.index(stamp)]
            assert y == pytest.approx(float(np.float32(stored)), rel=1e-5, abs=1e-6)
            assert row["price"] == prices[row["entity"]][loc]
            checked += 1
    assert checked == 3 * STEP * 12


def test_artifacts_admitted_in_segment_and_refused_before_cutoff(fx):
    release = json.loads(json.dumps(_run(fx)["releases"][0]))
    members = dict(REQUIRED_INTAKES)
    for lead in release["lead_groups"]:
        envelopes = ForecastPublisher.envelopes(release, lead)
        for when, admitted in (
            (release["segment_start_ms"] + 3_600_000, True),
            (release["segment_start_ms"] - 1, False),
        ):
            demand = DecisionDemand(
                decision_ts_ms=when,
                model_identity=release["release_id"],
                max_calibration_age_ms=64 * 86_400_000,
                min_measured_coverage=0.01,
            )
            for slot, envelope in envelopes.items():
                problems = admission_problems(envelope, demand, members[slot])
                if admitted:
                    assert problems == []
                else:
                    assert any(p.startswith("post_decision") for p in problems)


def test_cap_never_carries_the_p16_scope(fx):
    release = _run(fx)["releases"][0]
    cap = release["cap"]
    assert ConfirmedCaps.problems(cap) == []
    assert cap["evidence_scope"] != DEVELOPMENT_EVIDENCE_SCOPE
    assert "post-selection" in cap["evidence_scope"]
    assert cap["deployment_eligible"] is False
    assert cap["evidence_end_ms"] == _splits(COUNT - 1)["val_end_ms"]
    assert cap["generated_ms"] == os.stat(fx["params"]["gates"]).st_mtime_ns // 1_000_000
    assert cap["generated_ms"] > cap["evidence_end_ms"]
    assert cap["evidence"]["sha256"] == fx["params"]["gates_sha256"]
    assert cap["model_release_id"] == release["release_id"]
    assert cap["caps"] == [
        {"symbol": "LLY", "capped_horizon": 2},
        {"symbol": "LRCX", "capped_horizon": 2},
        {"symbol": "NOW", "capped_horizon": 1},
    ]


def test_only_gate_admitted_units_at_capped_horizon_enter_bundles(fx):
    out = _run(fx)
    units = {(row["entity"], row["lead"]) for bundle in out["bundles"] for row in bundle["rows"]}
    assert units == {("LLY", 2), ("LRCX", 2), ("NOW", 1)}
    assert out["releases"][0]["survivors"] == ["LLY", "LRCX", "NOW"]
    assert out["releases"][0]["lead_map"] == {"LLY": 2, "LRCX": 2, "NOW": 1}


def test_gates_sha_or_manifest_mismatch_refuses(fx):
    wrong = dict(fx["params"], gates_sha256="0" * 64)
    with pytest.raises(ValueError, match="gate artifact hash changed"):
        ForecastPublisher("publish", wrong).run(fx["ctx"], {})
    with open(fx["params"]["gates"], encoding="utf-8") as handle:
        gates = json.load(handle)
    gates["outputs"]["metrics"]["manifest_artifact"] = "/elsewhere/inventory.json"
    other = fx["params"]["gates"] + ".other.json"
    _dump(other, gates)
    moved = dict(fx["params"], gates=other, gates_sha256=_sha(other))
    with pytest.raises(ValueError, match="not the pinned inventory"):
        ForecastPublisher("publish", moved).run(fx["ctx"], {})


def test_one_bundle_per_lead_group_never_mixed(fx):
    out = _run(fx)
    bundles = out["bundles"]
    cutoff = out["releases"][0]["segment_start_ms"]
    keys = [(b["decision_ts"], b["lead"]) for b in bundles]
    assert len(keys) == len(set(keys))
    for bundle in bundles:
        assert {row["lead"] for row in bundle["rows"]} == {bundle["lead"]}
        assert {row["decision_ts"] for row in bundle["rows"]} == {bundle["decision_ts"]}
        for row in bundle["rows"]:
            assert row["producer"] == {"document_sha256": DOC, "node": "publish", "output": "bundle"}
            stamp = bundle["decision_ts"]
            assert row["known_at"] == {
                "sigma": stamp, "beta": stamp, "reference": stamp, "price": stamp, "yhat": stamp,
                "pi_hat": cutoff, "pi_widened": cutoff, "scenarios": cutoff,
            }
    by_tick = {}
    for stamp, lead in keys:
        by_tick.setdefault(stamp, set()).add(lead)
    assert all(leads == {1, 2} for leads in by_tick.values())
    assert {row["entity"] for b in bundles if b["lead"] == 2 for row in b["rows"]} == {"LLY", "LRCX"}


def test_false_signal_projection_preserves_values(fx):
    node = ForecastPublisher("publish", fx["params"])
    release = node.run(fx["ctx"], {})["releases"][0]
    walk = _Walk(fx["params"])
    try:
        estimate = node._false_signal(walk, 2)
    finally:
        walk.close()
    assert len(estimate.pi_hat) == len(NAMES) * len(LEADS)
    for symbol, lead in release["lead_map"].items():
        artifact = release["uncertainty"][str(lead)]["false_signal"]["artifact"]
        cell = f"{symbol}:h{lead:02d}"
        assert artifact["pi_hat"][symbol] == estimate.pi_hat[cell]
        assert artifact["pi_widened"][symbol] == estimate.pi_widened[cell]
        assert artifact["evidence"]["estimator"] == class_ref(GrenanderLocalFdr)
        assert artifact["evidence"]["pvalues"] == dict(estimate.evidence["pvalues"])


def test_outputs_are_json_and_the_kind_is_registered(fx):
    out = _run(fx)
    assert json.loads(json.dumps(out)) == out
    for bundle in out["bundles"]:
        ForecastBundle.digest(bundle["rows"])
    assert DEFAULT_NODE_KINDS.get(KIND)[0] is ForecastPublisher
    assert ForecastPublisher.validate_params(dict(fx["params"], extra=1))
    assert ForecastPublisher.validate_params(dict(fx["params"], first_fold=1))
    params = dict(fx["params"])
    params["uncertainty"] = dict(params["uncertainty"], null_draws=10)
    assert ForecastPublisher.validate_params(params)
    assert ForecastPublisher.validate_params(fx["params"]) == []


# --- ADR-0182 S6: the per-tick MIO decider inside EquityReplay --------------

CONFIGS = os.path.join(_child_root(), "configs")
FILL_POLICY = FillPolicy.from_path(os.path.join(CONFIGS, "fill-policy.json"))
CASH_POLICY = CashFlowPolicy.from_path(os.path.join(CONFIGS, "cash-flow-policy.json"))
#: ADR-0182's development placeholders, except ``hfdr_q`` 0.9 and
#: ``uncertainty_min_coverage`` 0.05: the three-fold fixture's widened
#: false-signal rates (0.66-1.0) and short-window coverage would otherwise
#: forbid every trade, and these tests need trades to check.
MIO = {
    "risk_aversion_gamma": 2.0, "n_tangents": 32, "n_scenarios_max": 64,
    "cvar_alpha": 0.95, "cvar_limit": None, "cardinality": 5, "min_ticket": 0.0,
    "hfdr_q": 0.9, "band_bps": 10.0, "max_position_notional": 1e12,
    "bundle_max_staleness_ms": 0, "cap_max_staleness_ms": 5_529_600_000,
    "deployment_mode": False, "uncertainty_max_calibration_age_ms": 5_529_600_000,
    "uncertainty_min_coverage": 0.05, "cap_evidence_look_ahead": True,
}


def _decider(published, fx, **overrides):
    node = MioDeciderNode("decide", {"mio": {**MIO, **overrides}})
    mio = node.run(fx["ctx"], {"releases": published["releases"]})["mio"]
    return MioDecider(published["releases"][0], published["bundles"], mio, FILL_POLICY, fx["ctx"])


def _thin_bars(published, fx):
    """Each admitted name's tape bar at every lattice tick and the three minutes after it.

    Enough for a lead-2 lot's next-bar fill and its forced exit, over all
    four segment days, at a fraction of the full tape's ticks.
    """
    stamps, prices = fx["tape"]
    keep = {b["decision_ts"] + k * 60_000 for b in published["bundles"] for k in range(4)}
    return [
        {"symbol": s, "asof_ms": int(t), "open": float(p), "close": float(p), "halted": False}
        for s in published["releases"][0]["survivors"]
        for t, p in zip(stamps, prices[s])
        if int(t) in keep
    ]


def _book(fx, asof_ms, positions=None):
    """A funded, flat account at ``asof_ms`` marked at the fixture tape's closes."""
    stamps, prices = fx["tape"]
    loc = int(np.searchsorted(stamps, asof_ms))
    return {
        "asof_ms": asof_ms, "cash": 1020.0, "buying_power": 1020.0,
        "positions": dict(positions or {}),
        "mark_prices": {s: float(prices[s][loc]) for s in ("LLY", "LRCX", "NOW")},
        "gross_limit": 1020.0,
    }


def _ticks(published):
    """Decision instants that carry a bundle for every lead group, ascending."""
    leads = {}
    for bundle in published["bundles"]:
        leads.setdefault(bundle["decision_ts"], set()).add(bundle["lead"])
    return sorted(ts for ts, found in leads.items() if found == {1, 2})


class _Spy:
    """Forward to a decider, recording every ``(asof_ms, portfolio, orders)``."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def decide(self, asof_ms, portfolio):
        orders = self.inner.decide(asof_ms, portfolio)
        self.calls.append((asof_ms, json.loads(json.dumps(portfolio)), list(orders)))
        return orders


class _Scripted:
    """A stub decider: fixed orders per instant, and the portfolio it saw at each tick."""

    def __init__(self, orders):
        self.orders = orders
        self.seen = {}

    def decide(self, asof_ms, portfolio):
        self.seen[asof_ms] = portfolio
        return list(self.orders.get(asof_ms, ()))


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    """One funded four-day MIO replay of segment 2, shared by the read-only checks."""
    fx = _build_fx(str(tmp_path_factory.mktemp("s6")))
    published = _run(fx)
    spy = _Spy(_decider(published, fx))
    bars = _thin_bars(published, fx)
    out = EquityReplay(FILL_POLICY, CASH_POLICY, decider=spy).run(bars)
    return {"fx": fx, "published": published, "bars": bars, "spy": spy, "out": out}


def _by_symbol(bars):
    out = {}
    for bar in sorted(bars, key=lambda b: b["asof_ms"]):
        out.setdefault(bar["symbol"], []).append(bar)
    return out


def _zero_fee_policy():
    with open(os.path.join(CONFIGS, "fill-policy.json"), encoding="utf-8") as handle:
        raw = json.load(handle)
    return FillPolicy({**raw, "spread_bps": 0.0, "taf_per_share": 0.0, "sec31_bps": 0.0})


def _minute(i):
    return _ms(FIRST) + OPEN_MS + i * 60_000


def test_decider_sees_cash_after_this_bars_fills():
    bars = [
        {"symbol": "AAA", "asof_ms": _minute(i), "open": 10.0 + i, "close": 10.5 + i, "halted": False}
        for i in range(6)
    ]
    buy = {"symbol": "AAA", "asof_ms": _minute(0), "lead": 3, "qty": 10, "side": "buy"}
    stub = _Scripted({_minute(0): [buy]})
    out = EquityReplay(_zero_fee_policy(), CASH_POLICY, decider=stub).run(bars)
    assert set(stub.seen) == {_minute(i) for i in range(6)}
    assert stub.seen[_minute(0)]["cash"] == 1020.0
    # The entry fills at t1's open (11) BEFORE the decider runs at t1.
    at1 = stub.seen[_minute(1)]
    assert at1 == {
        "asof_ms": _minute(1), "cash": 910.0, "buying_power": 910.0,
        "positions": {"AAA": 10}, "mark_prices": {"AAA": 11.5},
        "gross_limit": 910.0 + 10 * 11.5,
    }
    # At t3 the lot exits at the next bar (index 4 = fill + lead): no longer a
    # position for the decider, still part of NAV.
    assert stub.seen[_minute(3)]["positions"] == {}
    assert stub.seen[_minute(3)]["gross_limit"] == 910.0 + 10 * 13.5
    assert stub.seen[_minute(4)]["cash"] == 910.0 + 10 * 14.0
    assert [(f["kind"], f["asof_ms"], f["price"]) for f in out["fills"]] == [
        ("entry", _minute(1), 11.0), ("exit", _minute(4), 14.0),
    ]


def test_mio_orders_are_integer_shares_filled_next_bar_open(sim):
    calls = sim["spy"].calls
    orders = [order for _, _, found in calls for order in found]
    assert orders, "the fixture must trade for this test to mean anything"
    decided = {(b["decision_ts"], b["lead"]) for b in sim["published"]["bundles"]}
    by_symbol = _by_symbol(sim["bars"])
    entries = [f for f in sim["out"]["fills"] if f["kind"] == "entry"]
    refused = {(r["symbol"], r["asof_ms"], r["lead"]): r["reason"] for r in sim["out"]["refused"]}
    matched = 0
    for order in orders:
        assert type(order["qty"]) is int and order["qty"] > 0
        assert order["side"] == "buy"
        assert (order["asof_ms"], order["lead"]) in decided
        seq = by_symbol[order["symbol"]]
        nxt = seq[[b["asof_ms"] for b in seq].index(order["asof_ms"]) + 1]
        found = [
            f for f in entries
            if (f["symbol"], f["lead"], f["asof_ms"]) == (order["symbol"], order["lead"], nxt["asof_ms"])
        ]
        if found:
            assert (found[0]["qty"], found[0]["price"]) == (order["qty"], nxt["open"])
            matched += 1
        else:
            assert refused[(order["symbol"], nxt["asof_ms"], order["lead"])] == "insufficient_cash"
    assert matched == len(entries) > 0


def test_lots_force_exit_at_fill_plus_lead(sim):
    by_symbol = _by_symbol(sim["bars"])
    fills = sim["out"]["fills"]
    entries = [f for f in fills if f["kind"] == "entry"]
    exits = [f for f in fills if f["kind"] == "exit"]
    assert len(exits) == len(entries) > 0
    for entry in entries:
        seq = by_symbol[entry["symbol"]]
        due = seq[[b["asof_ms"] for b in seq].index(entry["asof_ms"]) + entry["lead"]]
        assert any(
            (f["symbol"], f["lead"], f["side"], f["qty"], f["asof_ms"], f["price"])
            == (entry["symbol"], entry["lead"], "sell", entry["qty"], due["asof_ms"], due["open"])
            for f in exits
        )


def test_mio_refusal_trades_nothing_and_is_recorded(sim):
    decider = _decider(sim["published"], sim["fx"], uncertainty_min_coverage=0.99)
    tick = _ticks(sim["published"])[0]
    assert decider.decide(tick, _book(sim["fx"], tick)) == []
    assert [(r["asof_ms"], r["lead"], r["reason"]) for r in decider.refused] == [
        (tick, 1, "mio_refused"), (tick, 2, "mio_refused"),
    ]
    assert all("coverage" in r["detail"] for r in decider.refused)


def test_cash_never_negative_over_a_funded_multi_day_tape(sim):
    calls = sim["spy"].calls
    days = sorted({asof // 86_400_000 for asof, _, _ in calls})
    assert len(days) == STEP
    assert all(portfolio["cash"] >= 0.0 for _, portfolio, _ in calls)
    fills = sim["out"]["fills"]
    assert len({f["asof_ms"] // 86_400_000 for f in fills if f["kind"] == "entry"}) >= 2
    flows = sum(
        (f["price"] * f["qty"] - f["fee"]) if f["side"] == "sell" else -(f["price"] * f["qty"] + f["fee"])
        for f in fills
    )
    # The last tick is flat: its cash is the seed, $20 per trading day, and every fill.
    assert calls[-1][1]["positions"] == {}
    assert calls[-1][1]["cash"] == pytest.approx(1000.0 + 20.0 * len(days) + flows)


def test_lead_groups_share_one_cash_budget_in_ascending_order(sim, monkeypatch):
    seen = []
    real = EquityKellyMIO.run

    def spy(self, ctx, inputs):
        out = real(self, ctx, inputs)
        seen.append((inputs["bundle"][0]["lead"], inputs["portfolio"]["cash"], out["cash_after"]))
        return out

    monkeypatch.setattr(EquityKellyMIO, "run", spy)
    decider = _decider(sim["published"], sim["fx"])
    for tick in _ticks(sim["published"]):
        seen.clear()
        decider.decide(tick, _book(sim["fx"], tick))
        if seen and seen[0][2] < seen[0][1]:
            break
    else:
        pytest.fail("no tick where the first lead group buys")
    assert [lead for lead, _, _ in seen] == [1, 2]
    assert seen[0][1] == 1020.0
    assert seen[1][1] == seen[0][2]


def test_lot_expires_before_next_lattice_decision_for_max_lead_10():
    bars = [
        {"symbol": "AAA", "asof_ms": _minute(i), "open": 10.0, "close": 10.0, "halted": False}
        for i in range(91)
    ]
    lattice = [_minute(0), _minute(30), _minute(60)]
    stub = _Scripted({
        ts: [{"symbol": "AAA", "asof_ms": ts, "lead": 10, "qty": 1, "side": "buy"}] for ts in lattice
    })
    out = EquityReplay(_zero_fee_policy(), CASH_POLICY, decider=stub).run(bars)
    assert all(stub.seen[ts]["positions"] == {} for ts in lattice)
    assert out["refused"] == [] and out["skipped"] == []
    assert [(f["kind"], f["asof_ms"]) for f in out["fills"]] == [
        ("entry", _minute(1)), ("exit", _minute(11)),
        ("entry", _minute(31)), ("exit", _minute(41)),
        ("entry", _minute(61)), ("exit", _minute(71)),
    ]


def test_thin_unit_with_open_lot_is_skipped_not_exited(sim, monkeypatch):
    seen = []
    real = EquityKellyMIO.run

    def spy(self, ctx, inputs):
        seen.append(([row["entity"] for row in inputs["bundle"]], inputs["portfolio"]["positions"]))
        return real(self, ctx, inputs)

    monkeypatch.setattr(EquityKellyMIO, "run", spy)
    decider = _decider(sim["published"], sim["fx"])
    tick = _ticks(sim["published"])[0]
    orders = decider.decide(tick, _book(sim["fx"], tick, positions={"LLY": 4}))
    assert all(o["symbol"] != "LLY" and o["side"] == "buy" for o in orders)
    assert decider.skipped == [
        {"symbol": "LLY", "asof_ms": tick, "lead": 2, "reason": "open_lot_at_decision"},
    ]
    assert seen == [(["NOW"], {}), (["LRCX"], {})]


def test_decide_node_emits_only_json_params(sim):
    out = MioDeciderNode("decide", {"mio": MIO}).run(
        sim["fx"]["ctx"], {"releases": sim["published"]["releases"]}
    )
    assert json.loads(json.dumps(out)) == out
    assert out == {"mio": {"params": MIO, "lead_groups": [1, 2]}}
    assert DEFAULT_NODE_KINDS.get("intraday_equities-mio-decider")[0] is MioDeciderNode
    assert MioDeciderNode.role == "transform"


def test_decide_node_refuses_bound_or_undeclared_params():
    check = MioDeciderNode.validate_params
    assert check({"mio": MIO}) == []
    for bound in ({"spread_bps": 2.2}, {"cap_artifact_sha256": "a" * 64}, {"bundle_producer_node": "x"}):
        assert any("must not carry" in p for p in check({"mio": {**MIO, **bound}}))
    undeclared = {k: v for k, v in MIO.items() if k != "cap_evidence_look_ahead"}
    assert any("declared true" in p for p in check({"mio": undeclared}))
    assert any("declared true" in p for p in check({"mio": {**MIO, "cap_evidence_look_ahead": False}}))
    assert any(p.startswith("mio: ") and "hfdr_q" in p for p in check({"mio": {**MIO, "hfdr_q": 1.5}}))
    assert any("deployment_mode" in p for p in check({"mio": {**MIO, "deployment_mode": True}}))
    assert check({"mio": MIO, "extra": 1})

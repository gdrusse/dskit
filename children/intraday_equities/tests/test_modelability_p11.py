"""P11 asset-local stopping and untouched confirmation tests."""

import json
from pathlib import Path
from types import SimpleNamespace


from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.stages import plan_stages

from intraday_equities import modelability_p11 as p11


def _root():
    return Path(__file__).parents[1]


def _context(tmp_path):
    source = _root() / "configs" / "run-p11-modelability.json"
    return SimpleNamespace(
        document=load_document(str(source)),
        source_path=str(source),
        run_dir=str(tmp_path / "stage"),
        asof="2026-02-28",
    )


def test_p11_config_has_gate1_followed_directly_by_gate3():
    raw = json.loads((_root() / "configs" / "run-p11-modelability.json").read_text())
    assert list(raw["stages"]) == ["memory", "gate1", "gate3_walks", "gate3"]
    assert "gate2" not in raw["stages"]
    assert raw["stages"]["gate3_walks"]["params"]["seeds"] == list(range(19))
    assert raw["stages"]["gate3"]["params"]["seeds"] == list(range(19))
    # ADR-0092: the walks stage scores each draw, so it reads the observed
    # cells and the scoring level; the result stage reads the stop record.
    assert raw["stages"]["gate3_walks"]["inputs"] == {
        "gate1": "$gate1.rows",
        "gate1_cells": "$gate1.cells",
    }
    assert raw["stages"]["gate3"]["inputs"]["draws"] == "$gate3_walks.draws"
    alphas = {
        key: stage["params"]["alpha"]
        for key, stage in raw["stages"].items()
        if "alpha" in stage.get("params", {})
    }
    assert set(alphas) == {"gate1", "gate3_walks", "gate3"}
    assert len(set(alphas.values())) == 1
    assert raw["pipeline"]["scan"]["params"]["fit_symbols"] == p11._ASSETS
    assert len(p11._ASSETS) == 25
    assert "META" not in p11._ASSETS
    assert "GROUP" not in p11._ASSETS
    assert raw["stages"]["gate1"]["params"]["horizons"] == p11._HORIZONS
    sources = {
        node["params"]["source"]
        for node in raw["pipeline"].values()
        if node.get("uses") == "intraday_equities-bars"
    }
    assert sources == {
        "alpaca-sip-split",
        "alpaca-sip-split-b",
        "alpaca-sip-split-c",
    }
    for name in ("run-p10-modelability.json", "run-p11-modelability.json"):
        pinned = json.loads((_root() / "configs" / name).read_text())
        sink = pinned["tracking"]["sinks"]
        assert sink[0]["params"]["experiment"] == "intraday_equities"


def test_the_shipped_document_plans_gate1_into_gate3_walks_into_gate3():
    # Importing the child registered its kinds, so the whole document
    # plans offline — and only a plan sees the stage wiring: dropping
    # "draws" from Gate3WalksStage.outputs makes gate3's input an
    # undeclared reference, which no stage-level unit test can notice.
    plan = plan_stages(
        load_document(str(_root() / "configs" / "run-p11-modelability.json"))
    )
    assert list(plan.order) == ["memory", "gate1", "gate3_walks", "gate3"]
    assert plan.document.stages["gate3"].inputs["draws"] == "$gate3_walks.draws"


def test_derived_walk_filters_features_but_keeps_reference_tape(tmp_path, monkeypatch):
    monkeypatch.setattr(
        p11.p10,
        "_feature_cache_info",
        lambda _ctx: ("./cache", "/cache", "a" * 64),
    )
    document = p11._derived_document(_context(tmp_path), "JPM", 3)
    assert list(document.pipeline) == [
        "universe",
        "features",
        "asset_features",
        "reference_tape",
        "scan",
    ]
    filt = document.pipeline["asset_features"]
    assert filt.inputs == {"records": "$features.records"}
    assert filt.params["where"] == [{"field": "symbol", "op": "==", "value": "JPM"}]
    scan = document.pipeline["scan"]
    assert scan.inputs["records"] == "$asset_features.records"
    assert scan.inputs["bars"] == "$reference_tape.records"
    assert scan.params["fit_symbols"] == ["JPM"]
    assert scan.params["score_symbols"] == ["JPM"]
    assert document.stages is None


def test_gate1_stops_on_first_failure_and_never_runs_or_registers_later(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(p11, "_ASSETS", ["A"])
    monkeypatch.setattr(p11, "_HORIZONS", [1, 2, 3])
    stage = p11.Gate1Stage(
        "gate1",
        {
            "assets": ["A"],
            "horizons": [1, 2, 3],
            "attempt_registry": "attempts.jsonl",
            "alpha": 0.05,
        },
    )
    calls = []
    registered = []
    monkeypatch.setattr(p11.p10, "_child_root", lambda _ctx: str(tmp_path))
    monkeypatch.setattr(
        p11,
        "_derived_document",
        lambda _ctx, asset, horizon: SimpleNamespace(asset=asset, horizon=horizon),
    )
    monkeypatch.setattr(
        p11.p10,
        "_run_bounded_walk",
        lambda _ctx, doc, _tag, **_kw: (
            calls.append(doc.horizon) or f"walk-{doc.horizon}"
        ),
    )
    monkeypatch.setattr(
        p11,
        "_score_one",
        lambda _summary, _asset, horizon, _alpha: {
            "passes": horizon == 1,
            "t_pool": 2.0,
            "t_fold": 2.0,
            "r2oos": 0.01,
            "n_folds": 20,
        },
    )

    class Registry:
        def __init__(self, _path):
            pass

        def record(self, key, **_fields):
            registered.append(key["horizon"])
            return f"cell-{key['horizon']}"

    monkeypatch.setattr(p11, "AttemptRegistry", Registry)
    out = stage.run(SimpleNamespace(), {"preflight": True})
    assert calls == [1, 2]
    assert registered == [1, 2]
    assert out["rows"] == [
        {
            "asset": "A",
            "gate1_h": 1,
            "gate1_passes": True,
            "first_failed_h": 2,
            "attempted_horizons": [1, 2],
            "unrun_horizons": [3],
        }
    ]


def _null_scores(by_seed):
    """A ``_score_one`` double keyed on the walk handle's trailing seed."""

    def score(summary, _asset, _horizon, _alpha):
        seed = int(str(summary).rsplit("-", 1)[-1])
        return {"r2oos": by_seed[seed], "t_pool": 0.1 * seed}

    return score


def _null_scores_per_asset(by_asset):
    """A ``_score_one`` double that gives each asset its own null series."""
    scorers = {asset: _null_scores(by_seed) for asset, by_seed in by_asset.items()}

    def score(summary, asset, horizon, alpha):
        return scorers[asset](summary, asset, horizon, alpha)

    return score


def _gate3_harness(monkeypatch, assets=("A", "B")):
    monkeypatch.setattr(p11, "_ASSETS", list(assets))
    documents = []
    monkeypatch.setattr(
        p11,
        "_derived_document",
        lambda _ctx, asset, horizon, **kwargs: (
            documents.append((asset, horizon, kwargs))
            or SimpleNamespace(
                asset=asset, horizon=horizon, seed=kwargs["scramble_seed"]
            )
        ),
    )
    monkeypatch.setattr(
        p11.p10,
        "_run_bounded_walk",
        lambda _ctx, doc, _tag, **_kw: f"walk-{doc.asset}-{doc.horizon}-{doc.seed}",
    )
    gate1 = [
        {"asset": "A", "gate1_h": 2, "gate1_passes": True},
        {"asset": "B", "gate1_h": None, "gate1_passes": False},
    ]
    cells = [{"asset": "A", "horizon": 2, "skill": {"r2oos": 0.02, "t_pool": 2.0}}]
    return documents, gate1, cells


def _gate3_two_survivors(monkeypatch):
    """The same plumbing with BOTH assets passing Gate 1, at different horizons."""
    documents, _gate1, _cells = _gate3_harness(monkeypatch)
    gate1 = [
        {"asset": "A", "gate1_h": 2, "gate1_passes": True},
        {"asset": "B", "gate1_h": 5, "gate1_passes": True},
    ]
    cells = [
        {"asset": "A", "horizon": 2, "skill": {"r2oos": 0.02, "t_pool": 2.0}},
        {"asset": "B", "horizon": 5, "skill": {"r2oos": 0.04, "t_pool": 3.0}},
    ]
    return documents, gate1, cells


def test_gate3_walks_stop_an_asset_at_the_first_null_that_is_not_beaten(
    monkeypatch,
):
    # ADR-0092: seeds run in order and the asset stops at the first null
    # with R2oos >= observed — a TIE included, because ">=" is the exact
    # negation of the strict ">" the verdict applies. Seeds 3..18 are
    # neither derived nor run.
    documents, gate1, cells = _gate3_harness(monkeypatch)
    by_seed = {seed: 0.0 for seed in range(19)}
    by_seed[1] = 0.005
    by_seed[2] = 0.02  # the tie
    monkeypatch.setattr(p11, "_score_one", _null_scores(by_seed))
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    out = walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    assert [kw["scramble_seed"] for _a, _h, kw in documents] == [0, 1, 2]
    assert set(out["walks"]) == {"A:2:0", "A:2:1", "A:2:2"}
    assert out["draws"] == {"A": {"stopped": True, "stop_seed": 2, "n_draws": 3}}
    assert out["survivors"] == ["A"]


def test_gate3_walks_never_stop_a_pass_and_run_every_seed(monkeypatch):
    documents, gate1, cells = _gate3_harness(monkeypatch)
    by_seed = {seed: -0.001 * (seed + 1) for seed in range(19)}
    monkeypatch.setattr(p11, "_score_one", _null_scores(by_seed))
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    out = walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    assert [kw["scramble_seed"] for _a, _h, kw in documents] == list(range(19))
    assert len(out["walks"]) == 19
    assert out["draws"] == {"A": {"stopped": False, "stop_seed": None, "n_draws": 19}}


def test_gate3_walks_stop_on_the_last_seed_too(monkeypatch):
    # Stopping on seed 18 is still a stop: n_draws is 19 and the bound is
    # 2/20, not the verdict's calibration read.
    documents, gate1, cells = _gate3_harness(monkeypatch)
    by_seed = {seed: -0.001 for seed in range(19)}
    by_seed[18] = 0.5
    monkeypatch.setattr(p11, "_score_one", _null_scores(by_seed))
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    out = walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    assert out["draws"] == {"A": {"stopped": True, "stop_seed": 18, "n_draws": 19}}
    assert len(documents) == 19


def test_gate3_walks_take_the_observed_result_from_the_gate1_cells(monkeypatch):
    # The stop compares against the SELECTED cell's r2oos, not another
    # horizon's: with two cells for A only the h=2 one may be read.
    documents, gate1, cells = _gate3_harness(monkeypatch)
    cells = [
        {"asset": "A", "horizon": 1, "skill": {"r2oos": 0.9, "t_pool": 2.0}},
        {"asset": "A", "horizon": 2, "skill": {"r2oos": 0.02, "t_pool": 2.0}},
    ]
    by_seed = {seed: 0.03 for seed in range(19)}
    monkeypatch.setattr(p11, "_score_one", _null_scores(by_seed))
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    out = walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    assert out["draws"]["A"] == {"stopped": True, "stop_seed": 0, "n_draws": 1}


def test_gate3_walks_key_every_stop_record_by_its_own_asset(monkeypatch):
    # Two survivors, one stop and one completed family: each record is
    # filed under the asset it describes. A single-survivor test cannot
    # tell that apart from writing every record under the first survivor.
    documents, gate1, cells = _gate3_two_survivors(monkeypatch)
    a_nulls = {seed: 0.0 for seed in range(19)}
    a_nulls[1] = 0.02  # the tie that stops A on its second draw
    b_nulls = {seed: -0.001 * (seed + 1) for seed in range(19)}
    monkeypatch.setattr(
        p11, "_score_one", _null_scores_per_asset({"A": a_nulls, "B": b_nulls})
    )
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    out = walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    assert out["survivors"] == ["A", "B"]
    assert out["draws"] == {
        "A": {"stopped": True, "stop_seed": 1, "n_draws": 2},
        "B": {"stopped": False, "stop_seed": None, "n_draws": 19},
    }
    assert [(asset, kw["scramble_seed"]) for asset, _h, kw in documents] == [
        ("A", 0),
        ("A", 1),
    ] + [("B", seed) for seed in range(19)]
    assert set(out["walks"]) == {"A:2:0", "A:2:1"} | {
        f"B:5:{seed}" for seed in range(19)
    }


def test_gate3_walks_refuse_a_survivor_whose_selected_cell_was_never_scored(
    monkeypatch,
):
    # A Gate-1 cell set that never scored the selected horizon is a named
    # refusal taken BEFORE the first null is derived, not a bare KeyError
    # raised once folds have already been burned.
    documents, gate1, _cells = _gate3_harness(monkeypatch)
    cells = [{"asset": "A", "horizon": 1, "skill": {"r2oos": 0.02, "t_pool": 2.0}}]
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    try:
        walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    except ValueError as error:
        assert "Gate-1 cell" in str(error)
        assert "('A', 2)" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("an unscored selection was audited")
    assert documents == []


def test_gate3_walks_refuse_the_whole_cohort_before_the_first_asset_is_audited(
    monkeypatch,
):
    # The cell check covers ALL survivors before ANY of them is audited:
    # B's unscored selection refuses A too, and A's nulls come first.
    # Checking each survivor inside the loop instead would burn A's whole
    # 19-walk family before it ever read B's row.
    documents, gate1, cells = _gate3_two_survivors(monkeypatch)
    cells = [cell for cell in cells if cell["asset"] == "A"]
    spawned = []
    monkeypatch.setattr(
        p11.p10,
        "_run_bounded_walk",
        lambda _ctx, doc, _tag, **_kw: (
            spawned.append((doc.asset, doc.seed))
            or f"walk-{doc.asset}-{doc.horizon}-{doc.seed}"
        ),
    )
    monkeypatch.setattr(
        p11, "_score_one", _null_scores({seed: -1.0 for seed in range(19)})
    )
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    try:
        walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    except ValueError as error:
        assert "('B', 5)" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("an unscored selection was audited")
    # A is the scored, auditable survivor and it still never runs.
    assert documents == []
    assert spawned == []


def test_gate3_walks_require_the_observed_cells_and_a_level():
    stage = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    assert stage.validate_inputs({"gate1": []}) != []
    assert stage.validate_inputs({"gate1": [], "gate1_cells": []}) == []
    assert p11.Gate3WalksStage.validate_params({"seeds": list(range(19))}) != []
    assert (
        p11.Gate3WalksStage.validate_params({"seeds": list(range(19)), "alpha": 1.5})
        != []
    )


def _result_stage():
    return p11.Gate3ResultStage(
        "gate3", {"assets": ["A", "B"], "seeds": list(range(19)), "alpha": 0.05}
    )


def test_gate3_result_marks_a_stopped_asset_as_a_bounded_fail(monkeypatch):
    _documents, gate1, cells = _gate3_harness(monkeypatch)
    monkeypatch.setattr(
        p11, "_score_one", lambda *_a: (_ for _ in ()).throw(AssertionError("scored"))
    )
    monkeypatch.setattr(
        p11, "tier2_verdict", lambda *_a: (_ for _ in ()).throw(AssertionError("verdict"))
    )
    walks = {f"A:2:{seed}": f"walk-A-2-{seed}" for seed in range(3)}
    draws = {"A": {"stopped": True, "stop_seed": 2, "n_draws": 3}}
    final = _result_stage().run(
        SimpleNamespace(),
        {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": draws},
    )
    row = final["rows"][0]
    assert row["asset"] == "A"
    assert row["gate3_status"] == "fail"
    assert row["gate3_passes"] is False
    assert row["stopped"] is True
    assert row["stop_seed"] == 2
    assert row["n_draws"] == 3
    assert row["p_bound"] == 2 / 4
    assert row["null_mean"] is None
    assert row["null_sd"] is None
    assert row["calibration"] == "not_computed_early_stop"
    assert "gate3" not in row
    assert row["not_reached_reason"] is None
    # B never reached Gate 3 at all: its row names the reason in words,
    # and carries neither the stop record nor a verdict.
    not_reached = final["rows"][1]
    assert not_reached["asset"] == "B"
    assert not_reached["gate3_status"] == "not_reached"
    assert not_reached["gate3_passes"] is False
    assert not_reached["not_reached_reason"] == "gate1_failed_at_h1"
    for key in ("stopped", "stop_seed", "n_draws", "p_bound", "calibration"):
        assert key not in not_reached
    assert "gate3" not in not_reached


def test_gate3_result_bound_is_never_zero_or_absent(monkeypatch):
    _documents, gate1, cells = _gate3_harness(monkeypatch)
    monkeypatch.setattr(p11, "_score_one", lambda *_a: {"r2oos": 0.0, "t_pool": 0.0})
    for n_draws, stop_seed in ((1, 0), (19, 18)):
        walks = {f"A:2:{seed}": f"walk-A-2-{seed}" for seed in range(n_draws)}
        draws = {"A": {"stopped": True, "stop_seed": stop_seed, "n_draws": n_draws}}
        row = _result_stage().run(
            SimpleNamespace(),
            {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": draws},
        )["rows"][0]
        assert row["p_bound"] == 2 / (n_draws + 1)
        assert row["p_bound"] > 0


def test_gate3_result_scores_a_completed_family_exactly_as_before(monkeypatch):
    _documents, gate1, cells = _gate3_harness(monkeypatch)
    by_seed = {seed: -0.001 * (seed + 1) for seed in range(19)}
    monkeypatch.setattr(p11, "_score_one", _null_scores(by_seed))
    seen = []

    def verdict(observed, nulls, ts):
        seen.append((observed, list(nulls), list(ts)))
        return {"passes": True, "beat_all": True}

    monkeypatch.setattr(p11, "tier2_verdict", verdict)
    walks = {f"A:2:{seed}": f"walk-A-2-{seed}" for seed in range(19)}
    draws = {"A": {"stopped": False, "stop_seed": None, "n_draws": 19}}
    row = _result_stage().run(
        SimpleNamespace(),
        {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": draws},
    )["rows"][0]
    assert seen == [
        (0.02, [by_seed[s] for s in range(19)], [0.1 * s for s in range(19)])
    ]
    assert row["gate3_status"] == "pass"
    assert row["gate3_passes"] is True
    assert row["gate3"] == {"passes": True, "beat_all": True}
    # A completed family DID reach Gate 3, so the reason is present and empty.
    assert "not_reached_reason" in row
    assert row["not_reached_reason"] is None
    for key in ("stopped", "stop_seed", "n_draws", "p_bound", "calibration"):
        assert key not in row


def test_gate3_result_refuses_a_completed_family_short_of_the_seeds(monkeypatch):
    # "not stopped" with fewer draws than seeds is an inconsistent record,
    # never a smaller family scored as a full one.
    _documents, gate1, cells = _gate3_harness(monkeypatch)
    monkeypatch.setattr(p11, "_score_one", lambda *_a: {"r2oos": 0.0, "t_pool": 0.0})
    walks = {f"A:2:{seed}": f"walk-A-2-{seed}" for seed in range(19)}
    draws = {"A": {"stopped": False, "stop_seed": None, "n_draws": 18}}
    try:
        _result_stage().run(
            SimpleNamespace(),
            {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": draws},
        )
    except ValueError as error:
        assert "n_draws" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a short family was scored")


def test_gate3_result_reads_each_survivors_own_draw_record(monkeypatch):
    # A stopped asset never lends its record to a completed one: A carries
    # the bound and B the 19-draw verdict, each from its own draws entry.
    _documents, gate1, cells = _gate3_two_survivors(monkeypatch)
    b_nulls = {seed: -0.001 * (seed + 1) for seed in range(19)}
    monkeypatch.setattr(
        p11, "_score_one", _null_scores_per_asset({"A": {}, "B": b_nulls})
    )
    seen = []

    def verdict(observed, nulls, ts):
        seen.append((observed, list(nulls), list(ts)))
        return {"passes": True, "beat_all": True}

    monkeypatch.setattr(p11, "tier2_verdict", verdict)
    walks = {f"A:2:{seed}": f"walk-A-2-{seed}" for seed in range(2)}
    walks.update({f"B:5:{seed}": f"walk-B-5-{seed}" for seed in range(19)})
    draws = {
        "A": {"stopped": True, "stop_seed": 1, "n_draws": 2},
        "B": {"stopped": False, "stop_seed": None, "n_draws": 19},
    }
    stopped, completed = _result_stage().run(
        SimpleNamespace(),
        {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": draws},
    )["rows"]
    assert stopped["asset"] == "A"
    assert stopped["gate3_status"] == "fail"
    assert stopped["stopped"] is True
    assert stopped["stop_seed"] == 1
    assert stopped["n_draws"] == 2
    assert stopped["p_bound"] == 2 / 3
    assert "gate3" not in stopped
    assert completed["asset"] == "B"
    assert completed["gate3_status"] == "pass"
    assert completed["gate3_passes"] is True
    assert completed["gate3"] == {"passes": True, "beat_all": True}
    for key in ("stopped", "stop_seed", "n_draws", "p_bound", "calibration"):
        assert key not in completed
    # B is judged against ITS observed cell and ITS 19 nulls, not A's.
    assert seen == [
        (0.04, [b_nulls[seed] for seed in range(19)], [0.1 * seed for seed in range(19)])
    ]


def test_gate3_result_refuses_a_survivor_with_no_draws_record(monkeypatch):
    _documents, gate1, cells = _gate3_harness(monkeypatch)
    monkeypatch.setattr(
        p11, "_score_one", lambda *_a: (_ for _ in ()).throw(AssertionError("scored"))
    )
    try:
        _result_stage().run(
            SimpleNamespace(),
            {"gate1": gate1, "gate1_cells": cells, "walks": {}, "draws": {}},
        )
    except ValueError as error:
        assert "A" in str(error) and "draws record" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a survivor with no stop record was decided")


def test_gate3_result_refuses_a_draw_record_that_is_not_the_three_fields(monkeypatch):
    # Each field is checked up front, before one walk is re-scored: a
    # missing "stopped" is a named refusal, never KeyError("stopped").
    _documents, gate1, cells = _gate3_harness(monkeypatch)
    monkeypatch.setattr(
        p11, "_score_one", lambda *_a: (_ for _ in ()).throw(AssertionError("scored"))
    )
    walks = {f"A:2:{seed}": f"walk-A-2-{seed}" for seed in range(19)}
    for draw, needle in (
        ({"stop_seed": None, "n_draws": 19}, "stopped"),
        ({"stopped": "yes", "stop_seed": 2, "n_draws": 3}, "stopped"),
        ({"stopped": True, "stop_seed": 2}, "n_draws"),
        ({"stopped": True, "stop_seed": 2, "n_draws": 0}, "n_draws"),
        ({"stopped": True, "stop_seed": 19, "n_draws": 3}, "stop_seed"),
        ({"stopped": True, "stop_seed": None, "n_draws": 3}, "stop_seed"),
        ({"stopped": False, "stop_seed": 4, "n_draws": 19}, "stop_seed"),
    ):
        try:
            _result_stage().run(
                SimpleNamespace(),
                {
                    "gate1": gate1,
                    "gate1_cells": cells,
                    "walks": walks,
                    "draws": {"A": draw},
                },
            )
        except ValueError as error:
            assert needle in str(error)
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError(f"{draw!r} was decided")


def test_gate3_result_refuses_a_stop_record_whose_n_draws_denies_the_stop_seed(
    monkeypatch,
):
    # The audit runs the seeds in order, so a stop on seed 2 is the third
    # draw and nothing else. Taking n_draws=19 there would publish the
    # completed family's bound 2/20 for an audit that ran three draws.
    _documents, gate1, cells = _gate3_harness(monkeypatch)
    monkeypatch.setattr(
        p11, "_score_one", lambda *_a: (_ for _ in ()).throw(AssertionError("scored"))
    )
    draw = {"stopped": True, "stop_seed": 2, "n_draws": 19}
    try:
        _result_stage().run(
            SimpleNamespace(),
            {"gate1": gate1, "gate1_cells": cells, "walks": {}, "draws": {"A": draw}},
        )
    except ValueError as error:
        assert "n_draws" in str(error) and "stop_seed" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a stop record that denies its own seed was decided")
    problems = p11._draw_problems("A", draw, list(range(19)))
    assert len(problems) == 1 and "n_draws=19" in problems[0]
    assert p11._draw_problems("A", {**draw, "n_draws": 3}, list(range(19))) == []


def test_gate3_result_refuses_the_whole_cohort_before_the_first_row_is_decided(
    monkeypatch,
):
    # The stop records are checked over ALL survivors before ANY row is
    # decided: B's short family refuses A too, and A's row comes first.
    # Checking each row inside the loop instead would re-score A's 19
    # walks and file its tier-2 verdict before it ever read B's record.
    _documents, gate1, cells = _gate3_two_survivors(monkeypatch)
    scored = []
    verdicts = []
    monkeypatch.setattr(
        p11,
        "_score_one",
        lambda summary, *_a: scored.append(summary) or {"r2oos": 0.0, "t_pool": 0.0},
    )
    monkeypatch.setattr(
        p11, "tier2_verdict", lambda *args: verdicts.append(args) or {"passes": True}
    )
    walks = {f"A:2:{seed}": f"walk-A-2-{seed}" for seed in range(19)}
    draws = {
        "A": {"stopped": False, "stop_seed": None, "n_draws": 19},
        "B": {"stopped": False, "stop_seed": None, "n_draws": 18},
    }
    try:
        _result_stage().run(
            SimpleNamespace(),
            {"gate1": gate1, "gate1_cells": cells, "walks": walks, "draws": draws},
        )
    except ValueError as error:
        assert "B did not stop" in str(error) and "n_draws" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a short family was scored")
    # A is the well-formed, decidable survivor and it still never runs.
    assert scored == []
    assert verdicts == []


def test_gate3_result_requires_the_draws_record(monkeypatch):
    monkeypatch.setattr(p11, "_ASSETS", ["A", "B"])
    stage = _result_stage()
    assert stage.validate_inputs({"gate1": [], "gate1_cells": [], "walks": {}}) != []
    assert (
        stage.validate_inputs(
            {"gate1": [], "gate1_cells": [], "walks": {}, "draws": {}}
        )
        == []
    )


def test_the_walks_stages_own_draws_decide_the_result_stages_rows(monkeypatch):
    # ADR-0092's seam, end to end: the stop records the walks stage really
    # produces are the ones the result stage decides on. Every other
    # result-stage test hand-authors its draws dict, so an audit that
    # miscounted its own draws — or named the wrong stop seed — would pass
    # both stages while the published Besag-Clifford bound was wrong.
    _documents, gate1, cells = _gate3_two_survivors(monkeypatch)
    a_nulls = {seed: 0.0 for seed in range(19)}
    a_nulls[3] = 0.02  # the tie that stops A on its fourth draw
    b_nulls = {seed: -0.001 * (seed + 1) for seed in range(19)}
    monkeypatch.setattr(
        p11, "_score_one", _null_scores_per_asset({"A": a_nulls, "B": b_nulls})
    )
    seen = []

    def verdict(observed, nulls, ts):
        seen.append((observed, list(nulls), list(ts)))
        return {"passes": True, "beat_all": True}

    monkeypatch.setattr(p11, "tier2_verdict", verdict)
    walks = p11.Gate3WalksStage("gate3_walks", {"seeds": list(range(19)), "alpha": 0.05})
    out = walks.run(SimpleNamespace(), {"gate1": gate1, "gate1_cells": cells})
    stopped, completed = _result_stage().run(
        SimpleNamespace(),
        {
            "gate1": gate1,
            "gate1_cells": cells,
            "walks": out["walks"],
            "draws": out["draws"],
        },
    )["rows"]
    assert stopped["asset"] == "A"
    assert stopped["gate3_status"] == "fail"
    assert stopped["gate3_passes"] is False
    assert stopped["stopped"] is True
    assert stopped["stop_seed"] == 3
    assert stopped["n_draws"] == 4
    # The bound the row publishes counts the null walks the audit really ran.
    ran = [key for key in out["walks"] if key.startswith("A:")]
    assert stopped["n_draws"] == len(ran)
    assert stopped["p_bound"] == 2 / 5
    assert "gate3" not in stopped
    assert completed["asset"] == "B"
    assert completed["gate3_status"] == "pass"
    assert completed["gate3_passes"] is True
    assert completed["gate3"] == {"passes": True, "beat_all": True}
    assert "not_reached_reason" in completed
    assert completed["not_reached_reason"] is None
    for key in ("stopped", "stop_seed", "n_draws", "p_bound", "calibration"):
        assert key not in completed
    # One verdict, over B's own 19 nulls in seed order — A never reaches it.
    assert seen == [
        (0.04, [b_nulls[seed] for seed in range(19)], [0.1 * seed for seed in range(19)])
    ]


def test_derived_walk_filters_the_tape_to_the_asset_and_its_reference(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        p11.p10,
        "_feature_cache_info",
        lambda _ctx: ("./cache", "/cache", "a" * 64),
    )
    document = p11._derived_document(_context(tmp_path), "JPM", 3)
    assert list(document.pipeline) == [
        "universe",
        "features",
        "asset_features",
        "reference_tape",
        "scan",
    ]
    tape = document.pipeline["reference_tape"]
    assert tape.uses == "filter"
    assert tape.inputs == {"records": "$features.tape"}
    assert tape.params["where"] == [
        {"field": "symbol", "op": "in", "value": ["JPM", "SPY"]}
    ]
    assert document.pipeline["scan"].inputs["bars"] == "$reference_tape.records"


def test_derived_walk_keeps_only_spy_when_spy_is_the_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        p11.p10,
        "_feature_cache_info",
        lambda _ctx: ("./cache", "/cache", "a" * 64),
    )
    document = p11._derived_document(_context(tmp_path), "SPY", 3)
    assert document.pipeline["reference_tape"].params["where"] == [
        {"field": "symbol", "op": "in", "value": ["SPY"]}
    ]


def _preflight_harness(tmp_path, monkeypatch, peak):
    monkeypatch.setattr(p11, "_ASSETS", ["A"])
    monkeypatch.setattr(
        p11.p10, "_feature_cache_info", lambda _ctx: ("./cache", "/cache", "a" * 64)
    )
    monkeypatch.setattr(p11, "_largest_asset", lambda _path, _assets: ("A", 10))
    tags = []
    monkeypatch.setattr(
        p11,
        "_derived_document",
        lambda _ctx, asset, horizon, **kwargs: (
            tags.append(kwargs["tag"]) or SimpleNamespace(asset=asset, horizon=horizon)
        ),
    )
    measured = []
    monkeypatch.setattr(
        p11.p10,
        "_measure_walk",
        lambda _ctx, doc, tag: (measured.append((doc.asset, tag)) or ("/summary", peak)),
    )
    monkeypatch.setattr(p11, "_score_one", lambda *_a: {"r2oos": 0.0})
    stage = p11.MemoryPreflightStage(
        "memory", {"assets": ["A"], "memory_limit_bytes": p11._MEMORY_LIMIT}
    )
    ctx = SimpleNamespace(document=SimpleNamespace(hash="f" * 64))
    return stage, ctx, tags, measured


def test_the_preflight_measures_one_fresh_walk_per_study_identity(
    tmp_path, monkeypatch
):
    # ADR-0093: the reading comes from measure_one, which needs a fresh
    # spawn, so the derived preflight walk is named for the staged
    # document's identity — a revised study measures again instead of
    # tripping over the previous study's finished walk.
    stage, ctx, tags, measured = _preflight_harness(tmp_path, monkeypatch, peak=5)
    out = stage.run(ctx, {})
    assert measured == [("A", "p11-memory-a")]
    assert tags == ["preflight-" + "f" * 8]
    assert out["peak_rss_bytes"] == 5
    assert out["summary_dir"] == "/summary"
    assert out["passed"] is True
    assert out["limit_bytes"] == p11._MEMORY_LIMIT


def test_the_preflight_refuses_a_peak_at_the_limit(tmp_path, monkeypatch):
    stage, ctx, _tags, _measured = _preflight_harness(
        tmp_path, monkeypatch, peak=p11._MEMORY_LIMIT
    )
    try:
        stage.run(ctx, {})
    except MemoryError as error:
        assert "strictly below" in str(error)
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a peak at the limit passed")


def test_the_memory_envelope_is_one_value_in_both_modules():
    # p10's constant is what a fold actually gets; p11's is what P11's
    # preflight validates against. Two copies, so pin the agreement.
    assert p11._MEMORY_LIMIT == p11.p10._MEMORY_LIMIT


def test_the_worker_knob_is_never_graded_into_the_document_identity():
    # The real claim: no stage declares it, so no width can move the
    # hash that names the run directory and keys the stored artifacts.
    for name in ("run-p10-modelability.json", "run-p11-modelability.json"):
        raw = json.loads((_root() / "configs" / name).read_text())
        for stage in raw["stages"].values():
            assert "workers" not in stage.get("params", {})
    for stage_class in (
        p11.Gate1Stage,
        p11.Gate3WalksStage,
        p11.p10.Gate1WalksStage,
        p11.p10.Gate3WalksStage,
    ):
        assert "workers" not in stage_class._PARAMS
    assert (
        p11.Gate3WalksStage.validate_params({"seeds": list(range(19)), "workers": 4})
        != []
    )


def test_the_reference_tape_follows_the_configs_declared_residual(
    tmp_path, monkeypatch
):
    # The kept tape must be derived from label_residual, not from a
    # second copy of "SPY": editing the config would otherwise drop the
    # reference and fail inside the fold subprocess, and only for the
    # assets that are not themselves the residual.
    monkeypatch.setattr(
        p11.p10, "_feature_cache_info", lambda _ctx: ("./cache", "/cache", "a" * 64)
    )
    ctx = _context(tmp_path)
    obj = ctx.document.to_obj()
    obj["pipeline"]["scan"]["params"]["label_residual"] = "QQQ"
    ctx.document = PipelineDocument.from_obj(obj)
    document = p11._derived_document(ctx, "JPM", 3)
    assert document.pipeline["reference_tape"].params["where"] == [
        {"field": "symbol", "op": "in", "value": ["JPM", "QQQ"]}
    ]


def test_the_reference_tape_keeps_one_symbol_when_no_residual_is_declared(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        p11.p10, "_feature_cache_info", lambda _ctx: ("./cache", "/cache", "a" * 64)
    )
    ctx = _context(tmp_path)
    obj = ctx.document.to_obj()
    obj["pipeline"]["scan"]["params"].pop("label_residual")
    ctx.document = PipelineDocument.from_obj(obj)
    document = p11._derived_document(ctx, "JPM", 3)
    assert document.pipeline["reference_tape"].params["where"] == [
        {"field": "symbol", "op": "in", "value": ["JPM"]}
    ]

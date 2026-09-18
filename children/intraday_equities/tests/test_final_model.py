"""``final_model.py`` — domain assembly for the ten-lead final release
(ADR-0114 Phase 2, reconciling ADR-0113's Phase 1).

Everything here is SYNTHETIC: tiny in-memory rows and a canned
``evaluate`` callback stand in for a real per-candidate LightGBM fit. No
market data, no real LightGBM fit against real history, no real P16
artifact load, and no pipeline `run`/`walkforward` execution — this
whole gate is synthetic-tests-only (the master plan's own ruling).
"""

from __future__ import annotations

import contextlib

from unittest.mock import patch

import pytest

from dskit.pipeline.kinds_search import CandidateInventory, OneStandardErrorSelector, TrialLedger
from dskit.pipeline.node import ConfigError
import intraday_equities.final_model as final_model

from intraday_equities.final_model import (
    EMBARGO_END_MS,
    EMBARGO_START_MS,
    EVIDENCE_FIELDS,
    HEADS,
    LOCKBOX_START_MS,
    boundary_flags,
    build_candidate_inventory,
    cluster_scores_by_day,
    hpo_space,
    lean_feature_drop,
    permitted_for_refit,
    refit_heads,
    run_lead_selection,
    simplicity_key,
    squared_error_improvement,
)


def _json_artifact(run_dir, payload):
    import hashlib
    import json

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = run_dir / "artifacts" / "json" / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "path": f"artifacts/json/{digest}.json",
        "sha256": digest,
        "bytes": len(raw),
        "media_type": "application/json",
    }


def test_module_contract_describes_the_wired_fixture_only_final_refit():
    contract = " ".join(final_model.__doc__.split())
    assert "wired by ``configs/run-final-refit.json``" in contract
    assert "ten labelled input wires" in contract
    assert "can execute ONLY on the ``fixture`` release channel" in contract
    assert "The ``production`` channel refuses outright" in contract
    assert (
        "Synthetic helper assembly is not a completed real final-model release"
        in contract
    )


def test_final_refit_refuses_pending_hpo_evidence_pins():
    with pytest.raises(ConfigError, match="pending"):
        final_model.FinalRefit("refit", {
            "hpo_run_dir": "PENDING-HPO-RUN",
            "hpo_document_sha256": "2db8e95a420614a91101535bb21d1ca4cd2cb6536bdb13791307754ae2805219",
            "hpo_evidence": {head: "PENDING-HPO-EVIDENCE" for head in HEADS},
            "feature_order": ["x", "drop"],
            "categorical_feature": [],
            "categorical_encoding": {},
            "predict_fixture": [[0.0, 0.0]],
        })


def test_final_refit_refuses_reused_or_wrong_head_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "intraday_equities.final_model.load_document",
        lambda path: type("Document", (), {"hash": "d" * 64, "to_obj": lambda self: {}})(),
    )
    ledger = {"inventory_digest": "a" * 64, "rows": [{"overrides": {"x": 1}}]}
    import hashlib
    import json
    ledger_digest = hashlib.sha256(
        json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = _json_artifact(tmp_path, {
        "producer_key": "scan_h01", "feature_order": ["x"],
        "categorical_feature": [], "ledger": ledger, "selection": {
            "inventory_digest": "a" * 64, "ledger_digest": ledger_digest,
            "selected_candidate": {"x": 1},
        },
    })
    node = object.__new__(final_model.FinalRefit)
    node.params = {
        "hpo_run_dir": str(tmp_path), "hpo_document_sha256": "d" * 64,
        "hpo_evidence": {head: manifest for head in HEADS},
        "feature_order": ["x"], "categorical_feature": [],
        "categorical_encoding": {}, "predict_fixture": [[0.0]],
        "refit_identity": {"source": {"sha256": "1" * 64},
                           "cache": {"sha256": "2" * 64},
                           "train_start_ms": 1, "refit_end_ms": LOCKBOX_START_MS,
                           "embargo_start_ms": EMBARGO_START_MS,
                           "embargo_end_ms": EMBARGO_END_MS},
    }
    monkeypatch.setattr(node, "_verified_hpo_outputs", lambda: {
        head: manifest for head in HEADS
    })
    with pytest.raises(ValueError, match="distinct|producer"):
        node._winners()


def test_final_refit_requires_complete_data_cache_and_cut_identity():
    params = {
        "hpo_run_dir": "/real/run", "hpo_document_sha256": "d" * 64,
        "hpo_evidence": {head: {} for head in HEADS}, "feature_order": ["x"],
        "categorical_feature": [], "categorical_encoding": {},
        "predict_fixture": [[0.0]],
    }
    with pytest.raises(ConfigError, match="refit_identity"):
        final_model.FinalRefit("refit", params)


def test_final_refit_refuses_hash_valid_artifact_absent_from_producer_record(tmp_path, monkeypatch):
    manifest = _json_artifact(tmp_path, {"forged": True})
    (tmp_path / "result.json").write_text(
        '{"document_hash":"' + "d" * 64 + '","run_hash":"' + "e" * 64
        + '","state":"ran","exit_code":0}'
    )
    (tmp_path / "carry.json").write_text("{}")
    (tmp_path / "nodes").mkdir()
    monkeypatch.setattr(
        "intraday_equities.final_model.load_document",
        lambda path: type("Document", (), {"hash": "d" * 64, "to_obj": lambda self: {}})(),
    )
    node = object.__new__(final_model.FinalRefit)
    node.params = {"hpo_run_dir": str(tmp_path), "hpo_document_sha256": "d" * 64,
                   "hpo_evidence": {head: manifest for head in HEADS}}
    with pytest.raises(ValueError, match="producer record|carry"):
        node._verified_hpo_outputs()


def test_final_refit_refuses_cross_run_manifest_substitution(tmp_path):
    own = _json_artifact(tmp_path, {"run": "own"})
    other_dir = tmp_path / "other"
    other = _json_artifact(other_dir, {"run": "other"})
    (tmp_path / "result.json").write_text(
        '{"document_hash":"' + "d" * 64 + '","run_hash":"' + "e" * 64
        + '","state":"ran","exit_code":0}'
    )
    carry = {f"scan_{head}": {"hpo_ledger": own} for head in HEADS}
    import json
    (tmp_path / "carry.json").write_text(json.dumps(carry))
    (tmp_path / "nodes").mkdir()
    for index, head in enumerate(HEADS, 1):
        record = {"node": f"scan_{head}", "status": "ok",
                  "outputs": {"hpo_ledger": own}}
        (tmp_path / "nodes" / f"{index:02d}-scan_{head}.json").write_text(json.dumps(record))
    node = object.__new__(final_model.FinalRefit)
    node.params = {"hpo_run_dir": str(tmp_path), "hpo_document_sha256": "d" * 64,
                   "hpo_evidence": {head: other for head in HEADS}}
    with pytest.raises(ValueError, match="trustworthy run attestation"):
        node._verified_hpo_outputs()


def test_final_refit_refuses_mutually_consistent_unbound_run_sidecars(tmp_path):
    """Mutable result/record/carry agreement is not a run attestation."""
    manifest = _json_artifact(tmp_path, {"forged": True})
    import json

    (tmp_path / "result.json").write_text(json.dumps({
        "document_hash": "d" * 64, "run_hash": "e" * 64,
        "state": "ran", "exit_code": 0,
    }))
    (tmp_path / "carry.json").write_text(json.dumps({
        f"scan_{head}": {"hpo_ledger": manifest} for head in HEADS
    }))
    (tmp_path / "nodes").mkdir()
    for index, head in enumerate(HEADS, 1):
        (tmp_path / "nodes" / f"{index:02d}-scan_{head}.json").write_text(
            json.dumps({"node": f"scan_{head}", "status": "ok",
                        "outputs": {"hpo_ledger": manifest}})
        )
    node = object.__new__(final_model.FinalRefit)
    node.params = {
        "hpo_run_dir": str(tmp_path), "hpo_document_sha256": "d" * 64,
        "hpo_evidence": {head: manifest for head in HEADS},
    }
    with pytest.raises(ValueError, match="trustworthy run attestation"):
        node._verified_hpo_outputs()


def test_final_refit_refuses_asserted_identities_no_run_or_content_earns():
    """Config digests are an expectation; they can never BE the attestation."""
    params = {
        "release_channel": final_model.FIXTURE_CHANNEL,
        "hpo_run_dir": "/completed/run", "hpo_document_sha256": "d" * 64,
        "hpo_evidence": {head: {} for head in HEADS},
        "feature_order": ["x"], "categorical_feature": [],
        "categorical_encoding": {}, "predict_fixture": [[0.0]],
        "refit_identity": {
            "source": {"sha256": "1" * 64}, "cache": {"sha256": "2" * 64},
            "rows": {head: "PENDING-ROW-IDENTITY" for head in HEADS},
            "train_start_ms": 1, "refit_end_ms": LOCKBOX_START_MS,
            "embargo_start_ms": EMBARGO_START_MS,
            "embargo_end_ms": EMBARGO_END_MS,
        },
    }
    with pytest.raises(ConfigError, match="non-executable"):
        final_model.FinalRefit("refit", params)

    node = object.__new__(final_model.FinalRefit)
    node.params = params
    with pytest.raises(ValueError, match="non-empty list of labelled rows"):
        node.run(None, {head: [] for head in HEADS})
    with pytest.raises(ValueError, match="trustworthy run attestation"):
        node._verified_hpo_outputs()


def test_final_refit_recomputes_complete_pinned_one_se_selection():
    inventory = CandidateInventory(REAL_HPO_SPACE, n_trials=24, seed=0)
    ledger = TrialLedger(inventory, evidence_fields=EVIDENCE_FIELDS)
    for index, candidate in enumerate(inventory.combinations):
        ledger.record(candidate, float(index), se=0.0, diagnostics={},
                      on_boundary={}, fit_seed=0, cuts={}, n_rows={},
                      train_val_gap=0.0, collapsed_prediction_variance=False)
    selection = OneStandardErrorSelector(
        select="max", simplicity_key=simplicity_key
    ).select(ledger).to_obj()
    evidence = {"ledger": ledger.to_obj(), "selection": selection}
    node = object.__new__(final_model.FinalRefit)
    node._hpo_document = {"stages": {"finalist": {"params": {"templates": [{
        "family": "pooled-lightgbm", "model": {"hpo_space": REAL_HPO_SPACE,
        "hpo_trials": 24, "hpo_seed": 0, "estimator_params": {}}
    }]}}}}
    assert node._winner_from_evidence("h01", evidence) == selection["selected_candidate"]

    incomplete = {"inventory_digest": inventory.digest,
                  "evidence_fields": list(EVIDENCE_FIELDS),
                  "rows": ledger.to_obj()["rows"][:-1]}
    with pytest.raises(ValueError, match="incomplete|noncanonical"):
        node._winner_from_evidence("h01", {"ledger": incomplete, "selection": selection})
    wrong = dict(selection)
    wrong["selected_candidate"] = dict(inventory.combinations[0])
    with pytest.raises(ValueError, match="one-standard-error"):
        node._winner_from_evidence("h01", {"ledger": ledger.to_obj(), "selection": wrong})

# ---------------------------------------------------------------------------
# The real P16 lean mask — the exact 33-column drop list, verbatim from
# configs/run-p16-feature-mask-zoo.json's "lean" template (independently
# transcribed here, the same way test_sklearn.py's _combined_digest
# independently mirrors the pack's own hash rule, so the test asserts
# something the module could not merely echo back).
# ---------------------------------------------------------------------------

REAL_LEAN_DROP = (
    "after_holiday", "amihud_3s", "dow_cos", "dow_sin", "mom_1w", "mom_2s",
    "mom_3s", "month_cos", "month_sin", "range_3s", "ret_1w", "ret_2s",
    "ret_3s", "ret_lag_10", "ret_lag_11", "ret_lag_12", "ret_lag_13",
    "ret_lag_14", "ret_lag_15", "ret_lag_16", "ret_lag_17", "ret_lag_18",
    "ret_lag_19", "ret_lag_5", "ret_lag_6", "ret_lag_7", "ret_lag_8",
    "ret_lag_9", "rv_1w", "rv_2s", "rv_3s", "session_gap_days", "vol_3s",
)


# ---------------------------------------------------------------------------
# HEADS / hpo_space() / candidate inventory
# ---------------------------------------------------------------------------

#: The real five-dimension LightGBM HPO grid, verbatim from
#: configs/run-final-hpo.json's "lean" (family "pooled-lightgbm")
#: finalist template (independently transcribed here, the same
#: discipline REAL_LEAN_DROP above uses, so the test asserts something
#: hpo_space() could not merely echo back).
REAL_HPO_SPACE = {
    "learning_rate": [0.003, 0.01, 0.03],
    "num_leaves": [4, 8, 16],
    "min_child_samples": [500, 1000, 2000, 4000],
    "reg_lambda": [10.0, 100.0, 1000.0],
    "reg_alpha": [0.0, 0.1, 1.0],
}


def test_heads_is_the_ten_exact_lead_names_in_order():
    assert HEADS == tuple(f"h{i:02d}" for i in range(1, 11))


def test_hpo_space_reads_the_real_five_dimension_grid_from_its_one_source():
    space = hpo_space()
    assert space == REAL_HPO_SPACE
    total = 1
    for values in space.values():
        total *= len(values)
    assert total == 324


def test_hpo_space_refuses_a_config_with_no_pooled_lightgbm_template(tmp_path):
    bad = tmp_path / "run-final-hpo.json"
    bad.write_text('{"stages": {"finalist": {"params": {"templates": []}}}}')
    with pytest.raises(ValueError, match="exactly one"):
        hpo_space(config_path=str(bad))


def test_hpo_space_refuses_a_missing_file(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        hpo_space(str(tmp_path / "nope.json"))


def test_hpo_space_refuses_duplicate_pooled_lightgbm_templates(tmp_path):
    # Matched by FAMILY, not id (ADR-0115) — two templates with different
    # ids but the same family still collide, which is exactly the point:
    # id is the finalist's candidate-name component, never a lookup key.
    import json

    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({"stages": {"finalist": {"params": {
        "templates": [
            {"id": "lean", "family": "pooled-lightgbm", "model": {"hpo_space": REAL_HPO_SPACE}},
            {"id": "other", "family": "pooled-lightgbm", "model": {"hpo_space": REAL_HPO_SPACE}},
        ]
    }}}}))
    with pytest.raises(ValueError, match="exactly one"):
        hpo_space(str(dup))


def test_hpo_space_refuses_the_wrong_family(tmp_path):
    import json

    bad = tmp_path / "wrong_family.json"
    bad.write_text(json.dumps({"stages": {"finalist": {"params": {
        "templates": [
            {"id": "lean", "family": "wrong-family", "model": {"hpo_space": REAL_HPO_SPACE}},
        ]
    }}}}))
    with pytest.raises(ValueError, match="pooled-lightgbm"):
        hpo_space(str(bad))


def test_hpo_space_refuses_a_missing_or_malformed_grid(tmp_path):
    import json

    for bad_space in (None, {}, {"learning_rate": []}, {"learning_rate": "not-a-list"}):
        template = {"id": "lean", "family": "pooled-lightgbm", "model": {}}
        if bad_space is not None:
            template["model"]["hpo_space"] = bad_space
        bad = tmp_path / "malformed.json"
        bad.write_text(json.dumps({"stages": {"finalist": {"params": {
            "templates": [template]
        }}}}))
        with pytest.raises(ValueError, match="non-empty mapping"):
            hpo_space(str(bad))


def test_build_candidate_inventory_is_frozen_deterministic_and_ordered():
    a = build_candidate_inventory()
    b = build_candidate_inventory()
    assert len(a.combinations) == 24
    assert a.digest == b.digest  # byte-identical: every lead sees the same
    assert a.combinations == b.combinations
    combos = [dict(c) for c in a.combinations]
    assert len(combos) == len({tuple(sorted(c.items())) for c in combos})
    for combo in combos:
        assert set(combo) == set(REAL_HPO_SPACE)


def test_build_candidate_inventory_different_seed_can_differ():
    a = build_candidate_inventory(seed=0)
    b = build_candidate_inventory(seed=1)
    assert a.digest != b.digest


# ---------------------------------------------------------------------------
# The P16 lean mask — read, not hardcoded a second time
# ---------------------------------------------------------------------------


def test_lean_feature_drop_matches_the_real_p16_config():
    drop = lean_feature_drop()
    assert len(drop) == 33
    assert tuple(sorted(drop)) == tuple(sorted(REAL_LEAN_DROP))
    assert "symbol_code" not in drop  # the native categorical survives


def test_lean_feature_drop_refuses_a_missing_file(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        lean_feature_drop(str(tmp_path / "nope.json"))


def test_lean_feature_drop_refuses_a_malformed_template(tmp_path):
    import json

    bad = tmp_path / "mask.json"
    bad.write_text(json.dumps({"stages": {"materialize": {"params": {
        "templates": [{"id": "lean", "family": "wrong-family"}]
    }}}}))
    with pytest.raises(ValueError, match="does not match"):
        lean_feature_drop(str(bad))


def test_lean_feature_drop_refuses_zero_or_multiple_lean_templates(tmp_path):
    import json

    zero = tmp_path / "zero.json"
    zero.write_text(json.dumps({"stages": {"materialize": {"params": {
        "templates": [{"id": "full"}]
    }}}}))
    with pytest.raises(ValueError, match="exactly one"):
        lean_feature_drop(str(zero))


# ---------------------------------------------------------------------------
# Simplicity ordering + boundary flags
# ---------------------------------------------------------------------------


def test_simplicity_key_matches_the_adr_ruled_ordering():
    row = {"overrides": {
        "num_leaves": 8, "learning_rate": 0.01, "min_child_samples": 1000,
        "reg_lambda": 100.0, "reg_alpha": 0.1,
    }}
    assert simplicity_key(row) == (8, 0.01, -1000, -100.0, -0.1)


def test_simplicity_key_orders_fewer_leaves_first():
    simple = {"overrides": {
        "num_leaves": 4, "learning_rate": 0.03, "min_child_samples": 500,
        "reg_lambda": 10.0, "reg_alpha": 1.0,
    }}
    complex_ = {"overrides": {
        "num_leaves": 16, "learning_rate": 0.003, "min_child_samples": 4000,
        "reg_lambda": 1000.0, "reg_alpha": 0.0,
    }}
    assert simplicity_key(simple) < simplicity_key(complex_)


def test_boundary_flags_reports_min_and_max_edges():
    candidate = {
        "num_leaves": 4, "learning_rate": 0.01, "min_child_samples": 4000,
        "reg_lambda": 100.0, "reg_alpha": 0.1,
    }
    flags = boundary_flags(candidate)
    assert flags == {
        "num_leaves": True, "learning_rate": False,
        "min_child_samples": True, "reg_lambda": False, "reg_alpha": False,
    }


# ---------------------------------------------------------------------------
# The forecast-accuracy objective — squared-error improvement, never IC.
# A hand-calculated example where the two disagree (plan §6 Phase 1 item 3).
# ---------------------------------------------------------------------------


def _spearman(y, yhat):
    """Plain Spearman rank correlation — a tiny, independent helper used
    ONLY by this test to compute IC for the contrast; final_model.py
    itself never computes IC (ADR-0114 §2: "not Spearman IC")."""
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0] * len(values)
        for rank, i in enumerate(order):
            out[i] = rank
        return out

    ry, ryhat = ranks(y), ranks(yhat)
    n = len(y)
    d2 = sum((a - b) ** 2 for a, b in zip(ry, ryhat))
    return 1 - 6 * d2 / (n * (n**2 - 1))


def test_squared_error_improvement_and_ic_prefer_different_candidates():
    y = [1.0, 2.0, 3.0]
    mu = [2.0, 2.0, 2.0]  # the training-mean baseline

    # Candidate A: perfectly rank-correlated with y (IC = 1.0) but badly
    # SCALED — far worse than the baseline on every decision.
    candidate_a = [10.0, 20.0, 30.0]
    # Candidate B: imperfect rank order (IC = 0.5) but close in magnitude
    # — clearly beats the baseline on every decision.
    candidate_b = [2.1, 1.9, 2.8]

    ic_a = _spearman(y, candidate_a)
    ic_b = _spearman(y, candidate_b)
    assert ic_a == pytest.approx(1.0)
    assert ic_b == pytest.approx(0.5)
    assert ic_a > ic_b  # IC prefers A

    improvement_a = sum(
        squared_error_improvement(yi, yhat, m)
        for yi, yhat, m in zip(y, candidate_a, mu)
    )
    improvement_b = sum(
        squared_error_improvement(yi, yhat, m)
        for yi, yhat, m in zip(y, candidate_b, mu)
    )
    assert improvement_a == pytest.approx(-1132.0)
    assert improvement_b == pytest.approx(0.74)
    assert improvement_b > improvement_a  # squared-error improvement prefers B


def test_cluster_scores_by_day_groups_by_the_declared_cluster_unit():
    rows = [
        {"day": "2026-01-05", "y": 1.0, "yhat": 0.9, "mu": 0.0},
        {"day": "2026-01-05", "y": -1.0, "yhat": -0.8, "mu": 0.0},
        {"day": "2026-01-06", "y": 2.0, "yhat": 2.0, "mu": 0.0},
    ]
    grouped = cluster_scores_by_day(rows)
    assert set(grouped) == {"2026-01-05", "2026-01-06"}
    assert grouped["2026-01-05"] == pytest.approx([0.99, 0.96])
    assert grouped["2026-01-06"] == pytest.approx([4.0])


def test_cluster_scores_by_day_refuses_empty_rows_and_missing_fields():
    with pytest.raises(ValueError, match="non-empty"):
        cluster_scores_by_day([])
    with pytest.raises(ValueError, match="missing"):
        cluster_scores_by_day([{"day": "d", "y": 1.0, "yhat": 1.0}])


# ---------------------------------------------------------------------------
# run_lead_selection — one-SE rule, independence, ledger completeness,
# non-finite/tie handling reused (not re-derived) from kinds_search.
# ---------------------------------------------------------------------------

_TINY_SPACE = {
    "learning_rate": [0.01],
    "num_leaves": [4, 8, 16],
    "min_child_samples": [1000],
    "reg_lambda": [100.0],
    "reg_alpha": [0.1],
}

#: Hand-computed so the one-SE band is known: num_leaves=16 has the best
#: mean (1.2) with se=0.2, so its threshold is 1.0 and every candidate
#: (1.0, 1.1, 1.2) is eligible — the simplest, num_leaves=4, must win.
_TINY_SCORES = {
    4: {"d1": [0.9], "d2": [1.1]},
    8: {"d1": [1.0], "d2": [1.2]},
    16: {"d1": [1.0], "d2": [1.4]},
}


def _tiny_evaluate(scores):
    def evaluate(candidate):
        return {
            "cluster_scores": scores[candidate["num_leaves"]],
            "fit_seed": 0,
            "cuts": {"train_end": "2025-11-30"},
            "n_rows": 10,
            "train_val_gap": 0.01,
            "collapsed_prediction_variance": False,
        }

    return evaluate


def test_run_lead_selection_applies_the_one_standard_error_rule():
    inventory = CandidateInventory(_TINY_SPACE)
    ledger, selection = run_lead_selection(
        inventory, _tiny_evaluate(_TINY_SCORES), n_boot=500, seed=0
    )
    assert ledger.is_complete()
    assert selection.best_score == pytest.approx(1.2)
    assert selection.threshold == pytest.approx(1.0)
    assert selection.selected_candidate["num_leaves"] == 4


def test_run_lead_selection_is_independent_across_leads():
    inventory = CandidateInventory(_TINY_SPACE)
    lead_a_scores = _TINY_SCORES
    lead_b_scores = {
        4: {"d1": [0.1], "d2": [0.2]},
        8: {"d1": [0.1], "d2": [0.2]},
        16: {"d1": [5.0], "d2": [6.0]},  # runs away with it, no one-SE overlap
    }

    _, selection_a_first = run_lead_selection(
        inventory, _tiny_evaluate(lead_a_scores), n_boot=500, seed=0
    )
    # Run lead B (a totally different score assignment over the SAME
    # inventory) BETWEEN the two lead-A calls — a shared inventory object
    # must never leak state between independent per-lead ledgers.
    _, selection_b = run_lead_selection(
        inventory, _tiny_evaluate(lead_b_scores), n_boot=500, seed=0
    )
    _, selection_a_second = run_lead_selection(
        inventory, _tiny_evaluate(lead_a_scores), n_boot=500, seed=0
    )

    assert selection_a_first.digest == selection_a_second.digest
    assert selection_b.selected_candidate["num_leaves"] == 16
    assert selection_a_first.selected_candidate["num_leaves"] == 4
    assert selection_b.digest != selection_a_first.digest


def test_run_lead_selection_refuses_a_score_only_evaluate_output():
    inventory = CandidateInventory(_TINY_SPACE)

    def score_only(candidate):
        return {"cluster_scores": {"d1": [1.0], "d2": [1.0]}}

    with pytest.raises(ValueError, match="missing evidence field"):
        run_lead_selection(inventory, score_only, n_boot=200, seed=0)


def test_run_lead_selection_refuses_evaluate_without_cluster_scores():
    inventory = CandidateInventory(_TINY_SPACE)
    with pytest.raises(ValueError, match="cluster_scores"):
        run_lead_selection(inventory, lambda candidate: {}, n_boot=200, seed=0)


def test_run_lead_selection_refuses_a_non_exact_inventory_type():
    class NotReallyAnInventory(CandidateInventory):
        pass

    with pytest.raises(TypeError, match="CandidateInventory"):
        run_lead_selection(
            NotReallyAnInventory(_TINY_SPACE), lambda c: {}, n_boot=200, seed=0
        )


def test_run_lead_selection_ledger_records_every_declared_field():
    inventory = CandidateInventory(_TINY_SPACE)
    ledger, _ = run_lead_selection(
        inventory, _tiny_evaluate(_TINY_SCORES), n_boot=500, seed=0
    )
    assert ledger.evidence_fields == EVIDENCE_FIELDS
    assert len(ledger.rows) == len(inventory.combinations) == 3
    for row in ledger.rows:
        for field in EVIDENCE_FIELDS:
            assert field in row


def test_run_lead_selection_propagates_a_non_finite_score_refusal():
    inventory = CandidateInventory(_TINY_SPACE)

    def evaluate(candidate):
        # A single cluster: cluster_bootstrap_t itself refuses (< 2
        # clusters has no studentized variance estimate) — this module
        # must not swallow or re-word that refusal.
        return {
            "cluster_scores": {"only": [1.0]},
            "fit_seed": 0, "cuts": {}, "n_rows": 1,
            "train_val_gap": 0.0, "collapsed_prediction_variance": False,
        }

    with pytest.raises(ValueError, match="at least 2 clusters"):
        run_lead_selection(inventory, evaluate, n_boot=200, seed=0)


# ---------------------------------------------------------------------------
# Calendar — no read reaches 2026-03-01; the embargo session is excluded.
# ---------------------------------------------------------------------------


def test_permitted_for_refit_excludes_embargo_and_lockbox():
    assert permitted_for_refit(EMBARGO_START_MS - 1) is True  # 2025-11-30
    assert permitted_for_refit(EMBARGO_START_MS) is False  # 2025-12-01
    assert permitted_for_refit(EMBARGO_END_MS - 1) is False  # still 12-01
    assert permitted_for_refit(EMBARGO_END_MS) is True  # 2025-12-02
    assert permitted_for_refit(LOCKBOX_START_MS - 1) is True  # 2026-02-28
    assert permitted_for_refit(LOCKBOX_START_MS) is False  # 2026-03-01
    assert permitted_for_refit(LOCKBOX_START_MS + 86_400_000) is False


# ---------------------------------------------------------------------------
# refit_heads — exact ten heads, shared contract, no HPO, calendar refusal.
# ---------------------------------------------------------------------------

FEATURE_ORDER = ["f0", "f1", "vol_5m"]


def _rows_for(slope, n=6, start_ms=EMBARGO_END_MS):
    return [
        {
            "f0": 0.1 * i,
            "f1": 1.0 - 0.1 * i,
            "vol_5m": 0.5,
            "label": slope * 0.1 * i,
            "ts_ms": start_ms + i * 86_400_000,
        }
        for i in range(n)
    ]


def _all_heads_rows():
    return {head: _rows_for(1.0 + 0.01 * i) for i, head in enumerate(HEADS)}


def _all_heads_winners():
    return {head: {"alpha": 1e-6} for head in HEADS}


def test_refit_heads_requires_exactly_the_ten_declared_heads():
    pytest.importorskip("sklearn")
    rows = _all_heads_rows()
    winners = _all_heads_winners()

    missing = {h: r for h, r in rows.items() if h != "h01"}
    with pytest.raises(ValueError, match="h01"):
        refit_heads(
            missing, winners, feature_order=FEATURE_ORDER,
            lean_drop=[], estimator_path="sklearn.linear_model.Ridge",
        )

    extra = dict(rows, h11=rows["h01"])
    with pytest.raises(ValueError, match="h11"):
        refit_heads(
            extra, winners, feature_order=FEATURE_ORDER,
            lean_drop=[], estimator_path="sklearn.linear_model.Ridge",
        )


def test_refit_heads_fits_each_head_on_only_its_own_winner(monkeypatch):
    pytest.importorskip("sklearn")
    rows = _all_heads_rows()
    winners = dict(_all_heads_winners())
    winners["h01"] = {"alpha": 1e-6}
    winners["h02"] = {"alpha": 5.0}  # a very different winner

    estimators, identities = refit_heads(
        rows, winners, feature_order=FEATURE_ORDER, lean_drop=["vol_5m"],
        estimator_path="sklearn.linear_model.Ridge", seed=0,
    )
    assert set(estimators) == set(HEADS) == set(identities)
    # Different alpha -> different fitted coefficients on the same rows.
    assert list(estimators["h01"]._model.coef_) != list(estimators["h02"]._model.coef_)
    for head in HEADS:
        assert identities[head]["winner"] == winners[head]
        assert identities[head]["n_rows"] == len(rows[head])
        assert identities[head]["seed"] == 0


def test_refit_heads_shares_the_identical_feature_and_category_contract():
    pytest.importorskip("sklearn")
    rows = _all_heads_rows()
    winners = _all_heads_winners()
    estimators, _ = refit_heads(
        rows, winners, feature_order=FEATURE_ORDER, lean_drop=["f0"],
        estimator_path="sklearn.linear_model.Ridge",
    )
    for head in HEADS:
        # Every head masked the SAME column (the shared lean_drop), so
        # every head's surviving width is feature_order minus one.
        assert estimators[head]._n_columns == len(FEATURE_ORDER)
        assert len(estimators[head]._indices) == len(FEATURE_ORDER) - 1


def test_refit_heads_refuses_a_row_at_or_after_the_lockbox():
    pytest.importorskip("sklearn")
    rows = _all_heads_rows()
    rows["h01"] = rows["h01"] + [
        {"f0": 0.0, "f1": 0.0, "vol_5m": 0.5, "label": 0.0,
         "ts_ms": LOCKBOX_START_MS}
    ]
    with pytest.raises(ValueError, match="outside the permitted"):
        refit_heads(
            rows, _all_heads_winners(), feature_order=FEATURE_ORDER,
            lean_drop=[], estimator_path="sklearn.linear_model.Ridge",
        )


def test_refit_heads_refuses_an_embargo_session_row():
    pytest.importorskip("sklearn")
    rows = _all_heads_rows()
    rows["h03"] = rows["h03"] + [
        {"f0": 0.0, "f1": 0.0, "vol_5m": 0.5, "label": 0.0,
         "ts_ms": EMBARGO_START_MS}
    ]
    with pytest.raises(ValueError, match="outside the permitted"):
        refit_heads(
            rows, _all_heads_winners(), feature_order=FEATURE_ORDER,
            lean_drop=["vol_5m"], estimator_path="sklearn.linear_model.Ridge",
        )


def test_refit_heads_never_calls_into_the_search_machinery():
    """A refit that touches CandidateInventory/OneStandardErrorSelector at
    all is the bug the plan's own §6 Phase 2 item 6 exists to catch."""
    pytest.importorskip("sklearn")
    with patch(
        "dskit.pipeline.kinds_search.CandidateInventory.__init__",
        side_effect=AssertionError("refit_heads must never build an inventory"),
    ), patch(
        "dskit.pipeline.kinds_search.OneStandardErrorSelector.select",
        side_effect=AssertionError("refit_heads must never run a selection"),
    ):
        estimators, _ = refit_heads(
            _all_heads_rows(), _all_heads_winners(),
            feature_order=FEATURE_ORDER, lean_drop=["vol_5m"],
            estimator_path="sklearn.linear_model.Ridge",
        )
    assert set(estimators) == set(HEADS)


def test_refit_heads_output_round_trips_through_write_bundle(tmp_path):
    """The refit's output is exactly the shape write_bundle expects — the
    two deliverables (final_model.py, sklearn.py's bundle writer) meet
    here, end to end, synthetically."""
    pytest.importorskip("sklearn")
    from dskit.pipeline.libs.sklearn import load_bundle, write_bundle

    rows = _all_heads_rows()
    winners = _all_heads_winners()
    estimators, identities = refit_heads(
        rows, winners, feature_order=FEATURE_ORDER, lean_drop=["vol_5m"],
        estimator_path="sklearn.linear_model.Ridge", seed=0,
    )
    surviving = [name for name in FEATURE_ORDER if name != "vol_5m"]
    manifest = write_bundle(
        str(tmp_path / "final-model.joblib"),
        HEADS,
        estimators,
        head_params={
            # refit_heads wraps every head in ColumnSubsetEstimator (it
            # always enforces lean_drop) — the manifest must declare the
            # class actually persisted, not the estimator ColumnSubset
            # wraps, or load_bundle's isinstance check refuses it as a
            # relabelled model (exactly the tamper it exists to catch).
            head: {
                "estimator": "dskit.pipeline.libs.sklearn.ColumnSubsetEstimator",
                "estimator_params": dict(winners[head], random_state=0),
            }
            for head in HEADS
        },
        feature_order=FEATURE_ORDER,
        surviving_features={head: surviving for head in HEADS},
        categorical_encoding={},
        training_identities=identities,
        predict_fixture=[[0.1, 0.9, 0.5], [0.5, 0.5, 0.5]],
    )
    assert manifest["heads"] == list(HEADS)
    bundle = load_bundle(str(tmp_path / "final-model.joblib"))
    assert set(bundle.estimators) == set(HEADS)


# ---------------------------------------------------------------------------
# EQ-01 — the immutable completed-run / content-derived-row / ten-labelled-wire
# contract, proved end to end on a deterministic FIXTURE release.
#
# NOTHING here is a real final-model release. The fixture channel is
# structurally unable to be one: it may never claim the shipped
# configs/run-final-hpo.json identity, the production channel refuses
# outright, and every head's HASHED bundle training identity carries
# release_channel="fixture" plus deployment_eligible=false.
# ---------------------------------------------------------------------------

WIRE_FEATURES = ["f0", "f1", "vol_5m"]
WIRE_DROP = ["vol_5m"]
WIRE_PREDICT_FIXTURE = [[0.1, 0.9, 0.5], [0.4, 0.6, 0.5]]


def _configs_dir():
    import pathlib

    return pathlib.Path(final_model.__file__).resolve().parents[1] / "configs"


def _fixture_hpo_document():
    """A REAL loadable document that is deliberately NOT the shipped final-HPO one."""
    import json

    from dskit.pipeline.document import PipelineDocument

    raw = json.loads(
        (_configs_dir() / "run-final-hpo.json").read_text(encoding="utf-8")
    )
    raw["name"] = "fixture-final-hpo"
    template = [
        t for t in raw["stages"]["finalist"]["params"]["templates"]
        if t.get("family") == "pooled-lightgbm"
    ][0]
    estimator_params = template["model"]["estimator_params"]
    estimator_params["drop"] = list(WIRE_DROP)
    estimator_params["n_estimators"] = 3
    estimator_params["n_jobs"] = 1
    return PipelineDocument.from_obj(raw)


def _head_rows(head, slope, n=6, start_ms=EMBARGO_END_MS):
    return [
        {
            final_model.WIRE_LABEL_FIELD: head,
            "f0": 0.1 * i,
            "f1": 1.0 - 0.1 * i,
            "vol_5m": 0.5,
            "label": slope * (0.1 * i),
            "ts_ms": start_ms + i * 86_400_000,
        }
        for i in range(n)
    ]


def _wires():
    return {
        head: _head_rows(head, 1.0 + 0.25 * index)
        for index, head in enumerate(HEADS)
    }


def _head_evidence(head, index, document_obj):
    model = [
        t for t in document_obj["stages"]["finalist"]["params"]["templates"]
        if t.get("family") == "pooled-lightgbm"
    ][0]["model"]
    inventory = CandidateInventory(
        model["hpo_space"], n_trials=model["hpo_trials"], seed=model["hpo_seed"]
    )
    ledger = TrialLedger(inventory, evidence_fields=EVIDENCE_FIELDS)
    for position, candidate in enumerate(inventory.combinations):
        ledger.record(
            candidate, float(position) + 0.01 * index, se=0.0, diagnostics={},
            on_boundary={}, fit_seed=0, cuts={}, n_rows=6, train_val_gap=0.0,
            collapsed_prediction_variance=False,
        )
    selection = OneStandardErrorSelector(
        select="max", simplicity_key=simplicity_key
    ).select(ledger)
    return {
        "producer_key": f"scan_{head}",
        "feature_order": list(WIRE_FEATURES),
        "categorical_feature": [],
        "ledger": ledger.to_obj(),
        "selection": selection.to_obj(),
    }


def _attested_hpo_run(
    tmp_path, *, state="ran", node_document_hash=None, config_obj=None,
    recorded=None, carried=None, name="hpo-run",
):
    """Write a run directory the driver's own RunAttestation accepts."""
    import json

    run_dir = tmp_path / name
    (run_dir / "nodes").mkdir(parents=True)
    document = _fixture_hpo_document()
    obj = document.to_obj()
    (run_dir / "config.json").write_text(
        json.dumps(obj if config_obj is None else config_obj, indent=2, sort_keys=True)
    )
    (run_dir / "resolved.json").write_text(
        json.dumps({"document_hash": document.hash, "run_hash": "e" * 64})
    )
    (run_dir / "result.json").write_text(json.dumps({
        "name": obj["name"], "asof": "2026-02-28", "document_hash": document.hash,
        "run_hash": "e" * 64, "state": state, "exit_code": 0,
    }))
    manifests, carry = {}, {}
    for index, head in enumerate(HEADS):
        manifest = _json_artifact(run_dir, _head_evidence(head, index, obj))
        manifests[head] = manifest
        carry[f"scan_{head}"] = {
            "hpo_ledger": manifest if carried is None else carried(head, manifest)
        }
        (run_dir / "nodes" / f"{index + 1:02d}-scan_{head}.json").write_text(json.dumps({
            "node": f"scan_{head}", "uses": "x:Y", "role": "search", "status": "ok",
            "document_hash": node_document_hash or document.hash,
            "outputs": {
                "hpo_ledger": manifest if recorded is None else recorded(head, manifest)
            },
        }))
    (run_dir / "carry.json").write_text(json.dumps(carry))
    return run_dir, document, manifests


def _fixture_params(run_dir, document, manifests, wires, **overrides):
    from dskit.pipeline.driver import row_set_identity

    params = {
        "release_channel": final_model.FIXTURE_CHANNEL,
        "hpo_run_dir": str(run_dir),
        "hpo_document_sha256": document.hash,
        "hpo_evidence": dict(manifests),
        "feature_order": list(WIRE_FEATURES),
        "categorical_feature": [],
        "categorical_encoding": {},
        "predict_fixture": [list(row) for row in WIRE_PREDICT_FIXTURE],
        "refit_identity": {
            "source": {"kind": "synthetic-fixture", "sha256": "1" * 64},
            "cache": {"kind": "synthetic-fixture", "sha256": "2" * 64},
            "train_start_ms": EMBARGO_END_MS,
            "refit_end_ms": LOCKBOX_START_MS,
            "embargo_start_ms": EMBARGO_START_MS,
            "embargo_end_ms": EMBARGO_END_MS,
            "rows": {head: row_set_identity(rows) for head, rows in wires.items()},
        },
        "seed": 0,
    }
    params.update(overrides)
    return params


def _fixture_release(tmp_path, **run_kwargs):
    wires = _wires()
    run_dir, document, manifests = _attested_hpo_run(tmp_path, **run_kwargs)
    return wires, _fixture_params(run_dir, document, manifests, wires), run_dir


def _ctx(tmp_path, name="refit-run"):
    from dskit.pipeline.node import NodeContext

    return NodeContext(name="fixture-refit", asof="2026-02-28",
                       run_dir=str(tmp_path / name))


def _run_fixture(tmp_path, params, wires, ctx_name="refit-run"):
    return final_model.FinalRefit("refit", params).run(_ctx(tmp_path, ctx_name), wires)


# --- 1. the frozen-bundle replay proof -------------------------------------


def test_final_refit_executes_a_fixture_release_that_replays_to_identical_outputs(tmp_path):
    pytest.importorskip("lightgbm")
    from dskit.pipeline.libs.sklearn import load_bundle

    wires, params, _ = _fixture_release(tmp_path)
    out = _run_fixture(tmp_path, params, wires)

    assert set(out) == {"bundle_path", "manifest"}
    assert out["manifest"]["heads"] == list(HEADS)
    replayed = load_bundle(out["bundle_path"])
    assert replayed.manifest["predict_checksum"] == out["manifest"]["predict_checksum"]
    assert replayed.manifest["sha256"] == out["manifest"]["sha256"]
    assert set(replayed.estimators) == set(HEADS)


def test_final_refit_refuses_to_overwrite_an_already_written_release(tmp_path):
    pytest.importorskip("lightgbm")
    wires, params, _ = _fixture_release(tmp_path)
    _run_fixture(tmp_path, params, wires)
    with pytest.raises(ValueError, match="exists|overwrite"):
        _run_fixture(tmp_path, params, wires)


# --- 2. content-derived materialized-row identities -------------------------


def test_final_refit_row_identity_is_content_derived_not_positional(tmp_path):
    pytest.importorskip("lightgbm")
    wires, params, _ = _fixture_release(tmp_path)
    first = _run_fixture(tmp_path, params, wires, "one")

    reordered = {head: list(reversed(rows)) for head, rows in wires.items()}
    second = _run_fixture(tmp_path, params, reordered, "two")

    identities = [
        second["manifest"]["training_identities"][head]["rows_sha256"]
        for head in HEADS
    ]
    assert identities == [
        first["manifest"]["training_identities"][head]["rows_sha256"]
        for head in HEADS
    ]
    assert len(set(identities)) == len(HEADS)
    assert second["manifest"]["predict_checksum"] == first["manifest"]["predict_checksum"]


def test_final_refit_refuses_rows_whose_content_is_not_what_the_release_pins(tmp_path):
    pytest.importorskip("lightgbm")
    wires, params, _ = _fixture_release(tmp_path)
    mutated = {head: [dict(row) for row in rows] for head, rows in wires.items()}
    mutated["h04"][2]["label"] += 1e-9
    with pytest.raises(ValueError, match="h04.*identif|identif.*h04"):
        _run_fixture(tmp_path, params, mutated)


def test_final_refit_refuses_a_caller_supplied_row_identity_that_content_does_not_produce(tmp_path):
    """A pin is an expectation; the identity is always recomputed from content."""
    wires, params, _ = _fixture_release(tmp_path)
    params["refit_identity"]["rows"]["h07"] = "a" * 64
    with pytest.raises(ValueError, match="h07"):
        _run_fixture(tmp_path, params, wires)


def test_final_refit_refuses_two_heads_pinned_to_one_materialized_row_set(tmp_path):
    from dskit.pipeline.node import ConfigError

    wires, params, _ = _fixture_release(tmp_path)
    params["refit_identity"]["rows"]["h02"] = params["refit_identity"]["rows"]["h01"]
    with pytest.raises(ConfigError, match="same materialized row set"):
        final_model.FinalRefit("refit", params)


# --- 3. ten labelled input wires -------------------------------------------


def test_final_refit_refuses_a_swapped_or_mislabelled_wire(tmp_path):
    wires, params, _ = _fixture_release(tmp_path)
    swapped = dict(wires)
    swapped["h04"] = wires["h03"]
    with pytest.raises(ValueError, match="h04 is wired to rows labelled 'h03'"):
        _run_fixture(tmp_path, params, swapped)

    unlabelled = {head: [dict(row) for row in rows] for head, rows in wires.items()}
    for row in unlabelled["h09"]:
        row.pop(final_model.WIRE_LABEL_FIELD)
    with pytest.raises(ValueError, match="h09 is wired to rows labelled None"):
        _run_fixture(tmp_path, params, unlabelled)


def test_final_refit_refuses_a_duplicated_wire(tmp_path):
    """One producer wired to two ports: the second port refuses by label."""
    wires, params, _ = _fixture_release(tmp_path)
    duplicated = dict(wires)
    duplicated["h02"] = wires["h01"]
    with pytest.raises(ValueError, match="h02 is wired to rows labelled 'h01'"):
        _run_fixture(tmp_path, params, duplicated)


def test_final_refit_refuses_a_missing_extra_or_empty_wire(tmp_path):
    wires, params, _ = _fixture_release(tmp_path)
    node = final_model.FinalRefit("refit", params)

    missing = {head: rows for head, rows in wires.items() if head != "h05"}
    assert node.validate_inputs(missing) == [
        f"inputs must be keyed by exactly {list(HEADS)!r}"
    ]
    assert node.validate_inputs(dict(wires, h11=[])) == [
        f"inputs must be keyed by exactly {list(HEADS)!r}"
    ]
    assert node.validate_inputs(dict(wires, h05=[])) == [
        "h05 must be a non-empty list of labelled rows"
    ]
    assert node.validate_inputs(wires) == []


def test_final_refit_refuses_a_wire_row_outside_the_permitted_window(tmp_path):
    wires, params, _ = _fixture_release(tmp_path)
    node = final_model.FinalRefit("refit", params)

    lockbox = {head: [dict(row) for row in rows] for head, rows in wires.items()}
    lockbox["h10"][-1]["ts_ms"] = LOCKBOX_START_MS
    assert any("h10" in problem for problem in node.validate_inputs(lockbox))

    embargo = {head: [dict(row) for row in rows] for head, rows in wires.items()}
    embargo["h01"][0]["ts_ms"] = EMBARGO_START_MS
    assert any("h01" in problem for problem in node.validate_inputs(embargo))


def test_heads_is_the_only_authority_for_the_ten_wire_labels(tmp_path):
    from dskit.pipeline.node import ConfigError

    wires, params, _ = _fixture_release(tmp_path)
    for field in ("hpo_evidence", "refit_identity"):
        broken = {key: value for key, value in params.items()}
        if field == "hpo_evidence":
            broken[field] = {k: v for k, v in params[field].items() if k != "h06"}
        else:
            broken[field] = dict(params[field])
            broken[field]["rows"] = {
                k: v for k, v in params[field]["rows"].items() if k != "h06"
            }
        with pytest.raises(ConfigError, match="h10"):
            final_model.FinalRefit("refit", broken)
    assert "search" not in " ".join(final_model.FinalRefit._PARAMS)


# --- 4. immutable completed-run provenance ---------------------------------


def test_final_refit_refuses_an_incomplete_upstream_run(tmp_path):
    wires, params, _ = _fixture_release(tmp_path, state="error")
    with pytest.raises(ValueError, match="trustworthy run attestation"):
        _run_fixture(tmp_path, params, wires)


def test_final_refit_refuses_a_run_whose_node_records_bind_another_document(tmp_path):
    wires, params, _ = _fixture_release(tmp_path, node_document_hash="f" * 64)
    with pytest.raises(ValueError, match="producer record and carry"):
        _run_fixture(tmp_path, params, wires)


def test_final_refit_refuses_a_run_whose_config_was_substituted_after_binding(tmp_path):
    import json

    from dskit.pipeline.document import load_document as _load

    wires, params, run_dir = _fixture_release(tmp_path)
    shipped = _load(str(_configs_dir() / "run-final-hpo.json")).to_obj()
    (run_dir / "config.json").write_text(json.dumps(shipped, indent=2, sort_keys=True))
    with pytest.raises(ValueError, match="trustworthy run attestation"):
        _run_fixture(tmp_path, params, wires)


def test_final_refit_refuses_evidence_absent_from_the_producer_record(tmp_path):
    foreign = {
        "path": "artifacts/json/" + "9" * 64 + ".json", "sha256": "9" * 64,
        "bytes": 2, "media_type": "application/json",
    }
    wires, params, _ = _fixture_release(
        tmp_path, recorded=lambda head, manifest: foreign
    )
    with pytest.raises(ValueError, match="producer record and carry|not what"):
        _run_fixture(tmp_path, params, wires)


def test_final_refit_refuses_evidence_absent_from_the_completed_runs_carry(tmp_path):
    foreign = {
        "path": "artifacts/json/" + "8" * 64 + ".json", "sha256": "8" * 64,
        "bytes": 2, "media_type": "application/json",
    }
    wires, params, _ = _fixture_release(
        tmp_path, carried=lambda head, manifest: foreign
    )
    with pytest.raises(ValueError, match="producer record and carry"):
        _run_fixture(tmp_path, params, wires)


def test_final_refit_refuses_a_substituted_run_directory(tmp_path):
    """Binding is by content: another run's directory cannot serve the pins."""
    wires, params, _ = _fixture_release(tmp_path)
    other_dir, _, _ = _attested_hpo_run(tmp_path, name="other-run")
    import shutil

    shutil.rmtree(other_dir / "artifacts")
    params["hpo_run_dir"] = str(other_dir)
    with pytest.raises(ValueError, match="JSON artifact is missing|not what"):
        _run_fixture(tmp_path, params, wires)


def test_final_refit_refuses_a_run_mutated_after_it_was_bound(tmp_path):
    wires, params, run_dir = _fixture_release(tmp_path)
    artifact = run_dir / params["hpo_evidence"]["h01"]["path"]
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(ValueError, match="byte count|digest"):
        _run_fixture(tmp_path, params, wires)


# --- 5. a fixture release can never claim to be a production one -----------


def test_final_refit_refuses_the_production_channel_outright(tmp_path):
    from dskit.pipeline.node import ConfigError

    wires, params, _ = _fixture_release(tmp_path)
    params["release_channel"] = final_model.PRODUCTION_CHANNEL
    with pytest.raises(ConfigError, match="non-executable on the production channel"):
        final_model.FinalRefit("refit", params)

    node = object.__new__(final_model.FinalRefit)
    node.params = params
    with pytest.raises(ValueError, match="non-executable on the production channel"):
        node.run(_ctx(tmp_path), wires)


def test_a_fixture_release_may_not_claim_the_shipped_final_hpo_document(tmp_path):
    from dskit.pipeline.node import ConfigError

    wires, params, _ = _fixture_release(tmp_path)
    params["hpo_document_sha256"] = final_model.final_hpo_document_identity()
    with pytest.raises(ConfigError, match="may not claim the shipped final-HPO"):
        final_model.FinalRefit("refit", params)


def test_final_refit_requires_a_declared_release_channel(tmp_path):
    from dskit.pipeline.node import ConfigError

    wires, params, _ = _fixture_release(tmp_path)
    del params["release_channel"]
    with pytest.raises(ConfigError, match="release_channel"):
        final_model.FinalRefit("refit", params)


def test_the_fixture_channel_stamp_is_inside_the_bundles_content_hash(tmp_path):
    pytest.importorskip("lightgbm")
    import json

    from dskit.pipeline.libs.sklearn import load_bundle

    wires, params, _ = _fixture_release(tmp_path)
    out = _run_fixture(tmp_path, params, wires)
    for head in HEADS:
        identity = out["manifest"]["training_identities"][head]
        assert identity["release_channel"] == final_model.FIXTURE_CHANNEL
        assert identity["deployment_eligible"] is False

    manifest_path = out["bundle_path"] + ".json"
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    manifest["training_identities"]["h01"]["release_channel"] = (
        final_model.PRODUCTION_CHANNEL
    )
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    with pytest.raises(ValueError, match="content hash"):
        load_bundle(out["bundle_path"])


def test_no_release_channel_can_emit_a_production_stamped_bundle(tmp_path):
    """The whole channel vocabulary, transcribed here, never read from the module."""
    pytest.importorskip("lightgbm")
    from dskit.pipeline.node import ConfigError

    wires, params, _ = _fixture_release(tmp_path)
    stamps = set()
    attempted = LITERAL_RELEASE_CHANNELS + ("staging", "shadow", "Production")
    for index, channel in enumerate(attempted):
        attempt = dict(params, release_channel=channel)
        try:
            out = _run_fixture(tmp_path, attempt, wires, f"channel-{index}")
        except (ConfigError, ValueError):
            continue
        stamps |= {
            out["manifest"]["training_identities"][head]["release_channel"]
            for head in HEADS
        }
    assert stamps == {final_model.FIXTURE_CHANNEL}


# --- 6. the bound window and data identities reach every head --------------


def test_final_refit_copies_the_bound_window_and_data_identity_into_every_head(tmp_path):
    pytest.importorskip("lightgbm")
    wires, params, _ = _fixture_release(tmp_path)
    out = _run_fixture(tmp_path, params, wires)
    pinned = params["refit_identity"]
    for head in HEADS:
        identity = out["manifest"]["training_identities"][head]
        assert identity["source"] == pinned["source"]
        assert identity["cache"] == pinned["cache"]
        assert identity["train_start_ms"] == pinned["train_start_ms"]
        assert identity["refit_end_ms"] == LOCKBOX_START_MS
        assert identity["embargo_start_ms"] == EMBARGO_START_MS
        assert identity["embargo_end_ms"] == EMBARGO_END_MS
        assert identity["rows_sha256"] == pinned["rows"][head]
        assert identity["hpo_document_sha256"] == params["hpo_document_sha256"]
        assert identity["winner"]["num_leaves"] in (4, 8, 16)


def test_final_refit_gives_each_head_only_its_own_frozen_winner(tmp_path):
    pytest.importorskip("lightgbm")
    wires, params, _ = _fixture_release(tmp_path)
    out = _run_fixture(tmp_path, params, wires)
    identities = out["manifest"]["training_identities"]
    surviving = out["manifest"]["surviving_features"]
    assert all(surviving[head] == ["f0", "f1"] for head in HEADS)
    assert len({identities[head]["rows_sha256"] for head in HEADS}) == len(HEADS)
    assert all(
        identities[head]["winner"]["n_estimators"] == 3 for head in HEADS
    )
    # The manifest states each head's recipe twice; pin the agreement.
    assert all(
        out["manifest"]["head_params"][head]["estimator_params"]
        == identities[head]["winner"]
        for head in HEADS
    )


# ---------------------------------------------------------------------------
# 7. the gate is sealed against the seam that defeats it
#
# Round-1 review, Critical: `uses: "module:ClassName"` accepts ANY class, so
# every refusal below resolves to ordinary overridable Python. A subclass that
# replaced `_channel_problems` produced a bundle stamped `production` and
# `deployment_eligible: True` over entirely fabricated evidence, with no edit
# to dskit or to this module. `__init_subclass__` closes that at
# class-definition time. It does NOT close post-hoc `Cls.attr = ...`
# assignment, and nothing here is a root of trust — see ADR-0166.
# ---------------------------------------------------------------------------

#: Every name `FinalRefit` seals, transcribed independently here rather than
#: imported, so this suite asserts something the module cannot echo back
#: (root CLAUDE.md: "a validation suite must NOT read its expected vocabulary
#: from the thing it validates").
SEALED_NAMES = (
    "__getattr__",
    "__getattribute__",
    "__init__",
    "__init_subclass__",
    "__new__",
    "_FINAL_METHODS",
    "_PARAMS",
    "_attestation",
    "_channel",
    "_channel_problems",
    "_estimator_params",
    "_hpo_template",
    "_identity_problems",
    "_lean_drop",
    "_release_identity",
    "_row_identities",
    "__setattr__",
    "_run_pin_problems",
    "_schema_problems",
    "_verified_hpo_outputs",
    "_winner_from_evidence",
    "_winners",
    "_wire_problems",
    "artifact_dir",
    "outputs",
    "role",
    "run",
    "validate_inputs",
    "validate_params",
)

#: The two release channels, transcribed literally for the same reason.
LITERAL_RELEASE_CHANNELS = ("fixture", "production")


def test_release_channels_is_exactly_the_two_literal_names():
    assert final_model.RELEASE_CHANNELS == LITERAL_RELEASE_CHANNELS
    assert final_model.FIXTURE_CHANNEL == "fixture"
    assert final_model.PRODUCTION_CHANNEL == "production"


def test_the_sealed_list_is_exactly_the_contract_the_suite_names():
    assert tuple(sorted(final_model.FinalRefit._FINAL_METHODS)) == tuple(
        sorted(SEALED_NAMES)
    )


#: Everything the compiler and ``ABCMeta`` put in ``vars(FinalRefit)`` that
#: is not a member. Round-2 review proved an allowlist nothing pins is an
#: escape hatch: add an unsealed method AND its name here, and the suite
#: goes green. ``test_nothing_executable_can_hide_in_the_class_metadata_
#: allowlist`` pins the contents and refuses anything executable in them.
CLASS_METADATA_NAMES = frozenset({
    "__module__", "__qualname__", "__doc__", "__dict__", "__weakref__",
    "__abstractmethods__", "_abc_impl", "__slotnames__", "__firstlineno__",
    "__static_attributes__", "__annotations__", "__type_params__",
})


def test_every_name_final_refit_defines_is_sealed():
    """A method added later without sealing it fails here, not in review."""
    defined = set(vars(final_model.FinalRefit)) - CLASS_METADATA_NAMES
    unsealed = defined - set(final_model.FinalRefit._FINAL_METHODS)
    assert unsealed == set(), f"unsealed FinalRefit members: {sorted(unsealed)}"


@pytest.mark.parametrize("name", sorted(SEALED_NAMES))
def test_a_subclass_may_not_override_any_sealed_member(name):
    with pytest.raises(TypeError, match="may not override"):
        type("Sneaky", (final_model.FinalRefit,), {name: lambda *a, **k: []})


def test_the_round_one_production_stamp_exploit_is_refused_at_class_definition(tmp_path):
    """The reviewer's exact class, verbatim in shape."""
    with pytest.raises(TypeError, match="_channel_problems"):
        class Sneaky(final_model.FinalRefit):  # noqa: D106
            @classmethod
            def _channel_problems(cls, params):
                return []

    with pytest.raises(TypeError, match="_release_identity"):
        class Eligible(final_model.FinalRefit):  # noqa: D106
            def _release_identity(self, channel, rows_sha256):
                return {"release_channel": "production", "deployment_eligible": True}


def test_an_outside_release_channel_is_refused_at_construction_and_at_run(tmp_path):
    """The Major: a narrowed gate let "staging" through and nothing failed."""
    from dskit.pipeline.node import ConfigError

    wires, params, _ = _fixture_release(tmp_path)
    for channel in ("staging", "Fixture", "PRODUCTION", " fixture", "", None, 0):
        attempt = dict(params, release_channel=channel)
        with pytest.raises(ConfigError, match="release_channel must be one of"):
            final_model.FinalRefit("refit", attempt)
        node = object.__new__(final_model.FinalRefit)
        node.params = attempt
        with pytest.raises(ValueError, match="release_channel must be one of"):
            node.run(_ctx(tmp_path), wires)


# ---------------------------------------------------------------------------
# 8. the seal resolves through the MRO, and its limits are executable
#
# Round-2 review reached past a `name in cls.__dict__` seal four ways: a mixin
# earlier in the MRO, a `__getattribute__` hijack that `vars(cls)` can never
# surface, a real depth-2 chain the old test never built, and an `ignored`
# allowlist that could absorb a new unsealed method. The seal now resolves
# every sealed name THROUGH THE MRO. It is an accident-and-drift guard, not an
# authority boundary, and the limits below are pinned as tests so the claim
# cannot drift back — see ADR-0166's threat model.
# ---------------------------------------------------------------------------


def test_a_mixin_earlier_in_the_mro_is_refused():
    """Round-2 Critical 1: the override never appears in the new class's body."""
    class EvilMixin:
        @classmethod
        def _channel_problems(cls, params):
            return []

    with pytest.raises(TypeError, match="_channel_problems"):
        class Sneaky(EvilMixin, final_model.FinalRefit):
            pass


def test_a_getattribute_hijack_is_refused():
    """Round-2 Critical 2: inherited from object, so vars(cls) can never see it."""
    with pytest.raises(TypeError, match="__getattribute__"):
        class Hijack(final_model.FinalRefit):
            def __getattribute__(self, name):
                return object.__getattribute__(self, name)

    class HijackMixin:
        def __getattribute__(self, name):
            return object.__getattribute__(self, name)

    with pytest.raises(TypeError, match="__getattribute__"):
        class SneakyHijack(HijackMixin, final_model.FinalRefit):
            pass


def test_a_subclass_at_any_depth_is_refused():
    """Round-2 Major 3: the old test built a direct child and called it a grandchild."""
    class Mid(final_model.FinalRefit):
        pass

    with pytest.raises(TypeError, match="may not override run"):
        class Grandchild(Mid):
            def run(self, ctx, inputs):
                return {}

    class Mid2(Mid):
        pass

    with pytest.raises(TypeError, match="_channel_problems"):
        class GreatGrandchild(Mid2):
            @classmethod
            def _channel_problems(cls, params):
                return []


def test_a_mixin_is_refused_at_depth_too():
    class Mid(final_model.FinalRefit):
        pass

    class EvilMixin:
        def _release_identity(self, channel, rows_sha256):
            return {"release_channel": "production", "deployment_eligible": True}

    with pytest.raises(TypeError, match="_release_identity"):
        class Deep(EvilMixin, Mid):
            pass


def test_a_subclass_may_still_override_an_unsealed_node_hook():
    """The boundary, not only the no-op: an UNSEALED hook stays overridable."""
    class Mid(final_model.FinalRefit):
        pass

    class Leaf(Mid):
        pass

    assert issubclass(Leaf, final_model.FinalRefit)

    # `serving_effect` is a Node hook the release gate does not resolve
    # through, so sealing it would be over-reach. Adding it to
    # `_FINAL_METHODS` must break this test.
    class Extended(final_model.FinalRefit):
        @classmethod
        def serving_effect(cls, params, verified_run_evidence):
            return None

    assert Extended.serving_effect({}, None) is None
    assert "serving_effect" not in final_model.FinalRefit._FINAL_METHODS


def test_nothing_executable_can_hide_in_the_class_metadata_allowlist():
    """Round-2 Major 4: widening `ignored` turned a red suite green."""
    members = vars(final_model.FinalRefit)
    for name in CLASS_METADATA_NAMES:
        raw = members.get(name)
        # CALLABILITY, not a type list: a `__call__`-bearing instance under a
        # dunder-shaped name passed the isinstance check (round-4 review).
        assert not callable(raw) and not isinstance(
            raw, (classmethod, staticmethod, property)
        ), f"{name!r} is an executable member hiding in the metadata allowlist"
    # Pinned exactly: extending the allowlist is itself a failure.
    assert len(CLASS_METADATA_NAMES) == 12
    assert all(
        (name.startswith("__") and name.endswith("__")) or name == "_abc_impl"
        for name in CLASS_METADATA_NAMES
    )


def test_the_seal_discloses_the_limits_it_does_not_cover():
    """One owner for the limits, and the class docstring may not contradict it."""
    disclosure = " ".join(
        final_model.FinalRefit.__init_subclass__.__doc__.split()
    )
    for phrase in (
        "not an authority boundary",
        "does not call ``super()``",
        "post-hoc",
        "metaclass",
        "per-instance",
        "no repository file is edited",
        # Round-4 rewrote the post-hoc bullet as a subclass-only illustration
        # and dropped the direct shape round 3 had named — the shape the
        # SHIPPED config actually wires. Both are pinned here now.
        "``FinalRefit.<name> = ...``",
        "no subclass is needed",
        "intercept attribute access on the class itself",
    ):
        assert phrase in disclosure, phrase

    contract = " ".join(final_model.FinalRefit.__doc__.split())
    assert "not an authority boundary" in contract
    assert "cost nothing" in contract
    # Round 3 stated this and round 4's rewrite dropped it; it is true and it
    # is the half of the accounting a reader needs beside the limits.
    assert "for the shipped configuration" in contract
    # Round-3 wrote these in the class docstring and round-4 review disproved
    # them: a post-hoc override on a SUBCLASS edits no repository file, so it
    # is not "an edit to trusted source" and not "a different threat class".
    assert "forces an edit to trusted source" not in contract
    assert "different threat class" not in contract


def test_an_intermediate_deriving_from_final_refit_may_not_swallow_the_seal():
    """A swallowing intermediate that IS a subclass is refused at its own definition."""
    with pytest.raises(TypeError, match="__init_subclass__"):
        class Swallow(final_model.FinalRefit):
            def __init_subclass__(cls, **kwargs):
                pass


def test_a_swallowing_mixin_is_an_uncovered_path_the_docstring_discloses():
    """This bypass WORKS, costs nothing, and is pinned so the disclosure cannot drift.

    A plain mixin is not a subclass, so sealing ``__init_subclass__`` cannot
    reach it: when the mixin sits earlier in the MRO and does not call
    ``super()``, ``FinalRefit.__init_subclass__`` never runs at all. No
    repository file is edited — ``uses:`` supplies the class. The test asserts
    the bypass AND that the docstring admits it; nothing here runs a release.
    """
    class SwallowMixin:
        def __init_subclass__(cls, **kwargs):
            pass

    class Evil(SwallowMixin, final_model.FinalRefit):
        @classmethod
        def _channel_problems(cls, params):
            return []

    assert Evil._channel_problems({}) == []          # the seal never ran
    # What the seal WOULD have reported had it run at all.
    assert final_model._sealed_violations(Evil) == ["__init_subclass__", "_channel_problems"]

    disclosure = " ".join(
        final_model.FinalRefit.__init_subclass__.__doc__.split()
    )
    assert "does not call ``super()``" in disclosure
    assert "no repository file is edited" in disclosure


def test_a_rigged_equality_object_cannot_pass_for_a_sealed_member():
    """The seal compares IDENTITY. Relaxing `is not` to `!=` admits this."""
    class FakeEqual:
        def __call__(self, *args, **kwargs):
            return {"release_channel": "production", "deployment_eligible": True}

        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    with pytest.raises(TypeError, match="_release_identity"):
        type(
            "Sneaky",
            (final_model.FinalRefit,),
            {"_release_identity": FakeEqual()},
        )


def test_a_subclass_cannot_shrink_the_sealed_list_to_admit_an_override():
    """The seal reads FinalRefit's own list, never the subclass's.

    Reading `subclass._FINAL_METHODS` would let an empty tuple in the
    subclass body silence every name at once. The refusal must name the
    override, not merely the shrink.
    """
    with pytest.raises(TypeError, match="_channel_problems"):
        type(
            "Sneaky",
            (final_model.FinalRefit,),
            {
                "_FINAL_METHODS": (),
                "_channel_problems": classmethod(lambda cls, params: []),
            },
        )


def test_post_hoc_assignment_on_a_subclass_is_an_uncovered_path(tmp_path):
    """This bypass WORKS and costs nothing; pinned so the disclosure stays true.

    `class Sneaky(FinalRefit): pass` passes the seal with an empty body, and
    assigning to a sealed name afterwards is never seen. No repository file is
    edited — `uses:` supplies the subclass. Nothing here runs a release.
    """
    class Sneaky(final_model.FinalRefit):
        pass

    assert final_model._sealed_violations(Sneaky) == []
    Sneaky._channel_problems = classmethod(lambda cls, params: [])
    assert Sneaky._channel_problems({}) == []

    contract = " ".join(final_model.FinalRefit.__doc__.split())
    assert "cost nothing" in contract


@contextlib.contextmanager
def _restored(owner, name):
    """Put ``owner.name`` back even if the body raises — it is shared module state."""
    missing = object()
    original = vars(owner).get(name, missing)
    try:
        yield
    finally:
        if original is missing:
            delattr(owner, name)
        else:
            setattr(owner, name, original)


def test_post_hoc_assignment_directly_on_final_refit_is_an_uncovered_path():
    """This bypass WORKS, needs no subclass, and is the SHIPPED wiring's shape.

    ``configs/run-final-refit.json`` declares
    ``uses: "intraday_equities.final_model:FinalRefit"`` — the class itself,
    not a subclass — and the document's ``uses:`` resolution is an import
    plus a ``getattr`` with no re-check of the resolved class's members. So
    assigning onto ``FinalRefit`` directly never reaches
    ``__init_subclass__`` at all. Round-3's docstring named this shape;
    round-4's rewrite replaced it with a subclass-only illustration, which
    is strictly narrower. Pinned here so the enumeration cannot lose it
    again. Nothing here executes a release.
    """
    original = vars(final_model.FinalRefit)["_release_identity"]
    payload = {"release_channel": "production", "deployment_eligible": True}

    with _restored(final_model.FinalRefit, "_release_identity"):
        final_model.FinalRefit._release_identity = classmethod(
            lambda cls, channel, rows_sha256: dict(payload)
        )
        assert final_model.FinalRefit._release_identity("production", "x") == payload

    # The shared class is exactly as it was, by identity — the seal compares
    # identity, so a merely-equal restore would not be a restore.
    assert vars(final_model.FinalRefit)["_release_identity"] is original

    disclosure = " ".join(
        final_model.FinalRefit.__init_subclass__.__doc__.split()
    )
    assert "``FinalRefit.<name> = ...``" in disclosure
    assert "no subclass is needed" in disclosure


def test_the_shipped_config_wires_the_class_itself_not_a_subclass():
    """Why the direct shape is the one that matters, asserted rather than assumed."""
    import json
    import pathlib

    raw = json.loads(
        (_configs_dir() / "run-final-refit.json").read_text(encoding="utf-8")
    )
    assert raw["pipeline"]["refit"]["uses"] == (
        "intraday_equities.final_model:FinalRefit"
    )
    assert pathlib.Path(final_model.__file__).name == "final_model.py"

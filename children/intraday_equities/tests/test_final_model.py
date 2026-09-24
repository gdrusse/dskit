"""``final_model.py`` — domain assembly for the ten-lead final release
(ADR-0114 Phase 2, reconciling ADR-0113's Phase 1).

Everything here is SYNTHETIC: tiny in-memory rows and a canned
``evaluate`` callback stand in for a real per-candidate LightGBM fit. No
market data, no real LightGBM fit against real history, no real P16
artifact load, and no pipeline `run`/`walkforward` execution — this
whole gate is synthetic-tests-only (the master plan's own ruling).
"""

from __future__ import annotations

import ast
import collections
import contextlib
import hashlib
import inspect
import json
import types

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
    import numpy as np
    from sklearn.linear_model import Ridge

    rows = _all_heads_rows()
    winners = dict(_all_heads_winners())
    winners["h01"] = {"alpha": 1e-6}
    winners["h02"] = {"alpha": 5.0}  # a very different winner

    estimators, identities = refit_heads(
        rows, winners, feature_order=FEATURE_ORDER, lean_drop=["vol_5m"],
        estimator_path="sklearn.linear_model.Ridge", seed=0,
    )
    assert set(estimators) == set(HEADS) == set(identities)

    def reference_model(head):
        """A Ridge with THIS head's own winner, on THIS head's own rows.

        ``estimators[head]._model.coef_ != estimators[other]._model.coef_``
        proved only that the two heads differ (sensitivity), not that each head
        got its OWN winner: a winner-swap fits h01 with h02's alpha and the
        inequality still holds while ``identities`` — built from the same input
        dict — still reports the intended winner. A reference fit pins the
        VALUE, so a swapped winner is contradicted outright.
        """
        matrix = np.array(
            [[row[name] for name in FEATURE_ORDER] for row in rows[head]],
            dtype=float,
        )
        targets = np.array([row["label"] for row in rows[head]], dtype=float)
        keep = [i for i, name in enumerate(FEATURE_ORDER) if name != "vol_5m"]
        return Ridge(alpha=winners[head]["alpha"], random_state=0).fit(
            matrix[:, keep], targets
        )

    for head in HEADS:
        reference = reference_model(head)
        assert list(estimators[head]._model.coef_) == list(reference.coef_)
        # Pin the RECIPE too, not just the result: a coefficient-invisible
        # substitution (e.g. solver="cholesky") fits the same numbers on a
        # 6-row set while the identity stamp still records the input winner.
        # The estimator's own parameters must equal the reference's exactly.
        assert estimators[head]._model.get_params() == reference.get_params()
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
    recorded=None, carried=None, name="hpo-run", document=None, evidence=None,
):
    """Write a run directory the driver's own RunAttestation accepts."""
    import json

    run_dir = tmp_path / name
    (run_dir / "nodes").mkdir(parents=True)
    document = document or _fixture_hpo_document()
    obj = _fixture_hpo_document().to_obj()
    (run_dir / "config.json").write_text(
        json.dumps(document.to_obj() if config_obj is None else config_obj, indent=2, sort_keys=True)
    )
    (run_dir / "resolved.json").write_text(
        json.dumps({"document_hash": document.hash, "run_hash": "e" * 64})
    )
    (run_dir / "result.json").write_text(json.dumps({
        "name": document.name, "asof": "2026-02-28", "document_hash": document.hash,
        "run_hash": "e" * 64, "state": state, "exit_code": 0,
    }))
    manifests, carry = {}, {}
    for index, head in enumerate(HEADS):
        manifest = _json_artifact(run_dir, (evidence or _head_evidence)(head, index, obj))
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
    with pytest.raises(final_model.SealRefused, match="may not override"):
        type("Sneaky", (final_model.FinalRefit,), {name: lambda *a, **k: []})


def test_a_metaclass_supplying_a_sealed_name_refuses_with_the_right_verdict():
    """The metaclass refusal message carries its own polarity, like the violation one.

    `test_a_subclass_may_not_override_any_sealed_member` pins the "may not
    override" verdict behaviourally; the metaclass verdict ("may not take a
    metaclass supplying …") was pinned only by the prose digest — a change
    detector that cannot see a polarity inversion. Asserting the phrase here
    means inverting "may not" to "may accept" fails this test, not just the
    digest (round-13 review).
    """
    class Answer:
        """A data descriptor: __set__ makes it win class-level lookup outright."""

        def __get__(self, obj, owner=None):
            return lambda params: []

        def __set__(self, obj, value):
            raise AttributeError

    class Shadowing(type(final_model.FinalRefit)):
        validate_params = Answer()

    with pytest.raises(
        final_model.SealRefused, match="may not take a metaclass supplying"
    ):
        Shadowing("Attempt", (final_model.FinalRefit,), {})


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

    with pytest.raises(final_model.SealRefused, match="may not override run"):
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


def test_the_seal_is_declared_as_an_accident_guard_not_a_boundary():
    """The one claim that is not per-attempt, kept because it frames the rest."""
    disclosure = " ".join(
        final_model.FinalRefit.__init_subclass__.__doc__.split()
    )
    assert "not an authority boundary" in disclosure
    # Round-3's claims, disproved in round 4: a post-hoc override edits no
    # repository file, so it is neither of these.
    contract = " ".join(final_model.FinalRefit.__doc__.split())
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


def test_post_hoc_assignment_on_a_subclass_passes_the_DEFINITION_time_check(tmp_path):
    """The definition-time half of the story, which is all this test ever pinned.

    Round-8 review, Minor: this docstring said the bypass "WORKS", written when
    the seal fired only at class definition. Since round 8 the USE-TIME check
    catches it, and ``GATE_FACTS`` declares ``reach=()`` for this attempt — so
    the old wording contradicted the module's own executed declaration. What
    remains true, and is what this pins, is the narrow definition-time fact:

    `class Sneaky(FinalRefit): pass` passes the seal with an empty body, and
    assigning to a sealed name afterwards is never seen BY THAT CHECK. No
    repository file is
    edited — `uses:` supplies the subclass. Nothing here runs a release.
    """
    class Sneaky(final_model.FinalRefit):
        pass

    assert final_model._sealed_violations(Sneaky) == []
    Sneaky._channel_problems = classmethod(lambda cls, params: [])
    assert Sneaky._channel_problems({}) == []



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



def test_the_shipped_config_wires_the_class_itself_not_a_subclass():
    """Why the direct shape is the one that matters, asserted rather than assumed."""
    import json

    raw = json.loads(
        (_configs_dir() / "run-final-refit.json").read_text(encoding="utf-8")
    )
    module, _, attribute = raw["pipeline"]["refit"]["uses"].partition(":")
    assert module == final_model.__name__
    assert getattr(final_model, attribute) is final_model.FinalRefit


# ---------------------------------------------------------------------------
# 9. every disclosed fact is EXECUTED, not grepped
#
# Round-5 review inverted three sentences in final_model.py — "a custom
# metaclass CANNOT ...", "It fails OPEN, not closed" — keeping the substrings
# the old disclosure test checked, and the whole file stayed green. A
# substring cannot see polarity. So the polarity now lives in
# `final_model.GATE_FACTS` as a boolean, and every entry has a probe here that
# performs the attempt for real. The probes are written against the gate's
# behaviour, never against GATE_FACTS' own summaries.
# ---------------------------------------------------------------------------

PRODUCTION_PARAMS = {
    "release_channel": "production",
    "hpo_document_sha256": "d" * 64,
}
STAMP = {"release_channel": "production", "deployment_eligible": True}

#: What FinalRefit itself reports for a production-channel release, captured
#: at import BEFORE any probe patches anything. An attempt has "reached"
#: ``validate_params`` when none of these survive there. Derived from the
#: class rather than typed as a literal, so rewording a refusal moves the
#: marker with it instead of silently turning every probe's answer into
#: "reached" — the control below asserts the marker is non-empty and that the
#: unattacked class reaches nothing.
_BASELINE_CHANNEL_PROBLEMS = tuple(
    final_model.FinalRefit._channel_problems(PRODUCTION_PARAMS)
)


def _release_gate_problems(cls):
    """What `validate_params` contributes as RELEASE gates, for this class.

    Two now, not one: the production-channel refusal and the use-time seal
    re-check. A measurement that watched only the first would report
    "validate_params reached" for a class the seal stops there — which is the
    wrong answer in the safe direction, and still the wrong answer.
    """
    return list(cls._channel_problems(dict(PRODUCTION_PARAMS))) + list(
        final_model._unsealed_problems(cls)
    )


def _reaches(cls, instance=None):
    """Which RELEASE_ENTRY_POINTS a production release can enter under this attempt."""
    reached = []
    gates = _release_gate_problems(cls)
    problems = cls.validate_params(dict(PRODUCTION_PARAMS))
    # A gate the class CAN produce that `validate_params` does not report is a
    # gate no entry point consults, so the attempt has reached the entry point
    # — the same answer as producing no gate at all. This was an ASSERT until
    # round 11, which crashed instead of measuring on the one attempt that
    # replaces `validate_params` outright, and would have kept that attempt out
    # of the table. The suite-drift it guarded against is caught better by
    # `test_the_reach_measurement_answers_nothing_for_the_unattacked_class`:
    # a `validate_params` that stopped calling a gate makes the PRISTINE class
    # measure as reaching, and that control fails.
    if not gates or any(gate not in problems for gate in gates):
        reached.append("validate_params")
    node = instance
    if node is None:
        node = object.__new__(cls)
        node.params = dict(PRODUCTION_PARAMS)
    # `run` ITSELF is called, with inputs it must refuse anyway. Anything
    # narrower is a re-implementation of run's gate sequence, and round-7
    # review killed two such: this line once hardcoded
    # `FinalRefit._channel(node)`, so an attempt that substituted `_channel`
    # itself measured as reaching nothing; and a later version asked
    # `_unsealed_problems` directly, so DELETING that check from `run` changed
    # no measurement at all. The only faithful question is what `run` does.
    #
    # `run` refuses in order: the seal re-check, then the channel, then the
    # wires. So the attempt has "reached run" when the refusal it gets is
    # NEITHER release gate — the wires refusal means both gates are behind it.
    # The markers `run`'s two gates produce. The channel one comes from the
    # PRISTINE class, captured at import: derived from the ATTACKED class it is
    # whatever the attack says it is, and the metaclass-interception row
    # briefly measured as reaching `run` for exactly that reason — the
    # interception emptied the expected text, so no refusal matched it.
    markers = list(_BASELINE_CHANNEL_PROBLEMS) + list(
        final_model._unsealed_problems(type(node))
    )
    try:
        node.run(None, {})
    except ValueError as exc:
        blocked = any(marker in str(exc) for marker in markers)
    except Exception:                       # noqa: BLE001 — past the gates
        blocked = False
    else:
        blocked = False
    if not blocked:
        reached.append("run")
    return tuple(reached)


def _define(namespace, bases=None, metaclass=None):
    """(the class or None, whether THE SEAL refused it) for this class definition.

    A probe that "passes" because Python rejected the construction for its
    own reasons is exactly the fake evidence this audit keeps finding, so a
    TypeError that is not the seal's is re-raised rather than counted.

    The two are told apart BY TYPE, never by the message. Matching a substring
    made every probe's answer depend on the wording of a refusal: reword it and
    each attempt silently became "Python refused it", with the whole table
    still green (round-10 review, 2026-09-19). ``SealRefused`` is raised at one
    site and by nothing else, so there is no phrase to keep in step.
    """
    builder = metaclass or type
    try:
        cls = builder("Attempt", bases or (final_model.FinalRefit,), dict(namespace))
    except final_model.SealRefused:
        return None, True
    except TypeError as exc:
        raise AssertionError(f"refused by Python, not by the seal: {exc}") from exc
    return cls, False


def _outcome(namespace, bases=None, metaclass=None):
    """(refused by the seal, entry points reached) for one class-definition attempt."""
    cls, refused = _define(namespace, bases, metaclass)
    return refused, () if refused else _reaches(cls)


def _probe_override_in_body():
    return _outcome({"run": lambda self, ctx, inputs: {}})


def _probe_override_from_a_mixin():
    class EvilMixin:
        @classmethod
        def _channel_problems(cls, params):
            return []

    return _outcome({}, (EvilMixin, final_model.FinalRefit))


def _probe_override_at_depth():
    class Mid(final_model.FinalRefit):
        pass

    class Mid2(Mid):
        pass

    return _outcome({"_release_identity": lambda *a: STAMP}, (Mid2,))


def _probe_override_of_an_inherited_hook():
    return _outcome(
        {"__getattribute__": lambda self, name: object.__getattribute__(self, name)}
    )


def _probe_rigged_equality():
    class FakeEqual:
        def __call__(self, *args, **kwargs):
            return dict(STAMP)

        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    forged = FakeEqual()
    # Control: the rigging is live — this object compares equal to anything, so
    # an `==`/`!=` seal would clear it. Deleting __eq__ above makes this control
    # fail and the row would stop guarding the `is not` identity rule (round-13).
    assert forged == object(), "the rigged __eq__ is not live"

    return _outcome({"_release_identity": forged})


def _probe_shrunk_sealed_list():
    return _outcome(
        {"_FINAL_METHODS": (), "_channel_problems": classmethod(lambda cls, p: [])}
    )


def _probe_replaced_init_subclass():
    return _outcome({"__init_subclass__": classmethod(lambda cls, **kw: None)})


def _probe_metaclass_shadows_the_class_dict():
    """A metaclass supplying __dict__ hides a live override from getattr_static."""
    class Shadowing(type(final_model.FinalRefit)):
        __dict__ = property(lambda cls: {})

    # Control: the shadow is the round-7 shape — getattr_static reads THROUGH
    # the shadowed entry and reports FinalRefit's own member while the seal,
    # which reads type's real __dict__ slot, still sees the live override.
    # Deleting `__dict__ = property(...)` above makes the two readings agree
    # and this control fails, so the shadow is load-bearing rather than
    # decorative (round-13 review).
    import inspect

    carrier = Shadowing("Carrier", (final_model.FinalRefit,), {})
    carrier._channel_problems = classmethod(lambda cls, params: [])
    theirs = inspect.getattr_static(carrier, "_channel_problems")
    mine = final_model._resolved_through_the_mro(carrier, "_channel_problems")
    assert mine is not theirs, "getattr_static no longer skips; this row is stale"

    # Control: the metaclass ALONE must be accepted, so a refusal below is
    # attributable to the override and not to the metaclass.
    innocent, refused_innocent = _define({}, metaclass=Shadowing)
    assert refused_innocent is False and innocent is not None
    return _outcome(
        {"_channel_problems": classmethod(lambda cls, p: [])}, metaclass=Shadowing
    )


def _probe_metaclass_doctors_the_mro():
    class Hidden:
        @classmethod
        def _channel_problems(cls, params):
            return []

    class Doctoring(type(final_model.FinalRefit)):
        def mro(cls):
            return [
                entry
                for entry in type(final_model.FinalRefit).mro(cls)
                if entry is not Hidden
            ]

    return _outcome({}, (Hidden, final_model.FinalRefit), Doctoring)


def _probe_metaclass_rotates_the_class_out_of_its_own_mro():
    """The shape that skips __init_subclass__ ENTIRELY (round-7 review).

    `type.__new__` invokes the hook as `super(cls, cls).__init_subclass__`,
    which searches `cls.__mro__` AFTER `cls`. Rotate `cls` to the end and the
    search window no longer holds `FinalRefit`, so the hook never runs — no
    exception, no refusal. `_sealed_violations` still sees the override; only
    its timing was wrong, which is what the use-time re-check answers.
    """
    class Hidden:
        @classmethod
        def _channel_problems(cls, params):
            return []

    class Rotating(type(final_model.FinalRefit)):
        def mro(cls):
            order = list(type(final_model.FinalRefit).mro(cls))
            return order[1:] + order[:1]

    cls, refused = _define({}, (Hidden, final_model.FinalRefit), Rotating)
    assert refused is False and cls is not None
    # The definition-time hook did NOT fire, and the violation was there to see.
    assert final_model._sealed_violations(cls) == ["_channel_problems"]
    return False, _reaches(cls)


def _probe_metaclass_injects_the_guard_beside_its_payload():
    """Round-8 review: the guard was reachable through the class it guards.

    Round 8 closed three timing-evading mechanisms by re-taking the seal's
    verdict at use time — but it asked ``cls._unsealed_problems()``, resolved
    through the very class under suspicion. So the same injection could supply
    a compliant guard ALONGSIDE its real payload, and the class reported itself
    clean at both entry points. Asking a compromised class whether it is
    compromised is not a check. The guard is a module-level function now.
    """
    class Injecting(type(final_model.FinalRefit)):
        def __new__(mcls, name, bases, namespace, **kwargs):
            cls = super().__new__(mcls, name, bases, namespace, **kwargs)
            cls._unsealed_problems = classmethod(lambda c: [])
            cls._channel_problems = classmethod(lambda c, params: [])
            return cls

    cls, refused = _define({}, metaclass=Injecting)
    # Round-11 review: while the metaclass rule refused any sealed name at all,
    # `super().__new__` refused this class for DEFINING `__new__`, so the two
    # payload lines above never ran and this row tested nothing it names —
    # deleting them left it green. The rule now asks whether the supplied
    # object actually wins class-level lookup, a plain `__new__` does not, and
    # the payload reaches the class again.
    assert refused is False and cls is not None
    # The injected guard is INERT: `_unsealed_problems` is a module-level
    # function, so it is not a sealed member and nothing resolves it through
    # the class. Both halves are pinned, so deleting EITHER payload line fails
    # here. The first assertion proves the guard is really there AND compliant
    # — it resolves through the class and would report the class clean if the
    # gate ever consulted it. The second proves the gate does not: it is
    # module-level and still sees the payload the guard hides.
    assert "_unsealed_problems" not in final_model.FinalRefit._FINAL_METHODS
    assert cls._unsealed_problems() == [], "the injected guard was not applied"
    assert final_model._sealed_violations(cls) == ["_channel_problems"]
    assert final_model._unsealed_problems(cls), "the module-level guard was fooled"
    return False, _reaches(cls)


def _probe_substituted_channel_resolver():
    """`_channel` ITSELF substituted — the member `run` resolves, not the one it calls.

    Round-7 review: `_reaches` used to hardcode `FinalRefit._channel(node)`, so
    this attempt measured as reaching nothing while a real `run` would have
    taken the substitute. Every other row attacks `_channel_problems`, which
    the real `_channel` calls dynamically, so the hardcoding was invisible.
    """
    class Injecting(type(final_model.FinalRefit)):
        def __new__(mcls, name, bases, namespace, **kwargs):
            cls = super().__new__(mcls, name, bases, namespace, **kwargs)
            cls._channel = lambda self: "production"
            return cls

    cls, refused = _define({}, metaclass=Injecting)
    # Round-11 review, as above: the payload line never ran while a metaclass
    # defining `__new__` was refused outright, so this row stopped measuring
    # the substitution it names. It measures it again.
    assert refused is False and cls is not None
    assert final_model._sealed_violations(cls) == ["_channel"]
    return False, _reaches(cls)


def _probe_metaclass_injects_after_class_creation():
    class Injecting(type(final_model.FinalRefit)):
        def __new__(mcls, name, bases, namespace, **kwargs):
            cls = super().__new__(mcls, name, bases, namespace, **kwargs)
            cls._channel_problems = classmethod(lambda c, params: [])
            return cls

    cls, refused = _define({}, metaclass=Injecting)
    # Round-11 review: while the metaclass rule refused any sealed name at all,
    # `super().__new__` refused this class for DEFINING `__new__`, so the
    # payload line above never ran and this row tested nothing it names —
    # deleting it left the suite green. The payload must reach the class again,
    # or this assertion fails: the violation is what the payload leaves behind.
    assert refused is False and cls is not None
    assert final_model._sealed_violations(cls) == ["_channel_problems"]
    return False, _reaches(cls)


def _probe_unsealed_node_hook():
    # The row's claim is that the gate does NOT seal a Node hook it never
    # resolves through. Pin the premise so deleting the override does not
    # silently turn this into a clean-class row (round-13 review).
    assert "serving_effect" not in final_model.FinalRefit._FINAL_METHODS
    cls, refused = _define(
        {"serving_effect": classmethod(lambda cls, params, evidence: None)}
    )
    assert refused is False and cls is not None
    assert (
        cls.serving_effect.__func__
        is not final_model.FinalRefit.serving_effect.__func__
    )
    return False, _reaches(cls)


def _probe_swallowing_mixin():
    class SwallowMixin:
        def __init_subclass__(cls, **kwargs):
            pass

    class Evil(SwallowMixin, final_model.FinalRefit):
        @classmethod
        def _channel_problems(cls, params):
            return []

    # The mixin swallowed the hook, so the override was NOT refused at
    # definition time — but it is still there to see at use time. Pin both
    # halves so deleting the override does not leave a clean class (round-13).
    assert "_channel_problems" in final_model._sealed_violations(Evil)
    return False, _reaches(Evil)


def _probe_post_hoc_on_this_class():
    with _restored(final_model.FinalRefit, "_channel_problems"):
        final_model.FinalRefit._channel_problems = classmethod(lambda cls, p: [])
        return False, _reaches(final_model.FinalRefit)


def _probe_post_hoc_on_a_subclass():
    class Sneaky(final_model.FinalRefit):
        pass

    Sneaky._channel_problems = classmethod(lambda cls, p: [])
    # The assignment is the payload: pin that it landed, so deleting it does
    # not leave a clean class measuring the same `()` (round-13 review).
    assert final_model._sealed_violations(Sneaky) == ["_channel_problems"]
    return False, _reaches(Sneaky)


def _probe_per_instance_shadowing():
    instance = object.__new__(final_model.FinalRefit)
    instance.params = dict(PRODUCTION_PARAMS)
    instance._channel_problems = lambda params: []
    return False, _reaches(final_model.FinalRefit, instance=instance)


def _probe_metaclass_interception():
    class Intercepting(type(final_model.FinalRefit)):
        def __getattribute__(cls, name):
            if name == "_channel_problems":
                return lambda params: []
            return super().__getattribute__(name)

    # Control: the interception is REAL, not decorative. On a plain class the
    # metaclass is allowed to build, the body answers the sealed name — so
    # deleting the body above raises AttributeError here and the row fails.
    class Probe(metaclass=Intercepting):
        pass

    assert Probe._channel_problems(None) == []

    # The body is empty, so the MRO walk finds nothing; the metaclass rule is
    # what bites, and it bites on __getattribute__ being a sealed name at all.
    return _outcome({}, metaclass=Intercepting)


def _probe_metaclass_data_descriptor():
    class Answer:
        """A data descriptor: __set__ makes it win class-level lookup outright."""

        def __get__(self, obj, owner=None):
            return lambda params: []

        def __set__(self, obj, value):
            raise AttributeError

    class Shadowing(type(final_model.FinalRefit)):
        validate_params = Answer()

    return _outcome({}, metaclass=Shadowing)


def _probe_metaclass_delete_only_data_descriptor():
    """A data descriptor by __delete__ alone: __set__ is absent.

    The guard reads ``__set__ or __delete__`` because that is what the
    interpreter's ``type.__getattribute__`` reads, and a descriptor that
    defines only ``__delete__`` still wins class-level lookup. The probe above
    exercises the ``__set__`` half; this one exercises the ``__delete__`` half,
    so dropping either side of the ``or`` fails (round-12 review: the whole
    guard was driven, and only the ``__set__`` half was actually probed).
    """
    class Answer:
        """A data descriptor by __delete__ alone."""

        def __get__(self, obj, owner=None):
            return lambda params: []

        def __delete__(self, obj):
            raise AttributeError

    class Shadowing(type(final_model.FinalRefit)):
        validate_params = Answer()

    return _outcome({}, metaclass=Shadowing)


def _probe_metaclass_with_rigged_equality():
    """The rigged-equality attack one level up, on the METACLASS baseline.

    `_sealed_violations` compares members by identity because an object whose
    `__eq__` answers True passes for any of them. `_metaclass_supplied`
    compares METACLASSES against the baseline, and the same trick works there:
    a meta-metaclass whose `__eq__` returns True makes the attacking metaclass
    compare equal to `ABCMeta`, so a baseline test written with `==` skips it
    and the sealed name it carries goes unseen. Round-10 sweep: `is` -> `==`
    survived every other test in this file.
    """
    class RiggedEquality(type):
        def __eq__(cls, other):
            return True

        def __hash__(cls):
            return 0

    class Answer:
        def __get__(self, obj, owner=None):
            return lambda params: []

        def __set__(self, obj, value):
            raise AttributeError

    class Shadowing(type(final_model.FinalRefit), metaclass=RiggedEquality):
        validate_params = Answer()

    # The rigging is live: this is what an `==` baseline would have believed.
    # E721 is silenced because `==` between types IS the defect being shown.
    assert Shadowing == type(final_model.FinalRefit)   # noqa: E721
    return _outcome({}, metaclass=Shadowing)


def _probe_metaclass_supplying_a_class_whose_metaclass_makes_it_a_descriptor():
    """A sealed name bound to a CLASS that shadows because ITS metaclass says so.

    Round-11 sweep: reading the carrier as ``supplied if isinstance(supplied,
    type) else type(supplied)`` survived everything, because no probe ever
    bound a sealed name to a class. The two readings disagree exactly here —
    ``__set__`` lives on the payload's METACLASS, so the wrong reading looks
    for it in the payload's own MRO, does not find it, and clears a name that
    really does win class-level lookup.
    """
    class DescriptorMeta(type):
        def __get__(cls, obj, owner=None):
            return lambda params: []

        def __set__(cls, obj, value):
            raise AttributeError

    class Payload(metaclass=DescriptorMeta):
        pass

    class Shadowing(type(final_model.FinalRefit)):
        validate_params = Payload

    return _outcome({}, metaclass=Shadowing)


def _probe_metaclass_swapped_after_definition():
    """The metaclass rule's own post-hoc shape: ``cls.__class__ = EvilMeta``.

    Every other metaclass row is present when the class is built, so the
    definition-time hook sees it. This one is not: an empty-bodied subclass
    passes the hook, and the metaclass carrying the sealed name is assigned
    afterwards — the metaclass twin of
    ``refuses_post_hoc_assignment_on_a_subclass``. Declared because the
    round-10/11 rule would otherwise be pinned only in the shape the hook
    happens to catch.
    """
    class Answer:
        def __get__(self, obj, owner=None):
            return lambda params: []

        def __set__(self, obj, value):
            raise AttributeError

    class Later(type(final_model.FinalRefit)):
        validate_params = Answer()

    cls, refused = _define({})
    assert refused is False and cls is not None
    cls.__class__ = Later
    return False, _reaches(cls)


def _probe_shipped_config_refuses_to_plan():
    from dskit.pipeline.document import load_document
    from dskit.pipeline.node import ConfigError
    from dskit.pipeline.stages import plan_stages

    try:
        plan_stages(load_document(str(_configs_dir() / "run-final-refit.json")))
    except ConfigError:
        return True, ()
    return False, ()


#: One probe per declared fact, keyed by the same name. Each performs the
#: attempt and returns ``(refused by the seal, entry points reached)``.
GATE_PROBES = {
    "refuses_an_override_in_the_subclass_body": _probe_override_in_body,
    "refuses_an_override_from_a_mixin_in_the_mro": _probe_override_from_a_mixin,
    "refuses_an_override_at_any_subclass_depth": _probe_override_at_depth,
    "refuses_an_override_of_an_inherited_hook": _probe_override_of_an_inherited_hook,
    "refuses_an_object_whose_equality_is_rigged": _probe_rigged_equality,
    "refuses_a_subclass_that_shrinks_the_sealed_list": _probe_shrunk_sealed_list,
    "refuses_a_subclass_that_replaces_init_subclass": _probe_replaced_init_subclass,
    "refuses_a_metaclass_that_shadows_the_class_dict":
        _probe_metaclass_shadows_the_class_dict,
    "refuses_a_metaclass_that_doctors_the_mro": _probe_metaclass_doctors_the_mro,
    "refuses_a_metaclass_that_rotates_the_class_out_of_its_own_mro":
        _probe_metaclass_rotates_the_class_out_of_its_own_mro,
    "refuses_a_metaclass_that_injects_the_guard_beside_its_payload":
        _probe_metaclass_injects_the_guard_beside_its_payload,
    "refuses_a_substituted_channel_resolver": _probe_substituted_channel_resolver,
    "refuses_a_subclass_overriding_an_unsealed_node_hook": _probe_unsealed_node_hook,
    "refuses_a_mixin_whose_init_subclass_swallows_the_hook": _probe_swallowing_mixin,
    "refuses_post_hoc_assignment_on_this_class": _probe_post_hoc_on_this_class,
    "refuses_post_hoc_assignment_on_a_subclass": _probe_post_hoc_on_a_subclass,
    "refuses_a_metaclass_that_injects_after_class_creation":
        _probe_metaclass_injects_after_class_creation,
    "refuses_per_instance_shadowing": _probe_per_instance_shadowing,
    "refuses_a_metaclass_that_intercepts_class_attribute_access":
        _probe_metaclass_interception,
    "refuses_a_metaclass_that_supplies_a_sealed_name_as_a_data_descriptor":
        _probe_metaclass_data_descriptor,
    "refuses_a_metaclass_supplying_a_delete_only_data_descriptor":
        _probe_metaclass_delete_only_data_descriptor,
    "refuses_a_metaclass_whose_own_metaclass_rigs_equality":
        _probe_metaclass_with_rigged_equality,
    "refuses_a_metaclass_supplying_a_class_that_is_itself_a_data_descriptor":
        _probe_metaclass_supplying_a_class_whose_metaclass_makes_it_a_descriptor,
    "refuses_a_metaclass_swapped_in_after_the_class_is_defined":
        _probe_metaclass_swapped_after_definition,
    "shipped_configuration_refuses_to_plan": _probe_shipped_config_refuses_to_plan,
}


def test_a_sealed_name_the_class_does_not_carry_is_shadowable_by_anything():
    """The second clause of the shadowing rule, asserted where an attack cannot reach it.

    ``type.__getattribute__`` prefers a metaclass attribute whenever the
    class's own MRO carries the name NOWHERE — whatever kind of object it is,
    data descriptor or not. Round 11 shipped the data-descriptor clause alone
    and was right only by coincidence: ``__getattr__`` is the one sealed name
    ``FinalRefit`` does not define, and it was already in
    ``_LOOKUP_INTERCEPTORS``. Adding a sealed name this class does not carry
    would have reopened the hole with nothing failing.

    There is no ATTACK that demonstrates this today, for exactly that reason,
    so the rule is asserted directly rather than through a probe — and the
    coincidence itself is asserted, so it fails the day it stops holding.
    """
    uncarried = [
        name for name in final_model.FinalRefit._FINAL_METHODS
        if final_model._resolved_through_the_mro(final_model.FinalRefit, name)
        is final_model._UNRESOLVED
    ]
    assert uncarried == ["__getattr__"], (
        f"a sealed name this class does not carry appeared: {uncarried}. The "
        "second clause covers it; this list is the record of which names need it."
    )

    def plain(cls_or_self, *args, **kwargs):
        return None

    for name in uncarried:
        assert final_model._wins_class_level_lookup(
            name, plain, final_model.FinalRefit
        ), f"{name} is carried nowhere on the MRO, so a metaclass answers it"
    # The second clause, isolated from the interceptor coincidence. The only
    # uncarried SEALED name is `__getattr__`, and it is also an interceptor, so
    # the loop above could pass on the interceptor clause alone and the second
    # clause would still be pinned by nothing (round-12 review). A synthetic
    # name that is NEITHER an interceptor NOR carried anywhere on the MRO can
    # only be answered by the second clause, so this pins the clause itself.
    assert final_model._wins_class_level_lookup(
        "a_name_final_refit_does_not_carry", plain, final_model.FinalRefit
    )
    # …and a plain function under a name the MRO DOES carry still loses.
    assert not final_model._wins_class_level_lookup(
        "run", plain, final_model.FinalRefit
    )


def test_the_shadowing_rule_matches_type_getattribute():
    """The full truth table of `_wins_class_level_lookup`, clause by clause.

    The second clause (metaclass wins a name the class's MRO carries nowhere)
    has no attack that demonstrates it today, so it cannot be pinned by a
    probe; the single synthetic-name assertion beside it was deletable. This
    table pins every clause on its own row, so each of the three `return`
    branches of the function has an independent witness and deleting any one
    is no longer enough (round-13 review).
    """
    uncarried = "a_name_final_refit_does_not_carry"

    def plain(cls_or_self, *args, **kwargs):
        return None

    class WithSet:
        def __get__(self, obj, owner=None):
            return lambda params: []

        def __set__(self, obj, value):
            raise AttributeError

    class WithDelete:
        def __get__(self, obj, owner=None):
            return lambda params: []

        def __delete__(self, obj):
            raise AttributeError

    class SetOnlyNoGet:
        """`__set__` without `__get__` is not a descriptor at all, so it loses."""

        def __set__(self, obj, value):
            raise AttributeError

    class DeleteOnlyNoGet:
        def __delete__(self, obj):
            raise AttributeError

    cases = [
        # interceptor clause — both names answer every class-level lookup.
        ("__getattr__", plain, True),
        ("__getattribute__", plain, True),
        # second clause — carried NOWHERE on the MRO, whatever kind of object.
        (uncarried, plain, True),
        (uncarried, WithSet(), True),
        # data-descriptor clause — each half, on a name the MRO DOES carry.
        ("run", WithSet(), True),
        ("run", WithDelete(), True),
        # a data descriptor must first BE a descriptor: __set__/__delete__
        # without __get__ does not shadow, and the seal must not refuse it.
        ("run", SetOnlyNoGet(), False),
        ("run", DeleteOnlyNoGet(), False),
        # a plain non-data descriptor under a carried name loses.
        ("run", plain, False),
    ]
    for name, supplied, expected in cases:
        assert final_model._wins_class_level_lookup(
            name, supplied, final_model.FinalRefit
        ) is expected, (name, supplied)


def test_the_probe_table_covers_every_declared_fact_and_nothing_else():
    declared = {name for name, _, _, _ in final_model.GATE_FACTS}
    assert set(GATE_PROBES) == declared
    assert len(final_model.GATE_FACTS) == len(declared) == 25
    # Every declared reach is a subset of the named entry points, in the
    # order they are named there — so a reach can never be a free-form string.
    points = final_model.RELEASE_ENTRY_POINTS
    for _, _, reach, _ in final_model.GATE_FACTS:
        assert tuple(reach) == tuple(p for p in points if p in reach)


def test_the_reach_measurement_answers_nothing_for_the_unattacked_class():
    """The control every ``reach=()`` fact rests on.

    A ``_reaches`` that always answered ``()`` would make every ``reach=()``
    fact pass while measuring nothing. So: the baseline
    marker must be non-empty, the pristine class must reach NEITHER entry
    point, and an unsealed instance shadow must reach exactly one — proving
    the measurement distinguishes the two.
    """
    assert _BASELINE_CHANNEL_PROBLEMS
    assert _reaches(final_model.FinalRefit) == ()
    opened = object.__new__(final_model.FinalRefit)
    opened.params = dict(PRODUCTION_PARAMS)
    opened._channel_problems = lambda params: []
    assert _reaches(final_model.FinalRefit, instance=opened) == ("run",)


def test_a_definition_python_itself_rejects_is_never_counted_as_a_refusal():
    """A probe that "passes" because Python rejected the construction is fake evidence.

    ``_define`` re-raises any TypeError that is not the seal's, so an attempt
    that never reaches the seal can never be recorded as refused BY it. Pinned
    with a base order Python rejects on its own: ``Node`` is already behind
    ``FinalRefit``, so naming it first has no consistent linearization.
    """
    from dskit.pipeline.node import Node

    with pytest.raises(AssertionError, match="refused by Python, not by the seal"):
        _define({}, (Node, final_model.FinalRefit))


def test_the_seal_sees_the_override_that_getattr_static_skips():
    """Why the seal reads type's real slots instead of inspect.getattr_static.

    ``getattr_static`` SKIPS any MRO entry whose metaclass shadows
    ``__dict__`` rather than trusting what it would return, so it reported
    FinalRefit's own member for a class carrying a live override — the
    round-7 Major. This pins the difference in both directions at once.
    """
    import inspect

    class Shadowing(type(final_model.FinalRefit)):
        __dict__ = property(lambda cls: {})

    carrier = Shadowing("Carrier", (final_model.FinalRefit,), {})
    carrier._channel_problems = classmethod(lambda cls, params: [])

    mine = inspect.getattr_static(carrier, "_channel_problems", None)
    theirs = inspect.getattr_static(final_model.FinalRefit, "_channel_problems")
    assert mine is theirs, "getattr_static no longer skips; this test is stale"

    assert final_model._resolved_through_the_mro(
        carrier, "_channel_problems"
    ) is not final_model._resolved_through_the_mro(
        final_model.FinalRefit, "_channel_problems"
    )
    # The override is live on both paths — that is what made it a TOTAL bypass
    # before round 7 — and it is the USE-TIME re-check that now stops it, at
    # both entry points, rather than the definition-time hook this carrier
    # was built to walk past.
    assert final_model._unsealed_problems(carrier)
    assert _reaches(carrier) == ()


@pytest.mark.parametrize(
    "name,refuses,reach,attempt",
    final_model.GATE_FACTS,
    ids=[name for name, _, _, _ in final_model.GATE_FACTS],
)
def test_every_declared_gate_fact_matches_what_actually_happens(
    name, refuses, reach, attempt
):
    """Change either field in GATE_FACTS and this contradicts an executed outcome."""
    observed_refuses, observed_reach = GATE_PROBES[name]()
    assert observed_refuses is refuses, (
        f"{name}: declared refuses={refuses}, observed {observed_refuses} "
        f"while attempting {attempt}"
    )
    assert observed_reach == tuple(reach), (
        f"{name}: declared reach={tuple(reach)}, observed {observed_reach} "
        f"while attempting {attempt}"
    )


def _module_prose():
    """Every docstring, every ``#:`` note and every string literal in ``final_model.py``.

    NOTHING CLASSIFIES. Round 8 pinned only the paragraphs a ``_GATE_WORDS``
    list judged to be about the gate, and round-8 review broke that twice: trim
    one word from the list, drop the matching digest, and a load-bearing
    paragraph is silently unpinned; and three whole categories of prose — the
    module docstring, every other method's docstring, and ``#:`` comments —
    were never read at all, so a bald false completeness claim was free there.
    Round 9 answered with "every docstring and every note", and round-10 review
    found the next unread category by the same move: :data:`GATE_FACTS` is a
    table of PROSE (each row's ``attempt`` sentence), and a string literal is
    neither a docstring nor a comment, so all twenty could be rewritten freely
    — a REGRESSION, since the round-8 pin had carried them under ``attempt:``
    keys. So the category is gone as a concept: every string constant in the
    module is pinned, whatever it is for.

    Returns
    -------
    dict
        ``{key: text}``. ``"module"``; ``"doc:<dotted name>"`` for every
        module, class and function docstring at any depth; ``"note:<subject>#n"``
        for each ``#:`` comment block, ``n`` distinguishing repeats of one
        subject; and ``"constants:<owner>"`` carrying the ``repr`` of every
        non-docstring literal an owner encloses, joined in source order.

    Notes
    -----
    The ``constants:`` owner is the nearest enclosing class, function or
    ASSIGNED NAME, so ``GATE_FACTS``' twenty rows are one key that no edit
    elsewhere moves. Keying each literal by line and column instead would
    make every insertion above it report hundreds of changes, and a pin
    nobody can read is a pin people regenerate without reading.
    """
    source = inspect.getsource(final_model)
    tree = ast.parse(source)
    out = {}
    module_doc = ast.get_docstring(tree)
    if module_doc:
        out["module"] = module_doc

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                doc = ast.get_docstring(child)
                if doc:
                    out[f"doc:{name}"] = doc
                walk(child, f"{name}.")

    walk(tree, "")
    out.update(_string_constants(tree))
    out.update(_note_blocks(source.splitlines()))
    return out


def _assigned_name(node):
    """The single plain name a statement assigns to, or None."""
    targets = getattr(node, "targets", None) or (
        [node.target] if isinstance(node, ast.AnnAssign) else []
    )
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def _docstring_statement(node):
    """The statement holding ``node``'s docstring, or None."""
    if not isinstance(
        node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
        return None
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        # The STATEMENT, not its value: the loop below iterates statements, so
        # comparing against the Constant never matched and every docstring was
        # collected a second time as an ordinary literal.
        return body[0]
    return None


def _string_constants(tree):
    """``{"constants:<owner>": joined repr}`` for every non-docstring literal.

    EVERY ``ast.Constant``, not every string one. Round 11 found the type
    filter on both sides of the totality assertion at once: a ``bytes``
    literal was invisible to this function AND to the independent walk that
    checks it, so the two agreed on a surface neither could see. The fix is
    not a wider filter — it is no filter. A literal is pinned whatever it is
    for, and the oracle has nothing left to share a blind spot with.
    """
    buckets = {}

    def descend(node, owner):
        docstring = _docstring_statement(node)
        for child in ast.iter_child_nodes(node):
            if child is docstring:
                continue
            if isinstance(child, ast.Constant):
                buckets.setdefault(owner, []).append(repr(child.value))
                continue
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                descend(child, f"{owner}.{child.name}" if owner else child.name)
                continue
            named = (
                _assigned_name(child)
                if isinstance(child, (ast.Assign, ast.AnnAssign))
                else None
            )
            descend(child, f"{owner}.{named}" if owner and named else (named or owner))

    descend(tree, "")
    return {
        f"constants:{owner or '<module>'}": "\x00".join(texts)
        for owner, texts in buckets.items()
    }


def _note_blocks(lines):
    """``{"note:<subject>#n": joined text}`` for every ``#:`` comment block."""
    out, block, subjects = {}, [], collections.Counter()

    def flush(index):
        subject = next((text.strip() for text in lines[index:] if text.strip()), "")
        # The ordinal is in the key ON PURPOSE. Keying by subject alone
        # COLLIDES when a name is documented twice, and the second block then
        # silently overwrites the first — which is exactly how a load-bearing
        # note went unpinned in the sibling module this mechanism was copied
        # to. Round 9 used the line number, which collides with nothing but
        # moves every key below any insertion; an ordinal per subject is
        # stable and just as unique.
        label = subject.split("=")[0].strip()[:48]
        subjects[label] += 1
        out[f"note:{label}#{subjects[label]}"] = " ".join(block)

    for index, line in enumerate(lines):
        if line.lstrip().startswith("#:"):
            block.append(line.strip()[2:].strip())
        elif block:
            flush(index)
            block = []
    if block:
        # A block that runs to the END OF FILE never meets a following line,
        # so a scanner that only emits inside the loop drops it — and prose
        # appended at EOF is then pinned by nothing (round-10 review).
        flush(len(lines))
    return out


def _digest(text):
    """First 16 hex of sha256 over the whitespace-normalised text."""
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]


#: Every piece of prose in ``final_model.py``, pinned by digest.
#:
#: FOUR CONSECUTIVE ROUNDS shipped a false claim in this module's prose that
#: the round's own pin could not see: two inverted sentences, then three more,
#: then one surviving clause, then five adversarial variants at once. Each
#: round's answer was a better filter — a substring, a polarity-bearing
#: substring, a banned vocabulary, a classified digest — and each filter was
#: defeated by writing somewhere the filter did not look. There is nowhere
#: left to look that is not covered here.
#:
#: THE HONEST SCOPE, and it is narrower than it sounds: this detects CHANGE,
#: not falsehood. When it fails, read the new text, decide whether it is TRUE,
#: and update the digest in the same commit. The cost is real — every docstring
#: edit in this module needs a digest update — and it is the cost of a file
#: whose prose has been wrong four rounds running.
_PINNED_MODULE_PROSE = {
    'constants:BUNDLE_FILENAME': '4aea89e0f88a57ad',
    'constants:DEFAULT_INVENTORY_SEED': '5feceb66ffc86f38',
    'constants:EMBARGO_END_MS': 'e5fe7a8a0319a2c5',
    'constants:EMBARGO_START_MS': 'd35a5de67f4a5615',
    'constants:ESTIMATOR_PATH': '3ff47f287f48a3a3',
    'constants:EVIDENCE_FIELDS': '4f0fefbd298ecbfc',
    'constants:FIXTURE_CHANNEL': '77d4e4fe0b5c9e73',
    'constants:FROZEN_CANDIDATE_COUNT': 'c2356069e9d1e79c',
    'constants:FinalRefit._FINAL_METHODS': '81a33309eb34591c',
    'constants:FinalRefit._PARAMS': '353c064591439819',
    'constants:FinalRefit.__init_subclass__': '128e88c0caf5ab47',
    'constants:FinalRefit._attestation': 'd17e304e38914b3e',
    'constants:FinalRefit._channel': '59e560dce77e6f10',
    'constants:FinalRefit._channel_problems': '2c3bf36a1599cda3',
    'constants:FinalRefit._channel_problems.channel': 'b923f9619a03fa40',
    'constants:FinalRefit._channel_problems.pinned': 'dabceb4401cdd3f1',
    'constants:FinalRefit._channel_problems.problems': '4aece890c1e3c488',
    'constants:FinalRefit._estimator_params': 'c09fdb35d0d235cc',
    'constants:FinalRefit._estimator_params.params': 'd86604c5fdf67aa6',
    'constants:FinalRefit._hpo_template': '077067ace77d51f2',
    'constants:FinalRefit._hpo_template.matches': 'fd78e94186205b94',
    'constants:FinalRefit._hpo_template.templates': '8d3f2668f8b894ca',
    'constants:FinalRefit._identity_problems': 'e0e67765cc036908',
    'constants:FinalRefit._identity_problems.digest': '06cadad76bc180e8',
    'constants:FinalRefit._identity_problems.expected': 'e725a27f83d54281',
    'constants:FinalRefit._identity_problems.fields': 'f5ddb3d426810f1b',
    'constants:FinalRefit._identity_problems.rows': 'e1686bec9058bab8',
    'constants:FinalRefit._lean_drop': 'cc6452a7321d04fe',
    'constants:FinalRefit._lean_drop.drop': '61e1af26f63f92a5',
    'constants:FinalRefit._release_identity': 'd1da6a7b18949587',
    'constants:FinalRefit._release_identity.identity': '320efffe762d958b',
    'constants:FinalRefit._row_identities': '3e092d72a5c01ca1',
    'constants:FinalRefit._row_identities.pinned': 'c2d2706b50d2f6a4',
    'constants:FinalRefit._run_pin_problems': '930ab6b3fecd918d',
    'constants:FinalRefit._run_pin_problems.evidence': '44e288ff35d977ad',
    'constants:FinalRefit._run_pin_problems.run_dir': 'd17e304e38914b3e',
    'constants:FinalRefit._schema_problems': '6a93e999a4a6840a',
    'constants:FinalRefit._schema_problems.categories': '5d42d9e9761eb7bf',
    'constants:FinalRefit._schema_problems.features': 'fbb4ccd844f5f09b',
    'constants:FinalRefit._schema_problems.fixture': '08ad17d85d857265',
    'constants:FinalRefit._verified_hpo_outputs': 'fdb6f168b15a2ff5',
    'constants:FinalRefit._verified_hpo_outputs.document_hash': 'dabceb4401cdd3f1',
    'constants:FinalRefit._winner_from_evidence': '3c08cf46a446f198',
    'constants:FinalRefit._winners': '4f2894548399cae9',
    'constants:FinalRefit._winners.digest': '2650c651d1a1b006',
    'constants:FinalRefit._winners.evidence': 'd17e304e38914b3e',
    'constants:FinalRefit._winners.inventory_digest': 'dc937b59892604f5',
    'constants:FinalRefit._winners.ledger': 'e0c8be2e1e7bd03d',
    'constants:FinalRefit._winners.manifest_digests': '946524b6e06e0df6',
    'constants:FinalRefit._winners.source_document': '9ca4f8a9aaf340c7',
    'constants:FinalRefit._wire_problems': '8cff04d108129504',
    'constants:FinalRefit._wire_problems.ts_ms': '253081dd263e948b',
    'constants:FinalRefit.outputs': '7b55291b0339b653',
    'constants:FinalRefit.role': '4f088242ee2d9ff4',
    'constants:FinalRefit.run': 'f9aaf8827a412d12',
    'constants:FinalRefit.run.feature_order': 'fbb4ccd844f5f09b',
    'constants:FinalRefit.run.manifest': '3e7164afcdea9bed',
    'constants:FinalRefit.validate_inputs': '3e0412bc2fd47995',
    'constants:FinalRefit.validate_inputs.identity': '320efffe762d958b',
    'constants:FinalRefit.validate_inputs.start': 'd5b70d0b70708d14',
    'constants:FinalRefit.validate_params': '832bf207a4480e54',
    'constants:FinalRefit.validate_params.seed': '366ecbf4e2adf7ba',
    'constants:FrozenWinners.outputs': 'a00e3eb4da724c4e',
    'constants:FrozenWinners.run': '4acd2f48f7db5a4a',
    'constants:FrozenWinners.run.document_hash': '2dffb92adacd6eea',
    'constants:FrozenWinners.run.folds': '01ae447499c674cf',
    'constants:FrozenWinners.run.pipeline': 'a234801ddfde6b0e',
    'constants:FrozenWinners.run.row': 'a7a03a73f90572ca',
    'constants:FrozenWinners.run.run_dir': '2ea241ed7beb23b4',
    'constants:FrozenWinners.run.scans': '6314b263a4727f00',
    'constants:FrozenWinners.validate_inputs': '830a5723330f3dd4',
    'constants:FrozenWinners.validate_inputs.row': 'a7a03a73f90572ca',
    'constants:GATE_FACTS': 'd0fac7cf2e13e890',
    'constants:HEADS': 'd7a3abf5e90e956b',
    'constants:HPO_LEDGER_OUTPUT': 'bdf0d7f6bf97df66',
    'constants:LOCKBOX_START_MS': '3485b2af67cc2e77',
    'constants:PRODUCTION_CHANNEL': '55f2295cc846c9ed',
    'constants:RELEASE_ENTRY_POINTS': '7e202d5283c862c1',
    'constants:WIRE_LABEL_FIELD': '0690eccab0b647c5',
    'constants:_DEFAULT_HPO_CONFIG': '0f1309523ab89619',
    'constants:_DEFAULT_LEAN_MASK_CONFIG': '7059e3fab565b236',
    'constants:_LOOKUP_INTERCEPTORS': 'b89c61f465ba32ce',
    'constants:_PENDING': '67e3e24bae75aaa8',
    'constants:_PRODUCER_PREFIX': '52511c515ea8db77',
    'constants:_REAL_DICT': '3bf137a69eec50e9',
    'constants:_REAL_MRO': 'dd2ceb4b3459e50c',
    'constants:_WRAPPER_PATH': 'ceab284417f60b67',
    'constants:__all__': 'fd81f7dd76197d27',
    'constants:_epoch_ms': '83149f20476e52d1',
    'constants:_is_sha256': 'c05217bb83828b48',
    'constants:_unsealed_problems': '2fa2ca57110e0297',
    'constants:_wins_class_level_lookup': '2d9373a3952b4e24',
    'constants:boundary_flags': 'dc937b59892604f5',
    'constants:boundary_flags.space': 'dc937b59892604f5',
    'constants:cluster_scores_by_day': 'eb3d70115f87cfc8',
    'constants:cluster_scores_by_day.missing': 'eab5d8567360b232',
    'constants:final_hpo_document_identity': '907d087e6b289872',
    'constants:final_hpo_document_identity.path': 'dc937b59892604f5',
    'constants:hpo_space': '0059a0787ac05d9f',
    'constants:hpo_space.matches': 'fd78e94186205b94',
    'constants:hpo_space.model': '8bd2e950f86ebd71',
    'constants:hpo_space.path': 'dc937b59892604f5',
    'constants:hpo_space.space': 'd1066122281b62fb',
    'constants:hpo_space.template': '5feceb66ffc86f38',
    'constants:hpo_space.templates': '8d3f2668f8b894ca',
    'constants:lean_feature_drop': 'db9fe9e77ebe15d5',
    'constants:lean_feature_drop.drop': '790b0bf652a462e1',
    'constants:lean_feature_drop.matches': '8159d89693b83b82',
    'constants:lean_feature_drop.model': '8bd2e950f86ebd71',
    'constants:lean_feature_drop.path': 'dc937b59892604f5',
    'constants:lean_feature_drop.template': '5feceb66ffc86f38',
    'constants:lean_feature_drop.templates': 'ba32e23886863b34',
    'constants:one_standard_error_winner': 'eb83c4bb6f30201d',
    'constants:one_standard_error_winner.inventory': '00feb4e19e3425b3',
    'constants:one_standard_error_winner.ruled': '3378f131311498f6',
    'constants:permitted_for_refit': '60a33e6cf5151f2d',
    'constants:refit_heads': '78dd6dcd08344ce5',
    'constants:refit_heads.categorical_feature': 'dc937b59892604f5',
    'constants:refit_heads.ts_ms': '253081dd263e948b',
    'constants:run_lead_selection': '037854259879db00',
    'constants:run_lead_selection.diagnostics': '08c3aaabaeb54ce2',
    'constants:run_lead_selection.passthrough': '6fd30de537e25e3e',
    'constants:run_lead_selection.selection': '3378f131311498f6',
    'constants:simplicity_key': 'c0e2a35e7d206be4',
    'constants:simplicity_key.overrides': 'b9ee4893ce521d81',
    'constants:squared_error_improvement': '8b39f25d2e4d7116',
    'doc:FinalRefit': 'be619cfde428dfb0',
    'doc:FinalRefit.__init_subclass__': '8be58a89d702665e',
    'doc:FinalRefit._attestation': 'c60c3dd2b09323d7',
    'doc:FinalRefit._channel': '3165214ce0642edb',
    'doc:FinalRefit._channel_problems': '1c2d0d907212a621',
    'doc:FinalRefit._estimator_params': '8b8854548858ccc6',
    'doc:FinalRefit._identity_problems': '8c5edf7bca0312bb',
    'doc:FinalRefit._lean_drop': '027a323085b013fc',
    'doc:FinalRefit._release_identity': '61345971e5adfb3d',
    'doc:FinalRefit._row_identities': '344ae2cdb83a72b8',
    'doc:FinalRefit._run_pin_problems': 'ddf84a8de8206188',
    'doc:FinalRefit._schema_problems': '7edb6ffb9b6d8ad5',
    'doc:FinalRefit._verified_hpo_outputs': 'e2834ebb3f26e340',
    'doc:FinalRefit._winner_from_evidence': '74f70cc3d2cc12af',
    'doc:FinalRefit._wire_problems': '151e3430202de377',
    'doc:FinalRefit.run': '2a1de82cc12f94c0',
    'doc:FinalRefit.validate_inputs': '4e057f59846b0464',
    'doc:FinalRefit.validate_params': '60749320f3fa55d4',
    'doc:FrozenWinners': '184c51ddcce3b008',
    'doc:FrozenWinners.run': '42d0cfd316d9520c',
    'doc:FrozenWinners.validate_inputs': 'be7561daf92cb45c',
    'doc:SealRefused': 'a6aaa8b83de3177d',
    'doc:_epoch_ms': '9775f2736675746a',
    'doc:_is_sha256': '69a8a4877d33b696',
    'doc:_metaclass_supplied': 'a09c4290733bbd9a',
    'doc:_resolved_through_the_mro': '3ef6b33a113e80e8',
    'doc:_sealed_violations': '07d207aa4015d069',
    'doc:_unsealed_problems': 'aff0d483ebef4f1f',
    'doc:_wins_class_level_lookup': 'ab05ac6736369802',
    'doc:boundary_flags': 'ab4c1bbb11748a13',
    'doc:build_candidate_inventory': 'd69c10a2fca8ff1b',
    'doc:cluster_scores_by_day': 'b1ec5cc1794f0f0b',
    'doc:final_hpo_document_identity': 'df13456fb8e3f933',
    'doc:hpo_space': '1db3ec460b038999',
    'doc:lean_feature_drop': 'e3a1d5be4da169fc',
    'doc:one_standard_error_winner': '6dee327e7b4d45c8',
    'doc:permitted_for_refit': '94a9e5c874036a23',
    'doc:refit_heads': '2c83bbf9c8af5d6e',
    'doc:run_lead_selection': 'c4a144976ac96c58',
    'doc:simplicity_key': '7a83d56eb8a48535',
    'doc:squared_error_improvement': '4d0251906dc9e959',
    'module': 'f33b5c04eb088a9a',
    'note:BUNDLE_FILENAME#1': '1e8632eddc800713',
    'note:DEFAULT_INVENTORY_SEED#1': 'dd96ec24fb430a07',
    'note:ESTIMATOR_PATH#1': '31e07da49514114f',
    'note:EVIDENCE_FIELDS#1': '14f0404e5e37120a',
    'note:FIXTURE_CHANNEL#1': '3c8484d55a451840',
    'note:FROZEN_CANDIDATE_COUNT#1': 'df64999508c56f91',
    'note:GATE_FACTS#1': '454b6b2dda59a80e',
    'note:HEADS#1': '7c63130588c65cdc',
    'note:RELEASE_ENTRY_POINTS#1': 'b8f09bd9b69b151b',
    'note:WIRE_LABEL_FIELD#1': '86460874f6af4aa8',
    'note:_DEFAULT_HPO_CONFIG#1': '8225e9a5c01b2b36',
    'note:_DEFAULT_LEAN_MASK_CONFIG#1': '78b48bf039114649',
    'note:_FINAL_METHODS#1': '69eca47ca66426e2',
    'note:_LOOKUP_INTERCEPTORS#1': 'ceb785904dc6e599',
    'note:_PENDING#1': '02e5e133366ecbd2',
    'note:_PRODUCER_PREFIX#1': '95291bc1fe7976ef',
    'note:_REAL_MRO#1': 'f54826c85575c6dc',
    'note:_UNRESOLVED#1': 'f62d95a53bd582c1',
    'note:def _epoch_ms(date_str):#1': '9333c1a81c62da1b',
}




def test_every_piece_of_prose_in_the_module_is_pinned_by_digest():
    """No prose in this module changes without this test failing.

    There is no classifier to trim and no unscanned corner to write in: the
    module docstring, every class and function docstring at any nesting depth,
    and every ``#:`` note are all here.
    """
    observed = {key: _digest(text) for key, text in _module_prose().items()}
    added = sorted(set(observed) - set(_PINNED_MODULE_PROSE))
    removed = sorted(set(_PINNED_MODULE_PROSE) - set(observed))
    changed = sorted(
        key for key in set(observed) & set(_PINNED_MODULE_PROSE)
        if observed[key] != _PINNED_MODULE_PROSE[key]
    )
    assert not (added or removed or changed), (
        "prose moved in final_model.py. Read the new text, decide whether it is "
        f"TRUE, then update _PINNED_MODULE_PROSE in the same commit. "
        f"added={added} removed={removed} changed={changed}"
    )


def _runtime_prose(module=None):
    """``{"module"|"doc:<dotted>": cleaned __doc__}`` read from the LIVE objects.

    The pin above reads source text. ``__doc__`` is an ordinary writable
    attribute, so ``FinalRefit.run.__doc__ += "..."`` or a reassignment
    anywhere below the definition changes what a reader, a help() call and
    any doc build actually see while the source the pin digests is untouched
    (round-10 review). This walks the module object instead, and the test
    below asserts the two agree everywhere they overlap.
    """
    module = final_model if module is None else module
    out = {}
    if module.__doc__:
        out["module"] = inspect.cleandoc(module.__doc__)

    def visit(owner, prefix, seen):
        for name, value in vars(owner).items():
            target = value
            if isinstance(target, (classmethod, staticmethod)):
                target = target.__func__
            if isinstance(target, property):
                target = target.fget
            if not (inspect.isclass(target) or inspect.isfunction(target)):
                continue
            if getattr(target, "__module__", None) != module.__name__:
                continue
            if id(target) in seen:
                continue
            seen.add(id(target))
            dotted = f"{prefix}{name}"
            if target.__doc__:
                out[f"doc:{dotted}"] = inspect.cleandoc(target.__doc__)
            if inspect.isclass(target):
                visit(target, f"{dotted}.", seen)

    visit(module, "", set())
    return out


def test_the_runtime_walk_reaches_every_way_a_docstring_can_be_attached():
    """The walk's unwrapping, exercised directly rather than by what the module has.

    Round-11 review deleted the ``property`` unwrap and the suite stayed green
    — ``final_model.py`` has no property today, so the branch was dormant, and
    "no test covers it because nothing uses it" is how a member becomes exempt
    the day someone adds one. The four shapes are asserted on a synthetic
    module instead, so the walk's reach does not depend on what this module
    happens to contain.
    """
    synthetic = types.ModuleType("synthetic")
    synthetic.__doc__ = "the synthetic module"

    class Carrier:
        """the class"""

        @classmethod
        def a_classmethod(cls):
            """the classmethod"""

        @staticmethod
        def a_staticmethod():
            """the staticmethod"""

        @property
        def a_property(self):
            """the property"""

        def a_method(self):
            """the method"""

    def a_function():
        """the function"""

    for value in (Carrier, a_function):
        value.__module__ = "synthetic"
    for member in ("a_classmethod", "a_staticmethod", "a_property", "a_method"):
        unwrapped = inspect.unwrap(vars(Carrier)[member])
        target = getattr(unwrapped, "__func__", getattr(unwrapped, "fget", unwrapped))
        target.__module__ = "synthetic"
    synthetic.Carrier = Carrier
    synthetic.a_function = a_function

    found = _runtime_prose(synthetic)
    assert found == {
        "module": "the synthetic module",
        "doc:Carrier": "the class",
        "doc:Carrier.a_classmethod": "the classmethod",
        "doc:Carrier.a_staticmethod": "the staticmethod",
        "doc:Carrier.a_property": "the property",
        "doc:Carrier.a_method": "the method",
        "doc:a_function": "the function",
    }


def test_the_live_docstrings_are_the_ones_the_pin_digested():
    """``__doc__`` is writable; the pin reads source. They must not diverge.

    A module that appends to a docstring after defining it publishes prose no
    source-reading pin can see. Every docstring reachable on the live module
    is compared with the one parsed out of the source, so that edit fails
    here even though the source text it digests never moved.
    """
    source = _module_prose()
    live = _runtime_prose()
    assert live, "the runtime walk found no docstring at all"
    assert "module" in live and "doc:FinalRefit" in live
    unseen = sorted(set(live) - set(source))
    assert not unseen, (
        f"live docstrings the source pin never saw: {unseen} — __doc__ was "
        "written at run time"
    )
    # BOTH directions. Round-11 review deleted the classmethod unwrap from
    # `_runtime_prose` and the suite stayed green: the walk silently stopped
    # finding six docstrings — `__init_subclass__`'s among them, the one
    # carrying the seal's four pinned claims — and a check that only asks
    # "did live find anything source did not" cannot see a live walk that
    # found LESS. A member this walk stops reaching is a member exempt from
    # the source pin forever. So the two sets must be EQUAL. A docstring on a
    # function nested inside a method would fail here, because `vars()` cannot
    # reach a closure: that is a deliberate refusal, not an oversight — add it
    # and decide what should happen, rather than inheriting an exemption.
    unreached = sorted(
        key for key in source if key.startswith("doc:") and key not in live
    )
    assert not unreached, (
        f"the live walk never reached: {unreached} — these docstrings are "
        "exempt from the runtime comparison, so a __doc__ written at run time "
        "over any of them would go unseen"
    )
    differing = sorted(key for key in live if live[key] != source[key])
    assert not differing, (
        f"live __doc__ differs from the source the pin digests: {differing}"
    )


def test_the_prose_pin_covers_every_docstring_the_module_defines():
    """The pin's REACH, asserted rather than assumed.

    A `_module_prose` that quietly stopped walking nested definitions would
    make the test above pass over a shrinking surface. So the count is checked
    against an independent walk, and the three categories round-8 review found
    unscanned are each asserted present by name.
    """
    prose = _module_prose()
    tree = ast.parse(inspect.getsource(final_model))
    assert "module" in prose
    assert "doc:FinalRefit" in prose
    assert "doc:FinalRefit.__init_subclass__" in prose
    assert "doc:FinalRefit.run" in prose
    assert "doc:_resolved_through_the_mro" in prose
    assert "doc:_unsealed_problems" in prose
    assert "doc:_metaclass_supplied" in prose
    notes = [key for key in prose if key.startswith("note:")]
    assert notes, "no #: note was captured"
    assert len(set(notes)) == len(notes), "two #: blocks share a key; one is unpinned"
    independent = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and ast.get_docstring(node)
    )
    assert sum(1 for key in prose if key.startswith("doc:")) == independent
    # Every GATE_FACTS row's `attempt` is PROSE inside a tuple. Round 8
    # pinned them, round 9's rewrite dropped the category while claiming wider
    # coverage, and round-10 review caught the regression: every ``attempt``
    # could be reworded freely for one whole round. The key is named here so
    # the same silent drop fails instead. The row COUNT is deliberately not
    # written out — round-11 review found the number this comment used to
    # carry already stale, in the one prose site no pin reaches (a plain `#`
    # comment), which is the same defect one level down. The count is asserted
    # executably in `test_the_probe_table_covers_every_declared_fact_and_nothing_else`.
    assert "constants:GATE_FACTS" in prose
    for _, _, _, attempt in final_model.GATE_FACTS:
        assert repr(attempt) in prose["constants:GATE_FACTS"]
    # …and the ``constants:`` buckets are TOTAL, not a sample: every literal
    # the module holds that is not a docstring is inside exactly one. NO TYPE
    # FILTER on either side. Round-11 review found the previous pair sharing
    # one — both said `isinstance(value, str)`, so a `bytes` literal was
    # invisible to the collector AND to the walk that was supposed to be
    # independent of it, and the assertion passed over a surface neither could
    # see. An oracle built from the thing it checks asserts nothing.
    docstrings = {
        id(statement.value)
        for node in ast.walk(tree)
        for statement in [_docstring_statement(node)]
        if statement is not None
    }
    every_literal = [
        repr(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and id(node) not in docstrings
    ]
    collected = [
        text
        for key in prose if key.startswith("constants:")
        for text in prose[key].split("\x00")
    ]
    assert sorted(collected) == sorted(every_literal)


def test_a_note_block_at_the_end_of_the_file_is_still_captured():
    """A ``#:`` block with no following line is prose too.

    The scanner emitted a block only when a non-comment line followed it, so
    documentation appended at EOF was pinned by nothing and could say anything
    (round-10 review). Asserted on synthetic lines rather than on the module,
    so it keeps holding whatever ``final_model.py``'s last line becomes.
    """
    assert _note_blocks(["#: mid-file", "SOMETHING = 1"]) == {
        "note:SOMETHING#1": "mid-file"
    }
    assert _note_blocks(["X = 1", "#: the tail"]) == {"note:#1": "the tail"}
    twice = _note_blocks(["#: first", "SAME = 1", "#: second", "SAME = 2"])
    assert twice == {"note:SAME#1": "first", "note:SAME#2": "second"}


#: The four claims the seal's docstring rests on, kept beside the digests
#: because a digest failure says WHAT changed and these say which claims are
#: load-bearing. Each carries its own polarity word, so an inversion deletes
#: the substring as well as moving the digest.
_PINNED_SEAL_CLAIMS = (
    "Refuse a subclass that resolves a sealed member to anything but this "
    "class's own.",
    "Scope lives in :data:`GATE_FACTS`, and this docstring states none of\n"
    "        it.",
    "it is **not an authority\n        boundary**",
    "ADR-0122's out-of-Python launcher, which is not built.",
)


def test_the_seal_docstring_still_rests_on_its_four_pinned_claims():
    """The digests say what changed; these name which claims are load-bearing."""
    doc = final_model.FinalRefit.__init_subclass__.__doc__
    assert "GATE_FACTS" in doc
    assert len(_PINNED_SEAL_CLAIMS) == 4, "a claim was dropped from the pin"
    for claim in _PINNED_SEAL_CLAIMS:
        assert claim in doc, f"pinned claim is gone or reworded: {claim!r}"


def test_the_declaration_is_where_the_polarity_lives():
    """The prose cites the data; it does not restate the outcomes."""
    for doc in (
        final_model.FinalRefit.__doc__,
        final_model.FinalRefit.__init_subclass__.__doc__,
    ):
        assert "GATE_FACTS" in doc
    # Every row names an ATTEMPT and carries both outcomes as fields. The
    # attempt TEXT is held by the ``strings:GATE_FACTS`` digest, not by a
    # banned-prefix list: round 10 dropped that list, which forbade three
    # openings and read as if it forbade stating an outcome in prose.
    for name, _, _, attempt in final_model.GATE_FACTS:
        assert attempt
        assert name.startswith(("refuses_", "shipped_"))
    # The class docstring's one summarising sentence is true by construction
    # only while some declared attempt actually opens an entry point.
    assert any(reach for _, _, reach, _ in final_model.GATE_FACTS)


# --- ADR-0185: FrozenWinners re-derives each lead's ruling from an attested walk fold ---


def _scan_hpo_document():
    """The fixture recipe as ten ``scan_hNN`` nodes, the shape a walk fold's config carries."""
    from dskit.pipeline.document import PipelineDocument

    obj = _fixture_hpo_document().to_obj()
    model = [
        t for t in obj["stages"]["finalist"]["params"]["templates"]
        if t.get("family") == "pooled-lightgbm"
    ][0]["model"]
    base = obj["pipeline"]["scan"]
    for head in HEADS:
        node = json.loads(json.dumps(base))
        node["params"].update({key: model[key] for key in ("hpo_space", "hpo_trials", "hpo_seed")})
        obj["pipeline"][f"scan_{head}"] = node
    obj["name"] = "fixture-warmup-hpo"
    return PipelineDocument.from_obj(obj)


def _walk_row(tmp_path, run_dir, folds=1):
    import hashlib

    summary = tmp_path / "walkforward.json"
    summary.write_text(json.dumps({
        "state": "ran",
        "folds": [{"cutoff": f"2022-05-0{6 + i}", "run_dir": str(run_dir), "state": "ran"} for i in range(folds)],
    }))
    return {
        "id": "lean", "state": "ran", "exit_code": 0,
        "evidence_manifest_path": str(summary),
        "evidence_manifest_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
    }


def test_frozen_winners_reads_each_leads_attested_one_standard_error_ruling(tmp_path):
    from intraday_equities.final_model import FrozenWinners

    document = _scan_hpo_document()
    run_dir, _, _ = _attested_hpo_run(tmp_path, document=document)
    out = FrozenWinners("winners").run(None, {"run": _walk_row(tmp_path, run_dir)})
    obj = _fixture_hpo_document().to_obj()
    for index, head in enumerate(HEADS):
        expected = _head_evidence(head, index, obj)["selection"]["selected_candidate"]
        assert out["winners"][f"scan_{head}"] == expected
    assert sorted(out["evidence"]["heads"]) == [f"scan_{head}" for head in HEADS]
    assert out["evidence"]["document_hash"] == document.hash


def test_frozen_winners_refuses_a_walk_with_more_than_one_fold(tmp_path):
    from intraday_equities.final_model import FrozenWinners

    run_dir, _, _ = _attested_hpo_run(tmp_path, document=_scan_hpo_document())
    with pytest.raises(ValueError, match="exactly one fold"):
        FrozenWinners("winners").run(None, {"run": _walk_row(tmp_path, run_dir, folds=2)})


def test_frozen_winners_refuses_an_unattested_fold(tmp_path):
    from intraday_equities.final_model import FrozenWinners

    run_dir, _, _ = _attested_hpo_run(tmp_path, document=_scan_hpo_document(), state="error")
    with pytest.raises(ValueError, match="attest"):
        FrozenWinners("winners").run(None, {"run": _walk_row(tmp_path, run_dir)})


def test_frozen_winners_refuses_a_summary_that_moved_after_its_seal(tmp_path):
    from intraday_equities.final_model import FrozenWinners

    run_dir, _, _ = _attested_hpo_run(tmp_path, document=_scan_hpo_document())
    row = _walk_row(tmp_path, run_dir)
    with open(row["evidence_manifest_path"], "a", encoding="utf-8") as handle:
        handle.write(" ")
    with pytest.raises(ValueError, match="seal"):
        FrozenWinners("winners").run(None, {"run": row})


def _distinct_head_evidence(head, index, document_obj):
    """Evidence whose one-standard-error winner differs per head (se 0, a distinct argmax)."""
    model = [
        t for t in document_obj["stages"]["finalist"]["params"]["templates"]
        if t.get("family") == "pooled-lightgbm"
    ][0]["model"]
    inventory = CandidateInventory(
        model["hpo_space"], n_trials=model["hpo_trials"], seed=model["hpo_seed"]
    )
    best = (3 * index) % len(inventory.combinations)
    ledger = TrialLedger(inventory, evidence_fields=EVIDENCE_FIELDS)
    for position, candidate in enumerate(inventory.combinations):
        ledger.record(
            candidate, -float((position - best) ** 2), se=0.0, diagnostics={},
            on_boundary={}, fit_seed=0, cuts={}, n_rows=6, train_val_gap=0.0,
            collapsed_prediction_variance=False,
        )
    selection = OneStandardErrorSelector(select="max", simplicity_key=simplicity_key).select(ledger)
    return {
        "producer_key": f"scan_{head}",
        "feature_order": list(WIRE_FEATURES),
        "categorical_feature": [],
        "ledger": ledger.to_obj(),
        "selection": selection.to_obj(),
    }


def test_frozen_winners_gives_each_lead_its_own_ledgers_winner(tmp_path):
    from intraday_equities.final_model import FrozenWinners

    obj = _fixture_hpo_document().to_obj()
    run_dir, _, _ = _attested_hpo_run(
        tmp_path, document=_scan_hpo_document(), evidence=_distinct_head_evidence,
    )
    winners = FrozenWinners("winners").run(None, {"run": _walk_row(tmp_path, run_dir)})["winners"]
    expected = {
        f"scan_{head}": _distinct_head_evidence(head, index, obj)["selection"]["selected_candidate"]
        for index, head in enumerate(HEADS)
    }
    assert winners == expected
    assert len({json.dumps(value, sort_keys=True) for value in winners.values()}) > 1


def test_frozen_winners_refuses_a_recorded_selection_that_is_not_the_ledgers_ruling(tmp_path):
    from intraday_equities.final_model import FrozenWinners

    def forged(head, index, obj):
        evidence = _distinct_head_evidence(head, index, obj)
        if head == "h03":
            other = _distinct_head_evidence("h04", index + 1, obj)["selection"]
            evidence["selection"] = other
        return evidence

    run_dir, _, _ = _attested_hpo_run(tmp_path, document=_scan_hpo_document(), evidence=forged)
    with pytest.raises(ValueError, match="one-standard-error ruling"):
        FrozenWinners("winners").run(None, {"run": _walk_row(tmp_path, run_dir)})


def test_frozen_winners_refuses_evidence_recorded_by_another_producer(tmp_path):
    from intraday_equities.final_model import FrozenWinners

    def swapped(head, index, obj):
        evidence = _distinct_head_evidence(head, index, obj)
        if head == "h03":
            evidence["producer_key"] = "scan_h04"
        return evidence

    run_dir, _, _ = _attested_hpo_run(tmp_path, document=_scan_hpo_document(), evidence=swapped)
    with pytest.raises(ValueError, match="another producer"):
        FrozenWinners("winners").run(None, {"run": _walk_row(tmp_path, run_dir)})

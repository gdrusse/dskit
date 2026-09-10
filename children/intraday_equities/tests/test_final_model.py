"""``final_model.py`` — domain assembly for the ten-lead final release
(ADR-0114 Phase 2, reconciling ADR-0113's Phase 1).

Everything here is SYNTHETIC: tiny in-memory rows and a canned
``evaluate`` callback stand in for a real per-candidate LightGBM fit. No
market data, no real LightGBM fit against real history, no real P16
artifact load, and no pipeline `run`/`walkforward` execution — this
whole gate is synthetic-tests-only (the master plan's own ruling).
"""

from __future__ import annotations

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


def test_module_contract_describes_wired_fail_closed_final_refit():
    contract = " ".join(final_model.__doc__.split())
    assert "wired by ``configs/run-final-refit.json``" in contract
    assert "unconditionally refuses validation and runtime" in contract
    assert "ten labelled input wires" in contract
    assert "cannot enable a refit or bundle write" in contract


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


def test_final_refit_remains_non_executable_with_filled_asserted_identities():
    """Config digests cannot attest the rows supplied to the node."""
    params = {
        "hpo_run_dir": "/completed/run", "hpo_document_sha256": "d" * 64,
        "hpo_evidence": {head: {} for head in HEADS},
        "feature_order": ["x"], "categorical_feature": [],
        "categorical_encoding": {}, "predict_fixture": [[0.0]],
        "refit_identity": {
            "source": {"sha256": "1" * 64}, "cache": {"sha256": "2" * 64},
            "train_start_ms": 1, "refit_end_ms": LOCKBOX_START_MS,
            "embargo_start_ms": EMBARGO_START_MS,
            "embargo_end_ms": EMBARGO_END_MS,
        },
    }
    with pytest.raises(ConfigError, match="non-executable.*attestation"):
        final_model.FinalRefit("refit", params)

    node = object.__new__(final_model.FinalRefit)
    node.params = params
    with pytest.raises(ValueError, match="non-executable.*attestation"):
        node.run(None, {head: [] for head in HEADS})


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

"""The binary-contract modules wired into a real document by import path, run through the driver.

Nothing is registered: a document names each class as ``module:Class``, which is how a project
uses a pack the toolkit has not claimed a short kind name for. The run also pins what the
identity hash is for here: reading the held-out rows is a different document.
"""

import json
import os

import pytest

from dskit.pipeline.base import ConfigError, import_ref, is_class_ref
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.node import Node
from dskit.pipeline.planner import plan

ASOF = "2026-10-06"
SETTLE = 1_800_000_000_000
CUT = SETTLE + 5_400_000  # 90 minutes in: contracts 0, 1, 2 settle before it, 3, 4, 5 at or after
PRICING = "dskit.pipeline.binary_pricing:BinaryFairValue"
SCORING = "dskit.pipeline.binary_scoring:BucketedBinaryScore"


class Rows(Node):
    """A stand-in source: six contracts on one underlying, three settling before the cut and three after."""

    role = "transform"
    outputs = ("records",)

    def run(self, ctx, inputs):
        rows = []
        for i in range(6):
            settle = SETTLE + i * 1_800_000
            rows.append({
                "id": f"c{i}", "event": f"e{i}", "settle_ms": settle, "decision_ms": settle - 305_000,
                "spot": 100.0, "rv": 0.0005 + 0.0001 * i, "payoff": "above", "lo": 99.0 + i * 0.25, "hi": None,
                "label": i % 2, "mid": 0.45 + 0.02 * i, "bid": 0.44 + 0.02 * i, "ask": 0.46 + 0.02 * i,
                "fee_yes": 0.02, "fee_no": 0.02, "ok": True, "lead": 5})
        return {"records": rows}


def document(run_root, report_segments):
    return PipelineDocument.from_obj({
        "name": "binary-contract-e2e",
        "pipeline": {
            "rows": {"uses": "tests.pipeline.test_binary_contract_e2e:Rows"},
            "fair": {"uses": PRICING, "inputs": {"records": "$rows.records"}, "params": {
                "vol_field": "rv", "spot_field": "spot", "payoff_field": "payoff", "lower_field": "lo",
                "upper_field": "hi", "decision_field": "decision_ms", "settle_field": "settle_ms",
                "fair_field": "fair", "exec_lag_s": 5, "averaging_window_s": 60}},
            "score": {"uses": SCORING, "inputs": {"records": "$fair.records"}, "params": {
                "model_fields": ["fair"], "market_field": "mid", "label_field": "label",
                "settle_field": "settle_ms", "bid_field": "bid", "ask_field": "ask",
                "fee_yes_field": "fee_yes", "fee_no_field": "fee_no", "eligible_field": "ok",
                "bucket_edges": [0.0, 0.5, 1.0], "margin": 0.0, "by": ["lead"],
                "segments": {"development": {"end_ms": CUT}, "heldout": {"start_ms": CUT}},
                "report_segments": report_segments, "cluster_field": "event", "cluster_block_s": 3600}},
        },
        "outputs": {"run_root": str(run_root)},
    })


def test_the_classes_resolve_by_import_path_and_name_no_registered_kind():
    assert is_class_ref(PRICING) and is_class_ref(SCORING)
    assert import_ref(PRICING).__name__ == "BinaryFairValue"
    assert import_ref(SCORING).__name__ == "BucketedBinaryScore"


def test_a_document_plans_and_runs_the_whole_chain(tmp_path):
    doc = document(tmp_path, ["development"])
    assert plan(doc) is not None
    result = run_document(doc, asof=ASOF)
    assert result.exit_code == 0
    artifacts = os.path.join(result.run_dir, "artifacts", "score")
    with open(os.path.join(artifacts, "binary_score.json"), encoding="utf-8") as handle:
        saved = json.load(handle)
    assert {s["segment"] for s in saved["scores"]} == {"development"}
    pooled = [s for s in saved["scores"] if s["group"] == "all" and s["bucket"] == "all"][0]
    assert pooled["n"] == 3, "the three contracts that settle before the cut"
    assert saved["summary"]["census"]["unreported"] == 3, "the held-out rows were counted, never scored"


def test_reading_the_held_out_segment_is_a_different_document_and_a_different_run(tmp_path):
    first = run_document(document(tmp_path, ["development"]), asof=ASOF)
    second = run_document(document(tmp_path, ["development", "heldout"]), asof=ASOF)
    assert first.run_hash != second.run_hash
    assert first.run_dir != second.run_dir
    with open(os.path.join(second.run_dir, "artifacts", "score", "binary_score.json"), encoding="utf-8") as handle:
        assert {s["segment"] for s in json.load(handle)["scores"]} == {"development", "heldout"}


def test_a_typo_in_a_param_is_a_plan_time_error_not_a_silent_default(tmp_path):
    doc = document(tmp_path, ["development"])
    doc.pipeline["fair"].params["averaging_window"] = 60  # the knob is averaging_window_s
    with pytest.raises(ConfigError, match="averaging_window"):
        run_document(doc, asof=ASOF)


def test_the_nodes_are_wired_by_import_path_and_claim_no_registry_entry():
    from dskit.pipeline.node import DEFAULT_NODE_KINDS

    ours = {import_ref(PRICING), import_ref(SCORING)}
    claimed = [kind for kind in DEFAULT_NODE_KINDS.kinds() if DEFAULT_NODE_KINDS.get(kind)[0] in ours]
    assert claimed == []

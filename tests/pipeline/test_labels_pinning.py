"""S4-F4 regression: the driver PINS fingerprinted labels instances.

Labels are identity material (the driver fingerprints them at resolve) —
but identity only binds what the run CONSUMES if execute reuses the very
instance whose scan the fingerprint hashed. A labels node rebuilt at
execute would re-scan a store that may have grown mid-run (live
recorders append), consuming outcomes the run's identity never saw.
The adapter halves of this regression (a store that really grows) live
with each venue adapter's own tests.
"""

from dskit.pipeline.base import OutputsConfig
from dskit.pipeline.document import NodeSpec
from dskit.pipeline.driver import run_document
from dskit.pipeline.node import Node
from tests.pipeline.dochelpers import (
    banking_document,
    banking_pipeline,
    make_registry,
)

ASOF = "2026-01-01"


class PinProbeLabels(Node):
    """Reports whether ``run()`` was served by the instance whose
    ``fingerprint()`` the driver hashed into the run identity."""

    role = "labels"
    outputs = ("outcomes", "metrics")

    def fingerprint(self):
        self._fingerprinted = True
        return {"probe": 1}

    def run(self, ctx, inputs):
        return {
            "outcomes": {e["contract"]: e["settled_yes"] for e in inputs["events"]},
            "metrics": {
                "served_by_fingerprinted_instance": int(
                    getattr(self, "_fingerprinted", False)
                )
            },
        }


def test_execute_reuses_the_fingerprinted_labels_instance(tmp_path):
    pipeline = banking_pipeline()
    pipeline["labels"] = NodeSpec(
        uses="tests.pipeline.test_labels_pinning:PinProbeLabels",
        inputs=pipeline["labels"].inputs,
    )
    doc = banking_document(
        pipeline=pipeline, outputs=OutputsConfig(run_root=str(tmp_path))
    )
    result = run_document(doc, asof=ASOF, registry=make_registry())
    assert result.node_states["labels"] == "ok"
    metrics = result.outputs["labels"]["metrics"]
    assert metrics["served_by_fingerprinted_instance"] == 1

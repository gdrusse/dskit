"""The child's node kinds under the TOOLKIT'S conformance bar, plus one
end-to-end run of ``configs/run-sample.json``.

The suite is the same one dskit holds its own kinds to — a child gets it
for the price of a probe per kind. The registry handed in is the child's
explicit ``NODE_KINDS`` table, never ``DEFAULT_NODE_KINDS`` (the global
registry's content is import-order dependent; a bar over it is not a
bar). ``probes()`` resets the in-memory sample table first: the
conformance checks mutate it in place (that is how they prove the
fingerprint tracks content), and every test must start from the pristine
store.
"""

import os
from dataclasses import replace

from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import load_document

import yourproject.nodes as nodes
from yourproject.nodes import NODE_KINDS

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_DOC = os.path.join(CHILD_ROOT, "configs", "run-sample.json")

#: The independent role census — cross-checked against the classes so a
#: mislabelled role cannot silently exit its checks.
EXPECTED_ROLES = {
    "yourproject-sample": "data",
    "yourproject-enrich": "transform",
}

#: The table as shipped — what every probe restores before its test runs.
_PRISTINE = [dict(row) for row in nodes.SAMPLE_ROWS]


def probes(tmp_path):
    """One NodeProbe per kind, over the pristine in-memory table.

    ``move()`` rewrites content IN PLACE (same rows, new values) and
    ``grow()`` appends — the two ways a real store changes between
    resolve and execute, which is exactly what the suite simulates.
    """
    nodes.SAMPLE_ROWS[:] = [dict(row) for row in _PRISTINE]

    def move():
        nodes.SAMPLE_ROWS[0]["value"] = nodes.SAMPLE_ROWS[0]["value"] + 1.25

    def grow():
        nodes.SAMPLE_ROWS.append(
            {"id": f"sample-{len(nodes.SAMPLE_ROWS) + 1:04d}",
             "day": "2026-01-09", "value": 12.25}
        )

    return {
        "yourproject-sample": NodeProbe(
            params={},
            make=lambda: nodes.SampleRecords("sample", {}),
            move=move,
            grow=grow,
            size=lambda out: len(out["records"]),
            runnable=True,
        ),
        "yourproject-enrich": NodeProbe(
            params={"factor": 2.0},
            required=("factor",),
            inputs={"records": [
                {"id": "r-1", "value": 4.0},
                {"id": "r-2", "value": 0.5},
            ]},
            stream_ports=("records",),
            runnable=True,
        ),
    }


TestConformance = conformance_suite(
    registry=NODE_KINDS,
    module="yourproject.nodes",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestConformance",
)


def test_run_sample_document_end_to_end(tmp_path):
    """configs/run-sample.json through the same entry the CLI uses
    (``run`` calls ``run_document``), with ``outputs.run_root`` overridden
    to a tmp dir — placement is not identity, so the swap is lawful."""
    document = load_document(RUN_DOC)
    document = replace(document, outputs=OutputsConfig(run_root=str(tmp_path)))
    result = run_document(document, asof="2026-01-01")
    assert result.state == "ran", (result.state, result.error)
    assert result.node_states == {"sample": "ok", "enrich": "ok"}
    enriched = result.outputs["enrich"]["records"]
    assert enriched, "the enrich node returned no records"
    assert all(row["derived"] == row["value"] * 2.0 for row in enriched), enriched
    # The run dir recorded it: the human report and the machine record.
    assert os.path.isfile(os.path.join(result.run_dir, "report.md"))
    assert os.path.isfile(os.path.join(result.run_dir, "result.json"))

"""Packet 7 slice 1 — the ReplayRun tape-pair descriptor grammar.

A node resolved as the generic ``ReplayRun`` consumer declares exactly two
canonical top-level inputs, ``tape_manifest`` and ``tape_data``, each a
complete legal ``$captured_artifact`` descriptor. Missing, extra, aliased,
nested, or wrongly-named ports refuse, and the replay kind is execution-only.
"""

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.node import Node
from dskit.pipeline.planner import plan


class _CustomReplayNode(Node):
    """A non-owned class that illegally declares the replay role."""

    role = "replay"
    outputs = ("result",)

    def run(self, ctx, inputs):
        return {}


def _descriptor(**overrides):
    descriptor = {
        "root_ref": "release://forecast/v42",
        "snapshot_version": "42",
        "document_sha256": "a" * 64,
        "node": "pit_bundle",
        "output": "bundle",
        "purpose": "synthetic",
    }
    descriptor.update(overrides)
    return descriptor


def _execution_backtest():
    return {
        "schema_version": "dskit.execution-backtest/v1",
        "purpose": "synthetic",
        "event_envelope_schema": "dskit.event-envelope/v2",
        "source_rank_policy_sha256": "1" * 64,
        "execution_profile_sha256": "2" * 64,
        "environment_identity_sha256": "3" * 64,
    }


def _replay_inputs(manifest=None, data=None):
    inputs = {}
    if manifest is not None:
        inputs["tape_manifest"] = {"$captured_artifact": manifest}
    if data is not None:
        inputs["tape_data"] = {"$captured_artifact": data}
    return inputs


def _document(pipeline, execution=True):
    doc = {"name": "replay-tape", "pipeline": pipeline}
    if execution:
        doc["execution_backtest"] = _execution_backtest()
    return doc


def _pair():
    return _replay_inputs(manifest=_descriptor(), data=_descriptor())


def test_replay_node_with_exact_pair_parses():
    doc = _document({"replay": {"uses": "replay", "inputs": _pair()}})
    parsed = PipelineDocument.from_obj(doc)
    assert set(parsed.pipeline["replay"].inputs) == {"tape_manifest", "tape_data"}


@pytest.mark.parametrize(
    "inputs",
    [
        _replay_inputs(manifest=_descriptor()),  # tape_manifest only
        _replay_inputs(data=_descriptor()),  # tape_data only
        {},  # neither
    ],
)
def test_replay_node_missing_pair_refuses(inputs):
    doc = _document({"replay": {"uses": "replay", "inputs": inputs}})
    with pytest.raises(ConfigError):
        PipelineDocument.from_obj(doc)


def test_replay_node_extra_input_refuses():
    inputs = _pair()
    inputs["extra"] = {"$captured_artifact": _descriptor()}
    doc = _document({"replay": {"uses": "replay", "inputs": inputs}})
    with pytest.raises(ConfigError):
        PipelineDocument.from_obj(doc)


def test_replay_node_aliased_port_refuses():
    doc = _document(
        {
            "replay": {
                "uses": "replay",
                "inputs": _replay_inputs(
                    manifest=_descriptor(), data=_descriptor()
                ),
            }
        }
    )
    doc["pipeline"]["replay"]["inputs"]["tape"] = doc["pipeline"]["replay"][
        "inputs"
    ].pop("tape_data")
    with pytest.raises(ConfigError):
        PipelineDocument.from_obj(doc)


def test_replay_node_nested_descriptor_refuses():
    doc = _document(
        {
            "replay": {
                "uses": "replay",
                "inputs": {
                    "tape_manifest": {"$captured_artifact": _descriptor()},
                    "tape_data": {"$captured_artifact": _descriptor()},
                },
            }
        }
    )
    doc["pipeline"]["replay"]["inputs"]["tape_data"] = {
        "$captured_artifact": [_descriptor()]
    }
    with pytest.raises(ConfigError):
        PipelineDocument.from_obj(doc)


def test_replay_kind_requires_execution_document():
    doc = _document(
        {"replay": {"uses": "replay", "inputs": _pair()}}, execution=False
    )
    with pytest.raises(ConfigError):
        PipelineDocument.from_obj(doc)


def test_non_replay_node_with_tape_ports_is_not_swept():
    doc = _document(
        {
            "consumer": {
                "uses": "synthetic-frame",
                "inputs": _pair(),
            }
        }
    )
    parsed = PipelineDocument.from_obj(doc)
    assert "consumer" in parsed.pipeline


def test_replay_identity_is_one_name():
    from dskit.pipeline import ReplayRun
    from dskit.pipeline.document import REPLAY_RUN_KIND
    from dskit.pipeline.node import DEFAULT_NODE_KINDS

    assert ReplayRun.role == "replay"
    assert REPLAY_RUN_KIND == "replay"
    kind_cls, owned = DEFAULT_NODE_KINDS.get("replay")
    assert kind_cls is ReplayRun
    assert owned is True


def test_replay_run_refuses_without_the_broker():
    from dskit.pipeline import ReplayRun

    node = ReplayRun("replay", {})
    with pytest.raises(RuntimeError):
        node.run(None, {})


def test_custom_replay_role_class_ref_refuses_as_non_owned():
    doc = _document(
        {
            "source": {"uses": "synthetic-frame"},
            "rp": {
                "uses": "test_replay_tape_descriptors:_CustomReplayNode",
                "inputs": {
                    "tape_manifest": "$source.out",
                    "tape_data": "$source.out",
                },
            },
        },
        execution=False,
    )
    parsed = PipelineDocument.from_obj(doc)
    with pytest.raises(ConfigError):
        plan(parsed)


def test_replay_kind_in_foreach_template_refuses():
    doc = _document(
        {"source": {"uses": "synthetic-frame"}}, execution=False
    )
    doc["foreach"] = {
        "keys": ["a", "b"],
        "pipeline": {
            "t": {
                "uses": "replay",
                "inputs": {
                    "tape_manifest": "$source.out",
                    "tape_data": "$source.out",
                },
            }
        },
    }
    with pytest.raises(ConfigError):
        PipelineDocument.from_obj(doc)



"""Packet 5 — the captured-artifact descriptor grammar and its only legal location.

The governing ADR (ADR-0123, "Captured ports and enforced multi-run stages")
freezes the exact descriptor ``{"$captured_artifact": {root_ref,
snapshot_version, document_sha256, node, output, purpose}}`` as valid ONLY as
the complete value of one declared node input in an execution_backtest
document. Everywhere else — params, tracking sinks, stages, foreach templates,
outputs, a ``$prev`` default, artifact, lists/maps, or any nested location —
and every misshapen descriptor refuses before planning, provider, broker,
node import, or output creation.
"""

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import PipelineDocument


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


def _document(sections):
    base = {
        "name": "captured-grammar",
        "pipeline": {"source": {"uses": "synthetic-frame"}},
    }
    base.update(sections)
    return base


def _captured_input_document(descriptor):
    return _document(
        {
            "pipeline": {
                "consume": {
                    "uses": "synthetic-frame",
                    "inputs": {"bundle": {"$captured_artifact": descriptor}},
                }
            },
            "execution_backtest": _execution_backtest(),
        }
    )


def test_captured_descriptor_parses_as_complete_node_input():
    descriptor = _descriptor()
    document = PipelineDocument.from_obj(_captured_input_document(descriptor))
    assert document.pipeline["consume"].inputs["bundle"] == {
        "$captured_artifact": descriptor
    }


def test_ordinary_document_refuses_captured_descriptor_input():
    with pytest.raises(ConfigError, match="execution_backtest"):
        PipelineDocument.from_obj(
            _document(
                {
                    "pipeline": {
                        "consume": {
                            "uses": "synthetic-frame",
                            "inputs": {"bundle": {"$captured_artifact": _descriptor()}},
                        }
                    }
                }
            )
        )


def test_captured_descriptor_in_params_refused():
    with pytest.raises(ConfigError, match=r"\$captured_artifact"):
        PipelineDocument.from_obj(
            _document(
                {
                    "pipeline": {
                        "consume": {
                            "uses": "synthetic-frame",
                            "params": {"x": {"$captured_artifact": _descriptor()}},
                        }
                    },
                    "execution_backtest": _execution_backtest(),
                }
            )
        )


@pytest.mark.parametrize(
    "sections",
    (
        {"outputs": {"report": {"$captured_artifact": "x"}}},
        {
            "tracking": {
                "sinks": [{"kind": "local", "params": {"$captured_artifact": "x"}}]
            }
        },
        {
            "stages": {
                "first": {
                    "uses": "synthetic-frame",
                    "params": {"$captured_artifact": "x"},
                }
            }
        },
        {
            "foreach": {
                "keys": ["one"],
                "pipeline": {
                    "template": {
                        "uses": "synthetic-frame",
                        "inputs": {"bundle": {"$captured_artifact": _descriptor()}},
                    }
                },
            }
        },
        {
            "pipeline": {
                "consume": {
                    "uses": "synthetic-frame",
                    "params": {
                        "carry": {"$prev": "source.value", "default": {"$captured_artifact": _descriptor()}}
                    },
                }
            }
        },
    ),
)
def test_captured_descriptor_in_forbidden_location_refused(sections):
    with pytest.raises(ConfigError, match=r"\$captured_artifact"):
        PipelineDocument.from_obj(_document(sections))


def test_captured_descriptor_nested_in_list_refused():
    with pytest.raises(ConfigError, match=r"\$captured_artifact"):
        PipelineDocument.from_obj(
            _document(
                {
                    "pipeline": {
                        "consume": {
                            "uses": "synthetic-frame",
                            "params": {"list": [{"$captured_artifact": _descriptor()}]},
                        }
                    }
                }
            )
        )


@pytest.mark.parametrize(
    ("descriptor", "match"),
    (
        (_descriptor(root_ref=None), "non-empty"),
        (_descriptor(snapshot_version=""), "non-empty"),
        (_descriptor(node=3), "non-empty"),
        (_descriptor(output=[]), "non-empty"),
        (_descriptor(purpose=""), "non-empty"),
        (_descriptor(document_sha256="A" * 64), "SHA-256"),
        (_descriptor(document_sha256="abc"), "SHA-256"),
        (_descriptor(document_sha256="0" * 64), "placeholder"),
        (_descriptor(document_sha256="self"), "SHA-256"),
        (_descriptor(root_ref="$other.output"), r"\$"),
        (_descriptor(extra="forged"), "unknown"),
        (_descriptor(consumer_document_sha256="c" * 64), "unknown"),
    ),
)
def test_captured_descriptor_misshapen_value_refused(descriptor, match):
    with pytest.raises(ConfigError, match=match):
        PipelineDocument.from_obj(_captured_input_document(descriptor))


def test_captured_descriptor_missing_key_refused():
    descriptor = _descriptor()
    descriptor.pop("node")
    with pytest.raises(ConfigError, match="missing"):
        PipelineDocument.from_obj(_captured_input_document(descriptor))


def test_captured_descriptor_nested_capture_refused():
    with pytest.raises(ConfigError, match="unknown"):
        PipelineDocument.from_obj(
            _captured_input_document(
                {"$captured_artifact": _descriptor()}
            )
        )


def test_captured_descriptor_value_must_be_an_object():
    with pytest.raises(ConfigError, match="object"):
        PipelineDocument.from_obj(
            _document(
                {
                    "pipeline": {
                        "consume": {
                            "uses": "synthetic-frame",
                            "inputs": {"bundle": {"$captured_artifact": "not-a-dict"}},
                        }
                    },
                    "execution_backtest": _execution_backtest(),
                }
            )
        )


def test_captured_descriptor_must_be_the_complete_input_value():
    with pytest.raises(ConfigError, match="complete"):
        PipelineDocument.from_obj(
            _document(
                {
                    "pipeline": {
                        "consume": {
                            "uses": "synthetic-frame",
                            "inputs": {
                                "bundle": {
                                    "$captured_artifact": _descriptor(),
                                    "extra": 1,
                                }
                            },
                        }
                    },
                    "execution_backtest": _execution_backtest(),
                }
            )
        )


def test_ordinary_document_round_trips_byte_identical():
    obj = {
        "name": "ordinary-round-trip",
        "pipeline": {"source": {"uses": "synthetic-frame"}},
    }
    document = PipelineDocument.from_obj(obj)
    assert document.to_obj()["pipeline"]["source"]["uses"] == "synthetic-frame"
    assert "execution_backtest" not in document.to_obj()

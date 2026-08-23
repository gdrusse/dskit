"""certify.py: decisions, the block gate, refusals as evidence."""

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    Rule,
    ValidationSuite,
    certify,
    run_acquisition,
    run_suite,
)

from .fake_connector import FakeConnector, record


@pytest.fixture
def snapshot(root, registry, fake_source):
    FakeConnector.script = [record("prices", "2026-01-02", {"close": 10.5}),
                            record("prices", "2026-01-03", {"close": None})]
    return run_acquisition(root, registry, "fake", "prices", "live")["snapshot"]


def _result(root, registry, snapshot, gating):
    """A validation_result with the requested gating, honestly earned."""
    rules = {
        "pass": Rule(id="r", target="prices", rule="bitemporal"),
        "warn": Rule(id="r", target="prices", rule="not_null",
                     kwargs={"field": "close"}, severity="warn"),
        "block": Rule(id="r", target="prices", rule="not_null",
                      kwargs={"field": "close"}),
    }[gating]
    out = run_suite(root, registry, ValidationSuite(name=gating, rules=(rules,)),
                    snapshot)
    assert out["gating"] == gating
    return out["result"]


def test_certify_pass_and_warn(root, registry, snapshot):
    for gating in ("pass", "warn"):
        vid = certify(registry, _result(root, registry, snapshot, gating),
                      "certified", certified_by="gibson")
        rec = registry.get(vid)
        assert rec.payload["decision"] == "certified"
        assert rec.refs["snapshot"] == snapshot


def test_block_cannot_be_certified(root, registry, snapshot):
    result = _result(root, registry, snapshot, "block")
    with pytest.raises(AssetError, match="BLOCK"):
        certify(registry, result, "certified")


def test_refusal_is_a_record_even_over_block(root, registry, snapshot):
    result = _result(root, registry, snapshot, "block")
    vid = certify(registry, result, "refused", certified_by="gibson")
    assert registry.get(vid).payload["decision"] == "refused"


def test_decision_vocabulary_closed(root, registry, snapshot):
    result = _result(root, registry, snapshot, "pass")
    with pytest.raises(AssetError, match="decision"):
        certify(registry, result, "approved")


def test_wrong_kind_refused(registry, snapshot):
    with pytest.raises(AssetError, match="not a validation_result"):
        certify(registry, snapshot, "certified")


def test_same_decision_same_identity(root, registry, snapshot):
    result = _result(root, registry, snapshot, "pass")
    a = certify(registry, result, "certified", certified_by="gibson")
    b = certify(registry, result, "certified", certified_by="gibson")
    assert a == b  # idempotent — reuse before duplication

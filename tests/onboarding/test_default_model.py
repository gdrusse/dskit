"""default_model.py: the ratified hash pin + parity with the architecture doc.

Editing the embedded model means updating BOTH the pin here and
``docs/architecture/onboarding-model.json`` in the same commit,
deliberately — the model is ratified design (ADR-0012…0016), not code
to drift.
"""

import pathlib

from dskit.assets.model import load_model, model_hash
from dskit.onboarding import onboarding_model

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC_MODEL = REPO_ROOT / "docs" / "architecture" / "onboarding-model.json"

#: The ratified identity (see docs/RE-ENTRY.md and ADR-0012…0015).
PINNED_HASH = "a877590373caeb4a0928199ceefa2a5b6a2131caa1c34ddeb764d2514c597465"


def test_hash_is_pinned():
    assert model_hash(onboarding_model()) == PINNED_HASH


def test_parity_with_architecture_document():
    assert model_hash(onboarding_model()) == model_hash(load_model(str(DOC_MODEL)))


def test_topology_is_the_design():
    m = onboarding_model()
    assert sorted(m.kinds) == [
        "acquisition_job", "certification", "published_version",
        "snapshot", "source_config", "validation_result",
    ]
    # Only config is governed; evidence is record-only.
    assert [k for k, ks in sorted(m.kinds.items()) if ks.states] == ["source_config"]
    # Mode is first-class on jobs and snapshots (ADR-0014).
    assert m.kinds["acquisition_job"].fields["mode"].required
    assert m.kinds["snapshot"].fields["mode"].required
    # The certification chain is required refs end to end (ADR-0015).
    assert m.kinds["snapshot"].refs["job"].required
    assert m.kinds["certification"].refs["result"].required
    assert m.kinds["certification"].refs["snapshot"].required
    assert m.kinds["published_version"].refs["certification"].required


def test_source_config_lifecycle_is_linear():
    ks = onboarding_model().kinds["source_config"]
    assert ks.states == ("draft", "active", "retired")
    assert ks.initial == "draft"
    assert ks.transitions == {"draft": ("active",), "active": ("retired",)}

"""default_model.py: the governance pin, and the spec's rules as topology.

The PIN TEST is the point (ADR-0007): governance is exactly as strong as
the model hash, so any edit to what the default model permits must show
up as a deliberate diff RIGHT HERE, alongside the change.
"""

from dskit.assets import default_model, model_hash
from dskit.assets.model import AssetModel

#: The default model's identity. If you changed the model ON PURPOSE,
#: update this pin in the same commit and say why in the message.
DEFAULT_MODEL_HASH = "176ed570f70219233543f6fd2330f11222e651ea155947e97a37e78c819ad684"


def test_governance_pin():
    assert model_hash(default_model()) == DEFAULT_MODEL_HASH


def test_twelve_kinds_plus_native_lineage_is_the_specs_thirteen():
    assert sorted(default_model().kinds) == [
        "artifact", "data_product", "dataset", "dataset_version",
        "entity", "feature", "feature_set", "feature_version",
        "output", "run_observation", "source", "target",
    ]


def test_spec_governance_topology():
    m = default_model()
    # "Features belong to entities" — required ref.
    assert m.kinds["feature"].refs["entity"].required
    # "Targets are joined to features only in run artifacts" — no feature ref.
    assert "feature" not in {r.kind for r in m.kinds["target"].refs.values()}
    # "Every certified dataset is traceable to a source" — required chain.
    assert m.kinds["dataset"].refs["source"].required
    assert m.kinds["dataset_version"].refs["dataset"].required
    # "Observe execution" — observations are record-only.
    for kind in ("run_observation", "artifact", "output"):
        assert m.kinds[kind].states == ()
    # Artifacts and outputs never float free of their run.
    assert m.kinds["artifact"].refs["run"].required
    assert m.kinds["output"].refs["run"].required


def test_spec_lifecycle_on_governed_kinds():
    m = default_model()
    ds = m.kinds["dataset"]
    assert ds.states == ("draft", "validated", "certified",
                        "published", "deprecated", "retired")
    assert ds.initial == "draft"
    # Strictly the spec's arrow chain — no skips declared.
    assert ds.transitions == {
        "draft": ("validated",), "validated": ("certified",),
        "certified": ("published",), "published": ("deprecated",),
        "deprecated": ("retired",),
    }


def test_round_trips_like_any_user_model():
    m = default_model()
    assert model_hash(AssetModel.from_obj(m.to_obj())) == model_hash(m)

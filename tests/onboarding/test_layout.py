"""layout.py: create-once, refusal of the uninitialized, path discipline."""

import os

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import OnboardingRoot


def test_create_builds_the_whole_estate(tmp_path):
    ob = OnboardingRoot.create(str(tmp_path / "ob"))
    for sub in ("store", "raw", "observations", "forecasts", "state", "published"):
        assert os.path.isdir(os.path.join(ob.root, sub)), sub
    assert ob.registry().model.name == "onboarding"


def test_create_exactly_once(tmp_path):
    OnboardingRoot.create(str(tmp_path / "ob"))
    with pytest.raises(AssetError, match="exactly once"):
        OnboardingRoot.create(str(tmp_path / "ob"))


def test_open_uninitialized_refused(tmp_path):
    with pytest.raises(AssetError, match="not an initialized onboarding root"):
        OnboardingRoot(str(tmp_path / "nope"))


def test_registry_pin_enforced_for_custom_models(tmp_path, root):
    # A root pinned to the onboarding model refuses the P1 default model.
    from dskit.assets import default_model

    with pytest.raises(AssetError, match="does not.*match the store's pin"):
        root.registry(default_model())


def test_path_helpers_compose_the_documented_layout(root):
    assert root.raw_dir("vendor").endswith(os.path.join("raw", "vendor"))
    assert root.snapshot_dir("vendor", "A").endswith(os.path.join("raw", "vendor", "A"))
    assert root.records_dir("vendor", "A").endswith(
        os.path.join("observations", "vendor", "A"))
    assert root.records_dir("vendor", "A", forecasts=True).endswith(
        os.path.join("forecasts", "vendor", "A"))
    assert root.state_path("vendor", "prices", "backfill").endswith(
        os.path.join("state", "vendor", "prices-backfill.json"))
    assert root.published_dir("prices-daily").endswith(
        os.path.join("published", "prices-daily"))


def test_unsafe_segments_refused_everywhere(root):
    with pytest.raises(AssetError):
        root.raw_dir("../escape")
    with pytest.raises(AssetError):
        root.state_path("ok", "Bad Stream", "live")
    with pytest.raises(AssetError):
        root.state_path("ok", "stream", "sideways")  # not a declared mode
    with pytest.raises(AssetError):
        root.published_dir("UPPER")

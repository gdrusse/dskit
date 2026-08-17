"""BackendRegistry mechanics — fail-loud both ways, adapter-side loading."""

import pytest

from dskit.pipeline.registry import BackendRegistry
from dskit.pipeline.testing import SyntheticBackend


class _Cfg:
    class data:
        venue = "nowhere"


class TestRegistry:
    def test_register_and_create(self, registry, synthetic_config):
        backend = registry.create(synthetic_config())
        assert isinstance(backend, SyntheticBackend)
        assert "synthetic" in registry
        assert registry.venues() == ("synthetic",)

    def test_unknown_venue_names_the_fix(self, registry):
        with pytest.raises(ValueError) as exc:
            registry.create(_Cfg())
        msg = str(exc.value)
        assert "'nowhere'" in msg and "synthetic" in msg
        assert "import" in msg  # the registration rule is in the message

    def test_duplicate_registration_refused(self, registry):
        with pytest.raises(ValueError, match="already registered"):
            registry.register("synthetic", SyntheticBackend)

    def test_bad_registration_inputs(self):
        reg = BackendRegistry()
        with pytest.raises(ValueError, match="non-empty string"):
            reg.register("", SyntheticBackend)
        with pytest.raises(ValueError, match="callable"):
            reg.register("v", "not a factory")

    def test_registries_are_isolated(self, registry):
        assert "synthetic" not in BackendRegistry()

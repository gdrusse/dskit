"""Config file I/O + the example inputs that must never rot."""

import json
import pathlib

import pytest

from dskit.pipeline import ConfigError, PipelineConfig
from dskit.pipeline.io import load_config, save_config

EXAMPLES = pathlib.Path(__file__).parents[2] / "examples" / "pipeline"


@pytest.fixture
def cfg_path(synthetic_config, tmp_path):
    path = tmp_path / "cfg.json"
    save_config(synthetic_config(), path)
    return path


class TestRoundTrip:
    def test_save_then_load_preserves_identity(self, synthetic_config, cfg_path):
        cfg = synthetic_config()
        loaded = load_config(cfg_path)
        assert loaded == cfg and loaded.hash == cfg.hash

    def test_saved_form_is_canonical_json(self, cfg_path):
        doc = json.loads(cfg_path.read_text())
        assert list(doc) == sorted(doc)  # sorted keys: diffable documents


class TestFailLoud:
    def test_broken_json_names_the_path(self, tmp_path):
        bad = tmp_path / "broken.json"
        bad.write_text("{not json")
        with pytest.raises(ValueError, match="broken.json is not valid JSON"):
            load_config(bad)

    def test_schema_violation_names_the_path(self, cfg_path):
        doc = json.loads(cfg_path.read_text())
        doc["splits"]["kind"] = "walk-forward"
        cfg_path.write_text(json.dumps(doc))
        with pytest.raises(ConfigError) as exc:
            load_config(cfg_path)
        assert "cfg.json" in str(exc.value) and "known kinds" in str(exc.value)

    def test_missing_file_is_oserror(self, tmp_path):
        with pytest.raises(OSError):
            load_config(tmp_path / "nope.json")

    def test_unknown_adapter_module_fails(self, cfg_path):
        with pytest.raises(ModuleNotFoundError):
            load_config(cfg_path, adapters=("dskit.no_such_adapter",))


class TestExampleInputs:
    def test_synthetic_example_loads_and_hashes(self):
        cfg = load_config(EXAMPLES / "synthetic.json")
        assert cfg.data.venue == "synthetic"
        assert cfg.hpo is None  # optional section exercised as null
        assert len(cfg.hash) == 64

    # (The stage-list "every section populated" canonical example was
    #  venue-specific and is not shipped by the standalone toolkit.)

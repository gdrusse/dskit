"""The security/env reference, outputs placement, and model.mode —
credential values must be structurally unable to leak."""

import json
import os

import pytest

from dskit.pipeline.base import (
    NON_IDENTITY_SECTIONS,
    ConfigError,
    EnvConfig,
    ModelConfig,
    OutputsConfig,
    SinkConfig,
    TrackingConfig,
    _strip_non_identity,
)
from dskit.pipeline.env import Secrets, load_env
from dskit.pipeline.resolve import pipeline_hash, resolve
from dskit.pipeline.runner import Runner


class TestEnvConfigShape:
    def test_valid_and_round_trip(self):
        cfg = EnvConfig(env_file=".env", require=("API_KEY_ID",))
        assert EnvConfig.from_obj(cfg.to_obj()) == cfg

    def test_bad_names_refused(self):
        with pytest.raises(ConfigError, match="not valid environment"):
            EnvConfig(require=("GOOD_NAME", "BAD-NAME", "1LEADING"))
        with pytest.raises(ConfigError, match="env.env_file"):
            EnvConfig(env_file="")


class TestEnvFileLoading:
    def test_parsing_precedence_and_requirements(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comment\n"
            "\n"
            "PLAIN=value\n"
            "export EXPORTED=yes\n"
            'QUOTED="with = signs"\n'
            "OVERRIDDEN=from-file\n"
        )
        monkeypatch.setenv("OVERRIDDEN", "from-process")
        secrets = load_env(
            EnvConfig(env_file=str(env_file), require=("PLAIN", "EXPORTED"))
        )
        assert secrets["PLAIN"] == "value"
        assert secrets["EXPORTED"] == "yes"
        assert secrets["QUOTED"] == "with = signs"
        assert secrets["OVERRIDDEN"] == "from-process"  # process env wins

    def test_missing_required_names_all_listed(self, tmp_path):
        cfg = EnvConfig(
            env_file=str(tmp_path / "absent.env"),
            require=("NO_SUCH_VAR_A", "NO_SUCH_VAR_B"),
        )
        with pytest.raises(ValueError) as exc:
            load_env(cfg)
        msg = str(exc.value)
        assert "NO_SUCH_VAR_A" in msg and "NO_SUCH_VAR_B" in msg
        assert "file not present" in msg

    def test_malformed_line_names_file_and_lineno(self, tmp_path):
        bad = tmp_path / ".env"
        bad.write_text("JUST A LINE\n")
        with pytest.raises(ValueError, match=r"\.env:1"):
            load_env(EnvConfig(env_file=str(bad)))

    def test_none_config_is_process_env_only(self, monkeypatch):
        monkeypatch.setenv("SOME_VAR_XYZ", "v")
        assert load_env(None)["SOME_VAR_XYZ"] == "v"


class TestSecretsRedaction:
    def test_values_do_not_display_or_serialize(self):
        secrets = Secrets({"KEY": "hunter2"})
        assert "hunter2" not in repr(secrets)
        assert "hunter2" not in str(secrets)
        assert list(secrets) == ["KEY"]  # iteration yields names only
        with pytest.raises(TypeError):
            json.dumps(secrets)
        assert secrets["KEY"] == "hunter2"  # ...but lookups work
        assert secrets.get("NOPE") is None and "KEY" in secrets
        with pytest.raises(KeyError, match="env.require"):
            secrets["NOPE"]


class TestNonIdentitySections:
    def test_env_and_outputs_never_move_the_hash(
        self, registry, synthetic_config, tmp_path
    ):
        base = synthetic_config()
        with_env = synthetic_config(
            env=EnvConfig(env_file=str(tmp_path / "x.env"), require=())
        )
        with_out = synthetic_config(
            outputs=OutputsConfig(run_root=str(tmp_path / "elsewhere"))
        )
        assert base.hash == with_env.hash == with_out.hash
        r1, _ = resolve(base, asof="2026-01-01", registry=registry)
        r2, _ = resolve(with_out, asof="2026-01-01", registry=registry)
        assert pipeline_hash(r1) == pipeline_hash(r2)  # placement != identity

    def test_tracking_never_moves_the_hash_either(self, registry, synthetic_config):
        """WHERE metrics land is placement too (Ruling 1).

        ``tracking`` names the sink a run's telemetry goes to — the same
        kind of fact as ``outputs``, and none of what the run COMPUTES.
        It joined the exclusion list LATE, though, so it is excluded by
        being rendered UNDECLARED rather than by having its key removed
        (``NULLED_IDENTITY_SECTIONS``): every hash ever written already
        counted a ``"tracking": null``, and dropping the key would have
        moved all of them.
        """
        base = synthetic_config()
        tracked = synthetic_config(
            tracking=TrackingConfig(sinks=(SinkConfig(kind="memory"),))
        )
        assert "tracking" in NON_IDENTITY_SECTIONS
        assert base.hash == tracked.hash
        # ...and the KEY survived, as null — the byte-for-byte reason no
        # existing hash moved when the section became non-identity.
        assert _strip_non_identity(tracked.to_obj())["tracking"] is None
        r1, _ = resolve(base, asof="2026-01-01", registry=registry)
        r2, _ = resolve(tracked, asof="2026-01-01", registry=registry)
        assert pipeline_hash(r1) == pipeline_hash(r2)

    def test_run_root_redirects_artifacts(self, registry, synthetic_config, tmp_path):
        cfg = synthetic_config(
            outputs=OutputsConfig(run_root=str(tmp_path / "elsewhere"))
        )
        resolved, _ = resolve(cfg, asof="2026-01-01", registry=registry)
        assert resolved.run_dir.startswith(str(tmp_path / "elsewhere"))

    def test_resolve_checks_required_env(self, registry, synthetic_config, tmp_path):
        cfg = synthetic_config(
            env=EnvConfig(
                env_file=str(tmp_path / "absent.env"), require=("NO_SUCH_VAR_Q",)
            )
        )
        with pytest.raises(ValueError, match="NO_SUCH_VAR_Q"):
            resolve(cfg, asof="2026-01-01", registry=registry)

    def test_ctx_secrets_facade(
        self, registry, synthetic_config, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("SYN_TOKEN", "sekrit")
        cfg = synthetic_config(
            env=EnvConfig(env_file=str(tmp_path / ".env"), require=("SYN_TOKEN",))
        )
        runner = Runner(cfg, asof="2026-01-01", registry=registry)
        result = runner.run()
        # no artifact anywhere contains the value
        for root, _, files in os.walk(result.run_dir):
            for name in files:
                assert "sekrit" not in open(os.path.join(root, name)).read()


class TestModelMode:
    def test_shape_rules(self):
        with pytest.raises(ConfigError, match="mode must be"):
            ModelConfig(name="m", model_dir="/m", mode="infer")
        with pytest.raises(ConfigError, match="artifact is required"):
            ModelConfig(name="m", model_dir="/m", mode="load")
        with pytest.raises(ConfigError, match="config lie"):
            ModelConfig(name="m", model_dir="/m", mode="train", artifact="x.pt")
        cfg = ModelConfig(name="m", model_dir="/m", mode="load", artifact="v3/model.pt")
        assert ModelConfig.from_obj(cfg.to_obj()) == cfg

    def test_mode_and_artifact_are_identity(self, synthetic_config, tmp_path):
        trained = synthetic_config()
        loaded = synthetic_config(
            model=ModelConfig(
                name="true-q",
                model_dir=str(tmp_path / "models"),
                params={"n_events": 60},
                mode="load",
                artifact="true-q/v1",
            )
        )
        assert trained.hash != loaded.hash

    def test_runner_load_path(self, registry, synthetic_config, tmp_path):
        cfg = synthetic_config(
            model=ModelConfig(
                name="true-q",
                model_dir=str(tmp_path / "models"),
                params={"n_events": 60},
                mode="load",
                artifact="true-q/v1",
            )
        )
        result = Runner(cfg, asof="2026-01-01", registry=registry).run()
        assert "loaded model" in result.stages[0].verdict
        assert "inference-only" in result.stages[0].verdict
        assert result.stages[0].payload["mode"] == "load"
        assert len(result.survivors) >= 1  # same informed signal, same edge

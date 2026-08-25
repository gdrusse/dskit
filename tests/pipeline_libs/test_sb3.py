"""The tier-2 sb3 pack (ADR-0028): declared algo/env, artifact protocol,
policy restore, episode evaluation, conformance."""

from __future__ import annotations

import json
import math
import os
import pathlib

import pytest

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs.sb3 import (
    ARTIFACT_FORMAT,
    NODE_KINDS,
    Sb3Eval,
    Sb3Policy,
    Sb3Train,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry

gymnasium = pytest.importorskip("gymnasium")
pytest.importorskip("stable_baselines3")

import numpy as np  # noqa: E402 - after the importorskips, like torch's pattern


class TinyEnv(gymnasium.Env):
    """A 4-step continuous toy: reward is highest when the action tracks
    the first observation coordinate. Deterministic given a reset seed."""

    def __init__(self, episode_len=4):
        self.episode_len = int(episode_len)
        self.observation_space = gymnasium.spaces.Box(
            low=-1.0, high=1.0, shape=(2,), dtype=np.float32
        )
        self.action_space = gymnasium.spaces.Box(
            low=-1.0, high=1.0, shape=(1,), dtype=np.float32
        )
        self._t = 0

    def _obs(self):
        return self.np_random.uniform(-1.0, 1.0, size=2).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._t = 0
        return self._obs(), {}

    def step(self, action):
        self._t += 1
        obs = self._obs()
        reward = -float(abs(float(action[0]) - float(obs[0])))
        return obs, reward, self._t >= self.episode_len, False, {}


ENV_REF = "tests.pipeline_libs.test_sb3:TinyEnv"

#: A fast, valid train param set — PPO with a tiny net and 32 timesteps.
PARAMS = {
    "algo": "PPO",
    "policy": "MlpPolicy",
    "env": ENV_REF,
    "env_params": {"episode_len": 4},
    "total_timesteps": 32,
    "seed": 7,
    "algo_params": {
        "n_steps": 16,
        "batch_size": 16,
        "n_epochs": 1,
        "policy_kwargs": {"net_arch": [4]},
    },
}


def ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


def train(tmp_path, *, sub="run", params=None):
    node = Sb3Train("agent", dict(params if params is not None else PARAMS))
    return node.run(ctx(tmp_path, sub), {})


def sidecar_path(artifact_path):
    return os.path.splitext(artifact_path)[0] + ".json"


OBS = np.array([0.5, -0.25], dtype=np.float32)


# -- params: default-deny + value checks ---------------------------------------


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"warm_start": True}, "warm_start"),
        ({"algo": None}, "algo is required"),
        ({"algo": "not a name"}, "algo"),
        ({"env": None}, "env is required"),
        ({"env": "no-ref"}, "env"),
        ({"policy": ""}, "policy"),
        ({"total_timesteps": None}, "total_timesteps"),
        ({"total_timesteps": 0}, "total_timesteps"),
        ({"seed": -1}, "seed"),
        ({"env_params": "wide"}, "env_params"),
        ({"algo_params": [1]}, "algo_params"),
    ],
)
def test_train_param_validation(override, needle):
    params = {**PARAMS, **override}
    params = {k: v for k, v in params.items() if v is not None}
    problems = Sb3Train.validate_params(params)
    assert any(needle in p for p in problems)


def test_reference_params_validate_clean():
    assert Sb3Train.validate_params(dict(PARAMS)) == []
    assert Sb3Policy.validate_params({}) == []
    assert (
        Sb3Eval.validate_params({"split": "val", "env": ENV_REF, "n_episodes": 2})
        == []
    )


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({"artifact": ""}, "artifact"),
        ({"algo": "bad name"}, "algo"),
        ({"env": "no-ref"}, "env"),
    ],
)
def test_policy_param_validation(params, needle):
    assert any(needle in p for p in Sb3Policy.validate_params(params))


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"n_episodes": 0}, "n_episodes"),
        ({"deterministic": "yes"}, "deterministic"),
        ({"env_params": 3}, "env_params"),
        ({"extra": 1}, "extra"),
    ],
)
def test_eval_param_validation(override, needle):
    problems = Sb3Eval.validate_params({"split": "val", **override})
    assert any(needle in p for p in problems)


# -- training: contract, artifact, policy --------------------------------------


def test_train_returns_the_contract_and_a_verifiable_artifact(tmp_path):
    out = train(tmp_path)
    assert set(out) == {"policy", "artifact_path", "metrics"}
    assert out["metrics"] == {"total_timesteps": 32, "seed": 7}
    artifact = out["artifact_path"]
    assert os.path.isfile(artifact)
    sidecar = json.loads(
        pathlib.Path(sidecar_path(artifact)).read_text(encoding="utf-8")
    )
    assert sidecar["format"] == ARTIFACT_FORMAT
    assert sidecar["algo"] == "PPO"
    assert sidecar["env"] == ENV_REF
    assert sidecar["env_params"] == {"episode_len": 4}
    assert sidecar["seed"] == 7
    action = out["policy"].act(OBS)
    assert action.shape == (1,)
    assert math.isfinite(float(action[0]))
    assert out["policy"].loaded is False


def test_unknown_algo_and_non_env_class_refuse_by_name(tmp_path):
    with pytest.raises(ValueError, match="not exported"):
        train(tmp_path, params={**PARAMS, "algo": "NoSuchAlgo"})
    bad_env = {**PARAMS, "env": "dskit.pipeline.node:Node"}
    with pytest.raises(ValueError, match="gymnasium.Env"):
        train(tmp_path, params=bad_env)
    bad_kwargs = {**PARAMS, "env_params": {"episode_len": 4, "bogus": 1}}
    with pytest.raises(ValueError, match="rejected env_params"):
        train(tmp_path, params=bad_kwargs)


# -- load/restore --------------------------------------------------------------


def test_train_mode_load_restores_and_the_policy_acts_identically(tmp_path):
    trained = train(tmp_path)
    artifact = trained["artifact_path"]
    node = Sb3Train("agent", dict(PARAMS), mode="load", artifact=artifact)
    out = node.run(ctx(tmp_path, "load-run"), {})
    assert out["metrics"] == {"loaded": 1, "seed": 7}
    assert out["policy"].loaded is True
    assert np.allclose(out["policy"].act(OBS), trained["policy"].act(OBS))


def test_policy_kind_loads_via_wire_params_and_mode(tmp_path):
    trained = train(tmp_path)
    artifact = trained["artifact_path"]
    by_wire = Sb3Policy("pi", {}).run(
        ctx(tmp_path, "p1"), {"artifact_path": artifact}
    )
    assert by_wire["policy"].loaded is True
    assert np.allclose(by_wire["policy"].act(OBS), trained["policy"].act(OBS))
    by_params = Sb3Policy("pi", {"artifact": artifact}).run(ctx(tmp_path, "p2"), {})
    assert by_params["policy"].loaded is True
    pinned = Sb3Policy("pi", {}, mode="load", artifact=artifact)
    assert pinned.run(ctx(tmp_path, "p3"), {})["policy"].loaded is True


def refuses(node, tmp_path, match, inputs=None):
    with pytest.raises(ValueError, match=match):
        node.run(ctx(tmp_path, "refusal-run"), inputs or {})


def test_policy_refuses_mode_train_and_missing_references(tmp_path):
    with pytest.raises(NotImplementedError, match="inference-only"):
        Sb3Policy("pi", {}, mode="train").run(ctx(tmp_path, "t"), {})
    refuses(Sb3Policy("pi", {}), tmp_path, "no artifact reference")
    refuses(
        Sb3Policy("pi", {}, mode="load", artifact=""),
        tmp_path,
        "empty artifact reference",
    )


def test_load_refuses_missing_files_tampering_and_mismatches(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    refuses(
        Sb3Policy("pi", {"artifact": str(tmp_path / "nope.zip")}),
        tmp_path,
        "does not exist",
    )
    # Sidecar gone.
    other = train(tmp_path, sub="b")["artifact_path"]
    os.remove(sidecar_path(other))
    refuses(Sb3Policy("pi", {"artifact": other}), tmp_path, "sidecar")
    # Content tamper: the hash covers the zip bytes.
    with open(artifact, "ab") as fh:
        fh.write(b"tampered")
    refuses(Sb3Policy("pi", {"artifact": artifact}), tmp_path, "hash mismatch")


def test_load_cross_checks_declared_algo_env_and_policy(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    refuses(
        Sb3Policy("pi", {"artifact": artifact, "algo": "SAC"}),
        tmp_path,
        "mismatch on 'algo'",
    )
    refuses(
        Sb3Policy(
            "pi", {"artifact": artifact, "env": "dskit.pipeline.node:Node"}
        ),
        tmp_path,
        "mismatch on 'env'",
    )


def test_sidecar_identity_edits_break_the_recorded_hash(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    path = pathlib.Path(sidecar_path(artifact))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["seed"] = 999  # unrehashed edit
    path.write_text(json.dumps(data), encoding="utf-8")
    refuses(Sb3Policy("pi", {"artifact": artifact}), tmp_path, "hash mismatch")


# -- evaluation ----------------------------------------------------------------


def test_eval_reports_episode_metrics_from_the_sidecar_env(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    node = Sb3Eval("score", {"split": "val", "n_episodes": 2, "seed": 3})
    out = node.run(ctx(tmp_path, "eval"), {"artifact_path": artifact})
    metrics = out["metrics"]
    assert set(metrics) == {"mean_reward", "std_reward", "n_episodes"}
    assert metrics["n_episodes"] == 2
    assert math.isfinite(metrics["mean_reward"])
    assert metrics["std_reward"] >= 0.0
    # TinyEnv rewards are -|action - obs| over 4 steps: bounded in [-8, 0].
    assert -8.0 <= metrics["mean_reward"] <= 0.0


class ClosingEnv(TinyEnv):
    """TinyEnv that records every close() — the leak probe."""

    closes = []

    def close(self):
        type(self).closes.append(True)
        super().close()


def test_train_closes_the_env_even_when_construction_fails(tmp_path):
    """The skeptic finding: a failed construction/learn must not leak the
    child environment's resources — the env closes on every exit."""
    ClosingEnv.closes = []
    params = {
        **PARAMS,
        "env": "tests.pipeline_libs.test_sb3:ClosingEnv",
        "algo_params": {"bogus_knob": 1},
    }
    with pytest.raises(ValueError, match="rejected its construction"):
        train(tmp_path, params=params)
    assert ClosingEnv.closes == [True]


def test_eval_pins_algo_and_policy_against_the_sidecar(tmp_path):
    """The skeptic finding: the eval's algo/policy pin was dead — the
    names were absent from its allowed params, so the cross-check could
    never fire. Declaring them now pins the model identity."""
    artifact = train(tmp_path)["artifact_path"]
    node = Sb3Eval("score", {"split": "val", "n_episodes": 1, "algo": "SAC"})
    refuses(node, tmp_path, "mismatch on 'algo'", {"artifact_path": artifact})
    ok = Sb3Eval(
        "score",
        {"split": "val", "n_episodes": 1, "algo": "PPO", "policy": "MlpPolicy"},
    )
    out = ok.run(ctx(tmp_path, "pinned-eval"), {"artifact_path": artifact})
    assert math.isfinite(out["metrics"]["mean_reward"])


def test_eval_can_declare_its_own_env_and_refuses_without_artifact(tmp_path):
    artifact = train(tmp_path)["artifact_path"]
    node = Sb3Eval(
        "score",
        {
            "split": "val",
            "n_episodes": 1,
            "env": ENV_REF,
            "env_params": {"episode_len": 2},
        },
    )
    out = node.run(ctx(tmp_path, "eval"), {"artifact_path": artifact})
    assert out["metrics"]["mean_reward"] >= -4.0  # 2 steps, bounded
    refuses(
        Sb3Eval("score", {"split": "val"}), tmp_path, "no artifact reference"
    )


# -- the shipped example (validate-only: the env is rightly the child's) -------


def test_shipped_example_loads_and_hashes_stably():
    from dskit.pipeline.document import load_document

    example = (
        pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "sb3-train.json"
    )
    doc = load_document(str(example))
    assert doc.name == "sb3-train-demo"
    assert tuple(doc.pipeline) == ("agent", "score")
    assert doc.hash == load_document(str(example)).hash


# -- registration and conformance ----------------------------------------------


def test_register_is_explicit_and_idempotent():
    registry = NodeKindRegistry()
    register(registry)
    register(registry)
    assert registry.kinds() == ("sb3-eval", "sb3-policy", "sb3-train")
    assert registry.get("sb3-train") == (Sb3Train, False)
    assert registry.get("sb3-policy") == (Sb3Policy, False)
    assert registry.get("sb3-eval") == (Sb3Eval, False)


EXPECTED_ROLES = {
    "sb3-train": "train",
    "sb3-policy": "signal",
    "sb3-eval": "score",
}


def probes(tmp_path):
    fixture = train(tmp_path, sub="fixture-run")
    artifact = fixture["artifact_path"]
    expected = fixture["policy"].act(OBS)

    def restored(out):
        policy = out["policy"]
        return (
            bool(getattr(policy, "loaded", False))
            and policy.artifact_path == artifact
            and np.allclose(policy.act(OBS), expected)
        )

    return {
        "sb3-train": NodeProbe(
            params=dict(PARAMS),
            required=("algo", "env", "total_timesteps"),
            inputs={},
            stream_ports=(),
            runnable=True,
            load_artifact=artifact,
            verify_loaded=lambda out: (
                restored(out) and out["artifact_path"] == artifact
            ),
        ),
        "sb3-policy": NodeProbe(
            params={},
            inputs={"artifact_path": artifact},
            stream_ports=(),
            runnable=True,
            load_artifact=artifact,
            verify_loaded=restored,
        ),
        "sb3-eval": NodeProbe(
            params={"split": "val", "n_episodes": 1},
            required=("split",),
            inputs={"artifact_path": artifact},
            stream_ports=(),
            runnable=True,
        ),
    }


TestSb3Conformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.sb3",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestSb3Conformance",
)

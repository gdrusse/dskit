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
    Sb3Eval,
    Sb3EvalEpisodes,
    Sb3Policy,
    Sb3Train,
    StructuredDecisionEnv,
    register,
)
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry, TrainableNode

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
    # ADR-0034: the fourth split name is lawful for an eval reader too.
    assert (
        Sb3Eval.validate_params({"split": "cal", "env": ENV_REF, "n_episodes": 2})
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


def test_only_the_two_trainable_kinds_carry_the_dispatch(tmp_path):
    """ADR-0038's sb3 rule: the shared base is NOT re-parented, because
    the eval kind (role 'score') inherits it and carries no mode at all.
    The services it needs sit on Node, reachable without the dispatch."""
    for cls in (Sb3Train, Sb3Policy):
        assert issubclass(cls, TrainableNode)
        assert cls.run is TrainableNode.run
        assert cls.validate_inputs is TrainableNode.validate_inputs
    assert not issubclass(Sb3Eval, TrainableNode)
    assert Sb3Eval.pinned_artifact is Node.pinned_artifact
    assert Sb3Eval.pin_port_problems is Node.pin_port_problems


def test_policy_refuses_a_node_level_pin_that_contradicts_the_param(tmp_path):
    """ADR-0038's declared delta: under mode='load' a contradicting
    params['artifact'] is refused rather than silently outranked."""
    artifact = train(tmp_path)["artifact_path"]
    refuses(
        Sb3Policy("pi", {"artifact": str(tmp_path / "other.zip")}, mode="load",
                  artifact=artifact),
        tmp_path,
        "disagree",
    )
    agreeing = Sb3Policy("pi", {"artifact": artifact}, mode="load", artifact=artifact)
    assert agreeing.run(ctx(tmp_path, "agree"), {})["policy"].loaded is True


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
    assert registry.kinds() == (
        "sb3-eval", "sb3-eval-episodes", "sb3-policy", "sb3-train",
    )
    assert registry.get("sb3-train") == (Sb3Train, False)
    assert registry.get("sb3-policy") == (Sb3Policy, False)
    assert registry.get("sb3-eval") == (Sb3Eval, False)
    assert registry.get("sb3-eval-episodes") == (Sb3EvalEpisodes, False)


#: The three kinds THIS file's conformance suite owns — spelled out rather
#: than read off the live ``NODE_KINDS``. The table gained a fourth kind
#: (``sb3-eval-episodes``, ADR-0160) whose probe and role census belong to
#: ``test_sb3_eval_episodes.py``, which needs neither library; reading the
#: live table here would drag that kind into this file's PPO-backed
#: ``probes()`` instead, where it has no probe and no reason to be.
LEGACY_NODE_KINDS = (
    ("sb3-train", Sb3Train),
    ("sb3-policy", Sb3Policy),
    ("sb3-eval", Sb3Eval),
)

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
    registry=LEGACY_NODE_KINDS,
    module="dskit.pipeline.libs.sb3",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestSb3Conformance",
)

from dskit.pipeline.libs.sb3 import FiniteDecisionEnv

class ToyFiniteEnv(FiniteDecisionEnv):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.closes = self.finishes = 0
        self.candidates = ["cash", "risk"]
    def _start_episode(self, rng):
        return [float(rng.random())], self.candidates, {"start": 1}
    def _finish_episode(self, action):
        self.finishes += 1
        return 4.0, {"done": 1}
    def _close_episode(self):
        self.closes += 1

def test_finite_decision_env_one_step_and_cash_alias():
    env = ToyFiniteEnv(max_actions=3, max_features=2, reward_scale=2)
    obs, info = env.reset(seed=4)
    assert obs.shape == (3,) and obs[0] == 2 and obs[2] == 0
    assert info == {"start": 1}
    _, reward, term, trunc, info = env.step(2)
    assert (reward, term, trunc) == (2, True, False)
    assert info == {"done": 1, "requested_action": 2, "effective_action": 0, "cash_alias": True}
    assert env.finishes == 1
    with pytest.raises(RuntimeError):
        env.step(0)

def test_finite_decision_env_rejects_invalid_action_and_resets_seed():
    env = ToyFiniteEnv(max_actions=2, max_features=2, reward_scale=1)
    a, _ = env.reset(seed=7)
    b, _ = env.reset(seed=7)
    assert np.array_equal(a, b) and env.closes == 1
    with pytest.raises(ValueError):
        env.step(0.5)
    with pytest.raises(ValueError):
        env.step(2)

def test_finite_decision_env_validates_bounds():
    with pytest.raises(ValueError):
        ToyFiniteEnv(max_actions=True, max_features=2, reward_scale=1)
    with pytest.raises(ValueError):
        ToyFiniteEnv(max_actions=2, max_features=2, reward_scale=float("inf"))


def test_finite_decision_env_failed_reset_and_close_release_episode():
    class BadEnv(ToyFiniteEnv):
        def _start_episode(self, rng):
            return [float("inf")], ["cash"], {"x": 1}

    env = BadEnv(max_actions=2, max_features=1, reward_scale=1)
    with pytest.raises(ValueError, match="finite"):
        env.reset(seed=3)
    assert env.closes == 1
    env.close()
    env.close()
    assert env.closes == 1


def test_finite_decision_env_finish_failure_invalidates_and_closes():
    class FailedFinish(ToyFiniteEnv):
        def _finish_episode(self, action):
            raise RuntimeError("finish failed")

    env = FailedFinish(max_actions=2, max_features=1, reward_scale=1)
    env.reset(seed=3)
    with pytest.raises(RuntimeError, match="finish failed"):
        env.step(1)
    assert env.closes == 1
    with pytest.raises(RuntimeError):
        env.step(0)

def test_finite_decision_env_snapshots_mutable_action_list():
    env = ToyFiniteEnv(max_actions=2, max_features=1, reward_scale=1)
    env.reset(seed=8)
    env.candidates[1] = "changed"
    assert env.actions == ("cash", "risk")


def test_finite_decision_env_deep_copies_nested_actions():
    class NestedActions(ToyFiniteEnv):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.candidate = {"legs": [1]}

        def _start_episode(self, rng):
            return [0.0], ["cash", self.candidate], {}

        def _finish_episode(self, action):
            action["legs"].append(2)
            return 1.0, {}

    env = NestedActions(max_actions=2, max_features=1, reward_scale=1)
    env.reset(seed=9)
    env.step(1)
    assert env.candidate == {"legs": [1]}
    assert env.actions[1] == {"legs": [1]}


def test_finite_decision_env_rejects_unsupported_reset_options():
    env = ToyFiniteEnv(max_actions=2, max_features=1, reward_scale=1)
    with pytest.raises(ValueError, match="options"):
        env.reset(seed=2, options={"cohort": "hidden"})


def test_finite_decision_env_refuses_float32_overflow_without_warning():
    import warnings

    class OverflowContext(ToyFiniteEnv):
        def _start_episode(self, rng):
            return [1e100], ["cash"], {}

    env = OverflowContext(max_actions=2, max_features=1, reward_scale=1)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError, match="float32"):
            env.reset(seed=2)


class ToyStructuredEnv(StructuredDecisionEnv):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.submitted = []

    def _start_episode(self, rng):
        return [float(rng.random())], [2, 1, 0], {"start": 1}

    def _finish_episode(self, quantities):
        self.submitted.append(tuple(quantities))
        return sum(quantities), {"submitted": list(quantities)}


def test_structured_decision_env_reaches_full_vector_and_auto_finishes():
    env = ToyStructuredEnv(
        max_coordinates=3, max_quantity=2, max_features=2,
        reward_scale=2, max_edits=3, feature_transform="identity",
    )
    obs, info = env.reset(seed=4)
    assert info == {"start": 1}
    assert env.action_space.nvec.tolist() == [2, 3, 3]
    assert obs.shape == env.observation_space.shape
    assert obs[1] == 3
    assert obs[4:7].tolist() == [2.0, 1.0, 0.0]
    assert obs[0] == 1 and obs[2] > 0 and obs[3] == 0
    _, reward, terminated, truncated, _ = env.step(np.array([0, 0, 2], dtype=np.int64))
    assert (reward, terminated, truncated) == (0, False, False)
    assert env.submitted == []
    _, reward, terminated, truncated, _ = env.step(np.array([0, 1, 1], dtype=np.int64))
    assert (reward, terminated, truncated) == (0, False, False)
    _, reward, terminated, truncated, info = env.step(
        np.array([0, 2, 0], dtype=np.int64)
    )
    assert (reward, terminated, truncated) == (1.5, True, False)
    assert env.submitted[-1] == (2, 1, 0)
    assert info["termination_reason"] == "max_edits"
    assert info["submitted_quantities"] == [2, 1, 0]


@pytest.mark.parametrize(
    "action, reason",
    [([0, 2, 1], "capacity_exceeded"), ([0, 3, 0], "padded_coordinate")],
)
def test_structured_decision_env_invalid_proposal_submits_cash(action, reason):
    env = ToyStructuredEnv(
        max_coordinates=4, max_quantity=2, max_features=1,
        reward_scale=1, max_edits=4, feature_transform="identity",
    )
    env.reset(seed=2)
    _, reward, terminated, truncated, info = env.step(action)
    assert (reward, terminated, truncated) == (0, True, False)
    assert env.submitted[-1] == (0, 0, 0)
    assert info["proposal_valid"] is False
    assert info["invalid_reason"] == reason


def test_structured_decision_env_training_diagnostics_are_cumulative_and_read_only():
    env = ToyStructuredEnv(
        max_coordinates=3, max_quantity=2, max_features=1,
        reward_scale=1, max_edits=3, feature_transform="identity",
    )
    empty = {
        "completed_episodes": 0,
        "proposed_cash_episodes": 0,
        "invalid_bounds_episodes": 0,
        "total_edits": 0,
        "terminal_reward_sum": 0.0,
    }
    assert env.training_diagnostics() == empty

    env.reset(seed=1)
    env.step([0, 0, 1])
    env.step([1, 2, 0])
    env.reset(seed=2)
    env.step([1, 0, 0])
    env.reset(seed=3)
    env.step([0, 1, 2])  # over capacity: cash is actually submitted
    diagnostics = env.training_diagnostics()
    assert diagnostics == {
        "completed_episodes": 3,
        "proposed_cash_episodes": 2,
        "invalid_bounds_episodes": 1,
        "total_edits": 1,
        "terminal_reward_sum": 1.0,
    }
    diagnostics["completed_episodes"] = 0
    env.close()
    assert env.training_diagnostics()["completed_episodes"] == 3


def test_structured_decision_env_ppo_train_eval_roundtrip(tmp_path):
    env_params = {
        "max_coordinates": 3,
        "max_quantity": 2,
        "max_features": 1,
        "reward_scale": 1,
        "max_edits": 3,
        "feature_transform": "signed_log1p",
    }
    env_ref = "tests.pipeline_libs.test_sb3:ToyStructuredEnv"
    trainer = Sb3Train("train", {
        "algo": "PPO",
        "env": env_ref,
        "env_params": env_params,
        "total_timesteps": 8,
        "seed": 5,
        "algo_params": {"n_steps": 4, "batch_size": 4, "n_epochs": 1, "device": "cpu"},
    })
    trained = trainer.run_train(ctx(tmp_path), {})
    trained_env = trained["policy"].model.get_env().envs[0].unwrapped
    diagnostics = trained_env.training_diagnostics()
    assert diagnostics["completed_episodes"] == len(trained_env.submitted)
    assert diagnostics["completed_episodes"] > 0
    assert diagnostics["proposed_cash_episodes"] <= diagnostics["completed_episodes"]
    assert diagnostics["invalid_bounds_episodes"] <= diagnostics["completed_episodes"]
    assert diagnostics["total_edits"] >= 0
    assert math.isfinite(diagnostics["terminal_reward_sum"])
    evaluator = Sb3Eval("eval", {
        "artifact": trained["artifact_path"],
        "split": "test",
        "n_episodes": 1,
    })
    scored = evaluator.run(ctx(tmp_path, "eval"), {})
    assert math.isfinite(scored["metrics"]["mean_reward"])
    assert math.isfinite(scored["metrics"]["std_reward"])


@pytest.mark.parametrize("quantities", [(a, b, 0) for a in range(3) for b in range(2)])
def test_structured_decision_env_reaches_every_bounded_vector(quantities):
    env = ToyStructuredEnv(
        max_coordinates=3, max_quantity=2, max_features=1,
        reward_scale=1, max_edits=3, feature_transform="identity",
    )
    env.reset(seed=8)
    for coordinate, quantity in enumerate(quantities):
        _, reward, terminated, truncated, _ = env.step(
            np.array([0, coordinate, quantity], dtype=np.int64)
        )
        assert reward == (sum(quantities) if coordinate == 2 else 0)
        assert terminated is (coordinate == 2)
        assert truncated is False
    assert env.submitted[-1] == quantities


@pytest.mark.parametrize("action", [[0, 0, 1.0], [False, 0, 0], [0, 0], [0, 3, 0]])
def test_structured_decision_env_rejects_malformed_or_out_of_space_action(action):
    env = ToyStructuredEnv(
        max_coordinates=3, max_quantity=2, max_features=1,
        reward_scale=1, max_edits=3, feature_transform="identity",
    )
    env.reset(seed=9)
    with pytest.raises(ValueError):
        env.step(action)


@pytest.mark.parametrize("capacities", [[True, 1], [1.0, 1], [-1, 1], [3, 1]])
def test_structured_decision_env_rejects_malformed_capacities(capacities):
    class BadCapacities(ToyStructuredEnv):
        def _start_episode(self, rng):
            return [0.0], capacities, {}

    env = BadCapacities(
        max_coordinates=2, max_quantity=2, max_features=1,
        reward_scale=1, max_edits=2, feature_transform="identity",
    )
    with pytest.raises(ValueError, match="capacities"):
        env.reset(seed=3)


def test_structured_decision_env_finish_submits_zero_vector_immediately():
    env = ToyStructuredEnv(
        max_coordinates=4, max_quantity=2, max_features=1,
        reward_scale=1, max_edits=4, feature_transform="identity",
    )
    env.reset(seed=3)
    _, reward, terminated, truncated, info = env.step([1, 3, 2])
    assert reward == 0
    assert terminated and not truncated
    assert env.submitted[-1] == (0, 0, 0)
    assert info["termination_reason"] == "finish"


def test_structured_decision_env_applies_declared_signed_log_transform():
    class LargeFeature(ToyStructuredEnv):
        def _start_episode(self, rng):
            return [100.0], [2, 1, 0], {}

    env = LargeFeature(
        max_coordinates=3, max_quantity=2, max_features=1,
        reward_scale=1, max_edits=3, feature_transform="signed_log1p",
    )
    obs, _ = env.reset(seed=1)
    assert obs[2] == pytest.approx(np.log1p(100.0))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_coordinates": 0, "max_quantity": 1, "max_features": 1, "reward_scale": 1, "max_edits": 1, "feature_transform": "identity"},
        {"max_coordinates": 2, "max_quantity": 1, "max_features": 1, "reward_scale": 1, "max_edits": 1, "feature_transform": "identity"},
        {"max_coordinates": 1, "max_quantity": 1, "max_features": 1, "reward_scale": 1, "max_edits": 1, "feature_transform": "zscore"},
    ],
)
def test_structured_decision_env_validates_dimensions_and_transform(kwargs):
    with pytest.raises(ValueError):
        ToyStructuredEnv(**kwargs)


class CompatibleEvaluationEnv(gymnasium.Env):
    @classmethod
    def validate_evaluation_params(cls, training_params, evaluation_params):
        problems = []
        for key in ("method", "schema", "dataset_hash", "source_hash"):
            if training_params.get(key) != evaluation_params.get(key):
                problems.append(f"{key} differs")
        return problems


class AlternateEvaluationEnv(gymnasium.Env):
    pass


COMPAT_ENV_REF = "tests.pipeline_libs.test_sb3:CompatibleEvaluationEnv"
ALTERNATE_ENV_REF = "tests.pipeline_libs.test_sb3:AlternateEvaluationEnv"


def _evaluation_node(node_cls, *, env_ref, env_params, tmp_path):
    params = {
        "artifact": "fake.zip",
        "env": env_ref,
        "env_params": env_params,
        "split": "val",
        "n_episodes": 1,
        "max_episode_steps": 1,
    }
    if node_cls is Sb3Eval:
        params.pop("max_episode_steps")
    return node_cls("eval", params), ctx(tmp_path)


def _prepare_mock_evaluation(monkeypatch, node, sidecar, env_builds):
    monkeypatch.setattr(node, "pinned_artifact", lambda *a, **k: "fake.zip")
    monkeypatch.setattr(node, "_read_sidecar", lambda path: sidecar)
    monkeypatch.setattr(node, "_load_model", lambda *a, **k: object())
    fake_env = type("FakeEnv", (), {
        "reset": lambda self, **kwargs: (OBS, {}),
        "close": lambda self: None,
    })()
    monkeypatch.setattr(
        node, "_build_env",
        lambda ref, params: env_builds.append((ref, params)) or fake_env,
    )
    if isinstance(node, Sb3Eval):
        from stable_baselines3.common import evaluation
        monkeypatch.setattr(evaluation, "evaluate_policy", lambda *a, **k: (1.0, 0.0))
    else:
        def fake_episode(index, env_ref, env_params, model):
            env = node._build_env(env_ref, env_params)
            env.close()
            return {"episode": 0}
        monkeypatch.setattr(node, "_episode", fake_episode)
        monkeypatch.setattr(node, "_summary", lambda episodes: {
            "n_episodes": 1, "mean_return": 1.0, "total_steps": 1,
        })


@pytest.mark.parametrize("node_cls", [Sb3Eval, Sb3EvalEpisodes])
def test_evaluation_cannot_substitute_an_unchecked_env_for_hooked_training(
    node_cls, monkeypatch, tmp_path
):
    training = {"method": "m1", "schema": 2, "dataset_hash": "data-a",
                "source_hash": "source-a", "split": "train"}
    sidecar = {
        "env": COMPAT_ENV_REF, "env_params": training, "algo": "PPO",
        "environment_contract": "validation-hooks/v1",
        "state_hash": "state", "policy": "MlpPolicy", "seed": 7,
    }
    node, run_ctx = _evaluation_node(
        node_cls, env_ref=ALTERNATE_ENV_REF,
        env_params={**training, "split": "val"}, tmp_path=tmp_path,
    )
    env_builds = []
    _prepare_mock_evaluation(monkeypatch, node, sidecar, env_builds)
    with pytest.raises(ValueError, match="same environment"):
        node.run(run_ctx, {})
    assert env_builds == []


@pytest.mark.parametrize("node_cls", [Sb3Eval, Sb3EvalEpisodes])
@pytest.mark.parametrize("changed", ["method", "schema", "dataset_hash", "source_hash"])
def test_evaluation_refuses_changed_training_provenance(
    node_cls, changed, monkeypatch, tmp_path
):
    training = {"method": "m1", "schema": 2, "dataset_hash": "data-a",
                "source_hash": "source-a", "split": "train"}
    evaluation_params = {**training, "split": "val"}
    evaluation_params[changed] = "changed"
    sidecar = {
        "env": COMPAT_ENV_REF, "env_params": training, "algo": "PPO",
        "environment_contract": "validation-hooks/v1",
        "state_hash": "state", "policy": "MlpPolicy", "seed": 7,
    }
    node, run_ctx = _evaluation_node(
        node_cls, env_ref=COMPAT_ENV_REF, env_params=evaluation_params,
        tmp_path=tmp_path,
    )
    env_builds = []
    _prepare_mock_evaluation(monkeypatch, node, sidecar, env_builds)
    with pytest.raises(ValueError, match=changed):
        node.run(run_ctx, {})
    assert env_builds == []


@pytest.mark.parametrize("node_cls", [Sb3Eval, Sb3EvalEpisodes])
def test_evaluation_allows_hook_approved_split_and_legacy_env(
    node_cls, monkeypatch, tmp_path
):
    training = {"method": "m1", "schema": 2, "dataset_hash": "data-a",
                "source_hash": "source-a", "split": "train"}
    allowed = {**training, "split": "val"}
    sidecar = {
        "env": COMPAT_ENV_REF, "env_params": training, "algo": "PPO",
        "environment_contract": "validation-hooks/v1",
        "state_hash": "state", "policy": "MlpPolicy", "seed": 7,
    }
    node, run_ctx = _evaluation_node(
        node_cls, env_ref=COMPAT_ENV_REF, env_params=allowed, tmp_path=tmp_path,
    )
    env_builds = []
    _prepare_mock_evaluation(monkeypatch, node, sidecar, env_builds)
    node.run(run_ctx, {})
    assert len(env_builds) == 1

    legacy_sidecar = {
        "env": ENV_REF, "env_params": {"episode_len": 4}, "algo": "PPO",
        "state_hash": "state", "policy": "MlpPolicy", "seed": 7,
    }
    legacy, legacy_ctx = _evaluation_node(
        node_cls, env_ref=ALTERNATE_ENV_REF, env_params={"scenario": "val"},
        tmp_path=tmp_path,
    )
    legacy_builds = []
    _prepare_mock_evaluation(monkeypatch, legacy, legacy_sidecar, legacy_builds)
    legacy.run(legacy_ctx, {})
    assert legacy_builds == [(ALTERNATE_ENV_REF, {"scenario": "val"})]


@pytest.mark.parametrize("node_cls", [Sb3Eval, Sb3EvalEpisodes])
def test_evaluation_env_split_must_match_declared_score_split(
    node_cls, monkeypatch, tmp_path
):
    training = {"method": "m1", "schema": 2, "dataset_hash": "data-a",
                "source_hash": "source-a", "split": "train"}
    sidecar = {
        "env": COMPAT_ENV_REF, "env_params": training, "algo": "PPO",
        "environment_contract": "validation-hooks/v1",
        "state_hash": "state", "policy": "MlpPolicy", "seed": 7,
    }
    node, run_ctx = _evaluation_node(
        node_cls, env_ref=COMPAT_ENV_REF,
        env_params={**training, "split": "test"}, tmp_path=tmp_path,
    )
    builds = []
    _prepare_mock_evaluation(monkeypatch, node, sidecar, builds)
    with pytest.raises(ValueError, match="does not match declared split"):
        node.run(run_ctx, {})
    assert builds == []


class TrainingGateEnv(gymnasium.Env):
    @classmethod
    def validate_training_params(cls, env_params):
        if env_params.get("split") != "train":
            return ["split must be train"]
        return []


TRAINING_GATE_ENV_REF = "tests.pipeline_libs.test_sb3:TrainingGateEnv"


class FakeTrainingAlgorithm:
    constructed = 0
    learned = 0

    def __init__(self, *args, **kwargs):
        type(self).constructed += 1

    def learn(self, **kwargs):
        type(self).learned += 1


class FakeTrainingEnv:
    closed = 0

    def close(self):
        type(self).closed += 1


def _training_node(env_params, env_ref=TRAINING_GATE_ENV_REF):
    params = {
        "algo": "PPO",
        "env": env_ref,
        "env_params": env_params,
        "total_timesteps": 2,
        "seed": 1,
    }
    return Sb3Train("train", params)


def _prepare_fake_training(monkeypatch, node, env_builds):
    FakeTrainingAlgorithm.constructed = 0
    FakeTrainingAlgorithm.learned = 0
    FakeTrainingEnv.closed = 0
    monkeypatch.setattr(node, "_resolve_algo", lambda key, name: FakeTrainingAlgorithm)
    monkeypatch.setattr(
        node, "_build_env",
        lambda ref, params: env_builds.append((ref, params)) or FakeTrainingEnv(),
    )
    node.saved_specs = []
    monkeypatch.setattr(
        node, "_save_artifact",
        lambda ctx, model, spec: node.saved_specs.append(dict(spec)) or "fake-model.zip",
    )


def test_training_refuses_nontraining_split_before_build_or_learn(
    monkeypatch, tmp_path
):
    node = _training_node({"split": "val"})
    env_builds = []
    _prepare_fake_training(monkeypatch, node, env_builds)
    with pytest.raises(ValueError, match="split must be train"):
        node.run_train(ctx(tmp_path), {})
    assert env_builds == []
    assert FakeTrainingAlgorithm.constructed == 0
    assert FakeTrainingAlgorithm.learned == 0


def test_training_hook_approves_train_split_and_runs(
    monkeypatch, tmp_path
):
    node = _training_node({"split": "train"})
    env_builds = []
    _prepare_fake_training(monkeypatch, node, env_builds)
    result = node.run_train(ctx(tmp_path), {})
    assert len(env_builds) == 1
    assert FakeTrainingAlgorithm.constructed == 1
    assert FakeTrainingAlgorithm.learned == 1
    assert FakeTrainingEnv.closed == 1
    assert result["artifact_path"] == "fake-model.zip"
    assert node.saved_specs[0]["environment_contract"] == "validation-hooks/v1"


def test_training_omits_hook_marker_for_legacy_env(
    monkeypatch, tmp_path
):
    node = _training_node({"episode_len": 1}, env_ref=ENV_REF)
    env_builds = []
    _prepare_fake_training(monkeypatch, node, env_builds)
    node.run_train(ctx(tmp_path), {})
    assert "environment_contract" not in node.saved_specs[0]


@pytest.mark.parametrize("node_cls", [Sb3Eval, Sb3EvalEpisodes])
def test_marked_training_env_without_evaluation_hook_refuses(
    node_cls, monkeypatch, tmp_path
):
    sidecar = {
        "env": ENV_REF, "env_params": {"episode_len": 4}, "algo": "PPO",
        "environment_contract": "validation-hooks/v1",
        "state_hash": "state", "policy": "MlpPolicy", "seed": 7,
    }
    node, run_ctx = _evaluation_node(
        node_cls, env_ref=ENV_REF, env_params={"episode_len": 4},
        tmp_path=tmp_path,
    )
    builds = []
    _prepare_mock_evaluation(monkeypatch, node, sidecar, builds)
    with pytest.raises(ValueError, match="no validate_evaluation_params hook"):
        node.run(run_ctx, {})
    assert builds == []


def test_environment_contract_marker_is_bound_by_sidecar_hash(tmp_path):
    zip_path = tmp_path / "marker.zip"
    zip_path.write_bytes(b"model")
    sidecar = {
        "format": ARTIFACT_FORMAT,
        "algo": "PPO",
        "env": ENV_REF,
        "env_params": {},
        "policy": "MlpPolicy",
        "seed": 1,
        "environment_contract": "validation-hooks/v1",
    }
    sidecar["state_hash"] = Sb3EvalEpisodes._state_hash(str(zip_path), sidecar)
    pathlib.Path(sidecar_path(str(zip_path))).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    node = Sb3EvalEpisodes("eval", {"split": "val", "max_episode_steps": 1})
    assert node._read_sidecar(str(zip_path))["environment_contract"] == "validation-hooks/v1"

    del sidecar["environment_contract"]
    pathlib.Path(sidecar_path(str(zip_path))).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="content hash mismatch"):
        node._read_sidecar(str(zip_path))


MINIMAL_FINITE_ENV_INSTANCES = []


class MinimalFiniteEnv(FiniteDecisionEnv):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        MINIMAL_FINITE_ENV_INSTANCES.append(self)

    def _start_episode(self, rng):
        return [float(rng.random())], ["cash", "risk"], {}

    def _finish_episode(self, selected_action):
        return 1.0, {}

    def _close_episode(self):
        return None


MINIMAL_FINITE_ENV_REF = "tests.pipeline_libs.test_sb3:MinimalFiniteEnv"


def test_sb3_train_accepts_finite_decision_env_factory_child(
    monkeypatch, tmp_path
):
    node = _training_node(
        {"max_actions": 2, "max_features": 1, "reward_scale": 1},
        env_ref=MINIMAL_FINITE_ENV_REF,
    )
    FakeTrainingAlgorithm.constructed = 0
    FakeTrainingAlgorithm.learned = 0
    monkeypatch.setattr(node, "_resolve_algo", lambda key, name: FakeTrainingAlgorithm)
    monkeypatch.setattr(node, "_save_artifact", lambda *a, **k: "fake-model.zip")
    result = node.run_train(ctx(tmp_path), {})
    assert result["artifact_path"] == "fake-model.zip"
    assert isinstance(MINIMAL_FINITE_ENV_INSTANCES[-1], gymnasium.Env)


@pytest.mark.parametrize("node_cls", [Sb3Eval, Sb3EvalEpisodes])
def test_sb3_evaluators_build_finite_decision_env_children(
    node_cls, monkeypatch, tmp_path
):
    params = {
        "env": MINIMAL_FINITE_ENV_REF,
        "env_params": {
            "max_actions": 2, "max_features": 1, "reward_scale": 1,
        },
    }
    if node_cls is Sb3Eval:
        params["n_episodes"] = 1
    else:
        params["max_episode_steps"] = 2
        params["n_episodes"] = 1
    node = node_cls("eval", {"artifact": "fake.zip", "split": "val", **params})
    sidecar = {
        "env": ENV_REF,
        "env_params": {"episode_len": 4},
        "algo": "PPO",
        "policy": "MlpPolicy",
        "seed": 1,
        "state_hash": "state",
    }
    monkeypatch.setattr(node, "pinned_artifact", lambda *a, **k: "fake.zip")
    monkeypatch.setattr(node, "_read_sidecar", lambda path: sidecar)
    if node_cls is Sb3Eval:
        monkeypatch.setattr(node, "_load_model", lambda *a, **k: object())
        from stable_baselines3.common import evaluation
        monkeypatch.setattr(evaluation, "evaluate_policy", lambda *a, **k: (1.0, 0.0))
    else:
        class Policy:
            def predict(self, observation, deterministic=True):
                return 0, None
        monkeypatch.setattr(node, "_load_model", lambda *a, **k: Policy())
    MINIMAL_FINITE_ENV_INSTANCES.clear()
    node.run(ctx(tmp_path), {})
    assert len(MINIMAL_FINITE_ENV_INSTANCES) == 1
    assert isinstance(MINIMAL_FINITE_ENV_INSTANCES[0], gymnasium.Env)


class NonGymFiniteEnv(MinimalFiniteEnv):
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)


def test_build_env_refuses_finite_decision_class_returning_non_gym_object():
    node = Sb3Eval("eval", {"split": "val"})
    with pytest.raises(ValueError, match="not a gymnasium.Env instance"):
        node._build_env(
            "tests.pipeline_libs.test_sb3:NonGymFiniteEnv",
            {"max_actions": 2, "max_features": 1, "reward_scale": 1},
        )

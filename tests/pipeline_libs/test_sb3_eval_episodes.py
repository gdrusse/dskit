"""``sb3-eval-episodes`` (ADR-0148): the auditable per-episode evaluator.

This module imports NEITHER Gymnasium NOR stable-baselines3, and has no
``pytest.importorskip`` — deliberately, and unlike its sibling
``test_sb3.py``, whose module-level skip and PPO fixture exist so
``TestSb3Conformance`` can invoke ``model.learn``. Nothing here trains
anything. The environments and the model are plain-Python stubs, so the
whole file runs on a machine with neither library installed, exactly as
the pack itself plans documents there.

The subject is the EVIDENCE: a bounded manual episode loop whose ordered
per-step record is a durable JSON artifact, and whose flat numeric
aggregates are derived from that same record rather than from anything
the environment asserted along the way.
"""

from __future__ import annotations

import hashlib
import json
import math
import os

import pytest

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.driver import _persist_json_artifacts
from dskit.pipeline.libs.sb3 import (
    ARTIFACT_FORMAT,
    EPISODE_SCHEMA,
    NODE_KINDS,
    Sb3Eval,
    Sb3EvalEpisodes,
    _Sb3Base,
    register,
)
from dskit.pipeline.node import JsonArtifact, NodeContext, NodeKindRegistry
from dskit.pipeline.split_policy import SPLIT_NAMES

#: A valid param set every case below varies one knob of.
PARAMS = {
    "split": "val",
    "env": "tests.pipeline_libs.test_sb3_eval_episodes:StubEnv",
    "env_params": {"steps": 3},
    "n_episodes": 3,
    "max_episode_steps": 500,
    "seed": 17,
    "deterministic": True,
}

#: Removes a key rather than setting it, so "absent" and "declared but
#: wrong" are both expressible.
_DROP = object()


def params(**overrides):
    merged = {**PARAMS, **overrides}
    return {k: v for k, v in merged.items() if v is not _DROP}


class RunWouldRaise(Sb3EvalEpisodes):
    """A concrete stand-in whose only purpose is to be constructible.

    ``validate_inputs`` is an INSTANCE method (unlike the classmethod
    ``validate_params``), so reaching it needs a live node — and
    ``Sb3EvalEpisodes`` cannot be one until its own ``run`` exists. Only
    ``run`` is supplied, and it raises: nothing in this section may
    execute.
    """

    def run(self, ctx, inputs):
        raise AssertionError("the validation slice never runs")


# ---------------------------------------------------------------------------
# The shape of the kind
# ---------------------------------------------------------------------------


def test_the_class_declares_its_role_and_its_two_outputs():
    assert Sb3EvalEpisodes.role == "score"
    assert Sb3EvalEpisodes.outputs == ("metrics", "episodes")


def test_the_declared_knobs_are_exactly_these():
    assert Sb3EvalEpisodes._PARAMS == (
        "algo",
        "artifact",
        "deterministic",
        "env",
        "env_params",
        "max_episode_steps",
        "n_episodes",
        "policy",
        "seed",
        "split",
    )


def test_the_sidecar_cross_check_is_the_evaluators_narrow_one():
    """Measuring on a DIFFERENT environment is the whole point of an
    eval, so ``env``/``env_params`` are exempt — only the model's own
    identity must match the artifact, exactly as ``sb3-eval`` has it."""
    assert Sb3EvalEpisodes._SIDECAR_CHECK == ("algo", "policy")
    assert Sb3Eval._SIDECAR_CHECK == ("algo", "policy")


def test_the_splits_this_kind_narrows_to_are_real_split_names():
    from dskit.pipeline.libs.sb3 import EPISODE_SPLITS

    assert set(EPISODE_SPLITS) < set(SPLIT_NAMES)


# ---------------------------------------------------------------------------
# validate_params — the classmethod alone; nothing is constructed
# ---------------------------------------------------------------------------


def test_the_canonical_params_validate_clean():
    assert Sb3EvalEpisodes.validate_params(params()) == []


def test_the_optional_knobs_may_all_be_omitted():
    assert Sb3EvalEpisodes.validate_params({
        "split": "test", "max_episode_steps": 10,
    }) == []


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"warm_start": True}, "warm_start"),
        ({"n_eval_episodes": 3}, "n_eval_episodes"),
        ({"algo": "not a name"}, "algo"),
        ({"algo": 3}, "algo"),
        ({"policy": ""}, "policy"),
        ({"policy": 3}, "policy"),
        ({"artifact": ""}, "artifact"),
        ({"artifact": 3}, "artifact"),
        ({"env": "no-ref"}, "env"),
        ({"env": 3}, "env"),
        ({"env_params": "wide"}, "env_params"),
        ({"env_params": [1]}, "env_params"),
        ({"env_params": {3: 1}}, "env_params"),
        ({"env_params": {"": 1}}, "env_params"),
        ({"n_episodes": 0}, "n_episodes"),
        ({"n_episodes": True}, "n_episodes"),
        ({"n_episodes": 10_001}, "n_episodes"),
        ({"max_episode_steps": _DROP}, "max_episode_steps"),
        ({"max_episode_steps": 0}, "max_episode_steps"),
        ({"max_episode_steps": True}, "max_episode_steps"),
        ({"max_episode_steps": 1_000_001}, "max_episode_steps"),
        ({"seed": -1}, "seed"),
        ({"seed": True}, "seed"),
        ({"seed": 2 ** 32}, "seed"),
        ({"deterministic": "yes"}, "deterministic"),
        ({"deterministic": 1}, "deterministic"),
    ],
)
def test_each_broken_knob_is_refused_by_name(override, needle):
    problems = Sb3EvalEpisodes.validate_params(params(**override))
    assert any(needle in p for p in problems), problems


# -- the split narrowing, and the legacy kind it does NOT change ------------


@pytest.mark.parametrize("split", ["val", "test"])
def test_a_held_out_split_is_accepted(split):
    assert Sb3EvalEpisodes.validate_params(params(split=split)) == []


@pytest.mark.parametrize("split", ["train", "cal", _DROP, None, "holdout"])
def test_every_other_split_is_refused(split):
    """``episodes`` is durable, persisted evidence of a policy's HELD-OUT
    performance, not a scalar a search may read from any split — evidence
    fitted on ``train`` or drawn from ``cal``'s inner calibration band
    would misrepresent itself as out-of-sample."""
    problems = Sb3EvalEpisodes.validate_params(params(split=split))
    assert any("split" in p for p in problems), problems


@pytest.mark.parametrize("split", sorted(SPLIT_NAMES))
def test_the_legacy_evaluator_still_accepts_every_split_name(split):
    """The narrowing is NEW, not a restatement: ``sb3-eval`` accepts all
    four today and must keep doing so."""
    assert Sb3Eval.validate_params({
        "split": split, "n_episodes": 3, "seed": 17,
    }) == []


# -- the cross-param bounds ------------------------------------------------


def test_the_trace_allocation_bound_is_a_plan_time_refusal():
    """``n_episodes * max_episode_steps`` bounds how many step records a
    run can allocate. Both knobs are plain literal ints with no
    ``$``-reference support, so the check belongs where the whole
    document can still be refused — not at execute, after the expensive
    part."""
    problems = Sb3EvalEpisodes.validate_params(
        params(n_episodes=1_000, max_episode_steps=1_001)
    )
    assert any("1000000" in p.replace(",", "").replace("_", "")
               for p in problems), problems


def test_the_trace_allocation_bound_admits_its_own_ceiling():
    assert Sb3EvalEpisodes.validate_params(
        params(n_episodes=1_000, max_episode_steps=1_000)
    ) == []


def test_the_last_episodes_seed_must_stay_inside_the_32_bit_range():
    """Episode ``i`` resets on ``seed + i``; the last one must still be a
    seed the environment can be given."""
    problems = Sb3EvalEpisodes.validate_params(
        params(seed=2 ** 32 - 2, n_episodes=3, max_episode_steps=2)
    )
    assert any("seed" in p for p in problems), problems


def test_the_last_episodes_seed_may_sit_exactly_on_the_ceiling():
    assert Sb3EvalEpisodes.validate_params(
        params(seed=2 ** 32 - 3, n_episodes=3, max_episode_steps=2)
    ) == []


# ---------------------------------------------------------------------------
# validate_inputs — an instance method, so it needs the stand-in
# ---------------------------------------------------------------------------


def test_an_unwired_artifact_port_is_lawful():
    """The reference may come from the document instead."""
    assert RunWouldRaise("eval", params()).validate_inputs({}) == []


def test_a_wired_artifact_port_is_accepted():
    node = RunWouldRaise("eval", params())
    assert node.validate_inputs({"artifact_path": "runs/x/model.zip"}) == []


@pytest.mark.parametrize("wired", ["", 3, []])
def test_a_wired_artifact_port_that_names_nothing_is_refused(wired):
    node = RunWouldRaise("eval", params())
    problems = node.validate_inputs({"artifact_path": wired})
    assert any("artifact_path" in p for p in problems), problems


# ---------------------------------------------------------------------------
# Registration — in the same change that made the class concrete
# ---------------------------------------------------------------------------


def test_the_pack_registers_the_new_kind_beside_the_three_legacy_ones():
    """``register()`` rejects an abstract class and ``test_sb3.py``'s
    conformance suite parametrizes every registered kind, so the table
    could only be widened once ``run`` existed — which is why this landed
    in the same change, never before it."""
    import dskit.pipeline.libs.sb3 as pack

    assert NODE_KINDS == (
        ("sb3-train", pack.Sb3Train),
        ("sb3-policy", pack.Sb3Policy),
        ("sb3-eval", Sb3Eval),
        ("sb3-eval-episodes", Sb3EvalEpisodes),
    )
    assert "Sb3EvalEpisodes" in pack.__all__
    assert "EPISODE_SCHEMA" in pack.__all__ and "EPISODE_SPLITS" in pack.__all__

    registry = NodeKindRegistry()
    register(registry)
    register(registry)  # idempotent: a present name is skipped, never shadowed
    assert registry.get("sb3-eval-episodes") == (Sb3EvalEpisodes, False)


# ---------------------------------------------------------------------------
# The stubbed world — plain Python, no Gymnasium and no stable-baselines3
# ---------------------------------------------------------------------------

#: What a well-behaved ``reset`` answers.
OK_RESET = ("observation-0", {})


def _answer(value):
    """Return ``value``, or raise it when a fixture installed an exception."""
    if isinstance(value, Exception):
        raise value
    return value


def step(reward=0.5, *, terminated=False, truncated=False, info=None):
    """One well-formed five-item ``step`` return."""
    return ("observation", reward, terminated, truncated, {} if info is None else info)


def ok_script(rewards=(0.25, 0.75)):
    """A script that terminates on its last step."""
    return [
        step(reward, terminated=index == len(rewards) - 1)
        for index, reward in enumerate(rewards)
    ]


class StubEnv:
    """A plain-Python environment: the exact returns a fixture dictated.

    ``script`` is what ``step`` answers in order and ``reset_value`` is
    what ``reset`` answers — both handed in WHOLE, so a malformed shape is
    expressible, which is what most of the fixtures below need. An
    ``Exception`` in either position is raised instead of returned.
    """

    def __init__(self, script=None, reset_value=OK_RESET):
        self.script = list(ok_script() if script is None else script)
        self.reset_value = reset_value
        self.seeds = []
        self.actions = []
        self.closed = 0
        self.ref = None
        self.env_params = None

    def reset(self, *, seed=None):
        self.seeds.append(seed)
        return _answer(self.reset_value)

    def step(self, action):
        self.actions.append(action)
        return _answer(self.script.pop(0) if self.script else step())

    def close(self):
        self.closed += 1


class StubModel:
    """A policy that answers one action and records how it was asked."""

    def __init__(self, action="ACTION"):
        self.action = action
        self.calls = []

    def predict(self, observation, deterministic=True):
        self.calls.append((observation, deterministic))
        return _answer(self.action), "policy-state"


class Lab:
    """The stubbed world: what was built, and what the policy answered."""

    def __init__(self):
        self.envs = []
        self.model = StubModel()
        self.factory = lambda index: StubEnv()
        self.loads = []

    def build_env(self, ref, env_params):
        env = self.factory(len(self.envs))
        env.ref, env.env_params = ref, env_params
        self.envs.append(env)
        return env

    def load_model(self, zip_path, sidecar, *, env=None):
        self.loads.append((zip_path, sidecar, env))
        return self.model

    def scripts(self, *scripts):
        """Give episode ``i`` the ``i``-th script."""
        self.factory = lambda index: StubEnv(scripts[index])


@pytest.fixture(autouse=True)
def lab(monkeypatch):
    """Replace the two library-touching seams for every test in this file.

    ``_load_model`` imports stable-baselines3 and ``_build_env`` imports
    Gymnasium; neither is this file's subject — the episode LOOP is — and
    neither may be imported here. Everything else on the restore path,
    including ``_read_sidecar``'s hash verification, is exercised for real.
    """
    state = Lab()
    monkeypatch.setattr(Sb3EvalEpisodes, "_build_env", state.build_env)
    monkeypatch.setattr(Sb3EvalEpisodes, "_load_model", state.load_model)
    return state


STUB_ENV_REF = "tests.pipeline_libs.test_sb3_eval_episodes:StubEnv"


def artifact(tmp_path, **overrides):
    """A real on-disk ``model.zip`` + verified sidecar — no SB3 involved.

    ``_read_sidecar`` checks existence, JSON shape, the required keys, the
    format tag and the content hash over the zip bytes AND the sidecar.
    Every one of those is answerable without the library, which is why
    this file can exercise the whole verified restore path with neither
    library installed.
    """
    directory = tmp_path / "fixture"
    directory.mkdir(parents=True, exist_ok=True)
    zip_path = directory / "model.zip"
    zip_path.write_bytes(b"opaque bytes; this file is never opened as a model")
    sidecar = {
        "format": ARTIFACT_FORMAT,
        "algo": "PPO",
        "policy": "MlpPolicy",
        "env": STUB_ENV_REF,
        "env_params": {"steps": 3},
        "seed": 7,
        **overrides,
    }
    sidecar["state_hash"] = _Sb3Base._state_hash(str(zip_path), sidecar)
    (directory / "model.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return str(zip_path), sidecar


def ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


def evaluate(tmp_path, **overrides):
    """Run one node over the fixture artifact; answer ``(node, outputs)``."""
    reference, _sidecar = artifact(tmp_path)
    node = Sb3EvalEpisodes("eval", params(**overrides))
    return node, node.run(ctx(tmp_path), {"artifact_path": reference})


def record(outputs):
    """The persisted evidence behind the ``episodes`` port."""
    return outputs["episodes"].value


# ---------------------------------------------------------------------------
# The loop: fresh environments, recorded seeds, unpacked predictions
# ---------------------------------------------------------------------------


def test_each_episode_gets_a_fresh_environment_seeded_from_its_index(
    tmp_path, lab
):
    _node, outputs = evaluate(tmp_path, n_episodes=3, seed=17)

    assert len(lab.envs) == 3
    assert [env.seeds for env in lab.envs] == [[17], [18], [19]]
    assert [episode["seed"] for episode in record(outputs)["episodes"]] == [
        17, 18, 19,
    ]


def test_every_environment_is_closed_exactly_once(tmp_path, lab):
    evaluate(tmp_path, n_episodes=3)
    assert [env.closed for env in lab.envs] == [1, 1, 1]


def test_the_environment_is_built_from_the_declared_ref_and_params(
    tmp_path, lab
):
    evaluate(tmp_path, n_episodes=1, env=STUB_ENV_REF, env_params={"steps": 9})
    assert lab.envs[0].ref == STUB_ENV_REF
    assert lab.envs[0].env_params == {"steps": 9}


def test_an_omitted_environment_defaults_to_the_one_the_model_trained_on(
    tmp_path, lab
):
    """The lawful difference between this and ``sb3-eval``'s sidecar
    cross-check: ``env``/``env_params`` are exempt, so they may DEFAULT
    from the artifact or be deliberately replaced."""
    evaluate(tmp_path, n_episodes=1, env=_DROP, env_params=_DROP)
    assert lab.envs[0].ref == STUB_ENV_REF
    assert lab.envs[0].env_params == {"steps": 3}


def test_the_action_is_unpacked_from_the_models_two_item_predict(
    tmp_path, lab
):
    lab.model = StubModel(action="THE-ACTION")
    evaluate(tmp_path, n_episodes=1)

    assert lab.envs[0].actions == ["THE-ACTION", "THE-ACTION"]
    assert [call[0] for call in lab.model.calls] == ["observation-0", "observation"]


@pytest.mark.parametrize("deterministic", [True, False])
def test_the_deterministic_flag_reaches_predict(tmp_path, lab, deterministic):
    evaluate(tmp_path, n_episodes=1, deterministic=deterministic)
    assert all(call[1] is deterministic for call in lab.model.calls)


# ---------------------------------------------------------------------------
# How an episode ends, and what the aggregate counts
# ---------------------------------------------------------------------------


def test_an_episode_stops_on_terminated(tmp_path, lab):
    lab.scripts([step(1.0, terminated=True), step(9.0)])
    _node, outputs = evaluate(tmp_path, n_episodes=1, max_episode_steps=10)
    episode = record(outputs)["episodes"][0]

    assert episode["steps"] == 1
    assert episode["reason"] == "terminated"
    assert episode["return"] == 1.0


def test_an_episode_stops_on_truncated(tmp_path, lab):
    lab.scripts([step(1.0, truncated=True), step(9.0)])
    _node, outputs = evaluate(tmp_path, n_episodes=1, max_episode_steps=10)
    episode = record(outputs)["episodes"][0]

    assert episode["steps"] == 1
    assert episode["reason"] == "truncated"


def test_an_episode_stops_at_the_step_cap(tmp_path, lab):
    lab.scripts([step(1.0) for _ in range(20)])
    _node, outputs = evaluate(tmp_path, n_episodes=1, max_episode_steps=3)
    episode = record(outputs)["episodes"][0]

    assert episode["steps"] == 3
    assert episode["reason"] == "max_episode_steps"
    assert lab.envs[0].actions == ["ACTION"] * 3


def test_both_flags_true_reads_terminated_and_counts_once_as_terminated(
    tmp_path, lab
):
    """ONE fixture, two claims. Reason precedence alone would pass an
    implementation that summed the raw flags for the aggregate — the
    per-episode ``reason`` still reads ``terminated`` while
    ``truncated_episodes`` silently counted it too, breaking the
    'exclusive' the three counts promise."""
    lab.scripts([step(1.0, terminated=True, truncated=True)])
    _node, outputs = evaluate(tmp_path, n_episodes=1, max_episode_steps=5)
    episode = record(outputs)["episodes"][0]

    assert episode["terminated"] is True and episode["truncated"] is True
    assert episode["trace"][0]["terminated"] is True
    assert episode["trace"][0]["truncated"] is True
    assert episode["reason"] == "terminated"
    assert outputs["metrics"]["terminated_episodes"] == 1
    assert outputs["metrics"]["truncated_episodes"] == 0
    assert outputs["metrics"]["max_episode_steps_episodes"] == 0


def test_the_three_reason_counts_are_exclusive_and_sum_to_the_episodes(
    tmp_path, lab
):
    lab.scripts(
        [step(1.0, terminated=True)],
        [step(1.0, truncated=True)],
        [step(1.0) for _ in range(5)],
    )
    _node, outputs = evaluate(tmp_path, n_episodes=3, max_episode_steps=2)
    metrics = outputs["metrics"]

    assert (metrics["terminated_episodes"],
            metrics["truncated_episodes"],
            metrics["max_episode_steps_episodes"]) == (1, 1, 1)
    assert sum((metrics["terminated_episodes"],
                metrics["truncated_episodes"],
                metrics["max_episode_steps_episodes"])) == metrics["n_episodes"]


def test_the_aggregate_formulas_are_the_declared_ones(tmp_path, lab):
    lab.scripts(
        [step(0.4), step(0.6, terminated=True)],
        [step(2.0, terminated=True)],
        [step(3.0, terminated=True)],
    )
    _node, outputs = evaluate(tmp_path, n_episodes=3, max_episode_steps=5)
    metrics = outputs["metrics"]

    assert [e["return"] for e in record(outputs)["episodes"]] == [1.0, 2.0, 3.0]
    assert metrics["mean_return"] == pytest.approx(2.0)
    # POPULATION std: sqrt(((1-2)^2 + 0 + (3-2)^2) / 3)
    assert metrics["std_return"] == pytest.approx(math.sqrt(2 / 3))
    assert metrics["total_steps"] == 4


# ---------------------------------------------------------------------------
# The evidence: exact schemas, and nothing else
# ---------------------------------------------------------------------------


def test_a_step_record_carries_exactly_these_four_keys(tmp_path, lab):
    lab.scripts([step(0.25, terminated=True)])
    _node, outputs = evaluate(tmp_path, n_episodes=1)
    trace = record(outputs)["episodes"][0]["trace"]

    assert trace == [
        {"step": 1, "reward": 0.25, "terminated": True, "truncated": False}
    ]


def test_a_step_record_keeps_no_observation_action_or_info(tmp_path, lab):
    """Any of the three may be huge, secret, or not JSON at all — the
    child owns whatever richer audit evidence its domain needs."""
    lab.scripts([step(1.0, terminated=True, info={"secret": object()})])
    _node, outputs = evaluate(tmp_path, n_episodes=1)
    assert set(record(outputs)["episodes"][0]["trace"][0]) == {
        "step", "reward", "terminated", "truncated",
    }


def test_an_episode_record_carries_exactly_these_eight_keys(tmp_path, lab):
    lab.scripts([step(1.5, terminated=True)])
    _node, outputs = evaluate(tmp_path, n_episodes=1, seed=17)
    assert record(outputs)["episodes"][0] == {
        "episode": 0,
        "seed": 17,
        "steps": 1,
        "return": 1.5,
        "terminated": True,
        "truncated": False,
        "reason": "terminated",
        "trace": [
            {"step": 1, "reward": 1.5, "terminated": True, "truncated": False}
        ],
    }


def test_the_episodes_port_is_a_json_artifact_with_exactly_four_keys(tmp_path):
    _node, outputs = evaluate(tmp_path, n_episodes=1)
    assert set(outputs) == {"metrics", "episodes"}
    assert isinstance(outputs["episodes"], JsonArtifact)
    assert set(record(outputs)) == {"schema", "environment", "episodes", "summary"}
    assert record(outputs)["schema"] == EPISODE_SCHEMA


def test_the_environment_block_carries_exactly_the_nine_provenance_facts(
    tmp_path
):
    reference, sidecar = artifact(tmp_path)
    node = Sb3EvalEpisodes("eval", params(n_episodes=2, seed=17))
    outputs = node.run(ctx(tmp_path), {"artifact_path": reference})

    assert record(outputs)["environment"] == {
        "env": STUB_ENV_REF,
        "env_params": {"steps": 3},
        "artifact_path": reference,
        "state_hash": sidecar["state_hash"],
        "algo": "PPO",
        "policy": "MlpPolicy",
        "split": "val",
        "deterministic": True,
        "seed": 17,
    }


def test_the_metrics_mirror_the_summary_and_are_flat_numbers(tmp_path):
    _node, outputs = evaluate(tmp_path, n_episodes=2)
    metrics = outputs["metrics"]

    assert metrics == record(outputs)["summary"]
    assert set(metrics) == {
        "n_episodes",
        "mean_return",
        "std_return",
        "total_steps",
        "terminated_episodes",
        "truncated_episodes",
        "max_episode_steps_episodes",
    }
    assert all(isinstance(value, (int, float)) for value in metrics.values())
    assert all(math.isfinite(value) for value in metrics.values())
    # Provenance lives in the ARTIFACT alone: metrics are numbers a report
    # summarizes, and a path is not one.
    assert "artifact_path" not in metrics and "env" not in metrics


def test_the_persisted_bytes_and_digest_are_the_drivers_canonical_ones(
    tmp_path
):
    """The record goes through the ordinary JSON-artifact seam — no new
    persistence path, no new format."""
    _node, outputs = evaluate(tmp_path, n_episodes=2)
    value = record(outputs)
    run_dir = str(tmp_path / "persisted")
    _persist_json_artifacts(run_dir, "eval", outputs)

    expected = (json.dumps(
        value, sort_keys=True, allow_nan=False, separators=(",", ":")
    ) + "\n").encode("utf-8")
    manifest = outputs["episodes"]
    assert manifest["sha256"] == hashlib.sha256(expected).hexdigest()
    assert manifest["bytes"] == len(expected)
    assert manifest["media_type"] == "application/json"
    with open(os.path.join(run_dir, manifest["path"]), "rb") as handle:
        assert handle.read() == expected


def test_the_node_writes_nothing_of_its_own(tmp_path):
    """No artifact path beyond ``episodes``: this kind persists through
    the JSON-artifact port and touches no directory itself.

    The run dir is asserted directly rather than through ``artifact_dir``,
    which CREATES the directory as a side effect of being asked — the
    check would otherwise manufacture the very thing it looked for.
    """
    _node, outputs = evaluate(tmp_path, n_episodes=1)
    assert set(outputs) == {"metrics", "episodes"}
    assert not os.path.exists(str(tmp_path / "run"))


# ---------------------------------------------------------------------------
# The Gymnasium contract: one atomic fixture per condition
# ---------------------------------------------------------------------------

#: ``(id, reset value, script, fragment)``. Each breaks exactly ONE of the
#: eight conditions §4.1 bundles — a compound "malformed reset/step"
#: fixture would prove at most one of them.
_BROKEN_CONTRACT = [
    ("reset-is-not-a-2-tuple", "observation-only", None, "reset"),
    ("reset-is-a-3-tuple", ("obs", {}, "extra"), None, "reset"),
    ("reset-info-is-not-a-dict", ("obs", "notes"), None, "info"),
    ("step-is-not-a-5-tuple", OK_RESET, [("obs", 1.0, False, False)], "step"),
    ("reward-is-not-finite", OK_RESET, [("obs", float("nan"), True, False, {})],
     "reward"),
    ("reward-is-a-bool", OK_RESET, [("obs", True, True, False, {})], "reward"),
    ("terminated-is-not-a-bool", OK_RESET, [("obs", 1.0, 1, False, {})],
     "terminated"),
    ("truncated-is-not-a-bool", OK_RESET, [("obs", 1.0, False, "no", {})],
     "truncated"),
    ("step-info-is-not-a-dict", OK_RESET, [("obs", 1.0, True, False, "notes")],
     "info"),
]
_BROKEN_IDS = [case[0] for case in _BROKEN_CONTRACT]


@pytest.mark.parametrize(
    "_id,reset_value,script,fragment", _BROKEN_CONTRACT, ids=_BROKEN_IDS
)
def test_each_broken_gymnasium_contract_refuses_by_name(
    tmp_path, lab, _id, reset_value, script, fragment
):
    lab.factory = lambda index: StubEnv(script, reset_value=reset_value)
    with pytest.raises(ValueError, match=fragment):
        evaluate(tmp_path, n_episodes=1, max_episode_steps=4)


def test_a_reset_refusal_names_the_episode_and_no_step_number(tmp_path, lab):
    """There is no step number yet, so inventing one would be a lie about
    where the environment broke."""
    lab.factory = lambda index: StubEnv(reset_value="observation-only")
    with pytest.raises(ValueError) as caught:
        evaluate(tmp_path, n_episodes=2, seed=17)
    message = str(caught.value)
    assert "episode 0" in message and "step" not in message


def test_a_step_refusal_names_the_episode_and_the_step(tmp_path, lab):
    lab.factory = lambda index: StubEnv(
        [step(1.0), ("obs", 1.0, False, False)]
    )
    with pytest.raises(ValueError, match="episode 0 step 2"):
        evaluate(tmp_path, n_episodes=1, max_episode_steps=4)


def test_a_reward_that_is_not_a_number_at_all_refuses(tmp_path, lab):
    """``float("1.5")`` succeeds, so a reward stream arriving as text
    would launder silently without the explicit exclusion."""
    lab.factory = lambda index: StubEnv([("obs", "1.5", True, False, {})])
    with pytest.raises(ValueError, match="reward"):
        evaluate(tmp_path, n_episodes=1)


def test_a_broken_episode_fails_the_node_rather_than_reporting_a_partial(
    tmp_path, lab
):
    """No partial aggregate is ever presented as a result."""
    lab.factory = lambda index: (
        StubEnv() if index == 0 else StubEnv(reset_value="broken")
    )
    with pytest.raises(ValueError, match="episode 1"):
        evaluate(tmp_path, n_episodes=3)


# -- the environment is closed on EVERY path ------------------------------


def test_the_environment_closes_when_reset_raises(tmp_path, lab):
    lab.factory = lambda index: StubEnv(reset_value=RuntimeError("reset blew up"))
    with pytest.raises(RuntimeError):
        evaluate(tmp_path, n_episodes=1)
    assert [env.closed for env in lab.envs] == [1]


def test_the_environment_closes_when_predict_raises(tmp_path, lab):
    lab.model = StubModel(action=RuntimeError("predict blew up"))
    with pytest.raises(RuntimeError):
        evaluate(tmp_path, n_episodes=1)
    assert [env.closed for env in lab.envs] == [1]


def test_the_environment_closes_when_step_raises(tmp_path, lab):
    lab.factory = lambda index: StubEnv([RuntimeError("step blew up")])
    with pytest.raises(RuntimeError):
        evaluate(tmp_path, n_episodes=1)
    assert [env.closed for env in lab.envs] == [1]


def test_the_environment_closes_when_a_shape_check_refuses(tmp_path, lab):
    lab.factory = lambda index: StubEnv([("obs", 1.0, False, False)])
    with pytest.raises(ValueError):
        evaluate(tmp_path, n_episodes=1)
    assert [env.closed for env in lab.envs] == [1]


# ---------------------------------------------------------------------------
# Resolved env_params must be EXACTLY JSON-safe, recursively
# ---------------------------------------------------------------------------


class _Sub(dict):
    pass


class _SubStr(str):
    pass


class _SubInt(int):
    pass


#: ``(id, env_params)``. Eight atomic cases: a compound fixture proves at
#: most one of its branches, and an implementation that checks type but
#: not emptiness (or descends into dicts but not lists) is caught by one
#: of each pair and not the other.
_UNSAFE_ENV_PARAMS = [
    ("top-level-tuple", {"window": (1, 2)}),
    ("top-level-bytes", {"blob": b"raw"}),
    ("non-finite-float", {"scale": float("inf")}),
    ("non-string-key", {3: "three"}),
    ("empty-string-key", {"": 1}),
    ("violation-nested-in-a-dict", {"outer": {"inner": (1, 2)}}),
    ("violation-nested-in-a-list", {"outer": [1, (2, 3)]}),
    ("builtin-subclass", {"outer": _Sub({"a": 1})}),
    ("str-subclass", {"outer": _SubStr("x")}),
    ("int-subclass", {"outer": _SubInt(3)}),
]
_UNSAFE_IDS = [case[0] for case in _UNSAFE_ENV_PARAMS]


@pytest.mark.parametrize("_id,env_params", _UNSAFE_ENV_PARAMS, ids=_UNSAFE_IDS)
def test_unsafe_resolved_env_params_refuse_before_the_environment_is_built(
    tmp_path, lab, _id, env_params
):
    node = Sb3EvalEpisodes("eval", params(env_params=_DROP))
    node.params["env_params"] = env_params  # past validate_params, as a caller could
    reference, _sidecar = artifact(tmp_path)

    with pytest.raises(ValueError, match="env_params"):
        node.run(ctx(tmp_path), {"artifact_path": reference})
    assert lab.envs == [], "the environment must not be built at all"


def test_the_check_is_on_the_resolved_value_not_the_declared_one(
    tmp_path, lab, monkeypatch
):
    """The document declares no ``env_params`` here, so the value under
    test is the one the SIDECAR supplied.

    A sidecar that reached this point through ``_read_sidecar`` cannot in
    fact carry an unsafe value — its content hash is computed with
    ``allow_nan=False`` over the sidecar's own JSON, so a non-finite
    number never survives the write, and ``json.load`` produces only
    exact built-ins otherwise. The refusal still belongs on the resolved
    value rather than on ``params``: it is what keeps a future loader, or
    a caller building this node directly, from reaching ``_build_env``
    with something the evidence could not record.
    """
    node = Sb3EvalEpisodes("eval", params(env_params=_DROP))
    reference, sidecar = artifact(tmp_path)
    monkeypatch.setattr(
        node, "_read_sidecar",
        lambda ref: {**sidecar, "env_params": {"window": (1, 2)}},
    )

    with pytest.raises(ValueError, match="env_params"):
        node.run(ctx(tmp_path), {"artifact_path": reference})
    assert lab.envs == []


def test_safe_nested_env_params_are_accepted(tmp_path, lab):
    """The control: the same shapes, unbroken, ride through."""
    safe = {"a": [1, 2.5, "x", True, None], "b": {"c": [{"d": 1}]}}
    node = Sb3EvalEpisodes("eval", params(n_episodes=1, env_params=safe))
    reference, _sidecar = artifact(tmp_path)

    node.run(ctx(tmp_path), {"artifact_path": reference})
    assert lab.envs[0].env_params == safe


# ---------------------------------------------------------------------------
# The pin, and the sidecar cross-check this kind inherits unchanged
# ---------------------------------------------------------------------------


def test_nothing_pinned_refuses_by_name(tmp_path):
    node = Sb3EvalEpisodes("eval", params(n_episodes=1))
    with pytest.raises(ValueError, match="artifact"):
        node.run(ctx(tmp_path), {})


def test_a_declared_artifact_param_is_a_lawful_pin(tmp_path, lab):
    reference, _sidecar = artifact(tmp_path)
    node = Sb3EvalEpisodes("eval", params(n_episodes=1, artifact=reference))
    assert node.run(ctx(tmp_path), {})["metrics"]["n_episodes"] == 1


@pytest.mark.parametrize("knob", ["algo", "policy"])
def test_a_model_identity_that_contradicts_the_artifact_refuses(
    tmp_path, knob
):
    reference, _sidecar = artifact(tmp_path)
    node = Sb3EvalEpisodes("eval", params(n_episodes=1, **{knob: "SAC"
                                                           if knob == "algo"
                                                           else "CnnPolicy"}))
    with pytest.raises(ValueError, match=knob):
        node.run(ctx(tmp_path), {"artifact_path": reference})


def test_a_tampered_artifact_refuses_through_the_packs_own_hash_check(
    tmp_path
):
    """No new integrity story: the existing zip-plus-sidecar content hash
    is what this kind restores through."""
    reference, _sidecar = artifact(tmp_path)
    with open(reference, "ab") as handle:
        handle.write(b"tamper")
    node = Sb3EvalEpisodes("eval", params(n_episodes=1))
    with pytest.raises(ValueError, match="hash"):
        node.run(ctx(tmp_path), {"artifact_path": reference})


# ---------------------------------------------------------------------------
# Conformance — this one kind, on stubs, with no training anywhere
# ---------------------------------------------------------------------------

EPISODE_NODE_KINDS = (("sb3-eval-episodes", Sb3EvalEpisodes),)

EXPECTED_ROLES = {"sb3-eval-episodes": "score"}


def probes(tmp_path):
    reference, _sidecar = artifact(tmp_path)
    return {
        "sb3-eval-episodes": NodeProbe(
            params=params(n_episodes=2, max_episode_steps=4),
            required=("max_episode_steps", "split"),
            inputs={"artifact_path": reference},
            stream_ports=(),
            runnable=True,
            digest=lambda out: (
                json.dumps(out["metrics"], sort_keys=True),
                json.dumps(out["episodes"].value, sort_keys=True),
            ),
        ),
    }


TestSb3EvalEpisodesConformance = conformance_suite(
    registry=EPISODE_NODE_KINDS,
    module="dskit.pipeline.libs.sb3",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestSb3EvalEpisodesConformance",
)


# ---------------------------------------------------------------------------
# Review corrections
# ---------------------------------------------------------------------------

#: The three declared defaults, each pinned at RUN level. A default that no
#: test asserts is a default a refactor moves silently, and all three land in
#: durable evidence: `environment.seed`, `environment.deterministic`, and how
#: many episode records the artifact carries.
def test_an_omitted_n_episodes_rolls_the_declared_default(tmp_path, lab):
    _node, outputs = evaluate(tmp_path, n_episodes=_DROP)
    assert outputs["metrics"]["n_episodes"] == 5
    assert len(lab.envs) == 5


def test_an_omitted_seed_resets_from_the_declared_default(tmp_path, lab):
    _node, outputs = evaluate(tmp_path, n_episodes=2, seed=_DROP)
    assert [env.seeds for env in lab.envs] == [[0], [1]]
    assert record(outputs)["environment"]["seed"] == 0


def test_an_omitted_deterministic_asks_the_policy_for_its_modal_action(
    tmp_path, lab
):
    _node, outputs = evaluate(tmp_path, n_episodes=1, deterministic=_DROP)
    assert all(call[1] is True for call in lab.model.calls)
    assert record(outputs)["environment"]["deterministic"] is True


# -- a truth flag is never a reward, whichever library minted it ------------


def test_a_numpy_bool_reward_is_refused_like_a_python_one(tmp_path, lab):
    """``isinstance(np.True_, bool)`` is False, so the plain bool exclusion
    misses exactly the scalar type this pack's own docstring says rewards
    normally arrive as — and a flag stream would average as 1.0/0.0
    returns into durable audit evidence."""
    numpy = pytest.importorskip("numpy")
    lab.scripts([("obs", numpy.True_, True, False, {})])
    with pytest.raises(ValueError, match="reward"):
        evaluate(tmp_path, n_episodes=1)


def test_a_numpy_float_reward_is_accepted(tmp_path, lab):
    """The other half: Gymnasium types a reward as ``SupportsFloat`` and a
    numpy scalar is the ordinary shape, so the exclusion must not widen
    into refusing real environments."""
    numpy = pytest.importorskip("numpy")
    lab.scripts([("obs", numpy.float32(1.5), True, False, {})])
    _node, outputs = evaluate(tmp_path, n_episodes=1)
    assert record(outputs)["episodes"][0]["return"] == pytest.approx(1.5)


# -- the contract names math.fsum, so the fixtures must be able to tell ----

#: Three rewards whose NAIVE left-to-right sum loses the middle term
#: entirely (1e16 + 1.0 == 1e16 in binary floating point) while ``fsum``
#: keeps it. Any fixture whose values sum identically either way cannot
#: pin the numerical guarantee §4.1 actually names.
_FSUM_REWARDS = (1e16, 1.0, -1e16)


def test_an_episode_return_is_summed_exactly(tmp_path, lab):
    assert sum(_FSUM_REWARDS) == 0.0 and math.fsum(_FSUM_REWARDS) == 1.0
    lab.scripts([
        step(_FSUM_REWARDS[0]), step(_FSUM_REWARDS[1]),
        step(_FSUM_REWARDS[2], terminated=True),
    ])
    _node, outputs = evaluate(tmp_path, n_episodes=1, max_episode_steps=5)
    assert record(outputs)["episodes"][0]["return"] == 1.0


def test_the_mean_return_is_summed_exactly(tmp_path, lab):
    lab.scripts(*([step(reward, terminated=True)] for reward in _FSUM_REWARDS))
    _node, outputs = evaluate(tmp_path, n_episodes=3, max_episode_steps=5)
    assert outputs["metrics"]["mean_return"] == pytest.approx(1.0 / 3)


# -- close, and the refusal it must not outrank ----------------------------


def test_a_raising_close_does_not_replace_the_refusal_that_caused_it(
    tmp_path, lab
):
    """The message explaining WHY the episode failed is the one worth
    keeping; a bare ``finally`` leaves it reachable only through
    ``__context__``."""
    class ClosesBadly(StubEnv):
        def close(self):
            self.closed += 1
            raise RuntimeError("close blew up")

    lab.factory = lambda index: ClosesBadly([("obs", 1.0, False, False)])
    with pytest.raises(ValueError, match="episode 0 step 1"):
        evaluate(tmp_path, n_episodes=1)
    assert lab.envs[0].closed == 1


def test_a_raising_close_on_the_success_path_still_propagates(tmp_path, lab):
    """Nothing else failed, so the close failure IS the failure."""
    class ClosesBadly(StubEnv):
        def close(self):
            self.closed += 1
            raise RuntimeError("close blew up")

    lab.factory = lambda index: ClosesBadly()
    with pytest.raises(RuntimeError, match="close blew up"):
        evaluate(tmp_path, n_episodes=1)


def test_a_predict_that_is_not_a_pair_refuses_by_name(tmp_path, lab):
    """Every other refusal in this class identifies itself; a bare unpack
    would say "not enough values to unpack" and name nothing."""
    class OneValue:
        def predict(self, observation, deterministic=True):
            return "just-an-action"

    lab.model = OneValue()
    with pytest.raises(ValueError, match="episode 0 step 1: predict"):
        evaluate(tmp_path, n_episodes=1)


def test_unsafe_env_params_refuse_before_the_model_is_loaded_too(
    tmp_path, lab
):
    """Ordering, pinned: the check sits above ``_load_model``, not merely
    above ``_build_env``."""
    node = Sb3EvalEpisodes("eval", params(env_params=_DROP))
    node.params["env_params"] = {"window": (1, 2)}
    reference, _sidecar = artifact(tmp_path)

    with pytest.raises(ValueError, match="env_params"):
        node.run(ctx(tmp_path), {"artifact_path": reference})
    assert lab.envs == [] and lab.loads == []


# ---------------------------------------------------------------------------
# The document doorway — plan-time, with neither library imported
# ---------------------------------------------------------------------------

DAY = 24 * 60 * 60 * 1000


_PLAN_WITH_LIBRARIES_BLOCKED = """
import sys
sys.path[:] = {path!r}
for name in ("gymnasium", "stable_baselines3", "torch"):
    sys.modules[name] = None   # `import name` now raises

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.libs.sb3 import register
from dskit.pipeline.planner import plan

register()
DAY = 24 * 60 * 60 * 1000
document = PipelineDocument.from_obj({{
    "name": "rl-doc",
    "splits": {{"kind": "time", "train_end_ms": 10 * DAY,
               "val_end_ms": 20 * DAY, "test_end_ms": 30 * DAY}},
    "pipeline": {{
        "agent": {{"uses": "sb3-train", "params": {{
            "algo": "PPO", "env": "my_child.envs:ReplayEnv",
            "env_params": {{"scenario": "train"}},
            "total_timesteps": 32, "seed": 7}}}},
        "eval": {{"uses": "sb3-eval-episodes", "params": {{
            "split": "val", "env": "my_child.envs:ReplayEnv",
            "env_params": {{"scenario": "validation"}},
            "n_episodes": 3, "max_episode_steps": 500, "seed": 17}},
            "inputs": {{"artifact_path": "$agent.artifact_path"}}}},
    }},
}})
assert plan(document).role_of("eval") == "score"
print("PLANNED")
"""


def test_a_document_wiring_this_kind_plans_with_no_library_installed():
    """The doorway a user reaches, and the doctrine the pack promises: a
    document naming an RL algorithm and a Gymnasium env class PLANS on a
    machine that has neither library, and fails only when a run path runs.

    In a SUBPROCESS with both blocked, because that is the only honest
    way — ``import_with_blocked``'s own docstring names the trap: in this
    process the libraries are already imported by the time any test runs,
    so an ``"x" not in sys.modules`` assertion is vacuous, and a sibling
    test file that imports them for its own PPO fixture would break it
    besides.
    """
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-c",
         _PLAN_WITH_LIBRARIES_BLOCKED.format(path=list(sys.path))],
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert "PLANNED" in done.stdout


@pytest.mark.parametrize("split", ["train", "cal"])
def test_a_document_declaring_a_selection_split_refuses_at_plan(split):
    """The narrowing reaches a DOCUMENT, not only a constructed node.

    At ``plan``, not at ``from_obj``: the document grammar checks generic
    shape and knows nothing about a kind's own knobs, so the refusal lands
    where the planner asks the kind — which is still before anything runs.
    """
    from dskit.pipeline.base import ConfigError
    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.libs.sb3 import register
    from dskit.pipeline.planner import plan

    register()
    document = PipelineDocument.from_obj({
        "name": "rl-doc",
        "splits": {"kind": "time", "train_end_ms": 10 * DAY,
                   "val_end_ms": 20 * DAY, "test_end_ms": 30 * DAY},
        "pipeline": {
            "eval": {"uses": "sb3-eval-episodes",
                     "params": params(split=split, artifact="runs/x/model.zip")},
        },
    })
    with pytest.raises(ConfigError, match="split"):
        plan(document)


# -- the outcome is read from the LAST step, and that position is pinned ---


def test_a_multi_step_episode_reports_what_its_last_step_gave(tmp_path, lab):
    """Where the per-episode outcome comes from, on episodes that do not
    end on step 1.

    Every other fixture here terminates or truncates immediately, where
    ``trace[0] is trace[-1]`` — so none of them can tell the two apart.
    That position is where ``terminated``, ``truncated``, ``reason`` and
    all three exclusive counts are read from, into the durable artifact,
    which is the whole reason ``episodes`` exists.
    """
    lab.scripts(
        [step(0.1), step(0.2), step(0.3, terminated=True)],
        [step(0.1), step(0.2, truncated=True)],
        [step(0.1), step(0.2), step(0.3)],          # runs into the cap
    )
    _node, outputs = evaluate(tmp_path, n_episodes=3, max_episode_steps=3)
    episodes = record(outputs)["episodes"]

    assert [e["steps"] for e in episodes] == [3, 2, 3]
    assert [e["reason"] for e in episodes] == [
        "terminated", "truncated", "max_episode_steps",
    ]
    assert [(e["terminated"], e["truncated"]) for e in episodes] == [
        (True, False), (False, True), (False, False),
    ]
    assert outputs["metrics"]["terminated_episodes"] == 1
    assert outputs["metrics"]["truncated_episodes"] == 1
    assert outputs["metrics"]["max_episode_steps_episodes"] == 1


def test_both_flags_true_on_a_later_step_still_counts_once(tmp_path, lab):
    """Reason precedence AND exclusive counting, off step 1 — so neither
    claim rests on the degenerate one-step case."""
    lab.scripts([step(0.1), step(0.2, terminated=True, truncated=True)])
    _node, outputs = evaluate(tmp_path, n_episodes=1, max_episode_steps=4)
    episode = record(outputs)["episodes"][0]

    assert episode["steps"] == 2
    assert episode["terminated"] is True and episode["truncated"] is True
    assert episode["reason"] == "terminated"
    assert outputs["metrics"]["terminated_episodes"] == 1
    assert outputs["metrics"]["truncated_episodes"] == 0
    assert outputs["metrics"]["max_episode_steps_episodes"] == 0

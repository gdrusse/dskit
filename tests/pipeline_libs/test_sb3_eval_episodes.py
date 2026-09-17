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

from dskit.pipeline.libs.sb3 import (
    NODE_KINDS,
    Sb3Eval,
    Sb3EvalEpisodes,
)
from dskit.pipeline.split_policy import SPLIT_NAMES

import pytest

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


def test_the_class_cannot_be_constructed_until_its_run_exists():
    """Abstract with ANY params, valid or not — so every refusal above is
    provable through the classmethod and nowhere else."""
    with pytest.raises(TypeError, match="abstract"):
        Sb3EvalEpisodes("eval", params())


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
# Registration comes later, with the concrete class
# ---------------------------------------------------------------------------


def test_the_kind_is_not_registered_while_the_class_is_abstract():
    """``register()`` rejects an abstract class, and the conformance suite
    in ``test_sb3.py`` parametrizes every registered kind — so widening
    the shared table before ``run`` exists would crash that sibling file,
    not merely this one."""
    assert "sb3-eval-episodes" not in dict(NODE_KINDS)

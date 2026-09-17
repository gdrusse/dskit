"""stable-baselines3 library pack — the declared RL doorway (ADR-0028,
tier 2).

Reinforcement learning's generic plumbing — construct a standard
algorithm over an environment, train it for N timesteps, save/restore
the policy, evaluate it over episodes — is the same in every project;
only the ENVIRONMENT is domain. This pack keeps the plumbing here and
the environment in the child: the DOCUMENT names everything.

* ``sb3-train`` (:class:`Sb3Train`, role ``train``) — ``algo`` names a
  stable-baselines3 algorithm (``"SAC"``, ``"PPO"``, …; resolved from
  the library BY NAME at run, refused by name when absent), ``policy``
  names its policy class (``"MlpPolicy"``, ``"MultiInputPolicy"``, …),
  ``env`` is the child environment's import path
  (``"pkg.module:Class"``, a gymnasium ``Env`` subclass) built as
  ``Class(**env_params)``, and ``algo_params`` passes constructor
  kwargs to the algorithm verbatim (the pyomo ``solver_options``
  precedent: the library's own signature is the contract). Training
  runs ``total_timesteps`` under a recorded ``seed`` and saves the
  policy as ``model.zip`` plus a hash-pinned ``model.json`` sidecar —
  the torch pack's artifact discipline (S2-A: the digest covers the
  zip bytes AND the sidecar).
* ``sb3-policy`` (:class:`Sb3Policy`, role ``signal``) — restores a
  pinned artifact into an :class:`Sb3PolicySignal` (``act(obs) ->
  action``), refusing a missing/mismatched sidecar by name; the
  declared ``algo``/``env`` cross-check the sidecar when present.
* ``sb3-eval`` (:class:`Sb3Eval`, role ``score``) — rolls the restored
  policy through the declared environment for ``n_episodes`` via SB3's
  own ``evaluate_policy`` and reports ``mean_reward``/``std_reward``.
  The ``split`` param is the score-role DECLARATION the planner holds
  search objectives to ("selection never sees test"): it names which
  split's environment this rollout measures, and the ``env_params``
  wired here should build exactly that environment — the binding is
  declared, not mechanical, because episodes are the env's to
  generate, not records the toolkit can partition.
* ``sb3-eval-episodes`` (:class:`Sb3EvalEpisodes`, role ``score``,
  ADR-0148) — the same restore, the same environment seam, but the
  episode loop is written HERE rather than delegated, so the ordered
  per-step record survives: one ``JsonArtifact`` carrying the resolved
  environment, the verified model provenance, every episode's trace and
  why it ended, beside seven flat numeric metrics. ``sb3-eval`` answers
  what a SEARCH wants (two scalars); this answers what an AUDIT wants.
  Its ``split`` narrows to ``"val"``/``"test"`` — the one deliberate
  difference from its sibling — because a durable record of held-out
  performance drawn from ``train``, or from ``cal``'s inner calibration
  band, would misrepresent itself the moment anyone read it back.
  ``n_episodes * max_episode_steps`` is capped at plan time: the trace is
  held whole before it is written.

Determinism is BEST-EFFORT and recorded, never promised: the seed is
handed to the algorithm and the environment reset, but bitwise
run-to-run identity is stable-baselines3's (and torch's) to guarantee.
The artifact hash pins what WAS trained; it does not promise a retrain
reproduces it.

Packs never auto-register: :data:`NODE_KINDS` + an explicit
:func:`register` call (``libs/__init__`` doctrine).

Import cost: stdlib + ``dskit.pipeline`` only — stable_baselines3,
gymnasium and (transitively) torch are imported strictly inside
run-path methods (``tests/pipeline/test_purity.py`` enforces the rule).
"""

from __future__ import annotations

import hashlib
import json
import math
import os

from dskit.pipeline.base import import_ref, is_class_ref
from dskit.pipeline.kinds_stats import _check_int, _reject_unknown
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    JsonArtifact,
    Node,
    TrainableNode,
)
from dskit.pipeline.split_policy import SPLIT_NAMES

__all__ = [
    "ARTIFACT_FORMAT",
    "EPISODE_SCHEMA",
    "EPISODE_SPLITS",
    "NODE_KINDS",
    "Sb3Eval",
    "Sb3EvalEpisodes",
    "Sb3Policy",
    "Sb3PolicySignal",
    "Sb3Train",
    "register",
]

#: The sidecar's format tag — a loader refuses any other by name.
ARTIFACT_FORMAT = "dskit-sb3-v1"

#: Keys every sidecar must carry — an artifact without them is refused.
_SIDECAR_KEYS = ("algo", "env", "env_params", "format", "policy", "seed", "state_hash")

DEFAULT_POLICY = "MlpPolicy"
DEFAULT_EPISODES = 5

#: The schema tag a persisted episode record carries.
EPISODE_SCHEMA = "dskit.sb3-eval-episodes/v1"

#: The splits :class:`Sb3EvalEpisodes` accepts — a deliberate NARROWING of
#: :data:`~dskit.pipeline.split_policy.SPLIT_NAMES`, not a restatement of
#: it. ``episodes`` is durable, persisted per-episode evidence of HELD-OUT
#: performance; the same record drawn from ``train``, or from ``cal``'s
#: inner calibration band (ADR-0034), would misrepresent itself as
#: out-of-sample the moment anyone read it back.
EPISODE_SPLITS = ("val", "test")

#: How many episodes one node may roll, and how long each may run.
_MAX_EPISODES = 10_000
_MAX_EPISODE_STEPS = 1_000_000

#: The ceiling on ``n_episodes * max_episode_steps`` — how many step
#: records the trace can allocate. Checked at PLAN, where the document
#: can still be refused: both knobs are plain literal ints with no
#: ``$``-reference support, so discovering this at execute would be
#: discovering it after the expensive part.
_MAX_TRACE_STEPS = 1_000_000

#: The largest value a seed (and the last episode's ``seed + i``) may take.
_MAX_SEED = 2 ** 32 - 1

DEFAULT_DETERMINISTIC = True
DEFAULT_SEED = 0


def _bounded_int_problem(problems, name, value, *, low, high):
    """Hold ``name`` to an exact int in ``[low, high]``, bool excluded.

    ``_check_int`` (tier-1's ``check_int_param``) owns the "is this an
    int knob" question and the bool exclusion; only the ceiling is this
    pack's to add, and it is added ONLY when the floor already passed, so
    one broken value never reports twice.
    """
    before = len(problems)
    _check_int(problems, name, value, ge=low)
    if len(problems) == before and value > high:
        problems.append(f"{name} must be at most {high}, got {value!r}")


def _params_dict_problem(problems, name, value):
    if value is not None and (
        not isinstance(value, dict)
        or any(not isinstance(k, str) or not k for k in value)
    ):
        problems.append(
            f"{name} must be a dict of constructor kwargs (string keys), "
            f"got {value!r}"
        )


def _is_truth_flag(value):
    """Say whether ``value`` is a truth FLAG rather than a number.

    Python's ``bool`` and numpy's ``bool_`` both answer arithmetic like
    the numbers 1 and 0 while MEANING a flag, and ``isinstance(x, bool)``
    catches only the first — ``np.True_`` is not a ``bool`` subclass.
    numpy is never imported here: a scalar is asked for its own
    ``dtype.kind``, which is ``"b"`` for exactly the boolean types, so
    any array library following the same protocol answers too.
    """
    if isinstance(value, bool):
        return True
    return getattr(getattr(value, "dtype", None), "kind", None) == "b"


def _episode_reward(value):
    """``value`` as a finite ``float``, or ``None`` when it is not a reward.

    NOT :func:`~dskit.pipeline.records.number_ok`, and the divergence is
    deliberate rather than a forgotten import: that rule answers "is this
    RECORD CELL an exact int or float", while Gymnasium's own API types a
    reward as ``SupportsFloat`` and a numpy scalar is the ordinary shape
    one arrives in. Refusing those would refuse most real environments.

    A truth flag is excluded all the same — ``number_ok``'s own
    documented hazard, for the same reason: ``True`` would enter the
    return as ``1.0``, and an environment emitting flags instead of
    rewards would average perfectly cleanly. Because a numpy scalar IS
    accepted here, the exclusion has to reach ``numpy.bool_`` too, or the
    one shape the docstring above endorses is exactly the one that
    launders (:func:`_is_truth_flag`). ``str`` and ``bytes`` are excluded
    because ``float("1.5")`` succeeds, and a reward stream arriving as
    text is corruption to report, never data to launder.
    """
    if _is_truth_flag(value) or isinstance(value, (str, bytes, bytearray)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _json_safe_problem(value, where):
    """The first way ``value`` is not EXACTLY JSON-safe, or ``None``.

    Exact type, never ``isinstance``: a ``dict``, ``str`` or ``int``
    SUBCLASS passes an isinstance check and then serializes as something
    its author did not write — an ``IntEnum`` member as a bare number, a
    path subclass as a string. Recursion descends into both container
    types, because a walker that entered dict values but not list
    elements would pass one nested violation and fail the other.
    """
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key:
                return (
                    f"{where} carries the key {key!r} — every key must be a "
                    "non-empty built-in str"
                )
            problem = _json_safe_problem(item, f"{where}[{key!r}]")
            if problem:
                return problem
        return None
    if type(value) is list:
        for index, item in enumerate(value):
            problem = _json_safe_problem(item, f"{where}[{index}]")
            if problem:
                return problem
        return None
    if value is None or type(value) in (bool, str, int):
        return None
    if type(value) is float:
        if math.isfinite(value):
            return None
        return f"{where} is {value!r}, which JSON cannot hold"
    return (
        f"{where} is a {type(value).__name__} — env_params must be built-in "
        "dict/list/str/bool/int/float/null values exactly, and a subclass of "
        "one of them is not one of them"
    )


def _algo_problem(problems, value, *, required):
    if value is None:
        if required:
            problems.append(
                "algo is required — a stable-baselines3 algorithm name "
                "('SAC', 'PPO', ...), resolved from the library at run"
            )
    elif not isinstance(value, str) or not value.isidentifier():
        problems.append(
            f"algo must be a stable-baselines3 class name (an identifier), "
            f"got {value!r}"
        )


def _env_problem(problems, value, *, required):
    if value is None:
        if required:
            problems.append(
                "env is required — the environment's import path "
                "('pkg.module:Class', a gymnasium Env subclass); the "
                "document names the environment (ADR-0028)"
            )
    elif not is_class_ref(value):
        problems.append(
            f"env must be a 'pkg.module:Class' import path, got {value!r}"
        )


class Sb3PolicySignal:
    """What an sb3 node's ``policy`` output IS: an actor + provenance.

    ``act(obs)`` maps one observation to one action via the model's
    ``predict`` (``deterministic=True`` — a served policy answers its
    modal action; exploration is training's business). ``artifact_path``
    and ``loaded`` are the provenance pair a probe's ``verify_loaded``
    checks, exactly like :class:`~dskit.pipeline.libs.torch.TorchSignal`.
    """

    __slots__ = ("artifact_path", "loaded", "model")

    def __init__(self, model, artifact_path, *, loaded):
        self.model = model
        self.artifact_path = artifact_path
        self.loaded = bool(loaded)

    def act(self, obs, deterministic=True):
        """One observation in, one action out."""
        action, _state = self.model.predict(obs, deterministic=deterministic)
        return action


class _Sb3Base(Node):
    """The artifact protocol the three kinds share — save, verify,
    restore; refuse by name through :meth:`_refuse`. Mirrors the torch
    pack's S2-A discipline: the content hash covers the model-zip bytes
    AND the sidecar.

    :meth:`_refuse` is the convention for refusals about the artifact's
    CONTENT only. The artifact-PIN refusals are tier-1's since ADR-0038
    — see that method.

    A plain :class:`~dskit.pipeline.node.Node`, deliberately: ADR-0038
    re-parents the two trainable kinds and NOT this base, because
    :class:`Sb3Eval` (role ``score``) also inherits it and carries no
    mode at all. It defines neither template method, so the two trainable
    kinds may keep it ahead of :class:`~dskit.pipeline.node.TrainableNode`
    in their bases and still resolve both to the base."""

    _PARAMS = ()
    #: Sidecar fields cross-checked against DECLARED params at load. The
    #: serving kinds check all four (a policy must restore under the
    #: model/env that trained it); :class:`Sb3Eval` narrows the list —
    #: measuring on a DIFFERENT environment is its whole point.
    _SIDECAR_CHECK = ("algo", "env", "env_params", "policy")

    def _refuse(self, why):
        """Refuse a load BY NAME, with this pack's ``cannot load
        artifact`` tail — the convention for every refusal about the
        artifact's CONTENT.

        It does NOT reach the artifact-PIN refusals: nothing pinned, an
        empty node-level pin, a node-level pin contradicting
        ``params['artifact']``. Since ADR-0038 those are raised by
        tier-1 :meth:`~dskit.pipeline.node.Node.pinned_artifact`, which
        names the node key and quotes the pack's ``missing`` wording but
        cannot add this tail — a stdlib-only base never calls a tier-2
        wrapper. ``sb3-eval`` reaches that same service while carrying no
        mode at all. Route a NEW refusal about WHICH artifact was pinned
        there, not here.
        """
        raise ValueError(
            f"{self.key}: cannot load artifact — {why}. A pinned artifact "
            "restores exactly; it is never refit."
        )

    @staticmethod
    def _resolve_algo(key, name):
        """The stable-baselines3 algorithm class for ``name``, refused by
        name when the library does not export it. Run-time only — the
        library is a heavy import."""
        import stable_baselines3

        algo = getattr(stable_baselines3, name, None)
        if algo is None or not isinstance(algo, type):
            exported = sorted(
                n
                for n in dir(stable_baselines3)
                if n[:1].isupper() and isinstance(getattr(stable_baselines3, n), type)
            )
            raise ValueError(
                f"{key}: algo {name!r} is not exported by stable_baselines3 "
                f"(available: {exported})"
            )
        return algo

    def _build_env(self, ref, env_params):
        """The declared environment, constructed — a gymnasium ``Env``
        subclass or a refusal naming the ref."""
        import gymnasium

        cls = import_ref(ref)  # raises ValueError naming the ref
        if not (isinstance(cls, type) and issubclass(cls, gymnasium.Env)):
            raise ValueError(
                f"{self.key}: env {ref!r} is not a gymnasium.Env subclass — "
                "the declared seam builds environments, nothing else"
            )
        try:
            return cls(**(env_params or {}))
        except TypeError as exc:
            raise ValueError(
                f"{self.key}: env {ref!r} rejected env_params "
                f"{env_params or {}!r}: {exc}"
            ) from exc

    @staticmethod
    def _state_hash(zip_path, sidecar):
        """sha256 over the zip bytes, a NUL byte, then the canonical JSON
        of every sidecar field except ``state_hash`` (the torch pack's
        recipe, S2-A)."""
        material = {k: v for k, v in sidecar.items() if k != "state_hash"}
        digest = hashlib.sha256()
        with open(zip_path, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
        digest.update(
            json.dumps(
                material, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        )
        return digest.hexdigest()

    def _save_artifact(self, ctx, model, spec):
        zip_path = os.path.join(self.artifact_dir(ctx), "model.zip")
        model.save(zip_path)
        sidecar = {"format": ARTIFACT_FORMAT, **spec}
        sidecar["state_hash"] = self._state_hash(zip_path, sidecar)
        self.write_artifact(ctx, "model.json", sidecar)
        return zip_path

    def _read_sidecar(self, zip_path):
        """The verified sidecar for ``zip_path``, or a refusal by name."""
        if not isinstance(zip_path, str) or not zip_path:
            self._refuse(f"no usable artifact reference, got {zip_path!r}")
        if not os.path.isfile(zip_path):
            self._refuse(f"artifact file {zip_path!r} does not exist")
        sidecar_path = os.path.splitext(zip_path)[0] + ".json"
        if not os.path.isfile(sidecar_path):
            self._refuse(
                f"artifact sidecar {sidecar_path!r} is missing — without it "
                "the algo, env and seed are unverifiable"
            )
        try:
            with open(sidecar_path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
        except ValueError as exc:
            self._refuse(f"artifact sidecar {sidecar_path!r} is not JSON: {exc}")
        if not isinstance(sidecar, dict):
            self._refuse(f"artifact sidecar {sidecar_path!r} is not a JSON object")
        missing = [k for k in _SIDECAR_KEYS if k not in sidecar]
        if missing:
            self._refuse(f"artifact sidecar {sidecar_path!r} lacks key(s) {missing}")
        if sidecar["format"] != ARTIFACT_FORMAT:
            self._refuse(
                f"artifact format {sidecar['format']!r} is not {ARTIFACT_FORMAT!r}"
            )
        got = self._state_hash(zip_path, sidecar)
        if got != sidecar["state_hash"]:
            self._refuse(
                f"artifact content hash mismatch for {zip_path!r} — the model "
                "file or its sidecar is not the one that was written (the hash "
                f"covers both; sidecar {sidecar['state_hash']!r}, computed {got!r})"
            )
        for name in self._SIDECAR_CHECK:
            declared = self.params.get(name)
            if declared is not None and declared != sidecar[name]:
                self._refuse(
                    f"artifact sidecar mismatch on {name!r}: trained with "
                    f"{sidecar[name]!r}, this node declares {declared!r}"
                )
        return sidecar

    def _load_model(self, zip_path, sidecar, *, env=None):
        algo = self._resolve_algo(self.key, sidecar["algo"])
        try:
            return algo.load(zip_path, env=env, device="cpu")
        except Exception as exc:  # noqa: BLE001 - refusal must name the artifact
            self._refuse(
                f"artifact at {zip_path!r} does not restore under "
                f"{sidecar['algo']}: {exc}"
            )


class Sb3Train(_Sb3Base, TrainableNode):
    """The declared RL trainer (role ``train``, kind ``sb3-train``).

    Knobs: ``algo`` (required SB3 class name), ``env`` (required import
    path) + ``env_params``, ``policy`` (default ``"MlpPolicy"``),
    ``total_timesteps`` (required), ``seed`` (default 0, recorded), and
    ``algo_params`` (constructor kwargs, verbatim). No input ports — the
    environment IS the data source here, and it is declared, not wired.
    ``mode="load"`` restores the pinned artifact and never trains.
    """

    role = "train"
    outputs = ("policy", "artifact_path", "metrics")

    _PARAMS = (
        "algo",
        "algo_params",
        "env",
        "env_params",
        "policy",
        "seed",
        "total_timesteps",
    )

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        _algo_problem(problems, params.get("algo"), required=True)
        _env_problem(problems, params.get("env"), required=True)
        policy = params.get("policy", DEFAULT_POLICY)
        if not isinstance(policy, str) or not policy:
            problems.append(f"policy must be a non-empty string, got {policy!r}")
        if "total_timesteps" not in params:
            problems.append(
                "total_timesteps is required — how long the algorithm trains"
            )
        else:
            _check_int(problems, "total_timesteps", params["total_timesteps"], ge=1)
        _check_int(problems, "seed", params.get("seed", 0), ge=0)
        _params_dict_problem(problems, "env_params", params.get("env_params"))
        _params_dict_problem(problems, "algo_params", params.get("algo_params"))
        return problems

    def run_load(self, ctx, inputs):
        sidecar = self._read_sidecar(self.artifact)
        model = self._load_model(self.artifact, sidecar)
        self.log.info("restored %s from %s", sidecar["algo"], self.artifact)
        return {
            "policy": Sb3PolicySignal(model, self.artifact, loaded=True),
            "artifact_path": self.artifact,
            "metrics": {"loaded": 1, "seed": sidecar["seed"]},
        }

    def run_train(self, ctx, inputs):
        algo_name = self.params["algo"]
        env_ref = self.params["env"]
        env_params = self.params.get("env_params", {})
        policy = self.params.get("policy", DEFAULT_POLICY)
        seed = self.params.get("seed", 0)
        total = self.params["total_timesteps"]
        algo_cls = self._resolve_algo(self.key, algo_name)
        env = self._build_env(env_ref, env_params)
        try:  # the env closes on EVERY exit — a child env may hold real
            # resources (readers, subprocess simulators), and a failed
            # learn() in a retrying fold loop must not leak them.
            try:
                model = algo_cls(
                    policy,
                    env,
                    seed=seed,
                    verbose=0,
                    **self.params.get("algo_params", {}),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{self.key}: {algo_name}({policy!r}, {env_ref}) rejected "
                    f"its construction: {exc}"
                ) from exc
            model.learn(total_timesteps=total, progress_bar=False)
            artifact_path = self._save_artifact(
                ctx,
                model,
                {
                    "algo": algo_name,
                    "policy": policy,
                    "env": env_ref,
                    "env_params": env_params,
                    "seed": seed,
                },
            )
        finally:
            env.close()
        self.log.info(
            "trained %s(%s) on %s for %d timestep(s), seed %d -> %s",
            algo_name,
            policy,
            env_ref,
            total,
            seed,
            artifact_path,
        )
        return {
            "policy": Sb3PolicySignal(model, artifact_path, loaded=False),
            "artifact_path": artifact_path,
            "metrics": {"total_timesteps": total, "seed": seed},
        }


class Sb3Policy(_Sb3Base, TrainableNode):
    """Inference-only restore of a pinned sb3 artifact (role ``signal``,
    kind ``sb3-policy``) — it always loads, it never trains.

    The artifact reference comes from (in order): node-level
    ``mode="load"`` + ``artifact``; ``params["artifact"]``; the
    ``artifact_path`` input port. Declared ``algo``/``env``/``policy``
    cross-check the sidecar when present.
    """

    role = "signal"
    outputs = ("policy",)
    default_mode = "load"

    _PARAMS = ("algo", "artifact", "env", "env_params", "policy")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        artifact = params.get("artifact")
        if artifact is not None and (not isinstance(artifact, str) or not artifact):
            problems.append(
                f"artifact must be a non-empty string path, got {artifact!r}"
            )
        _algo_problem(problems, params.get("algo"), required=False)
        _env_problem(problems, params.get("env"), required=False)
        _params_dict_problem(problems, "env_params", params.get("env_params"))
        return problems

    def validate_common_inputs(self, inputs):
        # The port is checked in EITHER mode: a document that wires it
        # wired it wrong regardless of which mode it also declared.
        return self.pin_port_problems(
            inputs,
            "artifact_path",
            hint="wire it from a train node's artifact_path output",
        )

    def run_train(self, ctx, inputs):
        raise NotImplementedError(
            f"{self.key}: sb3-policy is inference-only — mode='train' "
            "trains nothing here; train with sb3-train and pin its "
            "artifact"
        )

    def run_load(self, ctx, inputs):
        reference = self.pinned_artifact(
            self.params.get("artifact"),
            (inputs or {}).get("artifact_path"),
            missing=(
                "no artifact reference — set mode='load' + artifact, "
                "params['artifact'], or wire inputs['artifact_path'] from "
                "an sb3-train node"
            ),
        )
        sidecar = self._read_sidecar(reference)
        model = self._load_model(reference, sidecar)
        self.log.info("restored %s from %s", sidecar["algo"], reference)
        return {"policy": Sb3PolicySignal(model, reference, loaded=True)}


class Sb3Eval(_Sb3Base):
    """Episode evaluation of a pinned policy (role ``score``, kind
    ``sb3-eval``): SB3's own ``evaluate_policy`` over the declared
    environment.

    ``split`` is the score-role declaration (planner rule): which
    split's environment this rollout measures — wire ``env_params``
    that build exactly that environment (a val-period env for
    ``split="val"``). ``env``/``env_params`` default to the artifact
    sidecar's trained values when omitted.
    """

    role = "score"
    outputs = ("metrics",)

    _PARAMS = (
        "algo",
        "artifact",
        "deterministic",
        "env",
        "env_params",
        "n_episodes",
        "policy",
        "seed",
        "split",
    )
    #: An eval lawfully measures on an env OTHER than the training one —
    #: only the model's identity must match the artifact. ``algo``/
    #: ``policy`` are therefore declarable HERE (the pin), while ``env``/
    #: ``env_params`` are exempt from the sidecar cross-check.
    _SIDECAR_CHECK = ("algo", "policy")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        # The planner enforces this for every score node in a document;
        # repeated here so a directly-constructed node refuses too (the
        # kinds_stats wording).
        if params.get("split") not in SPLIT_NAMES:
            problems.append(
                f"split must declare which split this node reads "
                f"({'/'.join(repr(s) for s in SPLIT_NAMES)}), got "
                f"{params.get('split')!r}"
            )
        _algo_problem(problems, params.get("algo"), required=False)
        policy = params.get("policy")
        if policy is not None and (not isinstance(policy, str) or not policy):
            problems.append(f"policy must be a non-empty string, got {policy!r}")
        artifact = params.get("artifact")
        if artifact is not None and (not isinstance(artifact, str) or not artifact):
            problems.append(
                f"artifact must be a non-empty string path, got {artifact!r}"
            )
        _env_problem(problems, params.get("env"), required=False)
        _params_dict_problem(problems, "env_params", params.get("env_params"))
        _check_int(
            problems, "n_episodes", params.get("n_episodes", DEFAULT_EPISODES), ge=1
        )
        _check_int(problems, "seed", params.get("seed", 0), ge=0)
        deterministic = params.get("deterministic", True)
        if not isinstance(deterministic, bool):
            problems.append(
                f"deterministic must be a bool, got {deterministic!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        return self.pin_port_problems(
            inputs, "artifact_path", hint="wire it from an sb3-train node"
        )

    def run(self, ctx, inputs):
        from stable_baselines3.common.evaluation import evaluate_policy

        # A score role carries no node-level pin — Node.node_level_pin
        # says so — but the two remaining sources, and the refusal when
        # neither answers, are the same service the policy kind uses.
        reference = self.pinned_artifact(
            self.params.get("artifact"),
            (inputs or {}).get("artifact_path"),
            missing=(
                "no artifact reference — set params['artifact'] or wire "
                "inputs['artifact_path'] from an sb3-train node"
            ),
        )
        sidecar = self._read_sidecar(reference)
        env_ref = self.params.get("env") or sidecar["env"]
        env_params = self.params.get("env_params")
        if env_params is None:
            env_params = sidecar["env_params"]
        env = self._build_env(env_ref, env_params)
        try:
            env.reset(seed=self.params.get("seed", 0))
            model = self._load_model(reference, sidecar)
            n_episodes = self.params.get("n_episodes", DEFAULT_EPISODES)
            mean_reward, std_reward = evaluate_policy(
                model,
                env,
                n_eval_episodes=n_episodes,
                deterministic=self.params.get("deterministic", True),
            )
        finally:
            env.close()
        if not math.isfinite(mean_reward):
            raise ValueError(
                f"{self.key}: evaluation produced a non-finite mean reward "
                f"({mean_reward!r}) — the environment's reward stream is "
                "broken; a search cannot rank what it cannot measure"
            )
        self.log.info(
            "evaluated %s over %d episode(s) on %s (the %r split's env): "
            "mean %.6f ± %.6f",
            sidecar["algo"],
            n_episodes,
            env_ref,
            self.params["split"],
            mean_reward,
            std_reward,
        )
        return {
            "metrics": {
                "mean_reward": float(mean_reward),
                "std_reward": float(std_reward),
                "n_episodes": n_episodes,
            }
        }


class Sb3EvalEpisodes(_Sb3Base):
    """Per-episode evaluation of a pinned policy (role ``score``, kind
    ``sb3-eval-episodes``).

    The sibling of :class:`Sb3Eval`, and a deliberate one: that kind asks
    stable-baselines3's own ``evaluate_policy`` for two scalars, which is
    what a SEARCH wants. This kind rolls the episodes itself and keeps the
    ordered per-step record, which is what an AUDIT wants — a reader can
    ask what the policy actually did, on which seed, for how many steps,
    and why each episode ended, instead of taking a mean on faith.

    It introduces no new persistence seam and no new artifact format: the
    record rides the existing :class:`~dskit.pipeline.node.JsonArtifact`
    port, and the pinned model still arrives through the pack's own
    ``model.zip`` plus hashed sidecar. Every service it uses —
    ``_read_sidecar``, ``pinned_artifact``, ``_load_model``,
    ``_build_env`` — is the one the legacy kinds already use, unchanged.

    ``split`` accepts only ``"val"`` or ``"test"``. That is a NARROWING of
    what :class:`Sb3Eval` accepts, made on purpose: a durable record of
    held-out performance drawn from ``train`` or from the ``cal``
    calibration band would misrepresent itself the moment anyone read it
    back as evidence.

    Parameters
    ----------
    params : dict
        ``split`` (required, ``"val"`` or ``"test"``),
        ``max_episode_steps`` (required, the per-episode step cap),
        ``n_episodes`` (default 5), ``seed`` (default 0; episode ``i``
        resets on ``seed + i``), ``deterministic`` (default ``True``),
        plus the artifact knobs ``artifact`` / ``algo`` / ``policy`` and
        the environment knobs ``env`` / ``env_params``, which default to
        the sidecar's trained values when omitted.

    Examples
    --------
    Roll three bounded episodes of a trained policy on the val
    environment and keep the record::

        node = Sb3EvalEpisodes("eval", {
            "split": "val",
            "env": "my_child.envs:ReplayEnv",
            "env_params": {"scenario": "validation"},
            "n_episodes": 3,
            "max_episode_steps": 500,
            "seed": 17,
        })
        out = node.run(ctx, {"artifact_path": trained["artifact_path"]})
        # -> out["metrics"]["mean_return"] == 1.25
        # -> out["episodes"].value["episodes"][0]["reason"] == "terminated"
    """

    role = "score"
    outputs = ("metrics", "episodes")

    _PARAMS = (
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
    #: The evaluator's narrow cross-check, exactly as :class:`Sb3Eval`
    #: has it: measuring on a DIFFERENT environment is the point, so only
    #: the model's own identity must match the artifact.
    _SIDECAR_CHECK = ("algo", "policy")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob, plus the two CROSS-param bounds
            this kind owns: the trace-allocation ceiling
            (``n_episodes * max_episode_steps``) and the requirement that
            the LAST episode's ``seed + n_episodes - 1`` still be a seed.
            Both live here rather than in ``run`` because neither knob
            accepts a ``$``-reference, so both are answerable at plan.
        """
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        if params.get("split") not in EPISODE_SPLITS:
            problems.append(
                f"split must declare which HELD-OUT split this evidence "
                f"measures ({'/'.join(repr(s) for s in EPISODE_SPLITS)}) — "
                "a persisted per-episode record is not a scalar a search "
                f"may read from any split, got {params.get('split')!r}"
            )
        problems += cls._artifact_knob_problems(params)
        problems += cls._episode_knob_problems(params)
        return problems

    @classmethod
    def _artifact_knob_problems(cls, params):
        """The pin, the model's identity, and the environment to measure on."""
        problems = []
        _algo_problem(problems, params.get("algo"), required=False)
        policy = params.get("policy")
        if policy is not None and (not isinstance(policy, str) or not policy):
            problems.append(f"policy must be a non-empty string, got {policy!r}")
        artifact = params.get("artifact")
        if artifact is not None and (not isinstance(artifact, str) or not artifact):
            problems.append(
                f"artifact must be a non-empty string path, got {artifact!r}"
            )
        _env_problem(problems, params.get("env"), required=False)
        _params_dict_problem(problems, "env_params", params.get("env_params"))
        return problems

    @classmethod
    def _episode_knob_problems(cls, params):
        """How many episodes, how long, under which seeds — and the bounds."""
        problems = []
        episodes = params.get("n_episodes", DEFAULT_EPISODES)
        steps = params.get("max_episode_steps")
        seed = params.get("seed", DEFAULT_SEED)
        counts = []
        _bounded_int_problem(
            counts, "n_episodes", episodes, low=1, high=_MAX_EPISODES
        )
        if steps is None:
            counts.append(
                "max_episode_steps is required — an episode that never "
                "terminates would roll forever, and the cap is also what "
                "bounds the trace this node persists"
            )
        else:
            _bounded_int_problem(
                counts, "max_episode_steps", steps,
                low=1, high=_MAX_EPISODE_STEPS,
            )
        _bounded_int_problem(counts, "seed", seed, low=0, high=_MAX_SEED)
        problems += counts
        deterministic = params.get("deterministic", DEFAULT_DETERMINISTIC)
        if not isinstance(deterministic, bool):
            problems.append(
                f"deterministic must be a bool, got {deterministic!r}"
            )
        # The cross-bounds are arithmetic on the three counts, so they are
        # asked only once each of them is a number — and asked whatever
        # ELSE the document got wrong, so an unrelated broken knob never
        # hides a document that would blow the trace ceiling.
        if counts:
            return problems
        return problems + cls._cross_knob_problems(
            int(episodes), int(steps), int(seed)
        )

    @classmethod
    def _cross_knob_problems(cls, episodes, steps, seed):
        """The two bounds no single knob can break on its own."""
        problems = []
        if episodes * steps > _MAX_TRACE_STEPS:
            problems.append(
                f"n_episodes * max_episode_steps is {episodes * steps}, above "
                f"the {_MAX_TRACE_STEPS} step records this node will allocate "
                "— the trace is kept in memory and then persisted whole, so "
                "the ceiling is refused at plan rather than discovered at "
                "execute"
            )
        if seed + episodes - 1 > _MAX_SEED:
            problems.append(
                f"seed {seed} with {episodes} episode(s) would reset the last "
                f"one on {seed + episodes - 1}, above {_MAX_SEED} — episode i "
                "resets on seed + i, so every one of them must still be a "
                "seed the environment can be given"
            )
        return problems

    def validate_inputs(self, inputs):
        """Problems with ``inputs``, empty when none.

        Parameters
        ----------
        inputs : dict
            The optional ``artifact_path`` pin, and nothing else — the
            environment is DECLARED here, never wired.

        Returns
        -------
        list of str
            One problem when the port is wired to something unusable. An
            UNWIRED port is lawful: the reference may come from
            ``params['artifact']`` instead.
        """
        return self.pin_port_problems(
            inputs, "artifact_path", hint="wire it from an sb3-train node"
        )

    # -- the knobs, read once each ----------------------------------------

    def n_episodes(self):
        """How many episodes this node rolls (int)."""
        return int(self.params.get("n_episodes", DEFAULT_EPISODES))

    def max_episode_steps(self):
        """The per-episode step cap (int)."""
        return int(self.params["max_episode_steps"])

    def seed(self):
        """The first episode's reset seed; episode ``i`` uses ``seed + i`` (int)."""
        return int(self.params.get("seed", DEFAULT_SEED))

    def deterministic(self):
        """Whether the policy answers its modal action (bool)."""
        return bool(self.params.get("deterministic", DEFAULT_DETERMINISTIC))

    # -- the run ----------------------------------------------------------

    def run(self, ctx, inputs):
        """Roll bounded episodes and answer the record plus its aggregates.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused — this node persists through the
            ``episodes`` port's own artifact seam and writes nothing of
            its own.
        inputs : dict
            The optional ``artifact_path`` pin.

        Returns
        -------
        dict
            ``metrics`` (the seven flat numbers) and ``episodes`` (a
            :class:`~dskit.pipeline.node.JsonArtifact` carrying the
            schema tag, the resolved environment and model provenance,
            the ordered episode records, and the same seven numbers as
            ``summary``). The environment and the provenance appear in
            the ARTIFACT only: metrics are numbers a report summarizes.

        Raises
        ------
        ValueError
            When nothing pins an artifact, when the sidecar or model does
            not verify, when the resolved ``env_params`` are not exactly
            JSON-safe, or when the environment breaks the Gymnasium reset
            or step contract. No partial aggregate is ever presented as a
            result: one broken episode fails the node.
        """
        reference = self.pinned_artifact(
            self.params.get("artifact"),
            (inputs or {}).get("artifact_path"),
            missing=(
                "no artifact reference — set params['artifact'] or wire "
                "inputs['artifact_path'] from an sb3-train node"
            ),
        )
        sidecar = self._read_sidecar(reference)
        env_ref, env_params = self._resolved_environment(sidecar)
        model = self._load_model(reference, sidecar)
        episodes = [
            self._episode(index, env_ref, env_params, model)
            for index in range(self.n_episodes())
        ]
        summary = self._summary(episodes)
        self.log.info(
            "rolled %d episode(s) of %s on %s (the %r split's env): mean "
            "return %.6f over %d step(s)",
            summary["n_episodes"], sidecar["algo"], env_ref,
            self.params["split"], summary["mean_return"],
            summary["total_steps"],
        )
        return {
            "metrics": dict(summary),
            "episodes": JsonArtifact({
                "schema": EPISODE_SCHEMA,
                "environment": self._provenance(
                    env_ref, env_params, reference, sidecar
                ),
                "episodes": episodes,
                "summary": summary,
            }),
        }

    def _resolved_environment(self, sidecar):
        """The env this run measures on — declared, or the trained one.

        Its params are held to exact JSON-safety BEFORE anything is
        constructed: they are recorded verbatim in the persisted
        evidence, and a value JSON cannot hold would fail the write after
        every episode had already run.
        """
        env_ref = self.params.get("env") or sidecar["env"]
        env_params = self.params.get("env_params")
        if env_params is None:
            env_params = sidecar["env_params"]
        problem = _json_safe_problem(env_params, "env_params")
        if problem:
            raise ValueError(
                f"{self.key}: {problem}. The resolved env_params are "
                "recorded verbatim in this node's evidence, so they are "
                "checked before the environment is built"
            )
        return env_ref, env_params

    def _episode(self, index, env_ref, env_params, model):
        """One episode, on a FRESH environment that is always closed.

        Fresh per episode by design: an environment carried across
        episodes could remember the previous one, and a recorded seed
        that no longer determines the rollout is provenance for nothing.
        """
        seed = self.seed() + index
        env = self._build_env(env_ref, env_params)
        try:
            observation = self._reset(env, index, seed)
            trace = []
            for step in range(1, self.max_episode_steps() + 1):
                action = self._action(model, observation, index, step)
                observation, record = self._stepped(env, index, step, action)
                trace.append(record)
                if record["terminated"] or record["truncated"]:
                    break
            result = self._result(index, seed, trace)
        except BaseException:
            self._close_quietly(env, index)
            raise
        env.close()
        return result

    def _close_quietly(self, env, index):
        """Close an environment on the way out of a FAILURE.

        A bare ``finally`` would let a raising ``close()`` REPLACE the
        reset, step or validation error that brought us here, leaving the
        message that explains the failure reachable only through
        ``__context__``. On the SUCCESS path there is no such error, so a
        close that fails there IS the failure and propagates normally —
        which is why this is not simply wrapped around both.
        """
        try:
            env.close()
        except Exception:  # noqa: BLE001 - never outrank the real refusal
            self.log.warning(
                "episode %d: env.close() raised while unwinding; the "
                "original refusal stands",
                index, exc_info=True,
            )

    def _action(self, model, observation, index, step):
        """The policy's action, or a refusal naming episode and step.

        Every other refusal in this class identifies itself; a bare
        ``action, _state = model.predict(...)`` on a non-pair raises
        "not enough values to unpack", naming neither the node, the
        episode, nor the artifact it restored.
        """
        answer = model.predict(observation, deterministic=self.deterministic())
        if not (isinstance(answer, tuple) and len(answer) == 2):
            raise ValueError(
                f"{self.key}: episode {index} step {step}: predict(...) "
                f"returned {answer!r}, not the two-item (action, state) "
                "tuple stable-baselines3 defines"
            )
        return answer[0]

    def _reset(self, env, index, seed):
        """The first observation, or a refusal naming the EPISODE only.

        There is no step number yet, which is exactly why a reset-shape
        refusal must not invent one.
        """
        outcome = env.reset(seed=seed)
        where = f"episode {index}"
        if not (isinstance(outcome, tuple) and len(outcome) == 2):
            raise ValueError(
                f"{self.key}: {where}: reset(seed={seed}) returned "
                f"{outcome!r}, not the two-item (observation, info) tuple "
                "the Gymnasium API defines"
            )
        observation, info = outcome
        if not isinstance(info, dict):
            raise ValueError(
                f"{self.key}: {where}: reset(seed={seed}) returned info "
                f"{info!r}, which is not a dict"
            )
        return observation

    def _stepped(self, env, index, step, action):
        """``(observation, step record)``, or a refusal naming episode AND step.

        The record deliberately keeps no observation, action or info
        payload: those may be huge, secret, or not JSON at all, and what
        a domain wants remembered about them is the child's to define.
        """
        outcome = env.step(action)
        where = f"episode {index} step {step}"
        if not (isinstance(outcome, tuple) and len(outcome) == 5):
            raise ValueError(
                f"{self.key}: {where}: step(...) returned {outcome!r}, not "
                "the five-item (observation, reward, terminated, truncated, "
                "info) tuple the Gymnasium API defines"
            )
        observation, reward, terminated, truncated, info = outcome
        value = _episode_reward(reward)
        if value is None:
            raise ValueError(
                f"{self.key}: {where}: reward is {reward!r}, not a finite "
                "real number — a bool is not one, and True would enter the "
                "return as 1.0 where a stream of flags averages cleanly"
            )
        for name, flag in (("terminated", terminated), ("truncated", truncated)):
            if not isinstance(flag, bool):
                raise ValueError(
                    f"{self.key}: {where}: {name} is {flag!r}, not a bool — "
                    "the Gymnasium API defines both flags as bool; convert a "
                    "numpy scalar inside the environment"
                )
        if not isinstance(info, dict):
            raise ValueError(
                f"{self.key}: {where}: info is {info!r}, which is not a dict"
            )
        return observation, {
            "step": step,
            "reward": value,
            "terminated": terminated,
            "truncated": truncated,
        }

    def _result(self, index, seed, trace):
        """One episode's record: its trace, its total, and WHY it ended.

        Precedence is ``terminated``, then ``truncated``, then the cap.
        When Gymnasium sets both flags the record keeps both true and the
        reason is ``terminated`` — which is why the aggregate counts below
        read this field rather than the flags.
        """
        last = trace[-1]
        terminated, truncated = last["terminated"], last["truncated"]
        total = math.fsum(record["reward"] for record in trace)
        if not math.isfinite(total):
            raise ValueError(
                f"{self.key}: episode {index}'s return is {total!r} — a "
                "reward stream that sums past what a float can hold "
                "measures nothing"
            )
        return {
            "episode": index,
            "seed": seed,
            "steps": len(trace),
            "return": total,
            "terminated": terminated,
            "truncated": truncated,
            "reason": (
                "terminated" if terminated
                else "truncated" if truncated
                else "max_episode_steps"
            ),
            "trace": trace,
        }

    def _summary(self, episodes):
        """The seven flat numbers, every one derived from the records above.

        The three reason counts are EXCLUSIVE, and they are counted off
        each episode's own ``reason`` rather than by summing the raw
        flags. An episode that sets both ``terminated`` and ``truncated``
        — which Gymnasium permits — would otherwise be counted twice,
        while every per-episode ``reason`` still read correctly and no
        precedence test noticed.
        """
        returns = [episode["return"] for episode in episodes]
        count = len(returns)
        mean = math.fsum(returns) / count
        variance = math.fsum((value - mean) ** 2 for value in returns) / count
        reasons = [episode["reason"] for episode in episodes]
        summary = {
            "n_episodes": count,
            "mean_return": mean,
            "std_return": math.sqrt(variance),
            "total_steps": sum(episode["steps"] for episode in episodes),
            "terminated_episodes": reasons.count("terminated"),
            "truncated_episodes": reasons.count("truncated"),
            "max_episode_steps_episodes": reasons.count("max_episode_steps"),
        }
        unusable = sorted(
            name for name, value in summary.items() if not math.isfinite(value)
        )
        if unusable:
            raise ValueError(
                f"{self.key}: {unusable} came out non-finite — a search "
                "cannot rank what it cannot measure"
            )
        return summary

    def _provenance(self, env_ref, env_params, reference, sidecar):
        """The nine facts saying WHAT produced this record."""
        return {
            "env": env_ref,
            "env_params": env_params,
            "artifact_path": reference,
            "state_hash": sidecar["state_hash"],
            "algo": sidecar["algo"],
            "policy": sidecar["policy"],
            "split": self.params["split"],
            "deterministic": self.deterministic(),
            "seed": self.seed(),
        }


#: The pack's registerable kinds — concrete classes only.
NODE_KINDS = (
    ("sb3-train", Sb3Train),
    ("sb3-policy", Sb3Policy),
    ("sb3-eval", Sb3Eval),
    ("sb3-eval-episodes", Sb3EvalEpisodes),
)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default the toolkit
    registry) — explicit and idempotent, the libs doctrine."""
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)

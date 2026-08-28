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
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, TrainableNode
from dskit.pipeline.split_policy import SPLIT_NAMES

__all__ = [
    "ARTIFACT_FORMAT",
    "NODE_KINDS",
    "Sb3Eval",
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


def _params_dict_problem(problems, name, value):
    if value is not None and (
        not isinstance(value, dict)
        or any(not isinstance(k, str) or not k for k in value)
    ):
        problems.append(
            f"{name} must be a dict of constructor kwargs (string keys), "
            f"got {value!r}"
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


#: The pack's registerable kinds — concrete classes only.
NODE_KINDS = (
    ("sb3-train", Sb3Train),
    ("sb3-policy", Sb3Policy),
    ("sb3-eval", Sb3Eval),
)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default the toolkit
    registry) — explicit and idempotent, the libs doctrine."""
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)

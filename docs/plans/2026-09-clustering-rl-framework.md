# Segmentation and reinforcement-learning pipeline plan

**Status:** proposed 2026-09-13. This is an implementation contract, not
authority to run models. ADR-0122 and all gates in §9 require owner approval
before the first failing test.

## 1. Reset outcome and placement

The general placement is mostly right, with an important correction: no new
pipeline framework is needed. `FittedTransform` is the existing tier-1 lifecycle;
`sklearn.py` and `sb3.py` are the tier-2 library packs. This work adds one
`FittedTransform` member to the sklearn pack and one compatible SB3 evaluation
member to the SB3 pack. It creates no new core module or package.

```text
dskit/pipeline/fitted.py          existing lifecycle plus default sidecar hook
dskit/pipeline/libs/sklearn.py    SklearnSegment (new)
dskit/pipeline/libs/sb3.py        Sb3EvalEpisodes (new, legacy-compatible)
pyproject.toml                    rl optional extra (new)
children/<project>/...            future environment and economic policy only
```

This replaces the prior `ArtifactEnvelope`, `BinaryFittedTransform`,
`SealedDataset`, Gym source registry, publication protocol, and `sb3-*-v2`
proposal. None is implemented. Evidence for the reset: three security reviews
required an ever-expanding trusted-local binary/source protocol, while accepted
`FittedTransform` already provides split selection, all-row application, JSON
state/load, and purity; accepted `_Sb3Base` already provides Env subclass checks,
zip-plus-sidecar integrity, pinned artifact loading, and `finally: close()`.
Duplicating either seam is the boundary defect, not a missing capability.

`origin/main` is correct. Existing fitted-state sidecars, `sklearn-fit`,
`sklearn-predict`, `sklearn-select`, `sb3-train`, `sb3-policy`, `sb3-eval`, and
`Sb3PolicySignal` remain behaviorally and artifact compatible.

## 2. Scope and local-trust boundary

The additions support configuration-driven research pipelines. They do not
certify an environment, reward, transition, market simulation, fill model, or
economic conclusion. Pipeline JSON configures a child `env` import path and
JSON `env_params`; the child must separately define and test their semantics.

The WSL2 operator controls DSKit code, installed packages, JSON documents,
environment code, and local model artifact paths. Existing joblib and SB3
deserializers remain trusted-local only: their hashes catch drift/corruption,
not hostile Python code. No production-release claim or new trust authority is
introduced.

Out of scope: transductive clustering; generic cluster-quality promotion;
stable semantic segment names across refits; a Gym registry; new artifact
formats; SB3 training/policy lifecycle changes; vector/distributed RL, resume,
GPU determinism, callbacks, HPO, real market replay, paper trading, lockbox or
full backtesting.

Use `segment`, never `cluster`: `cluster` already names split/dependence/event
identity in DSKit.

## 3. `SklearnSegment`: exact public contract

### 3.1 Class, role, wires, and parameters

`SklearnSegment(FittedTransform)` lives in `dskit/pipeline/libs/sklearn.py`, is
exported in `__all__`, and is added as `("sklearn-segment", SklearnSegment)` to
the existing explicit `NODE_KINDS`/`register()` table. Its role and inherited
outputs are exactly:

```text
role: fitted_transform
inputs: rows; inherited optional artifact_path pin in load mode
outputs: transform, rows, metrics, segment_model_id
```

Its exact `_PARAMS` is `FittedTransform._PARAMS + ("algorithm",
"algorithm_params", "features", "seed")`. `fit_split`, `order_field`, and
`purity_check` retain their accepted meanings and validators. `validate_params`
only says a supplied `fit_split` must be `"train"`; it cannot inspect mode.
`validate_train_inputs` and the first train-path operation require literal
`fit_split: "train"` before sklearn work. Load may omit it under ADR-0040.
`FittedTransform.sidecar_problems(payload)` is a new default hook returning `[]`,
called unconditionally in `_sidecar` after basic payload shape and before
emission; `SklearnSegment` overrides it to require payload `fit_split: "train"`.
Thus omitted load cannot restore a val/test artifact; existing subclasses stay
unchanged. `rows` always emits every input row in original order.

`algorithm` is required and exactly one of `"kmeans"`, `"minibatch_kmeans"`,
or `"birch"`. It maps only to `sklearn.cluster.KMeans`,
`MiniBatchKMeans`, or `Birch`; arbitrary class paths are deliberately excluded.
`algorithm_params` defaults to `{}`, must be a dict with non-empty string keys,
and forwards only to the selected constructor. `features` is required: a
non-empty, duplicate-free list of non-empty strings. Each row must supply each
feature as a finite real number (bool is not numeric). `SklearnSegment` owns
all per-row admission in `row_problems(rows)`: every row must be a mapping,
every declared feature must be present and finite, and neither reserved output
key (`segment`, `segment_model_id`) may already be present. That one rule is
used by the direct `FittedTransform.validate_common_inputs` doorway and by a
carrier's `ApplyTransform.validate_inputs` doorway before either can fit, load,
or project. No later `apply_state` collision or numeric failure is an
alternative validator.

For `kmeans` and `minibatch_kmeans`, `seed` is an optional exact non-bool integer
`0..2**32-1`, default `0`; it is passed as `random_state`. Supplying
`algorithm_params.random_state` refuses, so there is one source of randomness.
For `birch`, `seed` must be absent and `algorithm_params.random_state` refuses:
Birch has no random-state contract and recording a seed it cannot consume would
be false provenance. Constructor errors are wrapped with the node key and
algorithm name; a resulting empty center set, wrong center width, non-finite
center, malformed label, or an estimator lacking the required fitted attributes
refuses before state is written.

### 3.2 Fit, extracted JSON state, and all-row assignment

`fit(rows, params)` receives only the base-selected `fit_split` rows, builds the
matrix in declared feature order, constructs the closed-catalog estimator, and
fits it. It does **not** persist a joblib/native estimator.

It returns this exact JSON-safe state, with no additional keys:

```json
{
  "schema": "dskit.sklearn-segment/v1",
  "algorithm": "kmeans|minibatch_kmeans|birch",
  "features": ["x1", "x2"],
  "centers": [[0.0, 1.0]],
  "center_labels": [0]
}
```

For KMeans and MiniBatchKMeans, `centers` is `cluster_centers_` in sklearn's
stored order and `center_labels` is exactly `[0, ..., len(centers)-1]`. For
Birch, `centers` is `subcluster_centers_` and `center_labels` is
`subcluster_labels_` in the same index order; repeated global labels are valid.
Every center has exactly `len(features)` finite JSON numbers; every label is an
exact non-bool integer `>= 0`; the lists are non-empty and equal-length.

`state_problems` validates that schema completely on load and that every
document-supplied `algorithm`/`features` restates it exactly. `algorithm_params`
and `seed` describe fitting, not the extracted predictor, so load must not
require them or claim they can recreate a native model. `FittedTransform` then
owns class, split, sidecar, JSON, and purity verification without change.
`SklearnSegment.validate_load_inputs` rejects any `seed` or `algorithm_params`;
its `sidecar_problems` repeats this fail-closed rule and requires recorded
`fit_split: "train"`. Train accepts those fitting knobs. Tests cover train and
load behavior independently.

`segment_model_id` is lowercase SHA-256 of the repository's existing canonical
JSON encoding (sorted keys, compact separators, `allow_nan=False`) of the exact
state object, UTF-8 encoded. It is recomputed from restored state; never copied
from an artifact field.

`apply_state` is row-wise and stateless. For each row, it calculates squared
Euclidean distance to every center in declared feature order; ties select the
lowest **center index**. It emits a new row preserving all fields plus exactly
`"segment": center_labels[selected_index]` and
`"segment_model_id": <state hash>`. The `row_problems` screen refuses existing
`segment` or `segment_model_id` keys rather than overwriting input evidence.
The ordinary base screen must prove single-row and full-stream application agree.
Fixture tests must prove this rule matches the selected sklearn estimator's
`predict` for unique-distance and exact-tie matrices for all three catalog
members; Birch comparison is against its global predicted label.

`state_outputs` emits exactly `{"segment_model_id": <hash>}` so the hash is a
port as well as a field on every output row. Metrics are numeric only and exactly
`{"n_rows": <base all-row count>, "n_fit_rows": <base count>, "n_segments":
<number of distinct center_labels>}`. No cluster score is reported or used for
selection.

### 3.3 JSON example

```json
{
  "key": "regime",
  "kind": "sklearn-segment",
  "params": {
    "fit_split": "train",
    "features": ["feature_a", "feature_b"],
    "algorithm": "kmeans",
    "algorithm_params": {"n_clusters": 3, "n_init": 1},
    "seed": 17
  },
  "inputs": {"rows": "$features.rows"}
}
```

The surrounding document must declare cuts that materialize `train`; the base
rejects a missing/empty/unassignable fit selection before sklearn imports.

## 4. Gymnasium/SB3: retain the accepted lifecycle

Do not add a Gymnasium pack/module. Existing `_Sb3Base._build_env` already
imports Gymnasium only at run time, resolves `env` as `pkg.module:Class`, requires
a `gymnasium.Env` subclass, constructs `Class(**env_params)`, and names rejected
parameters. Existing `Sb3Train`, `Sb3Policy`, `Sb3Eval`, zip `model.zip`,
`model.json` hash sidecar, pin resolution, restore, `Sb3PolicySignal`, and close
discipline are unchanged.

Add one sibling in `dskit/pipeline/libs/sb3.py`:

```text
class Sb3EvalEpisodes(_Sb3Base)
kind: sb3-eval-episodes
role: score
inputs: optional artifact_path only
outputs: metrics, episodes (JsonArtifact)
```

It uses the existing `_read_sidecar`, `pinned_artifact`, `_load_model`, and
`_build_env` services. It writes no artifact and changes neither `ARTIFACT_FORMAT`
nor `_SIDECAR_KEYS`. `NODE_KINDS` gains this one kind; `__all__` gains its class.
`Sb3EvalEpisodes` explicitly sets `_SIDECAR_CHECK = ("algo", "policy")`, exactly
like `Sb3Eval`: omitted `env`/`env_params` default from the sidecar and a
deliberately different evaluation environment is lawful, while mismatched
algo/policy refuses. `episodes` is `JsonArtifact` from the existing node seam;
the driver persists and reloads its standard path/sha256/bytes/media_type
manifest. No new persistence seam or artifact format is introduced.


Its exact parameters are `algo`, `artifact`, `deterministic`, `env`,
`env_params`, `max_episode_steps`, `n_episodes`, `policy`, `seed`, and `split`.
They have existing `Sb3Eval` meanings except that `max_episode_steps` is required
here. `split` is exactly `"val"` or `"test"`; `train` and every other
`SPLIT_NAMES` value refuse in direct construction and at plan. `artifact`,
`algo`, and `policy` cross-check the sidecar as `Sb3Eval`
does. `env`/`env_params` default from that sidecar when omitted, and may differ
for evaluation. `n_episodes` is exact non-bool int `1..10_000` (default 5);
`max_episode_steps` is exact non-bool int `1..1_000_000`; `seed` is exact
non-bool int `0..2**32-1` (default 0) and `seed+n_episodes-1` must not exceed
`2**32-1`; `deterministic` is exact bool (default true). Unknown keys and
non-dict/string-keyed `env_params` refuse using existing helpers.
Before environment construction, resolved `env_params` must be recursively
JSON-safe: exact built-in dict/list/string/bool/int-or-finite-float/null values,
non-empty string dict keys, and no non-finite number, tuple, bytes, subclass, or
other object. `n_episodes * max_episode_steps` must be at most `1_000_000`,
checked during parameter validation before any artifact or environment I/O. This
bounds trace allocation; the validated resolved params are safe metrics evidence.


### 4.1 Manual episode contract

After verified sidecar/model restore, each episode creates a fresh environment,
calls `reset(seed=seed+i)`, then unpacks `action, _state =
model.predict(observation, deterministic=deterministic)` before calling
`env.step(action)` until a terminal condition.
The environment closes in a `finally` for every episode, including model/reset/
step/validation errors. An env failure raises immediately: no partial aggregate
is presented as a result.

`reset` must return a two-item `(observation, info)` tuple. `step` must return a
five-item `(observation, reward, terminated, truncated, info)` tuple. Reward is
converted only from a finite non-bool real; `terminated` and `truncated` must be
bool. Each `info` must be a dict. Violations name episode and step. Observation,
action, and info remain environment data; DSKit does not define their economic
meaning.

At each step the trace stores this exact JSON-safe record:

```json
{"step": 1, "reward": 0.25, "terminated": false, "truncated": false}
```

It deliberately records no arbitrary observation/action/info payload, which may
be huge, secret, or non-JSON. The child owns any richer domain audit evidence.

An episode stops after a step with `terminated` or `truncated`, otherwise after
the `max_episode_steps`th step. Its reason precedence is `terminated`, then
`truncated`, then `max_episode_steps`; when Gym sets both flags, the trace keeps
both true and reason is `terminated`. Each episode result is exactly:

```json
{
  "episode": 0,
  "seed": 17,
  "steps": 4,
  "return": 1.5,
  "terminated": true,
  "truncated": false,
  "reason": "terminated",
  "trace": []
}
```

`episodes` is durable evidence, not a metric: `JsonArtifact` wraps exactly
`{"schema":"dskit.sb3-eval-episodes/v1","environment":<object>,
"episodes":<ordered episode-result list>,"summary":<object>}`, with no other
keys. `environment` has exactly `env`, `env_params`, `artifact_path`,
`state_hash`, `algo`, `policy`, `split`, `deterministic`, and `seed`; those
values are the resolved environment plus verified sidecar/model provenance.
`episodes` is the ordered list of the exact episode-result records above, whose
`trace` is the ordered list of the exact step records above. Environment and
provenance appear only in this artifact. `summary` and flat numeric `metrics`
mirror exactly these seven keys: `n_episodes`, `mean_return`, `std_return`,
`total_steps`, `terminated_episodes`, `truncated_episodes`, and
`max_episode_steps_episodes`. The last three are exclusive reason counts. Each
return is `math.fsum(trace rewards)`; mean is `math.fsum(returns)/n`, population
std is `sqrt(math.fsum((r-mean)**2)/n)`, and total steps is
`sum(episode steps)`. Tests pin bytes, sha256, schema and keys.

All recorded numeric values must be finite. This is deterministic *evidence*
for a deterministic environment and model under recorded seeds; it is not an
SB3/Torch cross-host bitwise-reproducibility promise.

### 4.2 RL configuration example

```json
{
  "key": "eval",
  "kind": "sb3-eval-episodes",
  "params": {
    "split": "val",
    "env": "my_child.envs:ReplayEnv",
    "env_params": {"scenario": "validation"},
    "n_episodes": 3,
    "max_episode_steps": 500,
    "seed": 17,
    "deterministic": true
  },
  "inputs": {"artifact_path": "$train.artifact_path"}
}
```

The JSON declares configuration, not a proof that the child environment really
uses validation data, realistic fills, or causal rewards. A future child ADR
must bind that policy before any realistic backtest.

## 5. Dependency and documentation changes

Add to `[project.optional-dependencies]`:

```toml
rl = ["gymnasium>=1.3,<1.4", "stable-baselines3>=2.9,<2.10"]
```

Append those same two bounded requirements to `all`, preserving every existing
extra. Imports remain function-local, so an install without `[rl]` still plans
documents and fails only when an SB3 run path is invoked. Update SB3 module docs
and the package/README capability lists only if their existing inventories name
the concrete kinds; do not create a new Gym docs tree.

## 6. Focused strict-TDD slices

No implementation starts before the gates in §9. The selected GLM/DeepSeek
author works one slice at a time: write the listed failing focused test, show
RED, write minimum GREEN, rerun only that test file, then request review.

1. **Segmentation validation.** In `tests/pipeline_libs/test_sklearn.py`, add
   unknown-parameter, catalog, feature, seed, and constructor rejection tests.
   Add focused direct `SklearnSegment.validate_common_inputs` and second-stream
   `ApplyTransform.validate_inputs` cases for a non-mapping row, a missing or
   non-finite/bool declared feature, and pre-existing `segment` or
   `segment_model_id`; both doorways must refuse through
   `SklearnSegment.row_problems` before execution. Because `SklearnSegment`
   remains abstract through this slice, define one tiny test-only concrete
   subclass that supplies the not-yet-tested `fit` and `apply_state` hooks and
   raises if either is called. Use it only to reach the inherited validators;
   do not assert or mock the subclass, and add no production placeholder.
   Implement only validation and `row_problems`.
2. **Train state.** Add a tiny split context fixture proving train-only fit,
   all-row emission, exact state schema/hash, load compatibility, corrupted or
   contradictory JSON state refusal, and no joblib file. Implement fit/state.
3. **Assignment.** Add KMeans, MiniBatchKMeans, and Birch fixed fixtures proving
   prediction parity, lowest-index ties, existing output-field refusal, and base
   one-row purity. Implement row-wise assignment; only now is
   `SklearnSegment` concrete. Then, in separate RED-to-GREEN microcycles, add
   its `__all__` export, exact `NODE_KINDS`/idempotent `register()` assertions,
   and the live sklearn conformance census. Extend `EXPECTED_ROLES` exactly to
   `{"sklearn-fit": "train", "sklearn-predict": "signal", "sklearn-select":
   "fitted_transform", "sklearn-segment": "fitted_transform"}`. In its existing
   `probes(tmp_path)`, add the one populated `sklearn-segment` `NodeProbe`;
   do not alter any legacy probe. Its real split-context fixture trains a segment
   once, retains its sidecar, carrier, canonical `segment_model_id`, and
   expected projection of a distinct probe row stream. The runnable probe
   supplies valid train params and stream rows, the fixture sidecar as
   `load_artifact`, and a `verify_loaded` that proves restored state identity
   and exact projected rows (including the fixture model id), so a fresh fit on
   the probe rows cannot pass. Do not register or add this live conformance probe
   before assignment is GREEN.
4. **SB3 episode contract, isolated.** Create
   `tests/pipeline_libs/test_sb3_eval_episodes.py`. It imports
   `NodeProbe` and `conformance_suite` from `dskit.pipeline.conformance`;
   `NODE_KINDS`, `Sb3Eval`, `Sb3EvalEpisodes`, `Sb3Policy`, `Sb3Train`, and
   `register` from `dskit.pipeline.libs.sb3`; and `JsonArtifact`, `NodeContext`,
   and `NodeKindRegistry` from `dskit.pipeline.node`. It has no
   `pytest.importorskip` and imports neither Gymnasium nor Stable-Baselines3.
   Use only local stubs: a model whose `predict` returns `(fixed_action, state)`,
   fresh plain-Python environments from patched `_build_env`, and patched
   `_read_sidecar`/`_load_model`; no zip is read and no SB3 object is constructed.
   Assert export, role, outputs, parameters/limits, algo/policy pinning,
   val/test-only split refusal, and no new artifact path. Assert this exact
   ordered `NODE_KINDS`: `("sb3-train", Sb3Train)`,
   `("sb3-policy", Sb3Policy)`, `("sb3-eval", Sb3Eval)`, and
   `("sb3-eval-episodes", Sb3EvalEpisodes)`.
5. **Manual evaluation and one-kind conformance, still stub-only.** In that
   file, prove fresh seeds, predict unpacking, exact trace/episode-result/artifact
   schemas and provenance, the seven metrics and formulas, reason precedence,
   step cap, malformed reset/step refusal, non-finite reward refusal, and close
   on reset, predict, step, and validation errors. Assert `episodes` is
   `JsonArtifact` before persistence. Define
   `EPISODE_NODE_KINDS = (("sb3-eval-episodes", Sb3EvalEpisodes),)`,
   `EXPECTED_ROLES = {"sb3-eval-episodes": "score"}`, and
   `TestSb3EvalEpisodesConformance = conformance_suite(...)` over that one-kind
   registry only. Its stub-only `NodeProbe` has valid `split: "val"` and bounded
   `max_episode_steps`, an `artifact_path` input, `stream_ports=()`, and
   `runnable=True`; it executes the output contract without training. Never
   invoke `learn`, HPO, real Gymnasium/SB3, or market data.

   The current `tests/pipeline_libs/test_sb3.py` is deliberately excluded:
   its module-level `pytest.importorskip("stable_baselines3")`, `train()`, and
   `probes(tmp_path)` create a PPO fixture through `train(tmp_path)`, so
   `TestSb3Conformance` can invoke `model.learn`. Do not run that class, any
   training test, or the broad file. If registration changes, run only
   `tests/pipeline_libs/test_sb3.py::test_register_is_explicit_and_idempotent`;
   import `Sb3EvalEpisodes` there and assert the four-entry registry without
   calling `train`.

   For eventual full-suite consistency, change the existing
   `TestSb3Conformance` call from live `NODE_KINDS` to an explicit three-entry
   `LEGACY_NODE_KINDS` tuple for train/policy/eval. Keep its existing three-entry
   `EXPECTED_ROLES` and train-backed `probes()` unchanged; do not add the new
   kind or a `NodeProbe` there. The dedicated one-kind suite above owns the new
   kind's role census and behavioural probe, so no conformance gap or broad
   training path is hidden.
   The slices explicitly pin train-mode omitted/val/test segment-fit refusal and
   load-mode optional/declared-train sidecar checks; the dedicated
   `segment_model_id` port and numeric-only metrics; SB3 default/different eval
   environment parameters, algo/policy mismatch, direct train/cal split refusal,
   recursive invalid params, trace-cap pre-I/O refusal, predict unpacking, and
   persisted JSON-artifact bytes/digest/schema checks.

6. **Compatibility/review fixes.** Run only affected files after each correction:

```bash
python -m pytest tests/pipeline_libs/test_sklearn.py -q
python -m pytest tests/pipeline_libs/test_sb3_eval_episodes.py -q
# Only if registration changed; never select the whole sb3 file.
python -m pytest tests/pipeline_libs/test_sb3.py::test_register_is_explicit_and_idempotent -q
```

After owner-approved dependency installation, use
`python -m pip install -e '.[sklearn,rl]'` once, then those focused commands.
Never run the full suite for this plan unless a reviewer demonstrates a direct
cross-package requirement.

## 7. Proportional local failure matrix

| Failure | Refusal/test owner |
| --- | --- |
| train/val/test leakage or empty fit slice | existing `FittedTransform` plan/run behavior; segment split tests |
| bad features, catalog, seed, constructor kwargs | `SklearnSegment.validate_params` and focused tests |
| malformed row, non-finite declared feature, or pre-existing segment output key | `SklearnSegment.row_problems` through both direct fitted and carrier/`ApplyTransform` focused tests |
| native model persistence or loaded native state | test no joblib output; JSON sidecar/load tests |
| assignment drift/tie change | sklearn parity fixtures for all catalog members |
| state/document mismatch | inherited class/split checks plus `state_problems` tests |
| corrupt/wrong SB3 zip or sidecar | existing `_read_sidecar` hash/cross-check tests; no format change |
| endless or malformed Gym episode | step cap and reset/step shape/type/refusal tests |
| resource leak | close-spy tests on every error path |
| false determinism/economic realism claim | recorded-seed wording and child-ADR/no-execution tests |
| hostile local pickle/package | out of v1 trust boundary; existing trusted-local policy, no new security claim |

## 8. Skeptic loop and convergence

Before mutation dispatch, the owner must select an **exact** GLM or DeepSeek
model ID and add a verified working `.agent-chain` mapping. The chain record
must name that model as author. It writes RED then minimal GREEN for each slice.

One Terra skeptic is active at a time. After each author pass or correction,
run two sequential fresh Terra reviews: (1) architecture/compatibility/
leakage/identity; (2) testability/determinism/Gym lifecycle/legacy artifact
compatibility. Each review reads the actual diff and focused test output, reports
Critical/Major/correctness findings or an explicit clean verdict. Any edit,
including a review correction, restarts both lenses. All corrections and every
subsequent review are Terra. Acceptance requires zero Critical, Major, or
correctness findings in the final two clean reviews.

After three non-convergent correction cycles, stop and write a checkpoint:
list repeated finding classes, root cause, smallest reset, retained accepted
seams, discarded design, owner choices, and the precise restart point. Do not
self-adjudicate or silently widen scope.

The mandatory checkpoint for this correction ruled **CONTINUE**: review3 Major
#3 is localized to test selection, not the accepted lifecycle, trust boundary,
or artifact design. Retain reset scope; restart at segmentation-validation RED,
and defer registration plus live conformance until the concrete assignment
slice, then resume both skeptic lenses.

## 9. Owner gates and strict no-execution gates

Required before RED: (1) owner approval of ADR-0122 and this reset plan;
(2) exact GLM/DeepSeek model ID plus working chain mapping; (3) approval to
install the bounded optional dependencies; (4) approval of a future child ADR
before any child environment/reward/transition/order/fill semantics; and (5)
approval of each proposed document/config whose identity changes.

Until those gates and final skeptic clearance: do not install dependencies, run
tests, train an SB3 model, run clustering, conduct HPO/final refit, replay
markets, backtest, paper trade, access a lockbox, commit, push, or merge. Tiny
synthetic focused tests become authorized only after RED/implementation approval;
they must not call real SB3 training or use market data.

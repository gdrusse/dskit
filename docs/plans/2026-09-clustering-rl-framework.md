# Segmentation and reinforcement-learning pipeline plan

**Status:** proposed 2026-09-13; **owner-approved and implemented 2026-09-17**
(§10). This is an implementation contract, not authority to run models. The §9
gates are dispositioned in §10; the economic gates among them stay CLOSED.

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
`purity_check` retain their accepted meanings and validators UNCHANGED:
`validate_params` is the base's own classmethod, mode-blind, and still
accepts any `SPLIT_NAMES` value (`"train"`, `"val"`, `"cal"`, or `"test"`) for
`fit_split` — it cannot inspect mode, so it cannot narrow to `"train"` itself.
The narrowing to literal `"train"` is enforced downstream, where mode is
known, at TWO defense-in-depth sites (matching this class's `sidecar_problems`
precedent of repeating a fail-closed rule rather than trusting one check):
`validate_train_inputs` refuses first; `fit()` itself repeats the exact same
check as its first operation, before touching sklearn, so a future caller of
`fit()` outside the normal `run_train` path cannot bypass the restriction
either. Both are Slice 1/2 tests, not one: a plan declaring `fit_split: "val"`
and mode-train still passes `validate_params`/plan-time shape checks, then
refuses at `validate_train_inputs` (Slice 1's focused test), and — separately —
calling `SklearnSegment.fit(rows, {"fit_split": "val", ...})` directly (bypassing
`validate_train_inputs`) also refuses, proving `fit()`'s own repeated check
(Slice 2's focused test). "Passes an earlier gate, refused by a later one" is
easy to under-implement silently — hence testing both sites, not just one.
Load may omit it under ADR-0040.
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
here, and `split` is a deliberate NEW narrowing, not a restatement of existing
behavior: `Sb3Eval.validate_params` today accepts every `SPLIT_NAMES` value —
`"train"`, `"val"`, `"cal"` (ADR-0034's inner-val calibration band), and
`"test"` — confirmed by `test_sb3.py`'s own
`test_reference_params_validate_clean`. `Sb3EvalEpisodes` instead accepts only
`"val"` or `"test"`; `"train"`, `"cal"`, and any other value refuse in
`validate_params`. The narrowing is deliberate: `episodes` is durable,
persisted per-episode audit evidence of a policy's held-out performance, not
a scalar score a search can read from any split — evidence fitted on
`"train"` or drawn from the `"cal"` calibration sub-band would misrepresent
itself as genuine out-of-sample performance. Like `Sb3Eval`, `Sb3EvalEpisodes(_Sb3Base)` implements its
own `run` (`_Sb3Base` supplies none; `Node.run` is `@abstractmethod`), so the
class stays abstract — uninstantiable with any params, valid or not — until
that `run` is written. Before then, this refusal is provable only through the
`validate_params` classmethod, never by constructing the node. `artifact`,
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
bool. Each `info` must be a dict. A `step`-shape violation names episode and
step; a `reset`-shape violation names episode only (there is no step number
yet). Observation,
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
   unknown-parameter, catalog, `features` (empty, duplicate, non-string,
   empty-string entry), and `algorithm_params`/constructor-kwarg-shape
   rejection tests. `seed`/`algorithm_params.random_state` is
   algorithm-conditional, not one "seed" bucket — four atomic fixtures, not
   one: kmeans/minibatch_kmeans with `algorithm_params.random_state` set
   (refuses, regardless of `seed`); birch with `seed` set alone, no
   `random_state` (refuses); birch with `algorithm_params.random_state` set
   alone, no `seed` (refuses); kmeans/minibatch_kmeans with a valid `seed` and
   no `random_state` (accepts) — an implementation collapsing birch's two
   refusals into one "birch + either" check would pass a fixture that only
   ever sets both together and never prove either refuses alone. Add a
   focused test that a train-mode document declaring `fit_split: "val"`
   (or `"cal"`/`"test"`) passes the inherited, mode-blind `validate_params`
   unchanged (still any `SPLIT_NAMES` value) but refuses at
   `validate_train_inputs` — the two-gate split described in §3.1. Add focused direct `SklearnSegment.validate_common_inputs` and second-stream
   `ApplyTransform.validate_inputs` cases, one fixture per atomic case since
   `row_problems` is bespoke logic this class alone owns, inheriting no
   existing coverage: a non-mapping row; a declared feature ABSENT from the
   row; a declared feature present but non-finite; a declared feature present
   but bool (matching `records.number_ok`'s own documented hazard — a
   finiteness-only check would silently accept a bool feature as 0/1);
   pre-existing `segment` already present; and pre-existing `segment_model_id`
   already present, separately (an implementation checking only one reserved
   key would pass a fixture that only ever sets the other) — six fixtures, not
   two compound ones. Both doorways must refuse through
   `SklearnSegment.row_problems` before execution. Because `SklearnSegment`
   remains abstract through this slice, define one tiny test-only concrete
   subclass that supplies the not-yet-tested `fit` and `apply_state` hooks and
   raises if either is called. Use it only to reach the inherited validators;
   do not assert or mock the subclass, and add no production placeholder.
   Implement only validation and `row_problems`.
2. **Train state.** `SklearnSegment` is still abstract here: `apply_state` is
   not implemented until Slice 3, and the accepted `FittedTransform.run_train`
   and `run_load` both end by calling the base's `_emit`, which calls
   `apply_state` unconditionally to build the `rows`/`transform` outputs —
   neither is callable yet, and Python's ABC construction refuses the class
   outright before either could run. Test `fit` and the sidecar directly,
   never through `.run()`: define a second tiny test-only subclass, distinct
   from Slice 1's, that overrides ONLY `apply_state` as a raiser and leaves
   `fit` unoverridden so it resolves to `SklearnSegment.fit` — the real
   implementation this slice adds (Slice 1's subclass stays Slice-1-only: its
   own `fit` override would shadow the real one and never exercise it). Call
   `fit(fit_rows, params)` on this new subclass to prove train-only fit
   selection and the exact state schema/hash; prove `fit()`'s own repeated
   `fit_split == "train"` check refuses a non-train `params["fit_split"]`
   passed directly to `fit()`, independent of `validate_train_inputs` (§3.1);
   prove a wrapped constructor error names the node key and algorithm when
   the sklearn constructor raises (patch the constructor to raise, per
   catalog member); and prove `fit()` refuses before writing state when the
   fitted estimator yields an empty center set, wrong center width, a
   non-finite center, or lacks a required fitted attribute (one fixture per
   case). §3.2's label invariant — "exact non-bool integer `>= 0`" — is
   THREE independent conditions an `or`-combined implementation could
   half-satisfy (matching this codebase's own idiom, e.g. `sb3.py`'s
   `_params_dict_problem`, `node.py`'s `check_int_param` bool-exclusion): a
   non-int label, a bool label (bool is an int in Python — `True`/`False`
   could silently pass as `1`/`0` if unexcluded), and a negative label, each
   its own fixture. Also prove `centers`/`center_labels` length mismatch
   refuses — §3.2 requires BOTH non-empty AND equal-length, and Slice 2's
   other fixtures only cover non-emptiness; for Birch, `centers` and
   `center_labels` come from two separately-read sklearn attributes
   (`subcluster_centers_`/`subcluster_labels_`), so a mismatch is real and
   independently mockable, not implied by the empty-set case. These are
   `fit()`'s own documented runtime defenses, not `validate_params`'s
   plan-time shape checks, and need their own tests here rather than being
   assumed covered by Slice 1's unrelated constructor-kwarg rejection tests.
   Call the base's
   `_sidecar` directly to prove load-mode schema
   verification, the new `sidecar_problems` hook's train-only enforcement, and
   corrupted or contradictory JSON state refusal; and prove no joblib file is
   written. `sidecar_problems` and its unconditional call site are new
   **tier-1** `FittedTransform` behavior (`fitted.py`), shared by every
   existing member, not sklearn-specific — so this slice also adds one
   dedicated tier-1 RED test in `tests/pipeline/test_fitted.py` proving the
   base default returns `[]` and is called unconditionally in `_sidecar`, and
   that adding the call site changes nothing observable for `Standardize`'s
   existing load-mode behavior (its default hook stays a no-op; `test_fitted.py`
   already has a concrete `Standardize` fixture to prove this on). `FeatureSelector`
   is abstract in `test_fitted.py` (no concrete subclass there) — its own
   load-mode pin belongs to its actual dedicated file,
   `tests/pipeline/test_selector.py` (whose `TopMeans`/`Answers` subclasses and
   existing `TestTheArtifactIsTheColumns` class already exercise `mode="load"`):
   add the equivalent no-op-hook assertion there instead, on those existing
   fixtures, never inventing a new `FeatureSelector` subclass in `test_fitted.py`.
   Implement `fit`, `state_problems`, and `sidecar_problems` only. Do
   not assert `rows`/`transform` output or any `.run()`-level train/load
   behavior in this slice — that all-row emission and end-to-end load
   compatibility move to Slice 3, with `apply_state`.
3. **Assignment.** Add KMeans, MiniBatchKMeans, and Birch fixed fixtures proving
   prediction parity, lowest-index ties, existing output-field refusal, and base
   one-row purity. For KMeans and MiniBatchKMeans, add a two-part determinism
   fixture PER algorithm — do not assume one small shared dataset makes both
   seed-sensitive; verify divergence empirically for each during RED, since a
   fixture sized for one (MiniBatchKMeans's batch subsampling is seed-sensitive
   at a handful of rows) can converge to one answer regardless of seed for the
   other (KMeans typically needs a larger, less-separated fixture to have more
   than one reachable local optimum) — seed-sensitive by construction
   (`n_init=1` and rows placed so more than one locally-optimal partition is
   reachable from different random
   starts — `center_labels` alone is `[0, ..., len(centers)-1]` by definition
   and proves nothing about the seed, so the fixture must make `centers`
   itself vary with initialization): (a) fit the same rows twice with the
   SAME `seed`/`algorithm_params` and assert identical `centers` (not just
   `center_labels`) and `segment_model_id`; (b) fit the same rows with two
   DIFFERENT seeds and assert `centers` actually DIFFER. Test (a) alone
   cannot distinguish "seed pins reproducibility" from "fit is deterministic
   regardless of seed"; test (b) is what proves the knob is real and
   threaded through, not silently dropped by `fit()`. Implement row-wise assignment (`apply_state`); only now is
   `SklearnSegment` concrete, and only now do `run_train`/`run_load`'s all-row
   `rows`/`transform` emission and end-to-end load-mode `.run()` behavior
   become testable — prove them here. Then, in the SAME RED-to-GREEN
   microcycle — never split across several, which would leave `NODE_KINDS`
   and `EXPECTED_ROLES` transiently disagreeing and fail the conformance
   suite's own role/probe-coverage checks — add its `__all__` export, exact
   `NODE_KINDS`/idempotent `register()` assertions, and the live sklearn
   conformance census. This also means updating the existing, currently
   3-entry `tests/pipeline_libs/test_sklearn.py::test_node_kinds_table_and_roles`,
   whose exact-dict equality against the live `NODE_KINDS` (`table == {...}`)
   otherwise fails the moment `sklearn-segment` is added: extend its expected
   dict to the fourth entry in the same change. Extend `EXPECTED_ROLES` exactly to
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
4. **SB3 episode contract, isolated — validation only.** Create
   `tests/pipeline_libs/test_sb3_eval_episodes.py`. It imports
   `NodeProbe` and `conformance_suite` from `dskit.pipeline.conformance`;
   `NODE_KINDS`, `Sb3Eval`, `Sb3EvalEpisodes`, `Sb3Policy`, `Sb3Train`, and
   `register` from `dskit.pipeline.libs.sb3`; and `JsonArtifact`, `NodeContext`,
   and `NodeKindRegistry` from `dskit.pipeline.node`. It has no
   `pytest.importorskip` and imports neither Gymnasium nor Stable-Baselines3.
   `Sb3EvalEpisodes` is still abstract here: like `Sb3Eval`, it implements its
   own `run` (`_Sb3Base` supplies none), `Node.run` is `@abstractmethod`, and
   `run` is not implemented until Slice 5 — so the class cannot be
   constructed yet, with any params. Test parameters/limits, catalog/algo/
   policy checks, val/test-only split refusal, and §4's
   `n_episodes * max_episode_steps <= 1_000_000` cross-param bound (matching
   `SklearnFit.validate_params`'s own seed/`random_state`-conflict precedent
   for a cross-param check living in `validate_params`, not `run`) — all by
   calling the classmethod
   `Sb3EvalEpisodes.validate_params({...})` directly, never by constructing a
   node. `validate_inputs` is an instance method (unlike `validate_params`),
   so reaching it needs a live instance despite `run` being unwritten: define
   one tiny test-only concrete subclass, exactly the Slice 1 pattern, that
   overrides ONLY `run` as a raiser to permit construction; use it solely to
   reach the inherited `pin_port_problems`-backed `validate_inputs` — do not
   assert or mock the subclass, and add no production placeholder. Assert
   `role`, `outputs`, and `_PARAMS` as class attributes. Do **not** yet add
   `Sb3EvalEpisodes` to the production `NODE_KINDS` in `sb3.py`, `__all__`, or
   call `register()` on it: that table is shared with the existing, untouched
   `tests/pipeline_libs/test_sb3.py`, whose
   `test_register_is_explicit_and_idempotent` calls the real `register()` (which
   rejects any abstract class via `node_class_errors`/`abstract_class_problem`,
   `dskit/pipeline/base.py`) and whose `TestSb3Conformance` builds
   `conformance_suite(registry=NODE_KINDS, ...)`, parametrizing every registered
   kind unconditionally — including one with no probe there yet, which falls
   back to `object.__new__(cls)` and raises the same abstract-class `TypeError`.
   Widening the live table here would crash that sibling file's own tests, not
   just this slice's. Implement only `validate_params`, `validate_inputs`, and
   class attributes — not `run`, and not registration.
5. **Manual evaluation and one-kind conformance.** Implement `run`; only now
   is `Sb3EvalEpisodes` concrete, and only now are the local stubs used: a
   model whose `predict` returns `(fixed_action, state)`, fresh plain-Python
   environments from patched `_build_env`, and patched
   `_read_sidecar`/`_load_model`; no zip is read and no SB3 object is
   constructed. In that file, prove fresh seeds, predict unpacking, exact
   trace/episode-result/artifact schemas and provenance, and the seven
   metrics and formulas. Reason precedence and the three exclusive reason
   counts are not two separate claims to test separately: the same
   both-flags-true episode fixture (§4.1: `terminated`/`truncated` both true
   → `reason: "terminated"`) must ALSO be asserted to count toward
   `terminated_episodes` only, never `truncated_episodes` — an implementation
   that sums raw `terminated`/`truncated` flags directly, rather than
   deriving the three counts from `reason`, would pass a reason-precedence
   test that only inspects the per-episode `reason` field and a metrics test
   built on episodes where the two flags never coincide, while silently
   violating "exclusive" the moment they do. Prove step cap. §4.1's reset/step shape rule
   bundles SEVEN independent conditions — one fixture per atomic case, not one
   "malformed reset/step" fixture: a `reset` return that isn't a 2-tuple; a
   `step` return that isn't a 5-tuple; a non-finite (but non-bool) `reward`;
   a bool `reward` — separately, matching this codebase's own
   `records.number_ok` precedent ("bool is excluded explicitly: it is an
   `int` in Python, so without the check `True` would pass as 1"), since a
   finiteness-only check would silently accept `reward=True`/`False` as
   `1.0`/`0.0`; a non-bool `terminated`; a non-bool `truncated`; and a
   non-dict `info`. Close
   on reset, predict, step, and validation errors, and no artifact path
   beyond `episodes`. Prove §4's recursive
   JSON-safety refusal on the resolved (sidecar-defaulted) `env_params` before
   `_build_env` is called — this check is only reachable once `run` exists to
   resolve and pass it, so it belongs here, not Slice 4 — with one fixture per
   ATOMIC case (a compound English description is not one case: "non-string
   or empty-string" and "nested inside a list/dict" are each two independent
   branches an `or`-combined implementation could half-satisfy), exactly as
   Slice 2's degenerate-estimator refusals are enumerated: a top-level tuple;
   a top-level bytes value; a non-finite float; a non-string dict key (e.g.
   an int key) — separately from — an empty-string dict key (`{"": 1}`), so
   an implementation that checks type but not emptiness (or vice versa) is
   caught by one but not the other; a violation nested inside a `dict` value
   — separately from — a violation nested inside a `list` element, so a
   recursive walker that descends into one container type but not the other
   is caught by one but not the other; and a `dict`/`str`/`int` subclass
   instance (proving "exact built-in" means exact-type, not `isinstance`,
   since a subclass would otherwise slip through) — eight fixtures in total.
   A single passing example does not prove recursion or exact-type checking,
   and a compound fixture proves at most one of its two branches. Assert `episodes` is
   `JsonArtifact` before persistence. Only now, in the same RED-to-GREEN
   microcycle as `run`, add `Sb3EvalEpisodes` to the production `NODE_KINDS`
   in `sb3.py` and `__all__`, and assert this exact ordered `NODE_KINDS`:
   `("sb3-train", Sb3Train)`, `("sb3-policy", Sb3Policy)`,
   `("sb3-eval", Sb3Eval)`, and `("sb3-eval-episodes", Sb3EvalEpisodes)`.
   In the SAME commit — never after — change `test_sb3.py`'s existing
   `TestSb3Conformance` from live `NODE_KINDS` to the explicit three-entry
   `LEGACY_NODE_KINDS` tuple described below, so the widened table and the
   sibling file's isolation from it land atomically; a widening committed
   without that change, even briefly, reproduces this same crash. Define
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

   In that same commit, change the existing
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
python -m pytest tests/pipeline/test_fitted.py tests/pipeline/test_selector.py -q
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
| non-`"train"` `fit_split` passing the mode-blind `validate_params` gate | `validate_train_inputs`'s literal-`"train"` refusal (Slice 1) AND `fit()`'s own repeated check (Slice 2) — two sites, two tests |
| bad features, catalog, constructor kwargs | `SklearnSegment.validate_params` and focused tests |
| birch/kmeans seed vs. `random_state` cross-algorithm confusion | `SklearnSegment.validate_params`; Slice 1, four atomic fixtures (kmeans+random_state, birch+seed-alone, birch+random_state-alone, kmeans+valid-seed) — collapsing birch's two refusals into one fixture proves neither alone |
| non-mapping row, missing/non-finite/bool declared feature, or pre-existing `segment`/`segment_model_id` output key | `SklearnSegment.row_problems` through both direct fitted and carrier/`ApplyTransform` focused tests; Slice 1, six atomic fixtures — bespoke logic owning no inherited coverage, so one compound fixture proves only one branch |
| native model persistence or loaded native state | test no joblib output; JSON sidecar/load tests |
| sklearn constructor raise, or a fitted estimator with an empty/mismatched/non-finite center set, or a non-int/bool/negative label | `SklearnSegment.fit()`'s wrapped-error and degenerate-output refusals; Slice 2, one fixture per atomic case (empty set, wrong width, non-finite center, length mismatch, missing attribute, non-int label, bool label, negative label) — a compound fixture proves at most one branch |
| `seed` silently ignored (non-determinism) | Slice 3 two-part fixture on KMeans/MiniBatchKMeans: same-seed round-trip (identical `centers`) AND different-seed comparison (`centers` differ) — same-seed alone can't rule out seed-independent determinism |
| recursive non-JSON-safe resolved `env_params` reaching environment construction | `Sb3EvalEpisodes.run`'s pre-construction recursion check; Slice 5, eight atomic fixtures (top-level tuple, top-level bytes, non-finite float, non-string key, empty-string key, dict-nested violation, list-nested violation, builtin subclass) — a compound fixture proves at most one of its branches |
| assignment drift/tie change | sklearn parity fixtures for all catalog members |
| state/document mismatch | inherited class/split checks plus `state_problems` tests |
| shared tier-1 `FittedTransform` hook regression (`sidecar_problems`) | dedicated `tests/pipeline/test_fitted.py` default/call-site test plus existing `Standardize` fixture there; `FeatureSelector`'s own load-mode pin added to its actual owning file, `tests/pipeline/test_selector.py`, on its existing `TopMeans`/`Answers` fixtures |
| shared `NODE_KINDS` table admits an abstract class between slices | Slice ordering: widen `NODE_KINDS`/`__all__`/registration only in the RED-to-GREEN microcycle that also makes the class concrete, never before; `test_register_is_explicit_and_idempotent` and `TestSb3Conformance`/`TestSklearnConformance` are the tripwire |
| corrupt/wrong SB3 zip or sidecar | existing `_read_sidecar` hash/cross-check tests; no format change |
| endless or malformed Gym episode | step cap; seven atomic reset/step shape fixtures (bad `reset` tuple, bad `step` tuple, non-finite reward, bool reward, non-bool `terminated`, non-bool `truncated`, non-dict `info`) — a compound fixture proves at most one branch |
| excessive trace allocation (`n_episodes * max_episode_steps > 1_000_000`) | `Sb3EvalEpisodes.validate_params`'s cross-param bound, matching `SklearnFit.validate_params`'s own seed/`random_state`-conflict precedent (`sklearn.py:1732-1741`) — provable via the classmethod alone, no construction; Slice 4 |
| exclusive reason-count metrics derived from raw flags, not `reason` | the both-flags-true reason-precedence fixture (§4.1) reused to assert it counts toward `terminated_episodes` only; Slice 5 |
| resource leak | close-spy tests on every error path |
| false determinism/economic realism claim | recorded-seed wording and child-ADR/no-execution tests |
| hostile local pickle/package | out of v1 trust boundary; existing trusted-local policy, no new security claim |

## 8. Skeptic loop and convergence

Before mutation dispatch, the owner must select an **exact** GLM or DeepSeek
model ID and add a verified working `.agent-chain` mapping. The chain record
must name that model as author. It writes RED then minimal GREEN for each slice.

One Terra skeptic is active at a time — dispatch mode is SEQUENTIAL throughout
this loop (never parallel), per skeptic-review.md's "Dispatch mode" section,
each review a fresh clean-context dispatch. After each author pass or correction,
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

A second mandatory checkpoint, triggered by a fresh architecture-lens review
of this document (Claude Sonnet 5, acting in the Terra reviewer/author role
under explicit 2026-09-13 owner authorization to substitute for GLM/DeepSeek
Terra on this pass), also ruled **CONTINUE**. Finding: former §6 Slice 2
claimed to prove "all-row emission" via "Implement fit/state," but
`FittedTransform.run_train`/`run_load` both end in `_emit`, which calls
`apply_state` unconditionally — and `apply_state` is not implemented until
Slice 3, where the document itself already said "only now is `SklearnSegment`
concrete." `Node(ABC)` plus `@abstractmethod fit`/`apply_state`
(`dskit/pipeline/node.py:467`, `dskit/pipeline/fitted.py:464,490`) means the
class cannot even be constructed, let alone run `.run()`-level train/load,
before Slice 3. This is the same repeated shape as the prior checkpoint's
registration/conformance defect (a slice asserting concrete-lifecycle
behavior one slice before the class is concrete), found one slice earlier —
but it disproves only a test-boundary claim, not the accepted
`FittedTransform`/SB3 reuse, the §3 contract, or the trust boundary. Retain
reset scope; smallest sound fix applied: Slice 2 now tests `fit`/`_sidecar`
directly and Slice 3 now explicitly owns all-row `rows`/`transform` emission
and end-to-end `.run()` load compatibility. Resume both skeptic lenses from
segmentation-validation RED.

A fresh independent Terra architecture-lens review of that second checkpoint's
diff (dispatched with a clean context, per skeptic-review.md Rule 2) found two
further Major/correctness issues, confirming this is now a **repeated defect
class** across three separate spots in this plan (the earlier registration/
conformance defect, the Slice 2 all-row-emission defect just fixed, and these
two), which independently triggers the checkpoint under skeptic-review.md
Rule 8 regardless of cycle count. (1) CONFIRMED: former §6 Slice 4 asserted
"val/test-only split refusal... in direct construction," but
`Sb3EvalEpisodes(_Sb3Base)` implements its own `run` exactly as `Sb3Eval`
does (`_Sb3Base` supplies none), `Node.run` is `@abstractmethod`
(`dskit/pipeline/node.py:591`), and `run` was not implemented until Slice 5 —
the identical construction-time defect, one slice earlier, now in the SB3
track. (2) PLAUSIBLE, now fixed regardless: the second checkpoint's own Slice
2 correction said to call `fit` "on the Slice 1 test-only concrete subclass,"
but that subclass overrides `fit` itself (as a raiser, per Slice 1); calling
`fit` on it would dispatch to that raiser, never to `SklearnSegment.fit`. Root
cause across all three instances: a slice's test claims were written from the
finished contract in §3/§4 rather than checked against which hooks that
slice's own concrete/abstract boundary has actually implemented. Still
localized to test-boundary wording, not the accepted `FittedTransform`/SB3
reuse or the §3/§4 contract — retain reset scope. Smallest sound fix applied:
Slice 4 is now validation-only (`validate_params` classmethod, no
construction) and Slice 5 owns `run` plus every behavior needing it; Slice 2's
`fit`-only stand-in is now its own subclass, separate from Slice 1's. Resume
both skeptic lenses from segmentation-validation RED.

A fourth mandatory checkpoint, from a second fresh independent Terra
architecture-lens review (again a clean-context dispatch) of that third
correction's diff, also ruled **CONTINUE**. Two more issues, both localized:
(1) MAJOR/CONFIRMED, a **fourth** instance of the same repeated shape: the
prior fix left Slice 4 saying "Implement... `validate_inputs`," but
`Node.validate_inputs` is an instance method (`node.py:585`), unlike the
classmethod `validate_params` — reaching it still needs a live
`Sb3EvalEpisodes`, impossible before `run` exists. Fixed by applying Slice
1's own established pattern (a tiny test-only subclass overriding only `run`
as a raiser, solely to reach `validate_inputs`) rather than inventing a new
technique. (2) MAJOR, a genuine gap rather than the repeated shape: the new
`sidecar_problems` hook and its `_sidecar` call site are **tier-1**
`FittedTransform` behavior shared by every existing member, but owned no
dedicated tier-1 test, failure-matrix row, or explicit "existing subclasses
unchanged" pin — an asserted-not-proven compatibility claim per this repo's
own "a pinning test that omits a knob is worse than none" doctrine. Fixed by
adding a dedicated `tests/pipeline/test_fitted.py` RED test (Slice 2) and a
§7 failure-matrix row. Both fixes are localized to test-plan completeness,
not the accepted architecture, contract, or trust boundary — retain reset
scope. Root cause across four instances now: every prior pass checked the
already-touched slices but not a slice's OWN full "Implement" list against
the hook inventory, nor whether a base-class change had a base-class-level
test. Resume both skeptic lenses from segmentation-validation RED.

A fifth mandatory checkpoint, from a third fresh independent Terra
architecture-lens review (clean-context dispatch) of that fourth correction's
diff, ruled **CONTINUE**. One MAJOR/CONFIRMED finding, a fifth instance of the
repeated shape, this time as collateral damage to a previously-untouched
sibling file rather than a false claim inside its own slice: Slice 4 required
asserting the live, production `NODE_KINDS` in `sb3.py` as its final 4-entry
form — meaning Slice 4's own GREEN step would widen that shared table to
include `Sb3EvalEpisodes` while `run` (and thus concreteness) does not exist
until Slice 5. `tests/pipeline_libs/test_sb3.py::test_register_is_explicit_and_idempotent`
(existing, untouched) calls the real `register()` over that same live table,
which rejects any abstract class (`node_class_errors`/`abstract_class_problem`,
`dskit/pipeline/base.py`); `TestSb3Conformance` there parametrizes every
registered kind unconditionally and falls back to `object.__new__(cls)` for
one with no probe yet, which also raises for an abstract class — confirmed by
empirical repro, not only by reading. The sklearn track already had this
right (Slice 3 defers `SklearnSegment`'s addition to `sklearn.py`'s live
`NODE_KINDS` until `apply_state` lands); the SB3 track regressed that
discipline. Still localized to slice-ordering, not the accepted architecture
or contract — retain reset scope. Smallest sound fix applied: Slice 4 no
longer touches production `NODE_KINDS`/`__all__`/registration at all; Slice 5
adds `Sb3EvalEpisodes` to them in the same RED-to-GREEN microcycle as `run`,
in the same commit as switching `test_sb3.py`'s `TestSb3Conformance` to the
explicit `LEGACY_NODE_KINDS` tuple — never one without the other. Added a §7
failure-matrix row pinning this ordering permanently. Root cause across all
five instances, restated once more for the restart: a registration/table
change is exactly as slice-bound as the hook it depends on, in EITHER
direction of dependency (a slice's own tests needing a hook not yet written,
or a shared table change breaking a sibling file that reads it) — check both
before calling a slice's boundary correct. Resume both skeptic lenses from
segmentation-validation RED.

A sixth mandatory checkpoint, from a fourth fresh independent Terra
architecture-lens review (clean-context dispatch) of that fifth correction's
diff, ruled **CONTINUE**. One MAJOR/CONFIRMED finding: the checkpoint above
said the sklearn track "already had this right" for deferring `NODE_KINDS`
concreteness — true for `apply_state`/construction, but incomplete for table
INVENTORY. Former Slice 3 widened `sklearn.py`'s live `NODE_KINDS` to four
entries in "separate RED-to-GREEN microcycles" from `EXPECTED_ROLES`, and
named no update to the existing, currently-3-entry
`tests/pipeline_libs/test_sklearn.py::test_node_kinds_table_and_roles`, whose
exact-dict equality against that same live table breaks the moment the fourth
entry lands — the identical shared-table-widened-without-full-inventory shape
as checkpoint 5, now found on the sklearn side, self-revealing as an ordinary
assertion failure (not a crash) because §6 item 6 already runs the whole
`test_sklearn.py` file, unlike the deliberately narrow SB3 command. Still
localized to slice-completeness, not the architecture or contract — retain
reset scope. Smallest sound fix applied: Slice 3's `NODE_KINDS`/`__all__`/
`register()`/`EXPECTED_ROLES`/conformance-probe additions now land in one
microcycle, matching Slice 5's SB3 wording, and Slice 3 now names
`test_node_kinds_table_and_roles`'s expected-dict update explicitly. Root
cause, restated a final time: "the shared table's concreteness ordering is
right" and "the shared table's every existing reader is accounted for" are
two separate claims — five of six checkpoints now have addressed the first:
this one closes the second, sklearn-side gap the fifth checkpoint's SB3 fix
already modeled correctly for its own track. Resume both skeptic lenses from
segmentation-validation RED.

A seventh mandatory checkpoint, from a fifth fresh independent Terra
architecture-lens review (clean-context dispatch) of that sixth correction's
diff, ruled **CONTINUE**. One MAJOR/CONFIRMED finding, a new shape (not the
ABC/table-widening family, but the same underlying cause — a claim written
from the finished contract without checking the named file/class can support
it): former Slice 2 claimed its `tests/pipeline/test_fitted.py` RED test
would prove the new `sidecar_problems` call site changes nothing for
`Standardize` **and** `FeatureSelector`'s load-mode behavior — but
`FeatureSelector` is abstract in that file (no concrete subclass exists
there; `surviving_features` is `@abstractmethod`, `fitted.py:1500`), so
nothing there can exercise its load path at all. `FeatureSelector`'s actual
load-mode coverage lives in `tests/pipeline/test_selector.py`, via its
existing `TopMeans`/`Answers` concrete fixtures and
`TestTheArtifactIsTheColumns` class — a file the plan never named or ran.
Localized to which file houses one assertion, not the architecture, the
`sidecar_problems` contract, or the trust boundary — retain reset scope.
Smallest sound fix applied: Slice 2 now pins `Standardize` in
`test_fitted.py` (which already has a fixture for it) and `FeatureSelector`
in `test_selector.py` (which already has fixtures for it) separately, never
inventing new scaffolding in the wrong file; §6 item 6 and §7 now name
`test_selector.py`. Resume both skeptic lenses from segmentation-validation
RED.

An eighth mandatory checkpoint, from a sixth fresh independent Terra
architecture-lens review (clean-context dispatch) of that seventh
correction's diff, ruled **CONTINUE**. One MAJOR/CONFIRMED finding, again the
same root cause in a new shape — an unverified claim about existing code,
this time in §4's contract prose rather than a slice's test-boundary timing:
former §4 said `Sb3EvalEpisodes`'s val/test-only `split` restriction refuses
`"train"` and every other `SPLIT_NAMES` value "exactly as `Sb3Eval` already
does." `Sb3Eval.validate_params` (`sb3.py:516-521`) in fact accepts every
`SPLIT_NAMES` value — `test_sb3.py::test_reference_params_validate_clean`
explicitly asserts `split: "cal"` is lawful for `Sb3Eval` (citing ADR-0034),
and nothing there refuses `"train"` either. The restriction is a genuine NEW
design choice for `Sb3EvalEpisodes`, misrepresented as settled precedent.
Localized to one paragraph's factual accuracy, not the architecture or the
restriction's own soundness (Slice 4 already tests the real, narrower
behavior directly against `Sb3EvalEpisodes`, not against a false premise
about `Sb3Eval`) — retain reset scope. Smallest sound fix applied: §4 now
states the narrowing honestly, names `Sb3Eval`'s actual four-way acceptance,
and gives the rationale (durable per-episode audit evidence must not be
drawn from `"train"` or the `"cal"` calibration sub-band and presented as
held-out performance). Resume both skeptic lenses from segmentation-validation
RED.

A ninth mandatory checkpoint, from a seventh fresh independent Terra
architecture-lens review (clean-context dispatch) of that eighth correction's
diff, ruled **CONTINUE**. One MAJOR/CONFIRMED finding, the same "claim of
unchanged/inherited behavior contradicted two sentences later" shape as the
prior checkpoint, now found in §3.1 rather than §4: former text said
`fit_split`/`order_field`/`purity_check` "retain their accepted meanings and
validators," then immediately said `validate_params` "only says a supplied
`fit_split` must be `\"train\"`" — but the base's actual `validate_params`
(`fitted.py:393-398`) accepts any of all four `SPLIT_NAMES`, unchanged; it is
mode-blind (a classmethod) and cannot itself narrow to `\"train\"`. The real
narrowing happens downstream, at `validate_train_inputs`, exactly as the
plan's own next two sentences already said — the contradiction was
self-correcting in prose but left a genuine completeness gap: no Slice 1 test
and no §7 row pinned that a non-`"train"` `fit_split` passes the earlier gate
and is only caught by the later one, an easy silent under-implementation.
Localized to one paragraph plus a missing pin, not the architecture or the
two-gate design itself (which was already correct) — retain reset scope.
Smallest sound fix applied: §3.1 now states `validate_params` is unchanged
and mode-blind, names the real downstream enforcement point, and Slice 1 plus
§7 now pin the two-gate behavior with a focused test. Also addressed this
review's nit: §8 now states dispatch mode (sequential, clean-context, per
skeptic-review.md's "Dispatch mode" section) as part of the required trace.
Resume both skeptic lenses from segmentation-validation RED.

**Lens 1 (architecture/compatibility/leakage/identity) went CLEAN** on its
ninth fresh dispatch — the first clean lens-1 pass this loop has produced.

A tenth mandatory checkpoint follows, from the FIRST-EVER fresh dispatch of
**lens 2** (TDD executability/determinism/Gym lifecycle/artifact
compatibility) — every prior round had exercised only lens 1. It ruled
**CONTINUE**. THREE MAJOR findings, all genuine and all new (lens 1's scope
never covered them): (1) §3.1's `fit()` runtime defenses — wrapped
constructor errors, and refusing an empty/malformed/non-finite/attribute-
missing fitted estimator — had no assigned slice or §7 row; Slice 1's
constructor-kwarg tests are `validate_params`-level shape checks, a different
thing. (2) the two-gate `fit_split` design named `validate_train_inputs`
"and the first train-path operation" but only the first gate had a test or
§7 row; the second was real intended defense-in-depth (matching
`sidecar_problems`'s own repeated-check precedent) that had gone completely
unassigned. (3) no slice ever proved `seed` actually pins KMeans/
MiniBatchKMeans reproducibility via a same-seed same-rows round-trip;
schema-shape and single-fit-parity tests alone would stay green even if
`fit()` silently dropped `seed`. All three are the SAME shape as this
document's entire history — an asserted behavior with no test assigned to
prove it — just caught by a lens that had literally never run before. Not
the architecture or contract itself — retain reset scope. Smallest sound fix
applied: Slice 2 now tests `fit()`'s own repeated `fit_split` check and its
wrapped-error/degenerate-estimator refusals directly; Slice 3 now adds the
determinism round-trip; §3.1's two-gate sentence and §7 now name both sites.
Per skeptic-review.md Rule 4/this task's rule 5, BOTH lenses restart from
zero — lens 1's clean pass does not carry forward across this edit. Resume
both skeptic lenses from segmentation-validation RED.

**Lens 1 went CLEAN again** on its tenth fresh dispatch (second clean pass),
re-verifying all prior fixes plus this round's three additions against
source with no new finding.

An eleventh mandatory checkpoint follows, from lens 2's SECOND fresh
dispatch. It ruled **CONTINUE**. Two more MAJOR findings, same root shape as
this document's entire history: (1) the new Slice 3 determinism round-trip
was itself under-powered — `center_labels` is defined (§3.2) as exactly
`[0, ..., len(centers)-1]`, a function of the declared cluster COUNT only,
never of which partition was found, so asserting it "identical" between two
same-seed fits proves nothing about `seed`; and nothing required the fixture
to be seed-SENSITIVE in the first place, so a dropped `seed` could pass
undetected. (2) §4's "resolved `env_params` must be recursively JSON-safe"
runtime refusal — reachable only post-sidecar-defaulting, i.e. only inside
`run`— was never assigned to Slice 4 or Slice 5, and had no §7 row: the
identical "documented runtime defense, no assigned test" shape checkpoint 10
just fixed on the sklearn track, found one section over on the SB3 track,
where that fix didn't reach. Both localized to test adequacy/assignment, not
the architecture or contract — retain reset scope. Smallest sound fix
applied: Slice 3's fixture must now be seed-sensitive by construction
(`n_init=1`, an ambiguous partition) and prove BOTH same-seed reproducibility
(on `centers`, not `center_labels`) AND different-seed divergence — the pair
is what proves the knob is real; Slice 5 now owns the `env_params`
JSON-safety refusal explicitly, with a §7 row. Resume both skeptic lenses
from segmentation-validation RED.

**Lens 1 went CLEAN a third time**, re-verifying the two new fixes
architecturally sound (no reopened abstractness/table-widening issue) plus a
full re-sweep, including tracing `JsonArtifact` persistence byte-for-byte
against `driver.py`.

A twelfth mandatory checkpoint follows, from lens 2's THIRD fresh dispatch —
which also independently installed scikit-learn in a scratch environment to
empirically test the Slice 3 fixture claims, not just read them. It ruled
**CONTINUE**. Both checkpoint-11 fixes verified correct (empirically: 20
seeds against a real 2-blob fixture produced 5 distinct KMeans/
MiniBatchKMeans partitions, and 5/5 identical same-seed centers). One new
MAJOR finding: the Slice 5 `env_params` JSON-safety test, though now
assigned, named no per-case enumeration — the identical "one fixture per
case" gap Slice 2's degenerate-estimator refusals were already held to,
found one section over. Localized to test-specification completeness, not
architecture — retain reset scope. Smallest sound fix applied: Slice 5 and
§7 now enumerate the six required cases (top-level tuple/bytes, non-finite
float, bad dict key, nested violation, builtin subclass). Also folded in an
empirically-grounded nit: Slice 3 now says a fixture sized for one algorithm
may not seed-sensitize the other (confirmed: a fixture that made
MiniBatchKMeans seed-sensitive left plain KMeans converging to one answer
across 16/16 seeds at the same scale) — verify divergence per algorithm
during RED, not from one assumed-shared dataset. Resume both skeptic lenses
from segmentation-validation RED.

**Lens 1 went CLEAN a fourth time**, confirming the six-case enumeration
introduced no new abstractness/placement issue and re-sweeping the whole
document (including tracing `JsonArtifact`'s manifest against `driver.py`
directly) with nothing further found.

A thirteenth mandatory checkpoint follows, from lens 2's FOURTH fresh
dispatch. It ruled **CONTINUE**. One more MAJOR finding, found INSIDE the
very fix checkpoint 12 just applied: the "six cases" still bundled two
independent branches into single fixtures — "non-string **or**
empty-string" dict key, and "nested inside a list**/**dict" — mirroring
`_params_dict_problem`'s own real `not isinstance(k, str) or not k`
`or`-combined idiom elsewhere in this exact codebase (`sb3.py:83-91`,
`node.py:349,609`), which a real implementation of the new
check is highly likely to copy; a single fixture per compound item proves at
most one branch — the plan's own adjacent sentence ("a single passing
example does not prove recursion or exact-type checking") already said this
but wasn't applied to the dict-key/nesting items. Not architecture — retain
reset scope. Smallest sound fix applied: split into the true atomic set —
non-string key, empty-string key, dict-nested violation, list-nested
violation — bringing the enumeration to eight fixtures; §7 updated to match.
Resume both skeptic lenses from segmentation-validation RED.

**Lens 1 went CLEAN a fifth time**, confirming the eight-case split
introduced nothing new and the document's placement/leakage/identity design
remains sound end to end.

A fourteenth mandatory checkpoint follows, from lens 2's FIFTH fresh
dispatch, which specifically hunted for the same "compound enumeration"
shape elsewhere before declaring convergence. It ruled **CONTINUE**. THREE
more MAJOR/PLAUSIBLE findings (plausible, not confirmed, since no code
exists yet to contradict — but the same shape as five prior confirmed
findings, and well-supported by this exact codebase's own `or`-combined
validation idioms it would likely be copied from): (1) Slice 2's "malformed
label" fixture bundled THREE independent conditions from §3.2's own
invariant ("exact non-bool integer >= 0") — non-int, bool, and negative —
the identical bool-exclusion hazard this codebase's own `node.py` documents
by name elsewhere. (2) no fixture existed for a `centers`/`center_labels`
LENGTH MISMATCH — §3.2 requires both non-empty AND equal-length, but Slice 2
only tested non-emptiness; genuinely independent for Birch, whose two lists
come from separately-read sklearn attributes. (3) Slice 5's "malformed
reset/step refusal" bundled SIX independent shape/type checks from §4.1's
own text (bad reset tuple, bad step tuple, non-finite/bool reward, non-bool
terminated, non-bool truncated, non-dict info) into one undecomposed phrase,
left uncorrected when the adjacent env_params item was split two checkpoints
ago. All three are the same recurring root cause named throughout this
document's history — a compound English description is not one test case —
now applied to two enumerations the prior thirteen checkpoints hadn't yet
reached. Not architecture — retain reset scope. Smallest sound fix applied:
Slice 2 now lists eight atomic degenerate-output cases (was five compound);
Slice 5 now lists six atomic reset/step shape cases in place of the one
compound phrase; §7's two corresponding rows updated to match. Resume both
skeptic lenses from segmentation-validation RED.

A fifteenth mandatory checkpoint follows, from lens 1's THIRTEENTH fresh
dispatch (verifying the prior round's two decompositions introduced nothing
architectural). It ruled **CONTINUE**. One more MAJOR/PLAUSIBLE finding, the
SAME shape found ONE MORE TIME in the very enumeration checkpoint 14 just
rewrote: Slice 5's new "non-finite or bool `reward`" fixture was itself still
compound — §4.1's own wording ("finite non-bool real") is two branches, and
this codebase's `records.number_ok` documents by name exactly why bool must
be excluded separately from finiteness ("bool is an `int` in Python... `True`
would pass as 1"). A finiteness-only fixture would not catch an
implementation that forgot the bool exclusion. Also fixed an inaccurate
citation this same reviewer caught: checkpoint 13's `base.py:175` reference
does not actually contain the `or`-combined idiom it was cited for (it checks
key type only, no emptiness clause) — removed, the other two citations
(`sb3.py:83-91`, `node.py:349,609`) remain accurate on their own. Not
architecture — retain reset scope. Smallest sound fix applied: the reward
case splits into "non-finite (non-bool)" and "bool" separately, bringing
Slice 5's enumeration to seven atomic cases; §7 updated to match. Resume both
skeptic lenses from segmentation-validation RED.

**Lens 1 went CLEAN a sixth time**, including a dedicated citation-accuracy
audit of every file:line reference in §8's entire history — all found
accurate except the one already caught and fixed.

A sixteenth mandatory checkpoint follows, from lens 2's SIXTH fresh dispatch,
an explicitly exhaustive final sweep of every enumerated list in §3/§4/§6
against the document's own established rule. It ruled **CONTINUE**. FOUR more
MAJOR/PLAUSIBLE findings, all the same recurring shape, none previously
touched: (1) Slice 1's `row_problems` refusals ("missing or non-finite/bool
declared feature" and "pre-existing `segment` or `segment_model_id`") were
still two compound bundles of six real independent branches — bespoke logic
this class alone owns, inheriting no coverage from elsewhere. (2) Slice 1's
"seed... rejection tests" collapsed FOUR independent, algorithm-conditional
refusals (kmeans+random_state; birch+seed-alone; birch+random_state-alone;
kmeans+valid-seed) into one word — an implementation could plausibly refuse
birch on "seed or random_state" without ever proving either refuses alone.
(3) reason precedence and the three "exclusive" reason-count metrics were
tested as if independent, but nothing required the same both-flags-true
fixture to also prove the aggregate counts derive from `reason` rather than
summing raw flags directly — the exact interaction that fixture exists to
stress, left unpointed at the metrics. (4) §7 had no row at all for the
`n_episodes * max_episode_steps` pre-I/O trace-allocation bound, despite §4
naming it explicitly and Slice 5 already testing it — the one runtime
defense in the whole document missing its index row. None architectural —
retain reset scope. Smallest sound fix applied: Slice 1 now lists six atomic
`row_problems` fixtures and four atomic seed/random_state fixtures in place
of the two former buckets; Slice 5 now requires the both-flags-true fixture
to also prove the exclusive-count derivation; §7 gained three rows (the
birch/kmeans seed confusion, the trace-allocation bound, and the
reason-count-derivation check) and one row was rewritten for the
`row_problems` split. Resume both skeptic lenses from segmentation-validation
RED.

A seventeenth mandatory checkpoint follows, from lens 1's FIFTEENTH fresh
dispatch. It ruled **CONTINUE**. One more MAJOR/CORRECTNESS finding: the
prior checkpoint's own new §7 row attributed the `n_episodes *
max_episode_steps <= 1_000_000` bound to `Sb3EvalEpisodes.run`, contradicting
§4's own text two sentences earlier ("checked during parameter validation"),
and neither Slice 4 nor Slice 5 actually named it as an enumerated test case
— the checkpoint's own claim that "Slice 5 already testing it" did not match
Slice 5's actual prose. Both `n_episodes`/`max_episode_steps` are plain
literal ints with no `$`-reference support, so the bound belongs in
`validate_params` (provable via the classmethod alone, matching
`SklearnFit.validate_params`'s own cross-param precedent), not `run` — the
`.run` attribution would let an out-of-bound document plan clean and only
fail at execute time, a real posture regression against this repo's own
"discovering it at execute is discovering it after the expensive part"
doctrine. This is the exact recurring defect class surviving inside the very
fix meant to close it. Not architecture — retain reset scope. Smallest sound
fix applied: §7's row now attributes the check to `validate_params`; Slice 4
now explicitly names the bound as one of its classmethod-level test cases.
Resume both skeptic lenses from segmentation-validation RED.

## 9. Owner gates and strict no-execution gates

Required before RED: (1) owner approval of ADR-0148 and this reset plan;
(2) exact GLM/DeepSeek model ID plus working chain mapping; (3) approval to
install the bounded optional dependencies; (4) approval of a future child ADR
before any child environment/reward/transition/order/fill semantics; and (5)
approval of each proposed document/config whose identity changes.

Until those gates and final skeptic clearance: do not install dependencies, run
tests, train an SB3 model, run clustering, conduct HPO/final refit, replay
markets, backtest, paper trade, access a lockbox, commit, push, or merge. Tiny
synthetic focused tests become authorized only after RED/implementation approval;
they must not call real SB3 training or use market data.

## 10. Authorization and execution record (2026-09-17)

The owner authorized this work in session `claude/dskit-rl-clustering-zy2dge`:
ADR write/implement permission, autonomous completion, wrap, and a pull
request. **`ADR-0122` was renumbered `ADR-0148`** — the number was already
taken on `origin/main` by the attested ten-head release ADR, and this plan
branched from a disjoint history that never saw it.

§9 gate dispositions:

1. **ADR/plan approval — MET.** Owner-granted ADR authority; ADR-0148 is
   accepted in `docs/architecture/decision-log.md`.
2. **Exact model ID — MET, by substitution.** No GLM/DeepSeek Terra chain is
   reachable from this environment. `Claude Opus 5` is the author and both
   skeptic lenses, dispatched as fresh clean-context subagents, one lens at a
   time — the same substitution the 2026-09-13/14 owner authorizations granted
   `Claude Sonnet 5` for this document's own §8 loop.
3. **Dependency install — MET.** `pytest`/`hypothesis`/`ruff`, `scikit-learn`,
   `joblib`, `gymnasium` and `stable-baselines3` installed at the bounded
   versions §5 pins. Nothing else.
4. **Child ADR — NOT MET, and not needed here.** No child environment, reward,
   transition, order or fill semantics exist in this change. Unchanged.
5. **Per-config identity approval — NOT MET, and not needed here.** No document
   or config in this repository gained, lost or moved an identity-graded field.

Still forbidden and still not done: real SB3 training (`learn` is never
called), HPO, final refit, market replay, backtest, paper or live trading,
lockbox access. Every test is tiny, synthetic and local; the SB3 episode suite
constructs no SB3 or Gymnasium object at all. This change adds no production
authority.

## 11. Convergence checkpoint (2026-09-17, after review round 3)

**Triggered by a REPEATED FAMILY**, not by a cycle count — skeptic-review.md
lets a repeated family fire the checkpoint before three failed cycles, and
this one recurred across two consecutive rounds. Recorded before any fourth
patch, as the rule requires.

**Cycle history.** Round 1 (`fceb32a`): two lenses, both CLEAN; Minors
corrected in `f43b6fe`. Round 2 (`f43b6fe`): correctness lens CLEAN,
tests lens **FAIL, 2 Major** — failed cycle 1. Round 3 (`faa524c`): tests
lens **FAIL, 2 Major** — failed cycle 2.

**The family, stated precisely.** *An assertion whose two candidate sources
COINCIDE in the fixture, so it cannot distinguish them.* Every Major found
after round 1 is an instance:

| # | Round | The value | Sources that coincided |
|---|---|---|---|
| 1 | 2 | the episode outcome | `trace[-1]` vs `trace[0]`, equal on one-step episodes |
| 2 | 2 | `DEFAULT_SEGMENT_SEED` | two omitting fits agree under ANY fixed default |
| 3 | 3 | the environment rolled on | declared `env` vs the sidecar's, equal in the fixture |
| 4 | 3 | five provenance facts | same coincidence, plus `deterministic` only asserted at its default |

**Why the earlier fixes did not cover it.** Each was a POINT fix for the
instance the reviewer named — a multi-step fixture, a literal-zero
comparison — and none asked the general question the instances are answers
to. A reviewer finding instance *n* and an author fixing instance *n* is a
loop that terminates only when the reviewers run out of ideas, not when the
defect class is closed.

**The changed approach: inventory, then batch.** Enumerate EVERY value the
two new kinds publish or act on that has more than one candidate source, and
for each, either make the fixture's sources DIFFER or record why they
provably cannot. Done, with the result:

- `env`, `env_params` — declared vs sidecar: now differ (`TRAINED`).
- `algo`, `policy` — sidecar vs this pack's defaults: now differ.
- `deterministic`, `seed`, `n_episodes` — declared vs default: differ, and
  each asserted in BOTH the declared and the omitted case.
- the episode outcome — `trace[-1]` vs `trace[0]`: multi-step fixtures.
- `SEGMENT_SCHEMA`, `EPISODE_SCHEMA` — the record vs the constant it is
  compared against: now pinned to LITERALS, so a rename cannot move both
  sides of its own assertion.
- `n_segments` — distinct labels vs centre count: coincide for KMeans by
  construction, so it is now asserted on a many-to-one Birch state.
- `artifact_path` — `params["artifact"]` vs the wired port: CANNOT differ;
  `pinned_artifact` refuses a contradiction between them.
- `features`, `algorithm` — document vs restored state: CANNOT differ;
  `state_problems` refuses a load whose state disagrees, and a carrier
  always carries its own fitting node's state.
- `split`, `max_episode_steps`, `state_hash`, the step number, the episode
  index, every computed aggregate — single-sourced.

**Why the next round should differ from the last two.** The previous rounds
were reviewing patches; this one reviews a closed inventory. A round-4 Major
in this family now means the inventory MISSED a value, which is a different
and checkable claim — and the remedy would be to widen the inventory's
definition, not to patch another instance.

Scope, contract, authority and the trust boundary are unchanged by this
checkpoint. No production code moved in rounds 2 or 3: both were test-only.

### 11.1 The inventory was falsified (review round 4)

Round 4's brief was to falsify §11 rather than to re-review the patches,
and it did, twice. Recorded here rather than quietly amended above,
because a checkpoint that edits away its own wrong claim teaches nothing.

**§11 was WRONG about `artifact_path`.** It certified that the declared
param and the wired port "CANNOT differ; `pinned_artifact` refuses a
contradiction between them". They can. `Node.pinned_artifact`
(`node.py:898-908`) refuses only a contradiction between the NODE-LEVEL pin
and the declared param, then resolves declared-vs-wired by taking the first
non-empty. And `Node.node_level_pin()` answers `None` unconditionally — only
`TrainableNode` overrides it — so for a `score`-role kind that refusal branch
is dead code. A document may lawfully declare `params["artifact"]` AND wire
`$train.artifact_path` at two different files, and nothing says a word.

The error was mine and it was a reading, not a typo: I asserted a refusal
without checking that the branch containing it was reachable for this
class. Three mutants survived on it — swapping the two sources, loading the
MODEL from the wired port while verifying the sidecar on the declared one,
and the reverse. The last two are the sharp ones: the hash-verified sidecar
and the executed model would be different files, so the `state_hash` in the
durable record would attest an artifact that never ran.

Pinned now by one run-level case with both sources present and disagreeing,
asserting that ONE file wins EVERYWHERE — the verified sidecar, the restored
model, and the recorded `artifact_path`/`state_hash`.

**Disclosed, NOT fixed here:** that a declared/wired DISAGREEMENT is
resolved silently rather than refused is tier-1 behaviour shared by every
pinning kind in this pack (`sb3-eval`, `sb3-policy`) and beyond, and it is
untested for all of them. Refusing it would be a change to a shared service
with its own blast radius, which ADR-0148 does not cover. It needs its own
ADR; this branch only stops ITS kind from being incoherent about which file
it used.

**§11's DEFINITION was too narrow.** It asked "does this value have more
than one candidate SOURCE", and so filed `split` under "single-sourced" —
true, and beside the point. `_provenance`'s `split` was asserted at exactly
one value (`"val"`, the fixture's), so a hardcoded `"val"` passes: a
`test`-split evaluation would be labelled `val` in durable, hash-pinned
evidence. That is the same shape as `deterministic`, which §11 DID fix,
one question away.

The widened definition, which the rest of the inventory now answers: **a
value is unpinned when the fixtures exercise it at exactly one value AND
that value equals something the code could plausibly have hardcoded,
defaulted to, or read from the wrong place.** Swept under it: `split` (now
asserted at both `"val"` and `"test"`), `seed` (17 and the omitted 0),
`deterministic` (True and False), `n_episodes` (1, 2, 3 and the omitted 5),
`algo`/`policy` (the artifact's, differing from this pack's defaults),
`env`/`env_params` (declared, differing from the artifact's), both schema
tags (literals), `n_segments` (a many-to-one Birch state), and the episode
outcome (multi-step episodes).

**What this round did NOT overturn.** §11's other two "cannot differ"
claims — `features` and `algorithm` between document and restored state —
were traced through every path to `apply_state` by an independent reviewer
and confirmed SOUND.

# ADR-0091 seam — binding design note (orchestrator, from reading driver.py/node.py/planner.py)

Read with plan §9.1 (`docs/new_package_proposals/production.md`, grep `### 9.1`). §9.1 is the
contract; this note pins the details it leaves to the implementer so tests and code agree.
Invariants: `tests/pipeline/test_driver.py` + `test_kinds_search.py` pass UNTOUCHED; every one
of the 20 sha256 literals under `tests/` unmoved; pipeline purity gate green; `dskit/pipeline`
never imports `dskit.production`; no new document params, no grammar change.

## 1. `dskit/pipeline/node.py`
- `SERVING_EFFECTS = ("pure", "entry_read", "release_read", "forbidden")` (module constant, in `__all__`).
- `ServingContract` frozen dataclass (in `__all__`): `source_binding` (dict — the normalized
  binding, e.g. `{"kind": "onboarding-stream", "root", "source", "stream"}`), `entity_key_fields`
  (tuple[str], non-empty), `event_time_field` (str, the epoch-ms field rows carry — required,
  since D6 watermarks need it), `digest_recipe` (dict, JSON-able, e.g.
  `{"kind": "stream-digest", "key_fields": [...], "ts_field": ..., "ts_unit": ...}`).
  `to_obj()` / `from_obj()`; construction validates types and non-emptiness (ConfigError).
- `Node.serving_effect(cls, params, verified_run_evidence) -> str` — classmethod; base returns
  `"forbidden"`. MUST be pure: no I/O (a test monkeypatches `builtins.open`, `os.listdir`,
  `os.scandir`, `socket.socket` to raise and calls every registered class).
- `Node.serving_contract(cls, params, verified_run_evidence)` — classmethod; base returns `None`
  ("cannot serve as the entry"); an entry class returns a `ServingContract`.
- `NodeContext.release_reader: object = None` (new last field; every existing construction site
  unaffected).
- `TrainableNode.serving_effect`: `"release_read"` iff
  `verified_run_evidence.get("mode") == "load" and verified_run_evidence.get("artifact_pinned") is True`
  AND `cls.serving_load_audited is True` (R16 — class attribute, `False` on `TrainableNode`; the
  evidence says the artifact was pinned, not that the code reading it stays inside the reader),
  else `"forbidden"`.
- Read-side artifact helpers beside the existing `write_artifact*`:
  `Node.read_artifact_text(ctx, filename, ref=None)` → when `ctx.release_reader` is not None,
  return `ctx.release_reader.get(filename)` (digest-checked text from the manifest; the reader is
  per node so `filename` is scoped to this node's artifact entry); otherwise resolve
  `ref or self.artifact` (a directory → join `filename`; a file → that file) and read it.
  `Node.read_artifact(ctx, filename, ref=None)` = `json.loads` of that. Audited `release_read`
  classes read ONLY through these (their `run_load` bodies contain no `open(`/`os.`/`io.`/`socket`
  names — an AST test pins it), so "no direct I/O" is checkable.

## 2. `dskit/pipeline/policy.py` (new, stdlib only)
```
class ExecutionPolicy(ABC):
    @abstractmethod
    def classify(self, key, cls, params, evidence) -> str   # a SERVING_EFFECTS member
    def defer(self, key) -> bool: return False              # concrete
    def reader(self, key): return None                      # concrete
def classify_plan(the_plan, policy, evidence_by_key) -> dict
```
`classify_plan` walks `the_plan.order`, calls `policy.classify(key, the_plan.resolved[key].cls,
the_plan.document.expanded[key].params, evidence_by_key.get(key, {}))`, refuses (ConfigError,
accumulating) any result not in `SERVING_EFFECTS`, and constructs NOTHING (a test asserts no
`__init__` of any node class runs). Entry dominance / "sole entry_read" rules are production's
(`ServingExecutionPolicy`, §5.3) and are NOT enforced here.

## 3. `dskit/pipeline/driver.py`
- Public `SubgraphRunner(the_plan, needed, node_outputs, splits_info, prev, policy=None)` in
  `__all__`. `node_outputs` is the BASE outputs dict the runner reads clean nodes from (today's
  `self._outputs`); `needed` is a set the caller computed.
- `rerun(overrides, outputs, ctx, prev_bindings, *, guard_verdicts=False) -> (outputs, reran_keys, seconds)`:
  * validates `overrides` is a dict; each target `"<node>.<param.path>"` addresses a DECLARED node
    (same ValueError text as today's `driver.py:507-511`) — nothing else (the unsearchable-role
    check moves UP into `_SearchSeam`);
  * `dirty` = override targets ∪ their descendants; `subgraph = order ∩ needed ∩ dirty`;
  * for each key in the subgraph: if `policy is not None and policy.defer(key)`: skip execution
    (the output already seeded in `outputs` stands; refuse with a clear error if `outputs` has no
    entry for a deferred key); else deep-copy params, apply overrides with the public
    `apply_param_override` (existing-path-only rule, unchanged text), `_materialize` params and
    inputs against `outputs`, construct `cls(k, params, mode=spec.mode, artifact=spec.artifact)`,
    hand it `ctx` — or `replace(ctx, release_reader=policy.reader(k))` when the policy returns a
    reader — `validate_inputs → run → validate_outputs`, the `guard_verdicts` NO-GO flip refusal
    exactly as today, write `outputs[k]`, time it;
  * returns `(outputs, tuple(subgraph_actually_run_in_order), seconds)` — the SAME `outputs`
    object it was given (mutated in place); `prev_bindings` is an OUT dict `_materialize` records
    `$prev` resolutions into.
- `run_keys(keys, outputs, ctx, prev_bindings) -> (outputs, ran_keys, seconds)`: the same
  lifecycle loop over the given keys in plan order with no overrides (declared-key check; deferred
  keys skipped as above). This is the serving BASE PASS verb (§9.1 "the immutable base pass
  constructs only pure nodes and approved release_read fingerprints"): production passes the keys
  of `needed` that are neither the entry nor its descendants. [Plan addition — bookkeeping: §9.1's
  API block gains this second method; the orchestrator records it for the skeptic review.]
- `apply_param_override(params, node_key, path, value)` becomes PUBLIC (in `__all__`; rename the
  private one and update its callers — one owner, never a copy). `serving_document` (production)
  applies search winners with it.
- `_SearchSeam` becomes the thin caller: `__init__(key, the_plan, node_outputs, splits_info, prev,
  trial_ctx)` unchanged in signature; it keeps `_key`, `_target/_obj_path`, `needed`,
  `seed_targets`, `calls`, and now holds `self._runner = SubgraphRunner(the_plan, self.needed,
  node_outputs, splits_info, prev)`. `__call__` → validate overrides itself (declared node FIRST,
  then `unsearchable_space_why(role, parts[1])` with today's exact message), `scratch =
  dict(self._outputs)`, `self._runner.rerun(overrides, scratch, trial_ctx, {})`, dig the objective.
  `apply_winner(overrides, ctx, bindings)` → same validation, then
  `_, reran, seconds = self._runner.rerun(overrides, self._outputs, ctx, bindings, guard_verdicts=True)`,
  returns `(reran, seconds)` — today's return shape and RuntimeError wrapping unchanged.

## 4. `dskit/pipeline/libs/observations.py`
- `ObservationRows.serving_effect(...)` → `"entry_read"` (always; it is the mutable read).
- `ObservationRows.serving_contract(params, verified_run_evidence)` (pure, document-blind):
  `ServingContract(source_binding={"kind": "onboarding-stream", "root": params["root"],
  "source": params["source"], "stream": params["stream"]}, entity_key_fields=tuple(f for f in
  params["key_fields"] if f != params.get("ts_field")), event_time_field=params.get("ts_out",
  DEFAULT_TS_OUT), digest_recipe={"kind": "stream-digest", "key_fields": list(params["key_fields"]),
  "ts_field": params.get("ts_field"), "ts_unit": params.get("ts_unit", "iso")})`.
  Refuses (ValueError) when `ts_field` is absent (no event time → no watermark → cannot serve) or
  when the entity projection is empty. NO universe field anywhere (D3/§5.2).
- The window param is `since_ms` (already in `_PARAMS`); a training document that will be served
  must DECLARE `"since_ms": null` on the entry so the existing-key-only override rule accepts the
  serving override (`apply_param_override` refuses to create a key). conftest authors: declare it.

## 5. Phase-1 audit (registry-enumeration test in `tests/pipeline/`, one line per class)
`pure`: kinds_flow `Concat, Derive, EventGrid, Filter, GroupBy, Join`; fitted `ApplyTransform`;
synthetic `SynthClip, SynthBank, SynthEligibility, SynthMarketSignal` (only if the e2e needs them —
otherwise leave forbidden). `release_read` (load-mode only, via `TrainableNode`): fitted
`Standardize, FeatureSelector`; synthetic `SynthTrain` (its `run_load` rewritten to
`self.read_artifact(ctx, "model.json")`; `FittedTransform._read_sidecar` rewritten to read through
`self.read_artifact(ctx, SIDECAR_NAME, ref)` — behaviour-neutral in ordinary runs).
`entry_read`: `ObservationRows`. Everything else registered (`kinds_banking`, `kinds_report`,
`kinds_search`, `kinds_stats`, `kinds_table`, every `libs/*` pack) stays `forbidden` — and for the
TRAINABLES among them that is not the base default doing the work, since `TrainableNode` widens.
R16: `TrainableNode.serving_load_audited` (class attribute, `False` on the base) gates the
widening, so `serving_effect` answers `release_read` only when the load evidence holds AND the
class carries the flag. The three audited classes above set it `True`; `SklearnSelect` and
`TorchImportance` set it back to `False`, because they subclass the audited `FeatureSelector` and
would otherwise inherit a licence for a load path nobody read. Widening in phase 2b is literally
one line per class: audit the load path, set the flag.
The test proves `serving_effect` performs no I/O for every registered class and `serving_contract`
returns `None` for every class except `ObservationRows`.
**Superseded in part (2026-09-06):** phase 2b read those two load paths and found them clean —
`SklearnSelect` and `TorchImportance` override only FIT-path members, so their restore IS the
audited `FittedTransform.run_load`, and both now carry the flag (stated per class, never
inherited). `StatTest` is `pure`. Plan §9.1's phase-2b paragraph carries the full round.

## 6. Production side (G12, `decider.py`) — for orientation only
`Decider.prepare`: `plan(serving_doc, registry)` (planner.plan already resolves classes/edges
without construction) → build `evidence_by_key` per node from the release (`mode`, `artifact`,
`artifact_pinned`, `role`) → `classify_plan` with `ServingExecutionPolicy` → exactly one
`entry_read`, it is a source root (no inputs, no `$` refs), every head descends from it, every
other needed node is `pure`/`release_read`, else refuse → base pass with `run_keys` over needed
keys that are not the entry nor its descendants (outputs kept for the process lifetime) →
per tick `read_entry` runs a `SubgraphRunner(needed={entry}, policy=None).rerun({f"{entry}.{param}":
tick_at_ms - window_ms}, ...)`, snapshot → `evaluate` seeds `outputs[entry]` and calls the
serving runner (policy defers the entry) `.rerun(same override, outputs, ctx, {})`.

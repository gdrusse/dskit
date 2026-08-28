"""The conformance suite's own tests: every invariant FIRES on a breaker.

A conformance bar that no bad node can fail is decoration. Each test here
builds a toy Node that violates exactly one rule and asserts the matching
check refuses it — and a clean toy that passes, so the suite is not
merely refusing everything it sees.

The toys stand in for real defects twice over: the F-220 adapter defects
(content-blind fingerprint, budget that did not bind, validator that eats
a generator, silently-ignored ``mode="load"``) and the F-222 defects of
the SUITE'S OWN first cut (path-echo fingerprints, the empty probe, the
special-cased unknown key, the tautological capital gate).
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import ClassVar

import pytest

from dskit.pipeline.conformance import (
    DEFAULT_BLOCKED_IMPORTS,
    NodeProbe,
    conformance_suite,
    import_with_blocked,
)
from dskit.pipeline.node import Node, NodeKindRegistry, TrainableNode

Skipped = pytest.skip.Exception


# ---------------------------------------------------------------------------
# toys
# ---------------------------------------------------------------------------


class Clean(Node):
    """Passes every structural check."""

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("scale",)

    @classmethod
    def validate_params(cls, params):
        unknown = sorted(set(params) - set(cls._PARAMS))
        return [f"unknown param(s) {unknown}"] if unknown else []

    def run(self, ctx, inputs):
        return {"records": list(inputs.get("records", ()))}


class NoOutputs(Clean):
    outputs = None


class PermissiveOutputs(Clean):
    def validate_outputs(self, outputs):
        return []


class StricterOutputs(Clean):
    """LAWFULLY stricter than the base: type-checks the payload too.
    The first cut refused this correct node (F-222 P1)."""

    def validate_outputs(self, outputs):
        problems = super().validate_outputs(outputs)
        if not problems and not isinstance(outputs.get("records"), list):
            problems.append("records must be a list")
        return problems


class ParamsAwareOutputs(Clean):
    """Reads instance state in validate_outputs — legal, and the first
    cut crashed on it via ``object.__new__`` (F-222 P2)."""

    def validate_outputs(self, outputs):
        problems = super().validate_outputs(outputs)
        if self.params.get("scale") == 0 and problems:
            problems.append("scale 0 forbids empty outputs")
        return problems


class RaisingParams(Clean):
    @classmethod
    def validate_params(cls, params):
        raise ValueError("boom")  # the I-001 shape: a guard that explodes


class FragileParams(Clean):
    """Total on the happy path, explodes on the classic JSON typo."""

    @classmethod
    def validate_params(cls, params):
        scale = params.get("scale", 1)
        if scale is not None and float(scale) <= 0:  # float("1,000") raises
            return ["scale must be > 0"]
        return []


class NonListParams(Clean):
    @classmethod
    def validate_params(cls, params):
        return "not a list"


class AcceptsUnknown(Clean):
    @classmethod
    def validate_params(cls, params):
        return []


class SpecialCasesTheProbeKey(Clean):
    """Games the first cut (F-222 FN-7): denies the suite's one literal
    probe key and accepts every OTHER unknown key."""

    @classmethod
    def validate_params(cls, params):
        return (
            ["conformance_unknown_knob is unknown"]
            if "conformance_unknown_knob" in params
            else []
        )


class NoRequiredCheck(Clean):
    _PARAMS = ("scale", "fee_table")


class SilentSource(Node):
    """A data node that never contributes to the run identity."""

    role = "data"
    outputs = ("records",)

    def run(self, ctx, inputs):
        return {"records": []}


class _Backed(SilentSource):
    """A data node over a mutable class-level list — the fixture 'dataset'.
    Probes mutate ``data`` in place (the F-222 FN-1 contract)."""

    data: ClassVar[list] = []

    def fingerprint(self):
        return {"n": len(type(self).data), "digest": sum(type(self).data)}

    def run(self, ctx, inputs):
        return {"records": list(type(self).data)}


class BlindSource(_Backed):
    def fingerprint(self):
        return {"n": len(type(self).data)}  # counts only — content-blind


class EchoSource(_Backed):
    """The F-222 FN-1 breaker: 'moves' only because its params moved.
    Under the in-place contract the params never move, so this is caught."""

    def fingerprint(self):
        return {"data_dir": self.params.get("data_dir", "")}


class HugeFingerprint(_Backed):
    def fingerprint(self):
        return {"rows": ["x" * 1000 for _ in range(200)]}


class DriftingFingerprint(_Backed):
    calls = 0

    def fingerprint(self):
        type(self).calls += 1
        return {"call": type(self).calls}


class RereadingSource(_Backed):
    """Re-reads at execute, so it consumes data its identity never saw."""


class PartialPin(_Backed):
    """Pins the count its size() is measured on, re-reads the REST — the
    F-222 FN-6 shape (records pinned, instruments re-read)."""

    outputs = ("records", "instruments")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._snap = None

    def fingerprint(self):
        if self._snap is None:
            self._snap = list(type(self).data)
        return {"n": len(self._snap), "digest": sum(self._snap)}

    def run(self, ctx, inputs):
        self.fingerprint()
        return {"records": list(self._snap), "instruments": list(type(self).data)}


class PinnedSource(_Backed):
    """Reads ONCE, at fingerprint time, and hands back that snapshot."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._snap = None

    def fingerprint(self):
        if self._snap is None:
            self._snap = list(type(self).data)
        return {"n": len(self._snap), "digest": sum(self._snap)}

    def run(self, ctx, inputs):
        self.fingerprint()
        return {"records": list(self._snap)}


class DriftingContract(PinnedSource):
    """Pins its data but returns an output it never declared."""

    def run(self, ctx, inputs):
        out = super().run(ctx, inputs)
        out["debug_dump"] = "oops"
        return out


#: A module-level knob table, the shape that made I-227 gap 1 hard: the
#: class below reads every one of these and carries not one of the
#: literals in its own bytecode.
TOY_KNOB_TABLE = ("alpha", "beta")


class OrphanKnob(Clean):
    """Allows a knob nothing reads — the I-227 gap-1 breaker. ``scale`` is
    read; ``leftover`` is the reader somebody deleted."""

    _PARAMS = ("scale", "leftover")

    def run(self, ctx, inputs):
        return {"records": [self.params.get("scale", 1)]}


class DocstringOnlyKnob(Clean):
    """Names its knob in PROSE only.

    ``leftover`` is documented here and read nowhere — the mention
    loophole, which must not count as reachability.
    """

    _PARAMS = ("scale", "leftover")

    def run(self, ctx, inputs):
        return {"records": [self.params.get("scale", 1)]}


class TableReadKnobs(Clean):
    """Reads its knobs by looping a module table — correct code that a
    bytecode-only check would falsely accuse."""

    _PARAMS = ("scale", *TOY_KNOB_TABLE)

    def run(self, ctx, inputs):
        records = [self.params.get("scale", 1)]
        records += [self.params[name] for name in TOY_KNOB_TABLE if name in self.params]
        return {"records": records}


class TupleLiteralKnobs(Clean):
    """Reads its knobs from a tuple literal INSIDE a method — the
    WalkForwardReplay shape; the literals live in a nested code const,
    not in co_names."""

    _PARAMS = ("scale", "gamma", "delta")

    def run(self, ctx, inputs):
        wanted = ("gamma", "delta")
        records = [self.params.get("scale", 1)]
        records += [self.params[n] for n in wanted if n in self.params]
        return {"records": records}


class RereadingLabels(SilentSource):
    """The I-229 / S4-F4 shape: a labels node that rescans the settled
    pool at execute, so the run is scored against outcomes its identity
    never hashed. ``settled`` is the mutable class-level 'store'."""

    role = "labels"
    outputs = ("outcomes",)
    settled: ClassVar[dict] = {}

    def fingerprint(self):
        store = type(self).settled
        return {"n": len(store), "digest": sorted(store.items())}

    def run(self, ctx, inputs):
        return {"outcomes": dict(type(self).settled)}


class PinnedLabels(RereadingLabels):
    """Reads the settled pool ONCE, at fingerprint time — one instance,
    one view — and hands that snapshot back at execute."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._snap = None

    def fingerprint(self):
        if self._snap is None:
            self._snap = dict(type(self).settled)
        return {"n": len(self._snap), "digest": sorted(self._snap.items())}

    def run(self, ctx, inputs):
        self.fingerprint()
        return {"outcomes": dict(self._snap)}


class FrozenLabels(PinnedLabels):
    """Memoises at CLASS level, so no fresh instance can ever see a newly
    settled event — the opposite failure, and just as wrong: the next run
    of the series would be stuck on this run's outcomes."""

    _shared: ClassVar[dict] = None

    def fingerprint(self):
        if type(self)._shared is None:
            type(self)._shared = dict(type(self).settled)
        self._snap = type(self)._shared
        return {"n": len(self._snap), "digest": sorted(self._snap.items())}


class IgnoresLoadMode(Clean):
    role = "train"
    outputs = ("signal",)

    def run(self, ctx, inputs):
        return {"signal": "refit"}  # mode never consulted — the silent refit


class HonoursLoadMode(TrainableNode):
    """The shape ADR-0038's structural bar accepts, REPARENTED from the
    ``if self.mode ==`` body it used to carry: two hooks, no branch, and
    both template methods still the base's. It persists nothing, so a
    load is an honest refusal."""

    role = "train"
    outputs = ("signal",)

    def run_train(self, ctx, inputs):
        return {"signal": "fresh"}

    def run_load(self, ctx, inputs):
        raise NotImplementedError("nothing to load — this family persists nothing")


class CrashingLoader(HonoursLoadMode):
    """Dispatches to the load hook, then dies with a message naming
    nothing — a crash is not a refusal."""

    def run_load(self, ctx, inputs):
        raise KeyError("weights_v2")


class RealLoader(HonoursLoadMode):
    def run_load(self, ctx, inputs):
        return {"signal": f"restored:{self.artifact}"}


class WrapsRun(RealLoader):
    """Wraps the ``run`` template method — refused: a wrapper is where the
    dispatch quietly grows a second opinion."""

    def run(self, ctx, inputs):
        return super().run(ctx, inputs)


class WrapsValidateInputs(RealLoader):
    """Overrides the ``validate_inputs`` template method — the likelier
    breach, so the refusal names the hook to override instead."""

    def validate_inputs(self, inputs):
        return []


class UnreadArtifactKnob(TrainableNode):
    """Declares ``artifact`` as a knob and never reads it.
    :class:`TrainableNode` DOES read ``self.artifact``, and toolkit code is
    never evidence about a child — so the leftover must still be caught."""

    role = "train"
    outputs = ("signal",)
    _PARAMS = ("scale", "artifact")

    def run_train(self, ctx, inputs):
        return {"signal": self.params.get("scale", 1)}

    def run_load(self, ctx, inputs):
        raise NotImplementedError("nothing to load")


class ConsumingValidator(Clean):
    """Walks the stream in validation; run() then gets an empty one."""

    def validate_inputs(self, inputs):
        list(inputs.get("records", ()))
        return []


class RefusingValidator(Clean):
    def validate_inputs(self, inputs):
        records = inputs.get("records")
        if not isinstance(records, (list, tuple)):
            return ["records must be a list or tuple (one-shot iterable)"]
        return []


class UntouchingValidator(Clean):
    def validate_inputs(self, inputs):
        return []  # never iterates — a generator survives to run()


class LenValidator(Clean):
    """``len()`` raises on a generator — LOUD, not silent, so lawful
    (F-222 P4: the first cut reported the TypeError as a suite error)."""

    def validate_inputs(self, inputs):
        return [] if len(inputs.get("records", ())) >= 0 else ["impossible"]


class CleanCapital(Clean):
    """Respects the budget and the gate."""

    role = "capital"
    outputs = ("positions", "outlay")

    def run(self, ctx, inputs):
        survivors = list(inputs.get("survivors", ()))
        if not survivors:
            return {"positions": {}, "outlay": 0.0}
        return {"positions": {s: 1 for s in survivors}, "outlay": 250.0}


class BudgetBuster(CleanCapital):
    """F-220 #1 verbatim: declared deployable 300, deploys 5833."""

    def run(self, ctx, inputs):
        return {"positions": {"A": 99}, "outlay": 5833.40}


class GateIgnorer(CleanCapital):
    """Sizes the same book whether or not anyone survived the edge test."""

    def run(self, ctx, inputs):
        return {"positions": {"A": 1}, "outlay": 250.0}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _suite(cls, *, probes=None, kind="toy", **kw):
    kw.setdefault("require_probes", False)
    return conformance_suite(registry={kind: cls}, probes=probes, **kw)()


def _stream_probe(inputs):
    return {"toy": NodeProbe(inputs=inputs, stream_ports=("records",))}


def _data_probe(cls, *, seed=(1, 2, 3), moved=(9, 9, 9)):
    """The in-place contract: make() reads cls.data; move() rewrites its
    CONTENT (same count); grow() appends."""
    cls.data = list(seed)
    return {
        "toy": NodeProbe(
            params={"data_dir": "/tmp/same-path-always"},
            make=lambda: cls("toy", {"data_dir": "/tmp/same-path-always"}),
            move=lambda: setattr(cls, "data", list(moved)),
            grow=lambda: setattr(cls, "data", [*cls.data, 4, 5]),
            size=lambda outputs: len(outputs["records"]),
        )
    }


def _labels_probe(cls, *, seed=(("A", True), ("B", False))):
    """The labels contract (I-229): make() reads cls.settled; move() flips
    an outcome IN PLACE; grow() SETTLES a new one."""
    cls.settled = dict(seed)
    if hasattr(cls, "_shared"):
        cls._shared = None
    return {
        "toy": NodeProbe(
            params={"data_dir": "/tmp/same-path-always"},
            inputs={},
            stream_ports=(),
            runnable=True,
            make=lambda: cls("toy", {"data_dir": "/tmp/same-path-always"}),
            move=lambda: cls.settled.update({"A": not cls.settled["A"]}),
            grow=lambda: cls.settled.update({f"NEW{len(cls.settled)}": True}),
        )
    }


CAPITAL_PROBE_KW = {
    "params": {"scale": 1},
    "inputs": {"records": [], "survivors": ["A", "B"]},
    "stream_ports": (),
    "runnable": True,
    "budget": 300.0,
    "outlay": lambda outputs: float(outputs["outlay"]),
    "gate_port": "survivors",
}


# ---------------------------------------------------------------------------
# the clean baseline
# ---------------------------------------------------------------------------


def test_a_clean_node_passes_every_structural_check(tmp_path):
    suite = _suite(Clean)
    suite.test_declares_an_output_contract("toy")
    suite.test_output_validation_refuses_undeclared_names("toy", tmp_path)
    suite.test_param_validation_is_total("toy", tmp_path)
    suite.test_params_default_deny_unknown_keys("toy", tmp_path)


# ---------------------------------------------------------------------------
# the class contract
# ---------------------------------------------------------------------------


def test_an_undeclared_output_contract_is_refused():
    with pytest.raises(AssertionError, match="declares no outputs contract"):
        _suite(NoOutputs).test_declares_an_output_contract("toy")


def test_a_permissive_validate_outputs_override_is_refused(tmp_path):
    """Drift is caught only because validate_outputs refuses a mismatched
    return; an override that accepts anything is the hole."""
    with pytest.raises(AssertionError, match="validate_outputs accepted"):
        _suite(PermissiveOutputs).test_output_validation_refuses_undeclared_names(
            "toy", tmp_path
        )


def test_a_lawfully_stricter_validate_outputs_passes(tmp_path):
    """F-222 P1: only REFUSAL is asserted, so a subclass that also
    type-checks its payload is conformant."""
    _suite(StricterOutputs).test_output_validation_refuses_undeclared_names(
        "toy", tmp_path
    )


def test_a_params_reading_validate_outputs_passes_with_a_probe(tmp_path):
    """F-222 P2: built from the probe's params, not object.__new__."""
    probes = {"toy": NodeProbe(params={"scale": 1})}
    _suite(
        ParamsAwareOutputs, probes=probes
    ).test_output_validation_refuses_undeclared_names("toy", tmp_path)


def test_a_validator_that_raises_is_reported_as_a_totality_failure(tmp_path):
    with pytest.raises(AssertionError, match="validate_params raised"):
        _suite(RaisingParams).test_param_validation_is_total("toy", tmp_path)


def test_the_fuzz_catches_a_validator_that_chokes_on_a_json_typo(tmp_path):
    """F-222 FN-13: total on the four fixed shapes, dead on '1,000'."""
    probes = {"toy": NodeProbe(params={"scale": 1})}
    with pytest.raises(AssertionError, match="validate_params raised"):
        _suite(FragileParams, probes=probes).test_param_validation_is_total(
            "toy", tmp_path
        )


def test_a_validator_that_returns_a_non_list_is_refused(tmp_path):
    with pytest.raises(AssertionError, match="expected a list"):
        _suite(NonListParams).test_param_validation_is_total("toy", tmp_path)


def test_params_that_accept_unknown_keys_are_refused(tmp_path):
    with pytest.raises(AssertionError, match="accepted the unknown param"):
        _suite(AcceptsUnknown).test_params_default_deny_unknown_keys("toy", tmp_path)


def test_special_casing_the_probe_key_no_longer_passes(tmp_path):
    """F-222 FN-7: the check probes SEVERAL unknown keys."""
    with pytest.raises(AssertionError, match="accepted the unknown param"):
        _suite(SpecialCasesTheProbeKey).test_params_default_deny_unknown_keys(
            "toy", tmp_path
        )


# ---------------------------------------------------------------------------
# probe-guarding
# ---------------------------------------------------------------------------


def test_the_probes_params_must_validate_clean(tmp_path):
    suite = _suite(Clean, probes={"toy": NodeProbe(params={"bogus": 1})})
    with pytest.raises(AssertionError):
        suite.test_the_probes_params_validate_clean("toy", tmp_path)


def test_a_required_param_that_does_not_refuse_at_plan_is_caught(tmp_path):
    probes = {
        "toy": NodeProbe(params={"scale": 1, "fee_table": {}}, required=("fee_table",))
    }
    suite = _suite(NoRequiredCheck, probes=probes)
    with pytest.raises(AssertionError, match="validated clean at plan time"):
        suite.test_required_params_refuse_at_plan_time("toy", tmp_path)


def test_required_params_pass_when_the_node_refuses_by_name(tmp_path):
    class Requires(Clean):
        _PARAMS = ("scale", "fee_table")

        @classmethod
        def validate_params(cls, params):
            problems = super().validate_params(params)
            if "fee_table" not in params:
                problems.append("params.fee_table is required")
            return problems

    probes = {
        "toy": NodeProbe(params={"scale": 1, "fee_table": {}}, required=("fee_table",))
    }
    _suite(Requires, probes=probes).test_required_params_refuse_at_plan_time(
        "toy", tmp_path
    )


def test_a_kind_with_no_probe_skips_the_behavioural_checks(tmp_path):
    with pytest.raises(Skipped, match="no probe"):
        _suite(Clean).test_the_probes_params_validate_clean("toy", tmp_path)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_a_data_node_without_a_fingerprint_is_refused():
    suite = _suite(SilentSource)
    with pytest.raises(AssertionError, match="does not override fingerprint"):
        suite.test_fingerprint_is_implemented("toy")


def test_a_content_blind_fingerprint_is_refused(tmp_path):
    """F-220 #3: counts do not move when a price is rewritten in place."""
    suite = _suite(BlindSource, probes=_data_probe(BlindSource))
    with pytest.raises(AssertionError, match="content changed IN PLACE"):
        suite.test_fingerprint_moves_when_the_data_moves("toy", tmp_path)


def test_a_params_echo_fingerprint_is_refused(tmp_path):
    """F-222 FN-1, the flagship regression: under the old two-path probe
    contract this breaker PASSED by echoing its own data_dir. In-place
    move means the params never change, so the echo cannot move."""
    suite = _suite(EchoSource, probes=_data_probe(EchoSource))
    with pytest.raises(AssertionError, match="content changed IN PLACE"):
        suite.test_fingerprint_moves_when_the_data_moves("toy", tmp_path)


def test_a_content_sensitive_fingerprint_passes(tmp_path):
    suite = _suite(_Backed, probes=_data_probe(_Backed))
    suite.test_fingerprint_moves_when_the_data_moves("toy", tmp_path)


def test_fingerprints_stay_json_small_and_deterministic(tmp_path):
    suite = _suite(_Backed, probes=_data_probe(_Backed))
    suite.test_fingerprint_is_json_small_and_deterministic("toy", tmp_path)


def test_an_oversized_fingerprint_is_refused(tmp_path):
    suite = _suite(HugeFingerprint, probes=_data_probe(HugeFingerprint))
    with pytest.raises(AssertionError, match="over the .* limit"):
        suite.test_fingerprint_is_json_small_and_deterministic("toy", tmp_path)


def test_a_nondeterministic_fingerprint_is_refused(tmp_path):
    suite = _suite(DriftingFingerprint, probes=_data_probe(DriftingFingerprint))
    with pytest.raises(AssertionError, match="identity is not reproducible"):
        suite.test_fingerprint_is_json_small_and_deterministic("toy", tmp_path)


def test_a_node_that_rereads_at_execute_is_refused(tmp_path):
    """F-220 #7: identity taken at resolve, records read at execute, the
    recorder writes in between."""
    suite = _suite(RereadingSource, probes=_data_probe(RereadingSource))
    with pytest.raises(AssertionError, match="re-read the store"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_a_node_that_pins_one_output_and_rereads_another_is_refused(tmp_path):
    """F-222 FN-6: size() measured only 'records'; the comparison now
    covers every declared output by value."""
    suite = _suite(PartialPin, probes=_data_probe(PartialPin))
    with pytest.raises(AssertionError, match="re-read the store"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_a_node_pinned_to_its_fingerprint_passes(tmp_path):
    suite = _suite(PinnedSource, probes=_data_probe(PinnedSource))
    suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_contract_drift_is_caught_even_on_a_pinned_node(tmp_path):
    """F-222 FN-3: every run() return in the suite's hands is held to the
    declared outputs contract."""
    suite = _suite(DriftingContract, probes=_data_probe(DriftingContract))
    with pytest.raises(AssertionError, match="declared output contract"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


# -- I-227 gap 1: declared-but-unread knobs ------------------------------


def test_a_declared_knob_nothing_reads_is_refused():
    suite = _suite(OrphanKnob)
    with pytest.raises(AssertionError, match=r"leftover.*appear\s+nowhere"):
        suite.test_every_declared_knob_is_reachable_from_the_class("toy")


def test_naming_a_knob_in_the_DOCSTRING_does_not_make_it_read():
    """The mention loophole, closed from the other side: prose is not a
    reader, exactly as `_referenced_names` already holds for `self.mode`."""
    suite = _suite(DocstringOnlyKnob)
    with pytest.raises(AssertionError, match="leftover"):
        suite.test_every_declared_knob_is_reachable_from_the_class("toy")


def test_knobs_read_via_a_module_table_are_not_falsely_accused():
    """The false positive that deferred this check for a round: reading
    `for name in TABLE: params[name]` leaves no literal in the class."""
    suite = _suite(TableReadKnobs)
    suite.test_every_declared_knob_is_reachable_from_the_class("toy")


def test_knobs_read_via_a_tuple_literal_in_a_method_are_not_falsely_accused():
    suite = _suite(TupleLiteralKnobs)
    suite.test_every_declared_knob_is_reachable_from_the_class("toy")


def test_a_kind_with_no_knob_table_skips_the_check():
    suite = _suite(SilentSource)
    with pytest.raises(Skipped):
        suite.test_every_declared_knob_is_reachable_from_the_class("toy")


# -- I-229: the labels straddle ------------------------------------------


def test_a_labels_node_that_rebuilds_at_execute_is_refused(tmp_path):
    """S4-F4, the bug this check exists for: labels fingerprinted at
    resolve, rebuilt at execute, so the run is scored against outcomes
    its identity never hashed. Before I-229 the bar could not see it."""
    suite = _suite(RereadingLabels, probes=_labels_probe(RereadingLabels))
    with pytest.raises(AssertionError, match="do not match the resolve-time"):
        suite.test_labels_run_returns_exactly_what_was_fingerprinted("toy", tmp_path)


def test_a_labels_node_pinned_to_its_fingerprint_passes(tmp_path):
    suite = _suite(PinnedLabels, probes=_labels_probe(PinnedLabels))
    suite.test_labels_run_returns_exactly_what_was_fingerprinted("toy", tmp_path)


def test_a_labels_node_frozen_across_instances_is_refused(tmp_path):
    """The other direction: memoising at CLASS level pins this run AND
    every later one. A fresh node must see the newly settled event."""
    suite = _suite(FrozenLabels, probes=_labels_probe(FrozenLabels))
    with pytest.raises(AssertionError, match="a FRESH node's fingerprint is"):
        suite.test_labels_run_returns_exactly_what_was_fingerprinted("toy", tmp_path)


def test_a_labels_probe_without_grow_is_reported_by_the_population_check(tmp_path):
    """The cost I-229 names: the population check now demands the hook,
    so a labels probe cannot dodge the straddle by omission."""
    probes = _labels_probe(PinnedLabels)
    probes["toy"] = replace(probes["toy"], grow=None)
    suite = _suite(PinnedLabels, probes=probes, require_probes=True)
    with pytest.raises(AssertionError, match="grow"):
        suite.test_every_probe_is_populated_for_its_role(tmp_path)


def test_a_noop_grow_is_reported_without_promising_whose_fault_it_is(tmp_path):
    """F-222 P6: the old message swore 'fix the probe, not the node', but
    a class-level scan cache produces the same symptom."""
    PinnedSource.data = [1, 2, 3]
    probes = {
        "toy": NodeProbe(
            make=lambda: PinnedSource("toy"),
            grow=lambda: None,
            size=lambda outputs: len(outputs["records"]),
        )
    }
    suite = _suite(PinnedSource, probes=probes)
    with pytest.raises(AssertionError, match="Investigate both"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


def test_a_validator_that_consumes_a_one_shot_input_is_refused(tmp_path):
    """F-220 #6: validation ate the generator, run() sized nothing, exit 0."""
    suite = _suite(ConsumingValidator, probes=_stream_probe({"records": [1, 2, 3]}))
    with pytest.raises(AssertionError, match="consumed 3 item"):
        suite.test_validation_never_consumes_a_one_shot_input("toy", tmp_path)


@pytest.mark.parametrize("cls", [RefusingValidator, UntouchingValidator, LenValidator])
def test_every_lawful_answer_to_a_one_shot_input_passes(cls, tmp_path):
    """Refuse by name, leave untouched, or raise LOUDLY (F-222 P4) — only
    silent consumption fails."""
    suite = _suite(cls, probes=_stream_probe({"records": [1, 2, 3]}))
    suite.test_validation_never_consumes_a_one_shot_input("toy", tmp_path)


def test_probe_inputs_that_the_node_refuses_are_reported(tmp_path):
    suite = _suite(RefusingValidator, probes=_stream_probe({"records": iter([])}))
    with pytest.raises(AssertionError, match="the probe's inputs are refused"):
        suite.test_the_probes_inputs_validate_clean("toy", tmp_path)


def test_inputs_check_skips_when_the_probe_supplies_none(tmp_path):
    suite = _suite(Clean, probes={"toy": NodeProbe()})
    with pytest.raises(Skipped, match="no inputs"):
        suite.test_the_probes_inputs_validate_clean("toy", tmp_path)


# ---------------------------------------------------------------------------
# the run contract
# ---------------------------------------------------------------------------


def test_a_runnable_node_that_drifts_from_its_contract_is_refused(tmp_path):
    class Drifter(Clean):
        def run(self, ctx, inputs):
            return {"records": [], "extra": 1}

    probes = {
        "toy": NodeProbe(params={"scale": 1}, inputs={}, stream_ports=(), runnable=True)
    }
    with pytest.raises(AssertionError, match="breaking the declared contract"):
        _suite(Drifter, probes=probes).test_run_matches_the_declared_output_contract(
            "toy", tmp_path
        )


def test_a_runnable_node_matching_its_contract_passes(tmp_path):
    probes = {
        "toy": NodeProbe(params={"scale": 1}, inputs={}, stream_ports=(), runnable=True)
    }
    _suite(Clean, probes=probes).test_run_matches_the_declared_output_contract(
        "toy", tmp_path
    )


class WidenedAllowlist(Clean):
    """F-222 round-2 NEW-2: reimplements exact-match but quietly allows
    one plausible extra output — refuses both round-2 sentinels, ships
    drift through the driver."""

    def validate_outputs(self, outputs):
        if not isinstance(outputs, dict):
            return ["must be a dict"]
        allowed = {*type(self).outputs, "metrics"}
        if not outputs or set(outputs) - allowed:
            return [f"outputs must be within {sorted(allowed)}"]
        return []

    def run(self, ctx, inputs):
        return {"records": [], "metrics": {"n": 0}}  # the laundered drift


def test_a_widened_outputs_allowlist_is_refused_structurally(tmp_path):
    suite = _suite(WidenedAllowlist)
    with pytest.raises(AssertionError, match="validate_outputs accepted"):
        suite.test_output_validation_refuses_undeclared_names("toy", tmp_path)


def test_a_widened_outputs_allowlist_cannot_launder_a_real_run(tmp_path):
    """Even if the structural probe were dodged, the runnable check now
    compares set(out) against the CLASS contract directly."""
    probes = {
        "toy": NodeProbe(params={"scale": 1}, inputs={}, stream_ports=(), runnable=True)
    }
    suite = _suite(WidenedAllowlist, probes=probes)
    with pytest.raises(AssertionError, match="checked directly"):
        suite.test_run_matches_the_declared_output_contract("toy", tmp_path)


def test_a_partial_digest_is_refused(tmp_path):
    """F-222 round-2 NEW-2b: a records-only digest reintroduces the
    partial pin. The suite now records which declared outputs the digest
    actually reads."""
    probes = _data_probe(PartialPin)
    probes["toy"] = NodeProbe(
        **{
            **probes["toy"].__dict__,
            "digest": lambda o: tuple(o["records"]),  # ignores instruments
        }
    )
    suite = _suite(PartialPin, probes=probes)
    with pytest.raises(AssertionError, match="cannot tell a change to declared"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


class FirstNRereader(_Backed):
    """F-222 round-2 GAME-5: pins only its FINGERPRINT, re-reads the store
    at execute and returns the first n rows — indistinguishable from a
    pinned node under pure appends, caught once move() rewrites content."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._n = None

    def fingerprint(self):
        if self._n is None:
            self._n = len(type(self).data)
        return {"n": self._n}

    def run(self, ctx, inputs):
        self.fingerprint()
        return {"records": list(type(self).data)[: self._n]}


def test_a_first_n_rereader_is_caught_by_the_in_place_rewrite(tmp_path):
    suite = _suite(FirstNRereader, probes=_data_probe(FirstNRereader))
    with pytest.raises(AssertionError, match="re-read the store"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_a_digest_that_dies_on_a_real_return_is_named(tmp_path):
    probes = _data_probe(PinnedSource)
    probes["toy"] = NodeProbe(
        **{
            **probes["toy"].__dict__,
            "digest": lambda o: 1 / 0,  # dies on ANY return
        }
    )
    suite = _suite(PinnedSource, probes=probes)
    with pytest.raises(AssertionError, match="digest fails on a real run"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_the_not_runnable_skip_is_honest_outside_the_must_run_roles(tmp_path):
    """F-222 round-2 DOG-5: the skip used to claim 'population check
    reports it' for roles population never covers."""
    probes = _data_probe(PinnedSource)  # data role: not in the must-run set
    suite = _suite(PinnedSource, probes=probes)
    with pytest.raises(Skipped, match="role outside the must-run set"):
        suite.test_run_matches_the_declared_output_contract("toy", tmp_path)


def test_a_full_coverage_digest_passes(tmp_path):
    probes = _data_probe(PinnedSource)
    probes["toy"] = NodeProbe(
        **{
            **probes["toy"].__dict__,
            "digest": lambda o: tuple(o["records"]),  # covers the ONE output
        }
    )
    _suite(
        PinnedSource, probes=probes
    ).test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_an_append_shaped_move_is_refused(tmp_path):
    """F-222 round-2 GAME-5: a move() that APPENDS lets a counts-only
    fingerprint ride the count change through the content check. move()
    must change content, not shape."""
    probes = _data_probe(BlindSource)
    probes["toy"] = NodeProbe(
        **{
            **probes["toy"].__dict__,
            "move": lambda: BlindSource.data.append(99),  # append, not rewrite
        }
    )
    suite = _suite(BlindSource, probes=probes)
    with pytest.raises(AssertionError, match="appending is grow\\(\\)'s job"):
        suite.test_fingerprint_moves_when_the_data_moves("toy", tmp_path)


def test_a_constant_digest_is_refused(tmp_path):
    """F-222 round-2 GAME-1: digest=lambda o: 0 made the re-read check
    vacuous. A digest that cannot tell grown data from the baseline is
    not measuring anything."""
    probes = _data_probe(PinnedSource)
    probes["toy"] = NodeProbe(
        **{
            **probes["toy"].__dict__,
            # Reads every declared key (defeats the coverage recorder),
            # then answers a constant anyway.
            "digest": lambda o: (o["records"], 0)[1],
        }
    )
    suite = _suite(PinnedSource, probes=probes)
    with pytest.raises(
        AssertionError,
        # Two detections race for a constant digest — mutation
        # sensitivity (coverage) or grown-data indistinguishability —
        # and either refusal is the right outcome.
        match="cannot tell a change to declared|cannot distinguish the grown",
    ):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


class _NoEqRow:
    """A payload row with no value equality — dataclass(eq=False) /
    C-extension shape. Two runs build DIFFERENT instances."""

    def __init__(self, value):
        self.value = value


class OpaquePayloadSource(_Backed):
    """CORRECT (fully pinned) node whose rows compare by identity."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._snap = None

    def fingerprint(self):
        if self._snap is None:
            self._snap = [_NoEqRow(v) for v in type(self).data]
        return {"n": len(self._snap), "digest": sum(r.value for r in self._snap)}

    def run(self, ctx, inputs):
        self.fingerprint()
        return {"records": list(self._snap)}


def test_opaque_payloads_fail_with_a_message_naming_the_digest_remedy(tmp_path):
    """F-222 round-2 FP-A: without a digest, identity-equality payloads
    cannot pass — but the failure must point at probe.digest, not
    misdiagnose a re-read."""
    suite = _suite(OpaquePayloadSource, probes=_data_probe(OpaquePayloadSource))
    with pytest.raises(AssertionError, match="supply probe.digest"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_opaque_payloads_pass_with_an_honest_digest(tmp_path):
    probes = _data_probe(OpaquePayloadSource)
    probes["toy"] = NodeProbe(
        **{
            **probes["toy"].__dict__,
            "digest": lambda o: tuple(r.value for r in o["records"]),
        }
    )
    suite = _suite(OpaquePayloadSource, probes=probes)
    suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


class _RaisingEqRow:
    """Equality raises — the numpy-array truth-ambiguity shape."""

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        raise ValueError("truth value is ambiguous")

    __hash__ = None


class AmbiguousPayloadSource(OpaquePayloadSource):
    def fingerprint(self):
        if self._snap is None:
            self._snap = [_RaisingEqRow(v) for v in type(self).data]
        return {"n": len(self._snap), "digest": sum(r.value for r in self._snap)}


def test_payloads_whose_equality_raises_fail_cleanly_not_with_a_stack_trace(
    tmp_path,
):
    """The numpy shape (F-222 round-2 FP-A): dict == raising must land on
    the digest-remedy message, never error the check."""
    suite = _suite(AmbiguousPayloadSource, probes=_data_probe(AmbiguousPayloadSource))
    with pytest.raises(AssertionError, match="supply probe.digest"):
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_a_digest_reading_via_get_or_items_counts_as_coverage(tmp_path):
    """The coverage recorder must see every access style — a digest using
    .get() or .items() is covering the outputs, not dodging them."""
    for reader in (
        lambda o: tuple(o.get("records", ())),
        lambda o: tuple(sorted((k, tuple(v)) for k, v in o.items())),
        lambda o: tuple(tuple(v) for v in o.values()),
    ):
        probes = _data_probe(PinnedSource)
        probes["toy"] = NodeProbe(**{**probes["toy"].__dict__, "digest": reader})
        suite = _suite(PinnedSource, probes=probes)
        suite.test_run_consumes_exactly_what_was_fingerprinted("toy", tmp_path)


def test_role_checks_skip_when_the_registry_has_no_such_role(tmp_path):
    """A registry with none of a role's kinds reads as an explicit named
    skip, never an empty parametrization."""
    suite = _suite(Clean)
    with pytest.raises(Skipped, match="no .* kinds"):
        suite.test_fingerprint_is_implemented(None)
    with pytest.raises(Skipped, match="no .* kinds"):
        suite.test_fingerprint_is_json_small_and_deterministic(None, tmp_path)
    with pytest.raises(Skipped, match="no .* kinds"):
        suite.test_fingerprint_moves_when_the_data_moves(None, tmp_path)
    with pytest.raises(Skipped, match="no data kinds"):
        suite.test_run_consumes_exactly_what_was_fingerprinted(None, tmp_path)
    with pytest.raises(Skipped, match="no .* kinds"):
        suite.test_trainable_kinds_dispatch_through_the_base(None)
    with pytest.raises(Skipped, match="no .* kinds"):
        suite.test_load_mode_loads_or_refuses(None, tmp_path)
    with pytest.raises(Skipped, match="no capital kinds"):
        suite.test_capital_refuses_to_plan_without_a_stat_test_wire(None, tmp_path)
    with pytest.raises(Skipped, match="no capital kinds"):
        suite.test_capital_stays_inside_the_declared_budget(None, tmp_path)
    with pytest.raises(Skipped, match="no capital kinds"):
        suite.test_capital_deploys_nothing_when_the_gate_clears_no_one(None, tmp_path)


# ---------------------------------------------------------------------------
# roles: trainable
# ---------------------------------------------------------------------------


def test_a_trainable_node_that_is_not_a_TrainableNode_is_refused():
    """ADR-0038's structural floor. A plain ``Node`` in a trainable role
    carries no dispatch at all — ``mode='load'`` would be accepted,
    hashed and silently ignored (F-220 #12), and after the port a child's
    own code never names ``mode``, so only the TYPE can answer."""
    with pytest.raises(AssertionError, match="does not subclass TrainableNode"):
        _suite(IgnoresLoadMode).test_trainable_kinds_dispatch_through_the_base("toy")


def test_a_ported_trainable_passes_the_structural_floor():
    _suite(HonoursLoadMode).test_trainable_kinds_dispatch_through_the_base("toy")


def test_wrapping_the_run_template_method_is_refused():
    with pytest.raises(AssertionError, match="run_train.*run_load"):
        _suite(WrapsRun).test_trainable_kinds_dispatch_through_the_base("toy")


def test_overriding_validate_inputs_is_refused_and_names_the_hook():
    """The validation half is the likelier breach — a pack reaches for
    ``validate_inputs`` by habit — so the refusal names the hooks."""
    with pytest.raises(AssertionError, match="validate_common_inputs"):
        _suite(WrapsValidateInputs).test_trainable_kinds_dispatch_through_the_base(
            "toy"
        )


def test_the_base_never_vouches_for_a_childs_declared_artifact_knob():
    """The ``_evidence_bases`` seam: ``TrainableNode.node_level_pin`` reads
    ``self.artifact``, a DECLARED knob of three pinned-inference kinds.
    Toolkit code is never evidence about a child, so the walk skips the
    base and the unread knob is still caught."""
    suite = _suite(UnreadArtifactKnob)
    with pytest.raises(AssertionError, match=r"\['artifact'\]"):
        suite.test_every_declared_knob_is_reachable_from_the_class("toy")


def test_knob_discovery_works_where_there_is_no_source_file():
    """Classes defined by exec() have no retrievable source — a source-
    text check would silently SKIP, which is a hole. Bytecode answers."""
    namespace = {"Clean": Clean}
    exec(  # noqa: S102 - the point of the test is a sourceless class
        "class Sourceless(Clean):\n"
        "    _PARAMS = ('scale', 'leftover')\n"
        "    def run(self, ctx, inputs):\n"
        "        return {'records': [self.params.get('scale', 1)]}\n",
        namespace,
    )
    with pytest.raises((OSError, TypeError)):  # the source really is gone
        __import__("inspect").getsource(namespace["Sourceless"])
    suite = _suite(namespace["Sourceless"])
    with pytest.raises(AssertionError, match="leftover"):
        suite.test_every_declared_knob_is_reachable_from_the_class("toy")


def _trainable_probe(**kw):
    kw.setdefault("params", {"scale": 1})
    kw.setdefault("inputs", {})
    kw.setdefault("stream_ports", ())
    kw.setdefault("runnable", True)
    return {"toy": NodeProbe(**kw)}


def test_the_silent_refit_is_caught_behaviourally(tmp_path):
    """F-222 FN-2: the structural floor can be met by ANY read of mode;
    the behavioural check demands the run REFUSE or PROVE the restore.
    IgnoresLoadMode returns happily under mode='load' with no proof."""
    suite = _suite(IgnoresLoadMode, probes=_trainable_probe())
    with pytest.raises(AssertionError, match="supplies no verify_loaded"):
        suite.test_load_mode_loads_or_refuses("toy", tmp_path)


def test_an_honest_load_refusal_passes(tmp_path):
    suite = _suite(HonoursLoadMode, probes=_trainable_probe())
    suite.test_load_mode_loads_or_refuses("toy", tmp_path)


def test_the_load_check_skips_when_the_probe_is_not_runnable(tmp_path):
    suite = _suite(HonoursLoadMode, probes={"toy": NodeProbe(params={"scale": 1})})
    with pytest.raises(Skipped, match="not runnable"):
        suite.test_load_mode_loads_or_refuses("toy", tmp_path)


def test_a_crash_that_names_nothing_is_not_a_refusal(tmp_path):
    suite = _suite(CrashingLoader, probes=_trainable_probe())
    with pytest.raises(AssertionError, match="must NAME"):
        suite.test_load_mode_loads_or_refuses("toy", tmp_path)


def test_a_real_restore_passes_with_proof(tmp_path):
    probes = _trainable_probe(
        load_artifact="model-v7",
        verify_loaded=lambda out: out["signal"] == "restored:model-v7",
    )
    _suite(RealLoader, probes=probes).test_load_mode_loads_or_refuses("toy", tmp_path)


def test_a_fake_restore_fails_the_proof(tmp_path):
    probes = _trainable_probe(
        load_artifact="model-v7",
        verify_loaded=lambda out: out["signal"] == "restored:model-v7",
    )
    suite = _suite(IgnoresLoadMode, probes=probes)
    with pytest.raises(AssertionError, match="refitted instead of restoring"):
        suite.test_load_mode_loads_or_refuses("toy", tmp_path)


def test_a_loader_whose_train_mode_cannot_run_still_passes(tmp_path):
    """The discrimination probe needs a fresh-fit sample; when mode='train'
    itself refuses, that is loud and the restore proof stands alone."""

    class LoadOnly(RealLoader):
        def run_train(self, ctx, inputs):
            raise RuntimeError("this family is inference-only; train elsewhere")

    probes = _trainable_probe(
        load_artifact="model-v7",
        verify_loaded=lambda out: out["signal"] == "restored:model-v7",
    )
    _suite(LoadOnly, probes=probes).test_load_mode_loads_or_refuses("toy", tmp_path)


def test_knob_discovery_survives_a_broken_allowed_hook(tmp_path):
    """_allowed() is a private convention; a hook that raises must not
    take the totality fuzz down with it."""

    class BrokenHook(Clean):
        @classmethod
        def _allowed(cls):
            raise RuntimeError("hook broken")

    probes = {"toy": NodeProbe(params={"scale": 1})}
    _suite(BrokenHook, probes=probes).test_param_validation_is_total("toy", tmp_path)


def test_a_rubber_stamp_verify_loaded_is_refused(tmp_path):
    """F-222 round-2 GAME-3: verify_loaded=lambda o: True blesses the
    silent refit with paperwork. The proof must reject a fresh fit."""
    probes = _trainable_probe(load_artifact="model-v7", verify_loaded=lambda out: True)
    suite = _suite(IgnoresLoadMode, probes=probes)
    with pytest.raises(AssertionError, match="proves nothing"):
        suite.test_load_mode_loads_or_refuses("toy", tmp_path)


class TeachesToTheTest(Clean):
    """F-222 round-2: denies exactly the suite's predictable probe keys
    (both literals and anything *_typoed), accepts every other unknown."""

    @classmethod
    def validate_params(cls, params):
        return [
            f"unknown param(s) ['{k}']"
            for k in params
            if k in ("conformance_unknown_knob", "another_knob_no_node_declares")
            or k.endswith("_typoed")
        ]


def test_teaching_to_the_probe_keys_no_longer_passes(tmp_path):
    """One probe key is random per run, so a validator cannot have
    allowlisted the suite's known literals."""
    with pytest.raises(AssertionError, match="accepted the unknown param 'unk_"):
        _suite(TeachesToTheTest).test_params_default_deny_unknown_keys("toy", tmp_path)


class OptionalKnobExplodes(Clean):
    """F-222 round-2 fuzz blind-spot: an OPTIONAL declared knob the probe
    never sets, whose validator explodes the first time a document sets
    it wrong."""

    _PARAMS = ("scale", "timeout")

    @classmethod
    def validate_params(cls, params):
        if "timeout" in params and float(params["timeout"]) <= 0:
            return ["timeout must be > 0"]
        return []


def test_the_fuzz_reaches_declared_knobs_the_probe_never_set(tmp_path):
    probes = {"toy": NodeProbe(params={"scale": 1})}
    suite = _suite(OptionalKnobExplodes, probes=probes)
    with pytest.raises(AssertionError, match="validate_params raised"):
        suite.test_param_validation_is_total("toy", tmp_path)


# ---------------------------------------------------------------------------
# roles: capital
# ---------------------------------------------------------------------------


def test_capital_without_a_stat_test_wire_refuses_to_plan(tmp_path):
    _suite(CleanCapital).test_capital_refuses_to_plan_without_a_stat_test_wire(
        "toy", tmp_path
    )


def test_a_budget_that_does_not_bind_is_caught(tmp_path):
    """F-220 #1 / F-222 FN-5: 19x the declared deployable, now measured
    on a REAL run instead of trusted to the planner."""
    suite = _suite(BudgetBuster, probes={"toy": NodeProbe(**CAPITAL_PROBE_KW)})
    with pytest.raises(AssertionError, match="the budget did not bind"):
        suite.test_capital_stays_inside_the_declared_budget("toy", tmp_path)


def test_a_binding_budget_passes(tmp_path):
    suite = _suite(CleanCapital, probes={"toy": NodeProbe(**CAPITAL_PROBE_KW)})
    suite.test_capital_stays_inside_the_declared_budget("toy", tmp_path)


def test_a_capital_probe_with_no_budget_and_no_reason_fails(tmp_path):
    kw = dict(CAPITAL_PROBE_KW)
    kw.pop("budget")
    suite = _suite(CleanCapital, probes={"toy": NodeProbe(**kw)})
    with pytest.raises(pytest.fail.Exception, match="must be written down"):
        suite.test_capital_stays_inside_the_declared_budget("toy", tmp_path)


def test_a_written_no_budget_reason_is_an_honest_skip(tmp_path):
    kw = dict(CAPITAL_PROBE_KW)
    kw.pop("budget")
    kw["no_budget_reason"] = "replay spends through its own accounting"
    suite = _suite(CleanCapital, probes={"toy": NodeProbe(**kw)})
    with pytest.raises(Skipped, match="replay spends"):
        suite.test_capital_stays_inside_the_declared_budget("toy", tmp_path)


def test_a_gate_nobody_reads_is_caught(tmp_path):
    """F-222 FN-5's second half: the survivors wire must be READ."""
    suite = _suite(GateIgnorer, probes={"toy": NodeProbe(**CAPITAL_PROBE_KW)})
    with pytest.raises(AssertionError, match="decoration, not a gate"):
        suite.test_capital_deploys_nothing_when_the_gate_clears_no_one("toy", tmp_path)


def test_a_read_gate_passes(tmp_path):
    suite = _suite(CleanCapital, probes={"toy": NodeProbe(**CAPITAL_PROBE_KW)})
    suite.test_capital_deploys_nothing_when_the_gate_clears_no_one("toy", tmp_path)


def test_refusing_an_empty_gate_loudly_is_lawful(tmp_path):
    class RefusesEmptyGate(CleanCapital):
        def run(self, ctx, inputs):
            if not inputs.get("survivors"):
                raise ValueError("survivors is empty — nothing cleared the gate")
            return {"positions": {}, "outlay": 0.0}

    suite = _suite(RefusesEmptyGate, probes={"toy": NodeProbe(**CAPITAL_PROBE_KW)})
    suite.test_capital_deploys_nothing_when_the_gate_clears_no_one("toy", tmp_path)


def test_capital_checks_skip_when_the_probe_is_not_runnable(tmp_path):
    kw = dict(CAPITAL_PROBE_KW)
    kw["runnable"] = False
    suite = _suite(CleanCapital, probes={"toy": NodeProbe(**kw)})
    with pytest.raises(Skipped, match="not runnable"):
        suite.test_capital_stays_inside_the_declared_budget("toy", tmp_path)
    with pytest.raises(Skipped, match="not runnable"):
        suite.test_capital_deploys_nothing_when_the_gate_clears_no_one("toy", tmp_path)


def test_a_probe_missing_a_needed_field_skips_by_name(tmp_path):
    suite = _suite(_Backed, probes={"toy": NodeProbe(make=lambda: _Backed("toy"))})
    with pytest.raises(Skipped, match="probe supplies no move"):
        suite.test_fingerprint_moves_when_the_data_moves("toy", tmp_path)


def test_params_reading_validate_outputs_without_a_probe_skips(tmp_path):
    """The F-222 P2 fallback: no probe means object.__new__, and a
    validator that reads instance state then skips WITH A REASON instead
    of crashing the suite."""
    with pytest.raises(Skipped, match="supply a probe"):
        _suite(ParamsAwareOutputs).test_output_validation_refuses_undeclared_names(
            "toy", tmp_path
        )


# ---------------------------------------------------------------------------
# the census
# ---------------------------------------------------------------------------


def test_a_role_census_mismatch_is_caught():
    """F-222 FN-12: a source mislabelled 'transform' exits the identity
    checks; the census makes the mislabel itself fail."""
    suite = _suite(Clean, expected_roles={"toy": "data"})
    with pytest.raises(AssertionError, match="contradict the package's declaration"):
        suite.test_roles_match_the_packages_declaration()


def test_an_incomplete_census_is_caught():
    suite = conformance_suite(
        registry={"toy": Clean, "cap": CleanCapital},
        expected_roles={"toy": "transform"},
        require_probes=False,
    )()
    with pytest.raises(AssertionError, match="cover exactly the registry's kinds"):
        suite.test_roles_match_the_packages_declaration()


def test_a_matching_census_passes():
    _suite(
        Clean, expected_roles={"toy": "transform"}
    ).test_roles_match_the_packages_declaration()


def test_no_census_is_a_named_skip():
    with pytest.raises(Skipped, match="no expected_roles"):
        _suite(Clean).test_roles_match_the_packages_declaration()


# ---------------------------------------------------------------------------
# the bar cannot be dodged by omission
# ---------------------------------------------------------------------------


def test_an_empty_probe_no_longer_satisfies_the_bar(tmp_path):
    """F-222 FN-4: an empty NodeProbe() cleared the old existence check
    while every behavioural invariant skipped. Population is per-role."""
    suite = conformance_suite(registry={"toy": _Backed}, probes={"toy": NodeProbe()})()
    with pytest.raises(AssertionError, match="under-populated") as excinfo:
        suite.test_every_probe_is_populated_for_its_role(tmp_path)
    for missing in ("make", "move", "grow", "size"):
        assert missing in str(excinfo.value)


def test_a_capital_probe_must_carry_its_teeth(tmp_path):
    suite = conformance_suite(
        registry={"cap": CleanCapital}, probes={"cap": NodeProbe(params={"scale": 1})}
    )()
    with pytest.raises(AssertionError) as excinfo:
        suite.test_every_probe_is_populated_for_its_role(tmp_path)
    message = str(excinfo.value)
    for missing in ("inputs", "gate_port", "outlay", "budget"):
        assert missing in message


def test_inputs_without_declared_stream_ports_are_flagged(tmp_path):
    suite = conformance_suite(
        registry={"toy": Clean}, probes={"toy": NodeProbe(inputs={"records": []})}
    )()
    with pytest.raises(AssertionError, match="stream_ports"):
        suite.test_every_probe_is_populated_for_its_role(tmp_path)


def test_a_missing_probe_fails_the_suite_by_default(tmp_path):
    suite = conformance_suite(registry={"toy": Clean})()
    with pytest.raises(AssertionError, match="no probe at all"):
        suite.test_every_probe_is_populated_for_its_role(tmp_path)


def test_a_trainable_probe_left_unrunnable_fails_population(tmp_path):
    """F-222 round-2 NEW-1: train/signal had NO population rules, so the
    silent-refit node shipped again behind a bare probe whose load check
    skipped. Now runnable (or a written reason) is required."""
    suite = conformance_suite(
        registry={"toy": IgnoresLoadMode},
        probes={"toy": NodeProbe(params={"scale": 1})},
    )()
    with pytest.raises(AssertionError, match="runnable=True"):
        suite.test_every_probe_is_populated_for_its_role(tmp_path)


def test_a_capital_probe_left_unrunnable_fails_population(tmp_path):
    """Same hole, capital shape: every role field populated but runnable
    defaulted False — budget and gate checks skipped, BudgetBuster shipped."""
    kw = dict(CAPITAL_PROBE_KW)
    kw["runnable"] = False
    suite = conformance_suite(
        registry={"toy": BudgetBuster}, probes={"toy": NodeProbe(**kw)}
    )()
    with pytest.raises(AssertionError, match="runnable=True"):
        suite.test_every_probe_is_populated_for_its_role(tmp_path)


def test_a_written_not_runnable_reason_satisfies_population_and_names_the_skip(
    tmp_path,
):
    kw = dict(CAPITAL_PROBE_KW)
    kw["runnable"] = False
    kw["not_runnable_reason"] = "needs the driver's ctx.rerun seam"
    probes = {"toy": NodeProbe(**kw)}
    suite = conformance_suite(registry={"toy": CleanCapital}, probes=probes)()
    suite.test_every_probe_is_populated_for_its_role(tmp_path)
    with pytest.raises(Skipped, match="ctx.rerun seam"):
        suite.test_capital_stays_inside_the_declared_budget("toy", tmp_path)


def test_opting_out_of_probes_is_explicit_and_names_the_gaps(tmp_path):
    with pytest.raises(Skipped, match="require_probes=False; probe gaps"):
        _suite(Clean).test_every_probe_is_populated_for_its_role(tmp_path)


def test_a_fully_populated_probe_set_satisfies_the_gate(tmp_path):
    suite = conformance_suite(
        registry={"toy": Clean},
        probes={"toy": NodeProbe(params={"scale": 1})},
    )()
    suite.test_every_probe_is_populated_for_its_role(tmp_path)


# ---------------------------------------------------------------------------
# blocked imports
# ---------------------------------------------------------------------------


def test_import_with_blocked_reports_success_for_a_pure_module():
    ok, detail = import_with_blocked("dskit.pipeline.node", DEFAULT_BLOCKED_IMPORTS)
    assert ok and detail == ""


def test_import_with_blocked_reports_the_failure():
    ok, detail = import_with_blocked("pandas", ("pandas",))
    assert not ok
    assert "pandas" in detail


def test_a_bare_string_blocklist_is_refused():
    """F-222 FN-10: tuple('numpy') blocks 'n','u','m','p','y' — nothing."""
    with pytest.raises(TypeError, match="sequence of module names"):
        import_with_blocked("dskit.pipeline.node", "numpy")


def test_an_unpinnable_held_module_is_refused_not_skipped():
    """F-222 round-2: when the parent HOLDS a module whose file cannot be
    resolved (a sys.modules stub), the old code silently dropped the
    copy-pin and validated whatever the child found. Now it refuses."""
    import sys as _sys
    import types

    ghost = types.ModuleType("conformance_ghost_mod")  # no __file__, no __spec__
    _sys.modules["conformance_ghost_mod"] = ghost
    try:
        ok, detail = import_with_blocked(
            "conformance_ghost_mod", DEFAULT_BLOCKED_IMPORTS
        )
    finally:
        del _sys.modules["conformance_ghost_mod"]
    assert not ok
    assert "cannot prove the subprocess would validate the same copy" in detail


def test_the_subprocess_resolves_the_same_copy_the_parent_does(tmp_path):
    """F-222 FN-11: a parent-side sys.path insert must not let the child
    validate a DIFFERENT copy of the module. The parent's path is passed
    through and the resolved __file__ is asserted, so the dirty copy the
    parent sees is the copy the child checks."""
    import sys as _sys

    pkg = tmp_path / "twocopy_conf_test"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "nodes.py").write_text("import pandas\n")
    _sys.path.insert(0, str(tmp_path))
    try:
        ok, detail = import_with_blocked(
            "twocopy_conf_test.nodes", DEFAULT_BLOCKED_IMPORTS
        )
    finally:
        _sys.path.remove(str(tmp_path))
        for mod in list(_sys.modules):
            if mod.startswith("twocopy_conf_test"):
                del _sys.modules[mod]
    assert not ok
    assert "pandas" in detail


# ---------------------------------------------------------------------------
# registry normalization
# ---------------------------------------------------------------------------


def test_the_registry_argument_accepts_a_node_kind_registry():
    registry = NodeKindRegistry()
    registry.register("toy", Clean, owned=True)
    suite = conformance_suite(registry=registry, require_probes=False)
    assert suite.conformance_kinds == ("toy",)
    suite().test_declares_an_output_contract("toy")


def test_the_registry_argument_accepts_a_pairs_table():
    """The shape adapters actually export (``(name, cls)`` tuples)."""
    suite = conformance_suite(
        registry=(("toy", Clean), ("cap", CleanCapital)), require_probes=False
    )
    assert suite.conformance_kinds == ("cap", "toy")


def test_the_registry_argument_accepts_the_get_shaped_mapping():
    """F-222 P3: ``{name: (cls, owned)}`` — what NodeKindRegistry.get
    returns — used to crash at suite-build time."""
    suite = conformance_suite(registry={"toy": (Clean, True)}, require_probes=False)
    assert suite.conformance_kinds == ("toy",)
    suite().test_declares_an_output_contract("toy")


def test_a_registry_of_non_classes_is_refused():
    with pytest.raises(TypeError, match="not a class"):
        conformance_suite(registry={"toy": "not-a-class"})


def test_an_empty_registry_is_refused():
    with pytest.raises(ValueError, match="declares no kinds"):
        conformance_suite(registry={})


def test_the_suite_class_is_named_for_collection():
    suite = conformance_suite(
        registry={"toy": Clean}, name="TestMine", require_probes=False
    )
    assert suite.__name__ == "TestMine" and suite.__qualname__ == "TestMine"


def test_the_suite_reports_what_it_was_pointed_at():
    suite = conformance_suite(
        registry={"toy": Clean}, module="dskit.pipeline.node", require_probes=False
    )
    assert suite.conformance_module == "dskit.pipeline.node"
    assert json.dumps(list(suite.conformance_kinds)) == '["toy"]'


# ---------------------------------------------------------------------------
# module import check plumbing
# ---------------------------------------------------------------------------


def test_the_module_import_check_fires_on_a_heavy_module():
    suite = _suite(Clean, module="pandas", blocked_imports=("pandas",))
    with pytest.raises(AssertionError, match="does not import with"):
        suite.test_module_imports_with_heavy_libraries_blocked()


def test_the_module_import_check_passes_for_the_toolkit():
    _suite(
        Clean, module="dskit.pipeline.planner"
    ).test_module_imports_with_heavy_libraries_blocked()


def test_the_module_import_check_skips_without_a_module():
    with pytest.raises(Skipped, match="no module="):
        _suite(Clean).test_module_imports_with_heavy_libraries_blocked()

"""The banking/admission chain as its own module (TODO 3e).

``event-bank -> eligibility -> banking-report`` is one cohesive story and
now lives in ``dskit/pipeline/kinds_banking.py``, split out of the
record-flow verbs it shared a file with. The split is MECHANICAL — kinds
register by NAME, so no document and no identity hash moves — and this
file pins the two things a mechanical move can still break:

* both modules' ``register()`` must stay reachable from
  ``dskit/pipeline/__init__.py``, or a document naming ``event-bank``
  stops resolving while every unit test still passes;
* the shared helpers (``_reject_unknown``, ``is_node_ref``) must stay
  importable THROUGH ``kinds_flow`` for one release, because that is the
  path sibling modules could be using.

The three classes' behaviour is exercised where it always was, in
``test_kinds_flow.py`` — this file pins the SPLIT, not the nodes. The one
exception is :class:`TestEventBankDefaultsHaveOneName`: ``EventBank``
restated its ``count`` and ``distinct_by`` defaults in ``validate_params``,
``validate_inputs``, ``run`` and a validator message, and the move made
those copies this module's problem to keep honest.
"""

import pytest

import dskit.pipeline as pipeline
from dskit.pipeline import kinds_banking, kinds_flow
from dskit.pipeline.document import is_node_ref as canonical_is_node_ref
from dskit.pipeline.kinds_banking import (
    _DEFAULT_COUNT,
    _DEFAULT_DISTINCT_BY,
    _DISTINCT_FIELDS,
    BankingReport,
    Eligibility,
    EventBank,
    register,
)
from dskit.pipeline.kinds_stats import _reject_unknown as canonical_reject_unknown
from dskit.pipeline.node import NodeContext, NodeKindRegistry
from dskit.pipeline.synthetic_nodes import SynthClip

#: The kinds each module owns after the split — the pin that catches a
#: class drifting back across the boundary.
BANKING_KINDS = ("event-bank", "eligibility", "banking-report")
FLOW_KINDS = ("filter", "event-grid", "concat", "join", "derive", "groupby")


def _rec(instrument, contract, asof_ms, **extra):
    """A plain-dict event record."""
    return {"instrument": instrument, "contract": contract, "asof_ms": asof_ms, **extra}


@pytest.fixture
def ctx(tmp_path):
    """A minimal :class:`NodeContext` — the nodes here read only ``run_dir``."""
    return NodeContext(name="banking", asof="2026-01-01", run_dir=str(tmp_path))


class TestModuleHome:
    def test_the_three_banking_classes_live_in_kinds_banking(self):
        for cls in (EventBank, Eligibility, BankingReport):
            assert cls.__module__ == "dskit.pipeline.kinds_banking"

    def test_kinds_banking_exports_exactly_its_own_surface(self):
        assert set(kinds_banking.__all__) == {
            "BankingReport",
            "Eligibility",
            "EventBank",
            "register",
        }

    def test_kinds_flow_keeps_only_the_flow_verbs(self):
        # The flow verbs plus the clause DSL they share (public since
        # ADR-0078, so a child's own tables can speak the document's
        # ``where`` grammar without restating it).
        assert set(kinds_flow.__all__) == {
            "CLAUSE_OPS",
            "Concat",
            "Derive",
            "EventGrid",
            "Filter",
            "GroupBy",
            "Join",
            "clause_holds",
            "clause_problems",
            "register",
        }

    def test_shared_helpers_stay_importable_through_kinds_flow(self):
        assert kinds_flow.is_node_ref is canonical_is_node_ref
        assert kinds_flow._reject_unknown is canonical_reject_unknown

    def test_the_one_record_accessor_is_shared_not_copied(self):
        assert kinds_banking._field is kinds_flow._field
        assert kinds_banking._MISSING is kinds_flow._MISSING


class TestRegister:
    def test_registers_the_three_unowned(self):
        reg = register(NodeKindRegistry())
        assert set(BANKING_KINDS) <= set(reg.kinds())
        assert reg.get("event-bank") == (EventBank, False)
        assert reg.get("eligibility") == (Eligibility, False)
        assert reg.get("banking-report") == (BankingReport, False)

    def test_registers_nothing_belonging_to_kinds_flow(self):
        reg = register(NodeKindRegistry())
        assert not set(FLOW_KINDS) & set(reg.kinds())

    def test_idempotent_and_never_shadows(self):
        reg = NodeKindRegistry()
        reg.register("event-bank", SynthClip)  # someone got there first
        register(reg)
        register(reg)  # second call: no duplicate-registration raise
        assert reg.get("event-bank") == (SynthClip, False)  # skipped, not shadowed
        assert reg.get("eligibility") == (Eligibility, False)

    def test_defaults_to_the_global_registry(self, monkeypatch):
        private = NodeKindRegistry()
        monkeypatch.setattr(kinds_banking, "DEFAULT_NODE_KINDS", private)
        assert register() is private
        assert "banking-report" in private


class TestBothRegistersReachableFromThePackage:
    def test_importing_the_package_resolves_every_kind_of_both_modules(self):
        names = set(pipeline.DEFAULT_NODE_KINDS.kinds())
        assert set(BANKING_KINDS) <= names
        assert set(FLOW_KINDS) <= names

    def test_the_package_still_exports_the_banking_classes(self):
        assert pipeline.EventBank is EventBank
        assert pipeline.EventGrid is kinds_flow.EventGrid
        assert pipeline.Eligibility is Eligibility
        assert pipeline.BankingReport is BankingReport
        for name in ("EventBank", "Eligibility", "BankingReport"):
            assert name in pipeline.__all__


class TestEventBankDefaultsHaveOneName:
    """Each ``EventBank`` default is ONE name, honoured everywhere.

    ``count`` was read in three methods and ``distinct_by`` in two plus a
    validator message. Changing one copy and missing another is silent and
    incoherent: ``count`` flipped in ``run`` alone leaves ``validate_inputs``
    demanding an ``outcomes`` port ``run`` ignores — or, the other way, lets
    a wiring with no ``outcomes`` past the gate and into a ``contract not in
    None`` TypeError. These pins read the constant and assert the omitted
    param is indistinguishable from the spelled-out one, so a literal that
    drifts away from the constant fails.
    """

    def test_the_defaults_are_legal_values_of_their_own_vocabularies(self):
        assert _DEFAULT_COUNT in ("settled", "all")
        assert _DEFAULT_DISTINCT_BY in _DISTINCT_FIELDS

    def test_omitting_count_runs_as_the_default_spelled_out(self, ctx):
        events = [_rec("A", "A-0", 10), _rec("A", "A-1", 11), _rec("B", "B-0", 12)]
        inputs = {"events": events, "outcomes": {"A-0": True, "B-0": False}}
        assert EventBank("bank").run(ctx, inputs) == EventBank(
            "bank", {"count": _DEFAULT_COUNT}
        ).run(ctx, inputs)

    def test_omitting_distinct_by_runs_as_the_default_spelled_out(self, ctx):
        events = [
            _rec("A", "A-0", 10, group="G"),
            _rec("A", "A-1", 11, group="G"),
            _rec("A", "A-2", 12),
        ]
        inputs = {"events": events, "outcomes": {}}
        spelled = {"count": "all", "distinct_by": _DEFAULT_DISTINCT_BY}
        assert EventBank("bank", {"count": "all"}).run(ctx, inputs) == EventBank(
            "bank", spelled
        ).run(ctx, inputs)

    def test_validate_inputs_gates_on_the_same_default_run_obeys(self, ctx):
        """The port contract and the code reading it must agree."""
        bare = EventBank("bank")
        spelled = EventBank("bank", {"count": _DEFAULT_COUNT})
        no_outcomes = {"events": []}
        assert bare.validate_inputs(no_outcomes) == spelled.validate_inputs(no_outcomes)
        # ...and the other direction, asserted unconditionally: the count the
        # gate DOES pass without an ``outcomes`` port is one ``run`` can serve
        # without one. Guarding this behind "if the gate said []" makes it dead
        # code while the default requires outcomes, so it is spelled out.
        lax = EventBank("bank", {"count": "all"})
        events = {"events": [_rec("A", "A-0", 10)]}
        assert lax.validate_inputs(events) == []
        assert lax.run(ctx, events)["counts"] == {"A": 1}  # no ``outcomes``: no raise

    def test_the_distinct_by_message_names_the_default_it_documents(self):
        # The phrase, not the bare repr: the message also renders the whole
        # vocabulary, so "'group' appears somewhere" passes even when the
        # sentence names a different default.
        problems = EventBank.validate_params({"distinct_by": "nope"})
        phrase = f"defaulting to {_DEFAULT_DISTINCT_BY!r}"
        assert any(phrase in problem for problem in problems), problems

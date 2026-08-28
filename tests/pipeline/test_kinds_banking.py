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
``test_kinds_flow.py`` — this file pins the SPLIT, not the nodes.
"""

import dskit.pipeline as pipeline
from dskit.pipeline import kinds_banking, kinds_flow
from dskit.pipeline.document import is_node_ref as canonical_is_node_ref
from dskit.pipeline.kinds_banking import (
    BankingReport,
    Eligibility,
    EventBank,
    register,
)
from dskit.pipeline.kinds_stats import _reject_unknown as canonical_reject_unknown
from dskit.pipeline.node import NodeKindRegistry
from dskit.pipeline.synthetic_nodes import SynthClip

#: The kinds each module owns after the split — the pin that catches a
#: class drifting back across the boundary.
BANKING_KINDS = ("event-bank", "eligibility", "banking-report")
FLOW_KINDS = ("filter", "concat", "join", "derive")


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
        assert set(kinds_flow.__all__) == {
            "Concat",
            "Derive",
            "Filter",
            "Join",
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
        assert pipeline.Eligibility is Eligibility
        assert pipeline.BankingReport is BankingReport
        for name in ("EventBank", "Eligibility", "BankingReport"):
            assert name in pipeline.__all__

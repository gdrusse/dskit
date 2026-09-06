"""`policy.py` — the cross-cutting invariant matrix (D10, D11, D12, §5.8, §5.14).

Two objects own every permission answer in the package: `ActionPolicy`
over `PolicyRequest` (nine declared values, one argument, so the request
and the call cannot drift apart) and `TransitionPolicy` over a breaker
transition.  No caller may re-derive either by branching — which is what
makes this file the place the safety rules are actually written down.

**How the matrix is represented.** 4 rungs x 3 breaker x 5 health x 2
readiness x 4 operations x 3 risk effects x 2 origins x 3 authority
values x 2 pending-control states is 17,280 cells, so it is not
enumerated in code.  It is an ordered tuple of named rules, each of
which may veto with a reason.  The shape pinned here:

- `Rule(name, veto)` — a frozen strategy; `veto(request)` returns a
  refusal reason or `None`.  `Rule.decide(request)` turns that into a
  `PolicyDecision(False, name)` or abstains.
- `AllowRule(Rule)` — the *explicit allow* §5.14 requires.  Same hook:
  `veto` answers why the request is outside this lane, so `None` means
  "this lane claims it" and `decide` returns `PolicyDecision(True,
  name)`.  Polymorphism, not a flag: `permits` walks the tuple and takes
  the first rule that decides.
- Fall-through is **refusal**, with a reason that is deliberately NOT a
  rule name, so `test_every_cell_is_vetoed_by_a_named_rule_or_claimed_by_an_allow_rule`
  fails on a combination nothing classified rather than defaulting.

`TRANSITION_RULES` is the same machinery over a different subject: its
rules receive the transition being asked for rather than a
`PolicyRequest`, and `TransitionPolicy.permits(from_state, to_state,
cause, proof)` walks them the same way.

**The rung axis is a table lookup, never a comparison.**
`tests/production/test_purity.py` fails any module outside `compose.py`
that compares a `rung` — `in` included — so a rung-dependent rule reads
a module-level lane table keyed by `request.rung`.  D10's matrix has a
rung axis and this module owns the matrix; what D2 forbids is asking
which rung one is in and branching, and `_LANES[request.rung].simulated`
does not.

**The golden table.** `decision_table(policy)` renders the full product
to `{"rung|breaker|health|readiness|operation|risk_effect|origin|authority|pending_control":
(allowed, reason)}` and `policy_golden.json` is the checked-in copy, so
a rule change shows the owner exactly which combinations moved.
Regenerate with `DSKIT_REGEN_GOLDEN=1 python -m pytest
tests/production/test_policy.py -k golden`; commit the diff and read it.
The tests below the golden are the hand-written half — one named test
per D10 cell, because these are the rules money moves under.
"""

import dataclasses
import inspect
import itertools
import json
import os
import pathlib

import pytest

from dskit.production import policy as policy_module
from dskit.production import records, vocab
from dskit.production.base import ProductionError
from dskit.production.policy import (
    ACTION_RULES,
    TRANSITION_RULES,
    ActionPolicy,
    AllowRule,
    PolicyDecision,
    Rule,
    TransitionPolicy,
    decision_table,
)

GOLDEN_PATH = pathlib.Path(__file__).with_name("policy_golden.json")

#: The authority axis: no authority, an ordinary arm, a reduction right
#: (`vocab.AUTHORITY_ROLES` plus the absent case).
AUTHORITY_VALUES = (None,) + tuple(vocab.AUTHORITY_ROLES)

#: The nine axes, in the order the golden key joins them.
AXIS_NAMES = (
    "rung",
    "breaker",
    "health",
    "readiness",
    "operation",
    "risk_effect",
    "origin",
    "authority",
    "pending_control",
)
AXES = (
    vocab.RUNGS,
    vocab.BREAKER_STATES,
    vocab.HEALTH_STATES,
    vocab.READINESS_VERDICTS,
    vocab.OPERATIONS,
    vocab.RISK_EFFECTS,
    vocab.LEG_ORIGINS,
    AUTHORITY_VALUES,
    (False, True),
)

#: 4 x 3 x 5 x 2 x 4 x 3 x 2 x 3 x 2. Pinned so that widening a
#: vocabulary is a deliberate golden regeneration, never a silent one.
EXPECTED_CELLS = 17_280

#: How many cells the plan permits: every query/reconcile/cancel (12,960
#: — the bounded priority lane runs in every state) plus 92 submits.
EXPECTED_ALLOWED = 13_052
EXPECTED_ALLOWED_SUBMITS = 92

#: A permitted live cell — every one-axis test below varies this.
PERMITTED_LIVE = {
    "operation": "submit",
    "risk_effect": "increase",
    "rung": "live",
    "breaker": "active",
    "health": "ready",
    "readiness": "go",
    "authority": "ordinary",
    "origin": "model",
    "pending_control": False,
}

LIVE_RUNGS = ("live_limited", "live")
SIMULATED_RUNGS = ("shadow", "paper")
PRIORITY_LANE = ("cancel", "query", "reconcile")


def request(**overrides):
    """A `PolicyRequest` for the permitted live cell, with overrides."""
    fields = dict(PERMITTED_LIVE)
    fields.update(overrides)
    return records.PolicyRequest(**fields)


def decide(**overrides):
    """`ActionPolicy().permits` over that request."""
    return ActionPolicy().permits(request(**overrides))


def combinations():
    """Every cell of the nine-axis product, as kwargs dicts."""
    for combo in itertools.product(*AXES):
        yield dict(zip(AXIS_NAMES, combo))


def key(fields):
    """The golden table's key for one cell."""
    authority = fields["authority"]
    return "|".join(
        (
            fields["rung"],
            fields["breaker"],
            fields["health"],
            fields["readiness"],
            fields["operation"],
            fields["risk_effect"],
            fields["origin"],
            "none" if authority is None else authority,
            "true" if fields["pending_control"] else "false",
        )
    )


def rule_names():
    return {rule.name for rule in ACTION_RULES}


# ---------------------------------------------------------------------------
# The objects (§5.14, §5.15)
# ---------------------------------------------------------------------------


def test_the_public_surface_is_the_two_policies_their_rules_and_the_table():
    assert set(policy_module.__all__) == {
        "ActionPolicy",
        "TransitionPolicy",
        "PolicyDecision",
        "Rule",
        "AllowRule",
        "ACTION_RULES",
        "TRANSITION_RULES",
        "decision_table",
    }


def test_a_policy_decision_is_a_frozen_allowed_and_reason():
    decision = PolicyDecision(allowed=True, reason="priority_lane")
    assert tuple(field.name for field in dataclasses.fields(decision)) == (
        "allowed",
        "reason",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.allowed = False


def test_permits_takes_the_whole_request_as_one_argument():
    """§5.14: one argument, so the nine-field request and the call
    cannot drift apart."""
    assert tuple(inspect.signature(ActionPolicy.permits).parameters) == (
        "self",
        "request",
    )
    assert tuple(inspect.signature(TransitionPolicy.permits).parameters) == (
        "self",
        "from_state",
        "to_state",
        "cause",
        "proof",
    )


def test_an_allow_rule_is_a_rule():
    """The two kinds share one hook; `decide` is the polymorphism that
    keeps `permits` free of a branch on which kind it is holding."""
    assert issubclass(AllowRule, Rule)
    veto_rule = Rule("veto_probe", lambda req: "because")
    allow_rule = AllowRule("allow_probe", lambda req: None)
    assert veto_rule.decide(request()) == PolicyDecision(False, "veto_probe")
    assert allow_rule.decide(request()) == PolicyDecision(True, "allow_probe")
    assert Rule("quiet", lambda req: None).decide(request()) is None
    assert AllowRule("elsewhere", lambda req: "not my lane").decide(request()) is None


def test_a_rule_is_frozen_and_named():
    rule = Rule("probe", lambda req: None)
    assert rule.name == "probe"
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.name = "other"


def test_the_action_rules_are_an_ordered_tuple_of_uniquely_named_rules():
    assert isinstance(ACTION_RULES, tuple)
    assert all(isinstance(rule, Rule) for rule in ACTION_RULES)
    assert len(rule_names()) == len(ACTION_RULES)
    assert all(rule.name and rule.name == rule.name.lower() for rule in ACTION_RULES)
    assert any(isinstance(rule, AllowRule) for rule in ACTION_RULES)


def test_the_default_policies_carry_the_module_rule_sets():
    assert tuple(ActionPolicy().rules) == ACTION_RULES
    assert tuple(TransitionPolicy().rules) == TRANSITION_RULES


def test_a_policy_refuses_a_rule_set_that_is_not_rules():
    with pytest.raises(ProductionError):
        ActionPolicy(rules=("submit_requires_ready_health",))


def test_a_policy_refuses_two_rules_that_share_a_name():
    """The name IS the recorded reason (§5.14), so two rules answering to
    one name make a refusal ambiguous and hide the second from the
    completeness test, which walks the rules by name."""
    twice = Rule("submit_requires_ready_health", lambda req: "no")
    with pytest.raises(ProductionError) as excinfo:
        ActionPolicy(rules=(twice, AllowRule("submit_requires_ready_health",
                                             lambda req: None)))
    assert "submit_requires_ready_health" in str(excinfo.value)


def test_a_transition_policy_refuses_two_rules_that_share_a_name():
    with pytest.raises(ProductionError):
        TransitionPolicy(rules=(Rule("door", lambda t: "no"),
                                Rule("door", lambda t: None)))


def test_a_cell_no_rule_claims_is_refused_and_not_named_by_a_rule():
    """Default-deny at the bottom: an unclassified combination is a
    refusal whose reason is not a rule name, which is what makes the
    completeness test below able to fail."""
    decision = ActionPolicy(rules=()).permits(request())
    assert decision.allowed is False
    assert decision.reason
    assert decision.reason not in rule_names()


# ---------------------------------------------------------------------------
# The bounded priority lane — query / reconcile / cancel (D10, D11, §5.13)
# ---------------------------------------------------------------------------


def test_query_reconcile_and_cancel_are_allowed_in_every_combination():
    """D10: "Query/reconcile/cancel use the bounded priority policy in
    every state"; D11 adds "without arming"; §5.13 repeats it for every
    rung/breaker/health.  Halt refuses submissions and cancels working
    orders, so the cancel lane must survive the halt that needs it."""
    policy = ActionPolicy()
    refused = [
        fields
        for fields in combinations()
        if fields["operation"] != "submit"
        and not policy.permits(records.PolicyRequest(**fields)).allowed
    ]
    assert not refused, refused[:5]


def test_the_priority_lane_survives_a_pending_control_command():
    """§5.8 blocks the next PRE-SUBMIT gate, not the lane that lets an
    operator cancel and reconcile while a control command is queued."""
    for operation in PRIORITY_LANE:
        assert decide(operation=operation, pending_control=True).allowed


@pytest.mark.parametrize("operation", PRIORITY_LANE)
@pytest.mark.parametrize("health", vocab.HEALTH_STATES)
def test_the_priority_lane_is_open_at_every_health_state(operation, health):
    """D10: starting permits startup query/reconcile/cancel, stopping
    permits query/reconcile/cancel — and unhealthy is when reconciling
    matters most."""
    decision = decide(operation=operation, health=health, breaker="halted")
    assert decision.allowed
    assert decision.reason == "priority_lane"


# ---------------------------------------------------------------------------
# Submit — the state gates (D10, D12, §5.8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("health", [h for h in vocab.HEALTH_STATES if h != "ready"])
def test_a_submit_is_refused_unless_health_is_ready(health):
    """D10: starting and stopping permit only the priority lane;
    degraded and unhealthy refuse submit."""
    decision = decide(health=health)
    assert decision.allowed is False
    assert decision.reason == "submit_requires_ready_health"


def test_a_submit_is_allowed_at_ready():
    assert decide(health="ready").allowed


@pytest.mark.parametrize("rung", vocab.RUNGS)
@pytest.mark.parametrize("origin", vocab.LEG_ORIGINS)
@pytest.mark.parametrize("risk_effect", vocab.RISK_EFFECTS)
def test_a_submit_is_refused_while_the_breaker_is_halted(rung, origin, risk_effect):
    """D12: halt refuses submissions.  Only a verified flatten (a
    TRANSITION, not a submit) may leave `halted`, so no rung, origin,
    authority or risk effect reopens this."""
    for authority in AUTHORITY_VALUES:
        decision = decide(
            rung=rung,
            origin=origin,
            risk_effect=risk_effect,
            authority=authority,
            breaker="halted",
        )
        assert decision.allowed is False


def test_the_halt_refusal_names_the_breaker():
    assert decide(breaker="halted").reason == "submit_refused_while_halted"


@pytest.mark.parametrize("rung", vocab.RUNGS)
def test_a_pending_mutating_command_blocks_the_next_pre_submit_gate(rung):
    """§5.8: a queued control command blocks the next pre-submit action
    gate until it is applied or rejected — enforced here, because
    `Authority` mints against `PolicyRequest.pending_control` (§5.13.1)."""
    decision = decide(rung=rung, pending_control=True)
    assert decision.allowed is False
    assert decision.reason == "submit_blocked_by_pending_control"


# ---------------------------------------------------------------------------
# Submit — shadow and paper (D10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", ["active", "reducing"])
@pytest.mark.parametrize("risk_effect", vocab.RISK_EFFECTS)
@pytest.mark.parametrize("readiness", vocab.READINESS_VERDICTS)
def test_shadow_permits_every_risk_effect_while_active_or_reducing(
    breaker, risk_effect, readiness
):
    """D10: "shadow + ready + {active | reducing}: submits route only to
    `ShadowExecutor` and return `not_sent`; no live permit exists" — so
    no authority is required and readiness gates nothing here."""
    decision = decide(
        rung="shadow",
        breaker=breaker,
        risk_effect=risk_effect,
        readiness=readiness,
        authority=None,
    )
    assert decision.allowed
    assert decision.reason == "simulated_submit"


@pytest.mark.parametrize("risk_effect", vocab.RISK_EFFECTS)
def test_paper_permits_every_risk_effect_while_active(risk_effect):
    """D10: "paper + ready + active: every risk effect may submit to
    `PaperExecutor` without live authority"."""
    decision = decide(rung="paper", risk_effect=risk_effect, authority=None)
    assert decision.allowed
    assert decision.reason == "simulated_submit"


def test_paper_while_reducing_permits_only_proven_reductions():
    """D10: "paper + ready + reducing permits only proven reductions" —
    the proof is the accounting classification, so the constraint is on
    the risk effect, not on who proposed the leg."""
    assert decide(rung="paper", breaker="reducing", risk_effect="reduce").allowed
    for risk_effect in ("increase", "neutral"):
        decision = decide(rung="paper", breaker="reducing", risk_effect=risk_effect)
        assert decision.allowed is False
        assert decision.reason == "reducing_permits_only_proven_reductions"


@pytest.mark.parametrize("rung", SIMULATED_RUNGS)
def test_a_simulated_rung_does_not_require_a_readiness_go(rung):
    """§5.13: "Every LIVE rung requires a current GO record" — the
    checklist gates real money, and NO-GO at shadow/paper means the
    checklist is simply not yet satisfied."""
    assert decide(rung=rung, readiness="no_go", risk_effect="reduce").allowed


@pytest.mark.parametrize("rung", SIMULATED_RUNGS)
@pytest.mark.parametrize("authority", AUTHORITY_VALUES)
def test_a_simulated_rung_neither_requires_nor_refuses_an_authority(rung, authority):
    """No live permit exists to mint at shadow/paper, so the authority
    axis decides nothing there — an arm may still have been folded (D11
    grades arming by rung; it does not forbid it below live)."""
    assert decide(rung=rung, authority=authority, risk_effect="reduce").allowed


# ---------------------------------------------------------------------------
# Submit — the live rungs (D10, D11, D12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rung", LIVE_RUNGS)
@pytest.mark.parametrize("risk_effect", vocab.RISK_EFFECTS)
def test_a_live_submit_in_active_needs_an_ordinary_arm_and_a_go(rung, risk_effect):
    """D10: "{live_limited | live} + ready + active + readiness GO: each
    submit requires a fresh exact-intent permit derived from the
    ordinary arm"."""
    decision = decide(rung=rung, risk_effect=risk_effect)
    assert decision.allowed
    assert decision.reason == "live_ordinary_submit"


@pytest.mark.parametrize("rung", LIVE_RUNGS)
@pytest.mark.parametrize("authority", [None, "reduction"])
def test_a_live_submit_without_an_ordinary_arm_is_refused(rung, authority):
    """D11: absent or expired arming refuses submission, and a reduction
    right is not an ordinary arm — it authorises only its own pre-signed
    intent digests."""
    decision = decide(rung=rung, authority=authority)
    assert decision.allowed is False
    assert decision.reason == "live_submit_requires_ordinary_arm"


@pytest.mark.parametrize("rung", LIVE_RUNGS)
@pytest.mark.parametrize("breaker", ["active", "reducing"])
def test_a_live_submit_is_refused_without_a_readiness_go(rung, breaker):
    """§5.13: every live rung requires a current GO bound to the exact
    release before arming or submit; NO-GO exits 5."""
    authority = "ordinary" if breaker == "active" else "reduction"
    origin = "model" if breaker == "active" else "reduction"
    decision = decide(
        rung=rung,
        breaker=breaker,
        readiness="no_go",
        authority=authority,
        origin=origin,
        risk_effect="reduce",
    )
    assert decision.allowed is False
    assert decision.reason == "live_submit_requires_go"


@pytest.mark.parametrize("rung", LIVE_RUNGS)
def test_a_live_reduction_needs_a_reduction_right_and_a_reduction_origin(rung):
    """D10/D12: in `reducing`, only accounting-proven reductions named by
    a fresh `ReductionAuthorization` are converted into an exact-intent
    permit; the `ReductionAuthority` is selected by origin, not rung
    (§5.13.1)."""
    decision = decide(
        rung=rung,
        breaker="reducing",
        risk_effect="reduce",
        origin="reduction",
        authority="reduction",
    )
    assert decision.allowed
    assert decision.reason == "live_reduction_submit"


@pytest.mark.parametrize("rung", LIVE_RUNGS)
@pytest.mark.parametrize("authority", [None, "ordinary"])
def test_a_live_reduction_refuses_a_stale_ordinary_arm(rung, authority):
    """D10/D12: leaving `active` revokes every ordinary arm and ordinary
    arming is never issued while reducing, so an ordinary authority here
    is a stale arm — the one this rule exists to catch."""
    decision = decide(
        rung=rung,
        breaker="reducing",
        risk_effect="reduce",
        origin="reduction",
        authority=authority,
    )
    assert decision.allowed is False
    assert decision.reason == "live_reduction_requires_reduction_right"


# ---------------------------------------------------------------------------
# Submit — origin (D12, §5.13, §5.13.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rung", vocab.RUNGS)
@pytest.mark.parametrize("authority", AUTHORITY_VALUES)
def test_a_reduction_origin_is_refused_unless_the_breaker_is_reducing(rung, authority):
    """§5.13.1: the `AuthorityTable` "refuses a reduction outside
    `reducing`"; D12: reduction authority is issued and executed only
    while reducing.  A stored flatten intent replayed while active is
    exactly the case."""
    decision = decide(
        rung=rung, breaker="active", origin="reduction", authority=authority
    )
    assert decision.allowed is False
    assert decision.reason == "reduction_origin_requires_reducing"


@pytest.mark.parametrize("rung", LIVE_RUNGS)
@pytest.mark.parametrize("risk_effect", vocab.RISK_EFFECTS)
@pytest.mark.parametrize("authority", AUTHORITY_VALUES)
def test_a_model_origin_is_refused_while_reducing_at_a_live_rung(
    rung, risk_effect, authority
):
    """D12/§5.13: "A reduction cycle is its own tick: it carries only the
    plan's legs, in maker-approved index order, never interleaved with
    model legs".  At a live rung a model leg during `reducing` is a
    model claim standing in for a maker-signed intent."""
    decision = decide(
        rung=rung,
        breaker="reducing",
        origin="model",
        risk_effect=risk_effect,
        authority=authority,
    )
    assert decision.allowed is False
    assert decision.reason == "model_origin_refused_while_reducing"


def test_a_model_origin_while_reducing_is_a_risk_effect_question_below_live():
    """The plan states the shadow/paper reducing cells as constraints on
    the RISK EFFECT — "paper + ready + reducing permits only proven
    reductions", shadow "+ {active | reducing}" for every effect — and
    no live permit exists to mint at either rung, so origin does not
    gate them.  REPORTED as a plan gap: D10 is explicit about origin
    only at the live rungs."""
    for rung in SIMULATED_RUNGS:
        assert decide(
            rung=rung, breaker="reducing", origin="model", risk_effect="reduce"
        ).allowed
    assert decide(
        rung="shadow", breaker="reducing", origin="model", risk_effect="increase"
    ).allowed


@pytest.mark.parametrize("rung", LIVE_RUNGS)
@pytest.mark.parametrize("origin", vocab.LEG_ORIGINS)
@pytest.mark.parametrize("authority", AUTHORITY_VALUES)
@pytest.mark.parametrize("readiness", vocab.READINESS_VERDICTS)
def test_no_live_authority_ever_permits_an_increase_while_reducing(
    rung, origin, authority, readiness
):
    """The single most important cell in the matrix: `reducing` exists to
    take risk off, and D12's accounting strategy — not a model claim —
    must prove each proposal cannot increase absolute exposure."""
    for risk_effect in ("increase", "neutral"):
        decision = decide(
            rung=rung,
            breaker="reducing",
            risk_effect=risk_effect,
            origin=origin,
            authority=authority,
            readiness=readiness,
        )
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# Rule order — the reason names the FIRST rule that vetoes
# ---------------------------------------------------------------------------


def test_the_reason_names_the_first_vetoing_rule():
    """Order is part of the contract: the reason a refusal records is
    what an operator reads first, so health beats the breaker, which
    beats a queued command, which beats the origin and authority rules."""
    assert (
        decide(health="unhealthy", breaker="halted", pending_control=True).reason
        == "submit_requires_ready_health"
    )
    assert (
        decide(breaker="halted", pending_control=True).reason
        == "submit_refused_while_halted"
    )
    assert (
        decide(pending_control=True, origin="reduction", authority=None).reason
        == "submit_blocked_by_pending_control"
    )
    assert (
        decide(origin="reduction", authority=None, readiness="no_go").reason
        == "reduction_origin_requires_reducing"
    )
    assert (
        decide(breaker="reducing", origin="model", readiness="no_go", authority=None).reason
        == "model_origin_refused_while_reducing"
    )


# ---------------------------------------------------------------------------
# Completeness (§5.14) — nothing falls through, nothing is anonymous
# ---------------------------------------------------------------------------


def test_the_product_is_the_size_the_golden_table_was_generated_over():
    assert AUTHORITY_VALUES == (None, "ordinary", "reduction")
    assert len(list(combinations())) == EXPECTED_CELLS


def test_every_cell_is_vetoed_by_a_named_rule_or_claimed_by_an_allow_rule():
    """§5.14's completeness test.  Every one of the 17,280 combinations
    must be classified BY NAME — a cell that fell through to the
    default-deny would carry a reason no rule owns, and fails here
    rather than quietly refusing forever."""
    policy = ActionPolicy()
    names = rule_names()
    unclassified = []
    for fields in combinations():
        decision = policy.permits(records.PolicyRequest(**fields))
        assert isinstance(decision.allowed, bool)
        if not decision.reason or decision.reason not in names:
            unclassified.append((key(fields), decision.reason))
    assert not unclassified, unclassified[:5]


def test_the_matrix_permits_only_the_cells_the_plan_names():
    """A readable canary in front of the golden diff: the bounded
    priority lane is open in all 12,960 non-submit cells, and exactly 92
    submit cells are permitted."""
    policy = ActionPolicy()
    allowed = [
        fields
        for fields in combinations()
        if policy.permits(records.PolicyRequest(**fields)).allowed
    ]
    assert len(allowed) == EXPECTED_ALLOWED
    submits = [fields for fields in allowed if fields["operation"] == "submit"]
    assert len(submits) == EXPECTED_ALLOWED_SUBMITS
    assert {fields["health"] for fields in submits} == {"ready"}
    assert {fields["pending_control"] for fields in submits} == {False}
    assert "halted" not in {fields["breaker"] for fields in submits}


# ---------------------------------------------------------------------------
# The golden table (§5.14) — checked in, so a moved cell is a diff
# ---------------------------------------------------------------------------


def dump_golden(table):
    """The checked-in rendering: sorted, one cell per line, JSON."""
    items = sorted(table.items())
    lines = ["{"]
    for index, (cell, (allowed, reason)) in enumerate(items):
        tail = "," if index < len(items) - 1 else ""
        lines.append(f"{json.dumps(cell)}: {json.dumps([allowed, reason])}{tail}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def load_golden():
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return {cell: tuple(value) for cell, value in golden.items()}


def test_the_decision_table_is_keyed_by_the_nine_axes_in_order():
    table = decision_table(ActionPolicy())
    assert table["live|active|ready|go|submit|increase|model|ordinary|false"] == (
        True,
        "live_ordinary_submit",
    )
    assert table["shadow|halted|ready|go|query|neutral|model|none|true"] == (
        True,
        "priority_lane",
    )
    assert "live|ready|active|go|submit|increase|model|ordinary|false" not in table


def test_the_decision_table_covers_the_whole_product():
    table = decision_table(ActionPolicy())
    assert len(table) == EXPECTED_CELLS
    assert set(table) == {key(fields) for fields in combinations()}


def test_the_decision_table_is_generated_from_the_policy_it_is_given():
    """Not a hard-coded copy: a policy with no rules tables as a refusal
    everywhere, and one holding only the priority lane allows exactly the
    non-submit cells."""
    empty = decision_table(ActionPolicy(rules=()))
    assert {allowed for allowed, _ in empty.values()} == {False}

    lane_only = decision_table(ActionPolicy(rules=ACTION_RULES[:1]))
    allowed_ops = {
        cell.split("|")[4] for cell, (allowed, _) in lane_only.items() if allowed
    }
    assert allowed_ops == set(PRIORITY_LANE)


def test_the_generated_decision_table_matches_the_checked_in_golden():
    """The audit artefact: regenerate with `DSKIT_REGEN_GOLDEN=1` and the
    diff is exactly which combinations moved."""
    table = decision_table(ActionPolicy())
    if os.environ.get("DSKIT_REGEN_GOLDEN") == "1":
        GOLDEN_PATH.write_text(dump_golden(table), encoding="utf-8")
    assert table == load_golden()


def test_the_golden_file_is_sorted_complete_and_named_by_rules():
    golden = load_golden()
    assert len(golden) == EXPECTED_CELLS
    assert list(golden) == sorted(golden)
    assert {reason for _, reason in golden.values()} <= rule_names()


# ---------------------------------------------------------------------------
# TransitionPolicy (D10, D12) — the breaker's only door
# ---------------------------------------------------------------------------

PROOF = "proof-digest"

#: Every transition D10 permits, and the rule that names it. Restated
#: independently of the implementation, which is the point.
PERMITTED_TRANSITIONS = {
    ("active", "reducing", "reduce"): "verified_reduce_enters_reducing",
    ("active", "reducing", "flatten_request"): "verified_reduce_enters_reducing",
    ("halted", "reducing", "flatten_request"): "verified_flatten_enters_reducing_from_halted",
    ("active", "halted", "trip"): "trip_enters_halted",
    ("active", "halted", "halt"): "trip_enters_halted",
    ("reducing", "halted", "trip"): "trip_enters_halted",
    ("reducing", "halted", "halt"): "trip_enters_halted",
    ("reducing", "active", "resume"): "verified_resume_enters_active",
    ("halted", "active", "resume"): "verified_resume_enters_active",
}

#: The verbs D11/D12 require a maker/checker proof for. `trip` and
#: `halt` are deliberately absent: stopping must never depend on a
#: proof, an inbox or the ledger being available (§5.8).
PROVEN_CAUSES = ("reduce", "flatten_request", "resume")

#: The causes a transition may claim, plus one that is not a cause at
#: all — D10: "No timer changes these states".
CAUSES = ("reduce", "flatten_request", "trip", "halt", "resume", "timer")


def transitions():
    for from_state in vocab.BREAKER_STATES:
        for to_state in vocab.BREAKER_STATES:
            for cause in CAUSES:
                for proof in (PROOF, None):
                    yield from_state, to_state, cause, proof


def test_a_verified_reduce_or_flatten_enters_reducing_from_active():
    policy = TransitionPolicy()
    for cause in ("reduce", "flatten_request"):
        decision = policy.permits("active", "reducing", cause, PROOF)
        assert decision.allowed
        assert decision.reason == "verified_reduce_enters_reducing"


def test_only_a_verified_flatten_enters_reducing_from_halted():
    """D12: a `reduce` grants no submit authority and cannot reopen a
    halted series; only a maker-signed flatten request may."""
    policy = TransitionPolicy()
    assert policy.permits("halted", "reducing", "flatten_request", PROOF).allowed
    refused = policy.permits("halted", "reducing", "reduce", PROOF)
    assert refused.allowed is False
    assert refused.reason == "transition_not_permitted"


@pytest.mark.parametrize("from_state", ["active", "reducing"])
@pytest.mark.parametrize("cause", ["trip", "halt"])
def test_a_trip_or_halt_enters_halted_without_a_proof(from_state, cause):
    """D12/§5.8: stopping does not depend on the decision loop, inbox
    health or ledger availability — so it cannot depend on a proof."""
    decision = TransitionPolicy().permits(from_state, "halted", cause, None)
    assert decision.allowed
    assert decision.reason == "trip_enters_halted"


@pytest.mark.parametrize("from_state", ["reducing", "halted"])
def test_only_a_verified_resume_returns_to_active(from_state):
    policy = TransitionPolicy()
    decision = policy.permits(from_state, "active", "resume", PROOF)
    assert decision.allowed
    assert decision.reason == "verified_resume_enters_active"
    for cause in ("reduce", "flatten_request", "trip", "halt", "timer"):
        assert policy.permits(from_state, "active", cause, PROOF).allowed is False


@pytest.mark.parametrize("triple", sorted(PERMITTED_TRANSITIONS))
def test_a_transition_that_needs_a_proof_refuses_without_one(triple):
    from_state, to_state, cause = triple
    decision = TransitionPolicy().permits(from_state, to_state, cause, None)
    if cause in PROVEN_CAUSES:
        assert decision.allowed is False
        assert decision.reason == "transition_requires_proof"
    else:
        assert decision.allowed


@pytest.mark.parametrize("state", vocab.BREAKER_STATES)
@pytest.mark.parametrize("cause", CAUSES)
def test_a_state_never_transitions_to_itself(state, cause):
    decision = TransitionPolicy().permits(state, state, cause, PROOF)
    assert decision.allowed is False
    assert decision.reason == "transition_not_permitted"


@pytest.mark.parametrize("from_state", vocab.BREAKER_STATES)
@pytest.mark.parametrize("to_state", vocab.BREAKER_STATES)
def test_no_timer_changes_the_breaker(from_state, to_state):
    """D10: "No timer changes these states"; D12: "State persists and
    never changes on a timer"."""
    assert TransitionPolicy().permits(from_state, to_state, "timer", PROOF).allowed is False


def test_an_unknown_state_or_cause_is_refused_not_guessed():
    policy = TransitionPolicy()
    for args in (
        ("stopping", "halted", "trip", PROOF),
        ("active", "flat", "reduce", PROOF),
        ("active", "reducing", "flatten", PROOF),
        ("active", "reducing", None, PROOF),
    ):
        decision = policy.permits(*args)
        assert decision.allowed is False
        assert decision.reason == "transition_not_permitted"


def test_every_breaker_transition_is_explicit():
    """D10: "every breaker transition is explicit".  The whole
    from x to x cause x proof product is walked; each cell is either one
    of the nine D10 permits or a named refusal."""
    policy = TransitionPolicy()
    names = {rule.name for rule in TRANSITION_RULES}
    for from_state, to_state, cause, proof in transitions():
        decision = policy.permits(from_state, to_state, cause, proof)
        triple = (from_state, to_state, cause)
        expected = triple in PERMITTED_TRANSITIONS and (
            proof is not None or cause not in PROVEN_CAUSES
        )
        assert decision.allowed is expected, (triple, proof, decision)
        assert decision.reason in names, (triple, proof, decision)
        if expected:
            assert decision.reason == PERMITTED_TRANSITIONS[triple]


def test_the_transition_rules_are_an_ordered_tuple_of_uniquely_named_rules():
    assert isinstance(TRANSITION_RULES, tuple)
    assert all(isinstance(rule, Rule) for rule in TRANSITION_RULES)
    assert len({rule.name for rule in TRANSITION_RULES}) == len(TRANSITION_RULES)
    assert any(isinstance(rule, AllowRule) for rule in TRANSITION_RULES)

"""The cross-cutting invariant matrix: who may act, and how the breaker may move (D10–D12, §5.8, §5.14).

Two objects own every permission answer in the package. :class:`ActionPolicy`
answers ``permits(request)`` over a ``PolicyRequest`` (§5.4) — nine closed
facts about the moment, passed as ONE argument so the request and the call
cannot drift apart. :class:`TransitionPolicy` answers ``permits(from_state,
to_state, cause, proof)`` for the breaker, whose only door this is: no
timer and no caller's ``if`` changes its state. No caller may re-derive
either answer by branching (§5.15), which is what makes this module the
place the safety rules are actually written down.

**The matrix is rules, not cells.** 4 rungs × 3 breaker × 5 health × 2
readiness × 4 operations × 3 risk effects × 2 origins × 3 authority values
× 2 pending-control states is 17,280 cells, so it is not enumerated. It is
an ordered tuple of named :class:`Rule` strategies. A ``Rule`` may VETO:
its ``veto(request)`` hook returns a reason or ``None``, and ``decide``
turns a reason into ``PolicyDecision(False, name)``. An :class:`AllowRule`
is the explicit allow §5.14 requires — the same hook read the other way:
``None`` means "this lane claims the request" and ``decide`` returns
``PolicyDecision(True, name)``. A policy walks its tuple and takes the
first rule that decides; a request nothing classified is REFUSED with a
reason that is deliberately not a rule name, so the completeness test
fails on it rather than the matrix quietly defaulting.

**The rung axis is a table lookup, never a comparison.** D2 forbids every
module but ``compose.py`` from asking which rung it is in, and
``tests/production/test_purity.py`` fails a ``rung ==`` anywhere else. The
matrix has a rung axis and this module owns the matrix, so what a rung
contributes lives in a module-level lane table keyed by ``request.rung``
and pinned to ``vocab.RUNGS`` at import: whether a live permit exists at
that rung, and what its ``reducing`` state admits.

**Order is part of the contract.** The reason a refusal records is what an
operator reads first, so the vetoes run health → breaker → queued control
→ origin → risk effect → readiness → authority, and the three allow lanes
(simulated, live-ordinary, live-reduction) come last. :func:`decision_table`
renders the whole product for a policy; ``tests/production/policy_golden.json``
is the checked-in rendering of the default rules, so a rule change shows
the owner exactly which combinations moved (regenerate with
``DSKIT_REGEN_GOLDEN=1``).

``SubmissionVerifier`` — the third owner §5.14 names — performs I/O and
lives in ``verifier.py`` (§10); the two policies here are pure over closed
vocabularies.
"""

import itertools
from dataclasses import dataclass

from dskit.production.base import ProductionError
from dskit.production.records import PolicyRequest
from dskit.production.vocab import (
    AUTHORITY_ROLES,
    BREAKER_STATES,
    HEALTH_STATES,
    LEG_ORIGINS,
    OPERATIONS,
    READINESS_VERDICTS,
    RISK_EFFECTS,
    RUNGS,
    TRANSITION_CAUSES,
)

__all__ = [
    "ACTION_RULES",
    "TRANSITION_RULES",
    "ActionPolicy",
    "AllowRule",
    "PolicyDecision",
    "Rule",
    "TransitionPolicy",
    "decision_table",
]

#: The fall-through refusal. Deliberately NOT a rule name: a decision
#: carrying it means no rule classified the subject, which the
#: completeness test turns into a failure rather than a silent default.
_UNCLASSIFIED = "unclassified"


# ---------------------------------------------------------------------------
# The objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDecision:
    """One permission answer: allowed or not, and the rule that said so (§5.14).

    Parameters
    ----------
    allowed : bool
        Whether the action or transition may proceed.
    reason : str
        The name of the rule that decided, or the fall-through marker when
        none did.

    Examples
    --------
    ::

        decision = PolicyDecision(allowed=False, reason="submit_refused_while_halted")
        decision.allowed  # False
    """

    allowed: bool
    reason: str

    def __post_init__(self):
        """Refuse a non-bool verdict or an empty reason."""
        problems = []
        if not isinstance(self.allowed, bool):
            problems.append(f"PolicyDecision.allowed must be a bool, got {self.allowed!r}")
        if not isinstance(self.reason, str) or not self.reason:
            problems.append(f"PolicyDecision.reason must be a non-empty str, got {self.reason!r}")
        if problems:
            raise ProductionError(problems)


@dataclass(frozen=True)
class Rule:
    """A named veto: the strategy object one row of the matrix is (§5.14, §5.15).

    Parameters
    ----------
    name : str
        The rule's name — what a refusal records as its ``reason``.
    veto : callable
        ``veto(subject) -> str | None``: a short explanation when the rule
        refuses ``subject``, ``None`` when it has nothing to say.

    Examples
    --------
    A rule that refuses every subject, and one that never speaks::

        always = Rule("always_refuses", lambda subject: "no")
        always.decide(object())  # PolicyDecision(allowed=False, reason="always_refuses")
        Rule("silent", lambda subject: None).decide(object())  # None
    """

    name: str
    veto: object

    def __post_init__(self):
        """Refuse an unnamed rule or a veto that cannot be called."""
        problems = []
        if not isinstance(self.name, str) or not self.name:
            problems.append(f"a rule needs a non-empty str name, got {self.name!r}")
        if not callable(self.veto):
            problems.append(f"rule {self.name!r}: veto must be callable, got {self.veto!r}")
        if problems:
            raise ProductionError(problems)

    def decide(self, subject):
        """Return this rule's decision over ``subject``, or abstain.

        Parameters
        ----------
        subject : object
            A ``PolicyRequest`` for the action rules; the transition being
            asked for, for the transition rules.

        Returns
        -------
        PolicyDecision or None
            ``PolicyDecision(False, name)`` when ``veto`` gave a reason;
            ``None`` when it abstained.
        """
        if self.veto(subject) is None:
            return None
        return PolicyDecision(False, self.name)


@dataclass(frozen=True)
class AllowRule(Rule):
    """The explicit allow: a lane that claims the subjects its veto does not exclude (§5.14).

    Same hook as :class:`Rule`, read the other way round — ``veto`` answers
    why ``subject`` is outside this lane, so ``None`` means the lane claims
    it. Polymorphism, not a flag: a policy calls ``decide`` and never asks
    which kind of rule it is holding.

    Parameters
    ----------
    name : str
        The lane's name — what a permission records as its ``reason``.
    veto : callable
        ``veto(subject) -> str | None``: a short explanation when
        ``subject`` is outside this lane, ``None`` when the lane claims it.

    Examples
    --------
    ::

        lane = AllowRule("everything", lambda subject: None)
        lane.decide(object())  # PolicyDecision(allowed=True, reason="everything")
        AllowRule("nothing", lambda subject: "not mine").decide(object())  # None
    """

    def decide(self, subject):
        """Return this lane's permission over ``subject``, or abstain.

        Parameters
        ----------
        subject : object
            As for :meth:`Rule.decide`.

        Returns
        -------
        PolicyDecision or None
            ``PolicyDecision(True, name)`` when ``veto`` returned ``None``;
            ``None`` when it gave a reason the subject is not this lane's.
        """
        if self.veto(subject) is None:
            return PolicyDecision(True, self.name)
        return None


def _checked_rules(rules):
    """Return ``rules`` as a tuple, refusing anything that is not a uniquely named Rule."""
    if isinstance(rules, (str, bytes)) or not isinstance(rules, (list, tuple)):
        raise ProductionError(
            [f"rules must be a sequence of Rule objects, got {type(rules).__name__}"]
        )
    problems, names = [], set()
    for position, rule in enumerate(rules):
        if not isinstance(rule, Rule):
            problems.append(f"rules[{position}]: {rule!r} is not a Rule")
            continue
        if rule.name in names:
            problems.append(f"rules[{position}]: the name {rule.name!r} is already taken")
        names.add(rule.name)
    if problems:
        raise ProductionError(problems)
    return tuple(rules)


class _Policy:
    """An ordered walk over named rules: the first that decides answers, and nothing deciding refuses."""

    def __init__(self, rules):
        self._rules = _checked_rules(rules)

    @property
    def rules(self):
        """The rules in the order they are walked, as a tuple."""
        return self._rules

    def _decide(self, subject):
        """Return the first rule's decision over ``subject``, or the fall-through refusal."""
        for rule in self._rules:
            decision = rule.decide(subject)
            if decision is not None:
                return decision
        return PolicyDecision(False, _UNCLASSIFIED)


# ---------------------------------------------------------------------------
# The action matrix (D10, D11, D12, §5.8, §5.13)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Lane:
    """What one rung contributes to a submit — read by lookup, never by comparing the rung (D2)."""

    #: A live permit is minted here, so a GO, an ordinary arm or a reduction
    #: right is required (D10, D11); below live no permit exists to mint.
    live: bool
    #: D10: shadow's ``reducing`` routes every risk effect to the
    #: ``ShadowExecutor``; every other rung's admits proven reductions only.
    reducing_any_effect: bool


#: One row per ``vocab.RUNGS`` member, keyed by ``request.rung`` and pinned
#: to the vocabulary at import.
_LANES = {
    "shadow": _Lane(live=False, reducing_any_effect=True),
    "paper": _Lane(live=False, reducing_any_effect=False),
    "live_limited": _Lane(live=True, reducing_any_effect=False),
    "live": _Lane(live=True, reducing_any_effect=False),
}
if set(_LANES) != set(RUNGS):
    raise ProductionError(
        [f"policy.py's lane table keys {sorted(_LANES)} != vocab.RUNGS {sorted(RUNGS)}"]
    )


def _outside_priority_lane(request):
    """Claim query, reconcile and cancel — open in every state (D10, D11); a submit is outside."""
    if request.operation == "submit":
        return "a submit follows the full pre-submit path, not the priority lane"
    return None


def _health_not_ready(request):
    """D10: only ``ready`` may submit; starting, degraded, unhealthy and stopping refuse."""
    if request.health != "ready":
        return f"health is {request.health}, not ready"
    return None


def _breaker_halted(request):
    """D12: halt refuses submissions; only a verified flatten TRANSITION leaves ``halted``."""
    if request.breaker == "halted":
        return "the breaker is halted"
    return None


def _control_pending(request):
    """§5.8: a queued mutating control command blocks the next pre-submit gate."""
    if request.pending_control:
        return "a mutating control command is pending"
    return None


def _reduction_leg_outside_reducing(request):
    """D12, §5.13.1: a reduction leg is legal only while the breaker is ``reducing``."""
    if request.origin == "reduction" and request.breaker != "reducing":
        return f"a reduction leg while the breaker is {request.breaker}"
    return None


def _model_leg_in_live_reduction_cycle(request):
    """§5.13: at a live rung a reduction cycle carries only the plan's legs, never a model's."""
    if _LANES[request.rung].live and request.breaker == "reducing" and request.origin == "model":
        return "a model leg during a live reduction cycle"
    return None


def _unproven_effect_while_reducing(request):
    """D10: ``reducing`` admits only accounting-proven reductions, except at shadow."""
    if (
        request.breaker == "reducing"
        and request.risk_effect != "reduce"
        and not _LANES[request.rung].reducing_any_effect
    ):
        return f"risk effect {request.risk_effect} while reducing"
    return None


def _live_without_go(request):
    """§5.13: every live rung requires a current readiness GO before a submit."""
    if _LANES[request.rung].live and request.readiness != "go":
        return f"readiness is {request.readiness}, not go"
    return None


def _live_active_without_ordinary_arm(request):
    """D10, D11: a live submit in ``active`` needs a permit derived from the ordinary arm."""
    if _LANES[request.rung].live and request.breaker == "active" and request.authority != "ordinary":
        return f"authority is {request.authority}, not an ordinary arm"
    return None


def _live_reducing_without_reduction_right(request):
    """D10, D12: a live reduction needs a fresh reduction right; an ordinary arm here is stale."""
    if _LANES[request.rung].live and request.breaker == "reducing" and request.authority != "reduction":
        return f"authority is {request.authority}, not a reduction right"
    return None


def _outside_simulated_lane(request):
    """Claim a submit at a rung where no live permit exists (D10: shadow, paper)."""
    if _LANES[request.rung].live:
        return "a live rung mints a live permit"
    return None


def _outside_live_ordinary_lane(request):
    """Claim a live submit while ``active`` — the ordinary-arm lane (D10)."""
    if _LANES[request.rung].live and request.breaker == "active":
        return None
    return "not a live submit while active"


def _outside_live_reduction_lane(request):
    """Claim a live submit while ``reducing`` — the reduction-right lane (D10, D12)."""
    if _LANES[request.rung].live and request.breaker == "reducing":
        return None
    return "not a live submit while reducing"


#: D10's action matrix as an ordered walk. The vetoes come first, most
#: legible reason first; the three allow lanes partition what is left.
ACTION_RULES = (
    AllowRule("priority_lane", _outside_priority_lane),
    Rule("submit_requires_ready_health", _health_not_ready),
    Rule("submit_refused_while_halted", _breaker_halted),
    Rule("submit_blocked_by_pending_control", _control_pending),
    Rule("reduction_origin_requires_reducing", _reduction_leg_outside_reducing),
    Rule("model_origin_refused_while_reducing", _model_leg_in_live_reduction_cycle),
    Rule("reducing_permits_only_proven_reductions", _unproven_effect_while_reducing),
    Rule("live_submit_requires_go", _live_without_go),
    Rule("live_submit_requires_ordinary_arm", _live_active_without_ordinary_arm),
    Rule("live_reduction_requires_reduction_right", _live_reducing_without_reduction_right),
    AllowRule("simulated_submit", _outside_simulated_lane),
    AllowRule("live_ordinary_submit", _outside_live_ordinary_lane),
    AllowRule("live_reduction_submit", _outside_live_reduction_lane),
)


class ActionPolicy(_Policy):
    """Who may act: D10's matrix over a ``PolicyRequest`` (§5.14).

    Parameters
    ----------
    rules : tuple of Rule, optional
        The ordered rules to walk; :data:`ACTION_RULES` by default. Any
        other value is for tests of the machinery — the shipped matrix is
        the default.

    Raises
    ------
    ProductionError
        If ``rules`` is not a sequence of uniquely named ``Rule`` objects.

    Examples
    --------
    The permitted live cell, then the same moment with the breaker halted::

        from dskit.production.records import PolicyRequest

        policy = ActionPolicy()
        request = PolicyRequest(
            operation="submit", risk_effect="increase", rung="live", breaker="active",
            health="ready", readiness="go", authority="ordinary", origin="model",
            pending_control=False,
        )
        policy.permits(request)
        # -> PolicyDecision(allowed=True, reason="live_ordinary_submit")
        halted = PolicyRequest(**{**request.to_obj(), "breaker": "halted"})
        policy.permits(halted).reason  # "submit_refused_while_halted"
    """

    def __init__(self, rules=ACTION_RULES):
        super().__init__(rules)

    def permits(self, request):
        """Return the matrix's answer for one moment.

        Parameters
        ----------
        request : PolicyRequest
            The nine closed facts — operation, risk effect, rung, breaker,
            health, readiness, authority, origin, pending control — as one
            value, so the request and the call cannot drift apart.

        Returns
        -------
        PolicyDecision
            The first rule's decision, or the fall-through refusal.

        Raises
        ------
        ProductionError
            If ``request`` is not a ``PolicyRequest``.
        """
        if not isinstance(request, PolicyRequest):
            raise ProductionError(
                [f"permits takes a PolicyRequest, got {type(request).__name__}"]
            )
        return self._decide(request)


# ---------------------------------------------------------------------------
# The breaker's transitions (D10, D12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Transition:
    """The breaker transition ``TransitionPolicy.permits`` was asked for — what its rules receive."""

    from_state: object
    to_state: object
    cause: object
    proof: object

    @property
    def triple(self):
        """The ``(from_state, to_state, cause)`` a door admits or not; never the proof."""
        return (self.from_state, self.to_state, self.cause)


@dataclass(frozen=True)
class _Door:
    """One family of D10's permitted transitions, and whether its cause is a verified act needing a proof."""

    triples: frozenset
    proven: bool

    def admits(self, transition):
        """Say whether ``transition``'s triple is one of this door's."""
        return transition.triple in self.triples

    def __call__(self, transition):
        """Serve as an allow rule's veto: ``None`` inside this door, a reason outside it."""
        if self.admits(transition):
            return None
        return "another door, or none"


#: ``active → reducing`` by a verified reduce or flatten request.
_REDUCE_DOOR = _Door(
    frozenset({("active", "reducing", "reduce"), ("active", "reducing", "flatten_request")}),
    proven=True,
)
#: ``halted → reducing`` by a verified flatten request only — a reduce
#: grants no submit authority and cannot reopen a halted series (D12).
_FLATTEN_FROM_HALTED_DOOR = _Door(frozenset({("halted", "reducing", "flatten_request")}), proven=True)
#: ``{active | reducing} → halted`` by trip or HALT — never proven, because
#: stopping may not depend on a proof, an inbox or the ledger (§5.8).
_TRIP_DOOR = _Door(
    frozenset(
        {
            ("active", "halted", "trip"),
            ("active", "halted", "halt"),
            ("reducing", "halted", "trip"),
            ("reducing", "halted", "halt"),
        }
    ),
    proven=False,
)
#: ``{reducing | halted} → active`` by a verified resume.
_RESUME_DOOR = _Door(
    frozenset({("reducing", "active", "resume"), ("halted", "active", "resume")}),
    proven=True,
)
_DOORS = (_REDUCE_DOOR, _FLATTEN_FROM_HALTED_DOOR, _TRIP_DOOR, _RESUME_DOOR)

_DOOR_TRIPLES = frozenset().union(*(door.triples for door in _DOORS))
if {state for triple in _DOOR_TRIPLES for state in triple[:2]} != set(BREAKER_STATES):
    raise ProductionError(
        [f"policy.py's doors name states other than vocab.BREAKER_STATES {sorted(BREAKER_STATES)}"]
    )
if {triple[2] for triple in _DOOR_TRIPLES} != set(TRANSITION_CAUSES):
    raise ProductionError(
        [f"policy.py's doors name causes other than vocab.TRANSITION_CAUSES {sorted(TRANSITION_CAUSES)}"]
    )


def _door_of(transition):
    """Return the door admitting ``transition``, or None when D10 names none."""
    for door in _DOORS:
        if door.admits(transition):
            return door
    return None


def _no_door(transition):
    """D10: every breaker transition is explicit — a triple no door admits is refused."""
    if _door_of(transition) is None:
        return f"D10 names no transition {transition.triple!r}"
    return None


def _proof_missing(transition):
    """D11, D12: a verified cause needs its maker/checker proof; trip and halt never do."""
    door = _door_of(transition)
    if door is not None and door.proven and not transition.proof:
        return f"{transition.cause} is a verified act and needs its proof"
    return None


#: D10's transition automaton as an ordered walk: two vetoes, then the
#: four doors as allow lanes.
TRANSITION_RULES = (
    Rule("transition_not_permitted", _no_door),
    Rule("transition_requires_proof", _proof_missing),
    AllowRule("verified_reduce_enters_reducing", _REDUCE_DOOR),
    AllowRule("verified_flatten_enters_reducing_from_halted", _FLATTEN_FROM_HALTED_DOOR),
    AllowRule("trip_enters_halted", _TRIP_DOOR),
    AllowRule("verified_resume_enters_active", _RESUME_DOOR),
)


class TransitionPolicy(_Policy):
    """How the breaker may move: D10's transitions, and nothing else (§5.14).

    Named so it cannot be confused with the ``Decision`` collaborator
    bundle. No timer changes a breaker state; every transition goes through
    ``permits`` and is then ledgered and barriered by the ``Breaker``.

    Parameters
    ----------
    rules : tuple of Rule, optional
        The ordered rules to walk; :data:`TRANSITION_RULES` by default.

    Raises
    ------
    ProductionError
        If ``rules`` is not a sequence of uniquely named ``Rule`` objects.

    Examples
    --------
    A trip needs no proof; a reduce cannot reopen a halted series::

        policy = TransitionPolicy()
        policy.permits("active", "halted", "trip", None)
        # -> PolicyDecision(allowed=True, reason="trip_enters_halted")
        policy.permits("halted", "reducing", "reduce", "proof-digest").reason
        # -> "transition_not_permitted"
    """

    def __init__(self, rules=TRANSITION_RULES):
        super().__init__(rules)

    def permits(self, from_state, to_state, cause, proof):
        """Return the automaton's answer for one requested transition.

        Parameters
        ----------
        from_state : str
            The breaker's current state, one of ``BREAKER_STATES``.
        to_state : str
            The state asked for.
        cause : str
            One of ``TRANSITION_CAUSES``; anything else is refused, never
            guessed.
        proof : str or None
            The verified proof digest for a reduce, flatten request or
            resume; ``None`` when there is none. Trip and halt need none.

        Returns
        -------
        PolicyDecision
            The first rule's decision, or the fall-through refusal.
        """
        return self._decide(_Transition(from_state, to_state, cause, proof))


# ---------------------------------------------------------------------------
# The golden table (§5.14)
# ---------------------------------------------------------------------------

#: The nine axes in golden-key order, each with its members. Absence of an
#: authority and the pending-control flag are the two non-vocabulary axes.
_AXES = (
    ("rung", RUNGS),
    ("breaker", BREAKER_STATES),
    ("health", HEALTH_STATES),
    ("readiness", READINESS_VERDICTS),
    ("operation", OPERATIONS),
    ("risk_effect", RISK_EFFECTS),
    ("origin", LEG_ORIGINS),
    ("authority", (None,) + tuple(AUTHORITY_ROLES)),
    ("pending_control", (False, True)),
)


def _spelled(value):
    """Spell one axis value in a golden key: absence as ``none``, a flag as ``true``/``false``."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def decision_table(policy):
    """Render every cell of the nine-axis product under ``policy``.

    The audit artefact §5.14 asks for: generated from the rules, never
    hand-copied, so a checked-in rendering diffs to exactly the
    combinations a rule change moved.

    Parameters
    ----------
    policy : ActionPolicy
        The policy to render; any object whose ``permits(request)`` returns
        a ``PolicyDecision`` will do.

    Returns
    -------
    dict
        ``{"rung|breaker|health|readiness|operation|risk_effect|origin|authority|pending_control":
        (allowed, reason)}`` over all 17,280 cells — an absent authority
        spelled ``none``, the flag ``true`` / ``false``.
    """
    names = tuple(name for name, _ in _AXES)
    table = {}
    for values in itertools.product(*(members for _, members in _AXES)):
        decision = policy.permits(PolicyRequest(**dict(zip(names, values))))
        table["|".join(_spelled(value) for value in values)] = (decision.allowed, decision.reason)
    return table

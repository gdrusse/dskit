"""The fractional-Kelly MIO: one event's exact-log program as a MILP (proposal §8.4).

Sizing is the terminal cash step and it does not create an edge — it
harvests the one the gate admitted. What it must get exactly right is
everything that turns a belief into a cash outcome: the joint settlement
law of the event's rungs (a partition pays exactly one rung; a threshold
family pays a nested set), integer lots against the recorded depth of
each executable level, the venue's exact fee at the gate, and a bankroll
that stays solvent in every scenario. This module is that program, and
nothing else: it is pure and node-free. The capital node resolves a
solver through the toolkit's ``PyomoSolve`` doorway and hands it in; this
module BUILDS models and READS solutions.

Three modelling choices are load-bearing and stated once:

* **The utility is outer-approximated by tangents.** ``u`` is concave
  (``ln(w/W0)`` at full Kelly, CRRA with ``gamma = 1/lambda`` below it),
  and ``n_tangents`` tangent planes at knots spread over the reachable
  wealth interval bound it from above, so the program is a MILP a
  deterministic solver answers exactly. The approximation never reaches a
  reported number: ``expected_log_growth``, ``outlay`` and ``wealth`` are
  RECOMPUTED exactly from the integer solution.
* **Fees are exact at the gate and linear inside.** The venue's ceil or
  grid rounding is not linear, so a level enters the program only if one
  lot at its price still clears ``tau`` under the EXACT fee, and the
  program then bills ``phi = rate · p · (1 − p)`` per lot plus one flat
  :data:`ROUND_UP_CENT` per active side — the per-order round-up. After
  the solve each position is re-billed at the exact fee on its VWAP and
  ``fee_reconciled`` says whether the edge survived it.
* **Post-solve assertions are assertions, not constraints.** Solvency in
  every scenario, one side per contract, and the budget are checked on
  the exact recompute and RAISE; a budget that does not bind is the
  silent failure this child exists to refuse.

Import cost: stdlib + dskit + :mod:`pmquant.books` / :mod:`pmquant.fees`.
The node modules import this at plan time, so numpy and pyomo are
imported inside functions, never at module top.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from dskit.pipeline.records import number_ok

from .books import NET_EDGE_EPS, ContractInputs, entry_gate
from .fees import trading_fee_for_series
from .ladder.protocols import LadderType

__all__ = [
    "DEFAULT_DEPTH_HAIRCUT",
    "DEFAULT_MIN_LOT",
    "DEFAULT_N_TANGENTS",
    "DEFAULT_TAU",
    "EMPTY_STATUS",
    "LAW_SLACK_TOL",
    "OPTIMAL_STATUS",
    "ROUND_UP_CENT",
    "SIDES",
    "TAILS",
    "WEIGHT_SUM_TOL",
    "DegenerateScenarioLawError",
    "EventAllocation",
    "EventInputs",
    "ScenarioSet",
    "SideBook",
    "empty_allocation",
    "event_program",
    "gated_sides",
    "mutually_exclusive_scenarios",
    "read_allocation",
    "solve_event",
    "threshold_scenarios",
    "utility_at",
    "wealth_bounds",
]

#: Tangent knots over the reachable wealth interval. 128 on every capital
#: kind: the allocation entry points once defaulted to 24 while the replay
#: loop passed 128, and two fidelities over the same books is arithmetic
#: drift, not tuning.
DEFAULT_N_TANGENTS = 128

#: The net-edge threshold in dollars; the gate's floor under zero is
#: :data:`~pmquant.books.NET_EDGE_EPS`, so a zero tau still refuses dust.
DEFAULT_TAU = 0.0

#: Fraction of each level's recorded depth treated as fillable (1 = all).
DEFAULT_DEPTH_HAIRCUT = 1.0

#: Smallest position a side may hold if it holds one at all.
DEFAULT_MIN_LOT = 1

#: The flat per-order charge the program bills once per active side — the
#: venue's ceil-to-cent round-up, which is a fixed cost, not a rate.
ROUND_UP_CENT = 0.01

#: How far a scenario law's weights may miss one before it is refused.
WEIGHT_SUM_TOL = 1e-8

#: Slack below which a partition's beliefs are taken to tile the line
#: (no none cell) and above which they are renormalized.
LAW_SLACK_TOL = 1e-9

#: The two sides of a binary contract, in the order positions are keyed.
SIDES = ("yes", "no")

#: The threshold tails a cut law may follow — the ladder vocabulary's own
#: two-tailed tuple, imported rather than restated.
TAILS = LadderType.TWO_TAILED.tails

#: The solver termination the reader accepts; anything else refuses.
OPTIMAL_STATUS = "optimal"

#: The status of an allocation that never woke the solver.
EMPTY_STATUS = "empty"

#: Tolerance on the post-solve budget and cap checks: a millionth of a
#: dollar, four orders below a cent and above any solver feasibility
#: tolerance — so a rounding hair never refuses an honest solve while an
#: unbound budget still does.
_OUTLAY_TOL = 1e-6


class DegenerateScenarioLawError(ValueError):
    """A partition whose beliefs sum to zero cannot be renormalized into a law.

    Examples
    --------
    Two rungs the model believes impossible have no partition law::

        a = ContractInputs("A", 0.0, ((0.3, 10),), ((0.7, 10),), 0.07)
        b = ContractInputs("B", 0.0, ((0.3, 10),), ((0.7, 10),), 0.07)
        try:
            mutually_exclusive_scenarios([a, b])
        except DegenerateScenarioLawError:
            pass
    """


class ScenarioSet:
    """A joint settlement law: scenario weights and each contract's binary payoff.

    Parameters
    ----------
    weights : sequence of float
        One probability per scenario: finite, non-negative, summing to one
        within :data:`WEIGHT_SUM_TOL`.
    payoffs : Mapping of str -> sequence of float
        ``contract_id -> (n_omega,)`` binary vector, ``1`` where the
        contract settles YES in that scenario.

    Examples
    --------
    A two-rung partition::

        law = ScenarioSet([0.4, 0.6], {"A": [1, 0], "B": [0, 1]})
        law.n_omega   # 2
    """

    __slots__ = ("weights", "payoffs")

    def __init__(self, weights, payoffs):
        import numpy as np

        w = np.asarray(weights, dtype=float)
        if w.ndim != 1 or w.size == 0:
            raise ValueError(f"weights must be a non-empty 1-d vector, got shape {w.shape}")
        if not np.all(np.isfinite(w)) or bool(np.any(w < 0.0)):
            raise ValueError(f"weights must be finite and >= 0 (no negative mass), got {w!r}")
        if abs(float(w.sum()) - 1.0) > WEIGHT_SUM_TOL:
            raise ValueError(f"weights must sum to 1 within {WEIGHT_SUM_TOL}, got {float(w.sum())!r}")
        if not isinstance(payoffs, Mapping) or not payoffs:
            raise ValueError("payoffs must be a non-empty mapping of contract_id -> binary vector")
        table = {}
        for cid, vec in payoffs.items():
            if not isinstance(cid, str) or not cid:
                raise ValueError(f"payoffs keys must be non-empty contract ids, got {cid!r}")
            a = np.asarray(vec, dtype=float)
            if a.shape != w.shape:
                raise ValueError(
                    f"payoffs[{cid!r}] has length/shape {a.shape}, weights have {w.shape}"
                )
            if not np.all((a == 0.0) | (a == 1.0)):
                raise ValueError(f"payoffs[{cid!r}] must be binary (0/1 per scenario), got {a!r}")
            table[cid] = a
        self.weights = w
        self.payoffs = table

    @property
    def n_omega(self):
        """Count the scenarios (int)."""
        return int(self.weights.shape[0])

    @property
    def contract_ids(self):
        """List the contract ids in declaration order (tuple of str)."""
        return tuple(self.payoffs)

    def payoff_of(self, contract_id):
        """Give one contract's binary payoff vector.

        Parameters
        ----------
        contract_id : str
            A contract the law covers.

        Returns
        -------
        numpy.ndarray
            The ``(n_omega,)`` 0/1 vector.

        Raises
        ------
        KeyError
            When the law does not cover ``contract_id``.
        """
        return self.payoffs[contract_id]

    def __repr__(self):
        """Spell the law for a log line."""
        return f"ScenarioSet(n_omega={self.n_omega}, contracts={list(self.payoffs)})"


def _unique_ids(contracts):
    """Return the contract ids, refusing an empty list or a duplicate."""
    contracts = list(contracts)
    if not contracts:
        raise ValueError("a scenario law needs at least one contract")
    ids = [c.contract_id for c in contracts]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate contract ids in one event: {sorted(ids)}")
    return contracts, ids


def mutually_exclusive_scenarios(contracts, exhaustive=True):
    """Build the PARTITION law: exactly one rung settles YES.

    One scenario per contract in which only that contract pays. With
    ``exhaustive`` (the rungs tile the line) the beliefs are renormalized
    to sum to one; otherwise a NONE cell — no listed rung pays — carries
    the slack ``1 − Σq`` when it exceeds :data:`LAW_SLACK_TOL`, and beliefs
    summing past one are renormalized regardless. A single contract is a
    coin either way: two scenarios, pays / does not.

    Parameters
    ----------
    contracts : sequence of ContractInputs
        The event's priced rungs, unique ids.
    exhaustive : bool
        Whether the listed rungs are the whole partition.

    Returns
    -------
    ScenarioSet
        The law, contracts in the given order.

    Raises
    ------
    DegenerateScenarioLawError
        An exhaustive partition whose beliefs sum to zero.
    ValueError
        No contracts, or a duplicate id.
    """
    import numpy as np

    contracts, ids = _unique_ids(contracts)
    q = np.array([float(c.q_hat) for c in contracts])
    if len(ids) == 1:
        return ScenarioSet([q[0], 1.0 - q[0]], {ids[0]: [1.0, 0.0]})
    total = float(q.sum())
    eye = np.eye(len(ids))
    if exhaustive or total > 1.0 + LAW_SLACK_TOL:
        if total <= 0.0:
            raise DegenerateScenarioLawError(
                f"partition over {ids} has beliefs summing to {total!r}: nothing can pay, "
                "so no renormalized law exists"
            )
        return ScenarioSet(q / total, {cid: eye[i] for i, cid in enumerate(ids)})
    slack = 1.0 - total
    if slack > LAW_SLACK_TOL:
        weights = np.append(q, slack)
        return ScenarioSet(weights, {cid: np.append(eye[i], 0.0) for i, cid in enumerate(ids)})
    return ScenarioSet(q / total, {cid: eye[i] for i, cid in enumerate(ids)})


def _one_tail(tails):
    """Return the single threshold tail named by ``tails``, or refuse."""
    names = (tails,) if isinstance(tails, str) else tuple(tails)
    if len(names) != 1 or names[0] not in TAILS:
        two = tuple(sorted(TAILS))
        raise ValueError(
            f"a cut law follows exactly one threshold tail from {list(TAILS)}, got {names!r}"
            + (
                " — a two-tailed family carries two latents and no single-line cut law"
                if tuple(sorted(names)) == two
                else ""
            )
        )
    return names[0]


def threshold_scenarios(contracts, tails):
    """Build the THRESHOLD law: the K+1 cuts of one latent by K nested strikes.

    ``contracts`` must be in :func:`~pmquant.ladder.protocols.rung_sort_key`
    order. For an upper family (``greater``, rung ``i`` pays iff ``X > s_i``
    with strikes ascending) the outcomes are "X below every strike" (no
    rung pays) and "X in (s_i, s_{i+1}]" for each ``i`` (rungs ``0..i``
    pay, the last cut meaning above every strike). Cell probabilities are
    the differences of the belief sequence after projecting it onto
    non-increasing order (``np.minimum.accumulate``, clipped to [0, 1]) —
    a model's rungs need not be perfectly monotone, a law must be. A lower
    family (``less``, rung ``i`` pays iff ``X < s_i`` with caps ascending)
    is the mirror: rungs from the top pay, beliefs projected onto
    non-decreasing order.

    Parameters
    ----------
    contracts : sequence of ContractInputs
        The rungs, in rung order, unique ids.
    tails : str or sequence of str
        ``"greater"`` or ``"less"`` (a one-element sequence is accepted —
        ``LadderType.tails``). A two-tailed family is refused by name.

    Returns
    -------
    ScenarioSet
        ``K + 1`` scenarios; weights sum to one.

    Raises
    ------
    ValueError
        No contracts, a duplicate id, or a tail this law cannot cut on.
    """
    import numpy as np

    tail = _one_tail(tails)
    contracts, ids = _unique_ids(contracts)
    q = np.array([float(c.q_hat) for c in contracts])
    k = len(ids)
    weights = np.empty(k + 1)
    payoffs = {}
    if tail == "greater":
        mono = np.clip(np.minimum.accumulate(q), 0.0, 1.0)
        weights[0] = 1.0 - mono[0]
        weights[1:k] = mono[:-1] - mono[1:]
        weights[k] = mono[-1]
        for i, cid in enumerate(ids):
            payoffs[cid] = np.array([1.0 if j >= i + 1 else 0.0 for j in range(k + 1)])
    else:
        mono = np.clip(np.maximum.accumulate(q), 0.0, 1.0)
        weights[0] = mono[0]
        weights[1:k] = mono[1:] - mono[:-1]
        weights[k] = 1.0 - mono[-1]
        for i, cid in enumerate(ids):
            payoffs[cid] = np.array([1.0 if j <= i else 0.0 for j in range(k + 1)])
    return ScenarioSet(np.clip(weights, 0.0, None), payoffs)


def utility_at(wealth, bankroll, kelly_fraction):
    """Evaluate the sizing utility and its derivative at given wealth levels.

    Full Kelly (``kelly_fraction >= 1``) is ``u(w) = ln(w / W0)``; a
    fraction ``lambda < 1`` is the CRRA utility with ``gamma = 1 / lambda``,
    ``u(w) = ((w/W0)^(1−gamma) − 1) / (1 − gamma)``, whose growth-optimal
    stake is the fractional-Kelly stake. Both are zero at ``w = W0``.

    Parameters
    ----------
    wealth : numpy.ndarray
        Positive wealth levels.
    bankroll : float
        ``W0``, the reference wealth.
    kelly_fraction : float
        ``lambda`` in (0, 1].

    Returns
    -------
    tuple of numpy.ndarray
        ``(u, u_prime)`` at each wealth level.
    """
    import numpy as np

    w = np.asarray(wealth, dtype=float)
    w0 = float(bankroll)
    if float(kelly_fraction) >= 1.0:
        return np.log(w / w0), 1.0 / w
    gamma = 1.0 / float(kelly_fraction)
    ratio = w / w0
    return (ratio ** (1.0 - gamma) - 1.0) / (1.0 - gamma), ratio ** (-gamma) / w0


@dataclass(frozen=True)
class EventInputs:
    """Everything one event's program needs, validated at construction.

    Parameters
    ----------
    event_id : str
        The event ticker (the dependence cluster).
    contracts : sequence of ContractInputs
        The event's priced rungs, unique ids.
    scenarios : ScenarioSet
        The joint settlement law over exactly those ids.
    bankroll : float
        ``W0 > 0``.
    deployable : float
        ``B``, the cash this program may spend: ``0 < B < W0``.
    kelly_fraction : float
        ``lambda`` in (0, 1].
    series : str
        The event's series ticker — its venue decides the fee rounding.
    min_lot : int
        Smallest position a side may hold (default :data:`DEFAULT_MIN_LOT`).
    tau : float
        Net-edge threshold in dollars (default :data:`DEFAULT_TAU`).
    depth_haircut : float
        Fillable fraction of each level's depth, in (0, 1].
    n_tangents : int
        Tangent knots, at least 2 (default :data:`DEFAULT_N_TANGENTS`).
    event_cap : float or None
        A dollar ceiling on this event's outlay; ``None`` means the
        deployable is the only ceiling.

    Examples
    --------
    One contract, a coin law, half Kelly::

        c = ContractInputs("KXA-1-T50", 0.40, ((0.30, 2000),), ((0.72, 2000),), 0.07)
        inputs = EventInputs("KXA-1", [c], mutually_exclusive_scenarios([c]),
                             bankroll=1000.0, deployable=500.0, kelly_fraction=0.5,
                             series="KXA")
        inputs.cap   # 500.0
    """

    event_id: str
    contracts: tuple
    scenarios: ScenarioSet
    bankroll: float
    deployable: float
    kelly_fraction: float
    series: str
    min_lot: int = DEFAULT_MIN_LOT
    tau: float = DEFAULT_TAU
    depth_haircut: float = DEFAULT_DEPTH_HAIRCUT
    n_tangents: int = DEFAULT_N_TANGENTS
    event_cap: object = None

    def __post_init__(self):
        """Refuse the first shape problem, loudly."""
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError(f"event_id must be a non-empty string, got {self.event_id!r}")
        contracts = tuple(self.contracts)
        if not contracts:
            raise ValueError(f"event {self.event_id!r}: at least one contract is needed")
        for c in contracts:
            if not isinstance(c, ContractInputs):
                raise ValueError(f"contracts must be ContractInputs, got {type(c).__name__}")
        _contracts, ids = _unique_ids(contracts)
        object.__setattr__(self, "contracts", contracts)
        if not isinstance(self.scenarios, ScenarioSet):
            raise ValueError("scenarios must be a ScenarioSet")
        if set(self.scenarios.contract_ids) != set(ids):
            raise ValueError(
                f"event {self.event_id!r}: the scenario law covers "
                f"{sorted(self.scenarios.contract_ids)} but the contracts are {sorted(ids)}"
            )
        if not number_ok(self.bankroll) or self.bankroll <= 0.0:
            raise ValueError(f"bankroll must be a finite number > 0, got {self.bankroll!r}")
        if not number_ok(self.deployable) or not 0.0 < self.deployable < self.bankroll:
            raise ValueError(
                f"deployable must satisfy 0 < B < bankroll ({self.bankroll!r}), "
                f"got {self.deployable!r}"
            )
        if not number_ok(self.kelly_fraction) or not 0.0 < self.kelly_fraction <= 1.0:
            raise ValueError(f"kelly_fraction must lie in (0, 1], got {self.kelly_fraction!r}")
        if isinstance(self.min_lot, bool) or not isinstance(self.min_lot, int) or self.min_lot < 1:
            raise ValueError(f"min_lot must be an int >= 1, got {self.min_lot!r}")
        if not number_ok(self.tau) or self.tau < 0.0:
            raise ValueError(f"tau must be a finite number >= 0, got {self.tau!r}")
        if not number_ok(self.depth_haircut) or not 0.0 < self.depth_haircut <= 1.0:
            raise ValueError(f"depth_haircut must lie in (0, 1], got {self.depth_haircut!r}")
        if (
            isinstance(self.n_tangents, bool)
            or not isinstance(self.n_tangents, int)
            or self.n_tangents < 2
        ):
            raise ValueError(f"n_tangents must be an int >= 2, got {self.n_tangents!r}")
        if self.event_cap is not None and (not number_ok(self.event_cap) or self.event_cap <= 0.0):
            raise ValueError(f"event_cap must be a finite number > 0 or None, got {self.event_cap!r}")
        if not isinstance(self.series, str) or not self.series:
            raise ValueError(f"series must be a non-empty string, got {self.series!r}")

    @property
    def cap(self):
        """Give the event's outlay ceiling in dollars (float): ``event_cap`` or ``B``."""
        return float(self.deployable if self.event_cap is None else self.event_cap)


@dataclass(frozen=True, eq=False)
class SideBook:
    """One gated side of one contract: its belief, its levels, its payoff vector.

    Parameters
    ----------
    contract_id : str
        The contract ticker.
    side : str
        ``"yes"`` or ``"no"``.
    rho : float
        The belief this side pays: ``q`` for YES, ``1 − q`` for NO.
    fee_rate : float
        The threaded rate.
    levels : tuple of tuple
        ``((price, phi_lin, fillable), ...)`` cheapest first — only the
        levels a single lot still clears the gate at, after the haircut.
    payoff : numpy.ndarray
        ``(n_omega,)`` — ``1`` where this SIDE pays.

    Examples
    --------
    ::

        SideBook("KXA-1-T50", "yes", 0.40, 0.07, ((0.30, 0.0147, 2000),),
                 payoff=law.payoff_of("KXA-1-T50"))
    """

    contract_id: str
    side: str
    rho: float
    fee_rate: float
    levels: tuple
    payoff: object

    @property
    def key(self):
        """Name the position this side holds (tuple ``(contract_id, side)``)."""
        return (self.contract_id, self.side)

    @property
    def max_lots(self):
        """Sum the fillable depth over the surviving levels (int)."""
        return int(sum(level[2] for level in self.levels))


def gated_sides(inputs):
    """Apply the entry gate B1 to every contract and keep each gated side's levels.

    Per contract :func:`~pmquant.books.entry_gate` names the better side or
    ``None``. For a gated side each executable ask ``(price, depth)``
    yields ``fillable = floor(depth · depth_haircut)`` (dropped at zero)
    and survives only if one lot at that price still clears
    ``max(tau, NET_EDGE_EPS)`` under the EXACT venue fee; it enters the
    program with the LINEAR fee ``phi = rate · p · (1 − p)``.

    Parameters
    ----------
    inputs : EventInputs
        The event.

    Returns
    -------
    list of SideBook
        The gated sides, in contract order; a side may carry no levels
        (gated, but nothing fillable after the haircut).
    """
    import numpy as np

    floor_edge = max(float(inputs.tau), NET_EDGE_EPS)
    out = []
    for contract in inputs.contracts:
        side, _info = entry_gate(contract, inputs.series, inputs.tau)
        if side is None:
            continue
        payoff = np.asarray(inputs.scenarios.payoff_of(contract.contract_id), dtype=float)
        if side == "yes":
            rho, asks = float(contract.q_hat), contract.yes_levels
        else:
            rho, asks, payoff = 1.0 - float(contract.q_hat), contract.no_levels, 1.0 - payoff
        levels = []
        for price, depth in asks:
            fillable = int(math.floor(depth * inputs.depth_haircut))
            if fillable <= 0:
                continue
            fee_one = trading_fee_for_series(inputs.series, 1, price, contract.fee_rate)
            if rho - float(price) - fee_one <= floor_edge:
                continue
            phi = float(contract.fee_rate) * float(price) * (1.0 - float(price))
            levels.append((float(price), phi, fillable))
        out.append(SideBook(contract.contract_id, side, rho, float(contract.fee_rate),
                            tuple(levels), payoff))
    return out


def wealth_bounds(inputs, sides):
    """Bound the wealth every scenario can reach — the tangent knots' interval.

    ``w_lo = W0 − B`` (the whole deployable lost). ``w_hi = W0 + max_o
    [min(Σ_{paying k} M_k, B / p_min + 1) · (1 − p_min)]`` where, per
    scenario, the paying sides' lots are bounded by their fillable depth
    and by the budget at their cheapest price ``p_min``, and each lot
    gains at most ``1 − p_min``.

    Parameters
    ----------
    inputs : EventInputs
        The event.
    sides : sequence of SideBook
        The program's sides (those with levels).

    Returns
    -------
    tuple of float
        ``(w_lo, w_hi)`` with ``w_lo > 0`` and ``w_hi >= W0``.
    """
    w0, budget = float(inputs.bankroll), float(inputs.deployable)
    best = 0.0
    for o in range(inputs.scenarios.n_omega):
        paying = [s for s in sides if s.levels and s.payoff[o] > 0.5]
        if not paying:
            continue
        p_min = min(s.levels[0][0] for s in paying)
        lots = min(float(sum(s.max_lots for s in paying)), budget / p_min + 1.0)
        best = max(best, lots * (1.0 - p_min))
    return w0 - budget, w0 + best


def event_program(inputs, sides=None):
    """Build the pyomo MILP for ONE event.

    Variables: ``q[k, l]`` integer lots per side and level in
    ``[0, fillable]``; ``n[k]`` total lots per side; ``y[k]`` binary side
    active; ``z`` binary event active; ``W[o]`` wealth per scenario in
    ``[w_lo, w_hi]``; ``t[o]`` the utility surrogate. Constraints:
    ``q ≤ fillable · z``; ``n = Σ q``; ``L · y ≤ n ≤ M · y``;
    ``outlay = Σ (price + phi) · q + ROUND_UP_CENT · Σ y ≤ B`` and
    ``≤ cap · z``; ``W[o] = W0 − outlay + Σ_k payoff_k[o] · n[k]``; and one
    tangent row per scenario and knot, ``t[o] ≤ u(w_j) + u'(w_j)(W[o] − w_j)``.
    Objective: maximize ``Σ_o weight_o · t[o]``.

    Parameters
    ----------
    inputs : EventInputs
        The event.
    sides : sequence of SideBook or None
        The gated sides (from :func:`gated_sides`); recomputed when
        ``None``. Sides without levels are recorded as entered but do not
        enter the program.

    Returns
    -------
    pyomo.environ.ConcreteModel
        The model, carrying its bookkeeping for :func:`read_allocation`
        under ``model._mio``.

    Raises
    ------
    ValueError
        When no gated side has a fillable level — :func:`solve_event`
        answers that case without a program.
    """
    import numpy as np
    import pyomo.environ as pyo

    gated = gated_sides(inputs) if sides is None else list(sides)
    program = [s for s in gated if s.levels]
    if not program:
        raise ValueError(
            f"event {inputs.event_id!r}: no gated side has a fillable level — nothing to "
            "program (solve_event returns an empty allocation for this case)"
        )
    w0, budget, cap = float(inputs.bankroll), float(inputs.deployable), inputs.cap
    law = inputs.scenarios
    w_lo, w_hi = wealth_bounds(inputs, program)
    knots = np.linspace(w_lo, w_hi, int(inputs.n_tangents))
    u, du = utility_at(knots, w0, inputs.kelly_fraction)

    side_ix = list(range(len(program)))
    level_ix = [(k, i) for k in side_ix for i in range(len(program[k].levels))]
    omega_ix = list(range(law.n_omega))
    tangent_ix = [(o, j) for o in omega_ix for j in range(len(knots))]

    def fillable(k, i):
        return program[k].levels[i][2]

    model = pyo.ConcreteModel(name=f"kelly-mio:{inputs.event_id}")
    model.q = pyo.Var(level_ix, domain=pyo.NonNegativeIntegers,
                      bounds=lambda m, k, i: (0, fillable(k, i)))
    model.n = pyo.Var(side_ix, domain=pyo.NonNegativeIntegers)
    model.y = pyo.Var(side_ix, domain=pyo.Binary)
    model.z = pyo.Var(domain=pyo.Binary)
    model.W = pyo.Var(omega_ix, bounds=(w_lo, w_hi))
    model.t = pyo.Var(omega_ix)

    model.level_active = pyo.Constraint(
        level_ix, rule=lambda m, k, i: m.q[k, i] <= fillable(k, i) * m.z
    )
    model.lots = pyo.Constraint(
        side_ix,
        rule=lambda m, k: m.n[k] == sum(m.q[k, i] for i in range(len(program[k].levels))),
    )
    model.min_lot = pyo.Constraint(
        side_ix, rule=lambda m, k: int(inputs.min_lot) * m.y[k] <= m.n[k]
    )
    model.max_lot = pyo.Constraint(
        side_ix, rule=lambda m, k: m.n[k] <= program[k].max_lots * m.y[k]
    )
    model.outlay = pyo.Expression(
        expr=sum(
            (program[k].levels[i][0] + program[k].levels[i][1]) * model.q[k, i]
            for k, i in level_ix
        )
        + sum(ROUND_UP_CENT * model.y[k] for k in side_ix)
    )
    model.budget = pyo.Constraint(expr=model.outlay <= budget)
    model.event_cap = pyo.Constraint(expr=model.outlay <= cap * model.z)
    model.wealth = pyo.Constraint(
        omega_ix,
        rule=lambda m, o: m.W[o]
        == w0 - m.outlay + sum(m.n[k] for k in side_ix if program[k].payoff[o] > 0.5),
    )
    model.tangent = pyo.Constraint(
        tangent_ix,
        rule=lambda m, o, j: m.t[o] <= float(u[j]) + float(du[j]) * (m.W[o] - float(knots[j])),
    )
    model.objective = pyo.Objective(
        expr=sum(float(law.weights[o]) * model.t[o] for o in omega_ix), sense=pyo.maximize
    )
    # Underscore-prefixed: plain bookkeeping for read_allocation(), invisible
    # to pyomo's component machinery (the BudgetedSelect precedent).
    model._mio = {
        "inputs": inputs,
        "sides": program,
        "entered": tuple(sorted(s.key for s in gated)),
        "w_lo": float(w_lo),
        "w_hi": float(w_hi),
    }
    return model


@dataclass(frozen=True, eq=False)
class EventAllocation:
    """What one event's solve decided, recomputed exactly from the integer solution.

    Parameters
    ----------
    event_id : str
        The event.
    positions : dict
        ``(contract_id, side) -> lots`` for every side holding lots.
    level_fills : dict
        ``(contract_id, side) -> ((price, lots), ...)`` cheapest first.
    outlay : float
        ``Σ lots · (price + phi) + ROUND_UP_CENT`` per active side.
    wealth : numpy.ndarray
        ``(n_omega,)`` terminal wealth per scenario, all positive.
    expected_log_growth : float
        ``Σ_o weight_o · ln(W_o / W0)`` — exact, never the surrogate.
    entered : tuple
        The gated ``(contract_id, side)`` pairs, sorted — lots or not.
    status : str
        The solver's termination (``"optimal"``) or :data:`EMPTY_STATUS`.
    fee_reconciled : dict
        ``(contract_id, side) -> bool``: the position's edge survives the
        EXACT fee on its total at VWAP.
    objective : float
        The surrogate objective the solver maximized (diagnostic only).

    Examples
    --------
    The allocation an event with nothing gated produces::

        alloc = empty_allocation(inputs)
        alloc.lots   # 0
    """

    event_id: str
    positions: dict
    level_fills: dict
    outlay: float
    wealth: object
    expected_log_growth: float
    entered: tuple
    status: str
    fee_reconciled: dict
    objective: float

    @property
    def lots(self):
        """Sum the lots over every position (int)."""
        return int(sum(self.positions.values()))


def empty_allocation(inputs, entered=()):
    """Answer an event that sizes nothing without a solve.

    Parameters
    ----------
    inputs : EventInputs
        The event.
    entered : iterable of tuple
        The gated ``(contract_id, side)`` pairs that had nothing fillable.

    Returns
    -------
    EventAllocation
        Zero positions, zero outlay, wealth ``W0`` everywhere, status
        :data:`EMPTY_STATUS`.
    """
    import numpy as np

    return EventAllocation(
        event_id=inputs.event_id,
        positions={},
        level_fills={},
        outlay=0.0,
        wealth=np.full(inputs.scenarios.n_omega, float(inputs.bankroll)),
        expected_log_growth=0.0,
        entered=tuple(sorted(entered)),
        status=EMPTY_STATUS,
        fee_reconciled={},
        objective=0.0,
    )


def _termination(results):
    """Spell the solver's termination condition as a string."""
    return str(getattr(getattr(results, "solver", None), "termination_condition", None))


def _lots_of(variable, where):
    """Read an integer variable's value, refusing an unloaded or fractional one."""
    value = variable.value
    if value is None:
        raise RuntimeError(f"{where}: the solver loaded no value")
    lots = int(round(float(value)))
    if abs(float(value) - lots) > 1e-6:
        raise AssertionError(f"{where}: integer variable came back fractional ({value!r})")
    return lots


def read_allocation(model, results):
    """Read the solved program back and recompute every reported number exactly.

    Parameters
    ----------
    model : pyomo.environ.ConcreteModel
        The solved model from :func:`event_program`.
    results : object
        What the solver's ``solve()`` returned.

    Returns
    -------
    EventAllocation
        Positions, fills, exact outlay/wealth/growth, the gated set, the
        per-side fee reconciliation.

    Raises
    ------
    RuntimeError
        When the termination is not :data:`OPTIMAL_STATUS`, or a variable
        carries no value.
    AssertionError
        When the exact recompute breaks the budget or cap, leaves a
        scenario insolvent, or holds both sides of one contract.
    """
    import numpy as np

    status = _termination(results)
    if status != OPTIMAL_STATUS:
        raise RuntimeError(
            f"solver finished with termination condition {status!r}, not "
            f"{OPTIMAL_STATUS!r} — refusing to read an allocation off a non-optimal solve"
        )
    meta = model._mio
    inputs, sides = meta["inputs"], meta["sides"]
    positions, level_fills, outlay = {}, {}, 0.0
    for k, side in enumerate(sides):
        fills = []
        for i, (price, phi, fillable) in enumerate(side.levels):
            lots = _lots_of(model.q[k, i], f"{side.key} level {price}")
            if lots < 0 or lots > fillable:
                raise AssertionError(f"{side.key}: {lots} lots at {price} exceed fillable {fillable}")
            if lots:
                fills.append((price, lots))
                outlay += lots * (price + phi)
        n = sum(lots for _price, lots in fills)
        if n:
            outlay += ROUND_UP_CENT
            positions[side.key] = n
            level_fills[side.key] = tuple(fills)
    if len({contract for contract, _side in positions}) != len(positions):
        raise AssertionError(f"both sides of one contract hold lots: {sorted(positions)}")
    if outlay > inputs.deployable + _OUTLAY_TOL:
        raise AssertionError(
            f"budget violated: outlay {outlay!r} exceeds deployable {inputs.deployable!r}"
        )
    if outlay > inputs.cap + _OUTLAY_TOL:
        raise AssertionError(f"event cap violated: outlay {outlay!r} exceeds cap {inputs.cap!r}")
    w0 = float(inputs.bankroll)
    wealth = np.full(inputs.scenarios.n_omega, w0 - outlay)
    by_key = {s.key: s for s in sides}
    for key, n in positions.items():
        wealth = wealth + n * by_key[key].payoff
    if not bool(np.all(wealth > 0.0)):
        raise AssertionError(f"insolvent in some scenario: wealth {wealth!r}")
    growth = float(np.sum(inputs.scenarios.weights * np.log(wealth / w0)))
    reconciled = {}
    for key, n in positions.items():
        side = by_key[key]
        premium = sum(price * lots for price, lots in level_fills[key])
        vwap = premium / n
        exact = trading_fee_for_series(inputs.series, n, vwap, side.fee_rate)
        reconciled[key] = bool(n * side.rho - premium - exact > 0.0)
    objective = model.objective()
    return EventAllocation(
        event_id=inputs.event_id,
        positions=positions,
        level_fills=level_fills,
        outlay=float(outlay),
        wealth=wealth,
        expected_log_growth=growth,
        entered=meta["entered"],
        status=status,
        fee_reconciled=reconciled,
        objective=float(objective) if objective is not None else 0.0,
    )


def solve_event(inputs, solver):
    """Gate, program, solve and read ONE event — or answer empty without a solve.

    Parameters
    ----------
    inputs : EventInputs
        The event.
    solver : object
        A resolved pyomo solver (``solve(model)``), options already
        applied — the node resolves it through the doorway.

    Returns
    -------
    EventAllocation
        The allocation; an event with no gated fillable side never wakes
        the solver.
    """
    gated = gated_sides(inputs)
    if not any(s.levels for s in gated):
        return empty_allocation(inputs, entered=[s.key for s in gated])
    model = event_program(inputs, gated)
    results = solver.solve(model)
    return read_allocation(model, results)

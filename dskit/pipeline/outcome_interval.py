"""Uncertainty about a REALIZED outcome when the observations are dependent.

How wide is the honest band around the next realization, and what joint
states of the world should a decision be tested against?

This is deliberately NOT the question :mod:`~dskit.pipeline.mean_interval`
answers. An interval for a MEAN shrinks as evidence accumulates and
describes where an average effect sits; an interval for a REALIZED
outcome does not shrink, because the thing being bracketed is a single
future draw and its spread is a property of the world rather than of the
sample. Reading a mean's confidence interval as a predictive band is the
single error this module exists to make impossible to commit silently.

A caller arrives with time-ordered out-of-fold residuals -- one
SIMULTANEOUS vector per observation, one component per named thing it
forecasts -- and needs two products from them:

* a predictive interval per component at a stated coverage, and
* a finite JOINT scenario set: weights that sum to one, and one array per
  component, where scenario ``omega`` means the same state of the world in
  every array.

**The dependence is an argument and is never defaulted.**
:class:`BlockResiduals` refuses to exist without explicit block labels.
There is no default block rule, because the only available default --
"every row is its own evidence" -- yields the narrowest possible band,
and a too-narrow band is precisely the failure this module prevents. The
block is :mod:`~dskit.pipeline.attempts`'s doctrine in argument form: the
exchangeable unit is a WHOLE session, never a row, because a session
moves every overlapping label with it. ``attempts.utc_day`` is the
canonical key to pass; its integer day index is an accepted block id for
exactly that reason.

**Ordinary IID split conformal is not assumed valid.** Following
Chernozhukov, Wuthrich and Zhu (2018), the exchangeable unit is the BLOCK,
which changes two things. The conformal correction counts BLOCKS, so the
level actually taken is ``ceil((B + 1) * c) / B`` rather than ``c``; and
rows are weighted so each block carries equal total mass, so the
effective sample size is the block count and one long block cannot
dominate a short one. When ``ceil((B + 1) * c) / B`` exceeds one the
requested coverage is NOT ACHIEVABLE at that block count and the
calibrator RAISES naming both numbers -- it never clamps to the widest
available quantile and calls the result calibrated.

**What is claimed, and what is not.** This is APPROXIMATE validity under
weak dependence plus a finite-sample correction applied at the block
level. It is NOT an exact finite-sample coverage guarantee: exact split
conformal needs exchangeability, which time-ordered residuals do not
have, and block-equalized weighting transfers the row-level result to
blocks by argument rather than by theorem. The decision criterion is
therefore EMPIRICAL -- coverage is measured on held-out blocks by the
suite, not asserted here.

**Scenarios are copied ROWS of whole blocks.** A drawn scenario is a real
historical simultaneous vector, so covariance, common shocks and tail
co-movement survive by construction and per-component independent
sampling is structurally impossible rather than merely discouraged. What
comes back is residuals, never payoffs: turning a residual into a payoff,
and any haircut or recentering, needs to know what was forecast, which is
what this tier must not know.

**Fail-closed, and loudly.** Non-finite input, an empty family, too few
blocks, a block that is not contiguous in time, a coverage the evidence
cannot support, weights that miss summing to one, and a scenario set with
no spread each refuse by name. Nothing here returns ``None`` for a bound
it cannot claim, because a consumer that reads an absent bound as "no
uncertainty" sizes as though there were none.

**The family is a class, not a switch.** :class:`OutcomeCalibrator` owns
``calibrate`` and ``scenarios`` as template methods a member can never
replace -- enforced by ``__init_subclass__``, not by a docstring asking
nicely. A member supplies three hooks.

Nothing here is a node kind, and nothing is wired into a document.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from dskit.pipeline.metrics import pinball
from dskit.pipeline.node import class_ref
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import _bootstrap_rng

__all__ = [
    "CALIBRATORS",
    "DEFAULT_COVERAGE",
    "DEFAULT_SEED",
    "DEFAULT_WINDOW_BLOCKS",
    "MAX_SCENARIOS",
    "MIN_CALIBRATION_BLOCKS",
    "MIN_SCENARIOS",
    "WEIGHTS_SUM_TOLERANCE",
    "BlockCalibrator",
    "BlockConformalInterval",
    "BlockResiduals",
    "OutcomeCalibrator",
    "OutcomeIntervalResult",
    "ScenarioSet",
    "TwoSidedBlockConformalInterval",
    "calibrator",
    "register_calibrator",
]

#: The two-sided coverage a predictive interval is taken at when the
#: caller does not say. Coverage is a POLICY a project pins
#: prospectively, so it has ONE name and appears as a literal nowhere.
#: The block structure, by contrast, has no default at all -- see the
#: module docstring for why the two are treated differently.
DEFAULT_COVERAGE = 0.95

#: How many consecutive blocks one rolling conditional-coverage window
#: spans when the caller does not say. Small enough that a regime lasting
#: a handful of blocks is visible, rather than averaged away.
DEFAULT_WINDOW_BLOCKS = 10

#: Base seed for the scenario draw. Any fixed value gives
#: reproducibility; this one is named so it is never written twice.
DEFAULT_SEED = 0

#: The structural floor on distinct blocks. Deliberately NOT a
#: coverage-sufficiency threshold: the number of blocks a given coverage
#: actually needs is DERIVED per call by the block correction, which
#: refuses when ``ceil((B + 1) * c) / B`` exceeds one. Below two there is
#: no block structure to speak of at all, which is what this floor says.
MIN_CALIBRATION_BLOCKS = 2

#: A scenario set of one point is a point estimate wearing a
#: distribution's clothes.
MIN_SCENARIOS = 2

#: The widest scenario set this module will emit. It mirrors
#: ``libs.pyomo.HARD_N_SCENARIOS_CEILING`` -- the measured tractability
#: ceiling of the optimizer that consumes these sets -- so a set too wide
#: to solve refuses HERE, naming the scenario count, rather than from
#: inside a solve. A tier-1 module cannot import a tier-2 pack, so the
#: two copies are pinned to agree by the suite.
MAX_SCENARIOS = 256

#: How far scenario weights may miss summing to exactly one. Strictly
#: TIGHTER than the ``1e-8`` the consuming optimizer enforces, so a set
#: this module accepts can never be one that consumer rejects; the
#: relationship is pinned by the suite.
WEIGHTS_SUM_TOLERANCE = 1e-9

#: Float slack when comparing an accumulated weight against a quantile
#: level. Pure representation noise in a sum of many small weights.
_QUANTILE_EPS = 1e-12


def _canonical_digest(payload):
    """sha256 over the repo's pinned canonical JSON (sorted, compact, ASCII)."""
    canon = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


def _block_ok(value):
    """Say whether ``value`` can label a block: a non-empty str or a non-bool int."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and value != ""


def _check_coverage(value):
    """Refuse a coverage that is not a finite number strictly inside (0, 1)."""
    if not number_ok(value) or not 0.0 < float(value) < 1.0:
        raise ValueError(f"coverage must be a number in (0, 1), got {value!r}")


def _check_level(value):
    """Refuse a level that is not a finite number in (0, 1]."""
    if not number_ok(value) or not 0.0 < float(value) <= 1.0:
        raise ValueError(f"level must be a number in (0, 1], got {value!r}")


def _weighted_quantile(values, weights, level):
    """Return the smallest value whose cumulative weight reaches ``level``.

    Parameters
    ----------
    values : sequence of float
        The sample, in any order.
    weights : sequence of float
        One non-negative weight per value, summing to one.
    level : float
        The quantile level in ``(0, 1]``.

    Returns
    -------
    float
        The level-quantile under ``weights``. The weighted share of
        values at or below it is at least ``level``, which is what makes
        the interval built from it not-too-narrow.
    """
    pairs = sorted(zip(values, weights))
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= level - _QUANTILE_EPS:
            return float(value)
    return float(pairs[-1][0])


def _closed_pinball(q, y, tau):
    """Pinball loss widened to the CLOSED [0, 1] tau range via its own limit at 0 and 1.

    ``metrics.pinball`` refuses ``tau`` outside the open ``(0, 1)`` by
    contract (ADR-0054) -- correct, and untouched here. An achieved
    level of exactly 1.0 is a legitimate, maximally-conservative outcome
    (:meth:`BlockCalibrator.achievable_level` only refuses ABOVE one),
    and it drives both tail quantile levels to that boundary. The loss
    is continuous in ``tau`` and has a well-defined limit there --
    ``max(q - y, 0)`` as ``tau -> 0``, ``max(y - q, 0)`` as ``tau -> 1``
    -- computed directly instead of routing through ``pinball``.
    """
    if tau <= 0.0:
        return max(q - y, 0.0)
    if tau >= 1.0:
        return max(y - q, 0.0)
    return pinball(q, y, tau)


def _frozen_tree(value):
    """Return a read-only copy of nested mappings and sequences; other values as given."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _frozen_tree(v) for k, v in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_frozen_tree(v) for v in value)
    return value


@dataclass(frozen=True)
class BlockResiduals:
    """Time-ordered residual VECTORS beside the block structure they carry.

    An instance cannot exist without ``blocks``: the dependence statement
    is an argument, never a default, because the only available default
    is the answer that is too narrow. Row ``i`` is one SIMULTANEOUS
    observation -- every component measured at the same moment -- which is
    what lets a scenario drawn from these rows preserve cross-component
    structure.

    Parameters
    ----------
    names : sequence of str
        The component names, in order, at least one, no duplicates.
    rows : sequence of sequence of float
        One residual vector per observation, in TIME order, each as wide
        as ``names``, every value finite. A partial vector is refused
        rather than imputed: an imputed component is a fabricated joint
        observation.
    blocks : sequence
        One block label per row -- a non-empty ``str`` or a non-bool
        ``int``. ``attempts.utc_day``'s integer day index is the
        canonical key, which is why an int is accepted where the record
        envelope's ``cluster_ok`` would take only a string. Blocks must
        be CONTIGUOUS runs: a label that reappears after a different one
        began means the rows are not in time order.
    stamps : sequence of int or None
        Optional instant per row, non-decreasing, recorded as the
        calibration window in provenance.

    Raises
    ------
    ValueError
        On an empty or duplicated family, no rows, a row of the wrong
        width, a non-finite residual, misaligned or unusable blocks,
        non-contiguous blocks, out-of-order stamps, or fewer than
        :data:`MIN_CALIBRATION_BLOCKS` distinct blocks.

    Examples
    --------
    Two components over three two-row sessions::

        ev = BlockResiduals(
            names=("alpha", "beta"),
            rows=[(0.1, -0.2), (0.0, 0.3), (-0.4, 0.1),
                  (0.2, 0.0), (0.3, -0.1), (-0.1, 0.2)],
            blocks=["s1", "s1", "s2", "s2", "s3", "s3"],
        )
        ev.n_blocks
        # -> 3
    """

    names: tuple
    rows: tuple
    blocks: tuple
    stamps: tuple = ()

    def __post_init__(self):
        """Normalize every field to tuples and refuse a panel that cannot be used."""
        names = tuple(self.names)
        if not names:
            raise ValueError("names must hold at least one component")
        for name in names:
            if not isinstance(name, str) or name == "":
                raise ValueError(f"names must be non-empty strings, got {name!r}")
        if len(set(names)) != len(names):
            raise ValueError(f"names must be unique — duplicate in {list(names)}")

        rows = tuple(tuple(row) for row in self.rows)
        if not rows:
            raise ValueError("rows must hold at least one row")
        width = len(names)
        for index, row in enumerate(rows):
            if len(row) != width:
                raise ValueError(
                    f"row {index} has {len(row)} values, expected {width} "
                    f"(one per component in {list(names)})"
                )
            for position, value in enumerate(row):
                if not number_ok(value):
                    raise ValueError(
                        f"row {index} component {names[position]!r} is not a finite "
                        f"number: {value!r}"
                    )

        blocks = tuple(self.blocks)
        if len(blocks) != len(rows):
            raise ValueError(
                f"blocks holds {len(blocks)} labels for {len(rows)} rows — the two must agree"
            )
        for index, label in enumerate(blocks):
            if not _block_ok(label):
                raise ValueError(
                    f"block id at row {index} must be a non-empty string or an int, "
                    f"got {label!r}"
                )
        seen = []
        for index, label in enumerate(blocks):
            if not seen or seen[-1] != label:
                if label in seen:
                    raise ValueError(
                        f"block {label!r} reappears at row {index} after another block "
                        "began — blocks must be contiguous runs in time order"
                    )
                seen.append(label)
        if len(seen) < MIN_CALIBRATION_BLOCKS:
            raise ValueError(
                f"calibration needs at least {MIN_CALIBRATION_BLOCKS} distinct blocks, "
                f"got {len(seen)}"
            )

        stamps = tuple(self.stamps)
        if stamps:
            if len(stamps) != len(rows):
                raise ValueError(
                    f"stamps holds {len(stamps)} instants for {len(rows)} rows — "
                    "the two must agree"
                )
            for index, stamp in enumerate(stamps):
                if not number_ok(stamp):
                    raise ValueError(f"stamps at row {index} is not a finite number: {stamp!r}")
            if any(b < a for a, b in zip(stamps, stamps[1:])):
                raise ValueError("stamps must be non-decreasing — the rows are not time-ordered")

        object.__setattr__(self, "names", names)
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "stamps", stamps)
        object.__setattr__(self, "_block_ids", tuple(seen))

    @property
    def block_ids(self):
        """The distinct block labels, in order of first appearance (tuple)."""
        return self._block_ids

    @property
    def n_blocks(self):
        """How many distinct blocks the panel holds (int)."""
        return len(self._block_ids)

    @property
    def n_rows(self):
        """How many observation rows the panel holds (int)."""
        return len(self.rows)

    def block_counts(self):
        """Rows per block.

        Returns
        -------
        dict
            ``{block_id: row count}``.
        """
        counts = {}
        for label in self.blocks:
            counts[label] = counts.get(label, 0) + 1
        return counts

    def row_indices(self, block_id):
        """Row positions belonging to one block.

        Parameters
        ----------
        block_id : str or int
            The block label.

        Returns
        -------
        tuple
            The row indices, in stored order.

        Raises
        ------
        KeyError
            When no row carries ``block_id``.
        """
        found = tuple(i for i, label in enumerate(self.blocks) if label == block_id)
        if not found:
            raise KeyError(f"no row carries block {block_id!r}")
        return found

    def row_weights(self):
        """Per-row weights that give every BLOCK equal total mass.

        The block-equalized weighting is half of what makes this
        dependence-aware: under it the effective sample size is the block
        count, and a long block cannot outvote a short one.

        Returns
        -------
        tuple
            One weight per row, summing to one. Row ``i``'s weight is
            ``1 / (B * n_b(i))``.
        """
        counts = self.block_counts()
        blocks = self.n_blocks
        return tuple(1.0 / (blocks * counts[label]) for label in self.blocks)

    def column(self, name):
        """One component's residuals, in row order.

        Parameters
        ----------
        name : str
            A member of ``names``.

        Returns
        -------
        tuple
            The component's values.

        Raises
        ------
        KeyError
            When ``name`` is not a component of this panel.
        """
        if name not in self.names:
            raise KeyError(f"unknown component {name!r} — known: {list(self.names)}")
        position = self.names.index(name)
        return tuple(row[position] for row in self.rows)

    def digest(self):
        """Return the calibration hash: sha256 over this panel's canonical JSON.

        Returns
        -------
        str
            A hex digest that changes when any residual, block label,
            stamp or component name changes, and not otherwise. It is
            what binds a scenario set to the evidence it came from.
        """
        return _canonical_digest(
            {
                "names": list(self.names),
                "rows": [list(row) for row in self.rows],
                "blocks": list(self.blocks),
                "stamps": list(self.stamps),
            }
        )


@dataclass(frozen=True)
class OutcomeIntervalResult:
    """A calibrated predictive interval beside the diagnostics that judge it.

    The bounds are OFFSETS on the residual scale: a caller adds them to
    its own point forecast. Keeping them as offsets is what lets this
    tier stay ignorant of what was forecast.

    Parameters
    ----------
    coverage_target : float
        The coverage that was asked for.
    achieved_level : float
        The level actually taken after the block correction; at or above
        ``coverage_target``, never below.
    lower_offset, upper_offset : mapping
        ``name -> float``. ``lower_offset`` is at or below
        ``upper_offset`` for every component.
    realized_coverage : mapping
        ``name -> float``, block-equalized, measured IN SAMPLE on the
        calibration residuals. It is a consistency check, not evidence of
        out-of-sample coverage.
    conditional_coverage : mapping
        ``name -> float``: the WORST coverage over rolling windows of
        consecutive blocks. Summarized at its worst rather than its
        average, because an average hides the regime that fails.
    tail_loss : mapping
        ``name -> float``: weighted ``metrics.pinball`` at both
        endpoints' quantile levels.
    n_blocks, n_rows : int
        The calibration panel's size.
    calibration_hash : str
        The panel's :meth:`BlockResiduals.digest`.
    provenance : mapping
        Block rule, window size, achieved level and calibration window.

    Examples
    --------
    Built by a calibrator, never by hand::

        ev = BlockResiduals(
            names=("alpha",),
            rows=[(0.1,), (-0.2,), (0.3,), (0.0,)],
            blocks=["s1", "s1", "s2", "s2"],
        )
        result = BlockConformalInterval().calibrate(
            ev, coverage=0.6, window_blocks=2
        )
        result.upper_offset["alpha"] >= 0.0
        # -> True
    """

    coverage_target: float
    achieved_level: float
    lower_offset: dict
    upper_offset: dict
    realized_coverage: dict
    conditional_coverage: dict
    tail_loss: dict
    n_blocks: int
    n_rows: int
    calibration_hash: str
    provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        """Freeze every mapping behind a read-only view; provenance freezes DEEP."""
        for name in (
            "lower_offset",
            "upper_offset",
            "realized_coverage",
            "conditional_coverage",
            "tail_loss",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        object.__setattr__(self, "provenance", _frozen_tree(dict(self.provenance)))


@dataclass(frozen=True)
class ScenarioSet:
    """A finite JOINT scenario set: shared weights and one array per component.

    Scenario ``omega`` means the SAME state of the world in every array,
    which is the property a caller combining every component into one
    decision depends on and the reason these are not sampled per component.

    Parameters
    ----------
    weights : sequence of float
        Scenario probabilities, non-empty, finite, non-negative, summing
        to one within :data:`WEIGHTS_SUM_TOLERANCE`.
    draws : mapping
        ``name -> sequence of float``, each exactly as long as
        ``weights``, every value finite, and not all equal -- a component
        with no spread is a degenerate scenario set, refused rather than
        emitted.
    calibration_hash : str
        The digest of the panel these were drawn from.
    seed : int
        The base seed the draw used.
    provenance : mapping
        Block rule, block count, calibration window and scenario count.

    Raises
    ------
    ValueError
        On an empty family, weights that are negative, non-finite or miss
        summing to one, a draw array whose length does not match the
        weights, a non-finite draw, or a component with no spread.

    Examples
    --------
    Two equally likely joint states over two components::

        s = ScenarioSet(
            weights=(0.5, 0.5),
            draws={"alpha": (-0.02, 0.03), "beta": (-0.01, 0.04)},
            calibration_hash="abc",
            seed=0,
            provenance={},
        )
        s.weighted_draws()[0]
        # -> [0.5, 0.5]
    """

    weights: tuple
    draws: dict
    calibration_hash: str
    seed: int
    provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        """Refuse a set a consumer could not safely maximize against."""
        weights = tuple(float(w) for w in self.weights)
        if not weights:
            raise ValueError("weights must be a non-empty sequence")
        for index, weight in enumerate(weights):
            if not number_ok(weight) or weight < 0.0:
                raise ValueError(
                    f"weights must be finite and >= 0 — weight {index} is {weight!r}"
                )
        total = math.fsum(weights)
        if abs(total - 1.0) > WEIGHTS_SUM_TOLERANCE:
            raise ValueError(f"weights must sum to 1 within {WEIGHTS_SUM_TOLERANCE}, got {total!r}")

        draws = {name: tuple(float(v) for v in values) for name, values in self.draws.items()}
        if not draws:
            raise ValueError("draws must name at least one component")
        for name, values in draws.items():
            if len(values) != len(weights):
                raise ValueError(
                    f"draws[{name!r}] has length {len(values)}, expected {len(weights)} "
                    "(one value per weight)"
                )
            for index, value in enumerate(values):
                if not number_ok(value):
                    raise ValueError(
                        f"draws[{name!r}] scenario {index} is not a finite number: {value!r}"
                    )
            if len(set(values)) < 2:
                raise ValueError(
                    f"draws[{name!r}] is degenerate — every scenario carries {values[0]!r}, "
                    "which is a point estimate rather than a distribution"
                )

        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "draws", MappingProxyType(draws))
        object.__setattr__(self, "provenance", _frozen_tree(dict(self.provenance)))

    def weighted_draws(self):
        """Return the ``(weights, arrays)`` pairing a scenario-consuming optimizer takes.

        Returns
        -------
        tuple
            ``(weights, draws)`` where ``weights`` is a list of floats
            summing to one and ``draws`` is ``{name: list of float}``,
            every list as long as ``weights``. The values are RESIDUALS;
            mapping them to payoffs is the caller's domain step.
        """
        return list(self.weights), {name: list(values) for name, values in self.draws.items()}


class OutcomeCalibrator(ABC):
    """The family: calibrate a predictive interval, and draw a joint scenario set.

    ``calibrate`` and ``scenarios`` are TEMPLATE methods. A subclass can
    never replace either -- ``__init_subclass__`` raises at class
    definition, so the guarantee is enforced rather than requested. A
    member supplies three hooks, all abstract, so an incomplete member
    refuses at construction instead of failing mid-run:

    * :meth:`achievable_level` -- the level actually taken for a
      requested coverage at a given block count, or a refusal when the
      evidence cannot support it.
    * :meth:`offsets` -- the interval bounds at that level.
    * :meth:`draw_blocks` -- which blocks the scenario draw resamples.

    The templates CHECK each hook's answer rather than trusting it: a
    level below the requested coverage or above one, a missing component,
    an inverted or non-finite bound, an unknown block and a draw too
    short to fill the request all refuse naming the member.

    Examples
    --------
    Members are instantiated without arguments::

        member = BlockConformalInterval()
        member.achievable_level(0.9, 40) > 0.9
        # -> True
    """

    def __init_subclass__(cls, **kwargs):
        """Refuse a subclass that replaces a template; every HOOK stays overridable."""
        super().__init_subclass__(**kwargs)
        for final in ("calibrate", "scenarios"):
            if final in vars(cls):
                raise TypeError(
                    f"{cls.__name__} overrides {final}, which is final (ADR-0155): the "
                    "calibration steps are the seam, their order and their screens are "
                    "not — override a hook instead"
                )

    # -- the three member hooks ---------------------------------------------

    @abstractmethod
    def achievable_level(self, coverage, n_blocks):
        """Return the level to take for ``coverage`` at ``n_blocks`` blocks.

        Parameters
        ----------
        coverage : float
            The requested two-sided coverage, in ``(0, 1)``.
        n_blocks : int
            Distinct blocks in the calibration panel.

        Returns
        -------
        float
            A level at or above ``coverage`` and at most one.

        Raises
        ------
        ValueError
            When the coverage is not achievable at this block count. A
            member RAISES rather than clamping, because a clamped level
            is a coverage claim nothing earned.
        """
        raise NotImplementedError

    @abstractmethod
    def offsets(self, residuals, level):
        """Return ``{name: (lower, upper)}`` residual offsets at ``level``.

        Parameters
        ----------
        residuals : BlockResiduals
            The calibration panel.
        level : float
            The level :meth:`achievable_level` returned.

        Returns
        -------
        dict
            One finite ``(lower, upper)`` pair per component, lower at or
            below upper.
        """
        raise NotImplementedError

    @abstractmethod
    def draw_blocks(self, residuals, n_scenarios, rng):
        """Return the block labels, with replacement, the scenario draw uses.

        Parameters
        ----------
        residuals : BlockResiduals
            The calibration panel.
        n_scenarios : int
            How many scenario rows the caller asked for.
        rng : random.Random
            The seeded generator; a member must take every random choice
            from it so the draw stays reproducible.

        Returns
        -------
        sequence
            Block labels, each a member of ``residuals.block_ids``,
            together holding at least ``n_scenarios`` rows.
        """
        raise NotImplementedError

    # -- the templates -------------------------------------------------------

    def calibrate(self, residuals, coverage=DEFAULT_COVERAGE, window_blocks=DEFAULT_WINDOW_BLOCKS):
        """Calibrate a predictive interval per component, with its diagnostics.

        Parameters
        ----------
        residuals : BlockResiduals
            Time-ordered out-of-fold residual vectors and their blocks.
        coverage : float
            Target two-sided coverage in ``(0, 1)``. Default
            :data:`DEFAULT_COVERAGE`.
        window_blocks : int
            Consecutive blocks per rolling conditional-coverage window,
            at most the panel's block count. Default
            :data:`DEFAULT_WINDOW_BLOCKS`.

        Returns
        -------
        OutcomeIntervalResult
            Offsets, measured coverage, worst-window conditional
            coverage, tail loss, the calibration hash and provenance.

        Raises
        ------
        TypeError
            When ``residuals`` is not a :class:`BlockResiduals`.
        ValueError
            When ``coverage`` or ``window_blocks`` is unusable, when the
            coverage is not achievable at this block count, or when the
            member's hook breaks its contract.
        """
        self._require_panel(residuals)
        _check_coverage(coverage)
        coverage = float(coverage)
        if isinstance(window_blocks, bool) or not isinstance(window_blocks, int):
            raise ValueError(f"window_blocks must be an int, got {window_blocks!r}")
        if window_blocks < 1 or window_blocks > residuals.n_blocks:
            raise ValueError(
                f"window_blocks must be between 1 and the panel's {residuals.n_blocks} "
                f"blocks, got {window_blocks}"
            )

        level = self._checked_level(coverage, residuals.n_blocks)
        bounds = self._checked_offsets(residuals, level)
        realized, conditional, tail = self._diagnostics(residuals, bounds, level, window_blocks)
        return OutcomeIntervalResult(
            coverage_target=coverage,
            achieved_level=level,
            lower_offset={name: bounds[name][0] for name in residuals.names},
            upper_offset={name: bounds[name][1] for name in residuals.names},
            realized_coverage=realized,
            conditional_coverage=conditional,
            tail_loss=tail,
            n_blocks=residuals.n_blocks,
            n_rows=residuals.n_rows,
            calibration_hash=residuals.digest(),
            provenance=self._provenance(residuals, window_blocks=window_blocks, level=level),
        )

    def scenarios(self, residuals, n_scenarios, seed=DEFAULT_SEED):
        """Draw a joint scenario set of whole rows from whole blocks.

        Each scenario is a COPIED simultaneous vector, so the joint
        structure of the calibration panel survives exactly.

        Parameters
        ----------
        residuals : BlockResiduals
            The calibration panel.
        n_scenarios : int
            How many scenarios to emit, between :data:`MIN_SCENARIOS` and
            :data:`MAX_SCENARIOS`.
        seed : int
            Base seed. Combined with the calibration hash, so identical
            inputs give identical scenarios and different evidence gives
            a different draw at the same seed. Default
            :data:`DEFAULT_SEED`.

        Returns
        -------
        ScenarioSet
            Uniform weights and one array per component.

        Raises
        ------
        TypeError
            When ``residuals`` is not a :class:`BlockResiduals`.
        ValueError
            When ``n_scenarios`` or ``seed`` is unusable, when the
            member names a block the panel does not hold or returns too
            few rows, or when the resulting set is degenerate.
        """
        self._require_panel(residuals)
        if (
            isinstance(n_scenarios, bool)
            or not isinstance(n_scenarios, int)
            or not MIN_SCENARIOS <= n_scenarios <= MAX_SCENARIOS
        ):
            raise ValueError(
                f"n_scenarios must be an int between {MIN_SCENARIOS} and {MAX_SCENARIOS}, "
                f"got {n_scenarios!r}"
            )
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"seed must be an int, got {seed!r}")

        digest = residuals.digest()
        rng = _bootstrap_rng(seed, digest)
        drawn = self.draw_blocks(residuals, n_scenarios, rng)
        known = set(residuals.block_ids)
        picked = []
        for label in drawn:
            if label not in known:
                raise ValueError(
                    f"{type(self).__name__}.draw_blocks named block {label!r}, which this "
                    f"panel does not hold"
                )
            picked.extend(residuals.row_indices(label))
        if len(picked) < n_scenarios:
            raise ValueError(
                f"{type(self).__name__}.draw_blocks returned {len(picked)} rows for "
                f"{n_scenarios} scenarios — a member must draw until the request is filled"
            )
        picked = picked[:n_scenarios]

        weight = 1.0 / n_scenarios
        return ScenarioSet(
            weights=tuple(weight for _ in range(n_scenarios)),
            draws={
                name: tuple(residuals.rows[i][position] for i in picked)
                for position, name in enumerate(residuals.names)
            },
            calibration_hash=digest,
            seed=seed,
            provenance=self._provenance(residuals, n_scenarios=n_scenarios, seed=seed),
        )

    # -- base-owned screens and reductions ------------------------------------

    @staticmethod
    def _require_panel(residuals):
        """Refuse anything that is not a BlockResiduals."""
        if not isinstance(residuals, BlockResiduals):
            raise TypeError(
                f"residuals must be a BlockResiduals carrying an explicit block "
                f"structure, got {type(residuals).__name__}"
            )

    def _checked_level(self, coverage, n_blocks):
        """Take the member's level and refuse one that is narrower or impossible."""
        level = self.achievable_level(coverage, n_blocks)
        if not number_ok(level) or not coverage - _QUANTILE_EPS <= float(level) <= 1.0:
            raise ValueError(
                f"{type(self).__name__}.achievable_level returned {level!r} for coverage "
                f"{coverage!r} — a level must lie in [coverage, 1]; below it the interval "
                "is narrower than the uncorrected empirical quantile"
            )
        return float(level)

    def _checked_offsets(self, residuals, level):
        """Take the member's bounds and refuse a pair no interval could use."""
        bounds = self.offsets(residuals, level)
        member = type(self).__name__
        if not isinstance(bounds, dict) or set(bounds) != set(residuals.names):
            raise ValueError(
                f"{member}.offsets must return one pair per component "
                f"{list(residuals.names)}, got {sorted(bounds) if isinstance(bounds, dict) else bounds!r}"
            )
        out = {}
        for name in residuals.names:
            pair = bounds[name]
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError(f"{member}.offsets[{name!r}] must be a (lower, upper) pair")
            lower, upper = pair
            if not number_ok(lower) or not number_ok(upper):
                raise ValueError(
                    f"{member}.offsets[{name!r}] must be finite numbers, got {pair!r}"
                )
            if float(lower) > float(upper):
                raise ValueError(
                    f"{member}.offsets[{name!r}] is inverted: lower {lower!r} exceeds "
                    f"upper {upper!r}"
                )
            out[name] = (float(lower), float(upper))
        return out

    def _diagnostics(self, residuals, bounds, level, window_blocks):
        """Measure realized coverage, worst-window coverage and tail loss."""
        weights = residuals.row_weights()
        alpha = 1.0 - level
        lo_tau, hi_tau = alpha / 2.0, 1.0 - alpha / 2.0
        counts = residuals.block_counts()
        realized, conditional, tail = {}, {}, {}
        for name in residuals.names:
            values = residuals.column(name)
            lower, upper = bounds[name]
            inside = [1.0 if lower <= v <= upper else 0.0 for v in values]
            realized[name] = math.fsum(w * hit for w, hit in zip(weights, inside))
            tail[name] = math.fsum(
                w * (_closed_pinball(lower, v, lo_tau) + _closed_pinball(upper, v, hi_tau))
                for w, v in zip(weights, values)
            )
            per_block = {}
            for label, hit in zip(residuals.blocks, inside):
                per_block[label] = per_block.get(label, 0.0) + hit
            ordered = [per_block[label] / counts[label] for label in residuals.block_ids]
            conditional[name] = min(
                math.fsum(ordered[i : i + window_blocks]) / window_blocks
                for i in range(len(ordered) - window_blocks + 1)
            )
        return realized, conditional, tail

    def _provenance(self, residuals, **extra):
        """Record the block rule, the calibration window and the caller's knobs."""
        return {
            "block_rule": class_ref(type(self)),
            "n_blocks": residuals.n_blocks,
            "n_rows": residuals.n_rows,
            "components": list(residuals.names),
            "first_block": residuals.block_ids[0],
            "last_block": residuals.block_ids[-1],
            "first_stamp": residuals.stamps[0] if residuals.stamps else None,
            "last_stamp": residuals.stamps[-1] if residuals.stamps else None,
            **extra,
        }


class BlockCalibrator(OutcomeCalibrator):
    """The block-conformal half of the family: the correction and the resample.

    Supplies :meth:`achievable_level` (the conformal correction counted
    over BLOCKS) and :meth:`draw_blocks` (a uniform whole-block
    resample), leaving :meth:`offsets` abstract. A project that wants a
    different nonconformity score subclasses this and writes one method;
    a project that wants a different dependence argument entirely
    subclasses :class:`OutcomeCalibrator` instead.

    Examples
    --------
    A member that only has to say what its bounds are::

        class Widest(BlockCalibrator):
            def offsets(self, residuals, level):
                return {n: (-10.0, 10.0) for n in residuals.names}

        Widest().achievable_level(0.9, 40)
        # -> 0.925
    """

    def achievable_level(self, coverage, n_blocks):
        """Inflate ``coverage`` by the conformal correction counted over BLOCKS.

        ``ceil((B + 1) * c) / B`` -- the split-conformal finite-sample
        correction with the BLOCK as the exchangeable unit rather than
        the row. At ``B = 40`` and ``c = 0.9`` that is ``0.925``, where a
        row-counting correction over the same panel's 800 rows would be
        ``0.90125``: the two are not close, and the block one is the
        honest one when a block moves every label inside it together.

        Parameters
        ----------
        coverage : float
            Requested two-sided coverage in ``(0, 1)``.
        n_blocks : int
            Distinct blocks available.

        Returns
        -------
        float
            The inflated level, at most one.

        Raises
        ------
        ValueError
            When ``coverage`` is not a finite number in ``(0, 1)``, when
            ``n_blocks`` is not a positive int, or when the correction
            exceeds one, i.e. no quantile of this many blocks can
            support the requested coverage.
        """
        _check_coverage(coverage)
        if isinstance(n_blocks, bool) or not isinstance(n_blocks, int) or n_blocks < 1:
            raise ValueError(f"n_blocks must be a positive int, got {n_blocks!r}")
        coverage = float(coverage)
        level = math.ceil((n_blocks + 1) * coverage) / n_blocks
        if level > 1.0:
            raise ValueError(
                f"coverage {coverage!r} is not achievable from {n_blocks} blocks — the "
                f"block-conformal level would be {level!r}; ask for less coverage or "
                f"bring at least {math.ceil(1.0 / (1.0 - coverage)) - 1} blocks"
            )
        return level

    def draw_blocks(self, residuals, n_scenarios, rng):
        """Draw whole blocks uniformly with replacement until the request is filled.

        Parameters
        ----------
        residuals : BlockResiduals
            The calibration panel.
        n_scenarios : int
            Rows needed.
        rng : random.Random
            The seeded generator.

        Returns
        -------
        list
            Block labels, with replacement.

        Raises
        ------
        TypeError
            When ``residuals`` is not a :class:`BlockResiduals`.
        ValueError
            When ``n_scenarios`` is not a non-negative int, or ``rng``
            does not expose ``randrange``.
        """
        self._require_panel(residuals)
        if isinstance(n_scenarios, bool) or not isinstance(n_scenarios, int) or n_scenarios < 0:
            raise ValueError(f"n_scenarios must be a non-negative int, got {n_scenarios!r}")
        if not callable(getattr(rng, "randrange", None)):
            raise ValueError(
                f"rng must be a random.Random-like generator exposing randrange, got {rng!r}"
            )
        counts = residuals.block_counts()
        labels, collected = [], 0
        while collected < n_scenarios:
            label = residuals.block_ids[rng.randrange(residuals.n_blocks)]
            labels.append(label)
            collected += counts[label]
        return labels


class BlockConformalInterval(BlockCalibrator):
    """Symmetric block-conformal interval over absolute residuals.

    The textbook split-conformal shape with the block as the exchangeable
    unit: one block-equalized quantile of ``|residual|`` at the corrected
    level, applied either side of the point forecast.

    Examples
    --------
    Calibrate at 90% over forty blocks::

        member = BlockConformalInterval()
        member.achievable_level(0.9, 40)
        # -> 0.925
    """

    def offsets(self, residuals, level):
        """Return the symmetric ``(-q, +q)`` pair per component.

        Parameters
        ----------
        residuals : BlockResiduals
            The calibration panel.
        level : float
            The corrected level.

        Returns
        -------
        dict
            ``{name: (-q, q)}`` where ``q`` is the block-equalized
            ``level``-quantile of that component's absolute residuals.

        Raises
        ------
        TypeError
            When ``residuals`` is not a :class:`BlockResiduals`.
        ValueError
            When ``level`` is not a finite number in ``(0, 1]``.
        """
        self._require_panel(residuals)
        _check_level(level)
        weights = residuals.row_weights()
        out = {}
        for name in residuals.names:
            scores = [abs(v) for v in residuals.column(name)]
            q = _weighted_quantile(scores, weights, level)
            out[name] = (-q, q)
        return out


class TwoSidedBlockConformalInterval(BlockCalibrator):
    """Asymmetric block-conformal interval from the two signed tails.

    Where the symmetric member folds the residuals onto one scale, this
    one takes each tail's own block-equalized quantile, so a skewed
    residual law produces a skewed band. That matters whenever the
    consumer reads one tail specifically -- a one-sided tail constraint does.

    Examples
    --------
    The asymmetric counterpart, same correction::

        member = TwoSidedBlockConformalInterval()
        member.achievable_level(0.9, 40)
        # -> 0.925
    """

    def offsets(self, residuals, level):
        """Return the ``(lower, upper)`` signed tail quantiles per component.

        Parameters
        ----------
        residuals : BlockResiduals
            The calibration panel.
        level : float
            The corrected level; each tail carries half the miscoverage.

        Returns
        -------
        dict
            ``{name: (lower, upper)}`` at levels ``alpha / 2`` and
            ``1 - alpha / 2`` where ``alpha = 1 - level``.

        Raises
        ------
        TypeError
            When ``residuals`` is not a :class:`BlockResiduals`.
        ValueError
            When ``level`` is not a finite number in ``(0, 1]``.
        """
        self._require_panel(residuals)
        _check_level(level)
        weights = residuals.row_weights()
        alpha = 1.0 - level
        out = {}
        for name in residuals.names:
            values = list(residuals.column(name))
            lower = _weighted_quantile(values, weights, alpha / 2.0)
            upper = _weighted_quantile(values, weights, 1.0 - alpha / 2.0)
            out[name] = (min(lower, upper), max(lower, upper))
        return out


#: The shipped calibrators, by registry name.
CALIBRATORS = {}


def register_calibrator(name, cls, doc="") -> None:
    """Add a calibrator family member. Duplicates raise.

    Parameters
    ----------
    name : str
        The registry key.
    cls : type
        An :class:`OutcomeCalibrator` subclass.
    doc : str
        One line on what the member assumes and when to reach for it.

    Raises
    ------
    ValueError
        When ``name`` is already registered, or ``cls`` is not an
        :class:`OutcomeCalibrator` subclass.

    Examples
    --------
    A project brings its own construction::

        register_calibrator(
            "my-stationary", MyStationaryBlocks, doc="Geometric run lengths."
        )
    """
    if name in CALIBRATORS:
        raise ValueError(f"calibrator {name!r} is already registered")
    if not (isinstance(cls, type) and issubclass(cls, OutcomeCalibrator)):
        raise ValueError(f"calibrator {name!r} must be an OutcomeCalibrator subclass, got {cls!r}")
    CALIBRATORS[name] = {"cls": cls, "doc": doc}


def calibrator(name):
    """Look up a registered calibrator entry, loudly.

    Parameters
    ----------
    name : str
        The registry key.

    Returns
    -------
    dict
        ``{"cls", "doc"}``.

    Raises
    ------
    ValueError
        When nothing is registered under ``name``; the message lists
        what is.

    Examples
    --------
    Resolve the shipped symmetric member::

        entry = calibrator("block-conformal")
        member = entry["cls"]()
    """
    try:
        return CALIBRATORS[name]
    except KeyError:
        raise ValueError(f"unknown calibrator {name!r} — known: {sorted(CALIBRATORS)}") from None


register_calibrator(
    "block-conformal",
    BlockConformalInterval,
    doc="Symmetric band from block-equalized |residual| quantiles.",
)
register_calibrator(
    "block-conformal-two-sided",
    TwoSidedBlockConformalInterval,
    doc="Asymmetric band from each signed tail's own block-equalized quantile.",
)

"""A hedge attachable to any option formulation by one config block (ADR-0312).

A *hedge overlay* turns a set of candidate option structures (*items*) into a signed
vector of hedge units, and prices what those units do: a margin floor, replay legs,
a settlement and a mark. A formulation never learns which hedge it carries; it reads
the units through one arithmetic owner (the ``hedge=(h, reference)`` keyword of
:func:`~dskit.pipeline.option_structures.structure_payoff` and
:func:`~dskit.pipeline.option_structures.structure_max_loss`), so the overlay attaches
to every formulation the same way and, detached, leaves every output byte-identical.

* :class:`HedgeOverlay` - the abstract interface: ``units``, ``reference``,
  ``floor_rows``, ``legs``, ``settle``, ``mark``, ``record``; one ``validate_params``
  keyed on the class's params tuples (``_BASE_PARAMS`` + ``_EXTRA_PARAMS`` up the
  bases); :meth:`HedgeOverlay.from_method` builds the overlay a method names.
* :class:`NullHedgeOverlay` - no ``hedge`` block: every hook is the identity or zero
  and ``units`` is ``None``.
* :class:`FixedDeltaForwardHedge` - a fixed ratio of each item's market delta, sold
  through a cash-settled FORWARD against the decision chain's parity forward (no cash
  at entry, no dividend table: carry sits inside the forward), whole shares per book.

Units are shares per contract, signed (negative is sold). The structure functions
take ``h`` per option unit, so :meth:`HedgeOverlay.structure_hedge` divides by the
multiplier once. Every refusal is a stable code (:data:`REFUSALS`) that starts its
message. Counted, never silent: ``forward_spot`` (the forward fell back to the spot),
a leg that cannot be priced (``leg_no_iv``, ``no_fill_quote``, ``leg_below_min_years``:
the whole item is left unhedged) and ``rounding_residual``. Import cost: stdlib only.
"""

import math
from abc import ABC, abstractmethod
from collections import Counter

from dskit.pipeline.base import ConfigError
from dskit.pipeline.option_exposure import IV_SOURCES, LegGreeks, ParityForward
from dskit.pipeline.records import number_ok

__all__ = ["HedgeOverlay", "NullHedgeOverlay", "FixedDeltaForwardHedge", "KINDS", "REFUSALS"]

#: Where a method's hedge runs: over a saved selection, or inside the solve.
MODES = ("replay", "program")
#: The delta sources a fixed-delta hedge knows: Black-76 on the decision chain's parity forward.
DELTA_SOURCES = ("black76_chain_forward",)
#: The one regime a condition may name (ADR-0311's trailing-median rule).
CONDITION_REGIMES = ("high",)
#: Formulation keys a hedge block must never carry.
FORMULATION_KEYS = ("rho", "shock", "kelly", "sizer", "radius_scale", "risk_charge")
#: Pricing defaults a context may override by key (named once).
DEFAULT_MIN_YEARS = 0.001
DEFAULT_VOL_FLOOR = 0.01
DEFAULT_MIN_PAIRS = 1
DEFAULT_MAX_PAIR_SPREAD_FRAC = 0.5
DEFAULT_IV_DISAGREE_TOL = 0.05
#: Stable refusal codes; each starts its message.
UNKNOWN_KIND, UNKNOWN_KEY, MISSING_KEY = "hedge_unknown_kind", "hedge_unknown_key", "hedge_missing_key"
RATIO_RANGE, MODE, PARAM_VALUE = "hedge_ratio_range", "hedge_mode", "hedge_param_value"
OVERRIDES_FORMULATION = "hedge_overrides_formulation"
REBALANCE_NOT_SUPPORTED = "hedge_rebalance_not_supported"
REFUSALS = (UNKNOWN_KIND, UNKNOWN_KEY, MISSING_KEY, RATIO_RANGE, MODE, PARAM_VALUE,
            OVERRIDES_FORMULATION, REBALANCE_NOT_SUPPORTED)
#: Counter names (see the module docstring).
FORWARD_SPOT, ROUNDING_RESIDUAL = "forward_spot", "rounding_residual"


def _coded(code, message):
    """Return a problem string starting with its stable ``code``."""
    return f"{code}: {message}"


class HedgeOverlay(ABC):
    """The hedge interface: units per item, and what the units cost, settle and mark at.

    Parameters
    ----------
    params : dict
        Every key of :meth:`params_tuple`, all required (default-deny): ``kind``,
        ``mode`` (``"replay"`` or ``"program"``), ``ratio`` (number in [0, 1]),
        ``margin_fraction`` and ``cost_per_unit`` (numbers >= 0), ``round_units``
        (bool), ``condition`` (``None``, ``{"shock_held_share": x}`` with x in (0, 1]
        or ``{"vol_regime": "high"}``) plus the subclass's ``_EXTRA_PARAMS``.

    Examples
    --------
    Build the overlay a method names, then read its units for the candidate items::

        overlay = HedgeOverlay.from_method({"hedge": block})
        units = overlay.units(items, context)   # shares per contract, negative = sold
    """

    KIND = None
    _BASE_PARAMS = ("kind", "mode", "ratio", "margin_fraction", "round_units", "cost_per_unit", "condition")
    _EXTRA_PARAMS = ()

    def __init__(self, params=None):
        params = {} if params is None else params
        problems = self.validate_params(params)
        if problems:
            raise ConfigError([f"{type(self).__name__}: {p}" for p in problems])
        self.params = dict(params)
        self.counts = Counter()

    @classmethod
    def params_tuple(cls):
        """Return every param name, ``_BASE_PARAMS`` then each class's ``_EXTRA_PARAMS`` up the bases."""
        names = []
        for owner in reversed(cls.__mro__):
            names.extend(owner.__dict__.get("_BASE_PARAMS", ()) + owner.__dict__.get("_EXTRA_PARAMS", ()))
        return tuple(dict.fromkeys(names))

    @classmethod
    def validate_params(cls, params):
        """Return every problem a params mapping has, each starting with its refusal code."""
        if not isinstance(params, dict):
            return [_coded(UNKNOWN_KIND, f"hedge block must be a mapping, got {type(params).__name__}")]
        problems = []
        if params.get("kind") != cls.KIND:
            problems.append(_coded(UNKNOWN_KIND, f"kind must be {cls.KIND!r}, got {params.get('kind')!r}"))
        allowed = cls.params_tuple()
        for key in sorted(k for k in params if k in FORMULATION_KEYS):
            problems.append(_coded(OVERRIDES_FORMULATION, f"a hedge block may not name the formulation key {key!r}"))
        if "rebalance" in params:
            problems.append(_coded(REBALANCE_NOT_SUPPORTED, "only a fixed hedge is built; dynamic rebalancing is not supported"))
        unknown = sorted(k for k in params if k not in allowed and k not in FORMULATION_KEYS and k != "rebalance")
        if unknown:
            problems.append(_coded(UNKNOWN_KEY, f"unknown key(s) {unknown} - allowed: {sorted(allowed)}"))
        missing = [k for k in allowed if k not in params and k != "kind"]
        if missing:
            problems.append(_coded(MISSING_KEY, f"missing required key(s) {missing}"))
        cls._check(problems, params)
        return problems

    @classmethod
    def _check(cls, problems, params):
        """Append the value problems of the base keys that are present."""
        if "ratio" in params:
            ratio = params["ratio"]
            if not (number_ok(ratio) and 0.0 <= ratio <= 1.0):
                problems.append(_coded(RATIO_RANGE, f"ratio must be a number in [0, 1], got {ratio!r}"))
        if "mode" in params and params["mode"] not in MODES:
            problems.append(_coded(MODE, f"mode must be one of {list(MODES)}, got {params['mode']!r}"))
        for name in ("margin_fraction", "cost_per_unit"):
            if name in params and not (number_ok(params[name]) and params[name] >= 0):
                problems.append(_coded(PARAM_VALUE, f"{name} must be a finite number >= 0, got {params[name]!r}"))
        if "round_units" in params and not isinstance(params["round_units"], bool):
            problems.append(_coded(PARAM_VALUE, f"round_units must be a bool, got {params['round_units']!r}"))
        if "condition" in params:
            cls._check_condition(problems, params["condition"])

    @staticmethod
    def _check_condition(problems, condition):
        """Append a problem unless ``condition`` is ``None`` or one known decision-time test."""
        if condition is None:
            return
        bad = _coded(PARAM_VALUE, f"condition must be None, {{'shock_held_share': x in (0, 1]}} or "
                                  f"{{'vol_regime': {list(CONDITION_REGIMES)}}}, got {condition!r}")
        if not isinstance(condition, dict) or len(condition) != 1:
            problems.append(bad)
            return
        (key, value), = condition.items()
        if key == "shock_held_share":
            ok = number_ok(value) and 0.0 < value <= 1.0
        else:
            ok = key == "vol_regime" and value in CONDITION_REGIMES
        if not ok:
            problems.append(bad)

    @classmethod
    def from_method(cls, method):
        """Return the overlay a method's ``hedge`` block names (:class:`NullHedgeOverlay` without one).

        Parameters
        ----------
        method : dict
            A method document; only its optional ``hedge`` key is read.

        Returns
        -------
        HedgeOverlay
            The :data:`KINDS` subclass for the block's ``kind``.

        Raises
        ------
        ConfigError
            With the stable refusal codes (:data:`REFUSALS`) of every problem.
        """
        block = method.get("hedge")
        if block is None:
            return NullHedgeOverlay()
        kind = block.get("kind") if isinstance(block, dict) else None
        if kind not in KINDS:
            raise ConfigError([_coded(UNKNOWN_KIND, f"kind must be one of {sorted(KINDS)}, got {kind!r}")])
        return KINDS[kind](block)

    @staticmethod
    def structure_hedge(units, multiplier, reference):
        """Return the ``hedge=(h, reference)`` keyword of the structure functions, or ``None``.

        ``h`` is the contract's units per option unit, ``units / multiplier``.
        """
        if units is None:
            return None
        return units / multiplier, reference

    @abstractmethod
    def units(self, items, context):
        """Return one signed hedge-unit count (shares per contract) per item, ratio and condition applied."""

    @abstractmethod
    def reference(self, context):
        """Return the level the hedge settles against, known at the decision in ``context``."""

    def floor_rows(self, items, units, reference):
        """Return the margin floor per item: ``margin_fraction x |units_i| x reference``."""
        if units is None:
            return [0.0] * len(items)
        return [self.params["margin_fraction"] * abs(u) * reference for u in units]

    def legs(self, book, units, fill_context):
        """Return the replay legs for ``book`` (``{"contracts": n}``) at ``units`` per contract.

        One forward leg: total units (whole shares when ``round_units``), the
        reference at the fill, the fill cost ``cost_per_unit x |units|`` and the
        rounding residual (counted). A zero-unit leg is still returned.
        """
        if units is None:
            return []
        total = units * book["contracts"]
        held = _whole(total) if self.params["round_units"] else total
        self.counts[ROUNDING_RESIDUAL] += abs(total - held)
        return [{"instrument": "forward", "units": held, "reference": self.reference(fill_context),
                 "cost": self.params["cost_per_unit"] * abs(held), "residual": total - held}]

    def settle(self, legs, level):
        """Return the legs' P&L at settlement ``level``: ``sum units x (level - reference)``; fill cost excluded."""
        return math.fsum(leg["units"] * (level - leg["reference"]) for leg in legs)

    def mark(self, legs, level):
        """Return the legs' mark at ``level`` (the spot): the same forward P&L as :meth:`settle`."""
        return self.settle(legs, level)

    def record(self):
        """Return what a decision or book carries: the params and the counts so far."""
        return {**self.params, "counts": dict(self.counts)}


def _whole(value):
    """Round ``value`` to whole shares, halves away from zero."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


class NullHedgeOverlay(HedgeOverlay):
    """No hedge: every hook is the identity or zero and ``units`` is ``None``.

    Examples
    --------
    The overlay of a method with no ``hedge`` block::

        overlay = NullHedgeOverlay()
        overlay.units(items, context)   # -> None
    """

    KIND = "none"

    def __init__(self, params=None):
        problems = self.validate_params({} if params is None else params)
        if problems:
            raise ConfigError([f"NullHedgeOverlay: {p}" for p in problems])
        self.params = {}
        self.counts = Counter()

    @classmethod
    def validate_params(cls, params):
        """Return a problem for any key: the null overlay takes no params."""
        if params == {}:
            return []
        return [_coded(UNKNOWN_KEY, "the null overlay takes no params")]

    def units(self, items, context):
        """Return ``None``: the unhedged branch."""
        return None

    def reference(self, context):
        """Return ``None``: nothing settles."""
        return None

    def structure_hedge(self, units, multiplier, reference):
        """Return ``None``: the structure functions take their unhedged branch."""
        return None

    def floor_rows(self, items, units, reference):
        """Return zeros, one per item."""
        return [0.0] * len(items)

    def legs(self, book, units, fill_context):
        """Return no legs."""
        return []

    def settle(self, legs, level):
        """Return zero."""
        return 0.0

    def record(self):
        """Return ``{"kind": "none"}``."""
        return {"kind": "none"}


class FixedDeltaForwardHedge(HedgeOverlay):
    """A fixed ratio of each item's market delta, hedged with a cash-settled forward.

    Units are ``-ratio x`` the item's per-contract delta in shares (ADR-0310's
    :class:`~dskit.pipeline.option_exposure.LegGreeks`, Black-76 on the decision
    chain's :class:`~dskit.pipeline.option_exposure.ParityForward`, delta in spot
    terms by the ``F/S`` chain rule), so a long-delta book sells shares. The
    reference is the parity forward (the spot, counted, when the chain gives no
    pair). An item with any leg that cannot be priced is left unhedged and
    counted; ``ratio`` 0 gives ``+0.0`` units.

    Parameters
    ----------
    params : dict
        The base keys of :class:`HedgeOverlay` plus ``delta_source``
        (``"black76_chain_forward"``), ``iv_source`` (``"vendor"`` or ``"mid"``),
        ``rate`` (finite number) and ``parity_strikes`` (int >= 1).

    Examples
    --------
    Hedge half the delta of a long call::

        overlay = FixedDeltaForwardHedge({
            "kind": "fixed_delta_forward", "mode": "program", "ratio": 0.5,
            "delta_source": "black76_chain_forward", "iv_source": "vendor", "rate": 0.0,
            "parity_strikes": 3, "margin_fraction": 0.15, "round_units": True,
            "cost_per_unit": 0.0, "condition": None})
        item = {"legs": (("call", 1),), "strikes": (100.0,), "expiry": "2026-04-01",
                "quotes": [{"bid": 3.0, "ask": 3.1, "iv": 0.2}]}
        context = {"rows": chain_rows, "spot": 100.3, "day": "2026-03-02", "multiplier": 100}
        overlay.units([item], context)   # -> [about -26.7]
    """

    KIND = "fixed_delta_forward"
    _EXTRA_PARAMS = ("delta_source", "iv_source", "rate", "parity_strikes")

    @classmethod
    def _check(cls, problems, params):
        super()._check(problems, params)
        if "delta_source" in params and params["delta_source"] not in DELTA_SOURCES:
            problems.append(_coded(PARAM_VALUE, f"delta_source must be one of {list(DELTA_SOURCES)}, "
                                                f"got {params['delta_source']!r}"))
        if "iv_source" in params and params["iv_source"] not in IV_SOURCES:
            problems.append(_coded(PARAM_VALUE, f"iv_source must be one of {list(IV_SOURCES)}, "
                                                f"got {params['iv_source']!r}"))
        if "rate" in params and not number_ok(params["rate"]):
            problems.append(_coded(PARAM_VALUE, f"rate must be a finite number, got {params['rate']!r}"))
        if "parity_strikes" in params:
            value = params["parity_strikes"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(_coded(PARAM_VALUE, f"parity_strikes must be an int >= 1, got {value!r}"))

    def _forward(self, context):
        """Return ``(forward, source)`` from the context's chain rows and spot."""
        parity = ParityForward({
            "parity_strikes": self.params["parity_strikes"],
            "min_pairs": context.get("min_pairs", DEFAULT_MIN_PAIRS),
            "max_pair_spread_frac": context.get("max_pair_spread_frac", DEFAULT_MAX_PAIR_SPREAD_FRAC)})
        return parity.from_chain(context["rows"], context["spot"])

    def reference(self, context):
        """Return the chain's parity forward at the decision (the spot when no pair is usable)."""
        return self._forward(context)[0]

    def _gate(self, context):
        """Return 1.0 when the condition holds on the decision-day inputs, else 0.0."""
        condition = self.params["condition"]
        if condition is None:
            return 1.0
        (key, value), = condition.items()
        if key not in context:
            raise ValueError(f"condition {key!r} needs the decision-day input {key!r} in the context")
        held = context[key] >= value if key == "shock_held_share" else context[key] == value
        return 1.0 if held else 0.0

    def _greeks(self, context):
        """Return a LegGreeks for the context's multiplier."""
        params = {"rate": self.params["rate"], "iv_source": self.params["iv_source"],
                  "multiplier": context["multiplier"],
                  "min_years": context.get("min_years", DEFAULT_MIN_YEARS),
                  "vol_floor": context.get("vol_floor", DEFAULT_VOL_FLOOR)}
        if self.params["iv_source"] == "mid":
            params["iv_disagree_tol"] = context.get("iv_disagree_tol", DEFAULT_IV_DISAGREE_TOL)
        return LegGreeks(params)

    def _item_delta(self, greeks, item, forward, context):
        """Return the item's per-contract delta in shares, or the first refusal code of a leg."""
        total = []
        for (right, sign), strike, quote in zip(item["legs"], item["strikes"], item["quotes"], strict=True):
            leg = {"right": right, "strike": strike, "sign": sign, "quantity": 1, "expiry": item["expiry"]}
            row, code = greeks.of(leg, quote, forward, context["spot"], context["day"])
            if code is not None:
                return None, code
            total.append(row["delta_sh"])
        return math.fsum(total), None

    def units(self, items, context):
        """Return ``-ratio x gate x`` the per-contract delta, per item; ``0.0`` for an unpriceable item.

        Parameters
        ----------
        items : list of dict
            Each ``legs`` (``(right, sign)`` pairs), ``strikes``, ``quotes``
            (``bid``, ``ask``, ``iv`` per leg) and ``expiry`` (ISO date).
        context : dict
            Decision-day inputs: ``rows`` (the expiry's chain rows), ``spot``,
            ``day``, ``multiplier``; a condition's input (``shock_held_share`` or
            ``vol_regime``); optional ``min_pairs``, ``max_pair_spread_frac``,
            ``min_years``, ``vol_floor``, ``iv_disagree_tol``.

        Returns
        -------
        list of float
            Shares per contract, negative when sold.

        Raises
        ------
        ValueError
            When a condition's decision-day input is absent from ``context``.
        """
        gate = self._gate(context)
        forward, source = self._forward(context)
        if source == ParityForward.SOURCE_SPOT:
            self.counts[FORWARD_SPOT] += 1
        greeks = self._greeks(context)
        out = []
        for item in items:
            delta, code = self._item_delta(greeks, item, forward, context)
            if code is not None:
                self.counts[code] += 1
                out.append(0.0)
            else:
                out.append(0.0 - self.params["ratio"] * gate * delta)
        return out


#: The kinds a method's ``hedge.kind`` may name, to the class that builds them.
KINDS = {FixedDeltaForwardHedge.KIND: FixedDeltaForwardHedge}

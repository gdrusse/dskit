"""A hedge attachable to any option formulation by one config block (ADR-0312 and Amendment 1).

A *hedge overlay* turns candidate option structures (*items*) into signed HELD-share hedge units,
nets them to ONE whole quantity per book, and prices what that quantity does: a netted margin floor,
a replay leg, its costs, a settlement and a mark. A formulation never learns which hedge it carries; it
reads the book quantity through the one arithmetic owner, the ``hedge=(U_sh, F_ref)`` keyword of
:func:`~dskit.pipeline.option_structures.structure_payoff` and
:func:`~dskit.pipeline.option_structures.structure_max_loss`, so the overlay attaches the same way to
every formulation and, detached, leaves every output byte-identical.

* :class:`HedgeOverlay` - the abstract interface: ``units`` (per item, with a status), ``book_units``
  (the net, truncated quantity), ``reference``, ``floor_rows``, ``legs``, ``costs``, ``settle``,
  ``mark``, ``max_loss`` / ``hedgeable`` / ``classify_book``, ``record``; one ``validate_params`` keyed on
  the class's params tuples (``_BASE_PARAMS`` + ``_EXTRA_PARAMS`` up the bases).
* :class:`NullHedgeOverlay` - no ``hedge`` block: every hook is the identity or zero, ``units`` is
  ``None`` and ``record`` is ``{}`` (the hedge fields are ABSENT, not null).
* :class:`FixedDeltaForwardHedge` - a fixed ratio of each item's Black-76 FORWARD delta, sold through a
  cash-settled forward struck at a parity forward the caller supplies.

Sign and units: held shares, negative is sold; ``u_i = -ratio x forward delta`` with the delta read from
``LegGreeks`` (``delta_f_sh``, never re-derived), and ONE net quantity per book
``U_b = trunc(sum_i u_i q_i)`` toward zero. Nothing is computed from a chain here: the decision and fill
forwards and the daily mark forward are INPUTS (``context["forward"]``, ``mark(legs, forward)``). The cost
rule, the hard-to-borrow proxy and the hedged maximum loss are imported (``StaticDeltaHedge.carry_costs``,
``HardToBorrowProxy``, ``structure_max_loss``). Every refusal is a stable code (:data:`REFUSALS`) that
starts its message. Counted, never silent: the ``LegGreeks`` codes, ``hedge_hard_to_borrow``,
``hedge_small_delta``, ``rounding_residual``, ``mark_forward_carried`` and the ``books_<status>`` of
:meth:`HedgeOverlay.classify_book`. Import cost: stdlib only.
"""

import math
from abc import ABC, abstractmethod
from collections import Counter

from dskit.pipeline.base import ConfigError
from dskit.pipeline.option_exposure import (
    HEDGE_HTB,
    HEDGE_OK_FORWARD,
    HEDGE_UNBOUNDED,
    IV_SOURCES,
    PLACEHOLDER_HEDGE_RATES,
    ForwardHedge,
    HardToBorrowProxy,
    LegGreeks,
    StaticDeltaHedge,
)
from dskit.pipeline.option_structures import structure_max_loss
from dskit.pipeline.records import number_ok

__all__ = ["HedgeOverlay", "NullHedgeOverlay", "FixedDeltaForwardHedge", "KINDS", "REFUSALS"]

#: Where a method's hedge runs: over a saved selection, or inside the solve.
MODES = ("replay", "program")
#: The delta sources a fixed-delta hedge knows: Black-76 on the parity forward the caller supplies.
DELTA_SOURCES = ("black76_chain_forward",)
#: The one regime a condition may name (ADR-0311's trailing-median rule).
CONDITION_REGIMES = ("high",)
#: Formulation keys a hedge block must never carry.
FORMULATION_KEYS = ("rho", "shock", "kelly", "sizer", "radius_scale", "risk_charge")
#: Pricing defaults a context may override by key (named once).
DEFAULT_MIN_YEARS = 0.001
DEFAULT_VOL_FLOOR = 0.01
DEFAULT_IV_DISAGREE_TOL = 0.05
#: Stable refusal codes; each starts its message.
UNKNOWN_KIND, UNKNOWN_KEY, MISSING_KEY = "hedge_unknown_kind", "hedge_unknown_key", "hedge_missing_key"
RATIO_RANGE, MODE, PARAM_VALUE = "hedge_ratio_range", "hedge_mode", "hedge_param_value"
OVERRIDES_FORMULATION = "hedge_overrides_formulation"
REBALANCE_NOT_SUPPORTED = "hedge_rebalance_not_supported"
COST_BELOW_FLOOR = "hedge_cost_below_floor"
REFUSALS = (UNKNOWN_KIND, UNKNOWN_KEY, MISSING_KEY, RATIO_RANGE, MODE, PARAM_VALUE,
            OVERRIDES_FORMULATION, REBALANCE_NOT_SUPPORTED, COST_BELOW_FLOOR)
#: Counter names (see the module docstring).
ROUNDING_RESIDUAL, SMALL_DELTA, MARK_CARRIED = "rounding_residual", "hedge_small_delta", "mark_forward_carried"
BOOKS_PREFIX = "books_"
#: Item and book statuses beside the ``LegGreeks`` refusal codes.
STATUS_OK, STATUS_HTB, STATUS_UNBOUNDED = HEDGE_OK_FORWARD, HEDGE_HTB, HEDGE_UNBOUNDED
_MISSING_PREFIX = "missing required"


def _coded(code, message):
    """Return a problem string starting with its stable ``code``."""
    return f"{code}: {message}"


def _positive_forward(context):
    """Return the context's parity forward, which must be a positive finite number."""
    forward = context.get("forward")
    if not (number_ok(forward) and forward > 0):
        raise ValueError(f"the context needs a positive finite 'forward' (the parity forward is an input), got {forward!r}")
    return forward


class HedgeOverlay(ABC):
    """The hedge interface: units per item, one net quantity per book, and what it costs, settles and marks at.

    Parameters
    ----------
    params : dict
        Every key of :meth:`params_tuple`, all required (default-deny): ``kind``, ``mode`` (``"replay"``
        or ``"program"``), ``ratio`` (number in [0, 1]), ``margin_fraction`` and ``small_delta`` (numbers
        >= 0; ``small_delta`` cuts items with ``|Delta_F| < small_delta`` in program mode only),
        ``round_units`` (bool), ``condition`` (``None``, ``{"shock_held_share": x}`` with x in (0, 1] or
        ``{"vol_regime": "high"}``), ``hedge_cost_source`` (``"placeholder"`` or ``"measured"``), the
        three ``HardToBorrowProxy`` params and the three ``StaticDeltaHedge`` rates (the fee and
        half-spread may not sit below the recorded placeholders unless the source is ``"measured"``;
        the forward is costed with a borrow rate of 0, its carry sits inside the parity forward;
        ``hedge_borrow_rate`` is kept for the shared cost tuple and is not charged on the forward,
        owner Q9a), plus
        the subclass's ``_EXTRA_PARAMS``.

    Examples
    --------
    Build the overlay a method names, read the item units, net them for one book::

        overlay = HedgeOverlay.from_method({"hedge": block})
        entries = overlay.units(items, context)            # held shares per item, with a status
        book = overlay.book_units(entries, [1, 2])         # {"units": U_b, "status": "ok", "raw": ...}
    """

    KIND = None
    _BASE_PARAMS = ("kind", "mode", "ratio", "margin_fraction", "round_units", "condition",
                    "small_delta", "hedge_cost_source") + HardToBorrowProxy._PARAMS + StaticDeltaHedge._PARAMS
    _EXTRA_PARAMS = ()

    def __init__(self, params=None):
        params = {} if params is None else params
        problems = self.validate_params(params)
        if problems:
            raise ConfigError([f"{type(self).__name__}: {p}" for p in problems])
        self.params = dict(params)
        self.counts = Counter()
        self._htb = HardToBorrowProxy({k: params[k] for k in HardToBorrowProxy._PARAMS})
        self._cost_rule = StaticDeltaHedge({**{k: params[k] for k in StaticDeltaHedge._PARAMS},
                                            "hedge_borrow_rate": 0.0})

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
        for name in ("margin_fraction", "small_delta"):
            if name in params and not (number_ok(params[name]) and params[name] >= 0):
                problems.append(_coded(PARAM_VALUE, f"{name} must be a finite number >= 0, got {params[name]!r}"))
        if "round_units" in params and not isinstance(params["round_units"], bool):
            problems.append(_coded(PARAM_VALUE, f"round_units must be a bool, got {params['round_units']!r}"))
        if "condition" in params:
            cls._check_condition(problems, params["condition"])
        for owner in (ForwardHedge, HardToBorrowProxy, StaticDeltaHedge):
            subset = {k: params[k] for k in owner._PARAMS if k in params}
            problems.extend(_coded(PARAM_VALUE, p) for p in owner.validate_params(subset)
                            if not p.startswith(_MISSING_PREFIX))
        cls._check_cost_floors(problems, params)

    @staticmethod
    def _check_cost_floors(problems, params):
        """Refuse a fee or half-spread below the recorded placeholder unless the source is ``measured``."""
        if params.get("hedge_cost_source") != "placeholder":
            return
        for name, floor in zip(StaticDeltaHedge._PARAMS, PLACEHOLDER_HEDGE_RATES, strict=True):
            if name == "hedge_borrow_rate":
                continue
            if number_ok(params.get(name)) and params[name] < floor:
                problems.append(_coded(COST_BELOW_FLOOR, f"{name} {params[name]!r} is below the placeholder floor "
                                                         f"{floor!r}; set hedge_cost_source to 'measured' to lower it"))

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

    @abstractmethod
    def units(self, items, context):
        """Return one ``{"units", "target", "status", "hard_to_borrow"}`` per item (held shares, ratio and condition applied)."""

    @abstractmethod
    def reference(self, context):
        """Return the level the hedge settles against, a positive forward the context supplies."""

    def book_units(self, entries, quantities):
        """Return the book's ONE net quantity ``{"units", "raw", "status"}``, truncated toward zero.

        ``raw = sum_i target_i x q_i``; ``units = trunc(raw)`` (``raw`` itself when ``round_units`` is
        false). A book with an item that could not be priced is left unhedged whole (units 0, that
        item's status); a net SHORT quantity on a hard-to-borrow name is left unhedged
        (``hedge_hard_to_borrow``); a net long quantity is hedged.
        """
        unpriced = [e["status"] for e in entries if e["status"] not in (STATUS_OK, STATUS_HTB)]
        if unpriced:
            return {"units": 0, "raw": 0.0, "status": unpriced[0]}
        raw = math.fsum(e["target"] * q for e, q in zip(entries, quantities, strict=True))
        held = math.trunc(raw) if self.params["round_units"] else raw
        if held < 0 and any(e["hard_to_borrow"] for e in entries):
            return {"units": 0, "raw": 0.0, "status": STATUS_HTB}
        self.counts[ROUNDING_RESIDUAL] += abs(raw - held)
        return {"units": held, "raw": raw, "status": STATUS_OK}

    def floor_rows(self, items, entries, reference):
        """Return the two NETTED margin rows ``(plus, minus)``: per item ``+m x reference x u_i`` and ``-m x reference x u_i``.

        The optimizer bounds ``reserve >= sum_i plus_i x_i`` and ``reserve >= sum_i minus_i x_i``, so a
        book's floor is ``m x reference x |sum_i u_i x_i|``, never the gross sum.
        """
        scale = self.params["margin_fraction"] * reference
        return [scale * e["units"] for e in entries], [-scale * e["units"] for e in entries]

    def book_floor(self, units, reference):
        """Return the margin floor of a book of net ``units``: ``margin_fraction x |units| x reference``."""
        return self.params["margin_fraction"] * abs(units) * reference

    def max_loss(self, legs, strikes, credit_usd, multiplier, units, reference):
        """Return the book's maximum loss through the ONE owner, hedged by ``(units, reference)`` (unhedged when ``units`` is ``None``)."""
        hedge = None if units is None else (units, reference)
        return structure_max_loss(legs, strikes, credit_usd, multiplier, hedge=hedge)

    def hedgeable(self, legs, strikes, credit_usd, multiplier, units, reference):
        """Say whether the hedged book's loss is bounded above (the amendment's classifier)."""
        return self.max_loss(legs, strikes, credit_usd, multiplier, units, reference) != math.inf

    def classify_book(self, book, legs, strikes, credit_usd, multiplier, reference):
        """Return the book's status: its own, else ``hedge_unbounded_above`` when the hedged loss is unbounded, else ``ok``.

        Every status other than ``ok`` is counted as ``books_<status>``.
        """
        status = book["status"]
        if status == STATUS_OK and not self.hedgeable(legs, strikes, credit_usd, multiplier, book["units"], reference):
            status = STATUS_UNBOUNDED
        if status != STATUS_OK:
            self.counts[BOOKS_PREFIX + status] += 1
        return status

    def legs(self, book, fill_context):
        """Return the replay legs for a :meth:`book_units` result: one forward leg struck at the fill reference.

        ``residual = units - raw`` is what truncation left unhedged; a zero-unit leg is still returned.
        """
        return [{"instrument": "forward", "units": book["units"], "reference": self.reference(fill_context),
                 "residual": book["units"] - book["raw"]}]

    def costs(self, legs, s_settle, days):
        """Return ``trade_costs`` and ``borrow`` of the legs through ``StaticDeltaHedge.carry_costs`` (borrow rate 0)."""
        parts = [self._cost_rule.carry_costs(leg["units"], leg["reference"], s_settle, days) for leg in legs]
        return {"trade_costs": math.fsum(p["trade_costs"] for p in parts), "borrow": math.fsum(p["borrow"] for p in parts)}

    def settle(self, legs, level):
        """Return the legs' P&L at settlement ``level``: ``sum units x (level - reference)``; costs excluded."""
        return math.fsum(leg["units"] * (level - leg["reference"]) for leg in legs)

    def net_pnl(self, legs, level, days):
        """Return the legs' settled P&L net of trade costs: the book's ``hedge_pnl`` (borrow not charged)."""
        return self.settle(legs, level) - self.costs(legs, level, days)["trade_costs"]

    def mark(self, legs, forward, carried=False):
        """Return the legs' mark at ``forward``, the day's parity forward the caller supplies.

        ``carried`` says the caller fell back to the carry-ratio mark; it is counted as ``mark_forward_carried``.
        """
        if carried:
            self.counts[MARK_CARRIED] += 1
        return self.settle(legs, forward)

    def record(self):
        """Return what a decision or book carries: the params and the counts so far."""
        return {**self.params, "forward_borrow_rate": 0.0, "counts": dict(self.counts)}


class NullHedgeOverlay(HedgeOverlay):
    """No hedge: every hook is the identity or zero, ``units`` is ``None`` and ``record`` is ``{}``.

    Examples
    --------
    The overlay of a method with no ``hedge`` block::

        overlay = NullHedgeOverlay()
        overlay.units(items, context)   # -> None
        overlay.record()                # -> {}  (the hedge fields are absent)
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

    def book_units(self, entries, quantities):
        """Return ``None``: no hedge quantity."""
        return None

    def floor_rows(self, items, entries, reference):
        """Return two rows of zeros, one entry per item."""
        return [0.0] * len(items), [0.0] * len(items)

    def book_floor(self, units, reference):
        """Return zero."""
        return 0.0

    def classify_book(self, book, legs, strikes, credit_usd, multiplier, reference):
        """Return ``ok``: nothing is hedged, so nothing is blocked or counted."""
        return STATUS_OK

    def legs(self, book, fill_context):
        """Return no legs."""
        return []

    def costs(self, legs, s_settle, days):
        """Return zero costs."""
        return {"trade_costs": 0.0, "borrow": 0.0}

    def mark(self, legs, forward, carried=False):
        """Return zero."""
        return 0.0

    def record(self):
        """Return ``{}``: a method without a hedge block carries no hedge fields."""
        return {}


class FixedDeltaForwardHedge(HedgeOverlay):
    """A fixed ratio of each item's forward delta, hedged with a cash-settled forward.

    Units are held shares, ``-ratio x gate x`` the item's per-contract FORWARD delta
    (:class:`~dskit.pipeline.option_exposure.LegGreeks` ``delta_f_sh``, Black-76 on the decision parity
    forward the context supplies), so a long-delta item sells shares and ``ratio`` 0 gives ``+0.0``. Each
    item carries a status: ``ok``, a ``LegGreeks`` refusal code (a leg without iv is ``leg_no_iv``), or
    ``hedge_hard_to_borrow`` (a SHORT hedge on a name :class:`~dskit.pipeline.option_exposure.HardToBorrowProxy`
    flags); a non-``ok`` item has ``units`` 0.0, which the child excludes in program mode and, through
    :meth:`HedgeOverlay.book_units`, leaves the whole book unhedged in replay mode.

    Parameters
    ----------
    params : dict
        The base keys of :class:`HedgeOverlay` plus ``delta_source`` (``"black76_chain_forward"``),
        ``iv_source`` (``"vendor"`` or ``"mid"``) and ``rate`` (finite number).

    Examples
    --------
    Hedge half the forward delta of a long call::

        overlay = FixedDeltaForwardHedge({
            "kind": "fixed_delta_forward", "mode": "program", "ratio": 0.5,
            "delta_source": "black76_chain_forward", "iv_source": "vendor", "rate": 0.0,
            "margin_fraction": 0.15, "round_units": True, "condition": None, "small_delta": 0.0,
            "hedge_cost_source": "placeholder", "htb_lookback": 20, "htb_vol_threshold": 2.5,
            "htb_jump_threshold": 0.75, "hedge_fee_per_share": 0.005,
            "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.005})
        item = {"legs": (("call", 1),), "strikes": (100.0,), "expiry": "2026-04-01",
                "quotes": [{"bid": 3.0, "ask": 3.1, "iv": 0.2}]}
        context = {"forward": 101.5, "spot": 100.3, "day": "2026-03-02", "multiplier": 100,
                   "closes": closes_before_the_decision}
        overlay.units([item], context)[0]["units"]   # -> about -27.2
    """

    KIND = "fixed_delta_forward"
    _EXTRA_PARAMS = ("delta_source", "iv_source", "rate")

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

    def reference(self, context):
        """Return the parity forward the context supplies (decision or fill); it is never computed here."""
        return _positive_forward(context)

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

    @staticmethod
    def _item_delta(greeks, item, forward, context):
        """Return ``(delta_f_sh, None)`` summed over the item's legs, or ``(None, first refusal code)``."""
        total = []
        for (right, sign), strike, quote in zip(item["legs"], item["strikes"], item["quotes"], strict=True):
            leg = {"right": right, "strike": strike, "sign": sign, "quantity": 1, "expiry": item["expiry"]}
            row, code = greeks.of(leg, quote, forward, context["spot"], context["day"])
            if code is not None:
                return None, code
            total.append(row["delta_f_sh"])
        return math.fsum(total), None

    def _entry(self, units, status, flagged, delta=None):
        """Return one item's entry: ``units`` is what is hedged, ``target`` what the ratio asked for.

        ``delta_f_sh`` is the item's one-contract signed forward delta (``None`` when it could not be priced), so a
        caller can read the item's effective hedge ratio ``-units / delta_f_sh``.
        """
        return {"units": units if status == STATUS_OK else 0.0, "target": units, "status": status,
                "hard_to_borrow": flagged, "delta_f_sh": delta}

    def units(self, items, context):
        """Return one entry per item: ``-ratio x gate x`` the per-contract forward delta, with a status.

        Parameters
        ----------
        items : list of dict
            Each ``legs`` (``(right, sign)`` pairs), ``strikes``, ``quotes`` (``bid``, ``ask``, ``iv``
            per leg) and ``expiry`` (ISO date).
        context : dict
            Decision-day inputs: ``forward`` (the parity forward), ``spot``, ``day``, ``multiplier``;
            ``closes`` (the name's ``htb_lookback`` closes strictly before the decision, from
            ``HardToBorrowProxy.closes_before``; absent means not flagged); a condition's input
            (``shock_held_share`` or ``vol_regime``); optional ``min_years``, ``vol_floor``,
            ``iv_disagree_tol``.

        Returns
        -------
        list of dict
            ``units`` (held shares, negative = sold; 0.0 unless ``status`` is ``ok``), ``target``,
            ``status`` and ``hard_to_borrow`` (the name's flag).

        Raises
        ------
        ValueError
            When the condition's input or the forward is absent from ``context``.
        """
        gate = self._gate(context)
        forward = _positive_forward(context)
        closes = context.get("closes")
        flagged = False if closes is None else self._htb.of(closes)
        greeks = self._greeks(context)
        out = []
        for item in items:
            delta, code = self._item_delta(greeks, item, forward, context)
            if code is not None:
                self.counts[code] += 1
                out.append(self._entry(0.0, code, flagged))
                continue
            if self.params["mode"] == "program" and abs(delta) / context["multiplier"] < self.params["small_delta"]:
                self.counts[SMALL_DELTA] += 1
                out.append(self._entry(0.0, STATUS_OK, flagged, delta))
                continue
            held = 0.0 - self.params["ratio"] * gate * delta
            if flagged and held < 0:
                self.counts[STATUS_HTB] += 1
                out.append(self._entry(held, STATUS_HTB, flagged, delta))
            else:
                out.append(self._entry(held, STATUS_OK, flagged, delta))
        return out


#: The kinds a method's ``hedge.kind`` may name, to the class that builds them.
KINDS = {FixedDeltaForwardHedge.KIND: FixedDeltaForwardHedge}

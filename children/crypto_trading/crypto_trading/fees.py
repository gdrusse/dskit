"""Kalshi's taker fee, read from the ``fee_schedules`` stream and rounded per order.

INTERIM HOME (PROPOSED ADR-0242): the exact venue fee models already exist in
``children/pmquant/pmquant/fees.py``; a child may not import a sibling, and the rule
that two modules never carry the same function makes the right home dskit. Until that ADR
is decided this is a minimal copy of the Kalshi branch only, with the same rounding.

The rule (pmquant's ``kalshi_trading_fee``, Kalshi's published schedule): the fee of ONE
ORDER of ``C`` contracts at price ``P`` dollars is ``ceil_to_cent(rate * C * P * (1 - P))``.
Rounding applies to the order, so the fee per contract depends on ``C``: one contract at
a coin-flip pays 2 cents for a 1.75-cent fee, 100 contracts pay 1.75 dollars. The cents
value is snapped to :data:`CENT_ROUNDING_DECIMALS` places before the ceiling, because
binary floating point cannot hold ``0.07 * 100 * 0.25 * 100`` exactly (it is
175.00000000000003, and a bare ceiling would bill 1.76).

Nothing about the rate is typed in code. The ``fee_schedules`` stream gives each series
a ``fee_type`` and a ``fee_multiplier``; the document maps each type to its base rate
(``base_rate_by_type``, from Kalshi's schedule) and the rate is ``base * multiplier``. A
type with no model or no base rate is marked, never priced. The stream holds only the
CURRENT schedule, so a historical market is priced under today's; the row says which
retrieval it used (``fee_schedule_retrieved``). Makers pay nothing under ``quadratic``;
a type that charges them would be a new model in :data:`FEE_MODELS`.

Import cost: stdlib + dskit.
"""

import math
from abc import ABC, abstractmethod

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

from . import fields as f

__all__ = ["CENT_ROUNDING_DECIMALS", "FEE_MODELS", "FeeColumns", "FeeModel", "QuadraticFee"]

#: Places the cents value is rounded to before the ceiling: a numerical guard against binary
#: representation dust, not a business value (a genuine sub-cent overage is far above 1e-9).
CENT_ROUNDING_DECIMALS = 9


def _check_order(contracts, price, rate):
    """Refuse a fill no fee formula may price."""
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 0:
        raise ValueError(f"contracts must be a non-negative integer, got {contracts!r}")
    if not (number_ok(price) and 0.0 <= price <= 1.0):
        raise ValueError(f"price must be a number in [0, 1] dollars, got {price!r}")
    if not (number_ok(rate) and rate >= 0.0):
        raise ValueError(f"rate must be a finite number >= 0, got {rate!r}")


class FeeModel(ABC):
    """One fee type's pricing rule: the dollars charged for one order.

    Abstract: a subclass supplies :meth:`order_fee` and is registered in :data:`FEE_MODELS`.

    Examples
    --------
    Use a registered model by the venue's fee type::

        FEE_MODELS["quadratic"].order_fee(100, 0.5, 0.07)   # -> 1.75
    """

    @abstractmethod
    def order_fee(self, contracts, price, rate):
        """Return the fee in dollars for one order.

        Parameters
        ----------
        contracts : int
            Contracts in the order, at least 0.
        price : float
            Price per contract in dollars, in ``[0, 1]``.
        rate : float
            The series' effective rate (base rate times the multiplier).

        Returns
        -------
        float
            The order's fee in dollars, rounded as the venue rounds it.

        Raises
        ------
        ValueError
            When the order is one no formula may price.
        """


class QuadraticFee(FeeModel):
    """``ceil_to_cent(rate * C * P * (1 - P))`` per order, zero at the ends.

    Examples
    --------
    A coin-flip contract bought alone, and in a hundred::

        QuadraticFee().order_fee(1, 0.5, 0.07)     # -> 0.02
        QuadraticFee().order_fee(100, 0.5, 0.07)   # -> 1.75
    """

    def order_fee(self, contracts, price, rate):
        """Return the order's fee in dollars, rounded up to the next cent."""
        _check_order(contracts, price, rate)
        cents = rate * contracts * price * (1.0 - price) * 100.0
        return math.ceil(round(cents, CENT_ROUNDING_DECIMALS)) / 100.0


#: The fee types this child can price, keyed by the ``fee_type`` the venue reports.
FEE_MODELS = {"quadratic": QuadraticFee()}


class FeeColumns(Node):
    """Add taker-fee columns to each row from the ``fee_schedules`` stream (role ``transform``).

    Inputs: ``records`` (rows with ``series``, ``yes_bid``, ``yes_ask``) and ``schedules``
    (the :class:`~crypto_trading.kalshi_rows.FeeRows` records; the latest retrieval per
    series wins). Output ``records``: every row plus ``fee_rate``, ``fee_buy_yes`` (per
    contract, buying YES at the ask), ``fee_buy_no`` (per contract, buying NO at
    ``1 - bid``), ``fee_status`` and ``fee_schedule_retrieved``. Statuses besides ``ok``:
    ``no_schedule``, ``unsupported_fee_type``, ``no_multiplier``, ``no_quote``.

    Parameters
    ----------
    params : dict
        ``base_rate_by_type`` (dict, REQUIRED) fee type -> base rate, each type one of
        :data:`FEE_MODELS`; ``contracts`` (int >= 1, REQUIRED) the order size the per-order
        rounding is applied to.

    Examples
    --------
    Price a 100-contract taker order from the stream's schedule::

        node = FeeColumns("fees", {"base_rate_by_type": {"quadratic": 0.07}, "contracts": 100})
        out = node.run(ctx, {"records": rows, "schedules": schedule_rows})
        # -> out["records"][0]["fee_buy_yes"] is dollars per contract, e.g. 0.0173
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("base_rate_by_type", "contracts")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        bases = params.get("base_rate_by_type")
        if not isinstance(bases, dict) or not bases:
            problems.append(f"base_rate_by_type is required: a map fee type -> base rate, got {bases!r}")
        else:
            for fee_type, rate in bases.items():
                if fee_type not in FEE_MODELS:
                    problems.append(f"base_rate_by_type: unknown fee type {fee_type!r}; known: {sorted(FEE_MODELS)}")
                if not (number_ok(rate) and rate >= 0.0):
                    problems.append(f"base_rate_by_type[{fee_type!r}] must be a number >= 0, got {rate!r}")
        if "contracts" not in params:
            problems.append("contracts is required: the order size the fee rounding applies to")
        else:
            check_int_param(problems, "contracts", params["contracts"], ge=1)
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the columns are a function of the row, the schedules and the params.

        Parameters
        ----------
        params : dict
            Unused.
        verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def validate_inputs(self, inputs):
        """Refuse a ``records`` or ``schedules`` port that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per port that is not a list.
        """
        return [f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}"
                for port in ("records", "schedules") if not isinstance(inputs.get(port), list)]

    def run(self, ctx, inputs):
        """Add the fee columns to every row.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records`` and ``schedules``.

        Returns
        -------
        dict
            ``{"records": [...]}``, new dicts in input order.
        """
        latest = self._latest(inputs["schedules"])
        records = [self._priced(dict(row), latest.get(row.get(f.SERIES))) for row in inputs["records"]]
        self.log.info("priced fees on %d row(s)", len(records))
        return {"records": records}

    @staticmethod
    def _latest(schedules):
        """Map each series to its most recently retrieved schedule row."""
        latest = {}
        for row in schedules:
            held = latest.get(row[f.SERIES])
            if held is None or row[f.RETRIEVED_MS] > held[f.RETRIEVED_MS]:
                latest[row[f.SERIES]] = row
        return latest

    def _rate(self, schedule):
        """Return ``(rate, status)``: the effective rate, or None and why there is none."""
        if schedule is None:
            return None, "no_schedule"
        base = self.params["base_rate_by_type"].get(schedule[f.FEE_TYPE])
        if base is None:
            return None, "unsupported_fee_type"
        multiplier = schedule[f.FEE_MULTIPLIER]
        if not (number_ok(multiplier) and multiplier >= 0.0):
            return None, "no_multiplier"
        return base * multiplier, "ok"

    def _priced(self, row, schedule):
        """Write the fee columns onto ``row`` (a copy)."""
        rate, status = self._rate(schedule)
        bid, ask = row.get(f.YES_BID), row.get(f.YES_ASK)
        row.update({"fee_rate": rate, f.FEE_BUY_YES: None, f.FEE_BUY_NO: None,
                    "fee_schedule_retrieved": None if schedule is None else schedule[f.RETRIEVED]})
        if status == "ok" and not (number_ok(bid) and number_ok(ask)):
            status = "no_quote"
        if status == "ok":
            model, count = FEE_MODELS[schedule[f.FEE_TYPE]], self.params["contracts"]
            row[f.FEE_BUY_YES] = model.order_fee(count, ask, rate) / count
            row[f.FEE_BUY_NO] = model.order_fee(count, 1.0 - bid, rate) / count
        row["fee_status"] = status
        return row

"""Liquidation tax and the hold-vs-redeploy comparison, by import path (ADR-0279).

A long-term holding carries an unrealized gain. Selling it realizes that gain,
and the tax paid today is capital an alternative use of the money never gets.
This module owns exactly that arithmetic, per row, and nothing about where the
rows come from or which strategy the money would go to: both are declared in
the document.

:class:`LiquidationTax` (role ``transform``) turns each position row into a
liquidation: proceeds (the row's declared value field), gain over the declared
basis field, the holding class (``long`` when the optional acquisition date is
at least ``long_term_days`` before ``as_of``, ``short`` when it is nearer, and
``default_holding`` when the row carries no date), the rate that class pays,
the tax and the net. A loss pays no tax unless ``loss_offset`` is declared, in
which case it is a negative tax: the offset a realized loss gives elsewhere.
``as_of`` defaults to the run's own as-of date.

:class:`HoldVsRedeploy` (role ``transform``) compares two terminal values over
``horizon_years``: holding the gross position at ``hold_return`` (a number, or
one per symbol) against redeploying the after-tax net at ``redeploy_return``
less ``redeploy_cost`` less ``risk_premium``. ``decision`` is ``exit`` only when
the redeploy path is strictly ahead; a tie holds. Two optional inputs replace
the matching params so estimates are wired, never pasted: ``hold_returns``
(``{symbol: rate}``, from :mod:`dskit.pipeline.hold_return`) and
``redeploy_return`` (a number from :mod:`dskit.pipeline.active_hurdle`, or
``None`` when that hurdle was inadmissible, which holds every row). A ``null``
``redeploy_return`` param declares that there is no alternative at all and takes
the same path, its summary source ``none`` rather than ``inadmissible``.

Taxes on the two paths are declared separately because they are paid
differently. The hold path realizes its gain once, at the horizon, over the
original basis: ``terminal_tax_rate`` with ``basis_field``. The redeploy path
pays ``redeploy_tax_rate`` either ``annual`` — a strategy that turns its book
over realizes short-term gains every year, so the rate compounds as
``(1 + r x (1 - t)) ** years`` with nothing owed at the end — or ``terminal``,
once at the horizon over the after-tax net it started from. Without the
declarations a path's tax is zero, and the summary says so.

The redeploy rate is built in a fixed order: ``net_rate = redeploy_return -
redeploy_cost``; under ``annual`` timing the tax takes ``max(net_rate, 0) x t``
(a negative rate gets no refund) and nothing else; the ``risk_premium`` is
then subtracted untaxed, since it is a haircut for forecast uncertainty and not
income. Under ``terminal`` or no timing, ``rate = net_rate - risk_premium``.

Rows that cannot be valued refuse by index and field name; nothing is filled.
That includes a short position or a negative balance: a hold-vs-exit decision
is about a long holding, so a value at or below zero (and, for the tax, a
negative basis) refuses and the document filters such rows before this
module sees them. So does an acquisition date after ``as_of`` (negative days
held). Wired rates are checked where they arrive: a ``hold_returns``
entry or a ``redeploy_return`` outside :data:`RATE_BOUNDS` (-100% to +200%)
refuses, because the usual cause is a percent where a fraction belongs (7 for
7% is +700%). Every rate, horizon and field name is a param: the module carries
no table.

Limits, disclosed rather than modelled. Every row is taxed as a taxable holding:
the account type is not an input, so a position in an IRA or a 401(k) is taxed
as if it were liquidated in a brokerage account. The hold path's dividends are
untaxed (``hold_return`` is a total return and only the terminal gain is taxed),
which favours holding; the redeploy path's annual tax does take its share of each
year's positive net return.
No state tax, wash-sale rule or loss carry-forward is modelled.

:func:`require_number` is the one reader of a numeric row field for the three
row-reading kinds (this module, :mod:`~dskit.pipeline.active_hurdle` and
:mod:`~dskit.pipeline.decision_outcomes`).

The row readers (:func:`require_rows`, :func:`require_number`,
:func:`require_amount`) and the param checks (:func:`check_name_params`,
:func:`check_redeploy_params`) are public because
:mod:`~dskit.pipeline.position_summary` reads the same rows under the same
rules; this module is their one owner.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

import math
from datetime import date

from dskit.pipeline.binary_curve import name_ok
from dskit.pipeline.document import date_problem
from dskit.pipeline.node import Node, check_int_param, check_number_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = [
    "DECISION_FIELDS", "HOLDINGS", "MAX_COUNT", "MAX_HORIZON_YEARS", "RATE_BOUNDS",
    "REDEPLOY_TAX_TIMINGS", "HoldVsRedeploy", "LiquidationTax", "check_count_param",
    "check_horizon_param", "check_name_params", "check_rate_param", "check_redeploy_params",
    "require_amount", "require_number", "require_rate", "require_rows",
]

#: The two holding classes a tax schedule distinguishes.
HOLDINGS = ("long", "short")
#: When the redeploy path pays its tax: every year as it turns over, or once at the horizon.
REDEPLOY_TAX_TIMINGS = ("annual", "terminal")

_LIQ_FIELDS = ("liq_proceeds", "liq_gain", "liq_holding", "liq_rate", "liq_tax", "liq_net")
#: The fields :class:`HoldVsRedeploy` adds to every row, which a reader of its rows may name.
DECISION_FIELDS = (
    "horizon_years", "hold_value", "hold_terminal_tax", "hold_net", "redeploy_value",
    "redeploy_terminal_tax", "redeploy_net", "advantage", "decision",
)
#: HoldVsRedeploy params that may be absent: the two a wire can replace and the two tax pairs.
_DECISION_OPTIONAL = (
    "hold_return", "redeploy_return", "terminal_tax_rate", "basis_field",
    "redeploy_tax_rate", "redeploy_tax_timing",
)
#: The one owner of the annual-return bounds (inclusive): -100% to +200%. A return is a
#: fraction; a value outside is almost surely a percent (7 for 7% is +700%) and refuses.
RATE_BOUNDS = (-1.0, 2.0)
#: The longest horizon, in years, any ADR-0279 kind accepts.
MAX_HORIZON_YEARS = 100
#: The largest count (reversion years, trials, periods a year) any ADR-0279 kind accepts. Far
#: above any real one; an int past ~1e308 cannot even become the float the arithmetic needs.
MAX_COUNT = 10 ** 6

_OUT_OF_SCOPE = "short positions and negative balances are out of scope for a hold-vs-exit decision"


def check_rate_param(problems, name, value):
    """Append a problem unless ``value`` is a finite number inside :data:`RATE_BOUNDS`."""
    check_number_param(problems, name, value, lower=RATE_BOUNDS[0], upper=RATE_BOUNDS[1])


def check_horizon_param(problems, name, value):
    """Append a problem unless ``value`` is an int in ``[1, MAX_HORIZON_YEARS]``."""
    before = len(problems)
    check_int_param(problems, name, value, ge=1)
    if len(problems) == before and value > MAX_HORIZON_YEARS:
        problems.append(f"{name} must be at most {MAX_HORIZON_YEARS} years, got {value!r}")


def check_count_param(problems, name, value, *, ge):
    """Append a problem unless ``value`` is an int in ``[ge, MAX_COUNT]``."""
    before = len(problems)
    check_int_param(problems, name, value, ge=ge)
    if len(problems) == before and value > MAX_COUNT:
        problems.append(f"{name} must be at most {MAX_COUNT}, got {value!r}")


def require_rate(key, what, value):
    """Return ``float(value)`` when it lies inside :data:`RATE_BOUNDS`; refuse by name otherwise.

    The run-time twin of :func:`check_rate_param`, for a rate that arrived on a wire or
    was computed rather than declared.
    """
    low, high = RATE_BOUNDS
    if not low <= value <= high:
        raise ValueError(
            f"{key}: {what} {value!r} is outside [{low}, {high}]; returns must be "
            "fractions (0.05 is 5%), not percent"
        )
    return float(value)


def check_name_params(problems, params, names):
    """Append a problem for every listed param that is present and not a field name."""
    for name in names:
        if name in params and not name_ok(params[name]):
            problems.append(f"{name} must be a non-empty field name, got {params[name]!r}")


def check_redeploy_params(problems, params):
    """Append a problem for each redeploy term in ``params`` that is malformed.

    The one owner of the redeploy path's declared terms, shared by every kind that
    restates them: ``redeploy_cost`` / ``risk_premium`` in ``[0, RATE_BOUNDS[1]]``;
    ``redeploy_tax_rate`` in ``[0, 1]`` and ``redeploy_tax_timing`` in
    :data:`REDEPLOY_TAX_TIMINGS`, declared together or not at all. Presence of the
    first two is the caller's rule.
    """
    for name in ("redeploy_cost", "risk_premium"):
        if name in params:
            check_number_param(problems, name, params[name], lower=0.0, upper=RATE_BOUNDS[1])
    if ("redeploy_tax_rate" in params) != ("redeploy_tax_timing" in params):
        problems.append(
            "redeploy_tax_rate and redeploy_tax_timing are declared together or not at all"
        )
    if "redeploy_tax_rate" in params:
        check_number_param(problems, "redeploy_tax_rate", params["redeploy_tax_rate"],
                           lower=0.0, upper=1.0)
    if ("redeploy_tax_timing" in params
            and params["redeploy_tax_timing"] not in REDEPLOY_TAX_TIMINGS):
        problems.append(
            f"redeploy_tax_timing must be one of {list(REDEPLOY_TAX_TIMINGS)}, "
            f"got {params['redeploy_tax_timing']!r}"
        )


def require_rows(key, records, outputs):
    """Refuse a non-list, a non-mapping row, or a row already carrying one of ``outputs``."""
    if not isinstance(records, list):
        raise ValueError(f"{key}: records must be a list, got {type(records).__name__}")
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise ValueError(f"{key}: row {index} must be a mapping, got {type(row).__name__}")
        taken = sorted(set(row) & set(outputs))
        if taken:
            raise ValueError(f"{key}: row {index} already carries {taken}")


def require_number(key, what, index, row, field):
    """Return the number ``row[field]`` holds as a float; refuse by node, input, index and field.

    The one reader of a numeric row field for every ADR-0279 kind that reads rows.
    ``what`` names the input in the message (``row``, ``position``, ``decision``).
    A missing field, a ``bool``, a non-finite value or a non-number all refuse.
    """
    value = row.get(field)
    if not number_ok(value):
        raise ValueError(f"{key}: {what} {index} field {field!r} is not a number, got {value!r}")
    return float(value)


def require_amount(key, index, row, field, *, allow_zero, why=_OUT_OF_SCOPE):
    """Return the number ``row[field]`` holds, positive (or zero when allowed); refuse otherwise.

    ``why`` ends the refusal: by default, that short positions and negative balances
    are out of scope for a hold-vs-exit decision.
    """
    value = require_number(key, "row", index, row, field)
    if value < 0.0 or (value == 0.0 and not allow_zero):
        raise ValueError(
            f"{key}: row {index} field {field!r} must be "
            f"{'non-negative' if allow_zero else 'positive'}, got {value!r}; {why}"
        )
    return value


def _growth(key, what, rate, years):
    """Return ``(1 + rate) ** years``; refuse a rate below -1, whose power is not a value."""
    if 1.0 + rate < 0.0:
        raise ValueError(f"{key}: {what} rate {rate!r} is below -1, so no terminal value exists")
    return math.pow(1.0 + rate, years)


class LiquidationTax(Node):
    """Per-row liquidation: proceeds, gain, holding class, rate, tax, net.

    Role ``transform``. See the module docstring for the rule. A row whose
    ``value_field`` is at or below zero, or whose ``basis_field`` is negative
    (a short position, a negative balance), refuses by index and field, as does
    an ``acquired_field`` date after ``as_of``.

    Parameters
    ----------
    params : dict
        ``short_term_rate`` / ``long_term_rate`` (numbers in ``[0, 1]``,
        REQUIRED); ``long_term_days`` (int >= 1, REQUIRED): days held at
        which a position is ``long``; ``default_holding`` (``long`` |
        ``short``, REQUIRED): the class of a row with no acquisition date;
        ``loss_offset`` (bool, REQUIRED): whether a loss is a negative tax;
        ``value_field`` / ``basis_field`` (field names, REQUIRED);
        ``acquired_field`` (field name, optional): the ISO date the position
        was acquired, absent or null on a row means ``default_holding``;
        ``as_of`` (ISO date, optional): the liquidation date the holding
        period is measured to; absent means the run's as-of date.

    Examples
    --------
    Tax a long-term gain at the long rate::

        node = LiquidationTax("tax", {
            "short_term_rate": 0.4, "long_term_rate": 0.2, "long_term_days": 365,
            "default_holding": "long", "loss_offset": False,
            "value_field": "market_value", "basis_field": "cost_basis",
            "acquired_field": "acquired"})
        out = node.run(ctx, {"records": [
            {"market_value": 2500.0, "cost_basis": 2000.0, "acquired": "2024-01-01"}]})
        out["records"][0]["liq_tax"]   # 100.0 when ctx.asof is 2026-10-27
    """

    role = "transform"
    outputs = ("records", "summary")

    _PARAMS = (
        "as_of", "short_term_rate", "long_term_rate", "long_term_days",
        "default_holding", "loss_offset", "value_field", "basis_field", "acquired_field",
    )
    _REQUIRED = tuple(name for name in _PARAMS if name not in ("as_of", "acquired_field"))

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        if "as_of" in params and date_problem(params["as_of"]):
            problems.append(f"as_of must be an ISO date, got {params['as_of']!r}")
        for name in ("short_term_rate", "long_term_rate"):
            if name in params:
                check_number_param(problems, name, params[name], lower=0.0, upper=1.0)
        if "long_term_days" in params:
            check_int_param(problems, "long_term_days", params["long_term_days"], ge=1)
        if "default_holding" in params and params["default_holding"] not in HOLDINGS:
            problems.append(
                f"default_holding must be one of {list(HOLDINGS)}, got {params['default_holding']!r}"
            )
        if "loss_offset" in params and not isinstance(params["loss_offset"], bool):
            problems.append(f"loss_offset must be a bool, got {params['loss_offset']!r}")
        check_name_params(problems, params, ("value_field", "basis_field"))
        if params.get("acquired_field") is not None and not name_ok(params["acquired_field"]):
            problems.append(
                f"acquired_field must be a non-empty field name, got {params['acquired_field']!r}"
            )
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _as_of(self, ctx):
        """Return the liquidation date: the param, else the run's as-of."""
        value = self.params.get("as_of", ctx.asof)
        if date_problem(value):
            raise ValueError(f"{self.key}: no usable as_of date (param or run), got {value!r}")
        return date.fromisoformat(value)

    def _holding(self, index, row, as_of):
        """Return the holding class of one row, from its acquisition date or the default."""
        field = self.params.get("acquired_field")
        acquired = row.get(field) if field is not None else None
        if acquired is None:
            return self.params["default_holding"]
        if date_problem(acquired):
            raise ValueError(
                f"{self.key}: row {index} field {field!r} is not an ISO date, got {acquired!r}"
            )
        held = (as_of - date.fromisoformat(acquired)).days
        if held < 0:
            raise ValueError(
                f"{self.key}: row {index} field {field!r} is dated {acquired}, after the "
                f"as_of {as_of.isoformat()}; a position cannot be acquired after its liquidation"
            )
        return "long" if held >= self.params["long_term_days"] else "short"

    def run(self, ctx, inputs):
        """Liquidate every row; return the rows with the six ``liq_*`` fields and a summary."""
        records = inputs.get("records")
        require_rows(self.key, records, _LIQ_FIELDS)
        as_of = self._as_of(ctx)
        rates = {"long": self.params["long_term_rate"], "short": self.params["short_term_rate"]}
        out, total_tax, total_net, by_holding = [], 0.0, 0.0, {"long": 0, "short": 0}
        for index, row in enumerate(records):
            proceeds = require_amount(self.key, index, row, self.params["value_field"],
                                      allow_zero=False)
            basis = require_amount(self.key, index, row, self.params["basis_field"],
                                   allow_zero=True)
            holding = self._holding(index, row, as_of)
            gain = proceeds - basis
            taxable = gain if self.params["loss_offset"] else max(gain, 0.0)
            tax = taxable * rates[holding]
            net = proceeds - tax
            out.append({**row, "liq_proceeds": proceeds, "liq_gain": gain,
                        "liq_holding": holding, "liq_rate": rates[holding],
                        "liq_tax": tax, "liq_net": net})
            total_tax += tax
            total_net += net
            by_holding[holding] += 1
        self.log.info("%s: %d row(s), tax %.2f, net %.2f", self.key, len(out), total_tax, total_net)
        return {"records": out, "summary": {
            "stage": "liquidation tax", "as_of": as_of.isoformat(), "rows": len(out),
            "tax": total_tax, "net": total_net, "holdings": by_holding}}


class HoldVsRedeploy(Node):
    """Terminal value of holding the gross position vs redeploying the net.

    Role ``transform``. See the module docstring for the rule. A row whose
    ``value_field`` or ``net_field`` is at or below zero refuses by index and
    field, as does a wired rate outside :data:`RATE_BOUNDS`.

    Parameters
    ----------
    params : dict
        ``horizon_years`` (int in ``[1, 100]``, REQUIRED); ``hold_return`` (a number in
        ``[-1, 2]`` or ``{symbol: number}``): the annual return of holding,
        per symbol when a mapping (a row whose symbol is absent refuses);
        required unless ``hold_returns`` is wired; ``redeploy_return``
        (number in ``[-1, 2]``, or ``null``): the annual return of the alternative,
        ``null`` declaring that there is none (every row holds, source ``none``);
        required unless the ``redeploy_return`` input is wired;
        ``redeploy_cost`` / ``risk_premium`` (numbers in ``[0, 2]``,
        REQUIRED): its annual cost and the premium charged for the
        forecast's own uncertainty (neither is a credit); ``value_field`` / ``net_field`` /
        ``symbol_field`` (field names, REQUIRED); ``terminal_tax_rate``
        (number in ``[0, 1]``) and ``basis_field`` (field name), together or
        not at all: the hold path's tax on its gain at the horizon;
        ``redeploy_tax_rate`` (number in ``[0, 1]``) and
        ``redeploy_tax_timing`` (``annual`` | ``terminal``), together or not
        at all: the redeploy path's tax and when it is paid.

    Inputs
    ------
    ``records`` (REQUIRED)
        Mapping rows carrying the declared fields.
    ``hold_returns`` (optional)
        ``{symbol: rate}``; when wired it replaces the ``hold_return`` param
        for every row, and a row whose symbol is absent refuses.
    ``redeploy_return`` (optional)
        A number that replaces the ``redeploy_return`` param, or ``None``:
        no admissible alternative, every row holds.

    Examples
    --------
    Decide one position over two years::

        node = HoldVsRedeploy("decide", {
            "horizon_years": 2, "hold_return": 0.07, "redeploy_return": 0.15,
            "redeploy_cost": 0.02, "risk_premium": 0.03, "value_field": "market_value",
            "net_field": "liq_net", "symbol_field": "symbol"})
        out = node.run(ctx, {"records": [
            {"symbol": "A", "market_value": 2500.0, "liq_net": 2400.0}]})
        out["records"][0]["decision"]   # "exit": 2904.0 beats 2862.25
    """

    role = "transform"
    outputs = ("records", "summary")

    _PARAMS = (
        "horizon_years", "hold_return", "redeploy_return", "redeploy_cost",
        "risk_premium", "value_field", "net_field", "symbol_field",
        "terminal_tax_rate", "basis_field", "redeploy_tax_rate", "redeploy_tax_timing",
    )
    _REQUIRED = tuple(name for name in _PARAMS if name not in _DECISION_OPTIONAL)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        if "horizon_years" in params:
            check_horizon_param(problems, "horizon_years", params["horizon_years"])
        hold = params.get("hold_return")
        if "hold_return" in params:
            if isinstance(hold, dict):
                if not hold or not all(name_ok(k) for k in hold):
                    problems.append("hold_return mapping must be {symbol: number}, non-empty")
                for symbol, value in hold.items():
                    check_rate_param(problems, f"hold_return[{symbol!r}]", value)
            else:
                check_rate_param(problems, "hold_return", hold)
        # null declares no alternative (every row holds); anything else keeps the rate bounds
        if params.get("redeploy_return") is not None:
            check_rate_param(problems, "redeploy_return", params["redeploy_return"])
        check_redeploy_params(problems, params)
        check_name_params(problems, params,
                          ("value_field", "net_field", "symbol_field", "basis_field"))
        if ("terminal_tax_rate" in params) != ("basis_field" in params):
            problems.append("terminal_tax_rate and basis_field are declared together or not at all")
        if "terminal_tax_rate" in params:
            check_number_param(problems, "terminal_tax_rate", params["terminal_tax_rate"],
                               lower=0.0, upper=1.0)
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _hold_rate(self, index, row, hold):
        """Return the annual hold return of one row: the scalar, or the symbol's entry."""
        if not isinstance(hold, dict):
            return float(hold)
        symbol = row.get(self.params["symbol_field"])
        if symbol not in hold:
            raise ValueError(
                f"{self.key}: row {index} has no hold_return entry for symbol {symbol!r}"
            )
        return float(hold[symbol])

    def _hold_source(self, inputs):
        """Return ``(rates, source)``: the wired ``hold_returns`` when present, else the param."""
        if "hold_returns" not in inputs:
            if "hold_return" not in self.params:
                raise ValueError(
                    f"{self.key}: no hold_return param and no hold_returns input"
                )
            return self.params["hold_return"], "param"
        wired = inputs["hold_returns"]
        if (
            not isinstance(wired, dict)
            or not all(isinstance(k, str) and number_ok(v) for k, v in wired.items())
        ):
            raise ValueError(
                f"{self.key}: hold_returns must be a {{symbol: number}} mapping, got {wired!r}"
            )
        for symbol, value in wired.items():
            require_rate(self.key, f"hold_returns[{symbol!r}]", value)
        return wired, "wired"

    def _redeploy_source(self, inputs):
        """Return ``(annual rate or None, source)`` for the redeploy path.

        The source is ``wired`` or ``inadmissible`` (a ``None`` input) when the port is wired,
        else ``param``, or ``none`` for a ``null`` param: no alternative declared.
        """
        if "redeploy_return" not in inputs:
            if "redeploy_return" not in self.params:
                raise ValueError(
                    f"{self.key}: no redeploy_return param and no redeploy_return input"
                )
            if self.params["redeploy_return"] is None:
                return None, "none"
            return self.params["redeploy_return"], "param"
        wired = inputs["redeploy_return"]
        if wired is None:
            return None, "inadmissible"
        if not number_ok(wired):
            raise ValueError(
                f"{self.key}: redeploy_return input must be a number or None, got {wired!r}"
            )
        return require_rate(self.key, "redeploy_return input", wired), "wired"

    def _redeploy_rate(self, redeploy_return):
        """Return the annual rate the redeploy path compounds at (see the module docstring)."""
        rate = redeploy_return - self.params["redeploy_cost"]
        if self.params.get("redeploy_tax_timing") == "annual":
            rate -= max(rate, 0.0) * self.params["redeploy_tax_rate"]
        return rate - self.params["risk_premium"]

    def _redeploy_path(self, net, rate, years):
        """Return ``(value, terminal_tax, net)`` of redeploying ``net`` at ``rate`` under the declared tax."""
        value = net * _growth(self.key, "redeploy", rate, years)
        tax = 0.0
        if self.params.get("redeploy_tax_timing") == "terminal":
            tax = max(value - net, 0.0) * self.params["redeploy_tax_rate"]
        return value, tax, value - tax

    def run(self, ctx, inputs):
        """Compare the two paths on every row; return the rows with the decision and a summary."""
        records = inputs.get("records")
        require_rows(self.key, records, DECISION_FIELDS)
        hold, hold_source = self._hold_source(inputs)
        redeploy_return, redeploy_source = self._redeploy_source(inputs)
        years = int(self.params["horizon_years"])
        basis_field = self.params.get("basis_field")
        terminal_rate = self.params.get("terminal_tax_rate")
        redeploy_rate = None
        if redeploy_return is not None:
            redeploy_rate = self._redeploy_rate(redeploy_return)
        out, hold_total, hold_net_total = [], 0.0, 0.0
        redeploy_total = redeploy_net_total = 0.0 if redeploy_rate is not None else None
        decisions = {"hold": 0, "exit": 0}
        for index, row in enumerate(records):
            value = require_amount(self.key, index, row, self.params["value_field"],
                                   allow_zero=False)
            net = require_amount(self.key, index, row, self.params["net_field"], allow_zero=False)
            hold_value = value * _growth(self.key, f"row {index} hold",
                                         self._hold_rate(index, row, hold), years)
            hold_tax = 0.0
            if basis_field:
                basis = require_number(self.key, "row", index, row, basis_field)
                hold_tax = max(hold_value - basis, 0.0) * terminal_rate
            hold_net = hold_value - hold_tax
            if redeploy_rate is None:
                redeploy_value = redeploy_tax = redeploy_net = advantage = None
                decision = "hold"
            else:
                redeploy_value, redeploy_tax, redeploy_net = self._redeploy_path(
                    net, redeploy_rate, years)
                advantage = redeploy_net - hold_net
                decision = "exit" if advantage > 0.0 else "hold"
                redeploy_total += redeploy_value
                redeploy_net_total += redeploy_net
            out.append({**row, "horizon_years": years, "hold_value": hold_value,
                        "hold_terminal_tax": hold_tax,
                        "hold_net": hold_net, "redeploy_value": redeploy_value,
                        "redeploy_terminal_tax": redeploy_tax, "redeploy_net": redeploy_net,
                        "advantage": advantage, "decision": decision})
            hold_total += hold_value
            hold_net_total += hold_net
            decisions[decision] += 1
        self.log.info("%s: %d row(s), %s", self.key, len(out), decisions)
        return {"records": out, "summary": {
            "stage": "hold vs redeploy", "rows": len(out), "decisions": decisions,
            "hold_value": hold_total, "hold_net": hold_net_total,
            "redeploy_value": redeploy_total, "redeploy_net": redeploy_net_total,
            "redeploy_rate": redeploy_rate, "horizon_years": years,
            "hold_return_source": hold_source, "redeploy_return_source": redeploy_source,
            "terminal_tax_rate": terminal_rate,
            "redeploy_tax_rate": self.params.get("redeploy_tax_rate"),
            "redeploy_tax_timing": self.params.get("redeploy_tax_timing")}}

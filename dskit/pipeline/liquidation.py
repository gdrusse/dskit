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

:class:`HoldVsRedeploy` (role ``transform``) compares two terminal values over
``horizon_years``: holding the gross position at ``hold_return`` (a number, or
one per symbol) against redeploying the after-tax net at ``redeploy_return``
less ``redeploy_cost`` less ``risk_premium``. ``decision`` is ``exit`` only when
the redeploy path is strictly ahead; a tie holds. Two optional inputs replace
the matching params so estimates are wired, never pasted: ``hold_returns``
(``{symbol: rate}``, from :mod:`dskit.pipeline.hold_return`) and
``redeploy_return`` (a number from :mod:`dskit.pipeline.active_hurdle`, or
``None`` when that hurdle was inadmissible, which holds every row). When
``terminal_tax_rate`` and ``basis_field`` are declared, both paths are
liquidated at the horizon and taxed on their gain — the hold path over the
original basis, the redeploy path over the after-tax net it started from —
so the comparison is net to net; without them the terminal tax is zero on
both sides, which favours holding.

Rows that cannot be valued refuse by index and field name; nothing is filled.
Every rate, horizon and field name is a param: the module carries no table.

Import cost: stdlib only.
"""

from __future__ import annotations

import math
from datetime import date

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["HOLDINGS", "HoldVsRedeploy", "LiquidationTax"]

#: The two holding classes a tax schedule distinguishes.
HOLDINGS = ("long", "short")

_LIQ_FIELDS = ("liq_proceeds", "liq_gain", "liq_holding", "liq_rate", "liq_tax", "liq_net")
_DECISION_FIELDS = (
    "horizon_years", "hold_value", "hold_terminal_tax", "hold_net", "redeploy_value",
    "redeploy_terminal_tax", "redeploy_net", "advantage", "decision",
)
#: HoldVsRedeploy params that may be absent: the two a wire can replace and the terminal-tax pair.
_DECISION_OPTIONAL = ("hold_return", "redeploy_return", "terminal_tax_rate", "basis_field")


def _name_ok(value):
    return isinstance(value, str) and bool(value.strip())


def _iso_date(value):
    """Return the date a strict ISO ``YYYY-MM-DD`` string names, else ``None``."""
    if not _name_ok(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_rate(problems, name, value, *, lower, upper):
    """Append a problem unless ``value`` is a number in ``[lower, upper]``."""
    if not number_ok(value) or not lower <= value <= upper:
        problems.append(f"{name} must be a number in [{lower}, {upper}], got {value!r}")


def _check_names(problems, params, names):
    """Append a problem for every field-name param that is not a name."""
    for name in names:
        if not _name_ok(params.get(name)):
            problems.append(f"{name} must be a non-empty field name, got {params.get(name)!r}")


def _check_rows(key, records, outputs):
    """Refuse a non-list, a non-mapping row, or a row already carrying an output."""
    if not isinstance(records, list):
        raise ValueError(f"{key}: records must be a list, got {type(records).__name__}")
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise ValueError(f"{key}: row {index} must be a mapping, got {type(row).__name__}")
        taken = sorted(set(row) & set(outputs))
        if taken:
            raise ValueError(f"{key}: row {index} already carries {taken}")


def _number(key, index, row, field):
    """Return the number ``row[field]`` holds; refuse by index and field otherwise."""
    value = row.get(field)
    if not number_ok(value):
        raise ValueError(f"{key}: row {index} field {field!r} is not a number, got {value!r}")
    return float(value)


class LiquidationTax(Node):
    """Per-row liquidation: proceeds, gain, holding class, rate, tax, net.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``as_of`` (ISO date, REQUIRED): the liquidation date the holding
        period is measured to; ``short_term_rate`` / ``long_term_rate``
        (numbers in ``[0, 1]``, REQUIRED); ``long_term_days`` (int >= 1,
        REQUIRED): days held at which a position is ``long``;
        ``default_holding`` (``long`` | ``short``, REQUIRED): the class of a
        row with no acquisition date; ``loss_offset`` (bool, REQUIRED):
        whether a loss is a negative tax; ``value_field`` / ``basis_field``
        (field names, REQUIRED); ``acquired_field`` (field name, optional):
        the ISO date the position was acquired, absent or null on a row
        means ``default_holding``.

    Examples
    --------
    Tax a long-term gain at the long rate::

        node = LiquidationTax("tax", {
            "as_of": "2026-10-27", "short_term_rate": 0.4, "long_term_rate": 0.2,
            "long_term_days": 365, "default_holding": "long", "loss_offset": False,
            "value_field": "market_value", "basis_field": "cost_basis",
            "acquired_field": "acquired"})
        out = node.run(ctx, {"records": [
            {"market_value": 2500.0, "cost_basis": 2000.0, "acquired": "2024-01-01"}]})
        out["records"][0]["liq_tax"]   # 100.0
    """

    role = "transform"
    outputs = ("records", "summary")

    _PARAMS = (
        "as_of", "short_term_rate", "long_term_rate", "long_term_days",
        "default_holding", "loss_offset", "value_field", "basis_field", "acquired_field",
    )
    _REQUIRED = tuple(name for name in _PARAMS if name != "acquired_field")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        if "as_of" in params and _iso_date(params["as_of"]) is None:
            problems.append(f"as_of must be an ISO date, got {params['as_of']!r}")
        for name in ("short_term_rate", "long_term_rate"):
            if name in params:
                _check_rate(problems, name, params[name], lower=0.0, upper=1.0)
        if "long_term_days" in params:
            check_int_param(problems, "long_term_days", params["long_term_days"], ge=1)
        if "default_holding" in params and params["default_holding"] not in HOLDINGS:
            problems.append(
                f"default_holding must be one of {list(HOLDINGS)}, got {params['default_holding']!r}"
            )
        if "loss_offset" in params and not isinstance(params["loss_offset"], bool):
            problems.append(f"loss_offset must be a bool, got {params['loss_offset']!r}")
        _check_names(problems, params, [n for n in ("value_field", "basis_field") if n in params])
        if params.get("acquired_field") is not None and not _name_ok(params["acquired_field"]):
            problems.append(
                f"acquired_field must be a non-empty field name, got {params['acquired_field']!r}"
            )
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _holding(self, index, row, as_of):
        """Return the holding class of one row, from its acquisition date or the default."""
        field = self.params.get("acquired_field")
        acquired = row.get(field) if field is not None else None
        if acquired is None:
            return self.params["default_holding"]
        day = _iso_date(acquired)
        if day is None:
            raise ValueError(
                f"{self.key}: row {index} field {field!r} is not an ISO date, got {acquired!r}"
            )
        held = (as_of - day).days
        return "long" if held >= self.params["long_term_days"] else "short"

    def run(self, ctx, inputs):
        """Liquidate every row; return the rows with the six ``liq_*`` fields and a summary."""
        records = inputs.get("records")
        _check_rows(self.key, records, _LIQ_FIELDS)
        as_of = _iso_date(self.params["as_of"])
        rates = {"long": self.params["long_term_rate"], "short": self.params["short_term_rate"]}
        out, total_tax, total_net, by_holding = [], 0.0, 0.0, {"long": 0, "short": 0}
        for index, row in enumerate(records):
            proceeds = _number(self.key, index, row, self.params["value_field"])
            basis = _number(self.key, index, row, self.params["basis_field"])
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
            "stage": "liquidation tax", "rows": len(out), "tax": total_tax,
            "net": total_net, "holdings": by_holding}}


class HoldVsRedeploy(Node):
    """Terminal value of holding the gross position vs redeploying the net.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``horizon_years`` (int >= 1, REQUIRED); ``hold_return`` (a number in
        ``[-1, 10]`` or ``{symbol: number}``): the annual return of holding,
        per symbol when a mapping (a row whose symbol is absent refuses);
        required unless ``hold_returns`` is wired; ``redeploy_return``
        (number in ``[-1, 10]``): the annual return of the alternative;
        required unless the ``redeploy_return`` input is wired;
        ``redeploy_cost`` / ``risk_premium`` (numbers in ``[-1, 10]``,
        REQUIRED): its annual cost and the premium charged for the
        forecast's own uncertainty; ``value_field`` / ``net_field`` /
        ``symbol_field`` (field names, REQUIRED); ``terminal_tax_rate``
        (number in ``[0, 1]``) and ``basis_field`` (field name), together or
        not at all: the tax on each path's gain at the horizon.

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
        "terminal_tax_rate", "basis_field",
    )
    _REQUIRED = tuple(name for name in _PARAMS if name not in _DECISION_OPTIONAL)
    _RATE_LOWER, _RATE_UPPER = -1.0, 10.0

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        if "horizon_years" in params:
            check_int_param(problems, "horizon_years", params["horizon_years"], ge=1)
        hold = params.get("hold_return")
        if "hold_return" in params:
            if isinstance(hold, dict):
                if not hold or not all(_name_ok(k) for k in hold):
                    problems.append("hold_return mapping must be {symbol: number}, non-empty")
                for symbol, value in hold.items():
                    _check_rate(problems, f"hold_return[{symbol!r}]", value,
                                lower=cls._RATE_LOWER, upper=cls._RATE_UPPER)
            else:
                _check_rate(problems, "hold_return", hold,
                            lower=cls._RATE_LOWER, upper=cls._RATE_UPPER)
        for name in ("redeploy_return", "redeploy_cost", "risk_premium"):
            if name in params:
                _check_rate(problems, name, params[name],
                            lower=cls._RATE_LOWER, upper=cls._RATE_UPPER)
        _check_names(problems, params,
                     [n for n in ("value_field", "net_field", "symbol_field", "basis_field")
                      if n in params])
        if ("terminal_tax_rate" in params) != ("basis_field" in params):
            problems.append("terminal_tax_rate and basis_field are declared together or not at all")
        if "terminal_tax_rate" in params:
            _check_rate(problems, "terminal_tax_rate", params["terminal_tax_rate"],
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
        return wired, "wired"

    def _redeploy_source(self, inputs):
        """Return ``(annual rate or None, source)`` for the redeploy path."""
        if "redeploy_return" not in inputs:
            if "redeploy_return" not in self.params:
                raise ValueError(
                    f"{self.key}: no redeploy_return param and no redeploy_return input"
                )
            return self.params["redeploy_return"], "param"
        wired = inputs["redeploy_return"]
        if wired is None:
            return None, "inadmissible"
        if not number_ok(wired):
            raise ValueError(f"{self.key}: redeploy_return input must be a number or None, got {wired!r}")
        return float(wired), "wired"

    def _terminal_tax(self, terminal, basis):
        """Return the tax on a path's gain at the horizon; a loss pays none."""
        rate = self.params.get("terminal_tax_rate")
        if rate is None:
            return 0.0
        return max(terminal - basis, 0.0) * rate

    def run(self, ctx, inputs):
        """Compare the two paths on every row; return the rows with the decision and a summary."""
        records = inputs.get("records")
        _check_rows(self.key, records, _DECISION_FIELDS)
        hold, hold_source = self._hold_source(inputs)
        redeploy_return, redeploy_source = self._redeploy_source(inputs)
        years = self.params["horizon_years"]
        basis_field = self.params.get("basis_field")
        redeploy_rate = None
        if redeploy_return is not None:
            redeploy_rate = (redeploy_return - self.params["redeploy_cost"]
                             - self.params["risk_premium"])
        out, hold_total, hold_net_total = [], 0.0, 0.0
        redeploy_total = redeploy_net_total = 0.0 if redeploy_rate is not None else None
        decisions = {"hold": 0, "exit": 0}
        for index, row in enumerate(records):
            value = _number(self.key, index, row, self.params["value_field"])
            net = _number(self.key, index, row, self.params["net_field"])
            basis = _number(self.key, index, row, basis_field) if basis_field else None
            hold_value = value * math.pow(1.0 + self._hold_rate(index, row, hold), years)
            hold_tax = self._terminal_tax(hold_value, basis) if basis_field else 0.0
            hold_net = hold_value - hold_tax
            if redeploy_rate is None:
                redeploy_value = redeploy_tax = redeploy_net = advantage = None
                decision = "hold"
            else:
                redeploy_value = net * math.pow(1.0 + redeploy_rate, years)
                redeploy_tax = self._terminal_tax(redeploy_value, net) if basis_field else 0.0
                redeploy_net = redeploy_value - redeploy_tax
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
            "terminal_tax_rate": self.params.get("terminal_tax_rate")}}

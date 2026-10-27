"""A decision run reduced to a few numbers per position and for the portfolio (ADR-0279, A0002).

:class:`~dskit.pipeline.liquidation.HoldVsRedeploy` answers hold or exit with two
terminal values. An owner reading a portfolio wants the same answer as a handful of
rates and shares: what each holding is expected to return, how risky it is, how much
of it is locked in by tax, and what holding it costs against the alternative. Every
number here is computed from those decision rows (plus two optional per-symbol
tables); nothing is re-estimated.

Per position, with ``v`` the value, ``b`` the basis, ``N`` the after-tax proceeds of
selling today, ``T`` the hold path's after-tax terminal value (``hold_net``) and ``H``
the horizon in years:

    weight            = v / sum(v)
    expected_return   = (hold_value / v) ** (1 / H) - 1          pre-tax, total, a year
    volatility, beta  = the wired tables' entries (None when not wired)
    embedded_gain     = (v - b) / v                               unrealized gain, a share
    tax_cost          = (v - N) / v                               lost to tax if sold today
    break_even_return = the redeploy_return at which redeploy_net == T
    opportunity_cost  = (redeploy_net / T) ** (1 / H) - 1          after tax, a year

``opportunity_cost`` above zero means the alternative wins (``decision`` is ``exit``),
below zero that holding wins; it is ``None`` when the hurdle was inadmissible (the rows carry
no redeploy value). The break-even return inverts the redeploy path under the declared tax
terms, which this node restates with :class:`HoldVsRedeploy`'s names and validator: one
:class:`BreakEvenRule` per ``redeploy_tax_timing`` (:data:`BREAK_EVEN_RULES`). With
``g = (T / N) ** (1 / H) - 1``, cost ``c``, premium ``p`` and tax rate ``t``:

* untaxed: ``R = g + c + p``;
* ``annual`` (the positive net rate is taxed each year, a loss gets no refund):
  ``y = g + p``; ``R = c + y / (1 - t)`` when ``y > 0``, else ``R = c + y``;
* ``terminal`` (the gain over ``N`` is taxed once at the horizon): ``G = (T/N - t) / (1 -
  t)`` when ``T > N``, else ``G = T / N``; ``R = G ** (1 / H) - 1 + c + p``.

With ``t = 1`` and a gain needed no return reaches ``T`` and the row refuses; so does a
break-even outside :data:`~dskit.pipeline.liquidation.RATE_BOUNDS`. Because the redeploy
value rises with the return, an active hurdle above the break-even is exactly an
``exit``.

The portfolio (output ``metrics``, so ``pipeline runs`` tabulates it): total value; the
value-weighted expected return, volatility and beta; concentration by SYMBOL (one stock
held in two accounts is one exposure): ``hhi = sum(w ** 2)``, its inverse
``effective_holdings``, ``top_symbol`` / ``top_weight``; embedded gain and tax cost on the
totals; break-even and opportunity cost from ``sum(N)``, ``sum(T)``, ``sum(redeploy_net)``
(exact, since every row of one run shares one redeploy rate), so the portfolio's
``opportunity_cost`` compares selling everything with holding everything, not the best mix of
the two; exit and hold counts.

Two approximations are disclosed rather than modelled. The weighted volatility is an
UPPER BOUND on the portfolio's (equal only when every pair is perfectly correlated); a
covariance needs a joint return panel. The portfolio's embedded gain NETS losses (a loss in
one holding reduces it) but its tax cost is the sum of each row's ``v - N``, which nets them
only when the liquidation did (``loss_offset`` makes a loss a negative tax: its ``N``
exceeds ``v`` and its tax cost is negative); when it did not, one position's loss does not
reduce another's gain, which overstates the cost of selling everything.

Rows refuse by index and field through :mod:`~dskit.pipeline.liquidation`'s readers.
A wired volatility must lie in :data:`VOLATILITY_BOUNDS` (an annual standard deviation
as a fraction: 20 for 20% refuses), a beta is any finite number (a negative beta is legal),
and a symbol missing from a wired table refuses.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import NamedTuple

from dskit.pipeline.binary_curve import fields_key, name_ok
from dskit.pipeline.decision_outcomes import annualized_growth, check_key_fields_param
from dskit.pipeline.liquidation import (
    MAX_HORIZON_YEARS, check_name_params, check_redeploy_params, require_amount,
    require_number, require_rate, require_rows,
)
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = [
    "BREAK_EVEN_RULES", "SUMMARY_FIELDS", "VOLATILITY_BOUNDS", "AnnualBreakEven",
    "BreakEvenRule", "PositionSummary", "TerminalBreakEven", "UntaxedBreakEven",
]

#: The fields of a :class:`~dskit.pipeline.liquidation.HoldVsRedeploy` row this node reads.
_HORIZON, _HOLD_VALUE, _HOLD_NET = "horizon_years", "hold_value", "hold_net"
_REDEPLOY_NET, _DECISION = "redeploy_net", "decision"
#: The decisions a row may carry: keep the position, or sell it.
_HOLD, _EXIT = "hold", "exit"
_DECISIONS = (_HOLD, _EXIT)

#: The numbers each output row carries after its key fields, in order.
SUMMARY_FIELDS = (
    "value", "weight", _HORIZON, "expected_return", "volatility", "beta", "embedded_gain",
    "tax_cost", "break_even_return", "opportunity_cost", _DECISION,
)
#: Inclusive bounds of a wired annual volatility: a fraction, 0 (cash) to 1000%.
VOLATILITY_BOUNDS = (0.0, 10.0)

_HOLD_NET_WHY = "a hold path worth nothing has no growth to break even against"

#: The two optional per-symbol input tables.
_VOLATILITIES, _BETAS = "volatilities", "betas"


class _TableRule(NamedTuple):
    """One wired table's check: inclusive ``bounds`` (None: any finite number) and ``why``."""

    bounds: tuple | None
    why: str


#: One rule per optional table; a beta, unlike a volatility, has no bound (an inverse fund is negative).
_TABLE_RULES = {
    _VOLATILITIES: _TableRule(
        VOLATILITY_BOUNDS, "an annual standard deviation is a fraction (0.2 is 20%), not percent"),
    _BETAS: _TableRule(None, ""),
}
#: The params that may be absent: the redeploy tax pair, declared together or not at all.
_OPTIONAL_PARAMS = ("redeploy_tax_rate", "redeploy_tax_timing")


class BreakEvenRule(ABC):
    """The redeploy return at which the redeploy path ends where the hold path does.

    Parameters
    ----------
    cost, premium : float
        The declared ``redeploy_cost`` and ``risk_premium``.
    tax_rate : float
        The declared ``redeploy_tax_rate`` (ignored by :class:`UntaxedBreakEven`).

    Examples
    --------
    Grow 1000 after tax to 1210 over two years, untaxed, at no cost::

        UntaxedBreakEven(0.0, 0.0, 0.0).rate(1.21, 2)   # 0.1
    """

    def __init__(self, cost, premium, tax_rate):
        self.cost = float(cost)
        self.premium = float(premium)
        self.tax_rate = float(tax_rate)

    @abstractmethod
    def rate(self, ratio, years):
        """Return the break-even annual return for ``ratio = hold_net / net``, or None if none exists."""


class UntaxedBreakEven(BreakEvenRule):
    """No redeploy tax declared: ``R = g + cost + premium``."""

    def rate(self, ratio, years):
        """Return the untaxed break-even (see the module docstring)."""
        return annualized_growth(ratio, years) + self.cost + self.premium


class AnnualBreakEven(BreakEvenRule):
    """Tax on the positive net rate every year: ``R = cost + (g + premium) / (1 - t)``, piecewise."""

    def rate(self, ratio, years):
        """Return the ``annual`` break-even, or None when the whole of a needed gain is taxed."""
        needed = annualized_growth(ratio, years) + self.premium
        if needed <= 0.0:
            return self.cost + needed
        if self.tax_rate >= 1.0:
            return None
        return self.cost + needed / (1.0 - self.tax_rate)


class TerminalBreakEven(BreakEvenRule):
    """Tax once on the gain at the horizon: solve ``G - max(G - 1, 0) x t = T / N`` for ``G``."""

    def rate(self, ratio, years):
        """Return the ``terminal`` break-even, or None when the whole of a needed gain is taxed."""
        growth = ratio
        if ratio > 1.0:
            if self.tax_rate >= 1.0:
                return None
            growth = (ratio - self.tax_rate) / (1.0 - self.tax_rate)
        return annualized_growth(growth, years) + self.cost + self.premium


#: One rule per ``redeploy_tax_timing``; ``None`` is a redeploy path with no tax declared.
BREAK_EVEN_RULES = {None: UntaxedBreakEven, "annual": AnnualBreakEven,
                    "terminal": TerminalBreakEven}


class PositionSummary(Node):
    """Per-position and portfolio numbers from a decision run's rows.

    Role ``transform``. See the module docstring for every formula.

    Parameters
    ----------
    params : dict
        ``key_fields`` (non-empty list of distinct field names, REQUIRED): what
        identifies a position (account and symbol, say), copied onto each output row;
        ``symbol_field`` (one of ``key_fields``, REQUIRED): what concentration and the
        wired tables are keyed by; ``value_field``, ``net_field``, ``basis_field`` (field
        names, REQUIRED): the value today, the after-tax proceeds of selling it and its
        cost basis; ``redeploy_cost``, ``risk_premium`` (REQUIRED) and
        ``redeploy_tax_rate`` + ``redeploy_tax_timing`` (together or not at all): the
        decision node's own terms, validated by
        :func:`~dskit.pipeline.liquidation.check_redeploy_params`.

    Inputs
    ------
    ``records`` (REQUIRED)
        :class:`~dskit.pipeline.liquidation.HoldVsRedeploy` rows on one horizon, one
        per key.
    ``volatilities`` (optional)
        ``{symbol: annual standard deviation}``, fractions in :data:`VOLATILITY_BOUNDS`.
    ``betas`` (optional)
        ``{symbol: beta}``, as :class:`~dskit.pipeline.hold_return.BetaHoldReturns`
        wires them. Any finite number: unlike a volatility a beta has no bounds, and a
        negative one (an inverse fund) is legal.

    A table that is wired must be a mapping holding every row's symbol (``None`` and ``{}``
    refuse); only an absent input means unwired, and its column is ``None``.

    Outputs
    -------
    ``records``
        One row per position: the key fields, then :data:`SUMMARY_FIELDS`.
    ``metrics``
        The portfolio's numbers (see the module docstring).

    Examples
    --------
    Summarize a decision run::

        node = PositionSummary("summary", {"key_fields": ["account", "symbol"],
            "symbol_field": "symbol", "value_field": "market_value",
            "net_field": "liq_net", "basis_field": "cost_basis",
            "redeploy_cost": 0.02, "risk_premium": 0.0,
            "redeploy_tax_rate": 0.408, "redeploy_tax_timing": "annual"})
        out = node.run(ctx, {"records": decided, "betas": betas})
        out["metrics"]["opportunity_cost"], out["records"][0]["break_even_return"]
    """

    role = "transform"
    outputs = ("records", "metrics")

    _PARAMS = (
        "key_fields", "symbol_field", "value_field", "net_field", "basis_field",
        "redeploy_cost", "risk_premium", "redeploy_tax_rate", "redeploy_tax_timing",
    )
    _REQUIRED = tuple(name for name in _PARAMS if name not in _OPTIONAL_PARAMS)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        if check_key_fields_param(problems, params):
            keys = params["key_fields"]
            taken = sorted(set(keys) & set(SUMMARY_FIELDS))
            if taken:
                problems.append(f"key_fields may not name an output field: {taken}")
            symbol = params.get("symbol_field")
            if name_ok(symbol) and symbol not in keys:
                problems.append(f"symbol_field {symbol!r} must be one of key_fields {keys!r}")
        check_name_params(problems, params,
                          ("symbol_field", "value_field", "net_field", "basis_field"))
        check_redeploy_params(problems, params)
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _table(self, inputs, name):
        """Return the wired ``{symbol: number}`` table, or None when the input is absent.

        A wired input that is not a mapping (``None`` included) refuses, as does an entry that
        is not a number or misses the table's bounds.
        """
        if name not in inputs:
            return None
        table = inputs[name]
        if not isinstance(table, dict):
            raise ValueError(
                f"{self.key}: {name} must be a {{symbol: number}} mapping, got {table!r}"
            )
        rule = _TABLE_RULES[name]
        for symbol, value in table.items():
            if not number_ok(value):
                raise ValueError(f"{self.key}: {name}[{symbol!r}] must be a number, got {value!r}")
            if rule.bounds is not None and not rule.bounds[0] <= value <= rule.bounds[1]:
                raise ValueError(
                    f"{self.key}: {name}[{symbol!r}] {value!r} is outside "
                    f"[{rule.bounds[0]}, {rule.bounds[1]}]; {rule.why}"
                )
        return table

    def _lookup(self, table, name, index, symbol):
        """Return ``table[symbol]`` as a float, None when not wired; refuse a missing symbol."""
        if table is None:
            return None
        if symbol not in table:
            raise ValueError(f"{self.key}: row {index} has no {name} entry for symbol {symbol!r}")
        return float(table[symbol])

    def _rule(self):
        """Build the break-even rule the declared redeploy tax timing names."""
        p = self.params
        rule = BREAK_EVEN_RULES[p.get("redeploy_tax_timing")]
        return rule(p["redeploy_cost"], p["risk_premium"], p.get("redeploy_tax_rate", 0.0))

    def _break_even(self, rule, where, ratio, years):
        """Return the break-even return; refuse when none exists or it leaves RATE_BOUNDS."""
        rate = rule.rate(ratio, years)
        if rate is None:
            raise ValueError(
                f"{self.key}: {where}: no redeploy return reaches hold_net under "
                f"redeploy_tax_rate {rule.tax_rate!r}; the tax takes the whole of a needed gain"
            )
        return require_rate(self.key, f"{where} break-even return", rate)

    def _horizon(self, index, row):
        """Return the row's horizon as an int in ``[1, MAX_HORIZON_YEARS]``; refuse otherwise."""
        years = require_number(self.key, "row", index, row, _HORIZON)
        if not years.is_integer() or not 1 <= years <= MAX_HORIZON_YEARS:
            raise ValueError(
                f"{self.key}: row {index} field {_HORIZON!r} must be an int in "
                f"[1, {MAX_HORIZON_YEARS}], got {row[_HORIZON]!r}"
            )
        return int(years)

    def _redeploy_net(self, index, row):
        """Return the row's redeploy net, or None when the hurdle was inadmissible."""
        if row.get(_REDEPLOY_NET) is None:
            return None
        return require_amount(self.key, index, row, _REDEPLOY_NET, allow_zero=True)

    def _read(self, index, row):
        """Return one row's numbers, each refused by index and field."""
        p = self.params
        key, problem = fields_key(row, p["key_fields"])
        if problem is not None:
            raise ValueError(f"{self.key}: row {index} key {p['key_fields']!r} is unusable: {problem}")
        decision = row.get(_DECISION)
        read = {
            "key": key, "symbol": row[p["symbol_field"]],
            "value": require_amount(self.key, index, row, p["value_field"], allow_zero=False),
            "net": require_amount(self.key, index, row, p["net_field"], allow_zero=False),
            "basis": require_amount(self.key, index, row, p["basis_field"], allow_zero=True),
            "years": self._horizon(index, row),
            "hold_value": require_amount(self.key, index, row, _HOLD_VALUE, allow_zero=True),
            "hold_net": require_amount(self.key, index, row, _HOLD_NET, allow_zero=False,
                                       why=_HOLD_NET_WHY),
            "redeploy_net": self._redeploy_net(index, row),
        }
        if decision not in _DECISIONS:
            raise ValueError(
                f"{self.key}: row {index} field {_DECISION!r} must be one of "
                f"{list(_DECISIONS)}, got {decision!r}"
            )
        read["decision"] = decision
        return read

    def _rows(self, records):
        """Read every row; refuse a repeated key, several horizons or a mixed hurdle."""
        require_rows(self.key, records, ())
        if not records:
            raise ValueError(f"{self.key}: records carry no rows: there is no portfolio to summarize")
        reads, seen = [], {}
        for index, row in enumerate(records):
            read = self._read(index, row)
            if read["key"] in seen:
                raise ValueError(
                    f"{self.key}: rows {seen[read['key']]} and {index} carry the key {read['key']!r}"
                )
            seen[read["key"]] = index
            reads.append(read)
        horizons = sorted({read["years"] for read in reads})
        if len(horizons) != 1:
            raise ValueError(
                f"{self.key}: rows carry several horizons {horizons}; one summary is one horizon"
            )
        admissible = {read["redeploy_net"] is not None for read in reads}
        if len(admissible) != 1:
            raise ValueError(
                f"{self.key}: some rows carry a {_REDEPLOY_NET!r} and some None; one decision "
                "run has one hurdle, admissible or not"
            )
        return reads

    def run(self, ctx, inputs):
        """Return one summary row per position and the portfolio's numbers."""
        tables = {name: self._table(inputs, name) for name in _TABLE_RULES}
        reads = self._rows(inputs.get("records"))
        rule = self._rule()
        years = reads[0]["years"]
        total = sum(read["value"] for read in reads)
        out, by_symbol = [], {}
        for index, read in enumerate(reads):
            value, symbol = read["value"], read["symbol"]
            risk = {name: self._lookup(tables[name], name, index, symbol) for name in _TABLE_RULES}
            redeploy = read["redeploy_net"]
            out.append({
                **dict(zip(self.params["key_fields"], read["key"])),
                "value": value, "weight": value / total, _HORIZON: years,
                "expected_return": annualized_growth(read["hold_value"] / value, years),
                "volatility": risk[_VOLATILITIES], "beta": risk[_BETAS],
                "embedded_gain": (value - read["basis"]) / value,
                "tax_cost": (value - read["net"]) / value,
                "break_even_return": self._break_even(
                    rule, f"row {index}", read["hold_net"] / read["net"], years),
                "opportunity_cost": (None if redeploy is None
                                     else annualized_growth(redeploy / read["hold_net"], years)),
                _DECISION: read["decision"],
            })
            by_symbol[symbol] = by_symbol.get(symbol, 0.0) + value
        metrics = self._portfolio(reads, out, by_symbol, total, rule, years)
        self.log.info("%s: %d position(s), opportunity cost %s", self.key, len(out),
                      metrics["opportunity_cost"])
        return {"records": out, "metrics": metrics}

    def _portfolio(self, reads, out, by_symbol, total, rule, years):
        """Return the portfolio's numbers from the reads and the per-position rows."""
        def weighted(field):
            if out[0][field] is None:
                return None
            return sum(row["weight"] * row[field] for row in out)

        shares = {symbol: value / total for symbol, value in by_symbol.items()}
        top_symbol, top_weight = max(sorted(shares.items(), key=lambda kv: repr(kv[0])),
                                     key=lambda kv: kv[1])
        hhi = sum(share ** 2 for share in shares.values())
        net = sum(read["net"] for read in reads)
        hold_net = sum(read["hold_net"] for read in reads)
        redeploy = None
        if reads[0]["redeploy_net"] is not None:
            redeploy = annualized_growth(sum(read["redeploy_net"] for read in reads) / hold_net, years)
        exits = sum(1 for read in reads if read["decision"] == _EXIT)
        # No refusal can fire here that a row's did not: sum(T) / sum(N) lies between the rows'
        # ratios and every rule's break-even is non-decreasing in the ratio, so a portfolio of
        # rows that each passed passes too (in exact arithmetic). The call states that invariant
        # (a rule that broke it would refuse); it adds no condition of its own.
        break_even = self._break_even(rule, "portfolio", hold_net / net, years)
        return {
            "positions": len(out), "total_value": total, _HORIZON: years,
            "expected_return": weighted("expected_return"),
            "weighted_volatility": weighted("volatility"), "beta": weighted("beta"),
            "hhi": hhi, "effective_holdings": 1.0 / hhi,
            "top_symbol": top_symbol, "top_weight": top_weight,
            "embedded_gain": sum(read["value"] - read["basis"] for read in reads) / total,
            "tax_cost": (total - net) / total,
            "break_even_return": break_even,
            "opportunity_cost": redeploy,
            "exit_positions": exits, "hold_positions": len(out) - exits,
        }

"""Expected hold return from named building blocks, by import path (ADR-0279, research A0001).

A trailing average is the noisiest estimate of an expected return there is
(its standard error falls with calendar time, never with more samples), and
the realized premium of any one window carries that window's valuation change.
The building-block estimate is both more precise and explainable in one line:

    market rate = dividend yield + real growth + inflation + valuation drift,
    valuation drift = (fair CAPE / CAPE) ** (1 / reversion years) - 1

and a single holding is expected to pay what the market pays, scaled by how
much market risk it carries: ``risk_free + beta x (market - risk_free)``. No
holding gets a return of its own; a stock-specific expectation is a forecast
of alpha, which is the active side's claim, not the hold side's.

:class:`MarketHoldReturn` (role ``data``: no inputs, literal params, the
params ARE its fingerprint) emits the market rate and its blocks.
:class:`BetaHoldReturns` (role ``transform``) reads the symbols on a record
stream and a declared beta table and emits ``{symbol: rate}``, which
:class:`~dskit.pipeline.liquidation.HoldVsRedeploy` takes on its optional
``hold_returns`` input port. Every number is a param of the document.

The valuation drift is spread over ``reversion_years``, so the market rate is an
average for a horizon no longer than that: ``horizon_years`` (the decision's
horizon, pinned equal to the decision node's by the document's tests) may not
exceed ``reversion_years``, or the drift would compound past full reversion.
Every rate this module computes is held to :data:`~dskit.pipeline.liquidation.RATE_BOUNDS`.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

from dskit.pipeline.binary_curve import name_ok
from dskit.pipeline.liquidation import check_horizon_param, check_rate_param, require_rate
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["BetaHoldReturns", "MarketHoldReturn"]


class MarketHoldReturn(Node):
    """The market's expected annual nominal return from four named blocks.

    Role ``data``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``dividend_yield``, ``real_growth``, ``inflation`` (numbers in
        ``[-1, 10]``, REQUIRED); ``cape``, ``fair_cape`` (positive numbers,
        REQUIRED): the current and the fair cyclically adjusted P/E;
        ``reversion_years`` (int >= 1, REQUIRED): the years over which the
        valuation is assumed to drift to fair; ``horizon_years`` (int in
        ``[1, 100]``, REQUIRED): the decision horizon this rate serves, at most
        ``reversion_years``. The computed rate must lie in ``[-1, 10]``.

    Examples
    --------
    A market priced above fair value::

        node = MarketHoldReturn("market", {
            "dividend_yield": 0.013, "real_growth": 0.02, "inflation": 0.025,
            "cape": 36.0, "fair_cape": 27.0, "reversion_years": 10,
            "horizon_years": 5})
        node.run(ctx, {})["rate"]   # 0.058 - 0.0284 = 0.0296
    """

    role = "data"
    outputs = ("rate", "blocks", "summary")

    _PARAMS = ("dividend_yield", "real_growth", "inflation", "cape", "fair_cape",
               "reversion_years", "horizon_years")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._PARAMS) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        for name in ("dividend_yield", "real_growth", "inflation"):
            if name in params:
                check_rate_param(problems, name, params[name])
        for name in ("cape", "fair_cape"):
            if name in params and (not number_ok(params[name]) or params[name] <= 0.0):
                problems.append(f"{name} must be a positive number, got {params[name]!r}")
        reversion_problems = len(problems)
        if "reversion_years" in params:
            check_int_param(problems, "reversion_years", params["reversion_years"], ge=1)
        reversion_ok = "reversion_years" in params and len(problems) == reversion_problems
        horizon_problems = len(problems)
        if "horizon_years" in params:
            check_horizon_param(problems, "horizon_years", params["horizon_years"])
        horizon_ok = "horizon_years" in params and len(problems) == horizon_problems
        if reversion_ok and horizon_ok and params["horizon_years"] > params["reversion_years"]:
            problems.append(
                f"horizon_years ({params['horizon_years']}) exceeds reversion_years "
                f"({params['reversion_years']}): the valuation drift is spread over "
                "reversion_years; a horizon beyond it would compound it past full reversion"
            )
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def fingerprint(self):
        """Return the params: a literal source's identity is exactly what it declares."""
        return dict(self.params)

    def run(self, ctx, inputs):
        """Return the rate, its blocks and a summary."""
        p = self.params
        valuation = (p["fair_cape"] / p["cape"]) ** (1.0 / p["reversion_years"]) - 1.0
        blocks = {"dividend_yield": float(p["dividend_yield"]),
                  "real_growth": float(p["real_growth"]),
                  "inflation": float(p["inflation"]),
                  "valuation": valuation}
        rate = require_rate(self.key, "market rate", sum(blocks.values()))
        self.log.info("%s: market hold return %.4f %s", self.key, rate, blocks)
        return {"rate": rate, "blocks": blocks,
                "summary": {"stage": "market hold return", "rate": rate,
                            "horizon_years": int(p["horizon_years"]), **blocks}}


class BetaHoldReturns(Node):
    """Per-symbol expected hold return: ``risk_free + beta x (market - risk_free)``.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``risk_free`` (number in ``[-1, 10]``, REQUIRED); ``betas``
        (non-empty ``{symbol: number}``, REQUIRED); ``symbol_field`` (field
        name, REQUIRED); ``default_beta`` (number, optional): the beta of a
        symbol absent from ``betas``; without it such a symbol refuses.

    Inputs
    ------
    ``records`` (REQUIRED)
        Mapping rows carrying ``symbol_field``.
    ``market`` (REQUIRED)
        The market's expected annual return, a number.

    Examples
    --------
    Two holdings against a 10% market::

        node = BetaHoldReturns("betas", {"risk_free": 0.04,
            "betas": {"VTI": 1.0, "AAPL": 1.2}, "symbol_field": "symbol"})
        node.run(ctx, {"records": rows, "market": 0.10})["rates"]
        # {"VTI": 0.10, "AAPL": 0.112}
    """

    role = "transform"
    outputs = ("rates", "summary")

    _PARAMS = ("risk_free", "betas", "symbol_field", "default_beta")
    _REQUIRED = ("risk_free", "betas", "symbol_field")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        if "risk_free" in params:
            check_rate_param(problems, "risk_free", params["risk_free"])
        betas = params.get("betas")
        if "betas" in params:
            if not isinstance(betas, dict) or not betas or not all(name_ok(k) for k in betas):
                problems.append(f"betas must be a non-empty {{symbol: number}}, got {betas!r}")
            else:
                for symbol, beta in betas.items():
                    if not number_ok(beta):
                        problems.append(f"betas[{symbol!r}] must be a number, got {beta!r}")
        if "symbol_field" in params and not name_ok(params["symbol_field"]):
            problems.append(
                f"symbol_field must be a non-empty field name, got {params['symbol_field']!r}"
            )
        if "default_beta" in params and not number_ok(params["default_beta"]):
            problems.append(f"default_beta must be a number, got {params['default_beta']!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def run(self, ctx, inputs):
        """Return ``{symbol: rate}`` for every symbol seen on the rows, and a summary."""
        records = inputs.get("records")
        market = inputs.get("market")
        if not isinstance(records, list):
            raise ValueError(f"{self.key}: records must be a list, got {type(records).__name__}")
        if not number_ok(market):
            raise ValueError(f"{self.key}: market must be a number, got {market!r}")
        field = self.params["symbol_field"]
        betas = self.params["betas"]
        rates, defaulted = {}, []
        for index, row in enumerate(records):
            if not isinstance(row, dict) or not name_ok(row.get(field)):
                raise ValueError(
                    f"{self.key}: row {index} has no symbol in field {field!r}"
                )
            symbol = row[field]
            if symbol in rates:
                continue
            if symbol in betas:
                beta = betas[symbol]
            elif "default_beta" in self.params:
                beta = self.params["default_beta"]
                defaulted.append(symbol)
            else:
                raise ValueError(f"{self.key}: row {index} has no beta for symbol {symbol!r}")
            rates[symbol] = require_rate(
                self.key, f"rate for {symbol!r}",
                self.params["risk_free"] + beta * (market - self.params["risk_free"]))
        self.log.info("%s: %d symbol(s), market %.4f", self.key, len(rates), market)
        return {"rates": rates, "summary": {
            "stage": "beta hold returns", "symbols": len(rates), "market": market,
            "risk_free": self.params["risk_free"], "defaulted": sorted(defaulted)}}

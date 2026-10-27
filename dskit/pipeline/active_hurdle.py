"""The active hurdle: a backtest's return series into a deflated annual expected return (ADR-0279).

A backtest is the best of everything that was tried, so its mean return is
not an expectation: part of it is what selection alone would have found. This
module turns a per-period net return series into the annual return an active
strategy may claim once that part is removed, by one rule:

    deflated rate = (mean - E[max null Sharpe] x sd) x periods_per_year

where ``E[max null Sharpe]`` is the per-period Sharpe the best of ``trials``
zero-skill strategies would show (:func:`~dskit.pipeline.stats.deflated_sharpe_ratio`,
Bailey & López de Prado 2014). ``mean`` is the arithmetic per-period mean, so
the annual rate is arithmetic too; a consumer that compounds it over years
overstates the geometric path by about ``sd^2 / 2`` per period, which for a
weekly series of a few percent vol is a few basis points a year and is left to
the document's ``risk_premium``. The deflated Sharpe probability from the same
call is the pre-registered admissibility criterion: below ``min_dsr`` the
strategy has not shown skill beyond its own search, and the hurdle is withheld
(``rate`` is ``None``), which :class:`~dskit.pipeline.liquidation.HoldVsRedeploy`
reads as "no admissible alternative, hold".

:class:`ActiveHurdle` (role ``transform``) is wired by import path. The series
comes in as records so it enters through onboarding like everything else;
``trials`` is the document's honest count of what was tried.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

import math

from dskit.pipeline.binary_curve import name_ok
from dskit.pipeline.node import Node, check_int_param, check_number_param, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import deflated_sharpe_ratio, sharpe_ratio

__all__ = ["ActiveHurdle"]

_MIN_PERIODS = 2


class ActiveHurdle(Node):
    """Deflated annual expected return of an active strategy from its backtest returns.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``return_field`` (field name, REQUIRED): the per-period net return
        on each row; ``periods_per_year`` (int >= 1, REQUIRED); ``trials``
        (int >= 1, REQUIRED): how many strategy variants the backtest chose
        among; ``min_dsr`` (number in ``[0, 1]``, REQUIRED): the deflated
        Sharpe probability below which the hurdle is withheld.

    Inputs
    ------
    ``records`` (REQUIRED)
        Mapping rows, at least two, each carrying ``return_field``.

    Examples
    --------
    Weekly net returns of a strategy chosen among five::

        node = ActiveHurdle("hurdle", {"return_field": "net_return",
            "periods_per_year": 52, "trials": 5, "min_dsr": 0.5})
        out = node.run(ctx, {"records": rows})
        out["rate"], out["admissible"]
    """

    role = "transform"
    outputs = ("rate", "admissible", "summary")

    _PARAMS = ("return_field", "periods_per_year", "trials", "min_dsr")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._PARAMS) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        field = params.get("return_field")
        if "return_field" in params and not name_ok(field):
            problems.append(f"return_field must be a non-empty field name, got {field!r}")
        for name in ("periods_per_year", "trials"):
            if name in params:
                check_int_param(problems, name, params[name], ge=1)
        if "min_dsr" in params:
            check_number_param(problems, "min_dsr", params["min_dsr"], lower=0.0, upper=1.0)
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _series(self, records):
        """Return the per-period returns the rows carry; refuse by index and field."""
        if not isinstance(records, list):
            raise ValueError(f"{self.key}: records must be a list, got {type(records).__name__}")
        field = self.params["return_field"]
        series = []
        for index, row in enumerate(records):
            if not isinstance(row, dict):
                raise ValueError(f"{self.key}: row {index} must be a mapping, got {type(row).__name__}")
            value = row.get(field)
            if not number_ok(value):
                raise ValueError(
                    f"{self.key}: row {index} field {field!r} is not a number, got {value!r}"
                )
            series.append(float(value))
        if len(series) < _MIN_PERIODS:
            raise ValueError(
                f"{self.key}: a hurdle needs at least {_MIN_PERIODS} periods, got {len(series)}"
            )
        return series

    def run(self, ctx, inputs):
        """Return the deflated annual rate (``None`` when inadmissible), the verdict and a summary."""
        series = self._series(inputs.get("records"))
        periods = self.params["periods_per_year"]
        found = sharpe_ratio(series)
        deflated = deflated_sharpe_ratio(series, self.params["trials"])
        expected_max = deflated["expected_max_sharpe"]
        if deflated["dsr"] is None or expected_max is None:
            raise ValueError(
                f"{self.key}: the series has no usable Sharpe (sd {found['sd']!r}); "
                "a hurdle needs a dispersed return series"
            )
        haircut = expected_max * found["sd"] * periods
        deflated_rate = found["mean"] * periods - haircut
        dsr = deflated["dsr"]
        admissible = dsr is not None and dsr >= self.params["min_dsr"]
        rate = deflated_rate if admissible else None
        self.log.info("%s: %d period(s), dsr %s, deflated rate %.4f, admissible %s",
                      self.key, len(series), dsr, deflated_rate, admissible)
        return {"rate": rate, "admissible": admissible, "summary": {
            "stage": "active hurdle", "periods": len(series),
            "periods_per_year": periods, "trials": self.params["trials"],
            "annual_mean": found["mean"] * periods,
            "annual_sd": found["sd"] * math.sqrt(periods),
            "sharpe": found["sharpe"], "expected_max_sharpe": expected_max,
            "dsr": dsr, "min_dsr": self.params["min_dsr"],
            "haircut": haircut, "deflated_rate": deflated_rate,
            "admissible": admissible}}

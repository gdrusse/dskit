"""A seeded synthetic price path with KNOWN conditional-volatility dynamics.

Distribution forecasts need a test bed whose truth is known: a GJR-GARCH(1,1)
process with standardized Student-t shocks has volatility clustering, a
leverage effect (negative shocks raise variance more) and fat tails — the
features a horizon-return distribution model must capture — and every one is
a declared parameter (ADR-0168). Nothing here names an asset or a market.

The generator is ``random.Random(seed)``: the Mersenne Twister's sequence for
a given seed is stable across platforms and Python versions, so a document's
identity (which hashes these params) pins the exact path. Stdlib only.
"""

from __future__ import annotations

import math
import random

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params

__all__ = ["GJR_DEFAULTS", "SynthGjrPaths"]

_DAY_MS = 24 * 60 * 60 * 1000

#: Every knob's default, named ONCE — read by validation, the run and the
#: fingerprint alike. Daily-scale values loosely resembling a broad equity
#: index: ~1% daily vol, persistence 0.98, a leverage term, t(6) shocks.
GJR_DEFAULTS = {
    "n_days": 1500,
    "seed": 0,
    "mu": 0.0003,
    "omega": 2e-6,
    "alpha": 0.02,
    "gamma": 0.12,
    "beta": 0.9,
    "nu": 6.0,
    "s0": 100.0,
    "instrument": "SYN",
    "start_ms": 1000 * _DAY_MS,
}

_FLOATS = ("mu", "omega", "alpha", "gamma", "beta", "nu", "s0")


class SynthGjrPaths(Node):
    """Daily closes from a GJR-GARCH(1,1) process with Student-t shocks (role ``data``).

    ``r_t = mu + sigma_t * eta_t`` with ``sigma_t^2 = omega + (alpha + gamma *
    1{e_{t-1} < 0}) * e_{t-1}^2 + beta * sigma_{t-1}^2`` and ``eta`` a
    unit-variance Student-t with ``nu`` degrees of freedom. Each record
    carries ``close`` and ``cond_vol`` (``sigma_t``, known at ``t - 1``: the
    oracle scale a test can compare a forecast against).

    Parameters
    ----------
    params : dict
        Any of :data:`GJR_DEFAULTS`' keys. ``omega`` > 0, ``alpha``,
        ``gamma``, ``beta`` >= 0 with ``alpha + gamma / 2 + beta < 1``,
        ``nu`` > 2, ``s0`` > 0, ``n_days`` >= 2.

    Examples
    --------
    Two thousand days with a stronger leverage effect::

        node = SynthGjrPaths("market", {"n_days": 2000, "seed": 7, "gamma": 0.15})
        records = node.run(None, {})["records"]
        # -> [{"instrument": "SYN", "asof_ms": ..., "close": 100.03, ...}, ...]
    """

    role = "data"
    outputs = ("records",)
    _PARAMS = tuple(sorted(GJR_DEFAULTS))

    def knob(self, name):
        """Read one knob, falling back to its single named default.

        Parameters
        ----------
        name : str
            A key of :data:`GJR_DEFAULTS`.

        Returns
        -------
        object
            The declared value, or the default.
        """
        return self.params.get(name, GJR_DEFAULTS[name])

    @classmethod
    def validate_params(cls, params):
        """List the problems with ``params``.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        merged = {**GJR_DEFAULTS, **params}
        check_int_param(problems, "n_days", merged["n_days"], ge=2)
        check_int_param(problems, "seed", merged["seed"], ge=0)
        check_int_param(problems, "start_ms", merged["start_ms"], ge=0)
        if not isinstance(merged["instrument"], str) or not merged["instrument"]:
            problems.append(f"instrument must be a non-empty string, got {merged['instrument']!r}")
        bad = [k for k in _FLOATS if isinstance(merged[k], bool)
               or not isinstance(merged[k], (int, float)) or not math.isfinite(merged[k])]
        if bad:
            return problems + [f"{bad} must be finite numbers"]
        if merged["omega"] <= 0 or merged["s0"] <= 0:
            problems.append("omega and s0 must be > 0")
        if min(merged["alpha"], merged["gamma"], merged["beta"]) < 0:
            problems.append("alpha, gamma and beta must be >= 0")
        if merged["alpha"] + merged["gamma"] / 2 + merged["beta"] >= 1:
            problems.append("alpha + gamma / 2 + beta must be < 1 (covariance stationarity)")
        if merged["nu"] <= 2:
            problems.append("nu must be > 2 (finite variance)")
        return problems

    def fingerprint(self):
        """Identify the path: every knob, defaults included.

        Returns
        -------
        dict
            The generator's full parameterization.
        """
        return {"kind": "synth-gjr-paths", **{k: self.knob(k) for k in self._PARAMS}}

    def data_edge(self):
        """Return the last record's instant.

        Returns
        -------
        int
            Epoch ms of the final day.
        """
        return self.knob("start_ms") + (self.knob("n_days") - 1) * _DAY_MS

    def shock(self, rng):
        """Draw one unit-variance Student-t shock.

        Parameters
        ----------
        rng : random.Random
            The seeded generator.

        Returns
        -------
        float
            A draw with mean 0 and variance 1.
        """
        nu = float(self.knob("nu"))
        chi2 = rng.gammavariate(nu / 2.0, 2.0)
        return rng.gauss(0.0, 1.0) / math.sqrt(chi2 / nu) * math.sqrt((nu - 2.0) / nu)

    def run(self, ctx, inputs):
        """Generate the path.

        Parameters
        ----------
        ctx : NodeContext or None
            Unused.
        inputs : dict
            Unused; a source has no inputs.

        Returns
        -------
        dict
            ``records``: one dict per day, oldest first.
        """
        rng = random.Random(self.knob("seed"))
        omega, alpha, gamma, beta = (self.knob(k) for k in ("omega", "alpha", "gamma", "beta"))
        var = omega / (1.0 - alpha - gamma / 2.0 - beta)
        price, shock_prev, records = float(self.knob("s0")), 0.0, []
        name = self.knob("instrument")
        for day in range(self.knob("n_days")):
            if day:
                var = omega + (alpha + gamma * (shock_prev < 0)) * shock_prev ** 2 + beta * var
            sigma = math.sqrt(var)
            shock_prev = sigma * self.shock(rng)
            if day:
                price *= math.exp(self.knob("mu") + shock_prev)
            records.append({
                "instrument": name, "contract": f"{name}-{day:06d}", "group": f"{name}:{day}",
                "asof_ms": self.knob("start_ms") + day * _DAY_MS,
                "close": price, "cond_vol": sigma,
            })
        return {"records": records}

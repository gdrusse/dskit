"""A random-walk level plus an AR(1) deviation, filtered per pair with parameters fitted on prior windows (ADR-0274).

The observed spread is modelled as ``s_t = mu_t + x_t + e_t`` with ``mu_t = mu_{t-1} + n_t`` (a slowly
moving level), ``x_t = phi x_{t-1} + w_t`` (a mean-reverting deviation) and Gaussian noises with standard
deviations ``sigma_mu``, ``sigma_x`` and ``sigma_obs``. The four parameters are estimated by maximum
likelihood on the ``window`` sessions strictly before each declared ``fit_date`` and carried until the
next fit; the Kalman filter then runs through every session with the current parameters, so the state
at ``t`` uses the spread through ``t`` and nothing later. The expected change over ``h`` sessions is
``(phi**h - 1) * x_hat_t``: the deviation decays, the level is a martingale. Numpy and scipy load only
inside ``run``. Nothing here trades.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "KalmanSpreadState"]

NODE_KINDS = ()

_BEFORE = "before_first_fit"
_OK = "ok"
_NO_SPREAD = "no_spread"
_DEGENERATE = "degenerate"
_PHI_MIN, _PHI_MAX = 0.01, 0.995


class KalmanSpreadState(Node):
    """Filter each pair's spread as level plus AR(1) deviation; parameters from prior-only windows (role ``transform``).

    Parameters
    ----------
    params : dict
        ``horizons`` (distinct positive ints, REQUIRED), ``window`` (int >= 20, sessions strictly before a
        fit date, REQUIRED), ``fit_dates`` (strictly increasing ISO dates, REQUIRED), ``max_iter``
        (int >= 1, optimizer iterations, REQUIRED); optional ``date_field``, ``symbol_a_field``,
        ``symbol_b_field``, ``spread_field``, ``status_field`` with ``ok_value``.

    Outputs
    -------
    ``records``
        One row per (pair, date) in pair then date order: ``date``, ``pair``, ``symbol_a``, ``symbol_b``,
        ``spread``, ``status`` (``before_first_fit`` / ``ok`` / ``no_spread`` / ``degenerate``),
        ``fit_date``, ``trained_through``, ``kf_level``, ``kf_dev``, ``kf_phi``, ``kf_sigma_x``,
        ``kf_sigma_mu``, ``kf_sigma_obs``, ``kf_halflife`` and ``kf_fc_<h>`` per horizon.
    ``summary``
        ``pairs``, ``records``, ``status`` counts and ``fits`` (one dict per pair and fit date).

    Examples
    --------
    Yearly fits on the prior 252 sessions, five- and twenty-session expected changes::

        node = KalmanSpreadState("kf", {"horizons": [5, 20], "window": 252, "max_iter": 200,
                                        "fit_dates": ["2020-01-02", "2021-01-04"]})
        out = node.run(ctx, {"spreads": spread_rows})
    """

    role = "transform"
    outputs = ("records", "summary")
    _PARAMS = ("horizons", "window", "fit_dates", "max_iter", "date_field", "symbol_a_field",
               "symbol_b_field", "spread_field", "status_field", "ok_value")
    _REQUIRED = ("horizons", "window", "fit_dates", "max_iter")

    @classmethod
    def validate_params(cls, params):
        """Default-deny: refuse unknown, missing and malformed knobs."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            if name not in params:
                problems.append(f"{name} is required")
        horizons = params.get("horizons")
        if "horizons" in params and (not isinstance(horizons, list) or not horizons
                                     or any(type(h) is not int or h < 1 for h in horizons)
                                     or len(set(horizons)) != len(horizons)):
            problems.append("horizons must be a non-empty list of distinct positive integers")
        if "window" in params:
            check_int_param(problems, "window", params["window"], ge=20)
        if "max_iter" in params:
            check_int_param(problems, "max_iter", params["max_iter"], ge=1)
        fit_dates = params.get("fit_dates")
        if "fit_dates" in params:
            ok = isinstance(fit_dates, list) and bool(fit_dates)
            if ok:
                for value in fit_dates:
                    try:
                        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                            raise ValueError
                    except ValueError:
                        ok = False
                if ok and any(a >= b for a, b in zip(fit_dates, fit_dates[1:])):
                    ok = False
            if not ok:
                problems.append("fit_dates must be a non-empty, strictly increasing list of ISO dates")
        for name in ("date_field", "symbol_a_field", "symbol_b_field", "spread_field", "status_field"):
            if name in params and (not isinstance(params[name], str) or not params[name]):
                problems.append(f"{name} must be a non-empty string")
        if ("ok_value" in params) != ("status_field" in params):
            problems.append("status_field and ok_value must be declared together")
        return problems

    def run(self, ctx, inputs):
        """Group the spreads per pair, fit at each fit date on the prior window, filter through every date.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``spreads``: rows with a date, two symbols and a spread value.

        Returns
        -------
        dict
            ``records`` and ``summary``.

        Raises
        ------
        ValueError
            A missing input, a malformed date, or a repeated (pair, date) key.
        """
        if "spreads" not in inputs:
            raise ValueError(f"{self.key}: spreads input is required")
        series = self._series(inputs["spreads"])
        records, fits, counts = [], [], Counter()
        for pair, rows in series.items():
            out, pair_fits = self._filter_pair(pair, rows)
            records.extend(out)
            fits.extend(pair_fits)
            counts.update(r["status"] for r in out)
        return {"records": records,
                "summary": {"pairs": len(series), "records": len(records), "status": dict(counts), "fits": fits}}

    # -- data -------------------------------------------------------------------------------------

    def _series(self, rows):
        """Return ``{(a, b): [(date, spread or None), ...]}`` in date order, refusing repeats."""
        p = self.params
        date_field, a_field, b_field = p.get("date_field", "date"), p.get("symbol_a_field", "symbol_a"), p.get("symbol_b_field", "symbol_b")
        spread_field = p.get("spread_field", "spread")
        series, seen = {}, set()
        for row in rows:
            day = row.get(date_field)
            try:
                if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(f"{self.key}: invalid ISO date {day!r}") from exc
            a, b = row.get(a_field), row.get(b_field)
            if not isinstance(a, str) or not isinstance(b, str) or not a or not b or a == b:
                raise ValueError(f"{self.key}: spread row needs two distinct non-empty symbols")
            pair = tuple(sorted((a, b)))
            sign = 1.0 if (a, b) == pair else -1.0
            if (pair, day) in seen:
                raise ValueError(f"{self.key}: duplicate spread key {(day, *pair)!r}")
            seen.add((pair, day))
            ok = "status_field" not in p or row.get(p["status_field"]) == p.get("ok_value")
            value = row.get(spread_field)
            series.setdefault(pair, []).append((day, sign * float(value) if ok and number_ok(value) else None))
        return {pair: sorted(rows) for pair, rows in sorted(series.items())}

    # -- model ------------------------------------------------------------------------------------

    @staticmethod
    def _unpack(theta):
        """Map unconstrained optimizer coordinates to (phi, sigma_x, sigma_mu, sigma_obs)."""
        phi = _PHI_MIN + (_PHI_MAX - _PHI_MIN) / (1.0 + math.exp(-theta[0]))
        return phi, math.exp(theta[1]), math.exp(theta[2]), math.exp(theta[3])

    @staticmethod
    def _filter_step(state, cov, obs, phi, var_mu, var_x, var_obs):
        """One predict-update step on the 2-state model; returns (state, cov, loglik term)."""
        import numpy as np

        f = np.array([[1.0, 0.0], [0.0, phi]])
        q = np.diag([var_mu, var_x])
        state = f @ state
        cov = f @ cov @ f.T + q
        if obs is None:
            return state, cov, 0.0
        h = np.array([1.0, 1.0])
        innovation = obs - h @ state
        s = float(h @ cov @ h + var_obs)
        gain = cov @ h / s
        state = state + gain * innovation
        cov = cov - np.outer(gain, h) @ cov
        return state, cov, -0.5 * (math.log(2.0 * math.pi * s) + innovation * innovation / s)

    def _loglik(self, theta, values):
        """Gaussian log-likelihood of a complete window under the coordinates ``theta``."""
        import numpy as np

        phi, sigma_x, sigma_mu, sigma_obs = self._unpack(theta)
        var_mu, var_x, var_obs = sigma_mu ** 2, sigma_x ** 2, sigma_obs ** 2
        spread_var = float(np.var(values)) or 1.0
        state = np.array([values[0], 0.0])
        cov = np.diag([spread_var, spread_var])
        total = 0.0
        for obs in values[1:]:
            state, cov, term = self._filter_step(state, cov, obs, phi, var_mu, var_x, var_obs)
            total += term
        return total

    def _fit(self, pair, fit_date, window_rows):
        """Maximum-likelihood parameters on one prior-only window; a fit record with ``converged``."""
        import numpy as np
        from scipy.optimize import minimize

        values = [v for _, v in window_rows if v is not None]
        record = {"pair": "|".join(pair), "fit_date": fit_date,
                  "trained_through": window_rows[-1][0] if window_rows else None,
                  "n": len(values), "converged": False, "phi": None, "sigma_x": None, "sigma_mu": None,
                  "sigma_obs": None, "loglik": None, "iterations": 0}
        if len(values) < max(20, self.params["window"] // 2) or float(np.std(values)) == 0.0:
            return record, None
        scale = float(np.std(np.diff(values))) or 1.0
        start = np.array([math.log(0.9 / 0.1), math.log(scale), math.log(scale * 0.1), math.log(scale * 0.1)])
        result = minimize(lambda t: -self._loglik(t, values), start, method="L-BFGS-B",
                          options={"maxiter": int(self.params["max_iter"])})
        phi, sigma_x, sigma_mu, sigma_obs = self._unpack(result.x)
        record.update(phi=phi, sigma_x=sigma_x, sigma_mu=sigma_mu, sigma_obs=sigma_obs,
                      loglik=-float(result.fun), iterations=int(result.nit))
        degenerate = (not bool(result.success) or not (_PHI_MIN + 1e-6 < phi < _PHI_MAX - 1e-6)
                      or not math.isfinite(result.fun) or sigma_x <= 1e-9 * scale)
        record["converged"] = not degenerate
        return record, (None if degenerate else (phi, sigma_x, sigma_mu, sigma_obs))

    def _filter_pair(self, pair, rows):
        """Fit at each fit date on the prior window, then filter the whole series with the current parameters."""
        import numpy as np

        p = self.params
        horizons = p["horizons"]
        days = [d for d, _ in rows]
        fits, params_at = [], []
        for fit_date in p["fit_dates"]:
            cutoff = sum(1 for d in days if d < fit_date)
            window_rows = rows[max(0, cutoff - p["window"]):cutoff]
            record, params = self._fit(pair, fit_date, window_rows)
            fits.append(record)
            params_at.append((fit_date, record["trained_through"], params))
        out = []
        current, provenance = None, (None, None)
        state = cov = None
        first_valid_index = next((i for i, (_, _, prm) in enumerate(params_at) if prm is not None), None)
        for i, (day, value) in enumerate(rows):
            for fit_date, trained_through, params in params_at:
                if fit_date <= day and (provenance[0] is None or fit_date > provenance[0]):
                    if params is not None:
                        current, provenance = params, (fit_date, trained_through)
                    elif current is None:
                        provenance = (fit_date, trained_through)
            record = {"date": day, "pair": "|".join(pair), "symbol_a": pair[0], "symbol_b": pair[1],
                      "spread": value, "fit_date": provenance[0], "trained_through": provenance[1],
                      "kf_level": None, "kf_dev": None, "kf_phi": None, "kf_sigma_x": None,
                      "kf_sigma_mu": None, "kf_sigma_obs": None, "kf_halflife": None}
            for h in horizons:
                record[f"kf_fc_{h}"] = None
            if first_valid_index is None or day < params_at[first_valid_index][0]:
                record["status"] = _BEFORE if first_valid_index is None or provenance[0] is None else _DEGENERATE
                if first_valid_index is not None and day < params_at[first_valid_index][0] and provenance[0] is None:
                    record["status"] = _BEFORE
                # warm the filter on history with the first valid parameters, emitting nothing
                if first_valid_index is not None and current is None:
                    warm = params_at[first_valid_index][2]
                    if state is None:
                        values = [v for _, v in rows[:i + 1] if v is not None]
                        spread_var = float(np.var(values)) if len(values) > 1 else 1.0
                        state = np.array([values[0] if values else 0.0, 0.0])
                        cov = np.diag([spread_var or 1.0, spread_var or 1.0])
                    else:
                        state, cov, _ = self._filter_step(state, cov, value, warm[0], warm[2] ** 2, warm[1] ** 2, warm[3] ** 2)
                out.append(record)
                continue
            if current is None:
                record["status"] = _DEGENERATE
                out.append(record)
                continue
            phi, sigma_x, sigma_mu, sigma_obs = current
            if state is None:
                values = [v for _, v in rows[:i + 1] if v is not None]
                spread_var = float(np.var(values)) if len(values) > 1 else 1.0
                state = np.array([values[0] if values else 0.0, 0.0])
                cov = np.diag([spread_var or 1.0, spread_var or 1.0])
            state, cov, _ = self._filter_step(state, cov, value, phi, sigma_mu ** 2, sigma_x ** 2, sigma_obs ** 2)
            if value is None:
                record["status"] = _NO_SPREAD
                out.append(record)
                continue
            dev = float(state[1])
            record.update(status=_OK, kf_level=float(state[0]), kf_dev=dev, kf_phi=phi, kf_sigma_x=sigma_x,
                          kf_sigma_mu=sigma_mu, kf_sigma_obs=sigma_obs,
                          kf_halflife=math.log(0.5) / math.log(phi))
            for h in horizons:
                record[f"kf_fc_{h}"] = (phi ** h - 1.0) * dev
            out.append(record)
        return out, fits

"""Lagged rolling sparse-precision graphs over factor-adjusted return panels.

A graph dated ``t`` uses only dates strictly before ``t``. Its edges are
conditional associations, not causal links or orders. An unusable fit emits a
counted status and no tradable edge. Numpy, sklearn and networkx load only at
execution, so a document remains plannable without optional dependencies.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from datetime import date

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "RollingPrecisionGraph", "RollingCorrelationGraph"]

NODE_KINDS = ()


class RollingPrecisionGraph(Node):
    """Fit one factor-residualized sparse precision graph per decision date.

    Parameters
    ----------
    params : dict
        ``date_field``, ``symbol_field``, ``return_field`` and ``factor_field``
        name long-panel columns; ``symbols`` names at least two assets, excluding
        the factor. ``window`` is the number of prior panel dates, and
        ``min_observations`` the minimum complete dates inside it. The factor
        and each asset must vary in the window. ``cv_splits`` determines the
        chronological TimeSeriesSplit, ``max_iter`` and ``tol`` control
        GraphicalLassoCV, and ``edge_min_weight`` screens absolute partial
        correlations. Optional ``decision_dates`` restricts decision outputs to ISO dates.
        ``refit_frequency`` is ``"yearly"`` and ``fit_dates`` pins at most
        one fit date per calendar year. A fit uses prior observations only;
        its graph is carried until the next pinned fit date.

    Examples
    --------
    Fit after each observed date from a three-asset panel::

        node = RollingPrecisionGraph("graph", {
            "date_field": "date", "symbol_field": "symbol",
            "return_field": "log_return", "factor_field": "index_return",
            "symbols": ["A", "B", "C"], "window": 60,
            "min_observations": 50, "cv_splits": 3,
            "max_iter": 200, "tol": 1e-4, "edge_min_weight": 0.05,
            "refit_frequency": "yearly",
            "fit_dates": ["2020-01-02"]})
        out = node.run(None, {"records": panel_rows})
        # -> out["edges"] contains only graph edges fitted from earlier dates
    """

    role = "transform"
    outputs = ("edges", "centrality", "fits")
    supported_split_kinds = ("time", "trailing")
    _PARAMS = ("date_field", "symbol_field", "return_field", "factor_field", "symbols",
               "window", "min_observations", "cv_splits", "max_iter", "tol",
               "edge_min_weight", "decision_dates", "refit_frequency", "fit_dates")
    _FIELDS = ("date_field", "symbol_field", "return_field", "factor_field")

    @classmethod
    def validate_params(cls, params):
        """Validate the graph declaration without importing optional libraries.

        Parameters
        ----------
        params : dict
            Node parameters.

        Returns
        -------
        list of str
            Problems, empty for a legal declaration.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for key in cls._FIELDS:
            if not isinstance(params.get(key), str) or not params.get(key):
                problems.append(f"{key} must name a non-empty field")
        fields = [params.get(k) for k in cls._FIELDS]
        if all(isinstance(v, str) and v for v in fields) and len(set(fields)) != len(fields):
            problems.append("date, symbol, return and factor fields must be distinct")
        symbols = params.get("symbols")
        if (not isinstance(symbols, list) or len(symbols) < 2
                or any(not isinstance(v, str) or not v for v in symbols)
                or len(set(symbols)) != len(symbols)):
            problems.append("symbols must be a list of at least two distinct non-empty strings")
        for key, floor in (("window", 4), ("min_observations", 4),
                           ("cv_splits", 2), ("max_iter", 1)):
            check_int_param(problems, key, params.get(key), ge=floor)
        window, minimum, splits = (params.get(k) for k in ("window", "min_observations", "cv_splits"))
        if all(type(v) is int for v in (window, minimum, splits)):
            if minimum > window:
                problems.append("min_observations must not exceed window")
            if minimum <= splits + 1:
                problems.append("min_observations must exceed cv_splits + 1")
        tol = params.get("tol")
        if not number_ok(tol) or tol <= 0:
            problems.append("tol must be finite and > 0")
        threshold = params.get("edge_min_weight")
        if not number_ok(threshold) or not 0 <= threshold < 1:
            problems.append("edge_min_weight must be finite in [0, 1)")
        if params.get("refit_frequency") != "yearly":
            problems.append("refit_frequency must be yearly")
        fit_dates = params.get("fit_dates")
        if (not isinstance(fit_dates, list) or not fit_dates
                or any(not isinstance(v, str) or _iso_day(v) is None for v in fit_dates)
                or len(set(fit_dates)) != len(fit_dates)):
            problems.append("fit_dates must be a non-empty list of distinct ISO dates")
        elif len({v[:4] for v in fit_dates}) != len(fit_dates):
            problems.append("fit_dates must have at most one date per calendar year")
        if "decision_dates" in params:
            dates = params["decision_dates"]
            if (not isinstance(dates, list) or not dates
                    or any(not isinstance(v, str) or _iso_day(v) is None for v in dates)
                    or len(set(dates)) != len(dates)):
                problems.append("decision_dates must be distinct ISO dates")
        return problems

    def validate_inputs(self, inputs):
        """Require a materialized long panel.

        Parameters
        ----------
        inputs : dict
            Wired node inputs.

        Returns
        -------
        list of str
            Input problems.
        """
        rows = inputs.get("records")
        return [] if isinstance(rows, list) and rows else ["records must be a non-empty list"]

    def run(self, ctx, inputs):
        """Return lagged edges, centralities and one fit-status row per date.

        Parameters
        ----------
        ctx : NodeContext
            Unused pipeline context.
        inputs : dict
            ``records`` is a long-panel list. Each date/symbol occurs once;
            each same-date row repeats the same factor return.

        Returns
        -------
        dict
            ``edges`` (dated sorted pairs with signed partial correlation and
            absolute weight), ``centrality`` (eigenvector score by node/date),
            and ``fits`` (status, history count, chosen alpha, edge count and
            warning for each decision date).

        Raises
        ------
        ValueError
            Malformed panel rows, duplicate keys or inconsistent factor rows.
        """
        import numpy as np
        import networkx as nx
        from sklearn.covariance import GraphicalLassoCV
        from sklearn.exceptions import ConvergenceWarning
        from sklearn.model_selection import TimeSeriesSplit

        problems = self.validate_inputs(inputs)
        if problems:
            raise ValueError(f"{self.key}: {problems[0]}")
        panel = self._panel(inputs["records"])
        dates = sorted(panel)
        decisions = sorted(self.params.get("decision_dates", dates))
        symbols = self.params["symbols"]
        edges, centrality, fits = [], [], []
        annual = None
        active_date = None
        fit_dates = sorted(self.params["fit_dates"])
        for decision in decisions:
            eligible = [d for d in fit_dates if d <= decision]
            next_date = eligible[-1] if eligible else None
            refit = next_date is not None and next_date != active_date
            if refit:
                active_date = next_date
                annual = self._fit_one(active_date, dates, panel, symbols, np, nx,
                                       GraphicalLassoCV, TimeSeriesSplit, ConvergenceWarning)
            if annual is None:
                fits.append({"date": decision, "fit_date": None, "trained_through": None,
                             "refit": False, "status": "not_fitted", "n_window": 0,
                             "alpha": None, "n_edges": 0, "warning": None,
                             "train_log_likelihood": None, "cv_score": None, "n_iter": None})
                continue
            fit, graph_edges, graph_centrality = annual
            status = dict(fit, date=decision, refit=refit)
            fits.append(status)
            if fit["status"] == "ok":
                edges.extend(dict(row, date=decision) for row in graph_edges)
                centrality.extend(dict(row, date=decision) for row in graph_centrality)
        return {"edges": edges, "centrality": centrality, "fits": fits}

    def _fit_one(self, decision, dates, panel, symbols, np, nx,
                 GraphicalLassoCV, TimeSeriesSplit, ConvergenceWarning):
        """Fit from prior dates only; return one annual graph and diagnostics."""
        past = [d for d in dates if d < decision][-self.params["window"]:]
        values = [panel[d] for d in past if self._complete(panel[d], symbols)]
        fit = {"fit_date": decision, "trained_through": past[-1] if past else None,
               "status": "insufficient_history", "n_window": len(values),
               "alpha": None, "n_edges": 0, "warning": None,
               "train_log_likelihood": None, "cv_score": None, "n_iter": None}
        if len(values) < self.params["min_observations"]:
            return fit, [], []
        fit["trained_through"] = next(d for d in reversed(past) if self._complete(panel[d], symbols))
        factor = np.asarray([row["factor"] for row in values], dtype=float)
        matrix = np.asarray([[row["returns"][s] for s in symbols] for row in values], dtype=float)
        if np.std(factor, ddof=1) <= 1e-12:
            fit["status"] = "factor_degenerate"
            return fit, [], []
        standardized = self._causal_features(factor, matrix, np)
        if len(standardized) <= self.params["cv_splits"] + 1:
            fit["status"] = "insufficient_causal_features"
            return fit, [], []
        estimator = GraphicalLassoCV(cv=TimeSeriesSplit(n_splits=self.params["cv_splits"]),
                                     max_iter=self.params["max_iter"], tol=self.params["tol"])
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                estimator.fit(standardized)
            fit["alpha"] = float(estimator.alpha_)
            fit["n_iter"] = int(estimator.n_iter_)
            train_score = float(estimator.score(standardized))
            if not math.isfinite(train_score) or not math.isfinite(fit["alpha"]):
                fit["status"] = "invalid_score"
                return fit, [], []
            fit["train_log_likelihood"] = train_score
            cv = np.asarray(estimator.cv_results_["mean_test_score"], dtype=float)
            if np.any(np.isfinite(cv)):
                fit["cv_score"] = float(np.max(cv[np.isfinite(cv)]))
            bad = [str(w.message) for w in caught if issubclass(w.category, ConvergenceWarning)]
            if bad:
                fit["status"], fit["warning"] = "nonconverged", bad[0]
                return fit, [], []
            precision = estimator.precision_
            if not np.all(np.isfinite(precision)) or np.any(np.diag(precision) <= 0):
                fit["status"] = "invalid_precision"
                return fit, [], []
        except (ValueError, FloatingPointError, ArithmeticError) as exc:
            fit["status"], fit["warning"] = "fit_failed", str(exc)
            return fit, [], []
        weights = -precision / np.sqrt(np.outer(np.diag(precision), np.diag(precision)))
        return self._finish_graph(decision, fit, symbols, weights, nx,
                                  "partial_correlation")

    def _finish_graph(self, decision, fit, symbols, weights, nx, signed_field):
        """Screen signed association weights and return a connected graph."""
        import numpy as np

        if not np.all(np.isfinite(weights)):
            fit["status"] = "invalid_weights"
            return fit, [], []
        graph = nx.Graph()
        graph.add_nodes_from(symbols)
        candidates = []
        for i, first in enumerate(symbols):
            for j in range(i + 1, len(symbols)):
                weight = float(weights[i, j])
                if abs(weight) <= self.params["edge_min_weight"]:
                    continue
                a, b = sorted((first, symbols[j]))
                row = {"symbol_a": a, "symbol_b": b,
                       signed_field: weight, "weight": abs(weight),
                       "fit_date": decision, "trained_through": fit["trained_through"]}
                candidates.append(row)
                graph.add_edge(a, b, weight=row["weight"])
        fit["n_edges"] = len(candidates)
        if not candidates:
            fit["status"] = "no_edges"
        elif not nx.is_connected(graph):
            fit["status"] = "disconnected"
        else:
            try:
                scores = nx.eigenvector_centrality(graph, weight="weight", max_iter=1000)
            except nx.PowerIterationFailedConvergence:
                fit["status"] = "centrality_failed"
            else:
                fit["status"] = "ok"
                centrality = [{"symbol": name, "eigenvector_centrality": float(scores[name]),
                               "fit_date": decision, "trained_through": fit["trained_through"]}
                              for name in sorted(symbols)]
                return fit, sorted(candidates, key=lambda r: (r["symbol_a"], r["symbol_b"])), centrality
        return fit, [], []


    @staticmethod
    def _causal_features(factor, matrix, np):
        """Standardize each return using factor OLS and scale fitted before its date."""
        minimum_history = 2 * matrix.shape[1]
        rows = []
        for index in range(minimum_history, len(factor)):
            historical_factor = factor[:index]
            if np.std(historical_factor, ddof=1) <= 1e-12:
                continue
            design = np.column_stack((np.ones(index), historical_factor))
            beta = np.linalg.lstsq(design, matrix[:index], rcond=None)[0]
            historical_residual = matrix[:index] - design @ beta
            scale = np.std(historical_residual, axis=0, ddof=1)
            if np.any(scale <= 1e-12):
                continue
            current_residual = matrix[index] - np.array([1.0, factor[index]]) @ beta
            rows.append(current_residual / scale)
        return np.asarray(rows, dtype=float).reshape(-1, matrix.shape[1])

    def _panel(self, rows):
        """Validate and index the long panel; non-finite values remain incomplete cells."""
        p = self.params
        panel = {}
        symbols = set(p["symbols"])
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"{self.key}: row {index} is not a mapping")
            day = _iso_day(row.get(p["date_field"]))
            symbol = row.get(p["symbol_field"])
            if day is None or not isinstance(symbol, str) or symbol not in symbols:
                raise ValueError(f"{self.key}: row {index} has an invalid date or symbol")
            cell = panel.setdefault(day, {"returns": {}, "factor": None})
            if symbol in cell["returns"]:
                raise ValueError(f"{self.key}: duplicate ({day}, {symbol})")
            factor = _finite(row.get(p["factor_field"]))
            if factor is not None:
                held = cell["factor"]
                if held is not None and abs(held - factor) > 1e-12:
                    raise ValueError(f"{self.key}: inconsistent factor return on {day}")
                cell["factor"] = factor
            cell["returns"][symbol] = _finite(row.get(p["return_field"]))
        return panel

    @staticmethod
    def _complete(cell, symbols):
        """Whether one date has every asset return and the shared factor."""
        return (cell["factor"] is not None and all(cell["returns"].get(s) is not None for s in symbols))


class RollingCorrelationGraph(RollingPrecisionGraph):
    """Annual prior-window Pearson correlation graph of causal factor residuals.

    It accepts the same window, edge, and decision calendar as the precision
    engine to make gate comparisons clock matched. GraphicalLasso-only fit
    controls are accepted but unused; alpha/CV/iterations are null in fits.
    """

    def _fit_one(self, decision, dates, panel, symbols, np, nx,
                 GraphicalLassoCV, TimeSeriesSplit, ConvergenceWarning):
        """Fit a simple residual-correlation gate using strictly earlier dates."""
        past = [d for d in dates if d < decision][-self.params["window"]:]
        values = [panel[d] for d in past if self._complete(panel[d], symbols)]
        fit = {"fit_date": decision, "trained_through": past[-1] if past else None,
               "status": "insufficient_history", "n_window": len(values),
               "alpha": None, "n_edges": 0, "warning": None,
               "train_log_likelihood": None, "cv_score": None, "n_iter": None}
        if len(values) < self.params["min_observations"]:
            return fit, [], []
        fit["trained_through"] = next(d for d in reversed(past)
                                      if self._complete(panel[d], symbols))
        factor = np.asarray([row["factor"] for row in values], dtype=float)
        matrix = np.asarray([[row["returns"][s] for s in symbols]
                             for row in values], dtype=float)
        if np.std(factor, ddof=1) <= 1e-12:
            fit["status"] = "factor_degenerate"
            return fit, [], []
        residuals = self._causal_features(factor, matrix, np)
        if len(residuals) <= self.params["cv_splits"] + 1:
            fit["status"] = "insufficient_causal_features"
            return fit, [], []
        scale = np.std(residuals, axis=0, ddof=1)
        if np.any(scale <= 1e-12):
            fit["status"] = "residual_degenerate"
            return fit, [], []
        weights = np.corrcoef(residuals, rowvar=False)
        return self._finish_graph(decision, fit, symbols, weights, nx, "correlation")


def _iso_day(value):
    """Return an ISO date string for an ISO date or date object."""
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat() if len(value) == 10 else None
        except ValueError:
            return None
    return None


def _finite(value):
    """Return a finite float or None without accepting booleans."""
    return float(value) if number_ok(value) else None

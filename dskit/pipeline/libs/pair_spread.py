"""Prior-window statistics for a difference between two contemporaneous panel values.

The relationship selector supplies edges, or a fixed symbol list selects every pair. History
uses the immediately preceding panel sessions, including dates on which an edge was inactive.
A missing pair value within that fixed window refuses the signal; it is never skipped. A diagnostic
at date t uses the value at t but its mean and standard deviation use only dates before t.
Signals describe direction after t's value is known; a downstream execution node must choose
a later executable quote. They do not imply option P&L or fills.
"""

from bisect import bisect_left
from collections import Counter
from datetime import date
from itertools import combinations
from statistics import fmean, stdev

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "RollingPairSpread"]
NODE_KINDS = ()


class RollingPairSpread(Node):
    """Compute a selected pair's current spread against prior paired observations.

    Parameters
    ----------
    params : dict
        ``select_mode`` is ``edges`` or ``all_pairs``. The latter requires ``symbols``.
        ``lookback`` is the number of immediately preceding panel dates (>= 2); ``entry_z``
        is a positive Z threshold. The date, symbol, value, optional target and surface-status
        field names can be supplied, as can the edge field names. Every prior date in the
        window needs both values; an incomplete window or zero scale refuses a Z.

    Examples
    --------
    Inspect the spread of a named panel pair::

        node = RollingPairSpread("pair", {"select_mode": "all_pairs",
            "symbols": ["AAA", "BBB"], "lookback": 20, "entry_z": 2.0,
            "value_field": "iv30"})
        out = node.run(ctx, {"surfaces": panel_rows})
        # -> out["records"] holds prior-window statistics and signal states
    """

    role = "transform"
    outputs = ("records", "summary")
    _PARAMS = (
        "select_mode",
        "symbols",
        "lookback",
        "entry_z",
        "date_field",
        "symbol_field",
        "value_field",
        "target_field",
        "target_value",
        "surface_status_field",
        "surface_ok_value",
        "edge_date_field",
        "edge_a_field",
        "edge_b_field",
        "end_before",
    )

    @classmethod
    def validate_params(cls, params):
        """Validate configuration before any rows are read.

        Parameters
        ----------
        params : dict
            Node configuration.

        Returns
        -------
        list of str
            Parameter problems, empty when valid.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        mode = params.get("select_mode")
        if mode not in ("edges", "all_pairs"):
            problems.append("select_mode must be 'edges' or 'all_pairs'")
        check_int_param(problems, "lookback", params.get("lookback"), ge=2)
        threshold = params.get("entry_z")
        if not number_ok(threshold) or threshold <= 0:
            problems.append("entry_z must be a finite number > 0")
        if mode == "all_pairs":
            symbols = params.get("symbols")
            if (
                not isinstance(symbols, list)
                or len(symbols) < 2
                or any(not isinstance(s, str) or not s for s in symbols)
                or len(set(symbols)) != len(symbols)
            ):
                problems.append(
                    "symbols must contain at least two distinct non-empty strings"
                )
        elif "symbols" in params:
            problems.append("symbols is only valid for all_pairs")
        for name in (
            "date_field",
            "symbol_field",
            "value_field",
            "target_field",
            "surface_status_field",
            "edge_date_field",
            "edge_a_field",
            "edge_b_field",
        ):
            if name in params and (
                not isinstance(params[name], str) or not params[name]
            ):
                problems.append(f"{name} must be a non-empty string")
        if ("target_value" in params) != ("target_field" in params):
            problems.append("target_value and target_field must be declared together")
        if "surface_ok_value" in params and "surface_status_field" not in params:
            problems.append("surface_ok_value needs surface_status_field")
        if "end_before" in params:
            try:
                value = params["end_before"]
                if (
                    not isinstance(value, str)
                    or date.fromisoformat(value).isoformat() != value
                ):
                    raise ValueError
            except ValueError:
                problems.append("end_before must be an ISO date")
        return problems

    def run(self, ctx, inputs):
        """Build historical spreads, then score only selected date-pairs.

        Parameters
        ----------
        ctx : NodeContext
            Unused execution context.
        inputs : dict
            ``surfaces`` is a panel of date/symbol/value rows; ``edges`` is required in
            edges mode and absent in all_pairs mode.

        Returns
        -------
        dict
            ``records`` contains one selected pair/date row, and ``summary`` counts statuses.

        Raises
        ------
        ValueError
            A required input is absent, a key repeats, or a date/pair is malformed.
        """
        p = self.params
        if "surfaces" not in inputs:
            raise ValueError(f"{self.key}: surfaces input is required")
        if p["select_mode"] == "edges" and "edges" not in inputs:
            raise ValueError(f"{self.key}: edges input is required in edges mode")
        if p["select_mode"] == "all_pairs" and "edges" in inputs:
            raise ValueError(f"{self.key}: edges input is not used in all_pairs mode")
        day_field = p.get("date_field", "date")
        symbol_field = p.get("symbol_field", "symbol")
        value_field = p.get("value_field", "value")
        panel = {}
        days = set()
        excluded = 0
        for row in inputs["surfaces"]:
            day = self._day(row.get(day_field))
            symbol = row.get(symbol_field)
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(f"{self.key}: surface symbol must be non-empty")
            if "target_field" in p and row.get(p["target_field"]) != p.get(
                "target_value"
            ):
                continue
            key = day, symbol
            if key in panel:
                raise ValueError(f"{self.key}: duplicate surface key {key!r}")
            days.add(day)
            value = row.get(value_field)
            good_status = "surface_status_field" not in p or row.get(
                p["surface_status_field"]
            ) == p.get("surface_ok_value", "ok")
            if not good_status or not number_ok(value):
                panel[key] = None
                excluded += 1
            else:
                panel[key] = float(value)
        selected = self._selected(inputs, days)
        ordered_days = sorted(days)
        out = []
        counts = Counter()
        for day, a, b, provenance in selected:
            av, bv = panel.get((day, a)), panel.get((day, b))
            record = {
                "date": day,
                "symbol_a": a,
                "symbol_b": b,
                "fit_date": provenance.get("fit_date"),
                "trained_through": provenance.get("trained_through"),
                "spread": None,
                "mean": None,
                "std": None,
                "z": None,
                "signal": "none",
                "status": "missing_surface",
                "n_history": 0,
            }
            if av is not None and bv is not None:
                record["spread"] = av - bv
                cutoff = bisect_left(ordered_days, day)
                prior_days = ordered_days[max(0, cutoff - p["lookback"]) : cutoff]
                prior = [
                    panel[(prior_day, a)] - panel[(prior_day, b)]
                    for prior_day in prior_days
                    if panel.get((prior_day, a)) is not None
                    and panel.get((prior_day, b)) is not None
                ]
                record["n_history"] = len(prior)
                if len(prior_days) < p["lookback"]:
                    record["status"] = "warmup"
                elif len(prior) < p["lookback"]:
                    record["status"] = "incomplete_window"
                else:
                    record["mean"] = fmean(prior)
                    record["std"] = stdev(prior)
                    if record["std"] == 0:
                        record["status"] = "zero_std"
                    else:
                        z = (record["spread"] - record["mean"]) / record["std"]
                        record["z"] = z
                        record["status"] = "ok"
                        record["signal"] = (
                            "short_a_long_b"
                            if z > p["entry_z"]
                            else "long_a_short_b"
                            if z < -p["entry_z"]
                            else "flat"
                        )
            out.append(record)
            counts[record["status"]] += 1
        return {
            "records": out,
            "summary": {
                "selected": len(out),
                "surface_rows": len(panel),
                "excluded_surface_rows": excluded,
                "status": dict(counts),
            },
        }

    def _selected(self, inputs, days):
        """Return unique ordered (date, a, b, provenance) selections."""
        p = self.params
        if p["select_mode"] == "all_pairs":
            pairs = list(combinations(sorted(p["symbols"]), 2))
            return [(day, a, b, {}) for day in sorted(days) for a, b in pairs]
        out = {}
        for row in inputs["edges"]:
            day = self._day(row.get(p.get("edge_date_field", "date")))
            a, b = (
                row.get(p.get("edge_a_field", "symbol_a")),
                row.get(p.get("edge_b_field", "symbol_b")),
            )
            if (
                not isinstance(a, str)
                or not isinstance(b, str)
                or not a
                or not b
                or a == b
            ):
                raise ValueError(
                    f"{self.key}: edge needs two distinct non-empty symbols"
                )
            key = (day, *sorted((a, b)))
            if key in out:
                raise ValueError(f"{self.key}: duplicate edge key {key!r}")
            fit_date = row.get("fit_date")
            trained_through = row.get("trained_through")
            if fit_date is not None and self._day(fit_date) > day:
                raise ValueError(f"{self.key}: edge fit_date is after decision date")
            if trained_through is not None and self._day(trained_through) >= day:
                raise ValueError(
                    f"{self.key}: edge trained_through reaches decision date"
                )
            out[key] = {"fit_date": fit_date, "trained_through": trained_through}
        return [(day, a, b, out[day, a, b]) for day, a, b in sorted(out)]

    def _day(self, value):
        """Parse a date and refuse the configured upper bound."""
        try:
            if (
                not isinstance(value, str)
                or date.fromisoformat(value).isoformat() != value
            ):
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"{self.key}: invalid ISO date {value!r}") from exc
        if "end_before" in self.params and value >= self.params["end_before"]:
            raise ValueError(f"{self.key}: date {value} reaches end_before")
        return value

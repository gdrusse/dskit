"""Score an earlier hold forecast against a later position snapshot (ADR-0279 Stage 4).

A hold-vs-redeploy decision is a forecast: the position was expected to grow
from its value then to ``hold_value`` over ``horizon_years``. The only honest
test of that forecast is the position's later price, and this module owns
exactly that join: for each decided symbol that still appears in a later
snapshot, the pro-rated projected growth for the time elapsed,
``(hold_value / value) ** (elapsed_years / horizon_years)``, against the
realized per-share growth, ``(later value / later quantity) / (value /
quantity)``. Per-share, so a purchase or a sale between the two dates is not
mistaken for a return; without a declared quantity field the value itself is
the measure. ``surprise`` is realized minus projected.

Only the hold side is scored here. The active side is scored where it runs
— a child's own production ledger — never by this node.

:class:`DecisionOutcomes` (role ``transform``) is wired by import path. A
decided symbol absent from the later snapshot is listed as unmatched, not
scored; a later snapshot carrying several dates refuses, because one
observation date is what makes the elapsed time a single number.

Import cost: stdlib only.
"""

from __future__ import annotations

from datetime import date

from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["DecisionOutcomes"]

_DAYS_PER_YEAR = 365.25


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


class DecisionOutcomes(Node):
    """Projected vs realized per-share growth for every decided symbol seen later.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``symbol_field``, ``as_of_field``, ``value_field`` (field names,
        REQUIRED), shared by both inputs; ``quantity_field`` (field name,
        optional): the share count on both inputs, making growth per share.

    Inputs
    ------
    ``decisions`` (REQUIRED)
        Rows written by :class:`~dskit.pipeline.liquidation.HoldVsRedeploy`:
        the three fields plus ``hold_value``, ``horizon_years``, ``decision``.
    ``positions`` (REQUIRED)
        A later snapshot, one row per symbol, all on one ``as_of``.

    Examples
    --------
    One year into a five-year forecast::

        node = DecisionOutcomes("outcomes", {"symbol_field": "symbol",
            "as_of_field": "as_of", "value_field": "market_value",
            "quantity_field": "long_quantity"})
        out = node.run(ctx, {"decisions": decided, "positions": later})
        out["records"][0]["surprise"]
    """

    role = "transform"
    outputs = ("records", "summary")

    _PARAMS = ("symbol_field", "as_of_field", "value_field", "quantity_field")
    _REQUIRED = ("symbol_field", "as_of_field", "value_field")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        for name in cls._PARAMS:
            if name in params and not _name_ok(params[name]):
                problems.append(f"{name} must be a non-empty field name, got {params[name]!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _number(self, what, index, row, field):
        """Return the number ``row[field]`` holds; refuse by input, index and field."""
        value = row.get(field)
        if not number_ok(value):
            raise ValueError(
                f"{self.key}: {what} {index} field {field!r} is not a number, got {value!r}"
            )
        return float(value)

    def _per_unit(self, what, index, row):
        """Return the row's measure: value per share when a quantity field is declared."""
        value = self._number(what, index, row, self.params["value_field"])
        field = self.params.get("quantity_field")
        if field is None:
            return value
        quantity = self._number(what, index, row, field)
        if quantity == 0.0:
            raise ValueError(f"{self.key}: {what} {index} has zero {field!r}")
        return value / quantity

    def _later(self, positions):
        """Return ``(as_of, {symbol: measure})`` from the later snapshot; refuse several dates."""
        if not isinstance(positions, list):
            raise ValueError(f"{self.key}: positions must be a list")
        symbol_field, as_of_field = self.params["symbol_field"], self.params["as_of_field"]
        dates, measures = set(), {}
        for index, row in enumerate(positions):
            if not isinstance(row, dict) or not _name_ok(row.get(symbol_field)):
                raise ValueError(f"{self.key}: position {index} has no symbol in {symbol_field!r}")
            day = _iso_date(row.get(as_of_field))
            if day is None:
                raise ValueError(
                    f"{self.key}: position {index} field {as_of_field!r} is not an ISO date"
                )
            dates.add(day)
            symbol = row[symbol_field]
            if symbol in measures:
                raise ValueError(f"{self.key}: positions carry symbol {symbol!r} several times")
            measures[symbol] = self._per_unit("position", index, row)
        if len(dates) != 1:
            raise ValueError(
                f"{self.key}: positions must carry one as_of, got several: "
                f"{sorted(d.isoformat() for d in dates)}"
            )
        return dates.pop(), measures

    def run(self, ctx, inputs):
        """Return one scored row per matched symbol, and a summary with the unmatched."""
        decisions = inputs.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError(f"{self.key}: decisions must be a list")
        later_day, later = self._later(inputs.get("positions"))
        symbol_field, as_of_field = self.params["symbol_field"], self.params["as_of_field"]
        out, unmatched, by_decision = [], [], {}
        for index, row in enumerate(decisions):
            if not isinstance(row, dict) or not _name_ok(row.get(symbol_field)):
                raise ValueError(f"{self.key}: decision {index} has no symbol in {symbol_field!r}")
            symbol = row[symbol_field]
            decided = _iso_date(row.get(as_of_field))
            if decided is None:
                raise ValueError(
                    f"{self.key}: decision {index} field {as_of_field!r} is not an ISO date"
                )
            if later_day <= decided:
                raise ValueError(
                    f"{self.key}: decision {index} dated {decided} is not before the "
                    f"positions as_of {later_day}"
                )
            if symbol not in later:
                unmatched.append(symbol)
                continue
            horizon = row.get("horizon_years")
            if not number_ok(horizon) or horizon <= 0:
                raise ValueError(
                    f"{self.key}: decision {index} field 'horizon_years' is not a positive "
                    f"number, got {horizon!r}"
                )
            then = self._per_unit("decision", index, row)
            value = self._number("decision", index, row, self.params["value_field"])
            hold_value = self._number("decision", index, row, "hold_value")
            elapsed = (later_day - decided).days / _DAYS_PER_YEAR
            projected = (hold_value / value) ** (elapsed / float(horizon))
            realized = later[symbol] / then
            surprise = realized - projected
            decision = row.get("decision")
            out.append({
                symbol_field: symbol, "decided_as_of": decided.isoformat(),
                "observed_as_of": later_day.isoformat(), "elapsed_years": elapsed,
                "projected_growth": projected, "realized_growth": realized,
                "surprise": surprise, "decision": decision,
            })
            bucket = by_decision.setdefault(str(decision), {"count": 0, "sum": 0.0})
            bucket["count"] += 1
            bucket["sum"] += surprise
        summary_by = {
            key: {"count": b["count"], "mean_surprise": b["sum"] / b["count"]}
            for key, b in sorted(by_decision.items())
        }
        mean = sum(r["surprise"] for r in out) / len(out) if out else None
        self.log.info("%s: scored %d, unmatched %d, mean surprise %s",
                      self.key, len(out), len(unmatched), mean)
        return {"records": out, "summary": {
            "stage": "decision outcomes", "scored": len(out),
            "observed_as_of": later_day.isoformat(), "unmatched": sorted(unmatched),
            "mean_surprise": mean, "by_decision": summary_by}}

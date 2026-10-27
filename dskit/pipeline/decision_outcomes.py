"""Score an earlier hold forecast against a later position snapshot (ADR-0279 Stage 4).

A hold-vs-redeploy decision is a forecast: the position was expected to grow
from its value then to ``hold_value`` over ``horizon_years``. The only honest
test of that forecast is the position's later price, and this module owns
exactly that join, on the declared ``key_fields`` (an account and a symbol,
say — one symbol held in two accounts is two positions): for each decided key
that still appears in a later snapshot, the pro-rated projected growth for the
time elapsed against the realized per-share growth, ``(later value / later
quantity) / (value / quantity)``. Per-share, so a purchase or a sale between
the two dates is not mistaken for a return; without a declared quantity field
the value itself is the measure. ``surprise`` is realized minus projected.

The two sides are not the same kind of return. ``hold_value`` is a TOTAL
return forecast (price plus income), while a position's market value moves with
price only. So the projection removes the income first, per year:

    g = (hold_value / value) ** (1 / horizon_years)      total growth a year
    p = g - income_rate                                  price growth a year
    projected = p ** elapsed_years

``income_rate`` is the annual income component of the hold forecast; the
document sets it to the market dividend yield it fed the forecast. This is an
approximation: every holding is assumed to pay that same yield (beta scaling of
income is ignored), and income is subtracted from the growth FACTOR rather than
compounded separately. A year whose ``p`` is not above zero refuses, because a
non-positive base has no growth path.

Only the hold side is scored here. The active side is scored where it runs
— a child's own production ledger — never by this node.

:class:`DecisionOutcomes` (role ``transform``) is wired by import path. A
decided key absent from the later snapshot is listed as unmatched, not
scored; a later snapshot carrying several dates refuses, because one
observation date is what makes the elapsed time a single number, as does one
carrying a key twice. Those structural refusals cover EVERY later row; the
value and quantity checks cover only the rows a decision matches, so a short, a
closed lot or an option nobody decided on is simply not scored and never stops
the run (the document filters the universe; this node does not need it to).

Limits, disclosed rather than modelled. Growth is per share, so a stock split,
reverse split or spin-off between the two dates changes the share count without
a purchase and is read as a return (a 2-for-1 split looks like a 50% loss); none
is detected or adjusted here. The income removed from the projection is one
yield for every holding, and no tax is applied to either side.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

from datetime import date

from dskit.pipeline.binary_curve import name_ok
from dskit.pipeline.document import date_problem
from dskit.pipeline.liquidation import require_number
from dskit.pipeline.node import Node, check_number_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["DecisionOutcomes"]

_DAYS_PER_YEAR = 365.25


class DecisionOutcomes(Node):
    """Projected vs realized per-share growth for every decided position seen later.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``key_fields`` (non-empty list of distinct field names, REQUIRED):
        what identifies one position on both inputs; ``as_of_field``,
        ``value_field`` (field names, REQUIRED), shared by both inputs;
        ``quantity_field`` (field name, optional): the share count on both
        inputs, making growth per share; ``income_rate`` (number in
        ``[0, 1]``, REQUIRED): the annual income component of the hold
        forecast, removed from the projected growth (see the module docstring).

    Inputs
    ------
    ``decisions`` (REQUIRED)
        Rows written by :class:`~dskit.pipeline.liquidation.HoldVsRedeploy`:
        the fields above plus ``hold_value``, ``horizon_years``, ``decision``.
    ``positions`` (REQUIRED)
        A later snapshot, one row per key, all on one ``as_of``. Only the rows
        a decision matches need a usable value and quantity.

    Examples
    --------
    One year into a five-year forecast::

        node = DecisionOutcomes("outcomes", {"key_fields": ["account", "symbol"],
            "as_of_field": "as_of", "value_field": "market_value",
            "quantity_field": "long_quantity", "income_rate": 0.013})
        out = node.run(ctx, {"decisions": decided, "positions": later})
        out["records"][0]["surprise"]
    """

    role = "transform"
    outputs = ("records", "summary")

    _PARAMS = ("key_fields", "as_of_field", "value_field", "quantity_field", "income_rate")
    _REQUIRED = ("key_fields", "as_of_field", "value_field", "income_rate")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        keys = params.get("key_fields")
        if "key_fields" in params and (
            not isinstance(keys, list) or not keys or not all(name_ok(k) for k in keys)
            or len(set(keys)) != len(keys)
        ):
            problems.append(
                f"key_fields must be a non-empty list of distinct field names, got {keys!r}"
            )
        for name in ("as_of_field", "value_field", "quantity_field"):
            if name in params and not name_ok(params[name]):
                problems.append(f"{name} must be a non-empty field name, got {params[name]!r}")
        if "income_rate" in params:
            check_number_param(problems, "income_rate", params["income_rate"], lower=0.0, upper=1.0)
        return problems

    def _key(self, what, index, row):
        """Return the row's key tuple; refuse a row missing any key field."""
        if not isinstance(row, dict):
            raise ValueError(f"{self.key}: {what} {index} must be a mapping, got {type(row).__name__}")
        key = []
        for field in self.params["key_fields"]:
            value = row.get(field)
            if value is None or isinstance(value, (dict, list)):
                raise ValueError(f"{self.key}: {what} {index} has no usable key field {field!r}")
            key.append(value)
        return tuple(key)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _per_unit(self, what, index, row):
        """Return the row's measure: value per share when a quantity field is declared."""
        value = require_number(self.key, what, index, row, self.params["value_field"])
        field = self.params.get("quantity_field")
        if field is None:
            return value
        quantity = require_number(self.key, what, index, row, field)
        if quantity == 0.0:
            raise ValueError(f"{self.key}: {what} {index} has zero {field!r}")
        return value / quantity

    def _positive(self, what, index, value, label):
        """Return ``value`` when it is above zero; a growth ratio needs a positive start."""
        if value <= 0.0:
            raise ValueError(f"{self.key}: {what} {index} has non-positive {label} {value!r}")
        return value

    def _later(self, positions):
        """Return ``(as_of, {key: (index, row)})`` from the later snapshot.

        Refuses several dates, an undated row, a missing key field and a repeated key
        over EVERY row; the rows' values are read later, only for the keys a decision matches.
        """
        if not isinstance(positions, list):
            raise ValueError(f"{self.key}: positions must be a list")
        as_of_field = self.params["as_of_field"]
        dates, rows = set(), {}
        for index, row in enumerate(positions):
            key = self._key("position", index, row)
            if date_problem(row.get(as_of_field)):
                raise ValueError(
                    f"{self.key}: position {index} field {as_of_field!r} is not an ISO date"
                )
            dates.add(date.fromisoformat(row[as_of_field]))
            if key in rows:
                raise ValueError(f"{self.key}: positions carry key {key!r} several times")
            rows[key] = (index, row)
        if len(dates) != 1:
            raise ValueError(
                f"{self.key}: positions must carry one as_of, got several: "
                f"{sorted(d.isoformat() for d in dates)}"
            )
        return dates.pop(), rows

    def run(self, ctx, inputs):
        """Return one scored row per matched symbol, and a summary with the unmatched."""
        decisions = inputs.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError(f"{self.key}: decisions must be a list")
        later_day, later = self._later(inputs.get("positions"))
        as_of_field, key_fields = self.params["as_of_field"], self.params["key_fields"]
        out, unmatched, by_decision, seen = [], [], {}, set()
        for index, row in enumerate(decisions):
            key = self._key("decision", index, row)
            if key in seen:
                raise ValueError(f"{self.key}: decisions carry key {key!r} several times")
            seen.add(key)
            if date_problem(row.get(as_of_field)):
                raise ValueError(
                    f"{self.key}: decision {index} field {as_of_field!r} is not an ISO date"
                )
            decided = date.fromisoformat(row[as_of_field])
            if later_day <= decided:
                raise ValueError(
                    f"{self.key}: decision {index} dated {decided} is not before the "
                    f"positions as_of {later_day}"
                )
            if key not in later:
                unmatched.append(key)
                continue
            horizon = row.get("horizon_years")
            if not number_ok(horizon) or horizon <= 0:
                raise ValueError(
                    f"{self.key}: decision {index} field 'horizon_years' is not a positive "
                    f"number, got {horizon!r}"
                )
            then = self._positive("decision", index, self._per_unit("decision", index, row),
                                  "measure")
            value = self._positive(
                "decision", index,
                require_number(self.key, "decision", index, row, self.params["value_field"]),
                self.params["value_field"])
            hold_value = self._positive(
                "decision", index,
                require_number(self.key, "decision", index, row, "hold_value"), "hold_value")
            elapsed = (later_day - decided).days / _DAYS_PER_YEAR
            income = float(self.params["income_rate"])
            total_growth = (hold_value / value) ** (1.0 / float(horizon))
            price_growth = total_growth - income
            if price_growth <= 0.0:
                raise ValueError(
                    f"{self.key}: decision {index} income_rate {income!r} leaves a price growth "
                    f"factor of {price_growth!r} a year from a total of {total_growth!r}; "
                    "a non-positive base has no growth path"
                )
            projected = price_growth ** elapsed
            position_index, position = later[key]
            realized = self._per_unit("position", position_index, position) / then
            surprise = realized - projected
            decision = row.get("decision")
            if decision not in ("hold", "exit"):
                raise ValueError(
                    f"{self.key}: decision {index} field 'decision' must be hold or exit, "
                    f"got {decision!r}"
                )
            out.append({
                **dict(zip(key_fields, key)), "decided_as_of": decided.isoformat(),
                "observed_as_of": later_day.isoformat(), "elapsed_years": elapsed,
                "projected_growth": projected, "realized_growth": realized,
                "surprise": surprise, "decision": decision,
            })
            bucket = by_decision.setdefault(decision, {"count": 0, "sum": 0.0})
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
            "observed_as_of": later_day.isoformat(),
            "unmatched": [dict(zip(key_fields, key)) for key in sorted(unmatched, key=repr)],
            "mean_surprise": mean, "by_decision": summary_by}}

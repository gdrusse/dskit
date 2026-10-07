"""Kalshi's taker fee: the venue's mapping from a ``fee_schedules`` row to a venue-neutral fee model.

dskit owns the MECHANICS (:mod:`dskit.pipeline.fee_mechanics`): a per-order rule such as
``rate * C * P * (1 - P)`` and a rounding policy that bills the ORDER on a grid. It carries no
venue name, no rate and no table of fee types. This module owns the other half, which is Kalshi's:
which of its fee types means which mechanic, what each type's base rate is, and the schedule
vocabulary (``fee_type``, ``fee_multiplier``) the ``kalshi`` pack's ``fee_schedules`` stream uses.
All of it is the node's ``fee_types`` param, so a second venue (or a changed Kalshi schedule) is a
config, never an edit here.

The rule it feeds, for the quadratic type (Kalshi's published schedule): the fee of ONE ORDER of
``C`` contracts at price ``P`` dollars is the exact ``rate * C * P * (1 - P)`` rounded UP to the next
cent. Rounding applies to the order, so the fee per contract depends on ``C``: one contract at a
coin-flip pays 2 cents for a 1.75-cent fee, 100 contracts pay 1.75 dollars (a cent value is snapped
to nine places before the ceiling, by the mechanic, because binary floating point cannot hold
``0.07 * 100 * 0.25 * 100`` exactly).

The ``fee_schedules`` stream gives each series a ``fee_type`` and a ``fee_multiplier``; the
document maps each type to its base rate and its mechanic, and the rate is ``base * multiplier``.
A type the document does not name is marked, never priced. The stream holds only the CURRENT
schedule, so a historical market is priced under today's; the row says which retrieval it used
(``fee_schedule_retrieved``). Makers pay nothing under ``quadratic``; a type that charges them
would be a new mechanic in dskit, named here by config.

Import cost: stdlib + dskit.
"""

from dskit.pipeline.fee_mechanics import fee_model_from_spec, fee_spec_problems
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

from . import fields as f
from .ports import ListPortsNode

__all__ = ["FeeColumns"]

_TYPE_KEYS = ("base_rate", "model")


class FeeColumns(ListPortsNode):
    """Add taker-fee columns to each row from the ``fee_schedules`` stream (role ``transform``).

    Inputs: ``records`` (rows with ``series``, ``yes_bid``, ``yes_ask``) and ``schedules``
    (the :class:`~crypto_trading.kalshi_rows.FeeRows` records; the latest retrieval per
    series wins). Output ``records``: every row plus ``fee_rate``, ``fee_buy_yes`` (per
    contract, buying YES at the ask), ``fee_buy_no`` (per contract, buying NO at
    ``1 - bid``), ``fee_status`` and ``fee_schedule_retrieved``. Statuses besides ``ok``:
    ``no_schedule``, ``unsupported_fee_type``, ``no_multiplier``, ``no_quote``.

    Parameters
    ----------
    params : dict
        ``fee_types`` (dict, REQUIRED) the venue's fee type -> ``{"base_rate": number >= 0,
        "model": <a fee spec for dskit's fee_model_from_spec: mechanic and rounding>}``; no
        other key (a ``notes`` inside would move the document's identity hash). ``contracts``
        (int >= 1, REQUIRED) the order size the per-order rounding is applied to.

    Examples
    --------
    Price a 100-contract taker order from the stream's schedule::

        node = FeeColumns("fees", {"contracts": 100, "fee_types": {"quadratic": {
            "base_rate": 0.07,
            "model": {"mechanic": "probability_quadratic",
                      "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}}}})
        out = node.run(ctx, {"records": rows, "schedules": schedule_rows})
        # -> out["records"][0]["fee_buy_yes"] is dollars per contract, e.g. 0.0173
    """

    role = "transform"
    outputs = ("records",)
    LIST_PORTS = ("records", "schedules")
    _PARAMS = ("fee_types", "contracts")

    @classmethod
    def _type_problems(cls, fee_type, entry):
        """List problems with one ``fee_types`` entry."""
        where = f"fee_types[{fee_type!r}]"
        if not isinstance(entry, dict) or set(entry) != set(_TYPE_KEYS):
            return [f"{where} must have exactly the keys {list(_TYPE_KEYS)}, got {entry!r}"]
        problems = []
        if not (number_ok(entry["base_rate"]) and entry["base_rate"] >= 0.0):
            problems.append(f"{where}.base_rate must be a number >= 0, got {entry['base_rate']!r}")
        problems += [f"{where}.model: {p}" for p in fee_spec_problems(entry["model"])]
        return problems

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        types = params.get("fee_types")
        if not isinstance(types, dict) or not types:
            problems.append(f"fee_types is required: a map fee type -> {{base_rate, model}}, got {types!r}")
        else:
            for fee_type, entry in types.items():
                problems += cls._type_problems(fee_type, entry)
        if "contracts" not in params:
            problems.append("contracts is required: the order size the fee rounding applies to")
        else:
            check_int_param(problems, "contracts", params["contracts"], ge=1)
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the columns are a function of the row, the schedules and the params.

        Parameters
        ----------
        params : dict
            Unused.
        verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def run(self, ctx, inputs):
        """Add the fee columns to every row.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records`` and ``schedules``.

        Returns
        -------
        dict
            ``{"records": [...]}``, new dicts in input order.
        """
        latest = self._latest(inputs["schedules"])
        models = {t: fee_model_from_spec(e["model"]) for t, e in self.params["fee_types"].items()}
        records = [self._priced(dict(row), latest.get(row.get(f.SERIES)), models) for row in inputs["records"]]
        self.log.info("priced fees on %d row(s)", len(records))
        return {"records": records}

    @staticmethod
    def _latest(schedules):
        """Map each series to its most recently retrieved schedule row."""
        latest = {}
        for row in schedules:
            held = latest.get(row[f.SERIES])
            if held is None or row[f.RETRIEVED_MS] > held[f.RETRIEVED_MS]:
                latest[row[f.SERIES]] = row
        return latest

    def _rate(self, schedule):
        """Return ``(rate, status)``: the effective rate, or None and why there is none."""
        if schedule is None:
            return None, "no_schedule"
        entry = self.params["fee_types"].get(schedule[f.FEE_TYPE])
        if entry is None:
            return None, "unsupported_fee_type"
        multiplier = schedule[f.FEE_MULTIPLIER]
        if not (number_ok(multiplier) and multiplier >= 0.0):
            return None, "no_multiplier"
        return entry["base_rate"] * multiplier, "ok"

    def _priced(self, row, schedule, models):
        """Write the fee columns onto ``row`` (a copy)."""
        rate, status = self._rate(schedule)
        bid, ask = row.get(f.YES_BID), row.get(f.YES_ASK)
        row.update({"fee_rate": rate, f.FEE_BUY_YES: None, f.FEE_BUY_NO: None,
                    "fee_schedule_retrieved": None if schedule is None else schedule[f.RETRIEVED]})
        if status == "ok" and not (number_ok(bid) and number_ok(ask)):
            status = "no_quote"
        if status == "ok":
            model, count = models[schedule[f.FEE_TYPE]], self.params["contracts"]
            row[f.FEE_BUY_YES] = model.order_fee(count, ask, rate) / count
            row[f.FEE_BUY_NO] = model.order_fee(count, 1.0 - bid, rate) / count
        row["fee_status"] = status
        return row

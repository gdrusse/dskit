"""Vendor Greeks and open interest summed per symbol-day from a daily option-chain stream (ADR-0273).

A vendor end-of-day chain row carries, beside the NBBO, the vendor's own Greeks
(``delta``, ``gamma``, ``vega``, ``vanna``, ...), its implied vol with a solver
residual, open interest, volume, a trade count and quote sizes.
:class:`ChainDayPositioning` reads those numbers as the vendor reports them and
sums them per (symbol, date) over the contracts inside a declared days-to-expiry
window and log-moneyness band, after dropping contracts whose vendor IV is not
positive, whose residual exceeds a cap, or whose open interest is missing. It
prices nothing, re-derives nothing, and holds one small tally per symbol-day.
``spot_power`` multiplies each contract's Greek by the vendor spot raised to a
declared power per Greek (gamma 2, delta 1 for dollar exposures), so a sum
survives a stock split, where per-share Greeks and contract counts both jump.

Outputs per symbol-day: ``oi_call``, ``oi_put``, ``oi_put_call_ratio``,
``volume_call``, ``volume_put``, ``volume_put_call_ratio``, ``count``,
``size_imbalance`` (bid sizes minus ask sizes), ``contracts``, ``excluded``,
``excluded_reasons``; and for every declared Greek ``g``: ``oi_<g>_call``,
``oi_<g>_put``, ``oi_<g>_net`` (call minus put, the dealer-positioning
convention for a Greek that is positive on both rights) and ``oi_<g>_sum``
(call plus put, the signed total for a Greek such as delta that the vendor
already signs); ``oi_vega_iv`` (the ``oi_vega_sum``-weighted vendor IV, when
``vega`` is declared). A symbol-day with no usable
contract is emitted with ``status`` ``no_contracts`` and ``None`` values.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "ChainDayPositioning"]

NODE_KINDS = ()

_STATUS_OK = "ok"
_STATUS_EMPTY = "no_contracts"


class _Tally:
    """One symbol-day's sums."""

    __slots__ = ("oi", "volume", "count", "bid_size", "ask_size", "contracts", "excluded",
                 "greeks", "vega_iv", "reasons")

    def __init__(self, greeks):
        self.oi = {"call": 0.0, "put": 0.0}
        self.volume = {"call": 0.0, "put": 0.0}
        self.count = 0.0
        self.bid_size = 0.0
        self.ask_size = 0.0
        self.contracts = 0
        self.excluded = 0
        self.greeks = {g: {"call": 0.0, "put": 0.0} for g in greeks}
        self.vega_iv = 0.0
        self.reasons = Counter()


class ChainDayPositioning(Node):
    """Sum vendor Greeks, open interest, volume and sizes per symbol-day (role ``transform``).

    Parameters
    ----------
    params : dict
        Row vocabulary (REQUIRED): ``symbol_field``, ``date_field``, ``expiration_field``,
        ``strike_field``, ``right_field``, ``call_values``, ``put_values`` (disjoint non-empty
        lists), ``open_interest_field``, ``volume_field``, ``count_field``, ``bid_size_field``,
        ``ask_size_field``, ``underlying_price_field``, ``iv_field``, ``iv_error_field``,
        ``greek_fields`` (non-empty distinct list of vendor Greek columns); optional ``spot_power``
        (``{greek: int >= 0}`` over declared Greeks, default none, 0 means unscaled). Window (REQUIRED):
        ``end_before`` (ISO date; a row at or after it refuses), ``min_dte`` and ``max_dte``
        (ints >= 1, calendar days to expiry, inclusive), ``moneyness_band`` (> 0, the largest
        ``|log(strike / underlying)|`` kept), ``iv_error_max`` (>= 0, the largest ``|iv_error|``
        kept). ``status_field`` (REQUIRED output name).

    Examples
    --------
    Net dealer-style gamma and vega per day inside two months and 15% of spot::

        node = ChainDayPositioning("pos", {..., "greek_fields": ["gamma", "vega"],
                                           "min_dte": 1, "max_dte": 60, "moneyness_band": 0.15,
                                           "iv_error_max": 0.01, "end_before": "2026-01-01",
                                           "status_field": "status"})
        rows = node.run(ctx, {"records": chain_rows})["records"]
    """

    role = "transform"
    outputs = ("records", "summary")

    _ROWS = ("symbol_field", "date_field", "expiration_field", "strike_field", "right_field",
             "open_interest_field", "volume_field", "count_field", "bid_size_field", "ask_size_field",
             "underlying_price_field", "iv_field", "iv_error_field")
    _PARAMS = (*_ROWS, "call_values", "put_values", "greek_fields", "spot_power", "end_before", "min_dte",
               "max_dte", "moneyness_band", "iv_error_max", "status_field")
    _REQUIRED = tuple(name for name in _PARAMS if name != "spot_power")

    @classmethod
    def validate_params(cls, params):
        """Default-deny: refuse unknown, missing and malformed knobs."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            if name not in params:
                problems.append(f"{name} is required")
        for name in (*cls._ROWS, "status_field"):
            if name in params and (not isinstance(params[name], str) or not params[name]):
                problems.append(f"{name} must be a non-empty string")
        rights = {}
        for name in ("call_values", "put_values"):
            value = params.get(name)
            if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v for v in value):
                problems.append(f"{name} must be a non-empty list of strings")
            else:
                rights[name] = set(value)
        if len(rights) == 2 and rights["call_values"] & rights["put_values"]:
            problems.append("call_values and put_values must be disjoint")
        greeks = params.get("greek_fields")
        if (not isinstance(greeks, list) or not greeks
                or any(not isinstance(g, str) or not g for g in greeks) or len(set(greeks)) != len(greeks)):
            problems.append("greek_fields must be a non-empty list of distinct column names")
        powers = params.get("spot_power", {})
        if (not isinstance(powers, dict)
                or any(type(v) is not int or v < 0 for v in powers.values())
                or (isinstance(greeks, list) and any(k not in greeks for k in powers))):
            problems.append("spot_power must map declared greek_fields to integers >= 0")
        if "end_before" in params:
            try:
                if date.fromisoformat(params["end_before"]).isoformat() != params["end_before"]:
                    raise ValueError
            except (TypeError, ValueError):
                problems.append("end_before must be an ISO YYYY-MM-DD date")
        for name in ("min_dte", "max_dte"):
            if name in params:
                check_int_param(problems, name, params[name], ge=1)
        if ("min_dte" in params and "max_dte" in params and type(params["min_dte"]) is int
                and type(params["max_dte"]) is int and params["min_dte"] > params["max_dte"]):
            problems.append("min_dte must not exceed max_dte")
        band = params.get("moneyness_band")
        if isinstance(band, bool) or not isinstance(band, (int, float)) or not band > 0:
            problems.append(f"moneyness_band must be a positive number, got {band!r}")
        cap = params.get("iv_error_max")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 0:
            problems.append(f"iv_error_max must be a non-negative number, got {cap!r}")
        return problems

    def run(self, ctx, inputs):
        """Tally one pass over the rows, then emit one record per symbol-day.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: an iterable of contract rows, read once.

        Returns
        -------
        dict
            ``records`` (sorted by symbol then date) and ``summary`` (row, symbol-day and
            exclusion counts).

        Raises
        ------
        ValueError
            A row at or after ``end_before``, a malformed date, or an unknown right.
        """
        p = self.params
        if "records" not in inputs:
            raise ValueError(f"{self.key}: records input is required")
        calls, puts = set(p["call_values"]), set(p["put_values"])
        greeks = list(p["greek_fields"])
        powers = {g: int(p.get("spot_power", {}).get(g, 0)) for g in greeks}
        end_before = p["end_before"]
        tallies = {}
        rows = 0
        for row in inputs["records"]:
            rows += 1
            day = self._iso(row.get(p["date_field"]), "date")
            if day >= end_before:
                raise ValueError(f"{self.key}: row dated {day} reaches end_before {end_before}")
            symbol = row.get(p["symbol_field"])
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(f"{self.key}: symbol must be a non-empty string")
            right = row.get(p["right_field"])
            if right in calls:
                side = "call"
            elif right in puts:
                side = "put"
            else:
                raise ValueError(f"{self.key}: unknown right {right!r}")
            tally = tallies.get((symbol, day))
            if tally is None:
                tally = tallies[(symbol, day)] = _Tally(greeks)
            reason = self._exclusion(row, day)
            if reason is not None:
                tally.excluded += 1
                tally.reasons[reason] += 1
                continue
            oi = float(row[p["open_interest_field"]])
            tally.contracts += 1
            tally.oi[side] += oi
            tally.volume[side] += self._number(row.get(p["volume_field"]))
            tally.count += self._number(row.get(p["count_field"]))
            tally.bid_size += self._number(row.get(p["bid_size_field"]))
            tally.ask_size += self._number(row.get(p["ask_size_field"]))
            spot = float(row[p["underlying_price_field"]])
            for g in greeks:
                tally.greeks[g][side] += oi * self._number(row.get(g)) * spot ** powers[g]
            if "vega" in tally.greeks:
                tally.vega_iv += (oi * self._number(row.get("vega")) * spot ** powers["vega"]
                                  * float(row[p["iv_field"]]))
        out = [self._record(symbol, day, tally, greeks) for (symbol, day), tally in sorted(tallies.items())]
        reasons = Counter()
        for tally in tallies.values():
            reasons.update(tally.reasons)
        return {
            "records": out,
            "summary": {"rows": rows, "symbol_days": len(out),
                        "status": dict(Counter(r[p["status_field"]] for r in out)),
                        "excluded": sum(t.excluded for t in tallies.values()),
                        "excluded_reasons": dict(reasons)},
        }

    def _exclusion(self, row, day):
        """Return why this contract is left out of the sums, or ``None`` when it is kept."""
        p = self.params
        expiry = self._iso(row.get(p["expiration_field"]), "expiration")
        dte = (date.fromisoformat(expiry) - date.fromisoformat(day)).days
        if dte < p["min_dte"] or dte > p["max_dte"]:
            return "dte"
        strike, spot = row.get(p["strike_field"]), row.get(p["underlying_price_field"])
        if not number_ok(strike) or not number_ok(spot) or strike <= 0 or spot <= 0:
            return "moneyness"
        if abs(math.log(float(strike) / float(spot))) > p["moneyness_band"]:
            return "moneyness"
        iv = row.get(p["iv_field"])
        if not number_ok(iv) or float(iv) <= 0.0:
            return "iv"
        error = row.get(p["iv_error_field"])
        if not number_ok(error) or abs(float(error)) > p["iv_error_max"]:
            return "iv_error"
        if not number_ok(row.get(p["open_interest_field"])):
            return "open_interest"
        return None

    @staticmethod
    def _number(value):
        """Return a finite number as float, else zero (a missing size or volume adds nothing)."""
        return float(value) if number_ok(value) else 0.0

    def _iso(self, value, what):
        """Return an ISO date string, refusing anything else."""
        text = value.isoformat() if isinstance(value, date) else value
        try:
            if not isinstance(text, str) or date.fromisoformat(text).isoformat() != text:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"{self.key}: invalid ISO {what} {value!r}") from exc
        return text

    def _record(self, symbol, day, tally, greeks):
        """One output row from a tally."""
        p = self.params
        empty = tally.contracts == 0
        oi_call, oi_put = tally.oi["call"], tally.oi["put"]
        record = {
            "symbol": symbol, "date": day,
            p["status_field"]: _STATUS_EMPTY if empty else _STATUS_OK,
            "contracts": tally.contracts, "excluded": tally.excluded,
            "excluded_reasons": dict(tally.reasons),
            "oi_call": None if empty else oi_call, "oi_put": None if empty else oi_put,
            "oi_put_call_ratio": (oi_put / oi_call) if not empty and oi_call > 0 else None,
            "volume_call": None if empty else tally.volume["call"],
            "volume_put": None if empty else tally.volume["put"],
            "volume_put_call_ratio": (tally.volume["put"] / tally.volume["call"])
            if not empty and tally.volume["call"] > 0 else None,
            "count": None if empty else tally.count,
            "size_imbalance": None if empty else tally.bid_size - tally.ask_size,
        }
        for g in greeks:
            call, put = tally.greeks[g]["call"], tally.greeks[g]["put"]
            record[f"oi_{g}_call"] = None if empty else call
            record[f"oi_{g}_put"] = None if empty else put
            record[f"oi_{g}_net"] = None if empty else call - put
            record[f"oi_{g}_sum"] = None if empty else call + put
        if "vega" in tally.greeks:
            oi_vega = tally.greeks["vega"]["call"] + tally.greeks["vega"]["put"]
            record["oi_vega_iv"] = (tally.vega_iv / oi_vega) if not empty and oi_vega > 0 else None
        return record

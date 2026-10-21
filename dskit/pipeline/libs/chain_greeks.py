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
``vega`` is declared); ``bid_size_sum`` and ``ask_size_sum`` beside the imbalance.
With ``profiles`` declared (ADR-0275), every half-open log-moneyness x days-to-expiry
bucket ``pf_m<i>_d<j>_`` also carries its open interest, volume and trade count per
right and the OI-weighted sums of the declared profile Greeks per right, so a
strike-by-expiry positioning profile around spot is one more set of columns, not a
second pass. A symbol-day with no usable contract is emitted with ``status``
``no_contracts`` and ``None`` values.

:class:`ChainDayDeltaSurface` (ADR-0275) reads the vendor's own delta and implied
vol: per (symbol, date) it takes the expiry nearest a declared target inside a
tolerance and, per right, linearly interpolates the vendor IV in absolute vendor
delta at declared delta points, plus the OI-vega-weighted vendor IV and the
OI-weighted theta-over-vega ratio (the implied carry) of that expiry. It prices
nothing and re-derives no Greek.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter
from datetime import date

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "ChainDayDeltaSurface", "ChainDayPositioning"]

NODE_KINDS = ()

_STATUS_OK = "ok"
_STATUS_EMPTY = "no_contracts"
_STATUS_NO_EXPIRY = "no_expiry"
_PROFILE_KEYS = ("moneyness_edges", "dte_edges", "greeks")


class _Tally:
    """One symbol-day's sums."""

    __slots__ = ("oi", "volume", "count", "bid_size", "ask_size", "contracts", "excluded",
                 "greeks", "vega_iv", "reasons", "profile")

    def __init__(self, greeks, buckets, profile_greeks):
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
        self.profile = {b: _Tally._bucket(profile_greeks) for b in buckets}

    @staticmethod
    def _bucket(profile_greeks):
        """One empty profile bucket."""
        return {"oi": {"call": 0.0, "put": 0.0}, "volume": {"call": 0.0, "put": 0.0}, "count": 0.0,
                "greeks": {g: {"call": 0.0, "put": 0.0} for g in profile_greeks}}


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
        kept). ``status_field`` (REQUIRED output name). Optional ``profiles``:
        ``{"moneyness_edges": [ascending floats, >= 2], "dte_edges": [ascending ints >= 1, >= 2],
        "greeks": [non-empty subset of greek_fields]}``; bucket ``m<i>`` is log-moneyness in
        ``[edge_i, edge_i+1)`` and ``d<j>`` days to expiry in ``[edge_j, edge_j+1)``; a contract
        outside every bucket still counts in the totals.

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
               "max_dte", "moneyness_band", "iv_error_max", "status_field", "profiles")
    _REQUIRED = tuple(name for name in _PARAMS if name not in ("spot_power", "profiles"))

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
        if "profiles" in params:
            cls._profile_problems(problems, params["profiles"], greeks if isinstance(greeks, list) else [])
        return problems

    @staticmethod
    def _profile_problems(problems, profiles, greeks):
        """Check the ``profiles`` block: two ascending edge lists and a subset of the declared Greeks."""
        if not isinstance(profiles, dict) or set(profiles) != set(_PROFILE_KEYS):
            problems.append(f"profiles must be an object with exactly {list(_PROFILE_KEYS)}, got {profiles!r}")
            return
        edges = profiles["moneyness_edges"]
        if (not isinstance(edges, list) or len(edges) < 2
                or any(isinstance(e, bool) or not isinstance(e, (int, float)) for e in edges)
                or any(b <= a for a, b in zip(edges, edges[1:]))):
            problems.append("profiles.moneyness_edges must be at least two strictly ascending numbers")
        dtes = profiles["dte_edges"]
        if (not isinstance(dtes, list) or len(dtes) < 2 or any(type(e) is not int or e < 1 for e in dtes)
                or any(b <= a for a, b in zip(dtes, dtes[1:]))):
            problems.append("profiles.dte_edges must be at least two strictly ascending integers >= 1")
        pg = profiles["greeks"]
        if (not isinstance(pg, list) or not pg or len(set(pg)) != len(pg)
                or any(g not in greeks for g in pg)):
            problems.append("profiles.greeks must be a non-empty list of distinct declared greek_fields")

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
        profiles = p.get("profiles")
        m_edges = list(profiles["moneyness_edges"]) if profiles else []
        d_edges = list(profiles["dte_edges"]) if profiles else []
        profile_greeks = list(profiles["greeks"]) if profiles else []
        buckets = [(i, j) for i in range(max(len(m_edges) - 1, 0)) for j in range(max(len(d_edges) - 1, 0))]
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
                tally = tallies[(symbol, day)] = _Tally(greeks, buckets, profile_greeks)
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
            if buckets:
                bucket = self._bucket_of(row, day, spot, m_edges, d_edges)
                if bucket is not None:
                    cell = tally.profile[bucket]
                    cell["oi"][side] += oi
                    cell["volume"][side] += self._number(row.get(p["volume_field"]))
                    cell["count"] += self._number(row.get(p["count_field"]))
                    for g in profile_greeks:
                        cell["greeks"][g][side] += oi * self._number(row.get(g)) * spot ** powers[g]
        out = [self._record(symbol, day, tally, greeks, buckets, profile_greeks)
               for (symbol, day), tally in sorted(tallies.items())]
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

    def _bucket_of(self, row, day, spot, m_edges, d_edges):
        """Return the ``(moneyness, dte)`` bucket of a kept contract, or ``None`` outside every bucket."""
        p = self.params
        moneyness = math.log(float(row[p["strike_field"]]) / spot)
        dte = (date.fromisoformat(self._iso(row.get(p["expiration_field"]), "expiration"))
               - date.fromisoformat(day)).days
        i, j = bisect_right(m_edges, moneyness) - 1, bisect_right(d_edges, dte) - 1
        if not 0 <= i < len(m_edges) - 1 or not 0 <= j < len(d_edges) - 1:
            return None
        return (i, j)

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

    def _record(self, symbol, day, tally, greeks, buckets, profile_greeks):
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
            "bid_size_sum": None if empty else tally.bid_size,
            "ask_size_sum": None if empty else tally.ask_size,
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
        for (i, j) in buckets:
            cell, prefix = tally.profile[(i, j)], f"pf_m{i}_d{j}_"
            for side in ("call", "put"):
                record[f"{prefix}oi_{side}"] = None if empty else cell["oi"][side]
                record[f"{prefix}volume_{side}"] = None if empty else cell["volume"][side]
            record[f"{prefix}count"] = None if empty else cell["count"]
            for g in profile_greeks:
                for side in ("call", "put"):
                    record[f"{prefix}oi_{g}_{side}"] = None if empty else cell["greeks"][g][side]
        return record


class ChainDayDeltaSurface(Node):
    """Vendor IV at declared absolute vendor deltas for the expiry nearest a target (role ``transform``).

    Per (symbol, date), contracts whose vendor IV is positive, whose ``|iv_error|`` is within
    the cap, whose open interest is present and whose vendor delta is a finite number are
    grouped by expiry; among expiries within ``tolerance`` days of ``target_dte`` the nearest
    (ties to the shorter) is chosen. Per right, the vendor IV is linearly interpolated in
    absolute vendor delta between the two bracketing contracts at each declared delta
    (``iv_call_25``, ``iv_put_10``, ...; ``None`` when the delta is not bracketed). With
    ``vega_field`` declared, ``vega_iv`` is the OI-vega-weighted vendor IV of the expiry's
    usable contracts; with ``theta_field`` as well, ``theta_vega`` is ``sum(OI theta) /
    sum(OI vega)``, the implied carry per unit vega. Vendor numbers are interpolated and
    ratioed, never re-derived. By import path (ADR-0275).

    Parameters
    ----------
    params : dict
        Row vocabulary (REQUIRED): ``symbol_field``, ``date_field``, ``expiration_field``,
        ``right_field``, ``call_values``, ``put_values`` (disjoint non-empty lists),
        ``delta_field``, ``iv_field``, ``iv_error_field``, ``open_interest_field``; optional
        ``vega_field`` and ``theta_field`` (theta needs vega). Window (REQUIRED): ``end_before``
        (ISO date; a row at or after it refuses), ``target_dte`` (int >= 1), ``tolerance``
        (int >= 0), ``deltas`` (distinct numbers strictly inside (0, 1)), ``iv_error_max``
        (>= 0). ``status_field`` (REQUIRED output name): ``ok`` or ``no_expiry``.

    Examples
    --------
    The 10/25/50-delta vendor smile of the expiry nearest 30 days::

        node = ChainDayDeltaSurface("vendor", {..., "delta_field": "delta", "iv_field": "implied_vol",
                                               "target_dte": 30, "tolerance": 7,
                                               "deltas": [0.1, 0.25, 0.5], "iv_error_max": 0.01,
                                               "end_before": "2026-01-01", "status_field": "status"})
        rows = node.run(ctx, {"records": chain_rows})["records"]   # iv_call_25, iv_put_10, ...
    """

    role = "transform"
    outputs = ("records", "summary")

    _ROWS = ("symbol_field", "date_field", "expiration_field", "right_field", "delta_field", "iv_field",
             "iv_error_field", "open_interest_field")
    _OPTIONAL_ROWS = ("vega_field", "theta_field")
    _PARAMS = (*_ROWS, *_OPTIONAL_ROWS, "call_values", "put_values", "end_before", "target_dte", "tolerance",
               "deltas", "iv_error_max", "status_field")
    _REQUIRED = (*_ROWS, "call_values", "put_values", "end_before", "target_dte", "tolerance",
                 "deltas", "iv_error_max", "status_field")

    @classmethod
    def validate_params(cls, params):
        """Default-deny: refuse unknown, missing and malformed knobs."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in cls._REQUIRED:
            if name not in params:
                problems.append(f"{name} is required")
        for name in (*cls._ROWS, *cls._OPTIONAL_ROWS, "status_field"):
            if name in params and (not isinstance(params[name], str) or not params[name]):
                problems.append(f"{name} must be a non-empty string")
        if "theta_field" in params and "vega_field" not in params:
            problems.append("theta_field needs vega_field (the carry ratio divides by OI vega)")
        rights = {}
        for name in ("call_values", "put_values"):
            value = params.get(name)
            if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v for v in value):
                problems.append(f"{name} must be a non-empty list of strings")
            else:
                rights[name] = set(value)
        if len(rights) == 2 and rights["call_values"] & rights["put_values"]:
            problems.append("call_values and put_values must be disjoint")
        if "end_before" in params:
            try:
                if date.fromisoformat(params["end_before"]).isoformat() != params["end_before"]:
                    raise ValueError
            except (TypeError, ValueError):
                problems.append("end_before must be an ISO YYYY-MM-DD date")
        if "target_dte" in params:
            check_int_param(problems, "target_dte", params["target_dte"], ge=1)
        if "tolerance" in params:
            check_int_param(problems, "tolerance", params["tolerance"], ge=0)
        deltas = params.get("deltas")
        if (not isinstance(deltas, list) or not deltas
                or any(isinstance(d, bool) or not isinstance(d, (int, float)) or not 0.0 < d < 1.0 for d in deltas)
                or len(set(deltas)) != len(deltas)):
            problems.append("deltas must be a non-empty list of distinct numbers strictly between 0 and 1")
        cap = params.get("iv_error_max")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 0:
            problems.append(f"iv_error_max must be a non-negative number, got {cap!r}")
        return problems

    def run(self, ctx, inputs):
        """Group usable contracts by symbol-day and expiry, pick the expiry, interpolate.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: an iterable of contract rows, read once.

        Returns
        -------
        dict
            ``records`` (sorted by symbol then date) and ``summary`` (row, symbol-day, status and
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
        lo, hi = p["target_dte"] - p["tolerance"], p["target_dte"] + p["tolerance"]
        days, rows = {}, 0
        for row in inputs["records"]:
            rows += 1
            day = self._iso(row.get(p["date_field"]), "date")
            if day >= p["end_before"]:
                raise ValueError(f"{self.key}: row dated {day} reaches end_before {p['end_before']}")
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
            expiry = self._iso(row.get(p["expiration_field"]), "expiration")
            dte = (date.fromisoformat(expiry) - date.fromisoformat(day)).days
            entry = days.setdefault((symbol, day), {"expiries": {}, "reasons": Counter()})
            if dte < lo or dte > hi:
                continue
            reason = self._exclusion(row)
            if reason is not None:
                entry["reasons"][reason] += 1
                continue
            entry["expiries"].setdefault((abs(dte - p["target_dte"]), dte, expiry), []).append(
                (side, abs(float(row[p["delta_field"]])), float(row[p["iv_field"]]),
                 float(row[p["open_interest_field"]]),
                 self._number(row.get(p["vega_field"])) if "vega_field" in p else 0.0,
                 self._number(row.get(p["theta_field"])) if "theta_field" in p else 0.0))
        out = [self._record(symbol, day, entry) for (symbol, day), entry in sorted(days.items())]
        reasons = Counter()
        for entry in days.values():
            reasons.update(entry["reasons"])
        return {"records": out,
                "summary": {"rows": rows, "symbol_days": len(out),
                            "status": dict(Counter(r[p["status_field"]] for r in out)),
                            "excluded": sum(reasons.values()), "excluded_reasons": dict(reasons)}}

    def _exclusion(self, row):
        """Return why this contract is unusable, or ``None`` when it is kept."""
        p = self.params
        iv = row.get(p["iv_field"])
        if not number_ok(iv) or float(iv) <= 0.0:
            return "iv"
        error = row.get(p["iv_error_field"])
        if not number_ok(error) or abs(float(error)) > p["iv_error_max"]:
            return "iv_error"
        if not number_ok(row.get(p["open_interest_field"])):
            return "open_interest"
        if not number_ok(row.get(p["delta_field"])):
            return "delta"
        return None

    @staticmethod
    def _number(value):
        """Return a finite number as float, else zero."""
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

    @staticmethod
    def _interpolate(points, target):
        """Linear IV at ``target`` absolute delta from ``[(abs delta, iv)]``, ``None`` when not bracketed."""
        if not points:
            return None
        points = sorted(points)
        deltas = [d for d, _ in points]
        if target < deltas[0] or target > deltas[-1]:
            return None
        k = bisect_right(deltas, target)
        if k == len(deltas) or deltas[k - 1] == target:
            return points[k - 1][1]
        (d0, v0), (d1, v1) = points[k - 1], points[k]
        return v0 + (v1 - v0) * (target - d0) / (d1 - d0)

    def _record(self, symbol, day, entry):
        """One output row for a symbol-day."""
        p = self.params
        names = [(side, int(round(d * 100))) for side in ("call", "put") for d in p["deltas"]]
        record = {"symbol": symbol, "date": day, p["status_field"]: _STATUS_NO_EXPIRY, "expiration": None,
                  "dte": None, "contracts": 0, "candidates": len(entry["expiries"]),
                  "excluded": sum(entry["reasons"].values()), "excluded_reasons": dict(entry["reasons"])}
        for side, pct in names:
            record[f"iv_{side}_{pct}"] = None
        if "vega_field" in p:
            record["vega_iv"] = None
        if "theta_field" in p:
            record["theta_vega"] = None
        if not entry["expiries"]:
            return record
        (_, dte, expiry), contracts = min(entry["expiries"].items(), key=lambda kv: kv[0])
        record.update({p["status_field"]: _STATUS_OK, "expiration": expiry, "dte": dte, "contracts": len(contracts)})
        for side in ("call", "put"):
            points = [(d, iv) for s, d, iv, _, _, _ in contracts if s == side]
            for d in p["deltas"]:
                record[f"iv_{side}_{int(round(d * 100))}"] = self._interpolate(points, d)
        if "vega_field" in p:
            oi_vega = sum(oi * vega for _, _, _, oi, vega, _ in contracts)
            record["vega_iv"] = (sum(oi * vega * iv for _, _, iv, oi, vega, _ in contracts) / oi_vega
                                 if oi_vega > 0 else None)
            if "theta_field" in p:
                record["theta_vega"] = (sum(oi * theta for _, _, _, oi, _, theta in contracts) / oi_vega
                                        if oi_vega > 0 else None)
        return record

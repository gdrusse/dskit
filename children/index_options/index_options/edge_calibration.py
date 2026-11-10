"""The weekly MIO's edge-calibration walk (ADR-0311 base stage 4, Amendment 1 stage A4, ADR-0313 X7).

:class:`IndexOptionsEdgeWalk` is a thin child of
:class:`dskit.pipeline.libs.edge_walk.EdgeReliabilityWalk`: it implements the walk's hooks over the weekly MIO's own
study, plus the guards a domain owns. No statistics live here.

* **Universe.** Every item ``WeeklyOptionPortfolioStudy._prepare(day)`` returns (all quoted strikes, both rights,
  in or out of the money, through MIO's own ``_usable_quote``). :class:`EdgeItemFeatures` is the ONE row builder both
  this walk and the later study call (the ADR-0313 item-feature seam).
* **e** is MIO's own nominal item value at the decision ask (buy) or bid (sell), the ``_prepare`` haircut included:
  ``credit_i - sum_s masses_s x losses[s][i]`` read from the prepared group. **r** is booked by the walk from the
  next-session fill this child looks up (``_fill_credits``, MIO's ``_usable_quote`` and basis rule, ``session`` /
  ``basis`` / ``quote`` / ``size`` codes) and the settlement level (:func:`settlement_level`, MIO's
  ``_settlements`` rule, copied; a split between decision and settlement is ``basis_changed``). Two pins in the
  tests keep the copies equal to MIO's.
* **Direction.** With ``direction_regressor`` the rows carry ``e_dir`` and ``sigma2`` from
  :class:`~dskit.pipeline.option_exposure.DirectionEdge`; every forward is
  ``ParityForward.from_chain`` on :func:`~index_options.contracts.usable_parity_rows` of the DECISION chain.
* **Seal.** ``sealed_from`` (at most the development seal) and any date on or after 2024-01-01 refuse
  ``window_outside_data_rights`` before a reader opens; the readers get ``end_before = sealed_from`` and a row dated
  on or after it refuses again. Everything is read-only; the walk writes only under a fresh ``out_dir``.

Refusals (stable ``code``): ``mio_document_not_pinned``, ``mio_document_invalid``, ``forecast_not_pinned``,
``out_dir_inside_inputs``, ``multiplier_mismatch``, ``direction_config_invalid``, ``side_rule_invalid``,
``study_scope_invalid``, ``calendar_mismatch``, ``window_outside_data_rights`` and the walk's own.
Reading is two seams, :meth:`IndexOptionsEdgeWalk._read_market` and :meth:`~IndexOptionsEdgeWalk._read_forecasts`;
the shipped ones delegate to ``MIOPacketInputs.read_bounded`` and are exercised only by the real-week stage.
Import cost: stdlib, numpy and the study module inside methods.
"""

import bisect
import hashlib
import json
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

from dskit.pipeline.libs.edge_walk import EdgeReliabilityWalk
from dskit.pipeline.edge_reliability import SettledBefore
from dskit.pipeline.option_exposure import DirectionEdge

from .contracts import usable_parity_rows, usable_quote

__all__ = ["EdgeCalibrationRefusal", "EdgeItemFeatures", "IndexOptionsEdgeWalk", "settlement_level",
           "usable_parity_rows"]

#: The quote values of the MIO bridge a run reads and writes to its summary (ADR-0313 A9).
QUOTE_VALUES = ("max_relative_spread", "min_quote_size", "max_abs_z")


class EdgeCalibrationRefusal(ValueError):
    """A refusal with a stable ``code`` (message ``"<code>: <detail>"``)."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def settlement_level(bars, factors, day, factor):
    """Return the underlying's settlement level, or ``no_settlement_bar`` / ``basis_changed``.

    A COPY of the rule in ``WeeklyOptionPortfolioStudy._settlements`` (the bar close over the decision factor, admitted
    only when the day's factor equals it, rel 1e-9); the child test pins the two equal. MIO refuses where this
    returns a code.

    Parameters
    ----------
    bars : dict
        Date to close.
    factors : dict
        Date to basis factor.
    day : str
        The settlement date.
    factor : float
        The row's decision-session factor.

    Returns
    -------
    float or str
    """
    if day not in bars:
        return "no_settlement_bar"
    if not math.isclose(factors.get(day, math.nan), factor, rel_tol=1e-9):
        return "basis_changed"
    return bars[day] / factor


class EdgeItemFeatures:
    """The row builder of the walk's decision hook, shared with the study that will read the maps.

    Parameters
    ----------
    direction : dict or None
        :class:`DirectionEdge` params, or ``None`` for rows without ``e_dir``.
    edge_scale : str
        ``notional`` (``spot x multiplier``), ``scale`` (``spot x reference_scale x multiplier``) or ``none``.

    Examples
    --------
    One decision day of a built study::

        rows = EdgeItemFeatures(None, "notional").rows(study, "2020-03-02")
    """

    def __init__(self, direction=None, edge_scale="notional"):
        self.direction = direction
        self.edge_scale = edge_scale
        self._edge = DirectionEdge(direction) if direction is not None else None

    @staticmethod
    def _chain_rows(study, symbol, day, expiry, factor):
        """Return one expiry's decision-session chain rows in the parity-filter shape."""
        view = study._quotes[symbol]
        if hasattr(view, "iter_for"):
            raw = list(view.iter_for(day, expiry))
        else:
            raw = [r for k, r in study._by_day[symbol].get(day, {}).items() if k[1] == expiry]
        return [{**r, "right": r["type"], "strike": float(r["strike_raw"])} for r in raw]

    def rows(self, study, day):
        """Return every decision row (one per item and side) of ``day``.

        Reads only ``study._prepare(day)`` and the decision session's chain.

        Parameters
        ----------
        study : WeeklyOptionPortfolioStudy
            A built study.
        day : str
            ISO decision session.

        Returns
        -------
        list of dict
            The walk's row dicts.
        """
        import numpy as np

        groups, meta, _ = study._prepare(day)
        calendar = study.calendar
        at = bisect.bisect_right(calendar, day)
        fill = calendar[at] if at < len(calendar) else None
        scales = {}
        for m in meta:
            context = study._contexts[m["symbol"]][day, m["expiry"]]
            scales.setdefault(m["symbol"], []).append(float(context["reference_scale"]) * math.sqrt(365 / m["dte"]))
        vols = {s: statistics.median(v) for s, v in scales.items()}
        out, flat = [], []
        for group, m in zip(groups, meta):
            node = study._nodes[m["symbol"]]
            mult = node.params["solver"]["multiplier"]
            fee = node.params["solver"]["fee_per_contract_usd"]
            context = study._contexts[m["symbol"]][day, m["expiry"]]
            ref, spot = float(context["reference_scale"]), m["spot"]
            credit = np.asarray(group["credit"])
            e = credit - np.asarray(group["masses"]) @ np.asarray(group["losses"])
            quotes = {}
            for item in m["items"]:
                quotes.setdefault((item["right"], item["strike"]), {})[item["sign"]] = item["price"]
            if self._edge is not None:
                usable = usable_parity_rows(self._chain_rows(study, m["symbol"], day, m["expiry"], m["factor"]),
                                            m["factor"], node.params["min_quote_size"],
                                            node.params["max_relative_spread"], strict=True, rescale=True)
                mean = DirectionEdge.forecast_mean(group["masses"], group["grid"])
                by = {(r["right"], r["strike"]): r for r in usable}
            for i, item in enumerate(m["items"]):
                sides = quotes[item["right"], item["strike"]]
                mid = (sides[1] + sides[-1]) / 2
                row = {"group": m["id"], "name": m["symbol"], "expiry": m["expiry"], "settle_date": m["expiry"],
                       "fill_date": fill, "factor": m["factor"], "right": item["right"], "strike": item["strike"],
                       "sign": item["sign"], "e": float(e[i]), "dte": m["dte"],
                       "abs_z": abs(math.log(item["strike"] / spot) / ref), "spread": (sides[1] - sides[-1]) / mid,
                       "v": vols[m["symbol"]], "credit": float(credit[i]), "multiplier": mult, "fee": fee,
                       "mid": mid, "cdf_repaired": "cdf_noise_raised" in m}
                if self.edge_scale != "none":
                    row["scale"] = spot * mult * (ref if self.edge_scale == "scale" else 1.0)
                if self._edge is not None:
                    flat.append((row, item, by.get((item["right"], item["strike"])), usable, spot, mean, m))
                out.append(row)
        if self._edge is not None:
            self._direction(flat, day)
        return out

    def _direction(self, flat, day):
        """Fill ``e_dir`` and ``sigma2`` (or a ``direction_code``), the name-week dispersion standing in for thin chains."""
        results, spread = [], {}
        for row, item, quote, usable, spot, mean, m in flat:
            probe = {"right": item["right"], "strike": item["strike"], "sign": item["sign"], "expiry": m["expiry"]}
            out, code = self._edge.of(probe, quote, usable, spot, mean, day)
            results.append((out, code))
            if out is not None and out["forward_se"] is not None and not out["forward_se_fallback"]:
                spread.setdefault(m["symbol"], {})[m["expiry"]] = out["forward_se"]
        for (row, item, quote, usable, spot, mean, m), (out, code) in zip(flat, results):
            if out is not None and out["forward_se_fallback"]:
                others = [v for k, v in spread.get(m["symbol"], {}).items() if k != m["expiry"]]
                if others:
                    probe = {"right": item["right"], "strike": item["strike"], "sign": item["sign"],
                             "expiry": m["expiry"]}
                    out, code = self._edge.of(probe, quote, usable, spot, mean, day, statistics.median(others))
            if out is None:
                row["e_dir"], row["direction_code"] = None, code.split(":", 1)[1]
            else:
                row["e_dir"], row["sigma2"] = out["e_dir"], out["sigma2_usd"]


class IndexOptionsEdgeWalk(EdgeReliabilityWalk):
    """The weekly MIO's forward edge-reliability walk (ADR-0311 stage 4 + A4).

    Parameters
    ----------
    params : dict
        The walk's own (``window``, ``report_from``, ``sealed_from``, ``out_dir``, ``buckets``, ``regime``,
        ``pooling``, ``name_adjust``, ``direction_regressor`` ...) plus ``mio_document`` (``{path, sha256}``: a MIO
        run document, whose ``pipeline.plan.params.study.domain`` names the study params, pairs and source pins),
        ``forecast_pins`` (per decision quarter ``{forecasts, contexts}`` sha256, each also among the document's
        forecast source pins), ``side_rule`` (``best``: the walk keeps the larger-``e`` side of an option),
        ``study_scope`` (``window``, or ``quarter``: one study per decision quarter, byte-identical output) and
        ``direction`` (the :class:`DirectionEdge` params; required iff ``direction_regressor``).

    Raises
    ------
    EdgeCalibrationRefusal
        With the codes in the module text.

    Examples
    --------
    From a config (see ``configs/edge-calibration-template.json``)::

        IndexOptionsEdgeWalk("edge", params).run(ctx, {})["summary"]
    """

    _EXTRA_PARAMS = ("mio_document", "forecast_pins", "side_rule", "study_scope", "direction")
    SIDE_RULES = ("best",)
    STUDY_SCOPES = ("window", "quarter")
    _DOMAIN = ("pipeline", "plan", "params", "study", "domain")

    @classmethod
    def validate_params(cls, params):
        """Return the walk's problems plus the child's (shape only; the pins are checked at run)."""
        problems = super().validate_params(params)
        doc = params.get("mio_document")
        if not (isinstance(doc, dict) and set(doc) == {"path", "sha256"}):
            problems.append("mio_document must hold exactly path and sha256")
        pins = params.get("forecast_pins")
        if not (isinstance(pins, dict) and pins and all(isinstance(v, dict) and set(v) == {"forecasts", "contexts"}
                                                         for v in pins.values())):
            problems.append("forecast_pins must map each decision quarter to {forecasts, contexts}")
        if params.get("side_rule") not in cls.SIDE_RULES:
            problems.append(f"side_rule_invalid: must be one of {list(cls.SIDE_RULES)}")
        if params.get("study_scope") not in cls.STUDY_SCOPES:
            problems.append(f"study_scope_invalid: must be one of {list(cls.STUDY_SCOPES)}")
        problems.extend(cls._direction_problems(params))
        return problems

    @staticmethod
    def _direction_problems(params):
        on, block = bool(params.get("direction_regressor")), params.get("direction")
        if on != (block is not None):
            return ["direction_config_invalid: direction is required exactly when direction_regressor is true"]
        return [f"direction_config_invalid: {p}" for p in DirectionEdge.validate_params(block)] if on else []

    # -- opening ---------------------------------------------------------------------------------

    def _open(self):
        """Run every guard, then read and build the studies, once; nothing is read before the guards pass."""
        if getattr(self, "_opened", False):
            return
        p = self.params
        seal = SettledBefore(p.get("sealed_from"))
        seal.check_window([p["window"]["start"], p["window"]["end"], p["report_from"]])
        self._sealed = seal.sealed_from
        for key, allowed, code in (("side_rule", self.SIDE_RULES, "side_rule_invalid"),
                                   ("study_scope", self.STUDY_SCOPES, "study_scope_invalid")):
            if p.get(key) not in allowed:
                raise EdgeCalibrationRefusal(code, f"{key} must be one of {list(allowed)}, got {p.get(key)!r}")
        problems = self._direction_problems(p)
        if problems:
            raise EdgeCalibrationRefusal("direction_config_invalid", "; ".join(problems))
        self._document()
        history = self._domain["history_start"]
        if p["window"]["start"] <= history:
            raise EdgeCalibrationRefusal("calendar_mismatch", f"window.start {p['window']['start']} has no history "
                                         f"after history_start {history}")
        self._guard_out_dir()
        self._guard_pins()
        self._features = EdgeItemFeatures(p.get("direction"), self._param("edge_scale"))
        self._build_market()
        self._opened = True

    def _document(self):
        pin = self.params["mio_document"]
        try:
            raw = Path(pin["path"]).read_bytes()
        except OSError as exc:
            raise EdgeCalibrationRefusal("mio_document_not_pinned", f"cannot read {pin['path']}: {exc}") from exc
        if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
            raise EdgeCalibrationRefusal("mio_document_not_pinned", "the document's sha256 differs from its pin")
        self._doc_sha = pin["sha256"]
        try:
            document = json.loads(raw)
            domain = document
            for part in self._DOMAIN:
                domain = domain[part]
            self._domain = domain
            self._symbols = list(domain["params"]["symbols"])
            bridge = domain["params"]["bridge"]
            self._quote_values = {**{k: bridge[k] for k in QUOTE_VALUES},
                                  "fee_per_contract_usd": bridge["solver"]["fee_per_contract_usd"],
                                  "multiplier": bridge["solver"]["multiplier"]}
            domain["pairs"], domain["history_start"], domain["sources"]["forecasts"]["pin"]["files"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EdgeCalibrationRefusal("mio_document_invalid", f"the document lacks {exc!r}") from exc
        direction = self.params.get("direction")
        if direction is not None and direction.get("multiplier") != self._quote_values["multiplier"]:
            raise EdgeCalibrationRefusal("multiplier_mismatch", f"direction.multiplier {direction.get('multiplier')!r} "
                                         f"is not the study's {self._quote_values['multiplier']}")

    def _guard_out_dir(self):
        out = Path(self.params["out_dir"]).resolve()
        roots = [Path(self.params["mio_document"]["path"]).resolve().parent]
        for binding in self._domain["sources"].values():
            root = (binding.get("pin") or {}).get("root")
            if root:
                roots.append(Path(root).resolve())
        for root in roots:
            if out == root or root in out.parents:
                raise EdgeCalibrationRefusal("out_dir_inside_inputs", f"{out} lies inside the input root {root}")

    def _quarters(self):
        """Return the decision quarters of the window, ``YYYYQn``, in order."""
        start, end = (date.fromisoformat(self.params["window"][k]) for k in ("start", "end"))
        out, year, q = [], start.year, (start.month - 1) // 3 + 1
        while (year, q) <= (end.year, (end.month - 1) // 3 + 1):
            out.append(f"{year}Q{q}")
            year, q = (year + 1, 1) if q == 4 else (year, q + 1)
        return out

    def _guard_pins(self):
        pinned = set(self._domain["sources"]["forecasts"]["pin"]["files"].values())
        for quarter in self._quarters():
            pin = self.params["forecast_pins"].get(quarter)
            if pin is None or pin["forecasts"] not in pinned or pin["contexts"] not in pinned:
                raise EdgeCalibrationRefusal("forecast_not_pinned", f"{quarter} has no pin matching the document's")

    @staticmethod
    def _quarter_bounds(quarter):
        year, q = int(quarter[:4]), int(quarter[5])
        start = date(year, 3 * q - 2, 1)
        end = date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
        return start.isoformat(), end.isoformat()

    # -- the reading seams ---------------------------------------------------------------------

    def _bounded(self, tail):  # pragma: no cover - needs the real pinned sources; exercised by the stage 5 week
        if getattr(self, "_bounded_cache", None) is None:
            from .observations import MIOPacketInputs

            with open(self.params["mio_document"]["path"]) as fh:
                study = json.load(fh)["pipeline"]["plan"]["params"]["study"]
            p, hist = self.params, study["domain"]["history_start"]
            end = min((date.fromisoformat(p["window"]["end"]) + timedelta(days=1)).isoformat(), tail)
            span = {"start": p["window"]["start"], "end_before": end}
            bounds = {"forecasts": span, "contexts": span, "chain": {"start": p["window"]["start"], "end_before": tail},
                      "bars": {"start": hist, "end_before": tail}, "sessions": {"start": hist, "end_before": tail}}
            root = Path(p["out_dir"]) / "inputs" / "mio-quotes"
            root.mkdir(parents=True, exist_ok=True)
            self._bounded_cache = MIOPacketInputs.read_bounded(study, bounds, span, root, None)
        return self._bounded_cache["inputs"]

    def _read_market(self, domain, tail):
        """Return ``{symbol: {"chain" or "chain_index", "bars"}}`` bounded below ``tail`` (the seam; read-only)."""
        data = self._bounded(tail)  # pragma: no cover
        return {s: {k: v for k, v in d.items() if k in ("chain", "chain_index", "bars")} for s, d in data.items()}

    def _read_forecasts(self, domain, start, end_before):
        """Return ``{symbol: {"forecasts", "contexts"}}`` for decisions in ``[start, end_before)`` (the seam)."""
        data = self._bounded(self._sealed)  # pragma: no cover
        return {s: {k: d[k] for k in ("forecasts", "contexts")} for s, d in data.items()}

    def _refuse_sealed(self, rows, fields):
        for row in rows:
            for field in fields:
                if row.get(field) is not None and row[field] >= self._sealed:
                    raise EdgeCalibrationRefusal("window_outside_data_rights",
                                                 f"a row dated {row[field]} is on or after sealed_from {self._sealed}")

    def _build_market(self):
        p = self.params
        tail = self._sealed
        market = self._read_market(self._domain, tail)
        if set(market) != set(self._symbols):
            raise EdgeCalibrationRefusal("mio_document_invalid", "the market inputs do not cover the document's symbols")
        for data in market.values():
            self._refuse_sealed(data.get("chain", []), ("date", "expiration"))
            self._refuse_sealed(data["bars"], ("date",))
        history = self._domain["history_start"]
        sessions = sorted(set.intersection(*({b["date"] for b in d["bars"]} for d in market.values())))
        self._sessions = [d for d in sessions if history <= d < tail]
        self._market = market
        self._entry_end = min((date.fromisoformat(p["window"]["end"]) + timedelta(days=1)).isoformat(), tail)
        self._stored, self._qkey, self._qstudy = {}, None, None
        forecasts = self._forecasts(p["window"]["start"], self._entry_end)
        if p["study_scope"] == "window":
            self._market_study = self._study = self._build(p["window"]["start"], self._entry_end, forecasts)
        else:
            empty = {s: {"forecasts": [], "contexts": []} for s in self._symbols}
            self._market_study = self._build(p["window"]["start"], self._entry_end, empty)

    def _forecasts(self, start, end_before):
        got = self._read_forecasts(self._domain, start, end_before)
        out = {}
        for symbol in self._symbols:
            rows = got[symbol]
            self._refuse_sealed(rows["forecasts"] + rows["contexts"], ("quote_date", "expiry", "settlement_date"))
            out[symbol] = {k: [r for r in rows[k] if start <= r["quote_date"] < end_before] for k in rows}
        return out

    def _build(self, start, end_before, forecasts):
        from .ledger_studies import WeeklyOptionPortfolioStudy

        domain = self._domain
        clocks = {"sessions": self._sessions, "history_start": domain["history_start"], "entry_start": start,
                  "entry_end_before": end_before, "tail_end_before": self._sealed}
        study_params = {**domain["params"], "selected_pairs": domain["pairs"], "clocks": clocks}
        inputs = {}
        for symbol in self._symbols:
            data = dict(self._market[symbol])
            if "chain" in data:
                data["chain"] = [r for r in data["chain"] if r["date"] >= start]
            inputs[symbol] = {**data, **forecasts[symbol]}
        return WeeklyOptionPortfolioStudy(study_params, inputs)

    def _study_for(self, t):
        """Return the study that decides ``t``: the window's, or the decision quarter's (one kept at a time)."""
        self._open()
        if self.params["study_scope"] == "window":
            return self._study
        quarter = f"{t[:4]}Q{(int(t[5:7]) - 1) // 3 + 1}"
        if self._qkey != quarter:
            first, last = self._quarter_bounds(quarter)
            start = max(first, self.params["window"]["start"])
            end = min(last, self._entry_end)
            self._qstudy = None
            self._qstudy = self._build(start, end, self._forecasts(start, end))
            self._qkey = quarter
        return self._qstudy

    # -- the walk's hooks --------------------------------------------------------------------------

    def _decision_calendar(self):
        self._open()
        p = self.params
        cal = [d for d in self._market_study.calendar if p["window"]["start"] <= d < self._entry_end]
        seen, weeks = set(), []
        for d in cal:
            week = date.fromisoformat(d).isocalendar()[:2]
            if week not in seen:
                seen.add(week)
                weeks.append(d)
        return weeks

    def _decision_rows(self, t):
        study = self._study_for(t)
        if t not in study.weeks:
            raise EdgeCalibrationRefusal("calendar_mismatch", f"{t} is not a decision week of the study")
        return self._features.rows(study, t)

    def _next_session(self, t):
        self._open()
        cal = self._market_study.calendar
        at = bisect.bisect_right(cal, t)
        if at >= len(cal):
            raise EdgeCalibrationRefusal("calendar_mismatch", f"no session after {t}")
        return cal[at]

    def _fill_credits(self, rows):
        """Return per row ``(credit, fill mid)`` or ``session`` / ``basis`` / ``quote`` / ``size``.

        MIO's own rule (``study._fill``): fill on the next session, same basis, ``node._usable_quote`` on the quote.
        ``size`` is a quote that only the displayed-size test refuses.
        """
        study = self._market_study
        out = []
        for r in rows:
            node, symbol = study._nodes[r["name"]], r["name"]
            if r["fill_date"] >= r["expiry"]:
                out.append("session")
                continue
            if not math.isclose(study._factors[symbol].get(r["fill_date"], math.nan), r["factor"], rel_tol=1e-9):
                out.append("basis")
                continue
            row = study._quotes[symbol].get((r["fill_date"], r["expiry"], r["right"], r["strike"]))
            market = node._usable_quote(row, r["factor"])
            if market is None:
                relaxed = usable_quote(row, r["factor"], 0, node.params["max_relative_spread"],
                                       strict=node.STRICT_BID_ASK)
                out.append("size" if relaxed is not None else "quote")
                continue
            bid, ask = market
            fee = node.params["solver"]["fee_per_contract_usd"]
            price = ask if r["sign"] > 0 else bid
            out.append((-r["sign"] * price * r["multiplier"] - fee, (bid + ask) / 2))
        return out

    def _settlement_levels(self, rows):
        study = self._market_study
        return [settlement_level(study._bars[r["name"]], study._factors[r["name"]], r["settle_date"], r["factor"])
                for r in rows]

    def _input_pins(self):
        self._open()
        pins = {"mio_document": self._doc_sha,
                "symbol_list": hashlib.sha256(json.dumps(self._symbols).encode()).hexdigest()}
        for quarter in self._quarters():
            pins[f"forecast:{quarter}"] = self.params["forecast_pins"][quarter]["forecasts"]
            pins[f"contexts:{quarter}"] = self.params["forecast_pins"][quarter]["contexts"]
        return pins

    def _summary(self, weeks):
        """Return the walk's summary plus the child keys ADR-0313 X7 asks for (the walk has no extras hook)."""
        out = super()._summary(weeks)
        out["forecast_pins"] = {q: dict(self.params["forecast_pins"][q]) for q in self._quarters()}
        out["mio_quote_values"] = dict(self._quote_values)
        out["symbol_list_sha256"] = hashlib.sha256(json.dumps(self._symbols).encode()).hexdigest()
        return out

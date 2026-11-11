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
``study_scope_invalid``, ``calendar_mismatch``, ``window_outside_data_rights``, ``quote_params_differ``,
``bridge_params_differ``, ``chain_not_covered`` and the walk's own.
Reading is two seams, :meth:`IndexOptionsEdgeWalk._read_market` and :meth:`~IndexOptionsEdgeWalk._read_forecasts`;
the shipped ones delegate to ``MIOPacketInputs.read_bounded`` and are exercised only by the real-week stage.
Import cost: stdlib, numpy and the study module inside methods.
"""

import bisect
import hashlib
import json
import math
import re
import sqlite3
import statistics
from datetime import date, timedelta
from pathlib import Path

from dskit.pipeline.libs.edge_walk import EdgeReliabilityWalk
from dskit.pipeline.edge_reliability import SettledBefore
from dskit.pipeline.option_exposure import DirectionEdge

from .contracts import usable_parity_rows, usable_quote

__all__ = ["EdgeCalibrationRefusal", "EdgeItemFeatures", "EdgeMapStream", "IndexOptionsEdgeWalk", "settlement_level",
           "usable_parity_rows"]

#: The quote values of the MIO bridge a run reads and writes to its summary (ADR-0313 A9).
#: Path A0003: nothing on or after this day is ever read.
HARD_END = "2024-01-01"
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


class EdgeMapStream:
    """The walk's decision-day maps as ONE published file, and the rules that read it back (ADR-0313 A9).

    The stream is a JSON object ``{"maps": {<t>: <map-t.json body>}}``: the walk writes ``map-<t>.json`` for every
    decision week, :meth:`assemble` joins them for publication through onboarding (the ``edge_maps`` source role
    binds this file as ``shared`` and the walk's ``summary.json`` as ``summary``). :meth:`parse` refuses a key that is
    not an ISO date or a body whose own ``t`` differs from its key (``calibration_map_date``), and :meth:`bounded`
    is the read bound: only maps whose decision date lies in ``[start, end_before)`` reach a packet.

    Examples
    --------
    Publish, then read back inside a packet's entry window::

        raw = EdgeMapStream.assemble(walk_out_dir)
        maps = EdgeMapStream.bounded(EdgeMapStream.parse(raw), "2020-01-01", "2020-04-01")
    """

    @staticmethod
    def assemble(out_dir):
        """Return the canonical bytes of the stream for the ``map-<t>.json`` files under ``out_dir``."""
        maps = {}
        for path in sorted(Path(out_dir).glob("map-*.json")):
            body = json.loads(path.read_text())
            maps[path.name[len("map-"):-len(".json")]] = body
        return (json.dumps({"maps": maps}, sort_keys=True, separators=(",", ":")) + "\n").encode()

    @classmethod
    def publish(cls, out_dir, dest_dir):
        """Write ``edge-maps.json`` and a copy of the walk's ``summary.json`` into ``dest_dir`` (the files to onboard)."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "edge-maps.json").write_bytes(cls.assemble(out_dir))
        (dest / "summary.json").write_bytes((Path(out_dir) / "summary.json").read_bytes())
        return dest

    @staticmethod
    def parse(raw):
        """Return ``{t: body}`` from the stream's bytes (or text); refuse any other shape."""
        try:
            blob = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise EdgeCalibrationRefusal("calibration_map_malformed", f"the map stream is not JSON: {exc}") from exc
        if not isinstance(blob, dict) or set(blob) != {"maps"} or not isinstance(blob["maps"], dict):
            raise EdgeCalibrationRefusal("calibration_map_malformed", "the map stream must be {'maps': {t: body}}")
        for t, body in blob["maps"].items():
            try:
                ok = date.fromisoformat(t).isoformat() == t
            except (TypeError, ValueError):
                ok = False
            if not ok or not isinstance(body, dict) or body.get("t") != t:
                raise EdgeCalibrationRefusal("calibration_map_date", f"map key {t!r} is not the decision date of its "
                                             f"body ({body.get('t') if isinstance(body, dict) else body!r})")
        return blob["maps"]

    @staticmethod
    def bounded(maps, start, end_before):
        """Return the maps dated in ``[start, end_before)``; every other map is dropped, never passed on."""
        return {t: body for t, body in maps.items() if start <= t < end_before}


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
        ``pooling``, ``name_adjust``, ``direction_regressor`` ...) plus ``mio_documents`` (``{quarter: {path, sha256}}``, the owner's
        layout: one MIO run document per decision quarter, each binding that year's chain file and that quarter's
        forecast files) or ``mio_document`` (``{path, sha256}``, one document that truly covers the window; a MIO
        run document, whose ``pipeline.plan.params.study.domain`` names the study params, pairs and source pins),
        ``forecast_pins`` (per decision quarter ``{forecasts, contexts}`` sha256, each also among the document's
        forecast source pins), ``side_rule`` (``best``: the walk keeps the larger-``e`` side of an option),
        ``study_scope`` (``window``, or ``quarter``: one study per decision quarter, byte-identical output) and
        ``direction`` (the :class:`DirectionEdge` params; required iff ``direction_regressor``), and the walk's own
        clocks ``history_start`` (before ``window.start``; the bars must reach back to it) and ``protected_end_before``
        (``sealed_from`` to 2024-01-01): a packet document's own values follow ITS packet and would refuse here.
        The document's corporate-action source is NOT read: the walk holds no books, so no split conversion
        applies; a split between decision and settlement is the counted ``basis_changed``.

    Raises
    ------
    EdgeCalibrationRefusal
        With the codes in the module text.

    Examples
    --------
    From a config (see ``configs/edge-calibration-template.json``)::

        IndexOptionsEdgeWalk("edge", params).run(ctx, {})["summary"]
    """

    _EXTRA_PARAMS = ("mio_document", "mio_documents", "forecast_pins", "side_rule", "study_scope", "direction", "history_start",
                     "protected_end_before")
    SIDE_RULES = ("best",)
    STUDY_SCOPES = ("window", "quarter")
    _DOMAIN = ("pipeline", "plan", "params", "study", "domain")
    #: The bridge keys that follow a document's own quarter; every other bridge value must agree across documents.
    _PER_QUARTER_BRIDGE = ("symbol", "fold", "start", "end_before", "protected_end_before")

    @classmethod
    def validate_params(cls, params):
        """Return the walk's problems plus the child's (shape only; the pins are checked at run)."""
        problems = super().validate_params(params)
        one, many = params.get("mio_document"), params.get("mio_documents")
        if (one is None) == (many is None):
            problems.append("exactly one of mio_document (one document covers the window) and mio_documents "
                            "(per decision quarter) is required")
        def pinned(d):
            return isinstance(d, dict) and set(d) == {"path", "sha256"}

        if one is not None and not pinned(one):
            problems.append("mio_document must hold exactly path and sha256")
        if many is not None and not (isinstance(many, dict) and many and all(
                isinstance(q, str) and re.fullmatch(r"\d{4}Q[1-4]", q) and pinned(d) for q, d in many.items())):
            problems.append("mio_documents must map each decision quarter (YYYYQn) to {path, sha256}")
        pins = params.get("forecast_pins")
        if not (isinstance(pins, dict) and pins and all(isinstance(v, dict) and set(v) == {"forecasts", "contexts"}
                                                         for v in pins.values())):
            problems.append("forecast_pins must map each decision quarter to {forecasts, contexts}")
        if params.get("side_rule") not in cls.SIDE_RULES:
            problems.append(f"side_rule_invalid: must be one of {list(cls.SIDE_RULES)}")
        if params.get("study_scope") not in cls.STUDY_SCOPES:
            problems.append(f"study_scope_invalid: must be one of {list(cls.STUDY_SCOPES)}")
        problems.extend(cls._direction_problems(params))
        for key in ("history_start", "protected_end_before"):
            try:
                date.fromisoformat(params[key])
            except (KeyError, TypeError, ValueError):
                problems.append(f"{key} is required: an ISO date (the walk's own clock, never the packet document's)")
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
        # The walk's own _setup repeats this seal check; it is kept because _open runs first when called directly
        # and must refuse before any pin or reader is touched.
        problems = self._direction_problems(p)
        if problems:
            raise EdgeCalibrationRefusal("direction_config_invalid", "; ".join(problems))
        self._documents()
        history = p["history_start"]
        if history >= p["window"]["start"]:
            raise EdgeCalibrationRefusal("history_start_invalid", f"history_start {history} is not before "
                                         f"window.start {p['window']['start']}")
        if not self._sealed <= p["protected_end_before"] <= HARD_END:
            raise EdgeCalibrationRefusal("protected_end_invalid", f"protected_end_before {p['protected_end_before']} "
                                         f"must lie in [sealed_from {self._sealed}, {HARD_END}]")
        self._guard_out_dir()
        self._guard_pins()
        self._features = EdgeItemFeatures(p.get("direction"), self._param("edge_scale"))
        self._build_market()
        self._opened = True

    def _documents(self):
        """Load and pin the MIO document of every decision quarter (one document may serve them all).

        ``mio_documents`` maps each quarter to its own pinned document (the owner's layout: one document per quarter,
        binding that year's chain file and that quarter's forecast files); ``mio_document`` is the single form. The
        quote values, fee, multiplier and the rest of the bridge must be identical across documents
        (``quote_params_differ`` / ``bridge_params_differ``). A symbol outside a quarter's universe is a counted drop.
        """
        p, quarters = self.params, self._quarters()
        pins = p.get("mio_documents")
        if pins is None:
            pins = {q: p["mio_document"] for q in quarters}
        missing = [q for q in quarters if q not in pins]
        if missing:
            raise EdgeCalibrationRefusal("mio_document_not_pinned", f"no document for decision quarter(s) {missing}")
        loaded, self._docs = {}, {}
        for quarter in quarters:
            pin = pins[quarter]
            key = pin["path"], pin["sha256"]
            if key not in loaded:
                loaded[key] = self._load_document(pin)
            self._docs[quarter] = loaded[key]
        first = self._docs[quarters[0]]
        for quarter, doc in self._docs.items():
            if doc["quote"] != first["quote"]:
                raise EdgeCalibrationRefusal("quote_params_differ", f"{quarter}'s document quotes {doc['quote']} "
                                             f"but {quarters[0]}'s {first['quote']}")
            if doc["bridge"] != first["bridge"]:
                raise EdgeCalibrationRefusal("bridge_params_differ", f"{quarter}'s bridge differs from "
                                             f"{quarters[0]}'s beyond fold, window and protected_end_before")
        self._domain, self._doc_sha, self._quote_values = first["domain"], first["sha"], first["quote"]
        self._idle = {}
        self._symbols = list(dict.fromkeys(s for q in quarters for s in self._docs[q]["symbols"]))
        self._absent = {s: [q for q in quarters if s not in self._docs[q]["symbols"]] for s in self._symbols}
        self._absent = {s: qs for s, qs in self._absent.items() if qs}
        pairs = {}
        for quarter in quarters:
            for pair in self._docs[quarter]["domain"]["pairs"]:
                if pairs.setdefault(pair["id"], pair) != pair:
                    raise EdgeCalibrationRefusal("mio_document_invalid", f"pair {pair['id']!r} differs across documents")
        self._pairs = list(pairs.values())
        direction = p.get("direction")
        if direction is not None and direction.get("multiplier") != self._quote_values["multiplier"]:
            raise EdgeCalibrationRefusal("multiplier_mismatch", f"direction.multiplier {direction.get('multiplier')!r} "
                                         f"is not the study's {self._quote_values['multiplier']}")

    def _load_document(self, pin):
        try:
            raw = Path(pin["path"]).read_bytes()
        except OSError as exc:
            raise EdgeCalibrationRefusal("mio_document_not_pinned", f"cannot read {pin['path']}: {exc}") from exc
        if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
            raise EdgeCalibrationRefusal("mio_document_not_pinned", f"{pin['path']}: the sha256 differs from its pin")
        try:
            document = json.loads(raw)
            domain = document
            for part in self._DOMAIN:
                domain = domain[part]
            bridge = domain["params"]["bridge"]
            quote = {**{k: bridge[k] for k in QUOTE_VALUES},
                     "fee_per_contract_usd": bridge["solver"]["fee_per_contract_usd"],
                     "multiplier": bridge["solver"]["multiplier"]}
            domain["pairs"], domain["sources"]["forecasts"]["pin"]["files"], domain["sources"]["contexts"]["pin"]["files"]
            return {"sha": pin["sha256"], "path": pin["path"], "domain": domain, "quote": quote,
                    "symbols": list(domain["params"]["symbols"]), "study": document["pipeline"]["plan"]["params"]["study"],
                    "bridge": {k: v for k, v in bridge.items() if k not in self._PER_QUARTER_BRIDGE}}
        except (ValueError, KeyError, TypeError) as exc:
            raise EdgeCalibrationRefusal("mio_document_invalid", f"the document lacks {exc!r}") from exc

    def _guard_out_dir(self):
        out = Path(self.params["out_dir"]).resolve()
        roots = []
        for doc in {id(d): d for d in self._docs.values()}.values():
            roots.append(Path(doc["path"]).resolve().parent)
            for binding in doc["domain"]["sources"].values():
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

    @staticmethod
    def _quarter_files(quarter):
        """Return the pinned relative names of one quarter (``quarterly_forecasts.pin``'s layout)."""
        return {"forecasts": f"{quarter}/forecasts.parquet", "contexts": f"{quarter}/contexts.parquet"}

    def _guard_pins(self):
        """Bind each quarter's config pin to the document's pin entry of THAT quarter's file, role by role."""
        for quarter in self._quarters():
            sources = self._docs[quarter]["domain"]["sources"]
            pin = self.params["forecast_pins"].get(quarter)
            if pin is None:
                raise EdgeCalibrationRefusal("forecast_not_pinned", f"{quarter} has no forecast pin")
            for role, relative in self._quarter_files(quarter).items():
                if sources[role]["pin"]["files"].get(relative) != pin[role]:
                    raise EdgeCalibrationRefusal("forecast_not_pinned", f"{quarter} {role} pin differs from the "
                                                 f"document's entry for {relative}")

    @staticmethod
    def _quarter_bounds(quarter):
        year, q = int(quarter[:4]), int(quarter[5])
        start = date(year, 3 * q - 2, 1)
        end = date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
        return start.isoformat(), end.isoformat()

    # -- the reading seams ---------------------------------------------------------------------

    def _passes(self):
        """Read the window ONE QUARTER AT A TIME (the real layout: ``<q>/forecasts.parquet`` per quarter).

        Each pass copies the document's study, points its forecast and context files at the quarter's, sets the
        bridge fold (``Quarter.parse``), start and end to the quarter, hashes the file it is about to open against
        ``forecast_pins`` and calls ``MIOPacketInputs.read_bounded``. A quarter with no forecast row refuses
        ``forecast_quarter_empty``. The quote indexes live under ``out_dir/inputs`` and are rebuilt (or hit in the
        content-addressed reuse cache) when a resumed run opens them again.
        """
        if getattr(self, "_pass_cache", None) is not None:
            return self._pass_cache
        import copy

        from dskit.onboarding.artifacts import PinnedPayloadSource

        from .observations import MIOPacketInputs
        from .quarterly_forecasts import Quarter

        p = self.params
        tail = self._sealed
        out, self._idle = {}, {}
        for name in self._quarters():
            base = self._docs[name]["study"]
            base.setdefault("code", {})
            base.setdefault("dependencies", {})
            try:
                quarter = Quarter.parse(name)
            except ValueError as exc:
                raise EdgeCalibrationRefusal("forecast_quarter_unknown", str(exc)) from exc
            start = max(quarter.start, p["window"]["start"])
            end = min(quarter.end_before, self._entry_end_of(p))
            study = copy.deepcopy(base)
            domain = study["domain"]
            domain["history_start"] = p["history_start"]
            domain["params"]["bridge"].update(fold=quarter.fold, start=start, end_before=end,
                                              protected_end_before=p["protected_end_before"])
            for role, relative in self._quarter_files(name).items():
                binding = domain["sources"][role]
                binding["files"] = {"shared": relative}
                path = PinnedPayloadSource(binding["pin"]).file(relative, verify=True)
                self._check_opened_pin(name, role, relative, path)
                if role == "forecasts":
                    import pyarrow.parquet as pq

                    if pq.ParquetFile(path).metadata.num_rows == 0:        # the reader refuses an empty file obscurely
                        raise EdgeCalibrationRefusal("forecast_quarter_empty", f"{relative} holds no forecast row")
            span = {"start": start, "end_before": end}
            bounds = {"forecasts": span, "contexts": span, "chain": {"start": start, "end_before": tail},
                      "bars": {"start": p["history_start"], "end_before": tail},
                      "sessions": {"start": p["history_start"], "end_before": tail}}
            root = Path(p["out_dir"]) / "inputs"
            quotes = root / name / "mio-quotes"
            quotes.mkdir(parents=True, exist_ok=True)
            bounded = MIOPacketInputs.read_bounded(study, bounds, span, quotes, root / "reuse")
            if not any(d["forecasts"] for d in bounded["inputs"].values()):
                raise EdgeCalibrationRefusal("forecast_quarter_empty", f"{name} yielded no forecast row")
            self._check_chain(name, bounded, span, tail)
            out[name] = bounded
        self._pass_cache = out
        return out

    def _check_opened_pin(self, quarter, role, relative, path):
        """Refuse unless the bytes about to be read hash to ``forecast_pins[quarter][role]`` (the per-file guard)."""
        with open(path, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != self.params["forecast_pins"][quarter][role]:
                raise EdgeCalibrationRefusal("forecast_not_pinned", f"the opened {relative} is not the pinned file")

    def _check_chain(self, quarter, bounded, span, tail):
        """Refuse ``chain_not_covered`` unless each forecast symbol's chain reaches its last fill session.

        A symbol with no forecast row in the pass is noted by name in ``self._idle``.
        """
        sessions = bounded["clocks"]["sessions"]
        for symbol, data in bounded["inputs"].items():
            decided = sorted(r["quote_date"] for r in data["forecasts"])
            if not decided:
                self._idle.setdefault(symbol, []).append(quarter)
                continue
            at = bisect.bisect_right(sessions, decided[-1])
            need = sessions[at] if at < len(sessions) else decided[-1]
            with sqlite3.connect(f"file:{data['chain_index']['index']['path']}?mode=ro", uri=True) as conn:
                count, last = conn.execute("SELECT COUNT(*), MAX(date) FROM rows").fetchone()
            if not count or last < need:
                raise EdgeCalibrationRefusal(
                    "chain_not_covered", f"{quarter} {symbol}: the chain holds {count} selected row(s), last date "
                    f"{last}, but the walk reads through {need} (decisions {decided[0]}..{decided[-1]}); bind a chain "
                    "file that covers the quarter in that quarter's document")

    @staticmethod
    def _entry_end_of(p):
        return min((date.fromisoformat(p["window"]["end"]) + timedelta(days=1)).isoformat(), p["sealed_from"])

    def _read_market(self, domain, tail):
        """Return ``{symbol: {"chain_index", "bars"}}`` (the seam; read-only) and note every quarter's index."""
        passes = self._passes()
        self._indexes, market = {}, {}
        for bounded in passes.values():
            for symbol, data in bounded["inputs"].items():
                self._indexes.setdefault(symbol, []).append(data["chain_index"]["index"])
                if symbol not in market:
                    market[symbol] = {"chain_index": {**data["chain_index"],
                                                      "basis_factors": dict(data["chain_index"]["basis_factors"])},
                                      "bars": [dict(b) for b in data["bars"]]}
                    continue
                # Later quarters add their own days (the chain files differ per document): union by date, the
                # daily basis factors too; a day two documents disagree on refuses.
                factors = market[symbol]["chain_index"]["basis_factors"]
                for day, factor in data["chain_index"]["basis_factors"].items():
                    if factors.setdefault(day, factor) != factor:
                        raise EdgeCalibrationRefusal("mio_document_invalid", f"{symbol} {day}: the documents' chains "
                                                     "carry different basis factors for one day")
                held = {b["date"]: b for b in market[symbol]["bars"]}
                for bar in data["bars"]:
                    if bar["date"] not in held:
                        market[symbol]["bars"].append(dict(bar))
                    elif "basis_factor" not in held[bar["date"]] and "basis_factor" in bar:
                        held[bar["date"]]["basis_factor"] = bar["basis_factor"]
        for data in market.values():
            data["bars"].sort(key=lambda b: b["date"])
        return market

    def _read_sessions(self, domain, tail):
        """Return the session calendar of the pinned ``sessions`` source (``read_bounded``'s ``clocks.sessions``)."""
        return sorted({d for bounded in self._passes().values() for d in bounded["clocks"]["sessions"]})

    def _read_forecasts(self, domain, start, end_before):
        """Return ``{symbol: {"forecasts", "contexts"}}`` for decisions in ``[start, end_before)`` (the seam)."""
        out = {s: {"forecasts": [], "contexts": []} for s in self._symbols}
        for bounded in self._passes().values():
            for symbol, data in bounded["inputs"].items():
                for key in ("forecasts", "contexts"):
                    out[symbol][key].extend(r for r in data[key] if start <= r["quote_date"] < end_before)
        return out

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
        history = p["history_start"]
        sessions = [d for d in self._read_sessions(self._domain, tail) if history <= d < tail]
        if not sessions or sessions[0] > (date.fromisoformat(history) + timedelta(days=4)).isoformat():
            raise EdgeCalibrationRefusal("history_not_covered", f"the session source starts "
                                         f"{sessions[0] if sessions else 'empty'}, after history_start {history}")
        self._sessions = sessions
        have, self._dropped = set(sessions), {}
        limit = (date.fromisoformat(history) + timedelta(days=4)).isoformat()
        for symbol, data in market.items():       # every symbol's lookback needs its own bars back to history_start
            first_bar = min((b["date"] for b in data["bars"]), default="empty")
            if first_bar > limit:
                raise EdgeCalibrationRefusal("history_not_covered", f"{symbol}'s bars start {first_bar}, after "
                                             f"history_start {history}")
        for symbol in list(market):
            dates = {b["date"] for b in market[symbol]["bars"]}
            market[symbol]["bars"] = [b for b in market[symbol]["bars"] if b["date"] in have]
            if have - dates:
                self._dropped[symbol] = len(have - dates)       # a symbol missing a session cannot enter the study
                del market[symbol]
        if not market:
            raise EdgeCalibrationRefusal("mio_document_invalid", "no symbol has a bar for every session")
        self._active = [s for s in self._symbols if s in market]
        self._market = market
        self._entry_end = min((date.fromisoformat(p["window"]["end"]) + timedelta(days=1)).isoformat(), tail)
        self._stored, self._qkey, self._qstudy = {}, None, None
        # MIO's node keeps ONE fold; every pass already kept only its own quarter's fold and band val, so the rows
        # are relabelled to one constant (decision dates are unique across quarters, so no key can collide).
        from .quarterly_forecasts import Quarter

        self._fold_label = Quarter.parse(self._quarters()[0]).fold
        forecasts = self._forecasts(p["window"]["start"], self._entry_end)
        if p["study_scope"] == "window":
            self._market_study = self._study = self._build(p["window"]["start"], self._entry_end, forecasts)
        else:
            empty = {s: {"forecasts": [], "contexts": []} for s in self._active}
            self._market_study = self._build(p["window"]["start"], self._entry_end, empty)

    def _forecasts(self, start, end_before):
        got = self._read_forecasts(self._domain, start, end_before)
        out = {}
        for symbol in self._active:
            rows = got[symbol]
            self._refuse_sealed(rows["forecasts"] + rows["contexts"], ("quote_date", "expiry", "settlement_date"))
            out[symbol] = {k: [{**r, "fold": self._fold_label} for r in rows[k] if start <= r["quote_date"] < end_before]
                           for k in rows}
        return out

    def _build(self, start, end_before, forecasts):
        from .ledger_studies import WeeklyOptionPortfolioStudy

        domain = self._domain
        clocks = {"sessions": self._sessions, "history_start": self.params["history_start"], "entry_start": start,
                  "entry_end_before": end_before, "tail_end_before": self._sealed}
        bridge = {**domain["params"]["bridge"], "fold": self._fold_label, "start": start, "end_before": end_before,
                  "protected_end_before": self.params["protected_end_before"]}
        study_params = {**domain["params"], "bridge": bridge, "symbols": list(self._active),
                        "selected_pairs": [x for x in self._pairs if x["symbol"] in self._active],
                        "clocks": clocks}
        inputs = {}
        for symbol in self._active:
            data = dict(self._market[symbol])
            if "chain" in data:
                data["chain"] = [r for r in data["chain"] if r["date"] >= start]
            inputs[symbol] = {**data, **forecasts[symbol]}
        study = WeeklyOptionPortfolioStudy(study_params, inputs)
        for symbol, descriptors in getattr(self, "_indexes", {}).items():
            if symbol in self._active and len(descriptors) > 1:
                study._quotes[symbol] = self._union(descriptors, symbol)
        return study

    @staticmethod
    def _union(descriptors, symbol):
        """Return one quote view over the per-quarter indexes (a decision's fill may sit in the next quarter's)."""
        from .ledger_studies import _SQLiteQuoteView

        class Union(_SQLiteQuoteView):
            def __init__(self):
                self.views = []
                for descriptor in descriptors:
                    view = _SQLiteQuoteView(descriptor)
                    view.symbol = symbol
                    self.views.append(view)
                self.symbol = symbol

            def get(self, key, default=None):
                for view in self.views:
                    row = view.get(key)
                    if row is not None:
                        return row
                return default

            def iter_for(self, day, expiry):
                for view in self.views:
                    rows = list(view.iter_for(day, expiry))
                    if rows:
                        return iter(rows)
                return iter(())

            def verify_digest(self):
                for view in self.views:
                    view.verify_digest()

            def close(self, verify=False):
                for view in self.views:
                    view.close(verify=verify)

        return Union()

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
        pins = {"symbol_list": hashlib.sha256(json.dumps(self._symbols).encode()).hexdigest()}
        if "mio_documents" in self.params:
            pins.update({f"mio_document:{q}": d["sha256"] for q, d in self.params["mio_documents"].items()})
        else:
            pins["mio_document"] = self._doc_sha
        for quarter in self._quarters():
            pins[f"forecast:{quarter}"] = self.params["forecast_pins"][quarter]["forecasts"]
            pins[f"contexts:{quarter}"] = self.params["forecast_pins"][quarter]["contexts"]
        return pins

    def _summary(self, weeks):
        """Return the walk's summary plus the child keys ADR-0313 X7 asks for (the walk has no extras hook)."""
        out = super()._summary(weeks)
        out["forecast_pins"] = {q: dict(self.params["forecast_pins"][q]) for q in self._quarters()}
        out["mio_quote_values"] = dict(self._quote_values)
        out["symbols_dropped"] = dict(self._dropped)
        out["symbols_absent"] = {s: list(q) for s, q in self._absent.items()}       # outside the quarter's universe
        out["symbols_no_forecast"] = {s: list(q) for s, q in self._idle.items()}    # in it, but no forecast row
        if self.params.get("direction") is not None:
            out["direction_params"] = dict(self.params["direction"])        # the study rebuilds e_dir with the SAME params
        out["symbol_list_sha256"] = hashlib.sha256(json.dumps(self._symbols).encode()).hexdigest()
        return out

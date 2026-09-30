"""Thin, non-serving Nodes binding condor diagnostics and the condor and put-spread backtests."""

import math
from abc import ABC, abstractmethod
from bisect import bisect_right
from collections import Counter
from datetime import date, timedelta
from itertools import product
from statistics import NormalDist
from typing import NamedTuple

from dskit.pipeline.distribution_models import REFERENCE_SCALE_FIELD
from dskit.pipeline.distribution_scores import (
    DEFAULT_OUTCOME_FIELD,
    DEFAULT_SAMPLES_FIELD,
    forecast_pair,
    row_in_split,
)
from dskit.pipeline.node import JsonArtifact, Node, check_int_param, reject_unknown_params
from dskit.pipeline.option_pricing import VolIndexSmileQuotes, black76_delta
from dskit.pipeline.records import number_ok, price_ok
from dskit.pipeline.split_policy import SPLIT_NAMES
from dskit.pipeline.stats import (
    expanding_percentile,
    expanding_quantile,
    lower_tail_mean,
    max_drawdown,
    newey_west_mean,
)

from .contracts import (
    CONDOR_LEGS,
    PUT_SPREAD_LEGS,
    STRUCTURES,
    CashIndexContract,
    DefinedRiskCondor,
    _IndexQuote,
    _IndexSettlement,
    _day,
    _decimal,
    _instant,
    _integer,
    _text,
    american_call_dividend,
    american_put_carry,
    dividends_paid,
    quote_problems,
    structure_credit,
)
from .distribution import CondorGeometry, condor_payoff, structure_payoff

__all__ = ["CondorBacktest", "CondorDistributionReport", "CondorPayoffDiagnostic",
           "CondorQuoteBacktest", "PayoffSelectQuoteBacktest", "PutSpreadQuoteBacktest",
           "VolRegimeSignals", "VolSizingWeights"]


class CondorPayoffDiagnostic(Node):
    """Bind exact synthetic rows and emit an explicitly persisted payoff report.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Corpus, four leg/version references, settlement reference, count and fees.

    Examples
    --------
    Load the shipped declaration from the child directory::

        import json
        from pathlib import Path
        params = json.loads(Path("configs/run-fixture.json").read_text())
        node = CondorPayoffDiagnostic("diagnostic",
                                     params["pipeline"]["diagnostic"]["params"])
    """

    role = "transform"
    outputs = ("report",)
    _PARAMS = ("corpus_id", "legs", "settlement", "count", "fees_usd")
    _LEG_FIELDS = ("contract_id", "contract_version", "quote_at", "quote_version", "quantity")
    _SETTLEMENT_FIELDS = ("settlement_id", "expiry", "row_version")

    @classmethod
    def validate_params(cls, params):
        """Check all configuration, including exact nested reference vocabularies.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All structural errors, or a domain-value error.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = [key for key in cls._PARAMS if key not in params]
        if missing:
            return problems + [f"missing params: {missing}"]
        try:
            _text(params["corpus_id"], "corpus_id")
            _integer(params["count"], "count", 1)
            if _decimal(params["fees_usd"], "fees_usd") < 0:
                raise ValueError("fees_usd must be nonnegative")
            legs = params["legs"]
            if not isinstance(legs, list) or len(legs) != 4:
                raise ValueError("legs must contain four exact references")
            for leg in legs:
                cls._reference(leg, cls._LEG_FIELDS, problems)
                for key in ("contract_id", "contract_version", "quote_version"):
                    _text(leg[key], key)
                _instant(leg["quote_at"], "quote_at")
                _integer(leg["quantity"], "quantity", -1)
            if tuple(leg["quantity"] for leg in legs) != (1, -1, -1, 1):
                raise ValueError("leg quantities must be [1, -1, -1, 1]")
            settlement = params["settlement"]
            cls._reference(settlement, cls._SETTLEMENT_FIELDS, problems)
            _text(settlement["settlement_id"], "settlement_id")
            _text(settlement["row_version"], "row_version")
            _day(settlement["expiry"], "expiry")
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(str(exc))
        return problems

    @staticmethod
    def _reference(value, fields, problems):
        """Validate one exact option/settlement reference before dereferencing."""
        if not isinstance(value, dict):
            raise ValueError("reference must be an object")
        reject_unknown_params(problems, value, fields)
        missing = [key for key in fields if key not in value]
        if missing:
            raise ValueError(f"missing reference fields: {missing}")

    @staticmethod
    def _select(rows, reference):
        """Resolve one unambiguous exact domain identity, not an as-of join."""
        matches = [row for row in rows if all(row[k] == v for k, v in reference.items())]
        if len(matches) != 1:
            raise ValueError(f"reference must match exactly one row: {reference}; got {len(matches)}")
        return matches[0]

    def _position(self, inputs):
        """Enforce the full boundary even when run is called outside the driver."""
        problems = self.validate_params(self.params)
        if problems:
            raise ValueError("; ".join(problems))
        if not isinstance(inputs, dict) or set(inputs) != {"contracts", "quotes", "settlements"}:
            raise ValueError("inputs must contain contracts, quotes and settlements")
        rows = {}
        for name, owner in (
            ("contracts", CashIndexContract), ("quotes", _IndexQuote),
            ("settlements", _IndexSettlement),
        ):
            if not isinstance(inputs[name], list) or not inputs[name]:
                raise ValueError(f"{name} must be a nonempty list")
            rows[name] = [dict(owner(row).data) for row in inputs[name]]
            if any(row["corpus_id"] != self.params["corpus_id"] for row in rows[name]):
                raise ValueError("all inputs must belong to the declared corpus_id")
        contracts, quotes = [], []
        for leg in self.params["legs"]:
            contracts.append(self._select(rows["contracts"], {
                "contract_id": leg["contract_id"], "row_version": leg["contract_version"],
            }))
            quote_instant = _instant(leg["quote_at"], "quote_at")
            at_instant = [
                row for row in rows["quotes"]
                if _instant(row["quote_at"], "quote_at") == quote_instant
            ]
            quotes.append(self._select(at_instant, {
                "contract_id": leg["contract_id"], "row_version": leg["quote_version"],
            }))
        settlement = self._select(rows["settlements"], self.params["settlement"])
        return DefinedRiskCondor(
            contracts, quotes, settlement, self.params["count"], self.params["fees_usd"],
            [leg["quantity"] for leg in self.params["legs"]],
        )

    def validate_inputs(self, inputs):
        """Report domain problems before the pipeline executes this diagnostic.

        Parameters
        ----------
        inputs : dict
            Three record streams.

        Returns
        -------
        list of str
            Empty on success; otherwise the domain refusal.
        """
        try:
            self._position(inputs)
        except (ValueError, TypeError) as exc:
            return [str(exc)]
        return []

    def run(self, ctx, inputs):
        """Compute via the domain owner, leaving persistence to DSKIT.

        Parameters
        ----------
        ctx : NodeContext or None
            Unused; this Node performs no I/O.
        inputs : dict
            Canonical contract, quote and settlement streams.

        Returns
        -------
        dict
            A report wrapped in JsonArtifact.

        Raises
        ------
        ValueError
            If any configuration, row or exact reference is invalid.
        """
        return {"report": JsonArtifact(self._position(inputs).evaluate())}


class CondorDistributionReport(Node):
    """Evaluate one standardized condor under every forecast row of a split.

    Reads sample-set forecast rows (draws of standardized log return, the
    realized value, the reference scale) and a forward level per row, maps
    the declared ``strikes_z`` to strikes at each entry, and reports mean
    forecast expected P&L, credit-keep and beyond-wing probabilities and
    CVaR beside their realized counterparts over the SAME rows (those with
    an outcome) — all per unit of narrower wing.
    Role ``score``: it measures, and a synthetic report is never
    decision-eligible.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        ``split`` (required), ``strikes_z`` (four increasing numbers,
        required), ``credit_fraction`` (in (0, 1), required),
        ``cvar_alpha`` (in (0, 1), default 0.95), ``forward_field``
        (default ``"close"``), ``samples_field`` / ``outcome_field``
        (dskit's defaults).

    Examples
    --------
    Shorts at one reference standard deviation, wings at two::

        node = CondorDistributionReport("condor", {
            "split": "val", "strikes_z": [-2.0, -1.0, 1.0, 2.0],
            "credit_fraction": 0.3,
        })
        out = node.run(ctx, {"forecasts": rows})
    """

    role = "score"
    outputs = ("metrics", "report")
    _PARAMS = ("credit_fraction", "cvar_alpha", "forward_field", "outcome_field",
               "samples_field", "split", "strikes_z")
    DEFAULT_CVAR_ALPHA = 0.95
    DEFAULT_FORWARD_FIELD = "close"

    @classmethod
    def validate_params(cls, params):
        """Check the declaration; every problem is reported.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if params.get("split") not in SPLIT_NAMES:
            problems.append(f"split must name one of {list(SPLIT_NAMES)}")
        for knob in ("strikes_z", "credit_fraction"):
            if knob not in params:
                problems.append(f"{knob} is required")
        problems.extend(CondorGeometry.problems(
            params.get("strikes_z"), params.get("credit_fraction"),
            params.get("cvar_alpha", cls.DEFAULT_CVAR_ALPHA)))
        for knob in ("forward_field", "samples_field", "outcome_field"):
            value = params.get(knob, "x")
            if not isinstance(value, str) or not value:
                problems.append(f"{knob} must be a nonempty string")
        return problems

    def validate_inputs(self, inputs):
        """Require a list of forecast rows.

        Parameters
        ----------
        inputs : dict
            The wired inputs.

        Returns
        -------
        list of str
            Empty when usable.
        """
        if not isinstance(inputs.get("forecasts"), list):
            return ["forecasts must be a list of forecast rows"]
        return []

    def _entries(self, ctx, rows):
        """Evaluate every in-split row with a forecast, forward, scale and outcome."""
        geometry = CondorGeometry(self.params["strikes_z"], self.params["credit_fraction"],
                                  self.params.get("cvar_alpha", self.DEFAULT_CVAR_ALPHA))
        forward_field = self.params.get("forward_field", self.DEFAULT_FORWARD_FIELD)
        samples = self.params.get("samples_field", DEFAULT_SAMPLES_FIELD)
        outcome = self.params.get("outcome_field", DEFAULT_OUTCOME_FIELD)
        entries, unscored = [], 0
        for row in rows:
            if not row_in_split(ctx, row, self.params["split"]):
                continue
            reason, dist, outcome_z = forecast_pair(row, samples, outcome)
            forward, scale = row.get(forward_field), row.get(REFERENCE_SCALE_FIELD)
            if reason or not (price_ok(forward) and price_ok(scale)):
                unscored += 1
                continue
            entries.append(geometry.evaluate(dist.samples, outcome_z, forward, scale))
        return geometry, entries, unscored

    def run(self, ctx, inputs):
        """Aggregate forecast and realized condor outcomes.

        Parameters
        ----------
        ctx : NodeContext or None
            Its splits decide membership.
        inputs : dict
            ``forecasts``: sample-set forecast rows.

        Returns
        -------
        dict
            ``metrics`` (numbers) and ``report`` (a JsonArtifact).

        Raises
        ------
        ValueError
            When no in-split row carries a usable forecast, or a row's
            draws are not finite numbers (an empty list included).
        """
        geometry, entries, unscored = self._entries(ctx, inputs["forecasts"])
        if not entries:
            raise ValueError(f"{self.key}: no in-split row carries a forecast and an "
                             f"outcome in split {self.params['split']!r}")
        realized = [e["realized_pnl"] for e in entries]

        def mean(key):
            return sum(e[key] for e in entries) / len(entries)

        metrics = {
            "n": len(entries), "n_skipped_unscorable": unscored,
            "forecast_expected_pnl": mean("expected_pnl"),
            "forecast_p_full_credit": mean("p_full_credit"),
            "forecast_p_beyond_wings": mean("p_beyond_wings"),
            "forecast_cvar": mean("cvar"),
            "realized_mean_pnl": sum(realized) / len(realized),
            "realized_full_credit_rate": sum(
                p >= geometry.credit_fraction for p in realized) / len(realized),
            "realized_cvar": geometry.cvar(realized),
        }
        report = {"kind": "synthetic_distribution_diagnostic", "decision_eligible": False,
                  "units": "narrower wing width", "strikes_z": list(geometry.strikes_z),
                  "credit_fraction": geometry.credit_fraction,
                  "cvar_alpha": geometry.cvar_alpha, "metrics": metrics}
        return {"metrics": metrics, "report": JsonArtifact(report)}


class _CondorBacktestBase(Node, ABC):
    """Non-overlapping three-book condor backtests over one split.

    The shared half of :class:`CondorBacktest` (VIX-proxy prices) and
    :class:`CondorQuoteBacktest` (archived quotes), ADR-0187 item 5: the
    per-instrument walk in ``asof_ms`` order, the scorable-row rule
    (:func:`~dskit.pipeline.distribution_scores.forecast_pair` plus the
    forward and reference scale), the three-book cell vocabulary and the
    flat ``<book>_<stat>`` metrics. A member supplies how one entry is
    priced and settled (:meth:`_entry`), where the next position may start
    (:meth:`_cursor_after` / :meth:`_before_cursor`), what it needs before
    the walk (:meth:`_prepare`), when a walk is refused (:meth:`_check_walk`)
    and its report (:meth:`_report`).

    **Entry-gate annotation (ADR-0194).** A member may declare
    ``gate_fields``, distinct names of boolean-or-``None`` fields on the
    forecast rows. Each entry's ledger row then carries ``gates``, the values
    read from the ENTRY row, and every book reports, per gate, how its
    traded cells split by the gate: ``<book>_<field>_closed_n`` /
    ``_closed_mean_pnl_usd`` / ``_closed_t`` (gate ``True``: it would have
    stood aside), the same three ``_open_*`` (``False``), and
    ``_unknown_n`` (``None``). Nothing is skipped: the trades are the trades
    the un-annotated node makes, and absent ``gate_fields`` the outputs are
    exactly those. A non-bool, non-``None`` value, or a declared field the
    entry row lacks, refuses.

    **Entry-size annotation (ADR-0196).** A member may also declare
    ``size_fields``, distinct names of forecast-row fields holding a positive
    finite weight, or ``None`` where it is unknown. Each ledger entry then
    carries ``sizes``, the weights read from the ENTRY row, and every book
    reports, per field and over its traded cells that carry a weight, the book
    as if each trade had been taken at that many contracts:
    ``<book>_<field>_n``, ``_mean_weight``, ``_total_pnl_usd`` (the sum of
    ``w x pnl``), ``_pnl_per_weight`` (that sum over the sum of ``w``: comparable
    to the unsized mean), ``_t`` (of ``w x pnl``), ``_cvar_usd``,
    ``_max_drawdown_usd`` (both on the ``w x pnl`` sequence) and ``_unknown_n``
    (no weight). Nothing is skipped and no trade changes (one contract stays
    one contract): absent ``size_fields`` the outputs are exactly those of the
    un-annotated node. A weight that is neither a positive finite number nor
    ``None``, or a declared field the entry row lacks, refuses.
    """

    role = "score"
    outputs = ("metrics", "report")
    BOOKS = ("model", "always", "implied")
    #: Why an in-split row cannot be an entry, in check order; a member extends it.
    ROW_REASONS = ("no_outcome", "no_forecast", "no_forward", "no_scale")
    #: Why a book skips an entry; a member declares its own.
    BOOK_REASONS = ()
    #: Knobs every member takes with these defaults.
    DEFAULTS = {"min_edge_usd": 0.0, "cvar_alpha": 0.95}
    #: Optional knobs every member accepts, no default: absent means the feature is off.
    SHARED_OPTIONAL = ("gate_fields", "size_fields")
    FORWARD_FIELD = "close"

    @staticmethod
    def _mean(values):
        """Return the mean of ``values``; 0.0 when there are none."""
        return sum(values) / len(values) if values else 0.0

    @classmethod
    def _pnl_stats(cls, prefix, pnls, start=0):
        """Return ``<prefix>_total_pnl_usd`` / ``_mean_pnl_usd`` / ``_hit_rate`` over ``pnls`` (a hit is ``p > 0``); ``start`` is the total's empty value."""
        return {f"{prefix}_total_pnl_usd": sum(pnls, start),
                f"{prefix}_mean_pnl_usd": cls._mean(pnls),
                f"{prefix}_hit_rate": cls._mean([p > 0 for p in pnls])}

    @staticmethod
    def _t(values):
        """Return the ``lags=0`` Newey-West t of the mean; 0.0 for under two values or where the stats owner finds no variance."""
        if len(values) < 2:
            return 0.0
        t = newey_west_mean(list(values), lags=0)["t"]
        return 0.0 if t is None else t

    @staticmethod
    def _short_q_ok(value):
        """Say whether ``value`` is a usable short-leg quantile: a number in (0, 0.5)."""
        return number_ok(value) and 0 < value < 0.5

    @classmethod
    def _shared_problems(cls, problems, params):
        """Check the knobs every member declares: split, short_q, multiplier, fees, gate, tail."""
        if params.get("split") not in SPLIT_NAMES:
            problems.append(f"split must name one of {list(SPLIT_NAMES)}")
        q = params.get("short_q")
        if not cls._short_q_ok(q):
            problems.append(f"short_q must be in (0, 0.5), got {q!r}")
        check_int_param(problems, "multiplier", params.get("multiplier"), ge=1)
        fee = params.get("fee_per_leg")
        if not number_ok(fee) or fee < 0:
            problems.append(f"fee_per_leg must be a nonnegative number, got {fee!r}")
        edge = params.get("min_edge_usd", cls.DEFAULTS["min_edge_usd"])
        if not number_ok(edge):
            problems.append(f"min_edge_usd must be a finite number, got {edge!r}")
        alpha = params.get("cvar_alpha", cls.DEFAULTS["cvar_alpha"])
        if not number_ok(alpha) or not 0 < alpha < 1:
            problems.append(f"cvar_alpha must be in (0, 1), got {alpha!r}")
        for name in cls.SHARED_OPTIONAL:
            cls._distinct_list_problems(problems, params, name, cls._name_ok,
                                        "non-empty strings")

    @staticmethod
    def _name_ok(value):
        """Say whether ``value`` is a usable field name: a non-empty string."""
        return isinstance(value, str) and bool(value)

    @staticmethod
    def _distinct_list_problems(problems, params, name, accepts, what):
        """Check a declared ``params[name]`` is a non-empty list of distinct values ``accepts`` takes; absent is fine."""
        if name not in params:
            return
        values = params[name]
        if (not isinstance(values, list) or not values
                or not all(accepts(v) for v in values) or len(set(values)) != len(values)):
            problems.append(f"{name} must be a non-empty list of distinct {what}, got {values!r}")

    def validate_inputs(self, inputs):
        """Require a list of forecast rows, plus whatever the member needs.

        Parameters
        ----------
        inputs : dict
            The wired inputs.

        Returns
        -------
        list of str
            Empty when usable.
        """
        if not isinstance(inputs.get("forecasts"), list):
            return ["forecasts must be a list of forecast rows"]
        return self._extra_input_problems(inputs)

    def _extra_input_problems(self, inputs):
        """Problems with a member's other inputs; none by default."""
        return []

    def _knob(self, name):
        """Return a declared knob or its default."""
        return self.params.get(name, self.DEFAULTS.get(name))

    def _row_reason(self, row):
        """Say why a scorable forecast row cannot be priced, or ``None``."""
        if not price_ok(row.get(self.FORWARD_FIELD)):
            return "no_forward"
        if not price_ok(row.get(REFERENCE_SCALE_FIELD)):
            return "no_scale"
        return None

    @abstractmethod
    def _prepare(self, inputs):
        """Build what the walk needs from the inputs; refuse unusable plumbing."""

    @abstractmethod
    def _entry(self, row, dist, outcome_z):
        """Price and settle one entry: ``(entry, None)``, or ``(None, reason)``."""

    @abstractmethod
    def _cursor_after(self, index, entry):
        """Return where the next position may start after ``entry`` at ``index``."""

    @abstractmethod
    def _before_cursor(self, index, row, cursor):
        """Say whether ``row`` at ``index`` still lies inside the open position."""

    @abstractmethod
    def _check_walk(self, ledger, skipped, n_in_split):
        """Refuse a walk the member cannot report on."""

    @abstractmethod
    def _report(self, ledger, metrics):
        """Return the member's JSON report."""

    def _book_extras(self, traded):
        """Extra flat metrics per book from its traded cells; none by default."""
        return {}

    def _read_fields(self, row, knob, what, accepts, expected):
        """Return ``{field: value}`` for the names ``params[knob]`` declares, read from an entry row; refuse an absent field or a value ``accepts`` does not take."""
        where = f"{row.get('instrument')} {row.get('date')}"
        values = {}
        for field in self.params[knob]:
            if field not in row:
                raise ValueError(f"{self.key}: {what} field {field!r} is absent from the "
                                 f"forecast row {where} — a misspelt {knob} name, or rows "
                                 "that did not come through the node that writes it")
            value = row[field]
            if not accepts(value):
                raise ValueError(f"{self.key}: {what} field {field!r} on {where} must be "
                                 f"{expected}, got {value!r}")
            values[field] = value
        return values

    def _read_gates(self, row):
        """Return ``{gate: bool or None}`` read from an entry row."""
        return self._read_fields(row, "gate_fields", "gate",
                                 lambda v: v is None or isinstance(v, bool), "a bool or None")

    def _read_sizes(self, row):
        """Return ``{field: weight or None}`` read from an entry row; a weight is a positive finite number."""
        return self._read_fields(row, "size_fields", "size",
                                 lambda v: v is None or price_ok(v),
                                 "a positive finite number or None")

    def _walk(self, ctx, rows):
        """Walk each instrument's in-split rows oldest first, one open position at a time."""
        by_instrument = {}
        for row in rows:
            if row_in_split(ctx, row, self.params["split"]):
                by_instrument.setdefault(str(row.get("instrument")), []).append(row)
        ledger, skipped = [], Counter()
        for instrument in sorted(by_instrument):
            ordered = sorted(by_instrument[instrument], key=lambda r: r["asof_ms"])
            cursor = None
            for index, row in enumerate(ordered):
                if cursor is not None and self._before_cursor(index, row, cursor):
                    continue
                reason, dist, outcome_z = forecast_pair(
                    row, DEFAULT_SAMPLES_FIELD, DEFAULT_OUTCOME_FIELD)
                reason = reason or self._row_reason(row)
                if reason is None:
                    entry, reason = self._entry(row, dist, outcome_z)
                if reason is not None:
                    skipped[reason] += 1
                    continue
                if "gate_fields" in self.params:
                    entry["gates"] = self._read_gates(row)
                if "size_fields" in self.params:
                    entry["sizes"] = self._read_sizes(row)
                ledger.append(entry)
                cursor = self._cursor_after(index, entry)
        return ledger, skipped, sum(len(v) for v in by_instrument.values())

    def _gate_field_metrics(self, book, entries):
        """Split a book's traded entries by each gate: n, mean P&L and t where closed and open, and the unknowns."""
        metrics = {}
        for field in self.params.get("gate_fields", ()):
            by_state = {True: [], False: [], None: []}
            for entry in entries:
                by_state[entry["gates"][field]].append(entry["books"][book]["pnl_usd"])
            for state, label in ((True, "closed"), (False, "open")):
                pnls = by_state[state]
                metrics.update({f"{book}_{field}_{label}_n": len(pnls),
                                f"{book}_{field}_{label}_mean_pnl_usd": self._mean(pnls),
                                f"{book}_{field}_{label}_t": self._t(pnls)})
            metrics[f"{book}_{field}_unknown_n"] = len(by_state[None])
        return metrics

    def _weighted(self, book, field, entries):
        """Return ``(weights, sized, n_traded)``: the ``field`` weights of a book's traded entries that have one, each ``w x pnl``, and how many entries traded."""
        pairs = [(entry["sizes"][field], entry["books"][book]["pnl_usd"]) for entry in entries]
        known = [(w, w * pnl) for w, pnl in pairs if w is not None]
        return [w for w, _sized in known], [sized for _w, sized in known], len(pairs)

    def _size_field_metrics(self, book, entries):
        """Report a book's traded entries as if each had been taken at its weight, per size field."""
        alpha = self._knob("cvar_alpha")
        metrics = {}
        for field in self.params.get("size_fields", ()):
            weights, sized, n_traded = self._weighted(book, field, entries)
            total = sum(sized, 0.0)
            metrics.update({
                f"{book}_{field}_n": len(sized),
                f"{book}_{field}_mean_weight": self._mean(weights),
                f"{book}_{field}_total_pnl_usd": total,
                f"{book}_{field}_pnl_per_weight": total / sum(weights) if weights else 0.0,
                f"{book}_{field}_t": self._t(sized),
                f"{book}_{field}_cvar_usd": lower_tail_mean(sized, alpha) if sized else 0.0,
                f"{book}_{field}_max_drawdown_usd": max_drawdown(sized),
                f"{book}_{field}_unknown_n": n_traded - len(sized)})
        return metrics

    def _metrics(self, ledger, skipped, n_in_split):
        """Flatten per-book summaries into objective-addressable numbers."""
        metrics = {"n_rows_in_split": n_in_split, "n_entries": len(ledger)}
        for reason in self.ROW_REASONS:
            metrics[f"n_skipped_{reason}"] = skipped.get(reason, 0)
        alpha = self._knob("cvar_alpha")
        for book in self.BOOKS:
            cells = [entry["books"][book] for entry in ledger]
            traded = [c for c in cells if c["entered"]]
            pnls = [c["pnl_usd"] for c in traded]
            metrics.update({
                f"{book}_n_trades": len(pnls),
                **self._pnl_stats(book, pnls),
                f"{book}_cvar_usd": lower_tail_mean(pnls, alpha) if pnls else 0.0,
                f"{book}_max_drawdown_usd": max_drawdown(pnls),
                f"{book}_mean_credit_usd": self._mean([c["credit_usd"] for c in traded]),
            })
            metrics.update({f"{book}_{k}": v for k, v in self._book_extras(traded).items()})
            entered = [entry for entry in ledger if entry["books"][book]["entered"]]
            metrics.update(self._gate_field_metrics(book, entered))
            metrics.update(self._size_field_metrics(book, entered))
            for reason in self.BOOK_REASONS:
                metrics[f"{book}_n_skipped_{reason}"] = sum(
                    c["reason"] == reason for c in cells)
        return metrics

    def run(self, ctx, inputs):
        """Backtest the three books over the split.

        Parameters
        ----------
        ctx : NodeContext
            Its splits decide membership.
        inputs : dict
            ``forecasts`` (sample-set forecast rows) and the member's own
            inputs.

        Returns
        -------
        dict
            ``metrics`` (flat numbers, ``<book>_<stat>``; a book with no
            trades reports zeros beside ``<book>_n_trades = 0``) and
            ``report`` (a JsonArtifact with the per-trade ledger).

        Raises
        ------
        ValueError
            When the member refuses the inputs or the walk (see the class).
        """
        self._prepare(inputs)
        ledger, skipped, n_in_split = self._walk(ctx, inputs["forecasts"])
        self._check_walk(ledger, skipped, n_in_split)
        metrics = self._metrics(ledger, skipped, n_in_split)
        return {"metrics": metrics, "report": JsonArtifact(self._report(ledger, metrics))}


class CondorBacktest(_CondorBacktestBase):
    """Non-overlapping iron condors over one split, priced by the VIX proxy.

    ADR-0182 item 5. Entries are the split's rows in ``asof_ms`` order (per
    instrument): a row carrying a forecast, a realized outcome, a ``close``
    forward, an ``iv_index`` (VIX) and a reference scale is an entry, and
    the next ``hold_steps - 1`` rows are passed over so no two condors
    overlap; a row missing any of those is counted by reason and the next
    row is tried. Each entry settles at ``S_T = F exp(scale * outcome)`` —
    the forecast row's outcome is the standardized label
    ``ln(S_T / F) / scale``, so this is ``close * exp(label)``, the SPXW PM
    expiry ``hold_steps`` trading days out that the proxy assumes.

    Three books share every entry and every price:

    * ``model`` — short put at the forecast's ``short_q`` quantile, short
      call at ``1 - short_q``; entered only when the condor's expected P&L
      under the forecast draws, at proxy prices net of fees, exceeds
      ``min_edge_usd``;
    * ``always`` — the same strikes, every entry;
    * ``implied`` — shorts at the VIX-lognormal ``short_q`` quantiles
      (``ln(K/F) = -s^2/2 + s z_q``, ``s = VIX/100 sqrt(T)``), every entry.

    Wings sit ``wing_points`` index points beyond each short, or
    ``wing_z`` units of the book's own scale beyond it (exactly one is
    declared); every strike rounds to ``strike_increment``. Short legs sell
    at the proxy bid, long legs buy at the proxy ask; credit is
    ``(short bids - long asks) * multiplier - 4 * fee_per_leg`` USD, fees
    at entry only (cash settlement). Pricing, tail mean and drawdown are
    dskit's (:class:`~dskit.pipeline.option_pricing.VolIndexSmileQuotes`
    with the VIX close as its vol-index level,
    :func:`~dskit.pipeline.stats.lower_tail_mean`,
    :func:`~dskit.pipeline.stats.max_drawdown`); this node owns only the
    condor. A condor whose rounded strikes are not
    strictly increasing, or whose credit is not positive, is skipped and
    counted. Role ``score``; ``decision_eligible`` is false while pricing
    is the proxy. ``run`` raises when no in-split row can be an entry.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Required: ``split``, ``short_q`` (in (0, 0.5)), one of
        ``wing_points`` / ``wing_z`` (positive), ``strike_increment``
        (positive), ``multiplier`` (int >= 1), ``fee_per_leg`` (>= 0) and
        the :class:`~dskit.pipeline.option_pricing.VolIndexSmileQuotes`
        knobs ``atm_ratio``, ``put_skew_per_z``, ``call_skew_per_z``,
        ``smile_curvature``, ``iv_floor``, ``iv_ceiling``,
        ``half_spread_min``, ``half_spread_frac``. Optional: ``hold_steps`` (21),
        ``trading_days_per_year`` (252; ``T = hold_steps / it``),
        ``min_edge_usd`` (0), ``cvar_alpha`` (0.95), ``rate`` (0),
        ``gate_fields``, ``size_fields`` (see :class:`_CondorBacktestBase`).

    Examples
    --------
    Ten-percent shorts, wings half a reference SD further out::

        node = CondorBacktest("backtest", {
            "split": "val", "short_q": 0.1, "wing_z": 0.5, "strike_increment": 5,
            "multiplier": 100, "fee_per_leg": 0.65, "atm_ratio": 0.794,
            "put_skew_per_z": 0.176, "call_skew_per_z": -0.064, "smile_curvature": 0.045,
            "iv_floor": 0.05, "iv_ceiling": 2.0, "half_spread_min": 0.20,
            "half_spread_frac": 0.005,
        })
        out = node.run(ctx, {"forecasts": rows})
        out["metrics"]["model_total_pnl_usd"]
    """

    #: Why an in-split row cannot be an entry, in the order they are checked.
    ROW_REASONS = _CondorBacktestBase.ROW_REASONS + ("no_iv",)
    #: Why a book skips an entry.
    BOOK_REASONS = ("degenerate_strikes", "nonpositive_credit", "below_min_edge")
    DEFAULTS = {"hold_steps": 21, "trading_days_per_year": 252, "min_edge_usd": 0.0,
                "cvar_alpha": 0.95, "rate": 0.0}
    _REQUIRED = ("split", "short_q", "strike_increment", "multiplier", "fee_per_leg",
                 "atm_ratio", "put_skew_per_z", "call_skew_per_z", "smile_curvature",
                 "iv_floor", "iv_ceiling", "half_spread_min", "half_spread_frac")
    _WINGS = ("wing_points", "wing_z")
    _PARAMS = tuple(sorted(_REQUIRED + _WINGS + tuple(DEFAULTS)
                           + _CondorBacktestBase.SHARED_OPTIONAL))
    IV_FIELD = "iv_index"

    @classmethod
    def validate_params(cls, params):
        """Check the declaration; every problem is reported.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = [k for k in cls._REQUIRED if k not in params]
        if missing:
            problems.append(f"missing params: {missing}")
        cls._shared_problems(problems, params)
        wings = [k for k in cls._WINGS if k in params]
        if len(wings) != 1:
            problems.append(f"declare exactly one of {list(cls._WINGS)}, got {wings}")
        for knob in wings + ["strike_increment"]:
            if not price_ok(params.get(knob)):
                problems.append(f"{knob} must be a positive number, got {params.get(knob)!r}")
        for knob in ("hold_steps", "trading_days_per_year"):
            check_int_param(problems, knob, params.get(knob, cls.DEFAULTS[knob]), ge=1)
        problems.extend(VolIndexSmileQuotes.problems(
            *(params.get(k, cls.DEFAULTS.get(k)) for k in VolIndexSmileQuotes.KNOBS)))
        return problems

    def _row_reason(self, row):
        """Add the VIX to the shared forward and scale checks."""
        reason = super()._row_reason(row)
        if reason is None and not price_ok(row.get(self.IV_FIELD)):
            return "no_iv"
        return reason

    def _prepare(self, inputs):
        """Build the proxy quote model and the expiry in years."""
        self._quotes = VolIndexSmileQuotes(*(self._knob(k) for k in VolIndexSmileQuotes.KNOBS))
        self._years = self._knob("hold_steps") / self._knob("trading_days_per_year")

    def _entry(self, row, dist, outcome_z):
        """Every priceable row is an entry under the proxy."""
        return self._evaluate(self._quotes, row, dist, outcome_z, self._years), None

    def _cursor_after(self, index, entry):
        """Return the index ``hold_steps`` rows on, where the next entry may start."""
        return index + int(self._knob("hold_steps"))

    def _before_cursor(self, index, row, cursor):
        """Rows inside the hold window are passed over, uncounted."""
        return index < cursor

    def _check_walk(self, ledger, skipped, n_in_split):
        """Refuse a split with no entry at all."""
        if not ledger:
            raise ValueError(f"{self.key}: no in-split row carries a forecast, outcome, "
                             f"forward, scale and iv_index in split "
                             f"{self.params['split']!r} (skipped {dict(skipped)})")

    def _strikes(self, forward, z_put, z_call, scale):
        """Round a condor's four strikes, or ``None`` when they degenerate."""
        short_put, short_call = (forward * math.exp(scale * z) for z in (z_put, z_call))
        if "wing_points" in self.params:
            long_put = short_put - self.params["wing_points"]
            long_call = short_call + self.params["wing_points"]
        else:
            wing = self.params["wing_z"]
            long_put = forward * math.exp(scale * (z_put - wing))
            long_call = forward * math.exp(scale * (z_call + wing))
        step = self.params["strike_increment"]
        # outward, never toward the money: puts down, calls up, so a listed
        # strike is never riskier than the quantile asked for
        strikes = (math.floor(long_put / step) * step, math.floor(short_put / step) * step,
                   math.ceil(short_call / step) * step, math.ceil(long_call / step) * step)
        if not 0 < strikes[0] < strikes[1] < strikes[2] < strikes[3]:
            return None
        return strikes

    def _credit_usd(self, quotes, forward, strikes, vix, years):
        """Sell shorts at the bid, buy wings at the ask, net of entry fees."""
        per_share = 0.0
        for (right, sign), strike in zip(CONDOR_LEGS, strikes):
            bid, _mid, ask = quotes.quote(right, forward, strike, vix, years)
            per_share += bid if sign < 0 else -ask
        return per_share * self.params["multiplier"] - 4 * self.params["fee_per_leg"]

    def _evaluate(self, quotes, row, dist, outcome_z, years):
        """Price and settle all three books at one entry."""
        forward, scale = row[self.FORWARD_FIELD], row[REFERENCE_SCALE_FIELD]
        vix = row[self.IV_FIELD]
        settlement = forward * math.exp(scale * outcome_z)
        q, multiplier = self.params["short_q"], self.params["multiplier"]
        implied_scale = vix / 100.0 * math.sqrt(years)
        z_implied = NormalDist().inv_cdf(q)
        model = self._strikes(forward, dist.quantile(q), dist.quantile(1 - q), scale)
        candidates = {"model": model, "always": model, "implied": self._strikes(
            forward, z_implied - implied_scale / 2, -z_implied - implied_scale / 2,
            implied_scale)}
        books, expected = {}, None
        for book in self.BOOKS:
            strikes = candidates[book]
            if strikes is None:
                books[book] = {"strikes": None, "credit_usd": None, "pnl_usd": None,
                               "entered": False, "reason": "degenerate_strikes"}
                continue
            credit = self._credit_usd(quotes, forward, strikes, vix, years)
            cell = {"strikes": list(strikes), "credit_usd": credit, "pnl_usd": None,
                    "entered": False, "reason": None}
            if credit <= 0:
                cell["reason"] = "nonpositive_credit"
            elif book == "model":
                expected = credit + multiplier * sum(
                    condor_payoff(forward * math.exp(scale * z), strikes)
                    for z in dist.samples) / len(dist.samples)
                if not expected > self._knob("min_edge_usd"):
                    cell["reason"] = "below_min_edge"
            if cell["reason"] is None:
                cell["entered"] = True
                cell["pnl_usd"] = credit + multiplier * condor_payoff(settlement, strikes)
            books[book] = cell
        return {"date": row.get("date"), "asof_ms": row["asof_ms"],
                "instrument": row.get("instrument"), "forward": forward,
                "iv_index": vix, "reference_scale": scale, "settlement": settlement,
                "model_expected_pnl_usd": expected, "books": books}

    def _report(self, ledger, metrics):
        """Return the proxy report; never decision-eligible."""
        return {"kind": "vix_proxy_condor_backtest", "pricing": "vix_proxy",
                "decision_eligible": False,
                "units": "USD per condor, one contract per leg",
                "params": {k: self._knob(k) for k in self._PARAMS if k in self.params
                           or k in self.DEFAULTS},
                "years_to_expiry": self._years, "metrics": metrics, "ledger": ledger}


def _sides_of(legs):
    """Return the distinct rights of a leg tuple in leg order: one wing of the P&L each."""
    return tuple(dict.fromkeys(right for right, _sign in legs))


class _EntryFacts(NamedTuple):
    """What every book of one entry prices and settles against."""

    instrument: str
    day: str
    settle_date: str
    dte: int
    forward: float
    settlement: float
    listed: dict
    dist: object


class CondorQuoteBacktest(_CondorBacktestBase):
    """Non-overlapping iron condors on one underlying, priced from archived quotes.

    ADR-0187. The sibling of :class:`CondorBacktest` for the ETF track: one
    cell (an underlying and a days-to-expiry bucket) whose ``chain`` input
    is :class:`~index_options.observations.ChainQuoteRows` (already bounded
    to the bucket) and whose ``underlying`` input is the same symbol's
    :class:`~index_options.observations.IndexCloseRows` (closes and
    ex-dividend amounts). Everything is indexed by ``(instrument, date)``;
    the walk is the base's.

    At an entry date ``t``:

    1. **Chain.** No admitted row that date is ``no_chain``. A chain row whose
       ``underlying_price`` is not the forecast row's ``close`` refuses (one
       archive number, so a difference is misalignment), as does a row
       whose ``dte`` lies outside this node's ``dte_min``/``dte_max``.
    2. **Expiry.** The smallest DTE listed that date; ``settle_date`` is the
       reader's (the last weekday on or before the OCC expiry).
    3. **Settlement.** ``S_T`` is the underlying close of the last row dated
       on or before ``settle_date``, valid only within 4 calendar days of it
       and when a later row exists; otherwise the entry is ``unsettled`` and
       never traded.
    4. **Strikes (model, always).** ``n`` weekdays in ``(t, settle_date]``
       rescale the reference scale, ``s' = scale sqrt(n / label_horizon)``;
       targets ``K = S exp(s' Q(q))`` at ``short_q`` / ``1 - short_q`` with
       wings ``wing_z`` further out, each snapped OUTWARD onto that expiry's
       QUOTABLE listed strikes (a short leg needs a positive bid and a bid
       size, a long leg a positive ask and an ask size —
       :func:`~index_options.contracts.quote_problems`). A target beyond
       ``max_abs_log_moneyness`` is ``target_outside_band``, a side with no
       quotable strike is ``no_quotable_strike``, a non-increasing geometry
       ``degenerate_strikes``.
    5. **Implied.** The proxy's formula with the chain's own vol: the mean
       call/put ``iv`` at the valid strike nearest ``S`` (``no_atm_iv``
       otherwise), ``s = iv sqrt(DTE / 365)``.
    6. **Credit.** :func:`~index_options.contracts.structure_credit` over
       :attr:`LEGS`; the USD credit is ``credit x multiplier - len(LEGS) x
       fee_per_leg``; a book is ``nonpositive_credit`` when that is not
       positive and ``credit_not_below_width`` when the per-share credit
       reaches the narrowest vertical width. The model book must also clear
       ``min_edge_usd`` in expectation under the draws, scaled by ``s'``.
    7. **P&L.** ``credit_usd + multiplier x structure_payoff(S_T) -`` the
       American charge: ONE rule over whatever legs a trade holds (ADR-0195),
       :func:`~index_options.contracts.american_put_carry` at ``carry_rate`` on
       the short put and :func:`~index_options.contracts.american_call_dividend`
       on the short call, each only where the structure has that short leg (for
       the condor their sum is ``american_short_charge``); a session in the
       window with no ``dividend_amount`` refuses wherever the short call is
       held.
    8. **Wing split (ADR-0193).** An entered cell also carries
       ``side_pnl_usd``, one entry per side in :attr:`SIDES` (``"put"``,
       ``"call"``): that side's own bid/ask credit, its legs' fees, its
       settlement payoff and its own American charge (put carry for the put
       side, dividends for the call side). The sides sum to ``pnl_usd``.
       Metrics add ``<book>_<side>_total_pnl_usd``, ``_mean_pnl_usd`` and
       ``_hit_rate`` (zeros with no trades).
    9. **Delta benchmark (ADR-0193).** At entry the structure's delta per
       share is ``sum of sign x black76_delta`` over the traded legs (each
       leg's own ``iv``, ``F`` = the entry close, ``DTE / 365``, rate 0);
       the cell's ``equity_pnl_usd`` is what that many shares would have
       earned: ``delta x multiplier x (S_T - S_entry + the ex-date cash on
       the sessions in (entry, settle_date])``. A leg without a positive
       ``iv`` leaves ``delta`` and ``equity_pnl_usd`` ``None`` and is
       counted (a session without a ``dividend_amount`` refuses, as in the
       charge). Metrics per book: ``<book>_delta_equity_total_usd``,
       ``_residual_mean_pnl_usd`` (mean of ``pnl - equity_pnl`` over the
       trades with a benchmark), ``_residual_t`` and ``_pnl_t`` (the t of
       the mean over the trades with a benchmark and over all trades: the
       ``lags=0`` Newey-West t of :func:`~dskit.pipeline.stats.newey_west_mean`,
       ``mean / (sd / sqrt(n))`` with ``sd`` on divisor ``n`` (so it exceeds a
       sample-sd t by ``sqrt(n / (n - 1))``), valid because positions never
       overlap; ``0.0`` with fewer than two trades or no variance, which
       is :data:`~dskit.pipeline.stats.NO_VARIANCE_RTOL`'s rule: a spread of
       at most that fraction of the largest magnitude counts as none)
       and ``_n_no_delta``. Black-76 on the spot at rate 0 ignores
       dividends and early exercise: a disclosed hedge-ratio approximation,
       not a price.

    A split with no forecast row, or an empty ``chain`` across the whole
    snapshot, refuses; a fold where no row can enter reports zero trades
    with every reason counted. Role ``score``, forbidden for serving,
    never decision-eligible: fills are the end-of-day touch at zero latency.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Required: ``split``, ``short_q`` (in (0, 0.5)), ``wing_z`` (> 0),
        ``multiplier`` (int >= 1), ``fee_per_leg`` (>= 0), ``dte_min`` /
        ``dte_max`` (ints, 1 <= min <= max), ``max_abs_log_moneyness``
        (> 0), ``label_horizon`` (int >= 1) and ``carry_rate`` (>= 0).
        Optional: ``min_edge_usd`` (0), ``cvar_alpha`` (0.95), ``gate_fields``
        and ``size_fields`` (see :class:`_CondorBacktestBase`).

    Examples
    --------
    SPY's 30-45 day cell::

        node = CondorQuoteBacktest("backtest", {
            "split": "val", "short_q": 0.1, "wing_z": 0.5, "multiplier": 100,
            "fee_per_leg": 0.65, "dte_min": 30, "dte_max": 45,
            "max_abs_log_moneyness": 0.2, "label_horizon": 22, "carry_rate": 0.055,
        })
        out = node.run(ctx, {"forecasts": rows, "chain": quotes, "underlying": closes})
        out["metrics"]["implied_n_trades"]
    """

    ROW_REASONS = _CondorBacktestBase.ROW_REASONS + ("no_chain", "unsettled")
    BOOK_REASONS = ("target_outside_band", "no_quotable_strike", "degenerate_strikes",
                    "no_atm_iv", "nonpositive_credit", "credit_not_below_width",
                    "below_min_edge")
    DEFAULTS = {"min_edge_usd": 0.0, "cvar_alpha": 0.95}
    #: The structure traded, ``(right, signed quantity)`` per leg in strike order (ADR-0193).
    LEGS = CONDOR_LEGS
    #: The wings of that structure — its distinct rights, derived from :attr:`LEGS`.
    SIDES = _sides_of(LEGS)
    #: The books that trade the forecast's own strikes, in :attr:`BOOKS` order; the rest is implied.
    FORECAST_BOOKS = ("model", "always")
    #: The report's ``kind`` and ``units``: what this node's output calls itself.
    REPORT_KIND = "archived_quote_condor_backtest"
    #: The report's ``pricing``: what every archived-quote member declares (the ledger studies read it).
    REPORT_PRICING = "archived_eod_quotes"
    UNITS = "USD per condor, one contract per leg"
    #: Calendar days a settlement close may precede the settlement date (Sandy, 2012).
    MAX_SETTLEMENT_GAP_DAYS = 4
    _REQUIRED = ("split", "short_q", "wing_z", "multiplier", "fee_per_leg", "dte_min",
                 "dte_max", "max_abs_log_moneyness", "label_horizon", "carry_rate")
    _PARAMS = tuple(sorted(_REQUIRED + tuple(DEFAULTS) + _CondorBacktestBase.SHARED_OPTIONAL))
    _CHAIN_FIELDS = ("instrument", "date", "expiry", "settle_date", "dte", "right", "strike",
                     "bid", "ask", "bid_size", "ask_size", "iv", "underlying_price")

    @classmethod
    def validate_params(cls, params):
        """Check the declaration; every problem is reported.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        missing = [k for k in cls._REQUIRED if k not in params]
        if missing:
            problems.append(f"missing params: {missing}")
        cls._shared_problems(problems, params)
        for knob in ("wing_z", "max_abs_log_moneyness"):
            if not price_ok(params.get(knob)):
                problems.append(f"{knob} must be a positive number, got {params.get(knob)!r}")
        for knob in ("dte_min", "dte_max", "label_horizon"):
            check_int_param(problems, knob, params.get(knob), ge=1)
        lo, hi = params.get("dte_min"), params.get("dte_max")
        if all(isinstance(v, int) and not isinstance(v, bool) for v in (lo, hi)) and lo > hi:
            problems.append(f"dte_min {lo} must not exceed dte_max {hi}")
        rate = params.get("carry_rate")
        if not number_ok(rate) or rate < 0:
            problems.append(f"carry_rate must be a finite number >= 0, got {rate!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep the backtest out of served graphs.

        Parameters
        ----------
        params, verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            Always forbidden.
        """
        return "forbidden"

    def _extra_input_problems(self, inputs):
        """Require the chain and underlying row lists."""
        return [f"{port} must be a list of rows" for port in ("chain", "underlying")
                if not isinstance(inputs.get(port), list)]

    # -- preparation --------------------------------------------------------

    def _prepare(self, inputs):
        """Index the chain by (instrument, date) and the closes by instrument; refuse bad plumbing."""
        chain = inputs["chain"]
        if not chain:
            raise ValueError(f"{self.key}: the chain input is empty across the whole "
                             "snapshot — a misconfigured reader, an unpulled store or a "
                             "wrong symbol")
        lo, hi = self.params["dte_min"], self.params["dte_max"]
        self._chain, dates = {}, []
        for n, row in enumerate(chain, start=1):
            missing = [f for f in self._CHAIN_FIELDS if f not in row]
            if missing:
                raise ValueError(f"{self.key}: chain row {n} lacks {missing}")
            if not lo <= row["dte"] <= hi:
                raise ValueError(f"{self.key}: chain row {n} ({row['instrument']} "
                                 f"{row['date']} {row['expiry']}) has dte {row['dte']} "
                                 f"outside the declared [{lo}, {hi}] — the reader and the "
                                 "backtest must declare one bucket")
            self._chain.setdefault((str(row["instrument"]), row["date"]), []).append(row)
            dates.append(row["date"])
        self._chain_span = {"first_date": min(dates), "last_date": max(dates),
                            "n_rows": len(chain)}
        self._closes = {}
        for n, row in enumerate(inputs["underlying"], start=1):
            if not price_ok(row.get("close")) or not isinstance(row.get("date"), str):
                raise ValueError(f"{self.key}: underlying row {n} needs a date and a "
                                 f"positive close, got {row!r}")
            self._closes.setdefault(str(row.get("instrument")), []).append(row)
        for rows in self._closes.values():
            rows.sort(key=lambda r: r["date"])
            if any(a["date"] == b["date"] for a, b in zip(rows, rows[1:])):
                raise ValueError(f"{self.key}: the underlying series repeats a date")

    # -- the walk's hooks ---------------------------------------------------

    def _cursor_after(self, index, entry):
        """Return this settlement date: the next entry is the first row on or after it."""
        return entry["settle_date"]

    def _before_cursor(self, index, row, cursor):
        """Rows before the open position's settlement date are passed over."""
        return row["date"] < cursor

    def _check_walk(self, ledger, skipped, n_in_split):
        """Refuse only a split with no forecast rows; an empty fold is a recorded result."""
        if n_in_split == 0:
            raise ValueError(f"{self.key}: no forecast row lies in split "
                             f"{self.params['split']!r}")

    # -- one entry ----------------------------------------------------------

    def _settlement(self, instrument, settle_date):
        """Return the ``(date, close)`` the entry settles at, or ``None`` when it cannot."""
        rows = self._closes.get(instrument, [])
        dates = [r["date"] for r in rows]
        at = bisect_right(dates, settle_date) - 1
        if at < 0 or at + 1 >= len(rows):
            return None
        gap = (date.fromisoformat(settle_date) - date.fromisoformat(dates[at])).days
        if gap > self.MAX_SETTLEMENT_GAP_DAYS:
            return None
        return rows[at]["date"], rows[at]["close"]

    @staticmethod
    def _sessions(day, settle_date):
        """Count the weekdays in ``(day, settle_date]`` — no calendar knowledge from the future."""
        start, end = date.fromisoformat(day), date.fromisoformat(settle_date)
        return sum(1 for k in range(1, (end - start).days + 1)
                   if (start + timedelta(days=k)).weekday() < 5)

    @staticmethod
    def _listed(rows):
        """Return ``{right: {strike: row}}`` for one expiry's chain rows."""
        listed = {"put": {}, "call": {}}
        for row in rows:
            listed[row["right"]][float(row["strike"])] = row
        return listed

    @staticmethod
    def _quotable(row, side):
        """Say whether one leg can be traded on ``side`` for one contract."""
        return not quote_problems(row["bid"], row["ask"], row["bid_size"], row["ask_size"],
                                  1, side=side)

    def _atm_iv(self, listed, forward):
        """Mean call/put ``iv`` at the valid strike nearest the forward, or ``None``."""
        candidates = []
        for strike in listed["put"].keys() & listed["call"].keys():
            ivs = [listed[right][strike].get("iv") for right in ("put", "call")]
            if all(price_ok(iv) for iv in ivs) and all(
                not quote_problems(listed[right][strike]["bid"], listed[right][strike]["ask"],
                                   listed[right][strike]["bid_size"],
                                   listed[right][strike]["ask_size"], 0)
                for right in ("put", "call")
            ):
                candidates.append((abs(strike - forward), strike, sum(ivs) / 2))
        if not candidates:
            return None
        return min(candidates)[2]

    def _put_exponents(self, z_put, wing):
        """Return the standardized targets of the long and short put; the long sits ``wing`` further out."""
        return (z_put - wing, z_put)

    def _call_exponents(self, z_call, wing):
        """Return the standardized targets of the short and long call; the long sits ``wing`` further out."""
        return (z_call, z_call + wing)

    def _outside_band(self, scale, exponents):
        """Say whether any standardized target lies beyond the log-moneyness band."""
        band = self.params["max_abs_log_moneyness"]
        return any(abs(scale * z) > band for z in exponents)

    def _snap_put(self, listed, forward, scale, z_put, wing):
        """Snap the put wing outward onto quotable strikes: ``(long, short)`` or ``None``."""
        long_target, short_target = (forward * math.exp(scale * z)
                                     for z in self._put_exponents(z_put, wing))
        puts = listed["put"]
        short = max((k for k, r in puts.items() if k <= short_target
                     and self._quotable(r, "sell")), default=None)
        if short is None:
            return None
        long = max((k for k, r in puts.items() if k <= long_target and k < short
                    and self._quotable(r, "buy")), default=None)
        return None if long is None else (long, short)

    def _snap_call(self, listed, forward, scale, z_call, wing):
        """Snap the call wing outward onto quotable strikes: ``(short, long)`` or ``None``."""
        short_target, long_target = (forward * math.exp(scale * z)
                                     for z in self._call_exponents(z_call, wing))
        calls = listed["call"]
        short = min((k for k, r in calls.items() if k >= short_target
                     and self._quotable(r, "sell")), default=None)
        if short is None:
            return None
        long = min((k for k, r in calls.items() if k >= long_target and k > short
                    and self._quotable(r, "buy")), default=None)
        return None if long is None else (short, long)

    @staticmethod
    def _geometry(strikes):
        """Return ``(strikes, None)``; ``(None, "degenerate_strikes")`` unless they ascend from > 0."""
        if not 0 < strikes[0] or not all(a < b for a, b in zip(strikes, strikes[1:])):
            return None, "degenerate_strikes"
        return strikes, None

    def _snap_legs(self, listed, forward, scale, legs, z_put, z_call, wing):
        """Snap the wings ``legs`` holds: ``(strikes, None)`` or ``(None, reason)``; band, quotability, geometry."""
        wings = {"put": (z_put, self._put_exponents, self._snap_put),
                 "call": (z_call, self._call_exponents, self._snap_call)}
        held = [wings[side] for side in _sides_of(legs)]
        if self._outside_band(scale, [t for z, exponents, _snap in held
                                      for t in exponents(z, wing)]):
            return None, "target_outside_band"
        picked = [snap(listed, forward, scale, z, wing) for z, _exponents, snap in held]
        if any(strikes is None for strikes in picked):
            return None, "no_quotable_strike"
        return self._geometry(tuple(k for strikes in picked for k in strikes))

    def _snap(self, listed, forward, scale, z_put, z_call):
        """Snap this structure's wings at ``wing_z``: ``(strikes, None)`` or ``(None, reason)``."""
        return self._snap_legs(listed, forward, scale, self.LEGS, z_put, z_call,
                               self.params["wing_z"])

    @staticmethod
    def _leg_quotes(facts, legs, strikes):
        """Return ``(rows, quotes)``: each leg's chain row and its ``(bid, ask)``."""
        rows = [facts.listed[right][k] for (right, _sign), k in zip(legs, strikes)]
        return rows, [(r["bid"], r["ask"]) for r in rows]

    @staticmethod
    def _blank_cell(strikes, reason):
        """Return one book's cell before pricing: unentered, with ``reason`` and no numbers."""
        return {"strikes": None if strikes is None else list(strikes), "credit_usd": None,
                "american_charge_usd": None, "pnl_usd": None, "entered": False,
                "reason": reason, "expected_pnl_usd": None, "side_pnl_usd": None,
                "delta": None, "equity_pnl_usd": None}

    def _priced(self, legs, strikes, quotes):
        """Return ``(credit_usd, widths, reason)``: the credit net of fees, and why a book must skip it (or ``None``)."""
        per_share, widths = structure_credit(legs, strikes, quotes)
        credit = per_share * self.params["multiplier"] - len(legs) * self.params["fee_per_leg"]
        if credit <= 0:
            return credit, widths, "nonpositive_credit"
        if per_share >= min(widths):
            return credit, widths, "credit_not_below_width"
        return credit, widths, None

    def _expected(self, credit, legs, strikes, scale, facts):
        """Return ``credit + multiplier x`` the mean payoff over the forecast draws, each priced at ``scale``."""
        samples = facts.dist.samples
        return credit + self.params["multiplier"] * sum(
            structure_payoff(legs, facts.forward * math.exp(scale * z), strikes)
            for z in samples) / len(samples)

    def _book(self, book, strikes, reason, scale, facts, legs=None):
        """Price, gate and settle one book's cell of ``legs`` (this node's :attr:`LEGS` by default)."""
        legs = self.LEGS if legs is None else legs
        cell = self._blank_cell(strikes, reason)
        if strikes is None:
            return cell
        rows, quotes = self._leg_quotes(facts, legs, strikes)
        self._gate(cell, book, legs, strikes, quotes, scale, facts)
        if cell["reason"] is None:
            self._settle(cell, legs, strikes, quotes, rows, facts)
        return cell

    def _gate(self, cell, book, legs, strikes, quotes, scale, facts):
        """Price the credit and set the cell's ``reason`` when its book must skip it."""
        credit, _widths, reason = self._priced(legs, strikes, quotes)
        cell["credit_usd"] = credit
        if reason is not None:
            cell["reason"] = reason
        elif book == "model":
            expected = self._expected(credit, legs, strikes, scale, facts)
            cell["expected_pnl_usd"] = expected
            if not expected > self._knob("min_edge_usd"):
                cell["reason"] = "below_min_edge"

    def _settle(self, cell, legs, strikes, quotes, rows, facts):
        """Enter the cell: its charge, P&L, per-side P&L and delta benchmark."""
        multiplier = self.params["multiplier"]
        total, by_side = self._american_charge(facts.instrument, legs, strikes, facts.day,
                                               facts.settle_date)
        cell["american_charge_usd"] = total
        cell["entered"] = True
        cell["pnl_usd"] = (cell["credit_usd"]
                           + multiplier * structure_payoff(legs, facts.settlement, strikes)
                           - total)
        cell["side_pnl_usd"] = {
            side: self._side_pnl(side, legs, strikes, quotes, facts.settlement,
                                 by_side.get(side, 0.0))
            for side in _sides_of(legs)}
        cell["delta"], cell["equity_pnl_usd"] = self._benchmark(rows, legs, strikes, facts)

    @staticmethod
    def _short_strike(legs, strikes, right):
        """Return the strike of the short ``right`` leg in ``legs``, or ``None`` when it has none."""
        return next((k for (r, sign), k in zip(legs, strikes) if (r, sign) == (right, -1)), None)

    def _american_charge(self, instrument, legs, strikes, day, settle_date):
        """Return the early-exercise charge as ``(total_usd, {side: usd})``: put carry at the short put, call dividends at the short call, each only where ``legs`` holds it."""
        closes, multiplier = self._closes[instrument], self.params["multiplier"]
        charges = {}
        put = self._short_strike(legs, strikes, "put")
        if put is not None:
            charges["put"] = american_put_carry(closes, put, day, settle_date,
                                                self.params["carry_rate"], multiplier)
        call = self._short_strike(legs, strikes, "call")
        if call is not None:
            charges["call"] = american_call_dividend(closes, call, day, settle_date, multiplier)
        return sum(charges.values()), charges

    def _side_pnl(self, side, legs, strikes, quotes, settlement, charge):
        """One wing's P&L: its own credit, fees, settlement payoff and American charge."""
        picks = [i for i, (right, _sign) in enumerate(legs) if right == side]
        wing_legs, wing = tuple(legs[i] for i in picks), [strikes[i] for i in picks]
        per_share, _widths = structure_credit(wing_legs, wing, [quotes[i] for i in picks])
        multiplier, fee = self.params["multiplier"], self.params["fee_per_leg"]
        return (per_share * multiplier - len(wing_legs) * fee
                + multiplier * structure_payoff(wing_legs, settlement, wing) - charge)

    def _benchmark(self, rows, legs, strikes, facts):
        """Return the delta-matched stock ``(delta, equity_pnl_usd)``; ``(None, None)`` lacking an iv."""
        ivs = [row["iv"] for row in rows]
        if not all(price_ok(iv) for iv in ivs):
            return None, None
        years = facts.dte / 365
        delta = sum(sign * black76_delta(right, facts.forward, k, iv, years)
                    for (right, sign), k, iv in zip(legs, strikes, ivs))
        paid = dividends_paid(self._closes[facts.instrument], facts.day, facts.settle_date)
        return delta, delta * self.params["multiplier"] * (
            facts.settlement - facts.forward + paid)

    def _forecast_books(self, facts, scale):
        """Return the model and always cells: the forecast's own strikes at the horizon ``scale``."""
        q = self.params["short_q"]
        strikes, reason = self._snap(facts.listed, facts.forward, scale,
                                     facts.dist.quantile(q), facts.dist.quantile(1 - q))
        return {book: self._book(book, strikes, reason, scale, facts)
                for book in self.FORECAST_BOOKS}

    def _implied_book(self, facts, atm_iv):
        """Return the implied cell: the proxy's formula at the chain's own ATM vol, ``no_atm_iv`` without one."""
        if atm_iv is None:
            return self._book("implied", None, "no_atm_iv", None, facts)
        scale = atm_iv * math.sqrt(facts.dte / 365)
        z = NormalDist().inv_cdf(self.params["short_q"])
        strikes, reason = self._snap(facts.listed, facts.forward, scale,
                                     z - scale / 2, -z - scale / 2)
        return self._book("implied", strikes, reason, scale, facts)

    def _entry(self, row, dist, outcome_z):
        """Price and settle one entry from that date's chain, or say why it cannot enter."""
        instrument, day = str(row.get("instrument")), row["date"]
        quotes = self._chain.get((instrument, day))
        if not quotes:
            return None, "no_chain"
        forward, scale = row[self.FORWARD_FIELD], row[REFERENCE_SCALE_FIELD]
        for q in quotes:
            if not math.isclose(q["underlying_price"], forward, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(f"{self.key}: {instrument} {day}: the chain's "
                                 f"underlying_price {q['underlying_price']!r} is not the "
                                 f"forecast row's close {forward!r} — misaligned inputs")
        dte = min(q["dte"] for q in quotes)
        expiry_rows = [q for q in quotes if q["dte"] == dte]
        expiry, settle_date = expiry_rows[0]["expiry"], expiry_rows[0]["settle_date"]
        settled = self._settlement(instrument, settle_date)
        if settled is None:
            return None, "unsettled"
        settlement_date, settlement = settled
        sessions = self._sessions(day, settle_date)
        horizon_scale = scale * math.sqrt(sessions / self.params["label_horizon"])
        listed = self._listed(expiry_rows)
        atm_iv = self._atm_iv(listed, forward)
        facts = _EntryFacts(instrument, day, settle_date, dte, forward, settlement, listed, dist)
        books = {**self._forecast_books(facts, horizon_scale),
                 "implied": self._implied_book(facts, atm_iv)}
        expected = books["model"].pop("expected_pnl_usd")
        for book in ("always", "implied"):
            books[book].pop("expected_pnl_usd")
        return {"date": day, "asof_ms": row["asof_ms"], "instrument": instrument,
                "forward": forward, "expiry": expiry, "settle_date": settle_date, "dte": dte,
                "sessions": sessions, "horizon_scale": horizon_scale,
                "reference_scale": scale, "settlement": settlement,
                "settlement_date": settlement_date, "atm_iv": atm_iv,
                "model_expected_pnl_usd": expected, "books": books}, None

    def _book_extras(self, traded):
        """Return a book's American charge, each wing and the hedge benchmark over its trades."""
        return {"american_charge_usd": sum(c["american_charge_usd"] for c in traded),
                **self._side_metrics(traded), **self._benchmark_metrics(traded)}

    def _side_metrics(self, traded):
        """Return each side's total, mean and hit rate over the traded cells that hold it; float zeros when none does."""
        metrics = {}
        for side in self.SIDES:
            metrics.update(self._pnl_stats(
                side, [c["side_pnl_usd"][side] for c in traded if side in c["side_pnl_usd"]],
                start=0.0))
        return metrics

    def _benchmark_metrics(self, traded):
        """Return the benchmark's totals and t statistics; trades without one are counted."""
        benchmarked = [c for c in traded if c["equity_pnl_usd"] is not None]
        residuals = [c["pnl_usd"] - c["equity_pnl_usd"] for c in benchmarked]
        return {
            "delta_equity_total_usd": sum((c["equity_pnl_usd"] for c in benchmarked), 0.0),
            "residual_mean_pnl_usd": self._mean(residuals),
            "residual_t": self._t(residuals),
            "pnl_t": self._t([c["pnl_usd"] for c in traded]),
            "n_no_delta": len(traded) - len(benchmarked),
        }

    def _report(self, ledger, metrics):
        """Return the archived-quote report; never decision-eligible."""
        return {"kind": self.REPORT_KIND, "pricing": self.REPORT_PRICING,
                "decision_eligible": False,
                "units": self.UNITS,
                "params": {k: self._knob(k) for k in self._PARAMS if k in self.params
                           or k in self.DEFAULTS},
                "chain": dict(self._chain_span), "metrics": metrics, "ledger": ledger}


class PutSpreadQuoteBacktest(CondorQuoteBacktest):
    """Non-overlapping put credit spreads on one underlying, from archived quotes.

    ADR-0193. The condor backtest's put wing alone: a long put under a short
    put (:data:`~index_options.contracts.PUT_SPREAD_LEGS`), traded wherever
    that wing is quotable. The walk, expiry, settlement, three books, knobs
    and metrics are :class:`CondorQuoteBacktest`'s, unchanged; its
    :attr:`LEGS` are the put wing's and everything else follows from them
    (ADR-0195 made each of these a rule over the trade's legs):

    * **Snap.** Only the put wing is snapped: the band is checked on the two
      put targets, quotability on the two put legs, and the implied book's
      short put is the condor's. The call target is never read, so the
      structure enters where the condor's call side cannot.
    * **Fees and width.** ``len(LEGS) = 2`` legs of ``fee_per_leg``, and
      ``credit_not_below_width`` against the put width.
    * **Charge.** The short put's carry alone: no call dividend, no dividend
      data for the charge. (The delta benchmark still reads
      ``dividend_amount``; a session without one refuses.)
    * **Report.** ``kind`` is ``archived_quote_put_spread_backtest``.

    ``side_pnl_usd`` has the one side ``"put"`` (its P&L is the cell's) and
    the metrics carry ``<book>_put_*`` only. With the condor's ``short_q``
    and ``wing_z`` on the same chain the spread's ``pnl_usd`` equals the
    condor's ``side_pnl_usd["put"]``. Role ``score``, forbidden for serving,
    never decision-eligible: fills are the end-of-day touch at zero latency.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Exactly :class:`CondorQuoteBacktest`'s; ``short_q`` places the short
        put and ``wing_z`` the long put.

    Examples
    --------
    SPY's 30-45 day cell, a short put near 16 delta and a long put near 5::

        node = PutSpreadQuoteBacktest("backtest", {
            "split": "val", "short_q": 0.16, "wing_z": 0.65, "multiplier": 100,
            "fee_per_leg": 0.65, "dte_min": 30, "dte_max": 45,
            "max_abs_log_moneyness": 0.2, "label_horizon": 22, "carry_rate": 0.055,
        })
        out = node.run(ctx, {"forecasts": rows, "chain": quotes, "underlying": closes})
        out["metrics"]["model_put_total_pnl_usd"]
    """

    LEGS = PUT_SPREAD_LEGS
    SIDES = _sides_of(LEGS)
    REPORT_KIND = "archived_quote_put_spread_backtest"
    UNITS = "USD per put spread, one contract per leg"


class _Candidate(NamedTuple):
    """One scorable candidate: what the selector ranks and what the winner's books enter."""

    structure: str
    short_q: float
    wing_z: float
    legs: tuple
    strikes: tuple
    score: float


class PayoffSelectQuoteBacktest(CondorQuoteBacktest):
    """Non-overlapping trades of the structure the forecast scores best, from archived quotes.

    ADR-0195. :class:`CondorQuoteBacktest` where the forecast also chooses
    WHICH defined-risk structure to trade and how far out, not only whether
    to trade a fixed condor. The walk, expiry, settlement, gates, American
    charge, wing split, delta benchmark and ADR-0194 ``gate_fields`` are the
    condor's; only the model and always books change.

    At an entry date every declared candidate ``(structure, short_q,
    wing_z)`` is, in declaration order (``candidate_structures`` outermost,
    then ``candidate_short_q``, then ``candidate_wing_z``):

    1. **Snapped** at the model's horizon scale ``s'`` (the condor's rule):
       only that structure's wings, at ``Q(short_q)`` / ``Q(1 - short_q)``
       and its own ``wing_z``, outward onto quotable strikes. A band,
       quotability or geometry refusal (:meth:`_snap_legs`) drops it.
    2. **Priced** at bid/ask with ``len(legs) x fee_per_leg``. A credit that
       is not positive (``nonpositive_credit``) or that reaches the narrowest
       vertical (``credit_not_below_width``) drops it: the condor's own gate.
    3. **Scored**: ``score = E_P[pnl] / max_loss``. ``E_P[pnl]`` is the credit
       plus ``multiplier`` times the mean, over the forecast draws ``z``, of the
       structure's payoff at ``forward x exp(s' z)``; ``max_loss`` is
       ``multiplier x`` the widest vertical less the credit, positive because
       the gate keeps the credit under the narrowest one.

    The highest score wins and an exact tie goes to the earlier declared
    candidate. The **model** book enters the winner when its ``E_P[pnl]``
    exceeds ``min_edge_usd`` (else ``below_min_edge``); the **always** book
    enters it whatever its edge; **implied** stays the VIX-implied condor at
    ``short_q`` / ``wing_z`` (which therefore stay required), the unchanged
    benchmark. With no scorable candidate both forecast books skip the entry
    as ``no_scorable_candidate``. Every model and always cell records
    ``selected`` (``structure``, ``short_q``, ``wing_z``, ``score`` and
    ``n_candidates_scored``, the count of scorable candidates); the implied
    cell, and a cell with no winner, records ``None``.

    Metrics add ``<book>_n_<structure>`` for each of
    :data:`~index_options.contracts.STRUCTURES` (traded cells by structure; the
    implied book counts as the condor). The side metrics
    ``<book>_<side>_total_pnl_usd`` / ``_mean_pnl_usd`` / ``_hit_rate`` cover
    the traded cells that CONTAIN that side (a call spread has no put side), which
    for a fixed condor or put spread is every traded cell. The empirical rung's
    forecast (the unconditional standardized shape scaled by TRAILING realized
    vol) makes the same selector an unconditional-shape, trailing-vol control:
    the har-vix minus empirical difference measures the conditional-forecast
    increment over that baseline, never either run's level.
    Role ``score``, forbidden for serving, never decision-eligible: fills are
    the end-of-day touch at zero latency.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        :class:`CondorQuoteBacktest`'s, plus required ``candidate_structures``
        (non-empty distinct names from
        :data:`~index_options.contracts.STRUCTURES`; their order breaks ties),
        ``candidate_short_q`` (non-empty distinct numbers in (0, 0.5)) and
        ``candidate_wing_z`` (non-empty distinct numbers > 0).

    Examples
    --------
    SPY's 30-45 day cell choosing among the three structures::

        node = PayoffSelectQuoteBacktest("backtest", {
            "split": "val", "short_q": 0.1, "wing_z": 0.5, "multiplier": 100,
            "fee_per_leg": 0.65, "dte_min": 30, "dte_max": 45,
            "max_abs_log_moneyness": 0.2, "label_horizon": 22, "carry_rate": 0.055,
            "candidate_structures": ["put_spread", "call_spread", "condor"],
            "candidate_short_q": [0.10, 0.16, 0.25], "candidate_wing_z": [0.35, 0.65, 1.0],
        })
        out = node.run(ctx, {"forecasts": rows, "chain": quotes, "underlying": closes})
        out["metrics"]["model_n_call_spread"]
    """

    BOOK_REASONS = CondorQuoteBacktest.BOOK_REASONS + ("no_scorable_candidate",)
    REPORT_KIND = "archived_quote_payoff_select_backtest"
    UNITS = "USD per selected structure, one contract per leg"
    #: The structure the implied book always trades: the one the inherited :attr:`LEGS` is.
    IMPLIED_STRUCTURE = next(name for name, legs in STRUCTURES.items()
                             if legs == CondorQuoteBacktest.LEGS)
    _CANDIDATE_KNOBS = ("candidate_structures", "candidate_short_q", "candidate_wing_z")
    _REQUIRED = CondorQuoteBacktest._REQUIRED + _CANDIDATE_KNOBS
    _PARAMS = tuple(sorted(_REQUIRED + tuple(CondorQuoteBacktest.DEFAULTS)
                           + _CondorBacktestBase.SHARED_OPTIONAL))

    @classmethod
    def validate_params(cls, params):
        """Check the declaration; every problem is reported.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = super().validate_params(params)
        cls._distinct_list_problems(problems, params, "candidate_structures",
                                    lambda name: isinstance(name, str) and name in STRUCTURES,
                                    f"names from {list(STRUCTURES)}")
        cls._distinct_list_problems(problems, params, "candidate_short_q", cls._short_q_ok,
                                    "numbers in (0, 0.5)")
        cls._distinct_list_problems(problems, params, "candidate_wing_z", price_ok,
                                    "positive numbers")
        return problems

    def _candidates(self):
        """Yield every declared ``(structure, short_q, wing_z)`` in tie-break order."""
        return product(self.params["candidate_structures"], self.params["candidate_short_q"],
                       self.params["candidate_wing_z"])

    def _score(self, legs, strikes, scale, facts):
        """Return the score of one snapped structure, or ``None`` where the credit gate refuses it."""
        _rows, quotes = self._leg_quotes(facts, legs, strikes)
        credit, widths, reason = self._priced(legs, strikes, quotes)
        if reason is not None:
            return None
        max_loss = self.params["multiplier"] * max(widths) - credit
        return self._expected(credit, legs, strikes, scale, facts) / max_loss

    def _candidate(self, structure, short_q, wing_z, facts, scale):
        """Snap and score one candidate: a :class:`_Candidate`, or ``None`` where a snap or the credit gate refuses it."""
        legs = STRUCTURES[structure]
        strikes, reason = self._snap_legs(facts.listed, facts.forward, scale, legs,
                                          facts.dist.quantile(short_q),
                                          facts.dist.quantile(1 - short_q), wing_z)
        if reason is not None:
            return None
        score = self._score(legs, strikes, scale, facts)
        return None if score is None else _Candidate(structure, short_q, wing_z, legs, strikes,
                                                     score)

    def _select(self, facts, scale):
        """Return ``(winner, n_scored)``: the best-scoring candidate (the earlier declared on a tie) and how many were scorable."""
        winner, n_scored = None, 0
        for structure, short_q, wing_z in self._candidates():
            candidate = self._candidate(structure, short_q, wing_z, facts, scale)
            if candidate is None:
                continue
            n_scored += 1
            if winner is None or candidate.score > winner.score:
                winner = candidate
        return winner, n_scored

    @staticmethod
    def _blank_cell(strikes, reason):
        """Return the condor's blank cell plus ``selected``, ``None`` until a winner is recorded."""
        return {**CondorQuoteBacktest._blank_cell(strikes, reason), "selected": None}

    def _forecast_books(self, facts, scale):
        """Return the model and always cells: the winning candidate at the horizon ``scale``, or ``no_scorable_candidate``."""
        winner, n_scored = self._select(facts, scale)
        books = {}
        for book in self.FORECAST_BOOKS:
            if winner is None:
                books[book] = self._book(book, None, "no_scorable_candidate", scale, facts)
                continue
            cell = self._book(book, winner.strikes, None, scale, facts, winner.legs)
            cell["selected"] = {"structure": winner.structure, "short_q": winner.short_q,
                                "wing_z": winner.wing_z, "score": winner.score,
                                "n_candidates_scored": n_scored}
            books[book] = cell
        return books

    def _book_extras(self, traded):
        """Return the condor's extras plus ``n_<structure>``: the traded cells by structure, implied counting as the condor."""
        counts = Counter(self.IMPLIED_STRUCTURE if cell["selected"] is None
                         else cell["selected"]["structure"] for cell in traded)
        return {**super()._book_extras(traded),
                **{f"n_{name}": counts[name] for name in STRUCTURES}}


class _LaggedRowSignals(Node, ABC):
    """Annotate forecast rows from each row's PREVIOUS reading of the same instrument.

    The one owner of what :class:`VolRegimeSignals` (ADR-0194) and
    :class:`VolSizingWeights` (ADR-0196) share: for each instrument in
    ``asof_ms`` order, the row ``lag_sessions`` earlier (one by default) is
    what a row may read, never its own day, and every input row comes back
    unchanged, in input order, with the member's fields added. The lag is the
    point: the 16:15 ET VIX close of day ``t`` cannot inform a 16:00 ET entry
    on day ``t``.

    The mechanism is ordering (:meth:`_by_instrument`), the lag
    (:meth:`_lagged`), the refusals, the shared knobs (:data:`SHARED_DEFAULTS`)
    and the output assembly (:meth:`run`). A member supplies two hooks:
    :meth:`_read` turns one lagged row into a reading, and :meth:`_annotate`
    turns an instrument's whole series of readings into one dict of added fields
    per position (a series-level rule, such as an expanding percentile or
    quantile, sees only the EARLIER readings, so nothing looks ahead). Role
    ``transform``; forbidden for serving.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        The member's knobs, default-deny (``_PARAMS``).

    Examples
    --------
    The smallest member stamps each row with the previous row's ``x``::

        class Echo(_LaggedRowSignals):
            def _read(self, previous, context):
                return None if previous is None else previous.get("x")

            def _annotate(self, readings):
                return [{"prev_x": reading} for reading in readings]

        out = Echo("echo", {}).run(ctx, {"rows": rows})["rows"]
    """

    role = "transform"
    outputs = ("rows",)
    #: The knobs every member takes, with their defaults, stated once: the previous row's
    #: implied-vol and realized-vol fields, how many rows back it is, and how many earlier
    #: readings a series-level rule needs.
    SHARED_DEFAULTS = {"implied_field": "iv_index", "realized_field": "rv_22",
                       "lag_sessions": 1, "min_history": 252}
    #: A member's knobs and defaults: :data:`SHARED_DEFAULTS` plus its own.
    DEFAULTS = SHARED_DEFAULTS
    _PARAMS = tuple(sorted(DEFAULTS))
    #: The input ports, name -> what it carries; every one must be a list.
    PORTS = {"rows": "forecast rows"}

    @classmethod
    def validate_params(cls, params):
        """Check the shared knobs and refuse any name the class does not declare.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._shared_problems(problems, params)
        return problems

    @classmethod
    def _shared_problems(cls, problems, params):
        """Check the two field names and the two history knobs every member declares."""
        for knob in ("implied_field", "realized_field"):
            value = params.get(knob, cls.DEFAULTS[knob])
            if not isinstance(value, str) or not value:
                problems.append(f"{knob} must be a non-empty string, got {value!r}")
        for knob in ("min_history", "lag_sessions"):
            check_int_param(problems, knob, params.get(knob, cls.DEFAULTS[knob]), ge=1)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep the study node out of served graphs.

        Parameters
        ----------
        params, verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            Always forbidden.
        """
        return "forbidden"

    def validate_inputs(self, inputs):
        """Require every declared input port to be a list.

        Parameters
        ----------
        inputs : dict
            The wired inputs.

        Returns
        -------
        list of str
            Empty when usable.
        """
        return [f"{port} must be a list of {what}" for port, what in self.PORTS.items()
                if not isinstance(inputs.get(port), list)]

    def _knob(self, name):
        """Return a declared knob or its default."""
        return self.params.get(name, self.DEFAULTS[name])

    def _by_instrument(self, rows):
        """Group ``(input index, row)`` by instrument in ``asof_ms`` order; a repeated stamp refuses."""
        grouped = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not number_ok(row.get("asof_ms")):
                raise ValueError(f"{self.key}: row {index + 1} needs a numeric asof_ms, "
                                 f"got {row!r}")
            grouped.setdefault(str(row.get("instrument")), []).append((index, row))
        for instrument, ordered in grouped.items():
            ordered.sort(key=lambda pair: pair[1]["asof_ms"])
            for (_, earlier), (_, later) in zip(ordered, ordered[1:]):
                if earlier["asof_ms"] == later["asof_ms"]:
                    raise ValueError(f"{self.key}: {instrument} repeats asof_ms "
                                     f"{later['asof_ms']}")
        return grouped

    def _lagged(self, ordered):
        """Return, per position, the row ``lag_sessions`` earlier of the same instrument, or ``None``."""
        lag = int(self._knob("lag_sessions"))
        return [ordered[k - lag][1] if k >= lag else None for k in range(len(ordered))]

    def _previous_fields(self, previous):
        """Return the lagged row's raw ``(implied, realized)`` fields; ``(None, None)`` with no such row."""
        if previous is None:
            return None, None
        return (previous.get(self._knob("implied_field")),
                previous.get(self._knob("realized_field")))

    def _context(self, inputs):
        """Return what :meth:`_read` needs beyond the lagged row, built once from the inputs; nothing by default."""
        return None

    @abstractmethod
    def _read(self, previous, context):
        """Return one row's reading from its lagged row ``previous`` (``None`` where it has none)."""

    @abstractmethod
    def _annotate(self, readings):
        """Return one dict of added fields per position from an instrument's series of readings, oldest first."""

    def run(self, ctx, inputs):
        """Add the member's fields to every row, each from that row's lagged reading.

        Parameters
        ----------
        ctx : NodeContext
            Unused: the node reads only its inputs and params.
        inputs : dict
            ``rows`` (forecast rows carrying ``instrument`` and ``asof_ms``) and
            the member's other ports.

        Returns
        -------
        dict
            ``rows``: every input row, unchanged and in input order, plus the
            member's fields.

        Raises
        ------
        ValueError
            When a row lacks a numeric ``asof_ms``, an instrument repeats an
            ``asof_ms``, or the member refuses its other inputs.
        """
        context = self._context(inputs)
        rows = inputs["rows"]
        added = [None] * len(rows)
        for ordered in self._by_instrument(rows).values():
            readings = [self._read(previous, context) for previous in self._lagged(ordered)]
            for (index, _row), extra in zip(ordered, self._annotate(readings)):
                added[index] = extra
        return {"rows": [{**row, **extra} for row, extra in zip(rows, added)]}


class VolRegimeSignals(_LaggedRowSignals):
    """Annotate forecast rows with the VIX curve's slope and the variance premium's sign.

    ADR-0194. For each instrument in ``asof_ms`` order, row ``t`` reads the
    row ``lag_sessions`` earlier of the SAME instrument (one session by
    default): its ``implied_field`` (the VIX close, in vol points), its
    ``realized_field`` (a per-session vol, :class:`RealizedVolFeatures`'
    convention, never annualized) and the second vol index's close on THAT
    row's ``date``. The lag is the point: the 16:15 ET VIX close of day ``t``
    cannot inform a 16:00 ET entry on day ``t``, so nothing here reads the
    row's own day. Every input row comes back unchanged (in input order)
    with these fields added:

    * ``vix_term_ratio`` — implied / term close, above 1 when the curve is
      inverted;
    * ``vix_term_ratio_pct`` — :func:`~dskit.pipeline.stats.expanding_percentile`
      of that ratio over the instrument's own ratio series (mid-rank among
      STRICTLY EARLIER ratios; ``None`` before ``min_history`` of them);
    * ``vrp`` — ``(implied / 100)^2 - periods_per_year * realized^2``, the
      variance premium in annualized variance units;
    * the booleans :attr:`GATE_FIELDS`: ``gate_term_inverted`` (ratio >=
      ``inverted_at``), ``gate_term_high_pct`` (percentile >=
      ``high_ratio_pct``), ``gate_vrp_nonpositive`` (``vrp <= 0``) and
      ``gate_any`` (``True`` if any of the three is ``True``; ``None`` if
      none is and any is ``None``; else ``False``).

    An input that is missing or not positive makes that output ``None``,
    never ``False``: an unknown is not a green light. The ordering, lag and
    output assembly are :class:`_LaggedRowSignals`'. Role ``transform``;
    forbidden for serving.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        All optional, default-deny: ``implied_field`` (``"iv_index"``),
        ``realized_field`` (``"rv_22"``), ``periods_per_year`` (positive,
        252), ``inverted_at`` (positive, 1.0), ``high_ratio_pct`` (in
        (0, 1], 0.8), ``min_history`` (int >= 1, 252), ``lag_sessions``
        (int >= 1, 1).

    Examples
    --------
    Rows from a forecast model plus the VIX3M closes::

        node = VolRegimeSignals("signals", {})
        out = node.run(ctx, {"rows": forecasts, "term": vix3m_closes})
        out["rows"][0]["gate_any"]
    """

    #: The four gate fields added to every row, in this order; the backtest's
    #: ``gate_fields`` and the grid's gate documents read this tuple.
    GATE_FIELDS = ("gate_term_inverted", "gate_term_high_pct", "gate_vrp_nonpositive",
                   "gate_any")
    DEFAULTS = {**_LaggedRowSignals.SHARED_DEFAULTS, "periods_per_year": 252,
                "inverted_at": 1.0, "high_ratio_pct": 0.8}
    _PARAMS = tuple(sorted(DEFAULTS))
    PORTS = {"rows": "forecast rows", "term": "index-close records"}

    @classmethod
    def validate_params(cls, params):
        """Check the declaration; every problem is reported.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = super().validate_params(params)
        for knob in ("periods_per_year", "inverted_at"):
            value = params.get(knob, cls.DEFAULTS[knob])
            if not price_ok(value):
                problems.append(f"{knob} must be a positive number, got {value!r}")
        pct = params.get("high_ratio_pct", cls.DEFAULTS["high_ratio_pct"])
        if not number_ok(pct) or not 0 < pct <= 1:
            problems.append(f"high_ratio_pct must be in (0, 1], got {pct!r}")
        return problems

    def _context(self, inputs):
        """Index the term-index records by date; a record without one, or a repeated date, refuses."""
        closes = {}
        for n, record in enumerate(inputs["term"], start=1):
            day = record.get("date") if isinstance(record, dict) else None
            if not isinstance(day, str) or not day:
                raise ValueError(f"{self.key}: term row {n} needs a date, got {record!r}")
            if day in closes:
                raise ValueError(f"{self.key}: the term series repeats a date ({day})")
            closes[day] = record.get("close")
        return closes

    def _read(self, previous, context):
        """Return ``(ratio, vrp)`` from the previous row and its date's term close; ``None`` where an input is unusable."""
        if previous is None:
            return None, None
        implied, realized = self._previous_fields(previous)
        day = previous.get("date")
        closes = context.get(day) if isinstance(day, str) else None
        ratio = implied / closes if price_ok(implied) and price_ok(closes) else None
        vrp = ((implied / 100) ** 2 - self._knob("periods_per_year") * realized ** 2
               if price_ok(implied) and price_ok(realized) else None)
        return ratio, vrp

    @staticmethod
    def _any(verdicts):
        """Combine gate verdicts: ``True`` if any is, else ``None`` if any is unknown, else ``False``."""
        if any(v is True for v in verdicts):
            return True
        return None if any(v is None for v in verdicts) else False

    def _signals(self, ratio, pct, vrp):
        """Return one row's added fields: the three measures and the four gates."""
        verdicts = (None if ratio is None else bool(ratio >= self._knob("inverted_at")),
                    None if pct is None else bool(pct >= self._knob("high_ratio_pct")),
                    None if vrp is None else bool(vrp <= 0))
        return {"vix_term_ratio": ratio, "vix_term_ratio_pct": pct, "vrp": vrp,
                **dict(zip(self.GATE_FIELDS, (*verdicts, self._any(verdicts))))}

    def _annotate(self, readings):
        """Return each position's ratio, its expanding percentile, the premium and the four gates."""
        percentiles = expanding_percentile(
            [ratio for ratio, _vrp in readings], int(self._knob("min_history")))
        return [self._signals(ratio, pct, vrp)
                for (ratio, vrp), pct in zip(readings, percentiles)]


class VolSizingWeights(_LaggedRowSignals):
    """Annotate forecast rows with volatility-scaled size weights, each read from the previous session.

    ADR-0196. For each instrument in ``asof_ms`` order, row ``t`` reads the
    row ``lag_sessions`` earlier of the SAME instrument (one session by
    default): its ``implied_field`` (the VIX close) and ``realized_field`` (a
    per-session vol). Each weight compares that lagged reading with the
    EXPANDING MEDIAN of the readings before it (the instrument's own earlier
    lagged values, via :func:`~dskit.pipeline.stats.expanding_quantile`),
    then is clipped to ``[min_weight, max_weight]``. Every input row comes
    back unchanged (in input order) with :attr:`SIZE_FIELDS` added:

    * ``size_inv_implied_var`` — median(implied^2) / implied^2: sell LESS after
      high implied variance (the volatility-managed direction);
    * ``size_inv_realized_var`` — median(realized^2) / realized^2: the same on
      realized variance;
    * ``size_implied`` — implied / median(implied): sell MORE after high VIX
      (the opposite direction; the literature conflicts, so both are measured).

    A weight is ``None`` before ``min_history`` earlier readings exist, or where
    the lagged input is missing or not positive: an unknown is not a weight
    of one. A weight is a plain positive ``float``. The ordering, lag and output
    assembly are :class:`_LaggedRowSignals`'. Role ``transform``; forbidden for
    serving.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        All optional, default-deny: ``implied_field`` (``"iv_index"``),
        ``realized_field`` (``"rv_22"``), ``lag_sessions`` (int >= 1, 1),
        ``min_history`` (int >= 1, 252), ``min_weight`` (positive, 0.25) and
        ``max_weight`` (positive, at least ``min_weight``, 4.0).

    Examples
    --------
    Rows from a forecast model, weighed by the previous session's vol::

        node = VolSizingWeights("sizing", {})
        out = node.run(ctx, {"rows": forecasts})
        out["rows"][-1]["size_implied"]
    """

    #: The three weight fields added to every row, in this order; the backtest's
    #: ``size_fields`` and the grid's sizing documents read this tuple.
    SIZE_FIELDS = ("size_inv_implied_var", "size_inv_realized_var", "size_implied")
    DEFAULTS = {**_LaggedRowSignals.SHARED_DEFAULTS, "min_weight": 0.25, "max_weight": 4.0}
    _PARAMS = tuple(sorted(DEFAULTS))

    @classmethod
    def validate_params(cls, params):
        """Check the declaration; every problem is reported.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = super().validate_params(params)
        low, high = (params.get(knob, cls.DEFAULTS[knob]) for knob in ("min_weight", "max_weight"))
        for knob, value in (("min_weight", low), ("max_weight", high)):
            if not price_ok(value):
                problems.append(f"{knob} must be a positive number, got {value!r}")
        if price_ok(low) and price_ok(high) and low > high:
            problems.append(f"min_weight {low!r} must not exceed max_weight {high!r}")
        return problems

    def _read(self, previous, context):
        """Return the lagged ``(implied, realized)`` readings, each ``None`` unless a positive number."""
        implied, realized = self._previous_fields(previous)
        return (float(implied) if price_ok(implied) else None,
                float(realized) if price_ok(realized) else None)

    @staticmethod
    def _squares(values):
        """Return ``values`` squared, a missing one staying ``None``."""
        return [None if v is None else v * v for v in values]

    def _median(self, series):
        """Return each position's median of the STRICTLY EARLIER finite values, ``None`` before ``min_history`` of them."""
        return expanding_quantile(series, 0.5, int(self._knob("min_history")))

    @staticmethod
    def _ratios(numerators, denominators):
        """Return the elementwise ratio, ``None`` wherever either side is ``None``."""
        return [None if n is None or d is None else n / d
                for n, d in zip(numerators, denominators)]

    def _weight(self, ratio):
        """Clip one ratio to ``[min_weight, max_weight]``; ``None`` (no history, no reading) stays ``None``."""
        if ratio is None:
            return None
        return min(self._knob("max_weight"), max(self._knob("min_weight"), ratio))

    def _annotate(self, readings):
        """Return each position's three weights against the expanding median of the earlier readings."""
        implied = [reading[0] for reading in readings]
        realized = [reading[1] for reading in readings]
        implied_var, realized_var = self._squares(implied), self._squares(realized)
        ratios = (self._ratios(self._median(implied_var), implied_var),
                  self._ratios(self._median(realized_var), realized_var),
                  self._ratios(implied, self._median(implied)))
        return [dict(zip(self.SIZE_FIELDS, map(self._weight, position)))
                for position in zip(*ratios)]

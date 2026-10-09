"""Thin, non-serving Nodes binding condor diagnostics and the condor, put-spread and debit-structure backtests."""

import math
import json
from abc import ABC, abstractmethod
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, timedelta
from itertools import product
from fractions import Fraction
from statistics import NormalDist
from typing import NamedTuple

from dskit.production.accounting import WindowBook

from dskit.pipeline.distribution_models import REFERENCE_SCALE_FIELD
from dskit.pipeline.distribution_scores import (
    DEFAULT_OUTCOME_FIELD,
    DEFAULT_SAMPLES_FIELD,
    forecast_pair,
    row_in_split,
)
from dskit.pipeline.node import JsonArtifact, Node, check_int_param, reject_unknown_params
from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.pyomo import BINDING_TOLERANCE, DEFAULT_SOLVER, PyomoSolve, WassersteinDual
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
    LONG_CALL_SPREAD_LEGS,
    LONG_PUT_SPREAD_LEGS,
    LONG_STRADDLE_LEGS,
    PUT_SPREAD_LEGS,
    STRUCTURES,
    CashIndexContract,
    DefinedRiskCondor,
    _IndexQuote,
    _IndexSettlement,
    _day,
    _decimal,
    _payoff_total,
    _instant,
    _integer,
    _text,
    american_call_dividend,
    american_put_carry,
    dividends_paid,
    leg_intrinsic,
    quote_problems,
    structure_credit,
    structure_max_loss,
    structure_payoff,
)
from .datafiles import entry_problems
from .distribution import CondorGeometry, condor_payoff

__all__ = ["ExactDteBarChain", "CondorExpirySettle", "CondorBacktest", "CondorDistributionReport", "CondorPayoffDiagnostic",
           "CondorQuoteBacktest", "DebitStructureQuoteBacktest", "ExactExpiryPanelRead",
           "LongCallSpreadQuoteBacktest",
           "LongPutSpreadQuoteBacktest", "LongStraddleQuoteBacktest", "PayoffSelectQuoteBacktest",
           "RobustCondorBatchSelect", "RobustCondorSelect", "PutSpreadQuoteBacktest", "VolRegimeSignals", "VolSizingWeights", "liquid_leg_haircut", "mean_or_zero",
           "t_or_zero"]


def mean_or_zero(values):
    """Return the mean of ``values``, ``0.0`` when there are none.

    The one owner (ADR-0197, from ADR-0196 review B-F1) of the child's "0.0 when
    undefined" mean: the backtests' metrics and the ledger studies' tables both
    print it, so a statistic cannot read differently in the two.

    Parameters
    ----------
    values : sequence of float
        Numbers (booleans count as 0 and 1, which makes a hit rate a mean).

    Returns
    -------
    float
        ``sum(values) / len(values)``, ``0.0`` for an empty sequence.

    Examples
    --------
    A hit rate over four trades::

        mean_or_zero([True, False, True, True])
        # -> 0.75
    """
    return sum(values) / len(values) if values else 0.0


def t_or_zero(values):
    """Return the ``lags=0`` Newey-West t of the mean, ``0.0`` when it is undefined.

    The one owner (ADR-0197) of the child's "0.0 when undefined" t: fewer than two
    values, or a series the stats owner finds no variance in
    (:data:`~dskit.pipeline.stats.NO_VARIANCE_RTOL`'s rule, never restated here).
    Valid because the positions it summarizes never overlap.

    Parameters
    ----------
    values : sequence of float
        Finite numbers.

    Returns
    -------
    float
        ``mean / (sd / sqrt(n))`` with ``sd`` on divisor ``n`` (the stats
        owner's), ``0.0`` when undefined.

    Examples
    --------
    A constant series has no variance and so no t::

        t_or_zero([1.7, 1.7, 1.7])
        # -> 0.0
    """
    if len(values) < 2:
        return 0.0
    t = newey_west_mean(list(values), lags=0)["t"]
    return 0.0 if t is None else t


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
    def _pnl_stats(prefix, pnls, start=0):
        """Return ``<prefix>_total_pnl_usd`` / ``_mean_pnl_usd`` / ``_hit_rate`` over ``pnls`` (a hit is ``p > 0``); ``start`` is the total's empty value."""
        return {f"{prefix}_total_pnl_usd": sum(pnls, start),
                f"{prefix}_mean_pnl_usd": mean_or_zero(pnls),
                f"{prefix}_hit_rate": mean_or_zero([p > 0 for p in pnls])}

    @staticmethod
    def _short_q_ok(value):
        """Say whether ``value`` is a usable short-leg quantile: a number in (0, 0.5)."""
        return number_ok(value) and 0 < value < 0.5

    @classmethod
    def _quantile_problems(cls, problems, params):
        """Check the quantile knob a member places its strikes with: ``short_q`` here; a member with another overrides it."""
        q = params.get("short_q")
        if not cls._short_q_ok(q):
            problems.append(f"short_q must be in (0, 0.5), got {q!r}")

    @classmethod
    def _shared_problems(cls, problems, params):
        """Check the knobs every member declares: split, its quantile, multiplier, fees, gate, tail."""
        if params.get("split") not in SPLIT_NAMES:
            problems.append(f"split must name one of {list(SPLIT_NAMES)}")
        cls._quantile_problems(problems, params)
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
                                f"{book}_{field}_{label}_mean_pnl_usd": mean_or_zero(pnls),
                                f"{book}_{field}_{label}_t": t_or_zero(pnls)})
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
                f"{book}_{field}_mean_weight": mean_or_zero(weights),
                f"{book}_{field}_total_pnl_usd": total,
                f"{book}_{field}_pnl_per_weight": total / sum(weights) if weights else 0.0,
                f"{book}_{field}_t": t_or_zero(sized),
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
                f"{book}_mean_credit_usd": mean_or_zero([c["credit_usd"] for c in traded]),
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


def _iso_day(value):
    """Say whether ``value`` is a ``YYYY-MM-DD`` string that ``contracts._day`` accepts unchanged."""
    try:
        _day(value, "date")
    except ValueError:
        return False
    return True


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
            if knob in cls._REQUIRED and not price_ok(params.get(knob)):
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
        self._reset_entry_caches()
        need = frozenset(self._CHAIN_FIELDS)
        for n, row in enumerate(chain, start=1):
            # a dict holding every field is the common case; anything else builds the message
            missing = (None if type(row) is dict and row.keys() >= need
                       else [f for f in self._CHAIN_FIELDS if f not in row])
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
        self._close_dates = {inst: [r["date"] for r in rows] for inst, rows in self._closes.items()}
        self._closes_iso = {inst: all(_iso_day(d) for d in dates)
                            for inst, dates in self._close_dates.items()}

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
        dates = self._close_dates.get(instrument, [])
        at = _bounded_settlement_index(dates, settle_date, self.MAX_SETTLEMENT_GAP_DAYS)
        return None if at is None else (rows[at]["date"], rows[at]["close"])

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

    def _reset_entry_caches(self):
        """Forget the per-entry quotable-strike and expected-payoff memos."""
        self._quotable_cache, self._expected_cache = {}, {}

    def _quotable_strikes(self, rows, side):
        """Return the ascending non-NaN strikes of ``rows`` quotable on ``side``, or ``None`` when a row cannot be judged up front."""
        # Memoized per entry (every snap, candidate and book reads the same expiry), keyed by
        # the dict itself so a different ``listed`` never hits. A NaN strike is never picked by
        # the scans this replaces (every comparison with it is False), so it is left out.
        if "_quotable_cache" not in self.__dict__:       # a hook called outside a walk
            self._reset_entry_caches()
        key = (id(rows), side)
        hit = self._quotable_cache.get(key)
        if hit is not None and hit[0] is rows:
            return hit[1]
        try:
            strikes = sorted(k for k, row in rows.items() if k == k and self._quotable(row, side))
        except Exception:      # noqa: BLE001 -- any refusal is the scan's to make, or not
            # judging a row the scan would never reach (an int size past float range) could
            # raise where the scan does not: the caller falls back to the scan instead
            strikes = None
        self._quotable_cache[key] = (rows, strikes)
        return strikes

    def _snap_put(self, listed, forward, scale, z_put, wing):
        """Snap the put wing outward onto quotable strikes: ``(long, short)`` or ``None``."""
        long_target, short_target = (forward * math.exp(scale * z)
                                     for z in self._put_exponents(z_put, wing))
        puts = listed["put"]
        sells, buys = self._quotable_strikes(puts, "sell"), self._quotable_strikes(puts, "buy")
        if sells is None or buys is None or long_target != long_target \
                or short_target != short_target:     # a NaN target: no bisect order to use
            return self._scan_put(puts, long_target, short_target)
        # the largest quotable strike <= the target, as the scan's max(); the long also < short
        at = bisect_right(sells, short_target)
        if at == 0:
            return None
        short = sells[at - 1]
        at = min(bisect_right(buys, long_target), bisect_left(buys, short))
        return None if at == 0 else (buys[at - 1], short)

    def _scan_put(self, puts, long_target, short_target):
        """Snap the put wing by a full scan: the reference :meth:`_snap_put` bisects."""
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
        sells, buys = self._quotable_strikes(calls, "sell"), self._quotable_strikes(calls, "buy")
        if sells is None or buys is None or long_target != long_target \
                or short_target != short_target:     # a NaN target: no bisect order to use
            return self._scan_call(calls, short_target, long_target)
        # the smallest quotable strike >= the target, as the scan's min(); the long also > short
        at = bisect_left(sells, short_target)
        if at == len(sells):
            return None
        short = sells[at]
        at = max(bisect_left(buys, long_target), bisect_right(buys, short))
        return None if at == len(buys) else (short, buys[at])

    def _scan_call(self, calls, short_target, long_target):
        """Snap the call wing by a full scan: the reference :meth:`_snap_call` bisects."""
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

    def _net_credit(self, legs, strikes, quotes):
        """Return ``(credit_usd, per_share, widths)``: the cash at entry net of fees (negative for a debit), the per-share credit and the vertical widths."""
        per_share, widths = structure_credit(legs, strikes, quotes)
        credit = per_share * self.params["multiplier"] - len(legs) * self.params["fee_per_leg"]
        return credit, per_share, widths

    def _priced(self, legs, strikes, quotes):
        """Return ``(credit_usd, widths, reason)``: the credit net of fees, and why a book must skip it (or ``None``)."""
        credit, per_share, widths = self._net_credit(legs, strikes, quotes)
        if credit <= 0:
            return credit, widths, "nonpositive_credit"
        if per_share >= min(widths):
            return credit, widths, "credit_not_below_width"
        return credit, widths, None

    def _expected(self, credit, legs, strikes, scale, facts):
        """Return ``credit + multiplier x`` the mean payoff over the forecast draws, each priced at ``scale``."""
        samples = facts.dist.samples
        # memoized per entry: the selector scores many candidates at one scale, and the model
        # book re-prices its winner; the arithmetic (and its order) is the unmemoized one's
        if "_expected_cache" not in self.__dict__:       # a hook called outside a walk
            self._reset_entry_caches()
        cache, key = self._expected_cache, (id(facts), scale, tuple(legs), tuple(strikes))
        hit = cache.get(key)
        if hit is None or hit[0] is not facts:
            levels_key = (id(facts), scale)
            levels = cache.get(levels_key)
            if levels is None or levels[0] is not facts:
                levels = cache[levels_key] = (facts, [facts.forward * math.exp(scale * z)
                                                      for z in samples])
            hit = cache[key] = (facts, _payoff_total(legs, levels[1], strikes))
        return credit + self.params["multiplier"] * hit[1] / len(samples)

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

    def _charge_window(self, instrument, day, settle_date):
        """Return the closes a charge reads: the ``[day, settle_date]`` slice when every date is canonical ISO, else them all."""
        # The charge owners parse every row's date and drop those outside the window; canonical
        # ISO strings sort as dates do, and _prepare already refused a bad close or a repeated
        # date, so the slice is exactly the rows they keep. Otherwise they see the full list and
        # refuse as before.
        rows = self._closes[instrument]
        if not (self._closes_iso[instrument] and _iso_day(day) and _iso_day(settle_date)):
            return rows
        dates = self._close_dates[instrument]
        return rows[bisect_left(dates, day):bisect_right(dates, settle_date)]

    def _american_charge(self, instrument, legs, strikes, day, settle_date):
        """Return the early-exercise charge as ``(total_usd, {side: usd})``: put carry at the short put, call dividends at the short call, each only where ``legs`` holds it."""
        closes, multiplier = self._charge_window(instrument, day, settle_date), self.params["multiplier"]
        charges = {}
        put = self._short_strike(legs, strikes, "put")
        if put is not None:
            charges["put"] = american_put_carry(closes, put, day, settle_date,
                                                self.params["carry_rate"], multiplier)
        call = self._short_strike(legs, strikes, "call")
        if call is not None:
            charges["call"] = american_call_dividend(closes, call, day, settle_date, multiplier)
        return sum(charges.values(), 0.0), charges

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
        paid = dividends_paid(self._charge_window(facts.instrument, facts.day, facts.settle_date),
                              facts.day, facts.settle_date)
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
        self._reset_entry_caches()
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
            "residual_mean_pnl_usd": mean_or_zero(residuals),
            "residual_t": t_or_zero(residuals),
            "pnl_t": t_or_zero([c["pnl_usd"] for c in traded]),
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


#: A debit gate's reasons replace the condor's credit gates' (the one family of book reasons).
_DEBIT_BOOK_REASONS = tuple(
    {"nonpositive_credit": "nonpositive_debit",
     "credit_not_below_width": "debit_not_below_width"}.get(reason, reason)
    for reason in CondorQuoteBacktest.BOOK_REASONS)


class DebitStructureQuoteBacktest(CondorQuoteBacktest):
    """Non-overlapping long (debit) structures on one underlying, priced from archived quotes.

    ADR-0197. The condor backtest where the structure is BOUGHT: a long straddle or a long vertical
    spread, the positive-convexity side the credit structures sell. The walk, expiry, settlement,
    American charge, wing split, delta benchmark, ADR-0194 ``gate_fields`` and ADR-0196
    ``size_fields`` are :class:`CondorQuoteBacktest`'s; a member supplies only where its strikes
    go (:meth:`_target_strikes`, the one abstract hook) and its legs
    (:data:`~index_options.contracts.LONG_STRADDLE_LEGS` and the long spreads').

    **Debit.** A long leg buys at the ask and a short leg sells at the bid, plus ``len(LEGS) x
    fee_per_leg``. The cell's ``credit_usd`` is that cash, the signed entry credit of every
    archived-quote cell: NEGATIVE for a debit (``credit_usd = -debit``), so ``pnl_usd = credit_usd
    + multiplier x structure_payoff(S_T) - charge`` is the condor's formula unchanged, and
    ``<book>_mean_credit_usd`` reports the mean of those negative numbers. A book refuses an entry
    as ``nonpositive_debit`` when the debit, the structure's maximum loss
    (:func:`~index_options.contracts.structure_max_loss`), is not positive, and, for a spread, as
    ``debit_not_below_width`` when the debit in USD, FEES INCLUDED, reaches ``multiplier x`` the
    narrowest width: the structure then cannot finish positive (at best it breaks even). These
    replace the condor's two credit gates in :attr:`BOOK_REASONS`; the other reasons keep
    their meanings. The **model** book enters when ``E_P[pnl]`` (over the forecast draws, at the
    horizon scale, as for the condor) exceeds ``min_edge_usd``; **always** enters every time the
    debit gate passes; **implied** places the same strikes by the VIX-lognormal quantile at the
    chain's ATM iv (``z_p - s / 2``, the condor's drift) and needs an ATM iv (``no_atm_iv``).
    Role ``score``, forbidden for serving, never decision-eligible: fills are the end-of-day
    touch at zero latency.

    **The American charge is a conservative bound, and here it runs against the structure.** A
    short leg is charged what the condor's is (ADR-0187: a put's carry from the first close below
    its strike, a call's dividend from the first ex-date with a pre-ex close above it), and that
    rule IGNORES the long leg by design. For a debit vertical the long leg protects the short
    one (exercising it nets the width), so assignment can only help the holder: the charge is a
    pure penalty, up to the short strike ``x (exp(carry_rate x tau) - 1) x multiplier`` for a put
    (``tau`` the years to settlement), the dividends for a call. It biases a debit vertical's P&L,
    and so the hedge study's verdict, AGAINST the sleeve; it is kept because a bound that ignores
    a leg is the ADR-0187 rule and this node does not second-guess it. A straddle holds no short
    leg and is charged ``0.0``.

    **A member declares what it is.** Beside :meth:`_target_strikes` a member supplies
    :attr:`LEGS`, :attr:`SIDES`, :attr:`REPORT_KIND` and :attr:`UNITS`, all abstract: a member that
    leaves them out would inherit the condor's, print a condor-labelled report the ledger studies
    would read with the condor's four legs, and fail late, so it refuses at construction instead.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Required: ``split``, ``multiplier`` (int >= 1), ``fee_per_leg`` (>= 0), ``dte_min`` /
        ``dte_max`` (ints, 1 <= min <= max), ``max_abs_log_moneyness`` (> 0), ``label_horizon``
        (int >= 1) and ``carry_rate`` (>= 0). Optional: ``min_edge_usd`` (0), ``cvar_alpha``
        (0.95), ``gate_fields`` and ``size_fields``. A member adds its own (no ``short_q``).

    Examples
    --------
    A member that puts a long straddle at the strike nearest the forward, declaring its own
    legs, wings and report names::

        class NearestStrike(DebitStructureQuoteBacktest):
            LEGS = LONG_STRADDLE_LEGS
            SIDES = ("put", "call")
            REPORT_KIND = "archived_quote_nearest_straddle_backtest"
            UNITS = "USD per nearest-strike straddle, one contract per leg"

            def _target_strikes(self, listed, forward, scale, quantile):
                strike = min(listed["put"].keys() & listed["call"].keys(),
                             key=lambda k: abs(k - forward))
                return (strike, strike), None

        node = NearestStrike("backtest", {
            "split": "val", "multiplier": 100, "fee_per_leg": 0.65, "dte_min": 5, "dte_max": 10,
            "max_abs_log_moneyness": 0.2, "label_horizon": 5, "carry_rate": 0.055})
    """

    BOOK_REASONS = _DEBIT_BOOK_REASONS
    _REQUIRED = tuple(knob for knob in CondorQuoteBacktest._REQUIRED
                      if knob not in ("short_q", "wing_z"))
    _PARAMS = tuple(sorted(_REQUIRED + tuple(CondorQuoteBacktest.DEFAULTS)
                           + _CondorBacktestBase.SHARED_OPTIONAL))

    @property
    @abstractmethod
    def LEGS(self):
        """The structure traded, ``(right, signed quantity)`` per leg in strike order: a member's own leg set, never the condor's."""

    @property
    @abstractmethod
    def SIDES(self):
        """The wings of that structure, its distinct rights in leg order (``_sides_of`` of :attr:`LEGS`)."""

    @property
    @abstractmethod
    def REPORT_KIND(self):
        """The report's ``kind``: what this member's output calls itself, never the condor's."""

    @property
    @abstractmethod
    def UNITS(self):
        """The report's ``units``, e.g. ``"USD per long straddle, one contract per leg"``."""

    @classmethod
    def _quantile_problems(cls, problems, params):
        """Check nothing: a member with no quantile knob has none to check."""

    @abstractmethod
    def _target_strikes(self, listed, forward, scale, quantile):
        """Place this structure's strikes on one expiry's listed rows.

        Parameters
        ----------
        listed : dict
            ``{right: {strike: chain row}}`` for the entry's expiry.
        forward : float
            The entry close.
        scale : float
            The horizon scale the targets are standardized by: the model's ``s'`` for the forecast
            books, the chain's own ``iv sqrt(DTE / 365)`` for the implied book.
        quantile : callable
            ``quantile(p)`` is the standardized target at probability ``p``: the forecast's own
            for the forecast books, the VIX-lognormal's ``z_p - s / 2`` for the implied book.

        Returns
        -------
        tuple
            ``(strikes, None)`` with one strike per leg in :attr:`LEGS` order, or
            ``(None, reason)`` with a reason in :attr:`BOOK_REASONS`.
        """

    def _priced(self, legs, strikes, quotes):
        """Return ``(credit_usd, widths, reason)``: the signed cash at entry (a debit is negative), and why a book must skip the debit (or ``None``)."""
        multiplier = self.params["multiplier"]
        credit, _per_share, widths = self._net_credit(legs, strikes, quotes)
        if not structure_max_loss(legs, strikes, credit, multiplier) > 0:
            return credit, widths, "nonpositive_debit"
        if widths and -credit >= multiplier * min(widths):      # the debit, fees in, cannot finish positive
            return credit, widths, "debit_not_below_width"
        return credit, widths, None

    def _forecast_books(self, facts, scale):
        """Return the model and always cells: the structure's strikes at the forecast's own quantiles."""
        strikes, reason = self._target_strikes(facts.listed, facts.forward, scale,
                                               facts.dist.quantile)
        return {book: self._book(book, strikes, reason, scale, facts)
                for book in self.FORECAST_BOOKS}

    def _implied_book(self, facts, atm_iv):
        """Return the implied cell: the same strike rule at the VIX-lognormal quantiles, ``no_atm_iv`` without an ATM iv."""
        if atm_iv is None:
            return self._book("implied", None, "no_atm_iv", None, facts)
        scale = atm_iv * math.sqrt(facts.dte / 365)
        strikes, reason = self._target_strikes(
            facts.listed, facts.forward, scale,
            lambda p: NormalDist().inv_cdf(p) - scale / 2)
        return self._book("implied", strikes, reason, scale, facts)


class LongStraddleQuoteBacktest(DebitStructureQuoteBacktest):
    """Non-overlapping long straddles on one underlying, from archived quotes.

    ADR-0197. A long put and a long call at ONE strike
    (:data:`~index_options.contracts.LONG_STRADDLE_LEGS`): the strike nearest the entry close
    among those within ``max_abs_log_moneyness`` where BOTH the put and the call can be bought (a
    positive ask and an ask size; a bid is irrelevant), the LOWER strike on a tie. A strike list
    is ``(K, K)``. It places its strike without the forecast's quantiles, so it has no quantile
    knob and all three books trade the same strike; they differ only in whether they enter (the
    model on ``E_P[pnl] > min_edge_usd``) and in ``no_atm_iv``, which the implied book alone
    reports. No candidate strike is ``no_quotable_strike``. The straddle holds no short leg, so
    there is no American charge and no width (``debit_not_below_width`` cannot occur); a debit
    is refused only as ``nonpositive_debit``. The evidence (Johnson 2017) is that long straddles pay
    when the VIX curve is inverted at short horizons: the ADR-0194 gate study measures that. See
    :class:`DebitStructureQuoteBacktest` for the debit, the books and the metrics.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Exactly :class:`DebitStructureQuoteBacktest`'s; no ``short_q``, ``long_q`` or ``wing_z``.

    Examples
    --------
    SPY's 2-3 day cell::

        node = LongStraddleQuoteBacktest("backtest", {
            "split": "val", "multiplier": 100, "fee_per_leg": 0.65, "dte_min": 2, "dte_max": 3,
            "max_abs_log_moneyness": 0.07, "label_horizon": 2, "carry_rate": 0.055})
        out = node.run(ctx, {"forecasts": rows, "chain": quotes, "underlying": closes})
        out["metrics"]["model_total_pnl_usd"]
    """

    LEGS = LONG_STRADDLE_LEGS
    SIDES = _sides_of(LEGS)
    REPORT_KIND = "archived_quote_long_straddle_backtest"
    UNITS = "USD per long straddle, one contract per leg"

    def _target_strikes(self, listed, forward, scale, quantile):
        """Return the strike nearest ``forward`` (the lower on a tie) where both legs can be bought, inside the band; else ``no_quotable_strike``."""
        band = self.params["max_abs_log_moneyness"]
        puts, calls = (self._quotable_strikes(listed[right], "buy") for right in ("put", "call"))
        if puts is None or calls is None:
            both = [k for k in sorted(listed["put"].keys() & listed["call"].keys())
                    if k > 0 and abs(math.log(k / forward)) <= band
                    and self._quotable(listed["put"][k], "buy")
                    and self._quotable(listed["call"][k], "buy")]
        else:      # the same filter, quotability read from the per-entry memo
            puts, calls = set(puts), set(calls)
            both = [k for k in sorted(listed["put"].keys() & listed["call"].keys())
                    if k > 0 and abs(math.log(k / forward)) <= band and k in puts and k in calls]
        if not both:
            return None, "no_quotable_strike"
        strike = min(both, key=lambda k: abs(k - forward))     # ascending: min keeps the lower of a tie
        return (strike, strike), None


class _LongVerticalQuoteBacktest(DebitStructureQuoteBacktest):
    """The strike rule the two long verticals share: a bought leg at a forecast quantile, a sold leg ``wing_z`` beyond it.

    A member supplies :attr:`LEGS` (two legs of one right, the long one nearer the money), its
    :attr:`SIDES`, report names (:attr:`REPORT_KIND`, :attr:`UNITS`) and its default ``long_q``
    (:attr:`DEFAULT_LONG_Q`, which it also writes into its ``DEFAULTS`` and ``_PARAMS``); all are
    abstract, so this class itself refuses construction. The bought leg targets
    ``forward x exp(s' Q(long_q))`` and the sold leg ``wing_z`` standardized units further from
    the money (up for calls, down for puts). Each target snaps OUTWARD, away from the money (up
    for calls, down for puts, the condor's direction), onto the nearest strike that is
    quotable for ITS side: the bought leg needs an ask and an ask size, the sold leg a bid and
    a bid size, and the sold leg must also lie strictly beyond the bought one. A target beyond
    ``max_abs_log_moneyness`` is ``target_outside_band``, a leg with no such strike
    ``no_quotable_strike``, a non-increasing or non-positive geometry ``degenerate_strikes``,
    classified in that order.
    """

    _REQUIRED = DebitStructureQuoteBacktest._REQUIRED + ("wing_z",)

    @property
    @abstractmethod
    def DEFAULT_LONG_Q(self):
        """The forecast quantile the long leg sits at when a document does not say: a member's own, written once (:attr:`DEFAULTS` reads it)."""

    @classmethod
    def _quantile_problems(cls, problems, params):
        """Check ``long_q``: a probability strictly inside (0, 1); it may be left to its default."""
        q = params.get("long_q", cls.DEFAULTS["long_q"])
        if not (number_ok(q) and 0 < q < 1):
            problems.append(f"long_q must be in (0, 1), got {q!r}")

    def _snap_outward(self, rows, targets, sides, direction):
        """Snap each target, in order from the money outward, onto the nearest strike at or beyond it that is quotable for its side and strictly beyond the one before; ``None`` when a leg has none."""
        picked = self._bisect_outward(rows, targets, sides, direction)
        if picked is not False:
            return picked
        picked = []
        for target, side in zip(targets, sides):
            beyond = [k for k, row in rows.items()
                      if direction * (k - target) >= 0
                      and (not picked or direction * (k - picked[-1]) > 0)
                      and self._quotable(row, side)]
            if not beyond:
                return None
            picked.append(min(beyond, key=lambda k: direction * k))
        return tuple(picked)

    def _bisect_outward(self, rows, targets, sides, direction):
        """Return :meth:`_snap_outward`'s answer by bisecting the per-entry quotable strikes, or ``False`` when only the scan can say."""
        picked = []
        for target, side in zip(targets, sides):
            strikes = self._quotable_strikes(rows, side)
            if strikes is None or target != target:
                return False
            if direction > 0:      # the smallest k >= target (and > the previous pick)
                at = bisect_left(strikes, target)
                if picked:
                    at = max(at, bisect_right(strikes, picked[-1]))
                if at == len(strikes):
                    return None
                picked.append(strikes[at])
            else:                  # the largest k <= target (and < the previous pick)
                at = bisect_right(strikes, target)
                if picked:
                    at = min(at, bisect_left(strikes, picked[-1]))
                if at == 0:
                    return None
                picked.append(strikes[at - 1])
        return tuple(picked)

    def _target_strikes(self, listed, forward, scale, quantile):
        """Return ``(strikes, None)`` low to high, or ``(None, reason)``: band, then quotability, then geometry."""
        right = self.LEGS[0][0]
        direction = 1 if right == "call" else -1
        outward = self.LEGS if direction > 0 else self.LEGS[::-1]
        sides = ["buy" if sign > 0 else "sell" for _right, sign in outward]
        z_long = quantile(self._knob("long_q"))
        z_by_side = {"buy": z_long, "sell": z_long + direction * self.params["wing_z"]}
        exponents = [z_by_side[side] for side in sides]
        if self._outside_band(scale, exponents):
            return None, "target_outside_band"
        picked = self._snap_outward(listed[right], [forward * math.exp(scale * z)
                                                    for z in exponents], sides, direction)
        if picked is None:
            return None, "no_quotable_strike"
        return self._geometry(picked if direction > 0 else picked[::-1])


class LongCallSpreadQuoteBacktest(_LongVerticalQuoteBacktest):
    """Non-overlapping long call spreads on one underlying, from archived quotes.

    ADR-0197. A long call under a short call
    (:data:`~index_options.contracts.LONG_CALL_SPREAD_LEGS`): the bullish, floored-loss
    structure that carries the equity premium through the option market's least overpriced leg
    (a hypothesis the backtest tests). The long call sits at the forecast's ``long_q`` quantile
    (default 0.5, about at the money) and the short call ``wing_z`` standardized units above,
    each snapped UP (outward) onto a strike quotable for its side, the short one strictly above
    the long (see :class:`_LongVerticalQuoteBacktest`). The call wing alone holds the short call,
    so only the call dividend is charged (the ADR-0187 bound, which ignores the long call: a
    penalty here, see :class:`DebitStructureQuoteBacktest`), and ``side_pnl_usd`` has the one
    side ``"call"``. The delta benchmark is positive. See :class:`DebitStructureQuoteBacktest`
    for the debit, the books and the metrics.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        :class:`DebitStructureQuoteBacktest`'s, plus required ``wing_z`` (> 0) and optional
        ``long_q`` (in (0, 1), default :attr:`DEFAULT_LONG_Q`).

    Examples
    --------
    SPY's 30-45 day cell, an at-the-money long call and a short call 0.65 units above::

        node = LongCallSpreadQuoteBacktest("backtest", {
            "split": "val", "long_q": 0.5, "wing_z": 0.65, "multiplier": 100,
            "fee_per_leg": 0.65, "dte_min": 30, "dte_max": 45,
            "max_abs_log_moneyness": 0.2, "label_horizon": 22, "carry_rate": 0.055})
        out = node.run(ctx, {"forecasts": rows, "chain": quotes, "underlying": closes})
        out["metrics"]["model_call_total_pnl_usd"]
    """

    #: The forecast quantile the long call sits at when a document does not say.
    DEFAULT_LONG_Q = 0.5
    DEFAULTS = {**CondorQuoteBacktest.DEFAULTS, "long_q": DEFAULT_LONG_Q}
    _PARAMS = tuple(sorted(_LongVerticalQuoteBacktest._REQUIRED + tuple(DEFAULTS)
                           + _CondorBacktestBase.SHARED_OPTIONAL))
    LEGS = LONG_CALL_SPREAD_LEGS
    SIDES = _sides_of(LEGS)
    REPORT_KIND = "archived_quote_long_call_spread_backtest"
    UNITS = "USD per long call spread, one contract per leg"


class LongPutSpreadQuoteBacktest(_LongVerticalQuoteBacktest):
    """Non-overlapping long put spreads on one underlying, from archived quotes.

    ADR-0197. A short put under a long put
    (:data:`~index_options.contracts.LONG_PUT_SPREAD_LEGS`), the bought put the HIGHER strike: the
    bearish, floored-loss structure a hedge sleeve can be built from (bought protection has a
    negative standalone expectation, which is why the hedge study asks whether it beats a smaller
    core at the same tail risk). The long put sits at the forecast's ``long_q`` quantile (default
    0.35, below the median) and the short put ``wing_z`` standardized units below, each snapped
    DOWN (outward) onto a strike quotable for its side, the short one strictly below the long
    (see :class:`_LongVerticalQuoteBacktest`). Strikes are listed low to high, short put
    first. Only the short put's carry is charged (the ADR-0187 bound, which ignores the long put:
    a penalty here, see :class:`DebitStructureQuoteBacktest`), and ``side_pnl_usd`` has the one
    side ``"put"``. The delta benchmark is negative. See :class:`DebitStructureQuoteBacktest` for
    the debit, the books and the metrics.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        :class:`DebitStructureQuoteBacktest`'s, plus required ``wing_z`` (> 0) and optional
        ``long_q`` (in (0, 1), default :attr:`DEFAULT_LONG_Q`).

    Examples
    --------
    SPY's 30-45 day cell, a long put near the 35th percentile and a short put 0.65 units below::

        node = LongPutSpreadQuoteBacktest("backtest", {
            "split": "val", "long_q": 0.35, "wing_z": 0.65, "multiplier": 100,
            "fee_per_leg": 0.65, "dte_min": 30, "dte_max": 45,
            "max_abs_log_moneyness": 0.2, "label_horizon": 22, "carry_rate": 0.055})
        out = node.run(ctx, {"forecasts": rows, "chain": quotes, "underlying": closes})
        out["metrics"]["model_put_total_pnl_usd"]
    """

    #: The forecast quantile the long put sits at when a document does not say.
    DEFAULT_LONG_Q = 0.35
    DEFAULTS = {**CondorQuoteBacktest.DEFAULTS, "long_q": DEFAULT_LONG_Q}
    _PARAMS = tuple(sorted(_LongVerticalQuoteBacktest._REQUIRED + tuple(DEFAULTS)
                           + _CondorBacktestBase.SHARED_OPTIONAL))
    LEGS = LONG_PUT_SPREAD_LEGS
    SIDES = _sides_of(LEGS)
    REPORT_KIND = "archived_quote_long_put_spread_backtest"
    UNITS = "USD per long put spread, one contract per leg"


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
       :func:`~index_options.contracts.structure_max_loss` (``multiplier x`` the widest
       vertical less the credit), positive because the gate keeps the credit under
       the narrowest one.

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
        credit, _widths, reason = self._priced(legs, strikes, quotes)
        if reason is not None:
            return None
        max_loss = structure_max_loss(legs, strikes, credit, self.params["multiplier"])
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
        return float(min(self._knob("max_weight"), max(self._knob("min_weight"), ratio)))

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


class ExactExpiryPanelRead(Node):
    """Read the exact-expiry panel once and emit the declared columns (ADR-0217).

    The one pipeline doorway to :meth:`ExactExpiryCDFPanel.read`, called
    unchanged: this node restates no formula, it only narrows the frame to
    ``columns`` and reports what the read consumed. Role ``data``, no inputs;
    the read happens once, at the driver's fingerprint, and ``run`` serves
    that same snapshot. Forbidden for serving.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        ``surface``, ``lifecycle`` and ``chain_features`` each name an onboarded file by store
        reference (``{"source", "stream", "relpath"[, "manifest_sha256"]}``, resolved against
        ``root``) or, legacy, by path string.
        The tail-data ``read()`` keys except ``archive_root`` (the
        ``prepare`` stage's) and the ones ``read()`` ignores here
        (``decision_regions``, ``macro_event_calendars``: refused), plus
        ``columns``, the frame columns to emit. Required: ``root symbols price_source
        iv_source since max_dte lags windows feature_gap_days reference_floor
        spot_tolerance columns``, plus ``surface`` and ``lifecycle`` unless ``reader`` is
        set; optional:
        ``chain_features raw_chain market_symbols fred_market_symbols
        surface_features ohlc_windows matched_dte_vrp reference_window change_lags
        directional_windows periods_per_year calendar calendar_pad_days dividend_field``
        (the study conventions; see :func:`index_options.cdf_study.panel_convention_problems`)
        and, ADR-0230, ``exact_dte`` (keep one horizon), ``reader`` (``price_calendar``
        builds the panel from a price file alone), ``keyed_tables`` and
        ``corporate_actions`` (see :func:`index_options.cdf_study.panel_reader_problems`), and
        ``as_of_acquisition_ms`` (the observation reads' vintage, ADR-0236 amendment 3).

    Examples
    --------
    Emit two engineered columns beside the row identity::

        node = ExactExpiryPanelRead("panel", {
            "root": "./ob", "surface": "surface.parquet", "lifecycle": "life.parquet",
            "symbols": {"QQQ": "VXN"}, "price_source": "prices", "iv_source": "indexes",
            "since": "2016-01-01", "max_dte": 45, "lags": 22, "windows": [1, 5, 22],
            "feature_gap_days": 7, "reference_floor": 0.001, "spot_tolerance": 0.02,
            "matched_dte_vrp": True,
            "columns": ["symbol", "quote_date", "expiry", "matched_vrp", "rv_22"]})
        out = node.run(ctx, {})   # out["records"], out["provenance"]
    """

    role = "data"
    outputs = ("records", "provenance", "cohort")
    _REQUIRED = ("columns", "feature_gap_days", "iv_source", "lags", "max_dte",
                 "price_source", "reference_floor", "root", "since", "spot_tolerance",
                 "symbols", "windows")
    #: ``surface`` and ``lifecycle`` are required only while no ``reader`` is selected (ADR-0230).
    _SURFACE_REQUIRED = ("lifecycle", "surface")
    _OPTIONAL = ("as_of_acquisition_ms", "calendar", "calendar_pad_days", "chain_features",
                 "change_lags", "cohort_columns", "corporate_actions", "directional_windows",
                 "dividend_field", "exact_dte",
                 "fred_market_symbols", "keyed_tables", "market_symbols", "matched_dte_vrp",
                 "ohlc_windows", "periods_per_year", "raw_chain", "reader", "reference_window",
                 "surface_features")
    _PARAMS = _SURFACE_REQUIRED + _REQUIRED + _OPTIONAL

    def __init__(self, key, params=None, **kwargs):
        super().__init__(key, params, **kwargs)
        self._snapshot = None

    @classmethod
    def validate_params(cls, params):
        """Refuse an undeclared knob, a missing required one and a malformed ``columns``/``symbols``.

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
        problems += [f"{name} is required" for name in cls._REQUIRED if name not in params]
        if params.get("reader") is None:
            problems += [f"{name} is required (no reader selected)"
                         for name in cls._SURFACE_REQUIRED if params.get(name) is None]
        owner = _CondorBacktestBase           # the distinct-list rule has one home (ADR-0195)
        owner._distinct_list_problems(problems, params, "columns", owner._name_ok, "names")
        if "cohort_columns" in params:
            owner._distinct_list_problems(problems, params, "cohort_columns", owner._name_ok, "names")
        symbols = params.get("symbols")
        if "symbols" in params and (
                not isinstance(symbols, dict) or not symbols
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in symbols.items())):
            problems.append(f"symbols must be a non-empty {{ticker: index}} dict, got {symbols!r}")
        for knob in ("max_dte", "lags"):
            if knob in params:
                check_int_param(problems, knob, params[knob], ge=1)
        for name in ("surface", "lifecycle", "chain_features"):
            if params.get(name) is not None:
                problems += entry_problems(name, params[name])
        # the one owners of the integer-knob, convention and reader-selection rules
        from .cdf_study import panel_convention_problems, panel_int_problems, panel_reader_problems
        problems += panel_int_problems(params)
        problems += panel_convention_problems(params)
        problems += panel_reader_problems(params)
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep this research reader out of served graphs.

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

    def _read(self):
        """Read the panel once and keep ``columns`` as plain Python rows."""
        from .cdf_study import panel_class

        columns = self.params["columns"]
        config = {k: v for k, v in self.params.items() if k not in self._NODE_ONLY}
        adapter = panel_class(config)(config)
        frame = adapter.read()
        cohort = self.params.get("cohort_columns")
        return {"records": self._rows(frame, columns), "provenance": adapter.provenance(),
                "cohort": self._rows(frame, cohort) if cohort else []}

    #: Node params the reader's own config never sees (they only shape what the node emits).
    _NODE_ONLY = ("columns", "cohort_columns")

    def _rows(self, frame, columns):
        """Return ``columns`` of the frame as plain rows (NaN as None), refusing a missing one."""
        missing = [name for name in columns if name not in frame.columns]
        if missing:
            raise ValueError(f"{self.key}: the panel read has no column(s) {missing}")
        kept = frame[columns].astype(object)
        return kept.where(frame[columns].notna(), None).to_dict("records")

    def _snapshot_once(self):
        """Return the one read both ``fingerprint`` and ``run`` serve."""
        if self._snapshot is None:
            self._snapshot = self._read()
        return self._snapshot

    def fingerprint(self):
        """Name the data this read consumes: row count plus the read's own provenance.

        Returns
        -------
        dict
            ``kind``, ``rows`` and ``provenance`` (source hashes, reader
            fingerprints, code hash); it moves with any of them.
        """
        snapshot = self._snapshot_once()
        return {"kind": type(self).__name__, "rows": len(snapshot["records"]),
                "provenance": snapshot["provenance"]}

    def run(self, ctx, inputs):
        """Emit the snapshot the fingerprint saw.

        Parameters
        ----------
        ctx : NodeContext
            Unused: a source reads only its params.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``records`` (rows with exactly ``columns``; NaN as ``None``) and
            ``provenance`` (the read's refusals, hashes and fingerprints).

        Raises
        ------
        ValueError
            When ``columns`` names a column the read does not produce.
        """
        snapshot = self._snapshot_once()
        return {"records": snapshot["records"], "provenance": snapshot["provenance"],
                "cohort": snapshot["cohort"]}



def _bounded_settlement_index(dates, settle_date, max_gap_days):
    """One owner of preceding-close settlement with a later confirming date."""
    at = bisect_right(dates, settle_date) - 1
    if at < 0 or at + 1 >= len(dates):
        return None
    gap = (date.fromisoformat(settle_date) - date.fromisoformat(dates[at])).days
    return at if gap <= max_gap_days else None


class _ExpiryFill(NamedTuple):
    """The raw fill attributes consumed by the public WindowBook."""

    instrument: str
    side: str
    qty: int
    price: Fraction
    fee: Fraction


class CondorExpirySettle(Node):
    """Fold one-lot condor entry/expiry legs through WindowBook (ADR-0255).

    Selections carry decision_id, arm_id, symbol, quote_date, expiry, multiplier
    and four ordered legs: role, contract, right, side, strike,
    price_usd_per_share and fee_usd. Entry prices already include haircuts.
    Bars carry symbol/date and the configured settlement field. Status-less
    hand-authored selections remain supported; an explicit status must be trade.
    No I/O or broker action occurs. Evidence uses raw_fills rather than RunReport's
    reserved fills fallback; one condor, never one leg, is the statistical unit.

    Parameters
    ----------
    key : str
        Pipeline node identity.
    params : dict
        Required settlement_field (str), max_settlement_gap_days (int >= 0),
        end_before (ISO date, exclusive) and labels (dict with nonempty fill
        and exercise text). The date boundary also applies to confirming bars.
        Optional exercise_threshold_usd (positive number; absent = no flags).

    Notes
    -----
    ADR-0256: entry fills are dated ``fill_date`` when a selection carries one
    (strictly between quote_date and expiry), else quote_date. A selection with
    ``status: "unfilled"`` and an ``unfilled_reason`` is skipped (reason
    ``unfilled``) and counted in ``evidence["unfilled_by_reason"]``. With the
    threshold, each outcome adds ``exercise_flags`` (per leg: ``itm`` when
    ITM by at least the threshold, ``pin_zone`` when |settlement - strike| is
    below it) and ``would_deliver_shares`` (signed shares moved when exactly one
    leg of a wing is past the threshold, else 0); P&L stays intrinsic and no
    exercise, assignment or delivery is simulated.

    Raises
    ------
    ConfigError
        If required parameters are missing, unknown or malformed.

    Examples
    --------
    Construct a research-only adapter with an explicit protected boundary::

        node = CondorExpirySettle("settle", {
            "settlement_field": "as_traded_close",
            "max_settlement_gap_days": 4,
            "end_before": "2026-01-01",
            "labels": {
                "fill": "assumed same-day average fill",
                "exercise": "early exercise and assignment ignored",
            },
        })
        out = node.run(None, {"selections": [], "bars": [], "skips": []})
        # -> out["evidence"]["totals"]["n_condors"] is 0
    """

    role = "transform"
    outputs = ("outcomes", "skips", "evidence")
    _PARAMS = ("settlement_field", "max_settlement_gap_days", "labels", "end_before",
               "exercise_threshold_usd")
    _ROLES = ("LP", "SP", "SC", "LC")

    @classmethod
    def validate_params(cls, params):
        """Require explicit settlement, availability boundary and research labels.

        Parameters
        ----------
        params : dict
            Candidate parameter declaration.

        Returns
        -------
        list of str
            All declaration problems; empty when the parameters are usable.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if not isinstance(params.get("settlement_field"), str) or not params["settlement_field"]:
            problems.append("settlement_field must be nonempty text")
        check_int_param(problems, "max_settlement_gap_days",
                        params.get("max_settlement_gap_days"), ge=0)
        if not _iso_day(params.get("end_before")):
            problems.append("end_before must be an ISO date")
        labels = params.get("labels")
        if not isinstance(labels, dict) or set(labels) != {"fill", "exercise"}:
            problems.append("labels must contain fill and exercise")
        elif any(not isinstance(v, str) or not v.strip() for v in labels.values()):
            problems.append("labels must be nonempty text")
        threshold = params.get("exercise_threshold_usd")
        if "exercise_threshold_usd" in params and (
                isinstance(threshold, bool) or not number_ok(threshold) or threshold <= 0):
            problems.append("exercise_threshold_usd must be a positive finite number")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep research-only accounting out of served graphs.

        Parameters
        ----------
        params : dict
            Node parameter declaration; unused.
        verified_run_evidence : dict
            Recorded run evidence; unused.

        Returns
        -------
        str
            Always "forbidden".
        """
        return "forbidden"

    def _bars(self, rows):
        """Validate the complete bounded close input before any accounting."""
        grouped = {}
        field, bound = self.params["settlement_field"], self.params["end_before"]
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("symbol"), str)
                    or not row["symbol"] or not _iso_day(row.get("date"))
                    or row["date"] >= bound or not price_ok(row.get(field))):
                raise ValueError("settlement bars require symbol, bounded ISO date and positive close")
            grouped.setdefault(row["symbol"], []).append(row)
        for values in grouped.values():
            values.sort(key=lambda r: r["date"])
            if len({r["date"] for r in values}) != len(values):
                raise ValueError("settlement bars repeat a symbol/date")
        return grouped

    def _selection(self, row):
        """Refuse malformed or partial structures before creating any fills."""
        if not isinstance(row, dict):
            raise ValueError("selection must be a dict")
        if "status" in row and row["status"] != "trade":
            raise ValueError("selection status must be trade when declared")
        for key in ("decision_id", "arm_id", "symbol"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"selection requires {key}")
        for key in ("quote_date", "expiry"):
            if not _iso_day(row.get(key)) or row[key] >= self.params["end_before"]:
                raise ValueError(f"selection requires bounded {key}")
        if row["quote_date"] >= row["expiry"]:
            raise ValueError("expiry must follow quote_date")
        if "fill_date" in row and (not _iso_day(row["fill_date"])
                                   or not row["quote_date"] < row["fill_date"] < row["expiry"]):
            raise ValueError("fill_date must fall strictly between quote_date and expiry")
        if "forecast_settlement_date" in row:
            day = row["forecast_settlement_date"]
            if (not _iso_day(day) or not row["quote_date"] < day <= row["expiry"]):
                raise ValueError("invalid forecast settlement date")
        multiplier = row.get("multiplier")
        if isinstance(multiplier, bool) or not isinstance(multiplier, int) or multiplier < 1:
            raise ValueError("multiplier must be a positive integer")
        legs = row.get("legs")
        if not isinstance(legs, list) or len(legs) != len(CONDOR_LEGS):
            raise ValueError("one condor needs four ordered legs")
        for role, (right, sign), leg in zip(self._ROLES, CONDOR_LEGS, legs):
            if (not isinstance(leg, dict) or leg.get("role") != role
                    or leg.get("right") != right
                    or leg.get("side") != ("buy" if sign > 0 else "sell")
                    or not isinstance(leg.get("contract"), str) or not leg["contract"]
                    or not price_ok(leg.get("strike"))):
                raise ValueError("invalid ordered condor leg")
            for field in ("price_usd_per_share", "fee_usd"):
                if not number_ok(leg.get(field)) or leg[field] < 0:
                    raise ValueError(f"{field} must be finite and nonnegative")
            if "contracts" in leg and (type(leg["contracts"]) is not int or leg["contracts"] != 1):
                raise ValueError("exactly one contract per leg is supported")
            if "multiplier" in leg and (type(leg["multiplier"]) is not int
                                       or leg["multiplier"] != multiplier):
                raise ValueError("contract multiplier disagrees with selection")
        if len({leg["contract"] for leg in legs}) != len(legs):
            raise ValueError("condor contracts must be distinct")
        if any(a["strike"] >= b["strike"] for a, b in zip(legs, legs[1:])):
            raise ValueError("condor strikes must be strictly ordered")

    @staticmethod
    def _unfilled(row):
        """Refuse an ``unfilled`` selection that lacks its identity or its reason."""
        for key in ("decision_id", "arm_id", "symbol", "quote_date", "expiry", "unfilled_reason"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"unfilled selection requires {key}")

    def _exercise(self, row, level):
        """Return per-leg ITM/pin-zone flags and the signed shares an exercise would move."""
        threshold = Fraction(str(self.params["exercise_threshold_usd"]))
        flags, itm = [], []
        for leg in row["legs"]:
            distance = level - Fraction(str(leg["strike"]))
            moneyness = distance if leg["right"] == "call" else -distance
            itm.append(moneyness >= threshold)
            flags.append({"role": leg["role"], "contract": leg["contract"], "itm": itm[-1],
                          "pin_zone": abs(distance) < threshold})
        shares = 0
        for wing in ((0, 1), (2, 3)):
            if sum(itm[i] for i in wing) == 1:
                leg = row["legs"][wing[0] if itm[wing[0]] else wing[1]]
                shares += (row["multiplier"] * (-1 if leg["right"] == "call" else 1)
                           * (1 if leg["side"] == "sell" else -1))
        return flags, shares

    def _disclosure(self, outcomes):
        """Return the exercise-disclosure counts over every settled outcome."""
        legs = [flag for outcome in outcomes for flag in outcome["exercise_flags"]]
        return {"exercise_threshold_usd": self.params["exercise_threshold_usd"],
                "n_itm_legs": sum(flag["itm"] for flag in legs),
                "n_pin_zone_legs": sum(flag["pin_zone"] for flag in legs),
                "n_would_deliver_shares": sum(o["would_deliver_shares"] != 0 for o in outcomes)}

    def _fold(self, row, settled, confirmed_date):
        """Emit auditable raw fills and reconcile exact closed-form expiry P&L."""
        identity = {key: row[key] for key in ("decision_id", "arm_id", "symbol",
                                             "quote_date", "expiry")}
        book, positions, fills, orders = WindowBook(), Counter(), [], []
        multiplier, level = row["multiplier"], Fraction(str(settled[self.params["settlement_field"]]))
        credit, fees = Fraction(0), Fraction(0)
        for leg, (_, sign) in zip(row["legs"], CONDOR_LEGS):
            position = json.dumps([row["arm_id"], row["decision_id"], leg["contract"]],
                                  separators=(",", ":"))
            premium, fee = Fraction(str(leg["price_usd_per_share"])), Fraction(str(leg["fee_usd"]))
            strike = Fraction(str(leg["strike"]))
            intrinsic = leg_intrinsic(leg["right"], strike, level)
            credit -= sign * premium
            fees += fee
            for phase, side, price, charge, day in (
                ("entry", leg["side"], premium, fee, row.get("fill_date", row["quote_date"])),
                ("expiry", "sell" if sign > 0 else "buy", intrinsic, Fraction(0), row["expiry"]),
            ):
                signed = 1 if side == "buy" else -1
                book.apply(_ExpiryFill(position, side, multiplier, price, charge))
                positions[position] += signed * multiplier
                raw = {**identity, "position_id": position, "contract": leg["contract"],
                       "role": leg["role"], "phase": phase, "date": day, "side": side,
                       "contracts": 1, "multiplier": multiplier, "qty": multiplier,
                       "price_usd_per_share": float(price), "fee_usd": float(charge),
                       "fill_label": self.params["labels"]["fill"]}
                if phase == "expiry":
                    raw.update(exercise_label=self.params["labels"]["exercise"],
                               settlement_date=settled["date"],
                               settlement_confirmed_date=confirmed_date)
                orders.append({**raw, "order_id": position + ":" + phase})
                fills.append({**raw, "order_id": position + ":" + phase,
                              "fill_id": position + ":" + phase + ":fill"})
        payoff = structure_payoff(CONDOR_LEGS, level,
                                  [Fraction(str(leg["strike"])) for leg in row["legs"]])
        expected = multiplier * (credit + payoff) - fees
        if any(positions.values()) or book.realised != expected:
            raise ValueError("expiry accounting did not reconcile")
        outcome = {**identity, "settlement_date": settled["date"], "settlement": float(level),
                   "settlement_confirmed_date": confirmed_date,
                   "multiplier": multiplier, "fees_usd": float(fees),
                   "credit_usd_per_share": float(credit), "pnl_usd": float(book.realised),
                   "closed_form_pnl_usd": float(expected), "end_flat": True}
        if "exercise_threshold_usd" in self.params:
            outcome["exercise_flags"], outcome["would_deliver_shares"] = self._exercise(row, level)
        return outcome, orders, fills

    def run(self, ctx, inputs):
        """Return one outcome or explicit refusal for every validated selection.

        Parameters
        ----------
        ctx : NodeContext or None
            Pipeline context; unused because this adapter performs no I/O.
        inputs : dict
            selections, bars and skips lists. Selections follow the class
            contract; bars contain symbol, date and the settlement price field.
            All input rows are validated before accounting begins.

        Returns
        -------
        dict
            outcomes and skips lists, plus evidence containing exact-fold
            reconciliation results, one-condor totals and raw_orders/raw_fills.
            Missing bounded settlement evidence produces a skip, not a trade.

        Raises
        ------
        ValueError
            If identities, dates, prices, fees, leg structure or units are
            malformed, dates reach end_before, identities repeat, or the
            accounting result does not reconcile.
        """
        if not isinstance(inputs, dict) or set(inputs) != {"selections", "bars", "skips"}:
            raise ValueError("inputs must contain selections, bars and skips")
        if any(not isinstance(inputs[key], list) for key in inputs):
            raise ValueError("settlement inputs must be lists")
        if any(not isinstance(row, dict) for row in inputs["skips"]):
            raise ValueError("upstream skips must be records")
        grouped = self._bars(inputs["bars"])
        seen, unfilled = set(), Counter()
        for row in inputs["selections"]:
            if isinstance(row, dict) and row.get("status") == "unfilled":
                self._unfilled(row)
            else:
                self._selection(row)
            key = row["arm_id"], row["decision_id"]
            if key in seen:
                raise ValueError("duplicate arm/decision identity")
            seen.add(key)
        outcomes, orders, fills, skips = [], [], [], list(inputs["skips"])
        for row in inputs["selections"]:
            if row.get("status") == "unfilled":
                unfilled[row["unfilled_reason"]] += 1
                skips.append({**{k: row[k] for k in ("decision_id", "arm_id", "symbol",
                                                   "quote_date", "expiry")},
                              "reason": "unfilled", "unfilled_reason": row["unfilled_reason"]})
                continue
            bars = grouped.get(row["symbol"], [])
            at = _bounded_settlement_index([bar["date"] for bar in bars], row["expiry"],
                                           self.params["max_settlement_gap_days"])
            if at is None or bars[at]["date"] < row["quote_date"]:
                skips.append({**{k: row[k] for k in ("decision_id", "arm_id", "symbol",
                                                   "quote_date", "expiry")},
                              "reason": "missing_settlement"})
                continue
            if ("forecast_settlement_date" in row
                    and row["forecast_settlement_date"] != bars[at]["date"]):
                skips.append({**{k: row[k] for k in
                                  ("decision_id", "arm_id", "symbol", "quote_date", "expiry")},
                              "reason": "forecast_settlement_mismatch"})
                continue
            outcome, new_orders, new_fills = self._fold(row, bars[at], bars[at + 1]["date"])
            outcomes.append(outcome)
            orders.extend(new_orders)
            fills.extend(new_fills)
        orders.sort(key=lambda r: (r["date"], r["order_id"]))
        fills.sort(key=lambda r: (r["date"], r["fill_id"]))
        totals = {"n_condors": len(outcomes), "n_skips": len(skips),
                  "net_pnl_usd": sum(row["pnl_usd"] for row in outcomes)}
        evidence = {"stage": "condor_expiry", "totals": totals, "outcomes": outcomes,
                    "skips": skips, "raw_orders": orders, "raw_fills": fills,
                    "units": "USD per one-lot condor; no portfolio return"}
        if unfilled:
            evidence["unfilled_by_reason"] = dict(sorted(unfilled.items()))
        if "exercise_threshold_usd" in self.params:
            evidence["exercise_disclosure"] = self._disclosure(outcomes)
        return {"outcomes": outcomes, "skips": skips, "evidence": evidence}


class _ContextDataError(ValueError):
    """A context's own data failed validation in ``build_model`` (not a solver fault)."""


class _IntegrityError(ValueError):
    """A solved incumbent broke a structural guard; ``reason`` names the skip it earns."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class _BatchContextError(ValueError):
    """A batch row set could not be prepared into one context."""


def liquid_leg_haircut(price, trade_count, volume, liquidity, tier_terms, multiplier):
    """Return one leg's tiered haircut and short eligibility, or ``None`` if it is illiquid.

    The one owner (ADR-0256) of the decision liquidity rule and the tiered haircut
    that :class:`RobustCondorBatchSelect` applies to a leg at the decision bar and
    again, with the same arm, at the fill bar.

    Parameters
    ----------
    price : float
        The bar's VWAP in USD per share, finite and nonnegative.
    trade_count, volume : int
        The bar's liquidity, nonnegative.
    liquidity : dict
        ``min_trade_count`` and ``min_volume`` (the arm's, inclusive thresholds).
    tier_terms : dict
        ``pct`` and ``floor_usd`` of the ticker's haircut tier.
    multiplier : float
        The arm's haircut multiplier.

    Returns
    -------
    tuple or None
        ``None`` when the bar is below either threshold; else
        ``(haircut, short_ok)`` with ``haircut = multiplier * max(floor_usd,
        pct * price)`` and ``short_ok = haircut <= price`` (a short leg's net
        price is nonnegative).

    Examples
    --------
    A liquid leg priced at 2 under a 1 percent tier with a 1 cent floor::

        terms = {"pct": .01, "floor_usd": .01}
        liquid_leg_haircut(2., 5, 1, {"min_trade_count": 5, "min_volume": 1}, terms, 1.)
        # -> (0.02, True)
    """
    if trade_count < liquidity["min_trade_count"] or volume < liquidity["min_volume"]:
        return None
    haircut = multiplier * max(tier_terms["floor_usd"], tier_terms["pct"] * price)
    return haircut, haircut <= price


def _mip_abs_gap(params):
    """One reader of ``solver_options.mip_abs_gap``; a non-dict block reads as unset."""
    options = params.get("solver_options")
    return options.get("mip_abs_gap") if isinstance(options, dict) else None


class RobustCondorSelect(PyomoSolve):
    """Select one all-strike robust condor from an already governed context.

    This narrow adapter accepts explicit per-leg eligibility and costs. It does
    not infer liquidity, dates, uncertainty calibration or execution policies.
    An empty eligible leg set gives the mathematically feasible no-trade choice.

    Parameters
    ----------
    params : dict
        Required multiplier, fee_per_contract_usd, tie_tolerance_usd,
        max_absolute_gap_usd and max_relative_gap (a number, or null = the
        relative gap is recorded in the certificate but never tested); optional
        base solver knobs. Optional ``objective_parity_usd`` (nonnegative):
        when present, each incumbent's robust value is recomputed by the
        independent primal owner and must agree within it, else a counted skip
        (``parity_failed`` before the tie solve, ``parity_failed_tie`` after;
        a primal owner that cannot price the structure skips ``primal_failed``),
        and the ordering ``solver_options.mip_abs_gap`` <= ``max_absolute_gap_usd``
        <= ``objective_parity_usd`` with ``tie_tolerance_usd`` >=
        ``max_absolute_gap_usd`` and ``objective_parity_usd`` >
        ``tie_tolerance_usd`` is enforced (an unset ``mip_abs_gap`` is not
        pinned). Structural guards then skip with their own reasons instead of
        raising. Absent, behaviour and config identity are the legacy ones.
        Input context contains grid/masses/spot/rho, optional q_lo/q_hi, identity
        fields, and legs mapping LP/SP/SC/LC to eligible contract dictionaries:
        index, price, haircut, contract_id. Prices and haircuts are USD/share.

    Examples
    --------
    Construct without choosing any calibration or market-data policy::

        node = RobustCondorSelect("select", {
            "multiplier": 100, "fee_per_contract_usd": .65,
            "tie_tolerance_usd": 1e-6, "max_absolute_gap_usd": 1e-7,
            "max_relative_gap": 1e-8})
    """

    role = "transform"
    outputs = ("decision", "evidence")
    _ROLES = ("LP", "SP", "SC", "LC")
    _IDENTITY = ("decision_id", "arm_id", "symbol", "quote_date", "expiry")
    _REQUIRED_NUMBERS = ("multiplier", "fee_per_contract_usd", "tie_tolerance_usd",
                         "max_absolute_gap_usd", "max_relative_gap")
    _PARAMS = PyomoSolve._PARAMS + _REQUIRED_NUMBERS + ("objective_parity_usd",)

    @classmethod
    def validate_params(cls, params):
        """Validate explicit solver/cost/tie limits without supplying policies.

        Parameters
        ----------
        params : dict
            Proposed node parameters.

        Returns
        -------
        list of str
            All parameter defects; an empty list means valid.
        """
        problems = super().validate_params(params)
        for key in cls._REQUIRED_NUMBERS:
            value = params.get(key)
            if key == "max_relative_gap" and key in params and value is None:
                continue
            if not number_ok(value) or value < 0 or (key == "multiplier" and value == 0):
                problems.append(f"{key} must be an explicit finite "
                                + ("positive" if key == "multiplier" else "nonnegative") + " number")
        if isinstance(params.get("multiplier"), bool) or not isinstance(params.get("multiplier"), int):
            problems.append("multiplier must be a positive integer")
        if "objective_parity_usd" in params:
            value = params["objective_parity_usd"]
            if not number_ok(value) or value < 0:
                problems.append("objective_parity_usd must be a finite nonnegative number")
            else:
                problems.extend(cls._ordering_problems(params))
        return problems

    @staticmethod
    def _ordering_problems(params):
        """Name each violated link of mip_abs_gap <= max_absolute_gap <= parity, tie >= max_absolute_gap, parity > tie."""
        problems = []
        gap, parity = params.get("max_absolute_gap_usd"), params["objective_parity_usd"]
        mip = _mip_abs_gap(params)
        if number_ok(gap):
            if number_ok(mip) and mip > gap:
                problems.append("ordering violated: solver_options.mip_abs_gap <= max_absolute_gap_usd")
            if gap > parity:
                problems.append("ordering violated: max_absolute_gap_usd <= objective_parity_usd")
            tie = params.get("tie_tolerance_usd")
            if number_ok(tie) and tie < gap:
                problems.append("ordering violated: tie_tolerance_usd >= max_absolute_gap_usd")
            # The tie solve leaves the dual variables anywhere above the tie
            # floor, so the traded structure's dual value can sit a full
            # tie_tolerance below its primal recompute (measured 1.00001e-6 in
            # the ADR-0256 dry run); a parity bound at or below it would skip
            # legitimate ties as parity_failed_tie.
            if number_ok(tie) and parity <= tie:
                problems.append("ordering violated: objective_parity_usd > tie_tolerance_usd")
        return problems

    def build_model(self, inputs, params):
        """Build the all-ordered-strike binary program with the shared dual.

        Parameters
        ----------
        inputs, params : dict
            One prepared context and the explicitly configured policies.

        Returns
        -------
        pyomo.environ.ConcreteModel
            One all-strike selection program, including no trade.

        Raises
        ------
        ValueError
            Invalid probabilities, bands, eligibility, costs or identities
            (raised as the private ``_ContextDataError`` subclass).
        """
        try:
            return self._build_checked(inputs, params)
        except ValueError as exc:
            raise _ContextDataError(str(exc)) from exc

    def _build_checked(self, inputs, params):
        """Validate one context and build its program (errors are context data defects)."""
        from pyomo.environ import Binary, ConcreteModel, Constraint, Expression, Objective, Var, maximize

        context = inputs.get("context")
        if not isinstance(context, dict):
            raise ValueError("context must be a mapping")
        for key in self._IDENTITY:
            if not isinstance(context.get(key), str) or not context[key]:
                raise ValueError(f"context requires {key}")
        if (not _iso_day(context["quote_date"]) or not _iso_day(context["expiry"])
                or context["quote_date"] >= context["expiry"]):
            raise ValueError("context requires canonical quote_date before expiry")
        dual = WassersteinDual(context["grid"], context["masses"], context["rho"],
                               context["spot"], q_lo=context.get("q_lo"), q_hi=context.get("q_hi"))
        grid = dual.distribution.grid
        if (grid <= 0).any():
            raise ValueError("equity strikes must be positive")
        if not isinstance(context.get("legs"), dict) or set(context["legs"]) != set(self._ROLES):
            raise ValueError("legs must declare LP/SP/SC/LC eligibility separately")
        contracts, identities = {}, {}
        for role in self._ROLES:
            rows = context["legs"][role]
            if not isinstance(rows, (list, tuple)):
                raise ValueError("eligible legs must be finite sequences")
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"index", "price", "haircut", "contract_id"}:
                    raise ValueError("eligible contract must carry index/price/haircut/contract_id")
                index = row["index"]
                if (isinstance(index, bool) or not isinstance(index, int)
                        or not 0 <= index < len(grid) or (role, index) in contracts):
                    raise ValueError("duplicate or invalid eligible strike index")
                if (not isinstance(row["contract_id"], str) or not row["contract_id"]
                        or any(not number_ok(row[k]) or row[k] < 0 for k in ("price", "haircut"))):
                    raise ValueError("invalid contract identity, price or haircut")
                if role in ("SP", "SC") and row["haircut"] > row["price"]:
                    raise ValueError("short-leg haircut exceeds price")
                identity = ("put" if role in ("LP", "SP") else "call", index)
                prior = identities.setdefault(row["contract_id"], identity)
                if prior != identity:
                    raise ValueError("contract identity has inconsistent right or strike")
                contracts[role, index] = dict(row)
        self._context, self._dual, self._contracts = dict(context), dual, contracts
        self._certificates, self._skip = [], None
        model = ConcreteModel()
        model.z = Var(domain=Binary)
        model.y = Var(self._ROLES, range(len(grid)), domain=Binary)
        for role in self._ROLES:
            for i in range(len(grid)):
                if (role, i) not in contracts:
                    model.y[role, i].fix(0)
        model.one = Constraint(self._ROLES, rule=lambda m, role:
                               sum(m.y[role, i] for i in range(len(grid))) == m.z)
        pairs = tuple(zip(self._ROLES[:-1], self._ROLES[1:]))
        model.order = Constraint(range(3), range(len(grid)), rule=lambda m, a, k:
                                 sum(m.y[pairs[a][0], i] for i in range(k, len(grid)))
                                 <= sum(m.y[pairs[a][1], i] for i in range(k+1, len(grid))))
        losses = [
            sum((model.y["SP", i]-model.y["LP", i]) * max(float(grid[i]-s), 0.)
                + (model.y["SC", i]-model.y["LC", i]) * max(float(s-grid[i]), 0.)
                for i in range(len(grid))) for s in grid]
        block = dual.attach(model, losses)
        credit = sum(((1 if role in ("SP", "SC") else -1) * row["price"] - row["haircut"])
                     * model.y[role, i] for (role, i), row in contracts.items())
        model.robust_value = Expression(expr=params["multiplier"] * (credit-block.cost)
                                       - 4 * params["fee_per_contract_usd"] * model.z)
        model.objective = Objective(expr=model.robust_value, sense=maximize)
        return model

    def _certified(self, record):
        """Require optimal termination and the absolute gap limit; the relative one unless null."""
        return (record.termination == "optimal" and record.objective is not None
                and record.bound is not None and record.gap is not None
                and abs(record.objective-record.bound) <= self.params["max_absolute_gap_usd"]
                and (self.params["max_relative_gap"] is None
                     or record.gap <= self.params["max_relative_gap"]))

    @staticmethod
    def _secondary_certified(record):
        """Prove a binary minimum from its incumbent and dimensionless lower bound."""
        if (record.termination != "optimal" or record.objective is None
                or record.bound is None):
            return False
        incumbent = round(record.objective)
        if (incumbent not in (0, 1)
                or abs(record.objective-incumbent) > BINDING_TOLERANCE
                or record.bound > incumbent+BINDING_TOLERANCE):
            return False
        # z is binary: z=0 is the domain lower bound. A z=1 incumbent
        # is optimal only if its certified lower bound excludes zero.
        return incumbent == 0 or record.bound > BINDING_TOLERANCE

    def _solve(self, solver, model):
        """Use the existing lifecycle twice, retaining primary and tie certificates."""
        import time
        from pyomo.environ import Constraint, Objective, minimize

        name = self.params.get("solver", DEFAULT_SOLVER)
        started = time.perf_counter()
        results = super()._solve(solver, model)
        primary = self._build_solve_record(solver, model, results, name, time.perf_counter()-started)
        self._certificates.append(primary.to_obj())
        if not self._certified(primary):
            self._skip = "uncertified_primary"
            return results
        if primary.bound-primary.objective > self.params["tie_tolerance_usd"]:
            self._skip = "primary_gap_exceeds_tie_tolerance"
            return results
        if primary.objective <= self.params["tie_tolerance_usd"] < primary.bound:
            self._skip = "uncertified_no_trade_tie"
            return results
        if self._parity_on() and not self._parity_gate(model, primary.objective,
                                                      self._certificates[-1], "parity_failed"):
            return results
        model.objective.deactivate()
        model.tie_floor = Constraint(expr=model.robust_value >= primary.bound-self.params["tie_tolerance_usd"])
        model.tie_objective = Objective(expr=model.z, sense=minimize)
        return super()._solve(solver, model)

    def extract(self, model, results):
        """Return a checked selection and both solver certificates.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            Solved selection program.
        results : object
            Solver results retained by the base lifecycle.

        Returns
        -------
        dict
            Decision and evidence; uncertified solves explicitly skip.

        Raises
        ------
        ValueError
            Selected legs or their independently recomputed value violate the model.
        """
        from pyomo.environ import value

        if self._skip is None:
            self._certificates.append(self.solve_record.to_obj())
            if not self._secondary_certified(self.solve_record):
                self._skip = "uncertified_tie"
        if self._skip is None and self._parity_on():
            self._parity_gate(model, value(model.robust_value), self._certificates[-1],
                              "parity_failed_tie")
        identity = {key:self._context[key] for key in self._IDENTITY}
        evidence = {"solves":self._certificates, "rho":self._dual.radius,
                    "multiplier":self.params["multiplier"], "decision_eligible":False,
                    "eligible_contracts":[dict(role=role, **row)
                                          for (role, _), row in self._contracts.items()]}
        if self._skip is None:
            legs = self._guarded_legs(model)
            if legs is not None:
                robust, worst = self._primal_value(legs)
                self._skip = self._floor_skip(robust)
        if self._skip:
            return {"decision":{**identity, "status":"skipped", "reason":self._skip},
                    "evidence":evidence}
        return {"decision":{**identity, "status":"trade" if legs else "no_trade",
                            "legs":legs, "multiplier":self.params["multiplier"],
                            "robust_value_usd":robust, "worst_loss_usd_per_share":worst},
                "evidence":evidence}

    def _parity_on(self):
        """Report whether ``objective_parity_usd`` is configured."""
        return "objective_parity_usd" in self.params

    def _guarded_legs(self, model):
        """Return the selected legs; a structural defect raises (legacy) or sets its own skip (parity mode)."""
        try:
            return self._selected_legs(model)
        except _IntegrityError as exc:
            if not self._parity_on():
                raise
            self._skip = exc.reason
            return None

    def _floor_skip(self, robust):
        """Refuse a traded structure below the certified tie floor: raise (legacy) or a skip reason."""
        floor = self._certificates[0]["bound"]-self.params["tie_tolerance_usd"]
        if robust >= floor-BINDING_TOLERANCE:
            return None
        if not self._parity_on():
            raise ValueError("selected primal value fails certified tie floor")
        return "tie_floor_failed"

    def _parity_gate(self, model, model_value, certificate, reason):
        """Recompute the incumbent through the primal owner; record it on ``certificate``; skip on a miss."""
        legs = self._guarded_legs(model)
        if legs is None:
            return False
        try:
            recomputed, _ = self._primal_value(legs)
        except ValueError as exc:
            certificate["parity"] = {"error": str(exc), "passed": False}
            self._skip = "primal_failed"
            return False
        tolerance = self.params["objective_parity_usd"]
        discrepancy = abs(recomputed-model_value)
        mip = _mip_abs_gap(self.params)
        # fail closed: a NaN discrepancy is not "within tolerance"
        passed = discrepancy <= tolerance
        certificate["parity"] = {
            "recomputed_usd": recomputed, "model_usd": model_value,
            "discrepancy_usd": discrepancy, "tolerance_usd": tolerance,
            "mip_abs_gap": mip if number_ok(mip) else "unset", "passed": passed}
        if not passed:
            self._skip = reason
            return False
        return True

    def _selected_legs(self, model):
        """Read the incumbent's legs off the solved model, refusing structural defects."""
        from pyomo.environ import value

        z = value(model.z)
        if abs(z-round(z)) > BINDING_TOLERANCE:
            raise _IntegrityError("nonintegral_solution", "nonintegral trade solution")
        legs, indices = [], []
        for role in self._ROLES:
            selected = [i for i in range(len(self._dual.distribution.grid))
                        if value(model.y[role, i]) > .5]
            if len(selected) != round(z):
                raise _IntegrityError("invalid_leg_count", "invalid leg count")
            for i in selected:
                row = self._contracts[role, i]
                short = role in ("SP", "SC")
                indices.append(i)
                legs.append(dict(role=role, contract=row["contract_id"],
                                 right="put" if role in ("LP","SP") else "call",
                                 strike=float(self._dual.distribution.grid[i]),
                                 side="sell" if short else "buy",
                                 price_usd_per_share=row["price"] + (-1 if short else 1)*row["haircut"],
                                 fee_usd=self.params["fee_per_contract_usd"]))
        if indices and not all(a < b for a,b in zip(indices,indices[1:])):
            raise _IntegrityError("unordered_strikes", "strikes are not strictly ordered")
        return legs

    def _primal_value(self, legs):
        """Robust USD value and worst loss of ``legs`` through the independent primal owner."""
        credit = sum((1 if leg["side"] == "sell" else -1)*leg["price_usd_per_share"] for leg in legs)
        grid = self._dual.distribution.grid
        losses = [sum((1 if leg["side"] == "sell" else -1)
                      * (max(leg["strike"]-s,0.) if leg["right"] == "put" else max(s-leg["strike"],0.))
                      for leg in legs) for s in grid]
        bands = self._dual.bands
        worst = self._dual.distribution.worst_expected_loss(
            losses, self._dual.radius, self._dual.scale,
            q_lo=None if bands is None else bands[0], q_hi=None if bands is None else bands[1])
        return self.params["multiplier"]*(credit-worst)-sum(leg["fee_usd"] for leg in legs), worst

class RobustCondorBatchSelect(RobustCondorSelect):
    """Prepare governed chain rows for the one-context robust selector.

    The adapter owns no optimization.  It freezes calibration-window ticker
    liquidity tiers, resolves one named arm, and delegates each prepared context
    to :class:`RobustCondorSelect`.  Tied ticker medians sort by symbol before
    assigning rank terciles; this rule makes the frozen entry-price tier stable.
    """

    outputs = ("selections", "decisions", "solves", "skips", "evidence")
    _ARTIFACT_KEYS = ("fit_identity", "checkpoint_identity", "input_identity", "source_identity")
    _ARM_RULES = {
        "nominal": {"radius": "zero"},
        "rho_base": {"radius": "calibrated"},
        "haircut_0": {"radius": "calibrated", "haircut_multiplier": 0},
        "haircut_half": {"radius": "calibrated", "haircut_multiplier": .5},
        "haircut_double": {"radius": "calibrated", "haircut_multiplier": 2},
        "liquidity_3": {"radius": "calibrated", "min_trade_count": 3},
        "liquidity_10": {"radius": "calibrated", "min_trade_count": 10},
        "liquidity_20": {"radius": "calibrated", "min_trade_count": 20},
    }
    _ARMS = tuple(_ARM_RULES)
    _BATCH_PARAMS = ("calibration_window", "entry_window", "protected_end_before",
                     "standard_multiplier", "liquidity", "haircut_tiers", "arms",
                     "tier_tie_policy")
    _PARAMS = RobustCondorSelect._PARAMS + _BATCH_PARAMS

    @classmethod
    def validate_params(cls, params):
        """Return solver, chronology and declared-arm policy problems."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems.extend(RobustCondorSelect.validate_params({
            key: params[key] for key in RobustCondorSelect._PARAMS if key in params}))
        for key in cls._BATCH_PARAMS:
            if key not in params:
                problems.append(f"{key} is required")
        for key in ("calibration_window", "entry_window"):
            window = params.get(key)
            if (not isinstance(window, dict) or set(window) != {"start", "end"}
                    or not _iso_day(window.get("start")) or not _iso_day(window.get("end"))
                    or window["start"] > window["end"]):
                problems.append(f"{key} must be an ordered ISO start/end mapping")
        windows = [params.get(key, {}) for key in ("calibration_window", "entry_window")]
        if all(isinstance(w, dict) and _iso_day(w.get("start"))
               and _iso_day(w.get("end")) for w in windows):
            if windows[0]["end"] >= windows[1]["start"]:
                problems.append("calibration must finish strictly before entry")
            if (_iso_day(params.get("protected_end_before"))
                    and windows[1]["end"] >= params["protected_end_before"]):
                problems.append("entry window reaches protected boundary")
        if params.get("standard_multiplier") != params.get("multiplier"):
            problems.append("standard and solver multipliers must agree")
        if not _iso_day(params.get("protected_end_before")):
            problems.append("protected_end_before must be an ISO date")
        for key in ("standard_multiplier",):
            if type(params.get(key)) is not int or params[key] < 1:
                problems.append(f"{key} must be a positive integer")
        liquidity = params.get("liquidity")
        if (not isinstance(liquidity, dict) or set(liquidity) != {"min_trade_count", "min_volume"}
                or any(type(liquidity[k]) is not int or liquidity[k] < 0 for k in liquidity)):
            problems.append("liquidity must contain nonnegative integer min_trade_count/min_volume")
        tiers = params.get("haircut_tiers")
        if not isinstance(tiers, dict) or set(tiers) != {"low", "mid", "high"}:
            problems.append("haircut_tiers must contain low/mid/high")
        elif any(not isinstance(v, dict) or set(v) != {"pct", "floor_usd"}
                  or any(not number_ok(v[k]) or v[k] < 0 for k in v) for v in tiers.values()):
            problems.append("haircut tier values must be finite nonnegative pct/floor_usd")
        if params.get("tier_tie_policy") != "symbol_ascending_rank":
            problems.append("tier_tie_policy must be symbol_ascending_rank")
        arms = params.get("arms")
        if not isinstance(arms, dict) or tuple(arms) != cls._ARMS:
            problems.append("arms must declare the eight canonical arms in canonical order")
        elif any(not isinstance(spec, dict) or set(spec) - {"radius", "haircut_multiplier",
                                                            "min_trade_count"}
                  or spec.get("radius") not in {"zero", "calibrated"}
                  or ("haircut_multiplier" in spec
                      and (not number_ok(spec["haircut_multiplier"])
                           or spec["haircut_multiplier"] < 0))
                  or ("min_trade_count" in spec
                      and (type(spec["min_trade_count"]) is not int
                           or spec["min_trade_count"] < 0))
                  for spec in arms.values()):
            problems.append("arm definitions contain an invalid one-factor override")
        if isinstance(arms, dict) and arms != cls._ARM_RULES:
            problems.append("canonical arms must match their named one-factor definitions")
        return problems

    @staticmethod
    def _day(row):
        value = row.get("quote_date", row.get("date"))
        return value if _iso_day(value) else None

    def _tiers(self, chain):
        from statistics import median

        values = {}
        window = self.params["calibration_window"]
        for row in chain:
            day = self._day(row)
            symbol = row.get("symbol") if isinstance(row, dict) else None
            count = row.get("trade_count") if isinstance(row, dict) else None
            if (isinstance(symbol, str) and symbol and window["start"] <= (day or "") <= window["end"]
                    and type(count) is int and count >= 0):
                values.setdefault(symbol, []).append(count)
        ranked = sorted((median(counts), symbol)
                        for symbol, counts in values.items())
        return {symbol: ("low", "mid", "high")[(3 * rank)//len(ranked)]
                for rank, (_, symbol) in enumerate(ranked)}
    def _mass_rows(self, rows):
        out = {}
        identity_keys = self._ARTIFACT_KEYS
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("projected masses must be records")
            key = tuple(row.get(k) for k in ("symbol", "quote_date", "expiry"))
            if (any(not isinstance(v, str) or not v for v in key)
                    or any(not isinstance(row.get(k), str) or not row[k] for k in identity_keys)
                    or not _iso_day(key[1]) or not _iso_day(key[2]) or key[1] >= key[2]
                    or not _iso_day(row.get("settlement_date"))
                    or not key[1] < row["settlement_date"] <= key[2]
                    or key in out or not isinstance(row.get("grid"), (list, tuple))
                    or not isinstance(row.get("masses"), (list, tuple))):
                raise ValueError("projected masses require canonical context and artifact identities")
            decision_id = row.get("decision_id") or value_hash({k: row[k] for k in ("symbol", "quote_date", "expiry", "grid", "masses", "spot") + identity_keys})
            out[key] = {**row, "decision_id": decision_id}
        return out

    @staticmethod
    def _rho(rho):
        if not isinstance(rho, dict):
            raise ValueError("rho must be a YYYY-MM to finite nonnegative number/null mapping")
        for month, value in rho.items():
            if (not isinstance(month, str) or len(month) != 7 or not _iso_day(month+"-01")
                    or (value is not None and (not number_ok(value) or value < 0))):
                raise ValueError("rho must be a YYYY-MM to finite nonnegative number/null mapping")
        return dict(rho)

    def _arm_terms(self, arm, tier):
        """Return the arm's liquidity thresholds, its ticker tier's haircut terms and multiplier."""
        spec, liquidity = self.params["arms"][arm], dict(self.params["liquidity"])
        liquidity["min_trade_count"] = spec.get("min_trade_count", liquidity["min_trade_count"])
        return liquidity, self.params["haircut_tiers"][tier], spec.get("haircut_multiplier", 1.)

    def _batch_context(self, mass, rows, tier, arm, rho):
        liquidity, tier_terms, arm_multiplier = self._arm_terms(arm, tier)
        by_role = {role: [] for role in self._ROLES}
        grid = list(mass["grid"])
        index_by_strike = {float(strike): index for index, strike in enumerate(grid)}
        if len(index_by_strike) != len(grid):
            raise ValueError("projected grid repeats a strike")
        for row in rows:
            right = row.get("right", row.get("type"))
            if right not in ("put", "call") or not isinstance(row.get("contract"), str) or not row["contract"]:
                raise ValueError("chain rows require contract and put/call right")
            strike, price = row.get("strike"), row.get("vwap")
            if (not number_ok(strike) or float(strike) not in index_by_strike
                    or not number_ok(price) or price < 0
                    or row.get("multiplier") != self.params["standard_multiplier"]
                    or type(row.get("trade_count")) is not int or row["trade_count"] < 0
                    or type(row.get("volume")) is not int or row["volume"] < 0):
                raise ValueError("chain row has invalid strike, VWAP or liquidity")
            terms = liquid_leg_haircut(price, row["trade_count"], row["volume"], liquidity,
                                       tier_terms, arm_multiplier)
            if terms is None:
                continue
            haircut, short_ok = terms
            side_roles = ("LP",) if right == "put" else ("LC",)
            if short_ok:
                side_roles += (("SP",) if right == "put" else ("SC",))
            for selected in side_roles:
                by_role[selected].append(dict(index=index_by_strike[float(strike)], price=float(price),
                                               haircut=float(haircut), contract_id=row["contract"]))
        return dict(decision_id=mass["decision_id"], arm_id=arm, symbol=mass["symbol"],
                    quote_date=mass["quote_date"], expiry=mass["expiry"], grid=grid,
                    masses=list(mass["masses"]), spot=mass["spot"], rho=rho, legs=by_role,
                    **({k: mass[k] for k in ("q_lo", "q_hi") if k in mass}))

    def _select_one(self, ctx, mass, rows, tier, arm, rho):
        """Prepare and solve one arm's context; data defects surface as ``_BatchContextError``."""
        try:
            context = self._batch_context(mass, rows, tier, arm, rho)
        except ValueError as exc:
            raise _BatchContextError(str(exc)) from exc
        return RobustCondorSelect.run(self, ctx, {"context": context})


    _FILL_KEYS = ("date", "vwap", "trade_count", "volume")

    def _fill_bar(self, row, day):
        """Return a chain row's ``fill`` bar after refusing a malformed or non-later one."""
        fill = row["fill"]
        if fill is None:
            return None
        if (not isinstance(fill, dict) or set(fill) != set(self._FILL_KEYS)
                or not _iso_day(fill["date"]) or not number_ok(fill["vwap"]) or fill["vwap"] < 0
                or any(type(fill[k]) is not int or fill[k] < 0 for k in ("trade_count", "volume"))):
            raise ValueError("chain fill must be null or a date/vwap/trade_count/volume bar")
        if not day < fill["date"] < row["expiry"]:
            raise ValueError("chain fill must fall strictly after the decision date and before expiry")
        return fill

    def _fill_session(self, rows, day):
        """Return t', the first session with any bar after the decision date, in one context."""
        dates = [bar["date"] for bar in (self._fill_bar(r, day) for r in rows) if bar is not None]
        return min(dates) if dates else None

    @staticmethod
    def _credit(legs, key, multiplier):
        """Return the one-lot USD credit of ``legs`` at the per-share price under ``key``."""
        return multiplier * sum((1 if leg["side"] == "sell" else -1) * leg[key] for leg in legs)

    def _fill_prices(self, decision, rows, tier):
        """Return ``(prices, None)`` repriced at t' with the arm's rule, or ``(None, reason)``."""
        liquidity, tier_terms, factor = self._arm_terms(decision["arm_id"], tier)
        session = self._fill_session(rows, decision["quote_date"])
        by_contract = {row["contract"]: row for row in rows}
        prices = []
        for leg in decision["legs"]:
            bar = by_contract[leg["contract"]]["fill"]
            if bar is None or bar["date"] != session:
                return None, "missing_fill_bar"
            terms = liquid_leg_haircut(bar["vwap"], bar["trade_count"], bar["volume"],
                                       liquidity, tier_terms, factor)
            if terms is None:
                return None, "fill_liquidity"
            haircut, short_ok = terms
            if leg["side"] == "sell" and not short_ok:
                return None, "negative_net_short_price"
            prices.append(bar["vwap"] + (-haircut if leg["side"] == "sell" else haircut))
        return prices, None

    def _fill(self, decision, rows, tier):
        """Fill one trade decision all-or-none at t' or mark it ``unfilled``; return the reason or ``None``."""
        multiplier = decision["multiplier"]
        decision["decision_credit_usd"] = self._credit(decision["legs"], "price_usd_per_share",
                                                      multiplier)
        prices, reason = self._fill_prices(decision, rows, tier)
        if reason is not None:
            decision.update(status="unfilled", unfilled_reason=reason)
            return reason
        session = self._fill_session(rows, decision["quote_date"])
        for leg, price in zip(decision["legs"], prices):
            leg["decision_price_usd_per_share"], leg["price_usd_per_share"] = (
                leg["price_usd_per_share"], price)
        decision["fill_date"] = session
        decision["remaining_dte"] = (date.fromisoformat(decision["expiry"])
                                     - date.fromisoformat(session)).days
        decision["fill_credit_usd"] = self._credit(decision["legs"], "price_usd_per_share",
                                                   multiplier)
        decision["slippage_usd"] = decision["decision_credit_usd"] - decision["fill_credit_usd"]
        return None

    def run(self, ctx, inputs):
        """Return decisions, settlement-compatible selections, and explicit skips.

        With ``objective_parity_usd`` configured, a data defect in one context's
        rows or model becomes a counted ``context_error`` skip (reason text kept
        in ``detail``) and the batch completes; without it the defect aborts, as
        before. Solver-integrity guards never become ``context_error``.
        """
        if not isinstance(inputs, dict) or set(inputs) != {
                "chain", "projected_masses", "rho", "skips", "holding_exclusions"}:
            raise ValueError("inputs require chain/projected_masses/rho/skips/holding_exclusions")
        if any(not isinstance(inputs[key], list) for key in ("chain", "projected_masses", "skips", "holding_exclusions")):
            raise ValueError("batch inputs must be lists")
        if any(not isinstance(row, dict) for key in ("chain", "skips")
               for row in inputs[key]):
            raise ValueError("chain and upstream skips must be records")
        tiers, masses, rhos = self._tiers(inputs["chain"]), self._mass_rows(inputs["projected_masses"]), self._rho(inputs["rho"])
        excluded = set()
        for row in inputs["holding_exclusions"]:
            if not isinstance(row, dict) or not isinstance(row.get("reason"), str) or not row["reason"]:
                raise ValueError("holding exclusions require an explicit reason")
            key = tuple(row.get(k) for k in ("symbol", "quote_date", "expiry"))
            if any(not isinstance(v, str) or not v for v in key):
                raise ValueError("holding exclusions require canonical context identity")
            excluded.add(key)
        chain = {}
        present = {"fill" in row for row in inputs["chain"]}
        if len(present) > 1:
            raise ValueError("chain fill must be present on every chain row or on none")
        filling = present == {True}
        fill_counts = Counter()
        for row in inputs["chain"]:
            if not isinstance(row, dict):
                raise ValueError("chain rows must be records")
            day = self._day(row)
            key = (row.get("symbol"), day, row.get("expiry"))
            if (any(not isinstance(v, str) or not v for v in key) or not _iso_day(row["expiry"])
                    or day >= row["expiry"] or row["expiry"] >= self.params["protected_end_before"]
                    or key in chain and any(r.get("contract") == row.get("contract") for r in chain[key])):
                raise ValueError("chain has duplicate or unprotected context identity")
            if filling:
                self._fill_bar(row, day)
            chain.setdefault(key, []).append(row)
        decisions, selections, solves, skips = [], [], [], list(inputs["skips"])
        for mass in masses.values():
            key = (mass["symbol"], mass["quote_date"], mass["expiry"])
            if not self.params["entry_window"]["start"] <= mass["quote_date"] <= self.params["entry_window"]["end"]:
                continue
            if mass["expiry"] >= self.params["protected_end_before"] or key in excluded:
                skips.append({**{k: mass[k] for k in ("decision_id", "symbol", "quote_date", "expiry") + self._ARTIFACT_KEYS},
                              "reason": "holding_excluded" if key in excluded else "protected_date"})
                continue
            if key not in chain:
                skips.append({**{k: mass[k] for k in ("decision_id", "symbol", "quote_date", "expiry") + self._ARTIFACT_KEYS},
                              "reason": "missing_chain"})
                continue
            if mass["symbol"] not in tiers:
                identity = {k: mass[k] for k in
                            ("decision_id", "symbol", "quote_date", "expiry") + self._ARTIFACT_KEYS}
                skips.extend({**identity, "arm_id": arm, "reason": "missing_calibration_tier"}
                             for arm in self._ARMS)
                continue
            for arm in self._ARMS:
                rho = 0. if self.params["arms"][arm]["radius"] == "zero" else rhos.get(mass["quote_date"][:7])
                identity = {k: mass[k] for k in ("decision_id", "symbol", "quote_date", "expiry") + self._ARTIFACT_KEYS}
                if rho is None:
                    skips.append({**identity, "arm_id": arm, "reason": "missing_calibrated_rho"})
                    continue
                try:
                    result = self._select_one(ctx, mass, chain[key], tiers[mass["symbol"]], arm, rho)
                except (_ContextDataError, _BatchContextError) as exc:
                    if not self._parity_on():
                        raise
                    skips.append({**identity, "arm_id": arm, "reason": "context_error",
                                  "detail": str(exc)})
                    continue
                decision = result["decision"]
                decision["forecast_settlement_date"] = mass["settlement_date"]
                decision.update({name: mass[name] for name in ("fit_identity", "checkpoint_identity", "input_identity", "source_identity")})
                decisions.append(decision)
                solves.extend({**identity, "arm_id": arm, **solve}
                              for solve in result["evidence"]["solves"])
                if decision["status"] == "trade":
                    if filling:
                        fill_counts[self._fill(decision, chain[key], tiers[mass["symbol"]])] += 1
                    selections.append(decision)
                elif decision["status"] == "skipped":
                    skips.append(decision)
        evidence = {"tiers": tiers, "decisions": decisions, "solves": solves, "skips": skips,
                    "tier_tie_policy": self.params["tier_tie_policy"]}
        if filling:
            evidence["fill"] = {"filled": fill_counts.pop(None, 0),
                                "unfilled": dict(sorted(fill_counts.items()))}
        return {"selections": selections, "decisions": decisions, "solves": solves,
                "skips": skips, "evidence": evidence}

class ExactDteBarChain(Node):
    """Apply exact equity-option tenor and phase rules to already bounded rows.

    This is a pure child adapter. It never opens sources, deduplicates conflicting
    vintages, infers missing contract multipliers or certifies adjustment basis.
    Upstream intake must establish those source and corporate-action facts.

    Parameters
    ----------
    params : dict
        Explicit fields mapping, positive dte and standard multiplier,
        exclusive end_before date and disjoint named inclusive windows. Optional
        ``fill_session`` (``"same"``, the default and the legacy identity, or
        ``"next"``): with ``"next"`` the node also takes a ``fills`` input of bar rows
        under the SAME field mapping, in any date range, and attaches to each
        eligible row ``fill``, the bar of the same contract on the first date
        strictly after the row's date and strictly before its expiry
        (``{date, vwap, trade_count, volume}``), or ``None`` when there is none.
        A fill row at or after ``end_before``, a malformed or duplicated one, or one
        that disagrees with the chain row on symbol, expiry, right or strike refuses.
        ``"next"`` without the ``fills`` input, or ``"same"`` with it, refuses.

    Examples
    --------
    Use the same external field names as the canonical contract::

        fields = {key: key for key in ExactDteBarChain.FIELDS}
        node = ExactDteBarChain("chain", {
            "fields": fields, "dte": 31, "multiplier": 100,
            "end_before": "2026-01-01",
            "windows": [{"name": "entry", "start": "2025-02-04",
                         "end": "2025-11-28"}]})
    """

    role = "transform"
    outputs = ("records", "skips", "evidence")
    _PARAMS = ("fields", "dte", "multiplier", "end_before", "windows", "fill_session")
    #: The fill clocks; the first is the default, the legacy same-bar clock.
    FILL_SESSIONS = ("same", "next")
    DEFAULT_FILL_SESSION = FILL_SESSIONS[0]
    FIELDS = ("contract", "symbol", "expiry", "right", "strike", "date",
              "vwap", "trade_count", "volume", "multiplier")

    @classmethod
    def validate_params(cls, params):
        """Accumulate field, tenor and phase-policy problems.

        Parameters
        ----------
        params : dict
            Explicit adapter policy.

        Returns
        -------
        list of str
            All malformed declaration problems.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        fields = params.get("fields")
        if (not isinstance(fields, dict) or set(fields) != set(cls.FIELDS)
                or any(not isinstance(v, str) or not v for v in fields.values())
                or len(set(fields.values())) != len(fields)):
            problems.append("fields must map every canonical field uniquely")
        for key in ("dte", "multiplier"):
            check_int_param(problems, key, params.get(key), ge=1)
        if not _iso_day(params.get("end_before")):
            problems.append("end_before requires canonical date")
        if "fill_session" in params and params["fill_session"] not in cls.FILL_SESSIONS:
            problems.append(f"fill_session must be one of {list(cls.FILL_SESSIONS)}")
        windows = params.get("windows")
        if not isinstance(windows, list) or not windows:
            return problems + ["windows must be a nonempty list"]
        valid = []
        for window in windows:
            if (not isinstance(window, dict) or set(window) != {"name", "start", "end"}
                    or not isinstance(window.get("name"), str) or not window["name"]
                    or not _iso_day(window.get("start")) or not _iso_day(window.get("end"))
                    or window["start"] > window["end"]):
                problems.append("invalid named window")
            else:
                valid.append(window)
                if _iso_day(params.get("end_before")) and window["end"] >= params["end_before"]:
                    problems.append("window reaches protected boundary")
        if len({w["name"] for w in valid}) != len(valid):
            problems.append("window names must be unique")
        ordered = sorted(valid, key=lambda w: w["start"])
        if any(a["end"] >= b["start"] for a, b in zip(ordered, ordered[1:])):
            problems.append("windows must not overlap")
        return problems

    def _project(self, row):
        """Return one canonical eligible record or raise its precise refusal."""
        fields = self.params["fields"]
        values = {key: row[field] for key, field in fields.items()}
        for key in ("contract", "symbol"):
            if not isinstance(values[key], str) or not values[key]:
                raise ValueError(f"missing {key}")
        day, expiry = values["date"], values["expiry"]
        if not _iso_day(day) or not _iso_day(expiry):
            raise ValueError("invalid quote_date or expiry")
        if max(day, expiry) >= self.params["end_before"]:
            raise ValueError("protected date boundary")
        if (date.fromisoformat(expiry)-date.fromisoformat(day)).days != self.params["dte"]:
            raise ValueError("ineligible exact dte")
        phase = next((w["name"] for w in self.params["windows"]
                      if w["start"] <= day <= w["end"]), None)
        if phase is None:
            raise ValueError("outside declared phase window")
        if values["right"] not in ("put", "call"):
            raise ValueError("unsupported right")
        for key in ("strike", "vwap"):
            if not price_ok(values[key]):
                raise ValueError(f"invalid {key}")
        for key in ("trade_count", "volume"):
            if type(values[key]) is not int or values[key] < 0:
                raise ValueError(f"invalid {key}")
        if type(values["multiplier"]) is not int or values["multiplier"] != self.params["multiplier"]:
            raise ValueError("unsupported contract multiplier")
        return {**row, **values, "quote_date": day, "type": values["right"],
                "phase": phase, "calendar_dte": self.params["dte"]}

    def run(self, ctx, inputs):
        """Apply the declared domain filter and retain each rejected identity.

        Parameters
        ----------
        ctx : NodeContext
            Pipeline context (unused).
        inputs : dict
            Finite records already selected by protected intake.

        Returns
        -------
        dict
            Eligible canonical records, explicit skips and aggregate counts.

        Raises
        ------
        ValueError
            Input shape or duplicate contract-date identities are ambiguous, or the
            ``fills`` input contradicts ``fill_session`` or is malformed.
        """
        rows = inputs["records"]
        if not isinstance(rows, (list, tuple)) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("records must be a finite sequence of mappings")
        nxt = self.params.get("fill_session", self.DEFAULT_FILL_SESSION) == "next"
        if nxt != ("fills" in inputs):
            raise ValueError("the fills input is required by fill_session next and "
                             "refused by fill_session same")
        fills = self._fill_index(inputs["fills"]) if nxt else None
        fields, seen = self.params["fields"], set()
        for row in rows:
            identity = (row.get(fields["contract"]), row.get(fields["date"]))
            if all(isinstance(v, str) and v for v in identity):
                if identity in seen:
                    raise ValueError("duplicate contract quote_date")
                seen.add(identity)
        records, skips = [], []
        for row in rows:
            try:
                records.append(self._project(row))
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                skips.append({key: row.get(fields[key])
                              for key in ("contract", "symbol", "date", "expiry")}
                             | {"reason": str(exc)})
        evidence = {"input_rows": len(rows), "eligible_rows": len(records), "skips": skips}
        if nxt:
            records = [{**record, "fill": self._fill_for(fills, record)} for record in records]
            attached = sum(record["fill"] is not None for record in records)
            evidence.update(fill_session="next", fill_rows=sum(map(len, fills.values())),
                            fills_attached=attached, fills_missing=len(records) - attached)
        return {"records": records, "skips": skips, "evidence": JsonArtifact(evidence)}

    def _fill_row(self, row):
        """Return one validated fill bar (canonical keys) or raise its precise refusal."""
        if not isinstance(row, dict):
            raise ValueError("fills must be a finite sequence of mappings")
        try:
            values = {key: row[field] for key, field in self.params["fields"].items()}
        except KeyError as exc:
            raise ValueError(f"fill row lacks field {exc}") from None
        for key in ("contract", "symbol"):
            if not isinstance(values[key], str) or not values[key]:
                raise ValueError(f"fill row missing {key}")
        if not _iso_day(values["date"]) or not _iso_day(values["expiry"]):
            raise ValueError("fill row has an invalid date or expiry")
        if max(values["date"], values["expiry"]) >= self.params["end_before"]:
            raise ValueError("fill row reaches the protected end_before boundary")
        if values["right"] not in ("put", "call"):
            raise ValueError("fill row has an unsupported right")
        if not price_ok(values["strike"]) or not price_ok(values["vwap"]):
            raise ValueError("fill row has an invalid strike or vwap")
        for key in ("trade_count", "volume"):
            if type(values[key]) is not int or values[key] < 0:
                raise ValueError(f"fill row has an invalid {key}")
        if type(values["multiplier"]) is not int or values["multiplier"] != self.params["multiplier"]:
            raise ValueError("fill row has an unsupported contract multiplier")
        return values

    def _fill_index(self, rows):
        """Return ``{contract: [bar, ...]}`` sorted by date, refusing a repeated contract/date."""
        if not isinstance(rows, (list, tuple)):
            raise ValueError("fills must be a finite sequence of mappings")
        index, seen = {}, set()
        for row in rows:
            bar = self._fill_row(row)
            if (bar["contract"], bar["date"]) in seen:
                raise ValueError("duplicate fill contract date")
            seen.add((bar["contract"], bar["date"]))
            index.setdefault(bar["contract"], []).append(bar)
        for bars in index.values():
            bars.sort(key=lambda bar: bar["date"])
        return index

    @staticmethod
    def _fill_for(index, record):
        """Return the contract's first bar strictly after the record's date and before its expiry."""
        bars = index.get(record["contract"], [])
        at = bisect_right([bar["date"] for bar in bars], record["quote_date"])
        if at == len(bars) or bars[at]["date"] >= record["expiry"]:
            return None
        bar = bars[at]
        if (bar["symbol"], bar["expiry"], bar["right"], float(bar["strike"])) != (
                record["symbol"], record["expiry"], record["right"], float(record["strike"])):
            raise ValueError("fill row disagrees with its contract's symbol, expiry, right or strike")
        return {key: bar[key] for key in ("date", "vwap", "trade_count", "volume")}

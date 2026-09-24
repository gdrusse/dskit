"""Thin, non-serving Nodes binding condor diagnostics and the proxy backtest."""

import math
from collections import Counter
from statistics import NormalDist

from dskit.pipeline.distribution_models import REFERENCE_SCALE_FIELD
from dskit.pipeline.distribution_scores import (
    DEFAULT_OUTCOME_FIELD,
    DEFAULT_SAMPLES_FIELD,
    forecast_pair,
    row_in_split,
)
from dskit.pipeline.node import JsonArtifact, Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok
from dskit.pipeline.split_policy import SPLIT_NAMES

from .contracts import (
    CashIndexContract,
    DefinedRiskCondor,
    _IndexQuote,
    _IndexSettlement,
    _day,
    _decimal,
    _instant,
    _integer,
    _text,
)
from .distribution import _LEGS, CondorGeometry, condor_payoff, tail_mean
from .pricing import VixProxyQuotes

__all__ = ["CondorBacktest", "CondorDistributionReport", "CondorPayoffDiagnostic"]


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


def _max_drawdown(pnls):
    """Return the largest peak-to-trough fall of the cumulative P&L, baseline 0."""
    total = peak = worst = 0.0
    for pnl in pnls:
        total += pnl
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst


class CondorBacktest(Node):
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
    at entry only (cash settlement). A condor whose rounded strikes are not
    strictly increasing, or whose credit is not positive, is skipped and
    counted. Role ``score``; ``decision_eligible`` is false while pricing
    is the proxy.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Required: ``split``, ``short_q`` (in (0, 0.5)), one of
        ``wing_points`` / ``wing_z`` (positive), ``strike_increment``
        (positive), ``multiplier`` (int >= 1), ``fee_per_leg`` (>= 0) and
        the :class:`~index_options.pricing.VixProxyQuotes` knobs
        ``atm_ratio``, ``put_skew_per_z``, ``call_skew_per_z``,
        ``smile_curvature``, ``iv_floor``, ``half_spread_min``,
        ``half_spread_frac``. Optional: ``hold_steps`` (21),
        ``trading_days_per_year`` (252; ``T = hold_steps / it``),
        ``min_edge_usd`` (0), ``cvar_alpha`` (0.95), ``rate`` (0).

    Examples
    --------
    Ten-percent shorts, wings half a reference SD further out::

        node = CondorBacktest("backtest", {
            "split": "val", "short_q": 0.1, "wing_z": 0.5, "strike_increment": 5,
            "multiplier": 100, "fee_per_leg": 0.65, "atm_ratio": 0.794,
            "put_skew_per_z": 0.176, "call_skew_per_z": -0.064, "smile_curvature": 0.045,
            "iv_floor": 0.05, "half_spread_min": 0.20, "half_spread_frac": 0.005,
        })
        out = node.run(ctx, {"forecasts": rows})
        out["metrics"]["model_total_pnl_usd"]
    """

    role = "score"
    outputs = ("metrics", "report")
    BOOKS = ("model", "always", "implied")
    #: Why an in-split row cannot be an entry, in the order they are checked.
    ROW_REASONS = ("no_outcome", "no_forecast", "no_forward", "no_scale", "no_iv")
    #: Why a book skips an entry.
    BOOK_REASONS = ("degenerate_strikes", "nonpositive_credit", "below_min_edge")
    DEFAULTS = {"hold_steps": 21, "trading_days_per_year": 252, "min_edge_usd": 0.0,
                "cvar_alpha": 0.95, "rate": 0.0}
    _REQUIRED = ("split", "short_q", "strike_increment", "multiplier", "fee_per_leg",
                 "atm_ratio", "put_skew_per_z", "call_skew_per_z", "smile_curvature",
                 "iv_floor", "half_spread_min", "half_spread_frac")
    _WINGS = ("wing_points", "wing_z")
    _PARAMS = tuple(sorted(_REQUIRED + _WINGS + tuple(DEFAULTS)))
    FORWARD_FIELD = "close"
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
        if params.get("split") not in SPLIT_NAMES:
            problems.append(f"split must name one of {list(SPLIT_NAMES)}")
        q = params.get("short_q")
        if not number_ok(q) or not 0 < q < 0.5:
            problems.append(f"short_q must be in (0, 0.5), got {q!r}")
        wings = [k for k in cls._WINGS if k in params]
        if len(wings) != 1:
            problems.append(f"declare exactly one of {list(cls._WINGS)}, got {wings}")
        for knob in wings + ["strike_increment"]:
            if not price_ok(params.get(knob)):
                problems.append(f"{knob} must be a positive number, got {params.get(knob)!r}")
        check_int_param(problems, "multiplier", params.get("multiplier"), ge=1)
        for knob in ("hold_steps", "trading_days_per_year"):
            check_int_param(problems, knob, params.get(knob, cls.DEFAULTS[knob]), ge=1)
        fee = params.get("fee_per_leg")
        if not number_ok(fee) or fee < 0:
            problems.append(f"fee_per_leg must be a nonnegative number, got {fee!r}")
        edge = params.get("min_edge_usd", cls.DEFAULTS["min_edge_usd"])
        if not number_ok(edge):
            problems.append(f"min_edge_usd must be a finite number, got {edge!r}")
        alpha = params.get("cvar_alpha", cls.DEFAULTS["cvar_alpha"])
        if not number_ok(alpha) or not 0 < alpha < 1:
            problems.append(f"cvar_alpha must be in (0, 1), got {alpha!r}")
        problems.extend(VixProxyQuotes.problems(
            *(params.get(k, cls.DEFAULTS.get(k)) for k in VixProxyQuotes.KNOBS)))
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

    def _knob(self, name):
        """Return a declared knob or its default."""
        return self.params.get(name, self.DEFAULTS.get(name))

    def _row_reason(self, row):
        """Say why a scorable forecast row cannot be priced, or ``None``."""
        if not price_ok(row.get(self.FORWARD_FIELD)):
            return "no_forward"
        if not price_ok(row.get(REFERENCE_SCALE_FIELD)):
            return "no_scale"
        if not price_ok(row.get(self.IV_FIELD)):
            return "no_iv"
        return None

    def _entries(self, ctx, rows):
        """Pick non-overlapping entry rows per instrument, counting skips."""
        hold = int(self._knob("hold_steps"))
        by_instrument = {}
        for row in rows:
            if row_in_split(ctx, row, self.params["split"]):
                by_instrument.setdefault(str(row.get("instrument")), []).append(row)
        entries, skipped = [], Counter()
        for instrument in sorted(by_instrument):
            ordered = sorted(by_instrument[instrument], key=lambda r: r["asof_ms"])
            next_entry = 0
            for index, row in enumerate(ordered):
                if index < next_entry:
                    continue
                reason, dist, outcome_z = forecast_pair(
                    row, DEFAULT_SAMPLES_FIELD, DEFAULT_OUTCOME_FIELD)
                reason = reason or self._row_reason(row)
                if reason:
                    skipped[reason] += 1
                    continue
                entries.append((row, dist, outcome_z))
                next_entry = index + hold
        return entries, skipped, sum(len(v) for v in by_instrument.values())

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
        for (right, sign), strike in zip(_LEGS, strikes):
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
            n = len(pnls)
            metrics.update({
                f"{book}_n_trades": n,
                f"{book}_total_pnl_usd": sum(pnls),
                f"{book}_mean_pnl_usd": sum(pnls) / n if n else 0.0,
                f"{book}_hit_rate": sum(p > 0 for p in pnls) / n if n else 0.0,
                f"{book}_cvar_usd": tail_mean(pnls, alpha) if n else 0.0,
                f"{book}_max_drawdown_usd": _max_drawdown(pnls),
                f"{book}_mean_credit_usd":
                    sum(c["credit_usd"] for c in traded) / n if n else 0.0,
            })
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
            ``forecasts``: sample-set forecast rows carrying ``close``,
            ``iv_index`` and the reference scale.

        Returns
        -------
        dict
            ``metrics`` (flat numbers, ``<book>_<stat>``; a book with no
            trades reports zeros beside ``<book>_n_trades = 0``) and
            ``report`` (a JsonArtifact with the per-trade ledger).

        Raises
        ------
        ValueError
            When no in-split row can be an entry.
        """
        quotes = VixProxyQuotes(*(self._knob(k) for k in VixProxyQuotes.KNOBS))
        years = self._knob("hold_steps") / self._knob("trading_days_per_year")
        entries, skipped, n_in_split = self._entries(ctx, inputs["forecasts"])
        if not entries:
            raise ValueError(f"{self.key}: no in-split row carries a forecast, outcome, "
                             f"forward, scale and iv_index in split "
                             f"{self.params['split']!r} (skipped {dict(skipped)})")
        ledger = [self._evaluate(quotes, row, dist, z, years) for row, dist, z in entries]
        metrics = self._metrics(ledger, skipped, n_in_split)
        report = {"kind": "vix_proxy_condor_backtest", "pricing": "vix_proxy",
                  "decision_eligible": False,
                  "units": "USD per condor, one contract per leg",
                  "params": {k: self._knob(k) for k in self._PARAMS if k in self.params
                             or k in self.DEFAULTS},
                  "years_to_expiry": years, "metrics": metrics, "ledger": ledger}
        return {"metrics": metrics, "report": JsonArtifact(report)}

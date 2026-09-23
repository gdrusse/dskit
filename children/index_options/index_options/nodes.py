"""One thin, non-serving Node binding exact references to condor diagnostics."""

from dskit.pipeline.distribution_models import REFERENCE_SCALE_FIELD
from dskit.pipeline.distribution_scores import (
    DEFAULT_OUTCOME_FIELD,
    DEFAULT_SAMPLES_FIELD,
    forecast_pair,
    row_in_split,
)
from dskit.pipeline.node import JsonArtifact, Node, reject_unknown_params
from dskit.pipeline.records import number_ok
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
from .distribution import CondorGeometry

__all__ = ["CondorDistributionReport", "CondorPayoffDiagnostic"]


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
            if reason or not (number_ok(forward) and number_ok(scale)):
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
            When no in-split row carries a usable forecast.
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

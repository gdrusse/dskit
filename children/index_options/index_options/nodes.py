"""One thin, non-serving Node binding exact references to condor diagnostics."""

from dskit.pipeline.node import JsonArtifact, Node, reject_unknown_params

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

__all__ = ["CondorPayoffDiagnostic"]


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

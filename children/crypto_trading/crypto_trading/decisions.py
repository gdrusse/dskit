"""Decision rows: one row per settled market and declared lead before its close.

A model is asked, at each lead, for P(YES) of a contract that will settle at its close. So
the unit of observation is ``(market, lead)``: the decision instant is the close minus the
lead, and the label is the market's own. Leads are a param, in minutes, because the lead
that matters is a research question, not a constant.

A lead longer than the market had been open (the decision would fall before the open) is
not clamped to the open: that row does not exist, and is listed on ``excluded``. A decision
exactly at the open is allowed.

Import cost: stdlib + dskit.
"""

from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

from . import fields as f

__all__ = ["DecisionRows"]

_MS_PER_MINUTE = 60_000
_MS_PER_S = 1000


class DecisionRows(Node):
    """Expand each market into one row per lead (role ``transform``).

    Inputs: ``records``, the :class:`~crypto_trading.kalshi_rows.MarketRows` rows. Outputs:
    ``records`` (every market field plus ``lead_minutes``, ``decision_ms`` and ``tau_s``, the
    seconds from the decision to the close; ordered by market then lead) and ``excluded``
    (``{"ticker", "lead_minutes", "reason"}`` for a decision before the market opened).

    Parameters
    ----------
    params : dict
        ``leads_minutes`` (non-empty list of distinct positive numbers, REQUIRED).

    Examples
    --------
    Decide 2, 5 and 10 minutes before each close::

        node = DecisionRows("decisions", {"leads_minutes": [2, 5, 10]})
        out = node.run(ctx, {"records": markets})
        # -> out["records"][0]["decision_ms"] == markets[0]["close_ms"] - 10 * 60000
    """

    role = "transform"
    outputs = ("records", "excluded")
    _PARAMS = ("leads_minutes",)

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
        leads = params.get("leads_minutes")
        if (not isinstance(leads, list) or not leads
                or any(isinstance(v, bool) or not number_ok(v) or v <= 0 for v in leads)
                or len(set(leads)) != len(leads)):
            problems.append(
                f"leads_minutes is required: a non-empty list of distinct positive numbers, got {leads!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: a function of the market rows and the leads.

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

    def validate_inputs(self, inputs):
        """Refuse a ``records`` port that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem when ``records`` is not a list.
        """
        if not isinstance(inputs.get("records"), list):
            return [f"records must be a list of market rows, got {type(inputs.get('records')).__name__}"]
        return []

    def _decision(self, market, lead):
        """Build the decision row for one market and lead (a new dict)."""
        decision = market[f.CLOSE_MS] - round(lead * _MS_PER_MINUTE)
        return {**market, f.LEAD: lead, f.DECISION_MS: decision,
                f.TAU_S: (market[f.CLOSE_MS] - decision) / _MS_PER_S}

    def run(self, ctx, inputs):
        """Expand the markets into decision rows.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the market rows.

        Returns
        -------
        dict
            ``{"records", "excluded"}``.
        """
        rows, excluded = [], []
        for market in sorted(inputs["records"], key=lambda m: (m[f.CLOSE_MS], m[f.TICKER])):
            for lead in sorted(self.params["leads_minutes"]):
                row = self._decision(market, lead)
                if row[f.DECISION_MS] < market[f.OPEN_MS]:
                    excluded.append({"ticker": market[f.TICKER], f.LEAD: lead, "reason": "before_open"})
                else:
                    rows.append(row)
        self.log.info("%d decision row(s) from %d market(s); %d before open",
                      len(rows), len(inputs["records"]), len(excluded))
        return {"records": rows, "excluded": excluded}

"""Decision rows: one row per settled market and declared lead before its close.

A model is asked, at each lead, for P(YES) of a contract that will settle at its close. So
the unit of observation is ``(market, lead)``: the INFORMATION instant ``I`` (``decision_ms``) is the
close minus the lead, and the label is the market's own. Leads are a param, in minutes, because the lead
that matters is a research question, not a constant.

One information instant, then a trade after it. The spot (the Binance bar that closed before ``I``) and
the market's quote (the candle that ended by ``I``) are both read as of ``I``; an order placed on them
can only fill at the EXECUTION instant ``E = I + exec_lag_s`` (``exec_ms``), a declared latency that must
be positive: a fill cannot precede the information it acts on. The fair-value horizon, ``tau_s``, runs
from ``E`` to the close (conservative: it is shorter than the horizon from ``I`` by the lag, so the
fair value is slightly overconfident, never slightly optimistic about the market). Everything known at
``I`` is legal at ``E``; nothing stamped at or after ``E`` may be read, and nothing is.

A decision is made only strictly AFTER the market's strike is known (``strike_known_ms``: the
open plus the series' publication lag). A 15-minute up/down strike is the previous window's
settlement value and is published a few seconds after the open, so a lead at or above the market's
duration minus that lag has nothing to price against. Such a row is not clamped: it does not exist,
and is listed on ``excluded`` (``before_open`` when the decision precedes the open, otherwise
``strike_not_known``).

Import cost: stdlib + dskit.
"""

from dskit.pipeline.node import reject_unknown_params
from dskit.pipeline.records import number_ok

from . import fields as f
from .ports import ListPortsNode

__all__ = ["DecisionRows"]

_MS_PER_MINUTE = 60_000
_MS_PER_S = 1000


class DecisionRows(ListPortsNode):
    """Expand each market into one row per lead (role ``transform``).

    Inputs: ``records``, the :class:`~crypto_trading.kalshi_rows.MarketRows` rows. Outputs:
    ``records`` (every market field plus ``lead_minutes``, ``decision_ms`` (the information instant),
    ``exec_ms`` and ``tau_s``, the seconds from execution to the close; ordered by market then lead) and
    ``excluded`` (``{"ticker", "lead_minutes", "reason"}`` for a decision before the open, not strictly
    after the strike is known, or with an execution at or after the close).

    Parameters
    ----------
    params : dict
        ``leads_minutes`` (non-empty list of distinct positive numbers, REQUIRED); ``exec_lag_s``
        (number > 0, REQUIRED) the seconds from the information instant to the fill.

    Examples
    --------
    Decide 2, 5 and 10 minutes before each close::

        node = DecisionRows("decisions", {"leads_minutes": [2, 5, 10], "exec_lag_s": 5})
        out = node.run(ctx, {"records": markets})
        # -> out["records"][0]["decision_ms"] == markets[0]["close_ms"] - 10 * 60000
    """

    role = "transform"
    outputs = ("records", "excluded")
    _PARAMS = ("leads_minutes", "exec_lag_s")

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
        lag = params.get("exec_lag_s")
        if isinstance(lag, bool) or not (number_ok(lag) and lag > 0):
            problems.append(
                f"exec_lag_s is required: a number of seconds > 0 (a fill cannot precede its information), got {lag!r}")
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

    def _decision(self, market, lead):
        """Build the decision row for one market and lead (a new dict)."""
        decision = market[f.CLOSE_MS] - round(lead * _MS_PER_MINUTE)
        execution = decision + round(self.params["exec_lag_s"] * _MS_PER_S)
        return {**market, f.LEAD: lead, f.DECISION_MS: decision, f.EXEC_MS: execution,
                f.TAU_S: (market[f.CLOSE_MS] - execution) / _MS_PER_S}

    @staticmethod
    def _exclusion(row):
        """Return why the decision cannot be made, or None: before the open, strike not yet known, or filled after the close."""
        if row[f.DECISION_MS] < row[f.OPEN_MS]:
            return "before_open"
        if row[f.DECISION_MS] <= row[f.STRIKE_KNOWN_MS]:
            return "strike_not_known"
        return "exec_not_before_close" if row[f.EXEC_MS] >= row[f.CLOSE_MS] else None

    def run(self, ctx, inputs):
        """Expand the markets into decision rows.

        Parameters
        ----------
        ctx : NodeContext or None
            The run frame; its run directory receives ``excluded.json`` (the run record holds only shapes).
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
                reason = self._exclusion(row)
                if reason:
                    excluded.append({"ticker": market[f.TICKER], f.LEAD: lead, "reason": reason})
                else:
                    rows.append(row)
        self.log.info("%d decision row(s) from %d market(s); %d excluded",
                      len(rows), len(inputs["records"]), len(excluded))
        if ctx is not None:
            self.write_artifact(ctx, "excluded.json", excluded)
        return {"records": rows, "excluded": excluded}

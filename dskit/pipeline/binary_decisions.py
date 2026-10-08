"""Decision rows over settled binary markets, and the market's own quote at each decision (ADR-0248).

A model is asked, at each declared lead before a binary contract's close, for P(YES). So the
unit of observation is ``(market, lead)``, and two transforms build it from rows a binary-market
reader wrote (:mod:`dskit.pipeline.libs.binary_market_rows`):

- :class:`DecisionRows` expands each market into one row per lead. The INFORMATION instant
  ``I`` (``decision_ms``) is the close minus the lead. An order placed on what is known at ``I``
  can only fill at the EXECUTION instant ``E = I + exec_lag_s`` (``exec_ms``), a declared latency
  that must be positive: a fill cannot precede the information it acts on. The fair-value
  horizon ``tau_s`` runs from ``E`` to the close (conservative: shorter than the horizon from
  ``I`` by the lag, so a fair value is slightly overconfident, never slightly optimistic about the
  market). Everything known at ``I`` is legal at ``E``; nothing stamped at or after ``E`` is read.
  A decision is made only strictly AFTER the market's strike is known (``strike_known_ms``: the
  open plus a declared publication lag). A strike that is the previous window's settlement value
  is published a few seconds after the open, so a lead at or above the market's duration minus
  that lag has nothing to price against. Such a row is not clamped: it does not exist, and is
  listed on ``excluded`` (``before_open`` when the decision precedes the open, ``strike_not_known``
  when it is not strictly after the strike is known, ``exec_not_before_close`` when the fill would
  land at or after the close).
- :class:`QuoteState` adds the quote as of ``I``. A quote bar summarises the interval that ENDS at
  its ``end_ms`` and its close is the last quote in it. The spot a model reads at ``I`` covers the
  interval before ``I``, and the matching quote is the bar that ended AT or before ``I`` (the same
  interval). Reading the bar that ended one interval earlier would compare a fair value computed
  from the price at ``I`` with an older quote: a market that had not yet seen the move, so the
  fair value would appear to beat it (a simulated zero-edge market showed a Brier gain of 0.013,
  about 6 standard errors, and a positive take profit). A bar that ends after ``I`` (so anything
  near the execution instant, and above all after the close, where quotes are the post-settlement
  0 or 1) is never read; the tests plant an extreme quote there. The last usable bar is the state
  unless it is older than ``max_candle_age_ms``: then the state is missing, never carried forward.
  Nothing is imputed. A side the bar does not carry is None, ``quote_missing`` says so, and
  ``mid`` and ``spread`` exist only when both sides do. ``two_sided`` is a scorer's gate: both
  quotes present, not crossed, and strictly inside ``(quote_floor, quote_ceiling)``; a bid at the
  floor or an ask at the ceiling is an empty side, not a price.

The column names every binary-market node shares live here once (they are the toolkit's
vocabulary, never a venue's: a reader maps the venue's own field names onto them). Units, stated
once because a mix-up is the classic silent bug: ``*_ms`` are epoch MILLISECONDS; ``tau_s`` is a
duration in seconds; prices of a contract are in ``[quote_floor, quote_ceiling]``.

Import cost: stdlib + dskit.
"""

from bisect import bisect_right

from dskit.pipeline.binary_pricing import LOWER, UPPER
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = [
    "BOUND_FIELDS", "CANDLE_AGE_MS", "CANDLE_OPEN_INTEREST", "CANDLE_PRICE", "CANDLE_VOLUME", "CAP_STRIKE",
    "CLOSE_MS", "DecisionRows", "DECISION_MS", "END_MS", "EVENT", "EXEC_MS", "FEE_BUY_NO", "FEE_BUY_YES",
    "FEE_MULTIPLIER", "FEE_RATE", "FEE_SCHEDULE_RETRIEVED", "FEE_STATUS", "FEE_TYPE", "FLOOR_STRIKE", "LABEL",
    "LEAD", "ListPortsNode", "MID", "OPEN_INTEREST", "OPEN_MS", "PAYOFF", "PRICE", "QUOTE_MISSING", "QuoteState",
    "RETRIEVED", "RETRIEVED_MS", "SERIES", "SETTLE_VALUE", "SETTLEMENT_MS", "SPREAD", "STRIKE_KNOWN_MS",
    "STRIKE_TYPE", "TAU_S", "TICKER", "TWO_SIDED", "VOLUME", "YES_ASK", "YES_BID",
]

# -- a market row (what BinaryMarketRows writes) ------------------------------------
TICKER = "ticker"
EVENT = "event_ticker"
SERIES = "series"
STRIKE_TYPE = "strike_type"
PAYOFF = "payoff"
FLOOR_STRIKE = "floor_strike"
CAP_STRIKE = "cap_strike"
#: The payoff geometries read a ``lower`` and an ``upper`` bound; a market row calls them the floor and the cap strike.
BOUND_FIELDS = {LOWER: FLOOR_STRIKE, UPPER: CAP_STRIKE}
OPEN_MS = "open_ms"
CLOSE_MS = "close_ms"
#: When the strike is known: the open plus the declared publication lag. A decision may price a market only strictly after it.
STRIKE_KNOWN_MS = "strike_known_ms"
LABEL = "label"
#: The realised settlement value and the instant it became known. Both are LABEL columns, carried only when the
#: stream has them: they exist from ``settlement_ms`` on, after the close, so they are never a feature of the market
#: they settle, and a join that wants one as an input to a LATER decision must gate on ``settlement_ms`` first.
SETTLE_VALUE = "settle_value"
SETTLEMENT_MS = "settlement_ms"

# -- a decision row (what DecisionRows adds) ----------------------------------------
LEAD = "lead_minutes"
#: The INFORMATION instant I; the trade fills at ``exec_ms = I + exec_lag_s`` and ``tau_s`` runs from there to the close.
DECISION_MS = "decision_ms"
EXEC_MS = "exec_ms"
TAU_S = "tau_s"

# -- a quote bar (what QuoteBarRows writes and QuoteState reads) --------------------
#: A bar's END instant in epoch ms.
END_MS = "end_ms"
PRICE = "price"
VOLUME = "volume"
OPEN_INTEREST = "open_interest"
YES_BID = "yes_bid"
YES_ASK = "yes_ask"

# -- the quote state (what QuoteState adds) ------------------------------------------
MID = "mid"
SPREAD = "spread"
QUOTE_MISSING = "quote_missing"
TWO_SIDED = "two_sided"
CANDLE_PRICE = "candle_price"
CANDLE_VOLUME = "candle_volume"
CANDLE_OPEN_INTEREST = "candle_open_interest"
CANDLE_AGE_MS = "candle_age_ms"

# -- a fee-schedule row (FeeScheduleRows) and the fee columns (FeeColumns) -----------
FEE_TYPE = "fee_type"
FEE_MULTIPLIER = "fee_multiplier"
RETRIEVED = "retrieved"
RETRIEVED_MS = "retrieved_ms"
FEE_RATE = "fee_rate"
#: Per-contract taker fees: buying YES at the ask, and buying NO at one minus the bid.
FEE_BUY_YES = "fee_buy_yes"
FEE_BUY_NO = "fee_buy_no"
FEE_STATUS = "fee_status"
FEE_SCHEDULE_RETRIEVED = "fee_schedule_retrieved"

_MS_PER_MINUTE = 60_000
_MS_PER_S = 1000


class ListPortsNode(Node):
    """A node whose named input ports must each carry a list of rows (abstract: a subclass supplies ``run``).

    The one owner of "this port must be a list": a one-shot iterable would be consumed by a check and
    reach ``run`` empty, so every port in :attr:`LIST_PORTS` is refused at run time unless it is a list.

    Parameters
    ----------
    key : str
        The node's key in the document.
    params : dict
        The node's params, as the subclass declares them.

    Examples
    --------
    A transform with two row ports::

        class Joiner(ListPortsNode):
            role = "transform"
            LIST_PORTS = ("records", "extras")

            def run(self, ctx, inputs):
                return {"records": inputs["records"] + inputs["extras"]}

        Joiner("join", {}).validate_inputs({"records": [], "extras": ()})
        # -> ["extras must be a list of rows, got tuple"]
    """

    #: The input ports that must be lists of rows.
    LIST_PORTS = ("records",)

    def validate_inputs(self, inputs):
        """Refuse a port in :attr:`LIST_PORTS` that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per port that is not a list; empty when all are.
        """
        return [f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}"
                for port in self.LIST_PORTS if not isinstance(inputs.get(port), list)]

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the output is a function of the input rows and the params.

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


class DecisionRows(ListPortsNode):
    """Expand each market into one row per lead (role ``transform``).

    Inputs: ``records``, market rows carrying at least ``ticker``, ``open_ms``, ``close_ms`` and
    ``strike_known_ms`` (what :class:`~dskit.pipeline.libs.binary_market_rows.BinaryMarketRows`
    writes). Outputs: ``records`` (every market field plus ``lead_minutes``, ``decision_ms`` (the
    information instant), ``exec_ms`` and ``tau_s``, the seconds from execution to the close;
    ordered by close, ticker, then lead) and ``excluded`` (``{"ticker", "lead_minutes", "reason"}``
    for a decision before the open, not strictly after the strike is known, or with an execution at
    or after the close).

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

    def _decision(self, market, lead):
        """Build the decision row for one market and lead (a new dict)."""
        decision = market[CLOSE_MS] - round(lead * _MS_PER_MINUTE)
        execution = decision + round(self.params["exec_lag_s"] * _MS_PER_S)
        return {**market, LEAD: lead, DECISION_MS: decision, EXEC_MS: execution,
                TAU_S: (market[CLOSE_MS] - execution) / _MS_PER_S}

    @staticmethod
    def _exclusion(row):
        """Return why the decision cannot be made (before the open, strike not yet known, filled after the close), or None."""
        if row[DECISION_MS] < row[OPEN_MS]:
            return "before_open"
        if row[DECISION_MS] <= row[STRIKE_KNOWN_MS]:
            return "strike_not_known"
        return "exec_not_before_close" if row[EXEC_MS] >= row[CLOSE_MS] else None

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
        for market in sorted(inputs["records"], key=lambda m: (m[CLOSE_MS], m[TICKER])):
            for lead in sorted(self.params["leads_minutes"]):
                row = self._decision(market, lead)
                reason = self._exclusion(row)
                if reason:
                    excluded.append({TICKER: market[TICKER], LEAD: lead, "reason": reason})
                else:
                    rows.append(row)
        self.log.info("%d decision row(s) from %d market(s); %d excluded",
                      len(rows), len(inputs["records"]), len(excluded))
        if ctx is not None:
            self.write_artifact(ctx, "excluded.json", excluded)
        return {"records": rows, "excluded": excluded}


class QuoteState(ListPortsNode):
    """Add the YES quote, mid, spread, volume and open interest at each decision (role ``transform``).

    Inputs: ``records`` (rows with ``ticker`` and ``decision_ms``) and ``candles`` (quote bars with
    ``ticker``, ``end_ms``, ``yes_bid``, ``yes_ask``, ``price``, ``volume``, ``open_interest``: what
    :class:`~dskit.pipeline.libs.binary_market_rows.QuoteBarRows` writes; the markets' own and any
    others). Output ``records``: every row plus ``yes_bid``, ``yes_ask``, ``mid``, ``spread``,
    ``candle_price``, ``candle_volume``, ``candle_open_interest``, ``candle_age_ms``,
    ``quote_missing`` and ``two_sided``.

    Parameters
    ----------
    params : dict
        ``max_candle_age_ms`` (int >= 1, REQUIRED) how old the last bar may be (inclusive);
        ``quote_floor`` and ``quote_ceiling`` (numbers, floor below ceiling, REQUIRED) the
        exclusive bounds a real bid and ask lie between.

    Examples
    --------
    Read the quote as of each decision::

        node = QuoteState("state", {"max_candle_age_ms": 120000, "quote_floor": 0.0,
                                    "quote_ceiling": 1.0})
        out = node.run(ctx, {"records": rows, "candles": bars})
        # -> out["records"][0]["mid"] is (yes_bid + yes_ask) / 2, or None
    """

    role = "transform"
    outputs = ("records",)
    LIST_PORTS = ("records", "candles")
    _PARAMS = ("max_candle_age_ms", "quote_floor", "quote_ceiling")

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
        if "max_candle_age_ms" not in params:
            problems.append("max_candle_age_ms is required")
        else:
            check_int_param(problems, "max_candle_age_ms", params["max_candle_age_ms"], ge=1)
        for name in ("quote_floor", "quote_ceiling"):
            if not number_ok(params.get(name)):
                problems.append(f"{name} is required: a number, got {params.get(name)!r}")
        if not problems and not params["quote_floor"] < params["quote_ceiling"]:
            problems.append("quote_ceiling must be above quote_floor")
        return problems

    @staticmethod
    def _by_ticker(bars):
        """Group bars by ticker, each group ascending by end instant, with its end instants beside it."""
        groups = {}
        for bar in bars:
            groups.setdefault(bar[TICKER], []).append(bar)
        return {ticker: ([b[END_MS] for b in ordered], ordered)
                for ticker, group in groups.items()
                for ordered in [sorted(group, key=lambda b: b[END_MS])]}

    def _last_bar(self, row, groups):
        """Return the row's market's last bar that ended by the information instant and is fresh enough, or None."""
        ends, bars = groups.get(row[TICKER], ((), ()))
        index = bisect_right(ends, row[DECISION_MS]) - 1
        if index < 0 or row[DECISION_MS] - ends[index] > self.params["max_candle_age_ms"]:
            return None
        return bars[index]

    def _two_sided(self, bid, ask):
        """Say whether bid and ask are a real, uncrossed two-sided quote inside the declared bounds."""
        low, high = self.params["quote_floor"], self.params["quote_ceiling"]
        return number_ok(bid) and number_ok(ask) and low < bid <= ask < high

    def _state(self, row, bar):
        """Return the state columns for one row and its bar (None when there is none)."""
        bid = None if bar is None else bar[YES_BID]
        ask = None if bar is None else bar[YES_ASK]
        both = number_ok(bid) and number_ok(ask)
        get = (lambda key: None) if bar is None else bar.get
        return {
            YES_BID: bid, YES_ASK: ask,
            MID: (bid + ask) / 2.0 if both else None, SPREAD: ask - bid if both else None,
            CANDLE_PRICE: get(PRICE), CANDLE_VOLUME: get(VOLUME), CANDLE_OPEN_INTEREST: get(OPEN_INTEREST),
            CANDLE_AGE_MS: None if bar is None else row[DECISION_MS] - bar[END_MS],
            QUOTE_MISSING: not both, TWO_SIDED: self._two_sided(bid, ask),
        }

    def run(self, ctx, inputs):
        """Add the quote state to every row.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records`` and ``candles``.

        Returns
        -------
        dict
            ``{"records": [...]}``, new dicts in input order.
        """
        groups = self._by_ticker(inputs["candles"])
        records = [{**row, **self._state(row, self._last_bar(row, groups))} for row in inputs["records"]]
        self.log.info("quote state on %d row(s); %d without a quote",
                      len(records), sum(1 for r in records if r[QUOTE_MISSING]))
        return {"records": records}

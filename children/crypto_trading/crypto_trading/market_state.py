"""The market's own quote at the decision instant, from Kalshi 1-minute candles that had ended.

A candle summarises the minute that ENDS at its ``end_ms``. It is usable at a decision when
``end_ms <= decision_ms``: the minute is over, so everything in it was known. A candle that ends
later summarises time the decision cannot see (and after the close its quotes are the
post-settlement 0 or 1), so it is never read; the test plants an extreme quote there. The last
usable candle is the state, unless it is older than ``max_candle_age_ms``: then the state is
missing, never carried forward.

Nothing is imputed. A side the candle does not carry is None, ``quote_missing`` says so, and
``mid`` and ``spread`` exist only when both sides do. ``two_sided`` is the scorer's gate: both
quotes present, not crossed, and strictly inside ``(quote_floor, quote_ceiling)``: a bid at the
floor or an ask at the ceiling is an empty side, not a price.

Import cost: stdlib + dskit.
"""

from bisect import bisect_right

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

from . import fields as f

__all__ = ["MarketState"]


class MarketState(Node):
    """Add the YES quote, mid, spread, volume and open interest at each decision (role ``transform``).

    Inputs: ``records`` (rows with ``ticker`` and ``decision_ms``) and ``candles`` (the
    :class:`~crypto_trading.kalshi_rows.CandleRows` records, the markets' own and any others).
    Output ``records``: every row plus ``yes_bid``, ``yes_ask``, ``mid``, ``spread``,
    ``candle_price``, ``candle_volume``, ``candle_open_interest``, ``candle_age_ms``,
    ``quote_missing`` and ``two_sided``.

    Parameters
    ----------
    params : dict
        ``max_candle_age_ms`` (int >= 1, REQUIRED) how old the last candle may be;
        ``quote_floor`` and ``quote_ceiling`` (numbers, floor below ceiling, REQUIRED) the
        exclusive bounds a real bid and ask lie between.

    Examples
    --------
    Read the quote as of each decision::

        node = MarketState("state", {"max_candle_age_ms": 120000, "quote_floor": 0.0,
                                     "quote_ceiling": 1.0})
        out = node.run(ctx, {"records": rows, "candles": candles})
        # -> out["records"][0]["mid"] is (yes_bid + yes_ask) / 2, or None
    """

    role = "transform"
    outputs = ("records",)
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

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: a function of the rows, the candles and the params.

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
        """Refuse a ``records`` or ``candles`` port that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per port that is not a list.
        """
        return [f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}"
                for port in ("records", "candles") if not isinstance(inputs.get(port), list)]

    @staticmethod
    def _by_ticker(candles):
        """Group candles by ticker, each group ascending by end instant."""
        groups = {}
        for candle in candles:
            groups.setdefault(candle[f.TICKER], []).append(candle)
        return {ticker: ([c[f.END_MS] for c in sorted_group], sorted_group)
                for ticker, group in groups.items()
                for sorted_group in [sorted(group, key=lambda c: c[f.END_MS])]}

    def _last_candle(self, row, groups):
        """Return the last candle of the row's market that ended by the decision and is fresh enough, or None."""
        ends, candles = groups.get(row[f.TICKER], ((), ()))
        index = bisect_right(ends, row[f.DECISION_MS]) - 1
        if index < 0 or row[f.DECISION_MS] - ends[index] > self.params["max_candle_age_ms"]:
            return None
        return candles[index]

    def _two_sided(self, bid, ask):
        """Say whether bid and ask are a real, uncrossed two-sided quote inside the declared bounds."""
        low, high = self.params["quote_floor"], self.params["quote_ceiling"]
        return number_ok(bid) and number_ok(ask) and low < bid <= ask < high

    def _state(self, row, candle):
        """Return the state columns for one row and its candle (None when there is none)."""
        bid = None if candle is None else candle[f.YES_BID]
        ask = None if candle is None else candle[f.YES_ASK]
        both = number_ok(bid) and number_ok(ask)
        get = (lambda key: None) if candle is None else candle.get
        return {
            f.YES_BID: bid, f.YES_ASK: ask,
            f.MID: (bid + ask) / 2.0 if both else None, f.SPREAD: ask - bid if both else None,
            f.CANDLE_PRICE: get(f.PRICE), f.CANDLE_VOLUME: get(f.VOLUME),
            f.CANDLE_OPEN_INTEREST: get(f.OPEN_INTEREST),
            f.CANDLE_AGE_MS: None if candle is None else row[f.DECISION_MS] - candle[f.END_MS],
            f.QUOTE_MISSING: not both, f.TWO_SIDED: self._two_sided(bid, ask),
        }

    def run(self, ctx, inputs):
        """Add the market state to every row.

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
        records = [{**row, **self._state(row, self._last_candle(row, groups))} for row in inputs["records"]]
        self.log.info("market state on %d row(s); %d without a quote",
                      len(records), sum(1 for r in records if r[f.QUOTE_MISSING]))
        return {"records": records}

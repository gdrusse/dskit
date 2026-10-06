"""The child's row vocabulary: every field name that crosses a node boundary, once.

Stage B passes one row dict from reader to scorer, and each node adds columns to
it. A name spelled in two nodes is a name that can drift in one of them, so every
name two nodes share lives here and is imported, never retyped. Names a document
chooses (a fair-value column, a volatility column) are NOT here: they are params.

Units, stated once because a mix-up is the classic silent bug here:

- ``*_ms`` fields are epoch MILLISECONDS (the pipeline's own convention, and
  Binance's). Kalshi's candle ``ts`` is epoch SECONDS; the reader converts it, and
  no row leaving a reader carries seconds except ``tau_s`` (a duration).
- Prices of a contract are dollars in ``[0, 1]``.
- Volatilities are the standard deviation of the log return per SQUARE ROOT OF A
  SECOND, whatever their source (bars, an EWMA, an annualised index).

Import cost: stdlib only.
"""

__all__ = [
    "ANCHOR_KNOWN_MS", "ANCHOR_MS", "ANCHOR_VALUE", "BASIS", "BASIS_AGE_MS", "BASIS_MISSING",
    "CAP", "CANDLE_AGE_MS", "CANDLE_OPEN_INTEREST", "CANDLE_PRICE", "CANDLE_VOLUME",
    "CLOSE_MS", "DECISION_MS", "END_MS", "EVENT", "FEE_BUY_NO", "FEE_BUY_YES", "FEE_MULTIPLIER",
    "FEE_TYPE", "FLOOR",
    "LABEL", "LEAD", "MID", "OPEN_INTEREST", "OPEN_MS", "PAYOFF", "PRICE", "QUOTE_MISSING",
    "RETRIEVED", "RETRIEVED_MS", "SERIES", "SPOT", "SPOT_BRTI", "SPREAD", "STRIKE_KNOWN_MS", "STRIKE_TYPE",
    "TAU_S", "TICKER",
    "TWO_SIDED", "VOLUME", "YES_ASK", "YES_BID",
]

TICKER = "ticker"
EVENT = "event_ticker"
SERIES = "series"
STRIKE_TYPE = "strike_type"
PAYOFF = "payoff"
FLOOR = "floor_strike"
CAP = "cap_strike"
OPEN_MS = "open_ms"
CLOSE_MS = "close_ms"
#: When the strike is known: the open plus the declared publication lag (see ``MarketRows``). A decision
#: may price a market only strictly after it.
STRIKE_KNOWN_MS = "strike_known_ms"
LABEL = "label"

LEAD = "lead_minutes"
DECISION_MS = "decision_ms"
TAU_S = "tau_s"

#: A Kalshi candle's END instant in epoch ms (the venue sends epoch seconds).
END_MS = "end_ms"

#: The raw Binance spot, and the same price restated in the settlement index's units (spot times basis).
SPOT = "spot"
SPOT_BRTI = "spot_brti"

#: A strike anchor row (``StrikeAnchors``): the index value a market's strike IS, the instant the average
#: it summarises ended, and the instant it became known.
ANCHOR_MS = "anchor_ms"
ANCHOR_KNOWN_MS = "known_ms"
ANCHOR_VALUE = "anchor_value"

#: Settlement index over Binance at the latest anchor known strictly before a decision, and its age.
BASIS = "basis"
BASIS_AGE_MS = "basis_age_ms"
BASIS_MISSING = "basis_missing"

#: A candle row's own fields (what ``CandleRows`` writes and ``MarketState`` reads).
PRICE = "price"
VOLUME = "volume"
OPEN_INTEREST = "open_interest"

#: A fee-schedule row's fields (what ``FeeRows`` writes and ``FeeColumns`` reads).
FEE_TYPE = "fee_type"
FEE_MULTIPLIER = "fee_multiplier"
RETRIEVED = "retrieved"
RETRIEVED_MS = "retrieved_ms"

#: Per-contract taker fee columns (what ``FeeColumns`` writes and ``KillTestScore`` reads).
FEE_BUY_YES = "fee_buy_yes"
FEE_BUY_NO = "fee_buy_no"

YES_BID = "yes_bid"
YES_ASK = "yes_ask"
MID = "mid"
SPREAD = "spread"
QUOTE_MISSING = "quote_missing"
TWO_SIDED = "two_sided"
CANDLE_PRICE = "candle_price"
CANDLE_VOLUME = "candle_volume"
CANDLE_OPEN_INTEREST = "candle_open_interest"
CANDLE_AGE_MS = "candle_age_ms"

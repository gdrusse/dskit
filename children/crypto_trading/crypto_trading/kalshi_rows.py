"""Readers that project the ``kalshi`` pack's provider-shaped streams into the child's vocabulary.

The pack stores the venue's own field names and units (ISO instants, candle ``ts`` in epoch
SECONDS, ``result`` as ``yes``/``no``). Each reader here subclasses
:class:`~dskit.pipeline.libs.observations.ObservationRows`, so the scan, the vintage bound
and the content fingerprint stay the seam's one implementation, and overrides only
:meth:`project`. The stream and its dedup key are facts about the stream, so a subclass pins
them (narrowing the knob away) rather than leaving them to the document.

- :class:`MarketRows` turns a settled market into one row with the payoff geometry, the strikes,
  the open and close instants (epoch ms), when the strike is KNOWN and the label (1 when YES settled,
  0 for NO). A 15-minute up/down market's strike is the previous window's settlement value, published
  a few seconds after this market opens, so ``strike_known_ms`` is the open plus a declared lag per series. A row
  that cannot be labelled or placed is NOT emitted: it is listed by ticker with a reason on
  the ``excluded`` port and counted on ``census``, never guessed (an unsettled market, a
  result that is not yes/no, a market never given a strike, a strike its geometry needs but
  lacks, an unparseable instant).
- :class:`CandleRows` converts the candle's END instant from seconds to ms and keeps the YES
  quotes, last price, volume and open interest, missing as None.
- :class:`FeeRows` keeps each series' ``fee_type`` and ``fee_multiplier`` per retrieval.

The venue vocabularies (which strike types map to which payoff, which results are binary,
which statuses mean settled) arrive as params, so a new geometry is configuration.

Import cost: stdlib + dskit.
"""

from abc import abstractmethod
from collections import Counter

from dskit.pipeline.libs.numpy import narrow_params
from dskit.pipeline.libs.observations import DEFAULT_TS_OUT, DEFAULT_TS_UNIT, ObservationRows
from dskit.pipeline.records import number_ok, price_ok

from . import fields as f
from .clock import instant_ms
from .payoffs import PAYOFFS

__all__ = ["CandleRows", "FeeRows", "MarketRows", "SECONDS_CEILING"]

#: Epoch SECONDS below this are plausible instants (it is the year 5138); a candle ``ts`` at or above it
#: is epoch MILLISECONDS passed where seconds belong, and is refused rather than read as year 50000.
SECONDS_CEILING = 10**11

_MS_PER_S = 1000

_PINNED = ("stream", "key_fields", "ts_field", "ts_unit", "ts_out", "shared_fields", "since_ms")


def _number(value):
    """Return ``value`` as a float when it is a finite number, else None."""
    return float(value) if number_ok(value) else None


class _StreamRows(ObservationRows):
    """The shared shape of this module's readers: one pinned stream, no instant field, research-only."""

    _PARAMS = narrow_params(ObservationRows._PARAMS, *_PINNED)

    @abstractmethod
    def stream(self):
        """Name the stream this reader fixes (str)."""

    @abstractmethod
    def key_fields(self):
        """Name the ``data`` fields the stream is deduplicated on (tuple of str)."""

    def ts_field(self):
        """Declare no instant field: each reader parses its own instants in :meth:`project`.

        Returns
        -------
        None
            No ``ts_field``.
        """
        return None

    def ts_unit(self):
        """Give the seam's default unit (unused without a ``ts_field``).

        Returns
        -------
        str
            The seam's default.
        """
        return DEFAULT_TS_UNIT

    def ts_out(self):
        """Give the seam's default output field (unused without a ``ts_field``).

        Returns
        -------
        str
            The seam's default.
        """
        return DEFAULT_TS_OUT

    def shared_fields(self):
        """Intern no field.

        Returns
        -------
        tuple
            Empty.
        """
        return ()

    def since_ms(self):
        """Read the whole stream: no implicit lower bound.

        Returns
        -------
        None
            No bound.
        """
        return None

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep this research reader out of served graphs.

        Parameters
        ----------
        params : dict
            Unused.
        verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            ``"forbidden"``.
        """
        return "forbidden"


class MarketRows(_StreamRows):
    """Read settled Kalshi markets as labelled rows (role ``data``).

    Outputs ``records`` (one row per usable market, ordered by close then ticker),
    ``excluded`` (``{"ticker", "reason"}`` per dropped market) and ``census`` (rows read,
    rows kept, and a count per reason).

    Parameters
    ----------
    params : dict
        ``root`` (str) the onboarding root; ``source`` (str) the registered source;
        ``series`` (non-empty list of str) the series kept, REQUIRED; ``payoff_by_strike_type``
        (dict, REQUIRED) the venue's ``strike_type`` -> a payoff name in
        :data:`~crypto_trading.payoffs.PAYOFFS`; ``result_labels`` (dict, REQUIRED) the
        venue's ``result`` -> 0 or 1; ``settled_statuses`` (non-empty list of str, REQUIRED)
        the payload statuses that mean settled; ``as_of_acquisition_ms`` (int >= 0, optional)
        the read vintage.

    Examples
    --------
    Read the two 15-minute series with their labels::

        node = MarketRows("markets", {
            "root": "./ob", "source": "kalshi-crypto", "series": ["KXBTC15M", "KXETH15M"],
            "payoff_by_strike_type": {"greater_or_equal": "above", "less": "below",
                                      "between": "between"},
            "result_labels": {"yes": 1, "no": 0}, "settled_statuses": ["finalized"],
            "strike_known_lag_s": {"KXBTC15M": 30, "KXETH15M": 30}})
        out = node.run(ctx, {})
        # -> out["records"][0]["label"] is 1 or 0; out["excluded"] names every dropped ticker
    """

    outputs = ("records", "excluded", "census")
    _PARAMS = _StreamRows._PARAMS + ("series", "payoff_by_strike_type", "result_labels", "settled_statuses",
                                     "strike_known_lag_s")

    #: Set by :meth:`project`: the dropped markets and the census of the last projection.
    excluded = None
    census = None

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
            The seam's problems plus one per unusable vocabulary knob.
        """
        problems = super().validate_params(params)
        for name in ("series", "settled_statuses"):
            value = params.get(name)
            if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v for v in value):
                problems.append(f"{name} is required: a non-empty list of strings, got {value!r}")
        mapping = params.get("payoff_by_strike_type")
        if not isinstance(mapping, dict) or not mapping:
            problems.append(f"payoff_by_strike_type is required: a map strike_type -> payoff, got {mapping!r}")
        else:
            problems += [f"payoff_by_strike_type[{k!r}] = {v!r} is not one of {sorted(PAYOFFS)}"
                         for k, v in mapping.items() if v not in PAYOFFS]
        labels = params.get("result_labels")
        if not isinstance(labels, dict) or not labels or any(
                isinstance(v, bool) or v not in (0, 1) for v in labels.values()):
            problems.append(f"result_labels is required: a map result -> 0 or 1, got {labels!r}")
        problems += cls._lag_problems(params.get("strike_known_lag_s"), params.get("series"))
        return problems

    @staticmethod
    def _lag_problems(lags, series):
        """Problems with ``strike_known_lag_s``: a map of listed series to non-negative seconds."""
        if not isinstance(lags, dict):
            return [f"strike_known_lag_s is required: a map series -> seconds (may be empty), got {lags!r}"]
        listed = series if isinstance(series, list) else []
        return [f"strike_known_lag_s[{k!r}] must be a number of seconds >= 0 for a series in {listed}, got {v!r}"
                for k, v in lags.items()
                if k not in listed or isinstance(v, bool) or not (number_ok(v) and v >= 0)]

    def stream(self):
        """Name the stream: the pack's ``markets``.

        Returns
        -------
        str
            ``"markets"``.
        """
        return "markets"

    def key_fields(self):
        """Dedup on the market ticker: the latest acquisition of a market wins.

        Returns
        -------
        tuple of str
            ``("ticker",)``.
        """
        return ("ticker",)

    def keep_values(self):
        """Read only the declared series at intake.

        Returns
        -------
        dict
            ``{"series_ticker": [...]}``.
        """
        return {"series_ticker": list(self.params["series"])}

    def _strike_problem(self, record):
        """Return why the record's strikes cannot place it (absent, zero or negative strikes included), or None."""
        strike_type = record.get("strike_type")
        if not strike_type:
            return "no_strike"
        name = self.params["payoff_by_strike_type"].get(strike_type)
        if name is None:
            return "unknown_strike_type"
        needed = PAYOFFS[name].strike_fields
        return "strike_missing" if not all(price_ok(record.get(field)) for field in needed) else None

    def _exclusion(self, record):
        """Return the reason this market is not usable, or None when it is."""
        if record.get("status") not in self.params["settled_statuses"]:
            return "not_settled"
        result = record.get("result")
        if not result:
            return "no_result"
        if result not in self.params["result_labels"]:
            return "result_not_binary"
        problem = self._strike_problem(record)
        if problem:
            return problem
        if instant_ms(record.get("open_time")) is None or instant_ms(record.get("close_time")) is None:
            return "bad_time"
        return None

    def _row(self, record):
        """Build the child-vocabulary row for one usable market."""
        return {
            f.TICKER: record["ticker"], f.EVENT: record["event_ticker"], f.SERIES: record["series_ticker"],
            f.STRIKE_TYPE: record["strike_type"],
            f.PAYOFF: self.params["payoff_by_strike_type"][record["strike_type"]],
            f.FLOOR: _number(record.get("floor_strike")), f.CAP: _number(record.get("cap_strike")),
            f.OPEN_MS: instant_ms(record["open_time"]), f.CLOSE_MS: instant_ms(record["close_time"]),
            f.STRIKE_KNOWN_MS: instant_ms(record["open_time"]) + self._lag_ms(record["series_ticker"]),
            f.LABEL: self.params["result_labels"][record["result"]],
        }

    def _lag_ms(self, series):
        """Return the publication lag of a series' strike in ms: 0 for a series with none declared."""
        return round(self.params["strike_known_lag_s"].get(series, 0) * _MS_PER_S)

    def project(self, records):
        """Turn the winning market rows into labelled rows, listing every market dropped.

        Parameters
        ----------
        records : list of dict
            The pack's ``markets`` rows, one per ticker.

        Returns
        -------
        list of dict
            The usable markets, ordered by close instant then ticker. :attr:`excluded` and
            :attr:`census` describe the rest.
        """
        kept, excluded = [], []
        for record in records:
            reason = self._exclusion(record)
            if reason is None:
                kept.append(self._row(record))
            else:
                excluded.append({"ticker": record["ticker"], "reason": reason})
        kept.sort(key=lambda row: (row[f.CLOSE_MS], row[f.TICKER]))
        self.excluded = sorted(excluded, key=lambda e: e["ticker"])
        self.census = {"rows": len(records), "kept": len(kept), **Counter(e["reason"] for e in excluded)}
        return kept

    def run(self, ctx, inputs):
        """Emit the labelled rows, the dropped tickers and the census.

        Parameters
        ----------
        ctx : NodeContext
            Unused: a source reads only its params.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``{"records", "excluded", "census"}``.
        """
        records = self._scan()
        self.log.info("kept %d market(s), excluded %d", len(records), len(self.excluded))
        return {"records": records, "excluded": self.excluded, "census": self.census}


class CandleRows(_StreamRows):
    """Read Kalshi 1-minute candles with the END instant in epoch milliseconds (role ``data``).

    Parameters
    ----------
    params : dict
        ``root`` (str) the onboarding root; ``source`` (str) the registered source;
        ``as_of_acquisition_ms`` (int >= 0, optional) the read vintage.

    Examples
    --------
    Read one series' candles, ordered by ticker then end instant::

        node = CandleRows("candles", {"root": "./ob", "source": "kalshi-crypto-candles-btc"})
        out = node.run(ctx, {})
        # -> out["records"][0] is {"ticker", "end_ms", "yes_bid", "yes_ask", "price",
        #    "volume", "open_interest"}
    """

    def stream(self):
        """Name the stream: the pack's ``candles``.

        Returns
        -------
        str
            ``"candles"``.
        """
        return "candles"

    def key_fields(self):
        """Dedup on ticker and candle instant.

        Returns
        -------
        tuple of str
            ``("ticker", "ts")``.
        """
        return ("ticker", "ts")

    @staticmethod
    def _end_ms(ts):
        """Convert a candle's epoch-seconds END instant to ms, refusing a value that is already ms."""
        if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
            raise ValueError(f"candle ts must be a non-negative integer of epoch seconds, got {ts!r}")
        if ts >= SECONDS_CEILING:
            raise ValueError(
                f"candle ts {ts} is not epoch seconds (it reads as year 5138 or later): "
                "it looks like milliseconds; the pack emits seconds")
        return ts * _MS_PER_S

    def project(self, records):
        """Convert each candle to the child vocabulary.

        Parameters
        ----------
        records : list of dict
            The pack's ``candles`` rows.

        Returns
        -------
        list of dict
            One row per candle, ordered by ticker then END instant; absent values stay None.

        Raises
        ------
        ValueError
            When a candle's ``ts`` is not epoch seconds.
        """
        rows = [{
            f.TICKER: r["ticker"], f.END_MS: self._end_ms(r["ts"]),
            f.YES_BID: _number(r.get("yes_bid_close")), f.YES_ASK: _number(r.get("yes_ask_close")),
            f.PRICE: _number(r.get("close")), f.VOLUME: _number(r.get("volume")),
            f.OPEN_INTEREST: _number(r.get("open_interest")),
        } for r in records]
        rows.sort(key=lambda row: (row[f.TICKER], row[f.END_MS]))
        return rows


class FeeRows(_StreamRows):
    """Read each series' fee schedule per retrieval (role ``data``).

    Parameters
    ----------
    params : dict
        ``root`` (str) the onboarding root; ``source`` (str) the registered source;
        ``as_of_acquisition_ms`` (int >= 0, optional) the read vintage.

    Examples
    --------
    Read the six series' schedules::

        node = FeeRows("fee_schedules", {"root": "./ob", "source": "kalshi-crypto"})
        out = node.run(ctx, {})
        # -> out["records"][0] is {"series", "fee_type", "fee_multiplier", "retrieved", "retrieved_ms"}
    """

    def stream(self):
        """Name the stream: the pack's ``fee_schedules``.

        Returns
        -------
        str
            ``"fee_schedules"``.
        """
        return "fee_schedules"

    def key_fields(self):
        """Dedup on series and retrieval instant.

        Returns
        -------
        tuple of str
            ``("series_ticker", "retrieved")``.
        """
        return ("series_ticker", "retrieved")

    def project(self, records):
        """Keep the schedule columns and the retrieval instant in ms.

        Parameters
        ----------
        records : list of dict
            The pack's ``fee_schedules`` rows.

        Returns
        -------
        list of dict
            One row per series and retrieval, ordered by series then retrieval.

        Raises
        ------
        ValueError
            When a row's ``retrieved`` is not an ISO instant.
        """
        rows = []
        for r in records:
            stamp = instant_ms(r.get("retrieved"))
            if stamp is None:
                raise ValueError(f"fee schedule row for {r.get('series_ticker')!r} has no usable retrieved instant")
            rows.append({f.SERIES: r["series_ticker"], f.FEE_TYPE: r.get("fee_type"),
                         f.FEE_MULTIPLIER: _number(r.get("fee_multiplier")),
                         f.RETRIEVED: r["retrieved"], f.RETRIEVED_MS: stamp})
        rows.sort(key=lambda row: (row[f.SERIES], row[f.RETRIEVED_MS]))
        return rows

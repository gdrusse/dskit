"""Readers that project a binary-market venue's acquired streams into the toolkit's row vocabulary (ADR-0256).

A connector pack stores the venue's own field names and units (ISO instants, a quote bar's
END instant in epoch SECONDS, a result spelled in the venue's words). Each reader here
subclasses :class:`~dskit.pipeline.libs.observations.ObservationRows`, so the scan, the vintage
bound and the content fingerprint stay the seam's one implementation, and overrides only
:meth:`project`. EVERY input field name is a required param with no default: the venue's
vocabulary lives in the child's run config, never here. The output columns are
:mod:`dskit.pipeline.binary_decisions`'s, so the decision and quote-state transforms read
what these readers write. The dedup key is derived from the field map (it is a fact about the
stream), and the seam's own knobs this reader answers itself are narrowed away.

- :class:`BinaryMarketRows` turns a settled market into one row with the payoff geometry, the
  strikes, the open and close instants (epoch ms), when the strike is KNOWN and the label (the
  venue's result mapped to 1 or 0). A strike that is the previous window's settlement value is
  published a few seconds after the market opens, so ``strike_known_ms`` is the open plus a
  declared lag per series. A row that cannot be labelled or placed is NOT emitted: it is listed
  by ticker with a reason on the ``excluded`` port and counted on ``census``, never guessed (an
  unsettled market, an empty result, a result that is not one of the declared labels, a market
  never given a strike, a strike type with no declared payoff, a strike its geometry needs but
  lacks, an unparseable or zoneless instant). When the stream carries the realised settlement
  value and its instant, the row carries ``settle_value`` (a LABEL) and ``settlement_ms`` (when
  it became known, after the close), and a market with no usable settlement instant is excluded
  by name (``no_settlement_ts``), because a label that cannot be gated is not used. A stream
  without those fields leaves the row exactly as it was.
- :class:`QuoteBarRows` converts a bar's END instant from epoch seconds to ms (refusing a value
  that is already ms) and keeps the YES quotes, last price, volume and open interest, missing
  as None.
- :class:`FeeScheduleRows` keeps each series' fee type and multiplier per retrieval.
- :class:`FeeColumns` prices the per-contract taker fee of buying YES at the ask and NO at one
  minus the bid. The MECHANICS (a per-order rule and a rounding policy that bills the ORDER on
  a grid) are :mod:`dskit.pipeline.fee_mechanics`'s; this node only maps the venue's fee type,
  through the ``fee_types`` param, to a base rate and a fee spec, and the rate is
  ``base_rate * multiplier``. A type the document does not name is marked, never priced. A
  stream that holds only the CURRENT schedule prices a historical market under today's; the row
  says which retrieval it used.

Import cost: stdlib + dskit.
"""

from abc import abstractmethod
from collections import Counter
from datetime import datetime, timedelta, timezone

from dskit.pipeline import binary_decisions as bd
from dskit.pipeline.binary_pricing import LOWER, PAYOFFS, UPPER
from dskit.pipeline.fee_mechanics import fee_model_from_spec, fee_spec_problems
from dskit.pipeline.libs.numpy import narrow_params
from dskit.pipeline.libs.observations import DEFAULT_TS_OUT, DEFAULT_TS_UNIT, ObservationRows
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = ["BinaryMarketRows", "FeeColumns", "FeeScheduleRows", "QuoteBarRows", "SECONDS_CEILING", "instant_ms"]

#: Epoch SECONDS below this are plausible instants (it is the year 5138); a bar END at or above it
#: is epoch MILLISECONDS passed where seconds belong, and is refused rather than read as year 50000.
SECONDS_CEILING = 10**11

_MS_PER_S = 1000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)

#: The seam's knobs every reader here answers itself, so they are narrowed out of ``_PARAMS``.
_PINNED = ("key_fields", "ts_field", "ts_unit", "ts_out", "shared_fields", "since_ms")

#: Which field-map param holds each payoff bound a geometry may read.
_BOUND_PARAMS = {LOWER: "floor_field", UPPER: "cap_field"}

_TYPE_KEYS = ("base_rate", "model")


def instant_ms(text):
    """Return an ISO-8601 instant that states its zone as epoch milliseconds, or None.

    The same rule as the platform's zone-required parser (a stamp with no zone is a guess and is
    refused; the result floors to whole milliseconds in exact integer arithmetic). It is restated
    here because a toolkit pack may not import the production layer; a test pins the two equal.
    A row with an unreadable instant is excluded by name, so the refusal is ``None``, not a crash.

    Parameters
    ----------
    text : str
        The instant, e.g. ``"2026-09-02T00:15:00Z"``.

    Returns
    -------
    int or None
        Epoch milliseconds, or None when ``text`` is empty, not a string, not an instant, or has no zone.

    Examples
    --------
    Convert a stamp, and refuse a zoneless one::

        instant_ms("1970-01-01T00:00:01Z")   # -> 1000
        instant_ms("1970-01-01T00:00:01")    # -> None, no zone
    """
    if not isinstance(text, str) or not text:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return (stamp - _EPOCH) // _ONE_MS


def _number(value):
    """Return ``value`` as a float when it is a finite number, else None."""
    return float(value) if number_ok(value) else None


class _StreamRows(ObservationRows):
    """The shared shape of this module's readers: a mapped stream, no instant field, research-only."""

    _PARAMS = narrow_params(ObservationRows._PARAMS, *_PINNED)
    #: The field-map params a subclass requires: each names one of the venue stream's fields.
    _FIELD_PARAMS = ()

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
            The seam's problems plus one per missing or unusable field-map param.
        """
        problems = super().validate_params(params)
        for name in cls._FIELD_PARAMS:
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(f"{name} is required: the stream's field name, a non-empty string, got {value!r}")
        return problems

    def field(self, name):
        """Return the stream's field name the document mapped to ``name``.

        Parameters
        ----------
        name : str
            A field-map param, e.g. ``"ticker_field"``.

        Returns
        -------
        str
            The venue's field name.
        """
        return self.params[name]

    @abstractmethod
    def key_fields(self):
        """Name the stream fields it is deduplicated on (tuple of str)."""

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


class BinaryMarketRows(_StreamRows):
    """Read settled binary markets as labelled rows (role ``data``).

    Outputs ``records`` (one row per usable market, ordered by close then ticker),
    ``excluded`` (``{"ticker", "reason"}`` per dropped market) and ``census`` (rows read,
    rows kept, and a count per reason).

    Parameters
    ----------
    params : dict
        ``root``, ``source``, ``stream`` (str, REQUIRED) the onboarding root, registered source and
        markets stream; ``as_of_acquisition_ms`` (int >= 0, optional) the read vintage. The field
        map, every entry a REQUIRED non-empty string naming the stream's own field:
        ``ticker_field`` (the dedup key), ``event_field``, ``series_field``, ``status_field``,
        ``result_field``, ``strike_type_field``, ``floor_field`` (the lower strike), ``cap_field``
        (the upper strike), ``open_time_field`` and ``close_time_field`` (ISO instants with a zone),
        ``settle_value_field`` and ``settled_at_field`` (read only when a row carries them). The
        vocabularies, all REQUIRED: ``series`` (non-empty list of str) the series kept;
        ``payoff_by_strike_type`` (dict) the venue's strike type -> a payoff name in
        :data:`dskit.pipeline.binary_pricing.PAYOFFS`; ``result_labels`` (dict) the venue's result
        -> 0 or 1; ``settled_statuses`` (non-empty list of str) the statuses that mean settled;
        ``strike_known_lag_s`` (dict, may be empty) a listed series -> seconds >= 0 from the open
        until its strike is known.

    Examples
    --------
    Read one series with its labels::

        node = BinaryMarketRows("markets", {
            "root": "./ob", "source": "venue-markets", "stream": "markets",
            "ticker_field": "id", "event_field": "event", "series_field": "series",
            "status_field": "state", "result_field": "outcome", "strike_type_field": "shape",
            "floor_field": "lo", "cap_field": "hi", "open_time_field": "opens",
            "close_time_field": "closes", "settle_value_field": "final", "settled_at_field": "final_at",
            "series": ["S15"], "payoff_by_strike_type": {"gte": "above", "lt": "below",
                                                         "range": "between"},
            "result_labels": {"Y": 1, "N": 0}, "settled_statuses": ["done"],
            "strike_known_lag_s": {"S15": 30}})
        out = node.run(ctx, {})
        # -> out["records"][0]["label"] is 1 or 0; out["excluded"] names every dropped ticker
    """

    outputs = ("records", "excluded", "census")
    _FIELD_PARAMS = ("ticker_field", "event_field", "series_field", "status_field", "result_field",
                     "strike_type_field", "floor_field", "cap_field", "open_time_field", "close_time_field",
                     "settle_value_field", "settled_at_field")
    _PARAMS = _StreamRows._PARAMS + _FIELD_PARAMS + (
        "series", "payoff_by_strike_type", "result_labels", "settled_statuses", "strike_known_lag_s")

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
            The seam's and the field map's problems plus one per unusable vocabulary knob.
        """
        problems = super().validate_params(params)
        for name in ("series", "settled_statuses"):
            value = params.get(name)
            if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v for v in value):
                problems.append(f"{name} is required: a non-empty list of strings, got {value!r}")
        mapping = params.get("payoff_by_strike_type")
        if not isinstance(mapping, dict) or not mapping:
            problems.append(f"payoff_by_strike_type is required: a map strike type -> payoff, got {mapping!r}")
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

    def key_fields(self):
        """Dedup on the market's ticker field: the latest acquisition of a market wins.

        Returns
        -------
        tuple of str
            ``(ticker_field,)``.
        """
        return (self.field("ticker_field"),)

    def keep_values(self):
        """Read only the declared series at intake.

        Returns
        -------
        dict
            ``{series_field: [...]}``.
        """
        return {self.field("series_field"): list(self.params["series"])}

    def _strike_problem(self, record):
        """Return why the record's strikes cannot place it (absent, zero or negative strikes included), or None."""
        strike_type = record.get(self.field("strike_type_field"))
        if not strike_type:
            return "no_strike"
        name = self.params["payoff_by_strike_type"].get(strike_type)
        if name is None:
            return "unknown_strike_type"
        needed = [self.field(_BOUND_PARAMS[bound]) for bound in PAYOFFS[name].bounds]
        return "strike_missing" if not all(price_ok(record.get(field)) for field in needed) else None

    def _exclusion(self, record):
        """Return the reason this market is not usable, or None when it is."""
        if record.get(self.field("status_field")) not in self.params["settled_statuses"]:
            return "not_settled"
        result = record.get(self.field("result_field"))
        if not result:
            return "no_result"
        if result not in self.params["result_labels"]:
            return "result_not_binary"
        problem = self._strike_problem(record)
        if problem:
            return problem
        if (instant_ms(record.get(self.field("open_time_field"))) is None
                or instant_ms(record.get(self.field("close_time_field"))) is None):
            return "bad_time"
        settled_at = self.field("settled_at_field")
        if settled_at in record and instant_ms(record[settled_at]) is None:
            return "no_settlement_ts"
        return None

    def _row(self, record):
        """Build the toolkit-vocabulary row for one usable market.

        The settlement columns are written only when the stream carries the fields: a source
        without them keeps the table exactly as it was.
        """
        row = self._base_row(record)
        settle_value, settled_at = self.field("settle_value_field"), self.field("settled_at_field")
        if settle_value in record:
            row[bd.SETTLE_VALUE] = _number(record[settle_value])
        if settled_at in record:
            row[bd.SETTLEMENT_MS] = instant_ms(record[settled_at])
        return row

    def _base_row(self, record):
        """Build the row's columns every markets stream carries."""
        strike_type, series = record[self.field("strike_type_field")], record[self.field("series_field")]
        opened = instant_ms(record[self.field("open_time_field")])
        return {
            bd.TICKER: record[self.field("ticker_field")], bd.EVENT: record[self.field("event_field")],
            bd.SERIES: series, bd.STRIKE_TYPE: strike_type,
            bd.PAYOFF: self.params["payoff_by_strike_type"][strike_type],
            bd.FLOOR_STRIKE: _number(record.get(self.field("floor_field"))),
            bd.CAP_STRIKE: _number(record.get(self.field("cap_field"))),
            bd.OPEN_MS: opened, bd.CLOSE_MS: instant_ms(record[self.field("close_time_field")]),
            bd.STRIKE_KNOWN_MS: opened + self._lag_ms(series),
            bd.LABEL: self.params["result_labels"][record[self.field("result_field")]],
        }

    def _lag_ms(self, series):
        """Return the publication lag of a series' strike in ms: 0 for a series with none declared."""
        return round(self.params["strike_known_lag_s"].get(series, 0) * _MS_PER_S)

    def project(self, records):
        """Turn the winning market rows into labelled rows, listing every market dropped.

        Parameters
        ----------
        records : list of dict
            The stream's market rows, one per ticker.

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
                excluded.append({bd.TICKER: record[self.field("ticker_field")], "reason": reason})
        kept.sort(key=lambda row: (row[bd.CLOSE_MS], row[bd.TICKER]))
        self.excluded = sorted(excluded, key=lambda e: e[bd.TICKER])
        self.census = {"rows": len(records), "kept": len(kept), **Counter(e["reason"] for e in excluded)}
        return kept

    def write_dropped(self, ctx):
        """Keep the dropped markets and the census beside the run: the run record holds only shapes.

        Parameters
        ----------
        ctx : NodeContext or None
            The run frame; with none (a unit test) nothing is written.
        """
        if ctx is not None:
            self.write_artifact(ctx, "excluded.json", self.excluded)
            self.write_artifact(ctx, "census.json", self.census)

    def run(self, ctx, inputs):
        """Emit the labelled rows, the dropped tickers and the census, and keep the latter two in the run directory.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; its run directory receives ``excluded.json`` and ``census.json``.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``{"records", "excluded", "census"}``.
        """
        records = self._scan()
        self.log.info("kept %d market(s), excluded %d", len(records), len(self.excluded))
        self.write_dropped(ctx)
        return {"records": records, "excluded": self.excluded, "census": self.census}


class QuoteBarRows(_StreamRows):
    """Read quote bars with the END instant in epoch milliseconds (role ``data``).

    Parameters
    ----------
    params : dict
        ``root``, ``source``, ``stream`` (str, REQUIRED); ``as_of_acquisition_ms`` (int >= 0,
        optional) the read vintage. The field map, every entry a REQUIRED non-empty string:
        ``ticker_field``, ``end_field`` (the bar's END instant in epoch SECONDS; with the ticker,
        the dedup key), ``bid_field`` and ``ask_field`` (the YES bid and ask at the bar's close),
        ``price_field`` (the last trade price), ``volume_field``, ``open_interest_field``.

    Examples
    --------
    Read one source's bars, ordered by ticker then end instant::

        node = QuoteBarRows("candles", {
            "root": "./ob", "source": "venue-bars", "stream": "candles",
            "ticker_field": "id", "end_field": "end_s", "bid_field": "bid", "ask_field": "ask",
            "price_field": "last", "volume_field": "qty", "open_interest_field": "oi"})
        out = node.run(ctx, {})
        # -> out["records"][0] is {"ticker", "end_ms", "yes_bid", "yes_ask", "price",
        #    "volume", "open_interest"}
    """

    _FIELD_PARAMS = ("ticker_field", "end_field", "bid_field", "ask_field", "price_field", "volume_field",
                     "open_interest_field")
    _PARAMS = _StreamRows._PARAMS + _FIELD_PARAMS

    def key_fields(self):
        """Dedup on ticker and bar instant.

        Returns
        -------
        tuple of str
            ``(ticker_field, end_field)``.
        """
        return (self.field("ticker_field"), self.field("end_field"))

    @staticmethod
    def _end_ms(ts):
        """Convert a bar's epoch-seconds END instant to ms, refusing a value that is already ms."""
        if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
            raise ValueError(f"a bar's end must be a non-negative integer of epoch seconds, got {ts!r}")
        if ts >= SECONDS_CEILING:
            raise ValueError(
                f"bar end {ts} is not epoch seconds (it reads as year 5138 or later): "
                "it looks like milliseconds; the reader expects seconds")
        return ts * _MS_PER_S

    def project(self, records):
        """Convert each bar to the toolkit vocabulary.

        Parameters
        ----------
        records : list of dict
            The stream's bar rows.

        Returns
        -------
        list of dict
            One row per bar, ordered by ticker then END instant; absent values stay None.

        Raises
        ------
        ValueError
            When a bar's end is not epoch seconds.
        """
        get = self.field
        rows = [{
            bd.TICKER: r[get("ticker_field")], bd.END_MS: self._end_ms(r.get(get("end_field"))),
            bd.YES_BID: _number(r.get(get("bid_field"))), bd.YES_ASK: _number(r.get(get("ask_field"))),
            bd.PRICE: _number(r.get(get("price_field"))), bd.VOLUME: _number(r.get(get("volume_field"))),
            bd.OPEN_INTEREST: _number(r.get(get("open_interest_field"))),
        } for r in records]
        rows.sort(key=lambda row: (row[bd.TICKER], row[bd.END_MS]))
        return rows


class FeeScheduleRows(_StreamRows):
    """Read each series' fee schedule per retrieval (role ``data``).

    Parameters
    ----------
    params : dict
        ``root``, ``source``, ``stream`` (str, REQUIRED); ``as_of_acquisition_ms`` (int >= 0,
        optional) the read vintage. The field map, every entry a REQUIRED non-empty string:
        ``series_field``, ``fee_type_field``, ``multiplier_field``, ``retrieved_field`` (an ISO
        instant with a zone; with the series, the dedup key).

    Examples
    --------
    Read every series' schedules::

        node = FeeScheduleRows("fee_schedules", {
            "root": "./ob", "source": "venue-fees", "stream": "fee_schedules",
            "series_field": "series", "fee_type_field": "kind", "multiplier_field": "scale",
            "retrieved_field": "pulled_at"})
        out = node.run(ctx, {})
        # -> out["records"][0] is {"series", "fee_type", "fee_multiplier", "retrieved", "retrieved_ms"}
    """

    _FIELD_PARAMS = ("series_field", "fee_type_field", "multiplier_field", "retrieved_field")
    _PARAMS = _StreamRows._PARAMS + _FIELD_PARAMS

    def key_fields(self):
        """Dedup on series and retrieval instant.

        Returns
        -------
        tuple of str
            ``(series_field, retrieved_field)``.
        """
        return (self.field("series_field"), self.field("retrieved_field"))

    def project(self, records):
        """Keep the schedule columns and the retrieval instant in ms.

        Parameters
        ----------
        records : list of dict
            The stream's fee-schedule rows.

        Returns
        -------
        list of dict
            One row per series and retrieval, ordered by series then retrieval.

        Raises
        ------
        ValueError
            When a row's retrieval is not an ISO instant with a zone.
        """
        series, retrieved = self.field("series_field"), self.field("retrieved_field")
        rows = []
        for r in records:
            stamp = instant_ms(r.get(retrieved))
            if stamp is None:
                raise ValueError(f"fee schedule row for {r.get(series)!r} has no usable retrieved instant "
                                 f"in {retrieved!r}")
            rows.append({bd.SERIES: r[series], bd.FEE_TYPE: r.get(self.field("fee_type_field")),
                         bd.FEE_MULTIPLIER: _number(r.get(self.field("multiplier_field"))),
                         bd.RETRIEVED: r[retrieved], bd.RETRIEVED_MS: stamp})
        rows.sort(key=lambda row: (row[bd.SERIES], row[bd.RETRIEVED_MS]))
        return rows


class FeeColumns(bd.ListPortsNode):
    """Add taker-fee columns to each row from a fee-schedule stream (role ``transform``).

    Inputs: ``records`` (rows with ``series``, ``yes_bid``, ``yes_ask``) and ``schedules``
    (:class:`FeeScheduleRows` records; the latest retrieval per series wins). Output ``records``:
    every row plus ``fee_rate``, ``fee_buy_yes`` (per contract, buying YES at the ask),
    ``fee_buy_no`` (per contract, buying NO at ``1 - bid``), ``fee_status`` and
    ``fee_schedule_retrieved``. Statuses besides ``ok``: ``no_schedule``,
    ``unsupported_fee_type``, ``no_multiplier``, ``no_quote``.

    Parameters
    ----------
    params : dict
        ``fee_types`` (dict, REQUIRED) the venue's fee type -> ``{"base_rate": number >= 0,
        "model": <a fee spec for fee_mechanics.fee_model_from_spec: mechanic and rounding>}``; no
        other key (a ``notes`` inside would move the document's identity hash). ``contracts``
        (int >= 1, REQUIRED) the order size the per-order rounding is applied to.

    Examples
    --------
    Price a 100-contract taker order from the stream's schedule::

        node = FeeColumns("fees", {"contracts": 100, "fee_types": {"type_q": {
            "base_rate": 0.07,
            "model": {"mechanic": "probability_quadratic",
                      "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}}}})
        out = node.run(ctx, {"records": rows, "schedules": schedule_rows})
        # -> out["records"][0]["fee_buy_yes"] is per contract, e.g. 0.0173
    """

    role = "transform"
    outputs = ("records",)
    LIST_PORTS = ("records", "schedules")
    _PARAMS = ("fee_types", "contracts")

    @classmethod
    def _type_problems(cls, fee_type, entry):
        """List problems with one ``fee_types`` entry."""
        where = f"fee_types[{fee_type!r}]"
        if not isinstance(entry, dict) or set(entry) != set(_TYPE_KEYS):
            return [f"{where} must have exactly the keys {list(_TYPE_KEYS)}, got {entry!r}"]
        problems = []
        if not (number_ok(entry["base_rate"]) and entry["base_rate"] >= 0.0):
            problems.append(f"{where}.base_rate must be a number >= 0, got {entry['base_rate']!r}")
        problems += [f"{where}.model: {p}" for p in fee_spec_problems(entry["model"])]
        return problems

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
        types = params.get("fee_types")
        if not isinstance(types, dict) or not types:
            problems.append(f"fee_types is required: a map fee type -> {{base_rate, model}}, got {types!r}")
        else:
            for fee_type, entry in types.items():
                problems += cls._type_problems(fee_type, entry)
        if "contracts" not in params:
            problems.append("contracts is required: the order size the fee rounding applies to")
        else:
            check_int_param(problems, "contracts", params["contracts"], ge=1)
        return problems

    def run(self, ctx, inputs):
        """Add the fee columns to every row.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records`` and ``schedules``.

        Returns
        -------
        dict
            ``{"records": [...]}``, new dicts in input order.
        """
        latest = self._latest(inputs["schedules"])
        models = {t: fee_model_from_spec(e["model"]) for t, e in self.params["fee_types"].items()}
        records = [self._priced(dict(row), latest.get(row.get(bd.SERIES)), models) for row in inputs["records"]]
        self.log.info("priced fees on %d row(s)", len(records))
        return {"records": records}

    @staticmethod
    def _latest(schedules):
        """Map each series to its most recently retrieved schedule row."""
        latest = {}
        for row in schedules:
            held = latest.get(row[bd.SERIES])
            if held is None or row[bd.RETRIEVED_MS] > held[bd.RETRIEVED_MS]:
                latest[row[bd.SERIES]] = row
        return latest

    def _rate(self, schedule):
        """Return ``(rate, status)``: the effective rate, or None and why there is none."""
        if schedule is None:
            return None, "no_schedule"
        entry = self.params["fee_types"].get(schedule[bd.FEE_TYPE])
        if entry is None:
            return None, "unsupported_fee_type"
        multiplier = schedule[bd.FEE_MULTIPLIER]
        if not (number_ok(multiplier) and multiplier >= 0.0):
            return None, "no_multiplier"
        return entry["base_rate"] * multiplier, "ok"

    def _priced(self, row, schedule, models):
        """Write the fee columns onto ``row`` (a copy)."""
        rate, status = self._rate(schedule)
        bid, ask = row.get(bd.YES_BID), row.get(bd.YES_ASK)
        row.update({bd.FEE_RATE: rate, bd.FEE_BUY_YES: None, bd.FEE_BUY_NO: None,
                    bd.FEE_SCHEDULE_RETRIEVED: None if schedule is None else schedule[bd.RETRIEVED]})
        if status == "ok" and not (number_ok(bid) and number_ok(ask)):
            status = "no_quote"
        if status == "ok":
            model, count = models[schedule[bd.FEE_TYPE]], self.params["contracts"]
            row[bd.FEE_BUY_YES] = model.order_fee(count, ask, rate) / count
            row[bd.FEE_BUY_NO] = model.order_fee(count, 1.0 - bid, rate) / count
        row[bd.FEE_STATUS] = status
        return row

"""Polymarket public data through the onboarding connector contract.

Polymarket exposes three keyless surfaces a research project needs
together: the Gamma catalogue (events, their markets, settlement and fee
fields), the CLOB order-book endpoint, and — for depth history the venue
itself never serves — the community pmxt archive of hourly order-book
dumps on the Hugging Face hub. This pack owns the transport for all
three: sub-windowed offset paging, pacing, retry with backoff, the
JSON-in-a-string decoding Gamma is fond of, book-level normalization,
the archive's three schema generations, and each stream's checkpoint
semantics. Projects declare the universe (series slugs, event slugs,
token ids), the window, and the archive coordinates.

Rows stay PROVIDER-SHAPED (ADR-0075): a record's top-level keys are
snake_case names for the venue's own fields, and its raw copies
(``fee_schedule``, ``resolution``) keep the venue's spelling. Mapping to
a project's vocabulary is the project's job.

Every HTTP call goes through ``get_json`` or ``post_json``, every archive
fetch through ``download``, every wait through ``sleep`` and every
instant through ``now`` — METHODS, so a test or a project subclasses and
overrides them (``run_acquisition`` instantiates the class itself, so a
script must live on a subclass; ``tests/onboarding/test_polymarket.py``
is the worked double). The defaults are stdlib urllib and
``huggingface_hub.hf_hub_download``; the hub client and pyarrow are
imported only inside ``read``. An archive hour file is ~360 MB, so it is
filtered in Arrow batches, never loaded whole, and deleted once read
(``cleanup`` removes the cache symlink AND its blob). The archive is
public: a token is optional, read from the environment at pull time
(``token_env``), handed to the hub, and never written into a record, a
log line or an error.

Two ``events`` choices differ from a market's "natural" key and are
deliberate. First, the venue's own ``closed`` flag decides both the kind
and the date (never date arithmetic). A market flagged ``closed: true``
is an observation stamped at the venue's ``closedTime`` — the instant it
really resolved, spelled ``2026-09-04 12:11:51+00`` — falling back to
``end_date`` only when the venue carries no ``closedTime`` (ADR-0080): a
market that resolves EARLY closes while its scheduled end is still ahead,
and dating it there would future-date the row and refuse the whole pull.
A closed market whose observation instant — the ``closedTime`` when it
has one, its end when it does not — still lies ahead of the pull is
refused BY NAME, both instants named, instead of emitted. A market flagged ``closed: false`` keeps
``end_date``, is emitted ``kind: "forecast"``, never advances the cursor,
and is re-emitted on every pull. Second, the cursor is the max instant
emitted, but it is RECORDED, NEVER CONSULTED — a market closes (and
resolves) after its end_date on a lag that varies by series, so a late
closer would sit below a cursor another market already advanced and be
lost for good. Every pull re-walks the declared window whole (the Kalshi
pack's ``markets`` choice) and bitemporal dedup keeps the latest evidence
per ``market_id``.

**The capture instant.** A row the venue does not date (a fee regime's
``retrieved``, a book the CLOB did not stamp) is dated at ``now`` FLOORED
TO THE MINUTE, and a book the CLOB stamped inside that minute keeps its
stamp as ``ts`` (the key, the venue's truth) but is dated at the capture
minute too. The floor makes a pull one capture (two pulls inside a minute
collide on their key; the later acquisition wins); it is no longer what
keeps rows before ``acquired_at`` — the platform stamps that at COMMIT,
after ``read`` has finished (ADR-0079).

**The archive's coordinates.** ``archive_hours`` filters to a token set:
the declared ``token_ids``, or — when none are declared — the ids the
Gamma walk resolves for ``series_slugs``/``slugs`` over the same window
(declared ids WIN outright; the two sets are never unioned). Before
walking, the mirror's own sync state (:data:`SYNC_STATE_PATH`) says what
a missing hour MEANS: an hour it lists in ``pending_gap_hours`` will
never arrive, so the walk skips it and the cursor advances PAST it (a
permanent gap can no longer block a backfill), while an hour past
``latest_available_hour`` is simply not mirrored yet, so the walk stops
with the cursor where it is and retries next pull. That document is
advisory — absent, unreadable, or carrying a stamp that does not parse,
it costs a LOG and nothing else. Each archive row also carries ``seq``,
its ordinal among the rows one ``(asset_id, ts)`` holds in the file's own
order: pmxt writes one ``price_change`` per price LEVEL and several land
in the same millisecond, so ``(asset_id, ts)`` alone is not a key and a
replay applies rows in ``(ts, seq)`` order.

Import cost: stdlib.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from ..base import AssetError, MODES, parse_utc
from ..connector import MAX_BACKOFF_S, PROTOCOL, Connector

__all__ = [
    "ARCHIVE_EVENT_TYPES",
    "ARCHIVE_FIELDS",
    "ARCHIVE_STREAM",
    "BOOKS_STREAM",
    "BOOK_FIELDS",
    "DEFAULT_BOOKS_CHUNK",
    "DEFAULT_CLEANUP",
    "DEFAULT_CLOB_URL",
    "DEFAULT_CLOSED",
    "DEFAULT_GAMMA_URL",
    "DEFAULT_HF_PATH_PATTERN",
    "DEFAULT_HF_REPO",
    "DEFAULT_MAX_OFFSET",
    "DEFAULT_PACE_S",
    "DEFAULT_PAGE_LIMIT",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TOKEN_ENV",
    "DEFAULT_USER_AGENT",
    "DEFAULT_WINDOW_HOURS",
    "EVENTS_STREAM",
    "EVENT_FIELDS",
    "FEES_STREAM",
    "FEE_SCHEDULE_FIELDS",
    "MAX_BACKOFF_S",
    "STREAMS",
    "STREAM_FIELDS",
    "STREAM_KEYS",
    "SYNC_STATE_PATH",
    "PolymarketConnector",
    "fee_rate_of",
]

STREAMS = ("events", "fee_schedules", "books", "archive_hours")
EVENTS_STREAM, FEES_STREAM, BOOKS_STREAM, ARCHIVE_STREAM = STREAMS

EVENT_FIELDS = (
    "event_slug", "event_id", "series_slug", "market_id", "slug", "question",
    "condition_id", "clob_token_ids", "outcomes", "outcome_prices", "start_date",
    "end_date", "closed", "closed_time", "fees_enabled", "fee_type", "fee_rate",
    "fee_schedule", "resolution",
)
FEE_SCHEDULE_FIELDS = (
    "series_slug", "from_end_date", "example_slug", "fees_enabled", "fee_type",
    "fee_rate", "fee_exponent", "retrieved",
)
#: The pmxt eight-column book shape, plus two provenance fields per poll.
BOOK_FIELDS = (
    "asset_id", "ts", "event_type", "bids", "asks", "price", "size", "side",
    "asof_ts_ms", "book_hash",
)
#: The same eight columns, plus the within-millisecond order (module docs).
ARCHIVE_FIELDS = BOOK_FIELDS[:8] + ("seq",)
STREAM_FIELDS = {
    EVENTS_STREAM: EVENT_FIELDS,
    FEES_STREAM: FEE_SCHEDULE_FIELDS,
    BOOKS_STREAM: BOOK_FIELDS,
    ARCHIVE_STREAM: ARCHIVE_FIELDS,
}
STREAM_KEYS = {
    EVENTS_STREAM: ("market_id",),
    FEES_STREAM: ("series_slug", "from_end_date"),
    BOOKS_STREAM: ("asset_id", "ts"),
    # Not event_type: pmxt writes one price_change per LEVEL and several
    # share a millisecond, so only the file's own order separates them.
    ARCHIVE_STREAM: ("asset_id", "ts", "seq"),
}
#: Archive event types kept; everything else the dump carries is dropped.
ARCHIVE_EVENT_TYPES = ("book", "price_change")

DEFAULT_GAMMA_URL = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_URL = "https://clob.polymarket.com"
DEFAULT_TIMEOUT_S = 45
#: Gamma answers urllib's default agent string with 403.
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; dskit-onboarding)"
DEFAULT_RETRIES = 4
DEFAULT_PACE_S = 0.2
DEFAULT_CLOSED = True
DEFAULT_WINDOW_HOURS = 6
DEFAULT_PAGE_LIMIT = 100
DEFAULT_MAX_OFFSET = 2000
DEFAULT_BOOKS_CHUNK = 50
DEFAULT_HF_REPO = "phobia76/pmxt-l2-dump"
DEFAULT_HF_PATH_PATTERN = "hours/%Y/%m/%d/polymarket_orderbook_%Y-%m-%dT%H.parquet"
DEFAULT_TOKEN_ENV = "HF_TOKEN"
DEFAULT_CLEANUP = True
#: The mirror's own bookkeeping inside the archive repo: which hours it
#: could not fetch (``pending_gap_hours``) and how far it has got
#: (``latest_available_hour``). Advisory — see the module docs.
SYNC_STATE_PATH = "meta/pmxt-polymarket-sync-state.json"

_RETRY_STATUSES = (429, 500, 502, 503, 504)
_BACKOFF_S = 0.5
_ARCHIVE_BATCH_ROWS = 65536
_GAMMA_TIME = "%Y-%m-%dT%H:%M:%SZ"
_HOUR = timedelta(hours=1)
_MS = timedelta(milliseconds=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LIST_KNOBS = ("series_slugs", "slugs", "token_ids", "hours")


# -- small pure helpers -----------------------------------------------------


def _safe(url):
    """Strip the query string off a URL bound for an error message."""
    return url.split("?", 1)[0]


def _join(base, path):
    """Join a base URL and a path with exactly one slash."""
    return base.rstrip("/") + "/" + path.lstrip("/")


def _ms(dt):
    """Epoch milliseconds of an aware datetime, exact integer arithmetic."""
    return (dt - _EPOCH) // _MS


def _iso_ms(ms):
    """ISO-8601 UTC stamp of epoch milliseconds, exact."""
    return (_EPOCH + ms * _MS).isoformat()


def _floor_hour(dt):
    """Return the hour boundary at or before ``dt``."""
    return dt.replace(minute=0, second=0, microsecond=0)


def _floor_minute(dt):
    """Return the minute boundary at or before ``dt`` — the capture instant (module docs)."""
    return dt.replace(second=0, microsecond=0)


def _text(value):
    """``str`` of a present value; ``None`` stays ``None``."""
    return None if value is None else str(value)


def _number(value, label):
    """Coerce a numeric or numeric-string value to float, or refuse by name."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AssetError([f"{label} is not a number: {value!r}"]) from exc


def _integer(value, label):
    """Coerce an int or int-string value to int; ``None`` stays ``None``."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AssetError([f"{label} is not an integer: {value!r}"]) from exc


def _int_ms(value):
    """Epoch milliseconds out of an int, digit string or ISO stamp; else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        return _ms(parse_utc(text))
    except AssetError:
        return None


def _json_value(value, label):
    """Decode a JSON-encoded string cell; non-strings pass through."""
    if not isinstance(value, str):
        return value
    if not value.strip():
        return None
    try:
        return json.loads(value)
    except ValueError as exc:
        raise AssetError([f"{label} is not JSON: {value[:80]!r}"]) from exc


def _json_list(value, label):
    """Coerce a list or JSON-encoded list to a list; absent means empty."""
    decoded = _json_value(value, label)
    if decoded is None:
        return []
    if not isinstance(decoded, list):
        raise AssetError([f"{label} must be a list, got {type(decoded).__name__}"])
    return decoded


def _levels(raw, descending):
    """Normalize book levels to ``[[price_str, size_str], ...]``, sorted.

    Accepts ``{"price", "size"}`` objects or ``[price, size]`` pairs;
    drops a level whose size is not a positive number or whose price
    does not parse. Decimal strings are kept verbatim.
    """
    kept = []
    for level in raw or []:
        if isinstance(level, dict):
            price, size = level.get("price"), level.get("size")
        elif isinstance(level, (list, tuple)) and len(level) == 2:
            price, size = level
        else:
            continue
        try:
            price_f, size_f = float(price), float(size)
        except (TypeError, ValueError):
            continue
        if size_f <= 0:
            continue
        kept.append((price_f, [str(price), str(size)]))
    kept.sort(key=lambda pair: pair[0], reverse=descending)
    return [pair for _price, pair in kept]


def _retry_after(headers):
    """Seconds a numeric Retry-After asks for, capped; None when unusable."""
    value = headers.get("Retry-After") if headers is not None else None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(seconds):
        # float() accepts "nan", and max/min pass a NaN straight through
        # to time.sleep (a ValueError, not a capped wait): unusable.
        return None
    return min(max(seconds, 0.0), MAX_BACKOFF_S)


def _backoff(attempt):
    """Exponential backoff for the ``attempt``-th failure, capped."""
    return min(_BACKOFF_S * 2 ** attempt, MAX_BACKOFF_S)


def _chunks(items, size):
    """Yield consecutive slices of ``items`` at most ``size`` long."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _record(stream, effective_date, kind, data):
    """One RECORD envelope."""
    return {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
            "effective_date": effective_date, "kind": kind, "data": data}


def _log(message):
    """One LOG envelope."""
    return {"protocol": PROTOCOL, "type": "LOG", "message": message}


def _require(stream, conditions):
    """Refuse a stream whose knob preconditions fail, all named at once."""
    problems = [message for ok, message in conditions if not ok]
    if problems:
        raise AssetError([f"stream {stream!r}: {p}" for p in problems])


def fee_rate_of(fields):
    """Decode a Polymarket fee configuration into ``(rate, exponent)``.

    The venue spells fees two ways. A ``feeSchedule`` object (sometimes a
    JSON-encoded string) carries ``rate`` as a fraction (``0.07`` is 7%)
    and an ``exponent`` (``1`` for the linear schedule); a legacy
    ``feeRateBps`` carries the rate in basis points and no exponent.
    ``feeSchedule`` wins when both are present.

    Parameters
    ----------
    fields : dict
        Fee-named keys as the venue emits them — the ``fee_schedule`` raw
        copy an ``events`` row carries.

    Returns
    -------
    tuple
        ``(rate, exponent)``: ``rate`` a float fraction, or ``None`` when
        no rate is declared; ``exponent`` an int, or ``None`` when absent.

    Raises
    ------
    AssetError
        When a present value does not decode as a number.
    """
    schedule = _json_value(fields.get("feeSchedule"), "feeSchedule")
    if isinstance(schedule, dict) and schedule.get("rate") is not None:
        return (_number(schedule["rate"], "feeSchedule.rate"),
                _integer(schedule.get("exponent"), "feeSchedule.exponent"))
    bps = fields.get("feeRateBps")
    if bps is not None and bps != "":
        return _number(bps, "feeRateBps") / 10000.0, None
    return None, None


# -- Gamma payload -> provider-shaped rows ----------------------------------


def _fee_fields(event, market):
    """Every fee-named key, the market's spelling over the event's."""
    return {key: value for source in (event, market)
            for key, value in source.items() if "fee" in key.lower()}


def _closed_time(value, label):
    """Normalize the venue's ``closedTime`` spelling to ISO; absent or blank is ``None``."""
    if value is None or not str(value).strip():
        return None
    try:
        # Gamma spells the close space-separated with a two-digit offset —
        # "2026-09-04 12:11:51+00" — beside the usual T/Z forms; fromisoformat
        # reads all three, so parse_utc IS the parser (no second spelling here).
        return parse_utc(str(value).strip()).isoformat()
    except AssetError as exc:
        raise AssetError([f"{label}: closedTime is not an instant: {value!r}"]) from exc


def _market_row(event, market, series_slug):
    """One ``events`` row for a market inside its Gamma event."""
    label = f"market {market.get('id')!r} in event {event.get('slug')!r}"
    end_date = market.get("endDate") or event.get("endDate")
    if not isinstance(end_date, str) or not end_date:
        raise AssetError([f"{label}: endDate missing — every market needs its end"])
    parse_utc(end_date)
    closed_time = _closed_time(market.get("closedTime") or event.get("closedTime"), label)
    fee = _fee_fields(event, market)
    rate, _exponent = fee_rate_of(fee)
    prices = _json_list(market.get("outcomePrices"), f"{label}: outcomePrices")
    if series_slug is None:
        series = event.get("series") or [{}]
        series_slug = series[0].get("slug") if isinstance(series[0], dict) else None
    return {
        "event_slug": event.get("slug"),
        "event_id": _text(event.get("id")),
        "series_slug": series_slug,
        "market_id": _text(market.get("id")),
        "slug": market.get("slug"),
        "question": market.get("question"),
        "condition_id": market.get("conditionId"),
        "clob_token_ids": [
            str(t) for t in _json_list(market.get("clobTokenIds"), f"{label}: clobTokenIds")
        ],
        "outcomes": _json_list(market.get("outcomes"), f"{label}: outcomes"),
        "outcome_prices": [_number(p, f"{label}: outcomePrices") for p in prices],
        "start_date": market.get("startDate"),
        "end_date": end_date,
        "closed": bool(market.get("closed", event.get("closed", False))),
        "closed_time": closed_time,
        "fees_enabled": bool(fee.get("feesEnabled", False)),
        "fee_type": fee.get("feeType"),
        "fee_rate": rate,
        "fee_schedule": fee,
        "resolution": {k: v for k, v in market.items() if "resol" in k.lower()},
    }


def _market_rows(event, series_slug):
    """Every market row of one event."""
    markets = event.get("markets") or []
    if not isinstance(markets, list):
        raise AssetError([f"event {event.get('slug')!r}: markets is not a list"])
    return [_market_row(event, m, series_slug) for m in markets if isinstance(m, dict)]


def _observed_at(row):
    """Return a closed market's observation instant: its closedTime, else its end (ADR-0080)."""
    return row["closed_time"] or row["end_date"]


def _future_closed(rows, now):
    """Name every closed market whose observation instant still lies ahead of ``now``."""
    return [
        f"market {row['market_id']!r}: closed, but its observation instant "
        f"{_observed_at(row)} is ahead of the pull {now.isoformat()} "
        f"(end_date {row['end_date']}, closed_time {row['closed_time']}) — the venue "
        "has not dated the close yet; re-pull once it has"
        for row in rows if row["closed"] and parse_utc(_observed_at(row)) > now
    ]


def _page(body, label):
    """Return the event list of a Gamma response, or refuse by shape."""
    if not isinstance(body, list) or not all(isinstance(e, dict) for e in body):
        raise AssetError([f"{label}: Gamma response is not a list of events"])
    return body


# -- archive parquet -> eight-column rows -----------------------------------


def _decode_new(raw):
    """Decode a row of the current pmxt schema (``bids``/``asks`` JSON pairs)."""
    return {
        "asset_id": _text(raw.get("asset_id")),
        "ts": _int_ms(raw.get("timestamp", raw.get("ts"))),
        "event_type": _text(raw.get("event_type")),
        "bids": _levels(_json_value(raw.get("bids"), "bids"), True),
        "asks": _levels(_json_value(raw.get("asks"), "asks"), False),
        "price": _text(raw.get("price")),
        "size": _text(raw.get("size")),
        "side": _text(raw.get("side")),
    }


def _decode_mid(raw):
    """Decode a row of the middle schema (parallel price/size arrays per side)."""
    bids = zip(raw.get("bid_prices") or [], raw.get("bid_sizes") or [])
    asks = zip(raw.get("ask_prices") or [], raw.get("ask_sizes") or [])
    return {
        "asset_id": _text(raw.get("asset_id")),
        "ts": _int_ms(raw.get("timestamp", raw.get("ts"))),
        "event_type": _text(raw.get("event_type")),
        "bids": _levels(list(bids), True),
        "asks": _levels(list(asks), False),
        "price": _text(raw.get("price")),
        "size": _text(raw.get("size")),
        "side": _text(raw.get("side")),
    }


def _decode_old(raw):
    """Decode a row of the first schema (the raw WSS message as JSON under ``data``)."""
    payload = _json_value(raw.get("data"), "data")
    if not isinstance(payload, dict):
        return None
    return {
        "asset_id": _text(payload.get("asset_id", payload.get("token_id"))),
        "ts": _int_ms(payload.get("timestamp", raw.get("timestamp"))),
        "event_type": _text(raw.get("update_type") or payload.get("event_type")),
        "bids": _levels(payload.get("bids"), True),
        "asks": _levels(payload.get("asks"), False),
        "price": _text(payload.get("price", payload.get("change_price"))),
        "size": _text(payload.get("size", payload.get("change_size"))),
        "side": _text(payload.get("side", payload.get("change_side"))),
    }


#: Column that identifies each archive schema generation -> its decoder.
_ARCHIVE_DECODERS = (("bids", _decode_new), ("bid_prices", _decode_mid),
                     ("data", _decode_old))


def _archive_decoder(names):
    """Pick the decoder for a parquet file by its column names."""
    for marker, decoder in _ARCHIVE_DECODERS:
        if marker in names:
            return decoder
    raise AssetError(
        [f"unrecognized archive schema — columns {sorted(names)}; expected one of "
         f"{[marker for marker, _d in _ARCHIVE_DECODERS]}"]
    )


def _archive_rows(path, tokens):
    """Yield the archive rows of ``tokens`` out of one parquet file, in file order.

    Batches bound memory; a string-typed ``asset_id`` column is filtered
    in Arrow before any row is materialized. ``seq`` numbers the rows one
    ``(asset_id, ts)`` carries, across batches — the within-millisecond
    order the archive recorded (module docs).
    """
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise AssetError(
            [f"stream {ARCHIVE_STREAM!r} needs pyarrow to read the archive "
             "(pip install pyarrow)"]
        ) from exc
    reader = pq.ParquetFile(path)
    names = reader.schema_arrow.names
    decode = _archive_decoder(names)
    wanted = pa.array(sorted(tokens))
    seqs = {}
    for batch in reader.iter_batches(batch_size=_ARCHIVE_BATCH_ROWS):
        if "asset_id" in names:
            kind = batch.schema.field("asset_id").type
            if pa.types.is_string(kind) or pa.types.is_large_string(kind):
                batch = batch.filter(pc.is_in(batch.column("asset_id"), value_set=wanted))
        for raw in batch.to_pylist():
            row = decode(raw)
            if row is None or row["asset_id"] not in tokens:
                continue
            # Numbered BEFORE the event-type filter, so seq is the FILE's own
            # order: widening ARCHIVE_EVENT_TYPES then adds rows instead of
            # renumbering — rewriting — the keys already stored. The token
            # filter cannot shift it: a place belongs to one asset_id.
            place = (row["asset_id"], row["ts"])
            row["seq"] = seqs.get(place, 0)
            seqs[place] = row["seq"] + 1
            if row["event_type"] not in ARCHIVE_EVENT_TYPES:
                continue
            if row["ts"] is None:
                raise AssetError([f"archive row for {row['asset_id']!r} has no timestamp"])
            yield row


def _meta_hours(value):
    """Hour boundaries out of one sync-state stamp or a list of them, plus the rejects."""
    raw = value if isinstance(value, list) else [] if value is None else [value]
    hours, ignored = set(), []
    for item in raw:
        try:
            hours.add(_floor_hour(parse_utc(item)))
        except AssetError:
            ignored.append(item)
    return hours, ignored


def _remove(path):
    """Delete a downloaded file and, for a cache symlink, its blob too."""
    for target in {os.path.realpath(path), path}:
        try:
            os.remove(target)
        except OSError:
            pass


class _Client:
    """Paced, retried HTTP for one verb invocation of the connector."""

    def __init__(self, connector, knobs):
        self._connector = connector
        self._knobs = knobs
        self._headers = {"User-Agent": knobs["user_agent"], "Accept": "application/json"}
        self._calls = 0

    def get(self, base, path, params, label):
        """GET ``base/path?params`` and return the decoded JSON body."""
        url = _join(base, path)
        return self._retrying(label, url, lambda: self._connector.get_json(
            url, params, dict(self._headers), self._knobs["timeout_s"]))

    def post(self, base, path, body, label):
        """POST a JSON ``body`` to ``base/path`` and return the decoded reply."""
        url = _join(base, path)
        return self._retrying(label, url, lambda: self._connector.post_json(
            url, body, dict(self._headers), self._knobs["timeout_s"]))

    def _pace(self):
        """Wait ``pace_s`` between consecutive requests, never before the first."""
        if self._calls and self._knobs["pace_s"] > 0:
            self._connector.sleep(self._knobs["pace_s"])
        self._calls += 1

    def _retrying(self, label, url, call):
        """Run ``call`` with backoff on 429/5xx and network faults."""
        self._pace()
        retries = self._knobs["retries"]
        last = None
        for attempt in range(retries + 1):
            try:
                return call()
            except urllib.error.HTTPError as exc:  # before OSError: it IS one
                if exc.code not in _RETRY_STATUSES:
                    raise AssetError(
                        [f"{label}: HTTP {exc.code} from {_safe(url)}"]
                    ) from exc
                last, delay = f"HTTP {exc.code}", _retry_after(exc.headers)
            except OSError as exc:
                last, delay = f"network error: {exc}", None
            if attempt < retries:
                self._connector.sleep(_backoff(attempt) if delay is None else delay)
        raise AssetError(
            [f"{label}: giving up on {_safe(url)} after {retries + 1} attempt(s) — "
             f"last failure: {last}"]
        )


class PolymarketConnector(Connector):
    """Polymarket Gamma events, fee regimes, CLOB books and the pmxt archive.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Discover the four streams without touching the network::

        connector = PolymarketConnector()
        streams = connector.discover({"token_ids": ["7136..."]})
        [s["stream"] for s in streams]
        # -> ['events', 'fee_schedules', 'books', 'archive_hours']
    """

    def spec(self):
        """Declare the default-deny Polymarket configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations; every knob is stream-scoped, so
            none is globally required — ``read`` names what a stream needs.
        """
        return {"params": {
            "series_slugs": {
                "notes": "Gamma series slugs whose events the `events` and "
                         "`fee_schedules` streams walk, windowed by `start`/`end`.",
            },
            "slugs": {
                "notes": "Explicit event slugs looked up one by one "
                         "(`GET /events?slug=`) for the `events` stream; no window.",
            },
            "start": {
                "notes": "ISO lower bound: on end_date for series walks, on the "
                         "hour for `archive_hours` when `hours` is absent.",
            },
            "end": {
                "notes": "ISO upper bound; default the pull instant. A window "
                         "reaching past the pull is fine: a closed market dates at "
                         "the venue's `closedTime` and an open one is a forecast. "
                         "Only a closed market the venue has not dated, whose "
                         "scheduled end is still ahead, is refused (by name).",
            },
            "closed": {
                "notes": f"Gamma `closed` filter for series walks; default "
                         f"{DEFAULT_CLOSED}. Open markets (closed: false) are emitted "
                         "kind=forecast and never move the cursor.",
            },
            "window_hours": {
                "notes": "Sub-window width (hours) for series walks; default "
                         f"{DEFAULT_WINDOW_HOURS}. Narrow it when a window exceeds "
                         "`max_offset`.",
            },
            "page_limit": {
                "notes": f"Events per Gamma page; default {DEFAULT_PAGE_LIMIT}.",
            },
            "max_offset": {
                "notes": "Deepest page offset a window may need before the pull "
                         f"refuses; default {DEFAULT_MAX_OFFSET}. The remedy is a "
                         "narrower `window_hours`, never a deeper walk.",
            },
            "token_ids": {
                "notes": "CLOB token ids (strings) the `books` and `archive_hours` "
                         "streams cover. `archive_hours` may omit them: it then "
                         "resolves them from `series_slugs`/`slugs` over the same "
                         "window, through the very walk `events` uses, and refuses "
                         "when that resolves no market. Declared ids WIN — resolution "
                         "runs only when none are declared, never as a union. A "
                         "series walk carries the `closed` filter, so under its "
                         f"default ({DEFAULT_CLOSED}) a market that has not settled "
                         "resolves no token: declare the ids to backfill an open "
                         "market's hours.",
            },
            "books_chunk": {
                "notes": f"Token ids per `POST /books` call; default {DEFAULT_BOOKS_CHUNK}.",
            },
            "hours": {
                "notes": "Explicit ISO hour stamps for `archive_hours`; takes "
                         "precedence over `start`/`end`. Hours at or before the "
                         "cursor are skipped, and so is an hour the mirror lists in "
                         "`pending_gap_hours` — an explicit list steps over anything "
                         "it leaves out, but never over a gap the mirror declares, "
                         "and it still stops at the mirror's latest_available_hour.",
            },
            "hf_repo": {
                "notes": f"Hugging Face dataset repo of the archive; default "
                         f"{DEFAULT_HF_REPO}. Its {SYNC_STATE_PATH} is read before "
                         "each walk to tell a permanent gap from an hour not mirrored "
                         "yet; absent or unreadable, the walk proceeds without it.",
            },
            "hf_path_pattern": {
                "notes": "strftime pattern of one hour file inside the repo; default "
                         f"{DEFAULT_HF_PATH_PATTERN}.",
            },
            "token_env": {
                "secret": True,
                "notes": "Environment-variable NAME holding a hub token, read at pull "
                         f"time; default {DEFAULT_TOKEN_ENV}. Optional: the archive is "
                         "public, so unset means anonymous.",
            },
            "cleanup": {
                "notes": "Delete each hour file (~360 MB; the cache symlink and its "
                         f"blob) after filtering; default {DEFAULT_CLEANUP}.",
            },
            "gamma_url": {
                "notes": f"Gamma API root; default {DEFAULT_GAMMA_URL}.",
            },
            "clob_url": {
                "notes": f"CLOB API root; default {DEFAULT_CLOB_URL}.",
            },
            "timeout_s": {
                "notes": f"Socket timeout in seconds; default {DEFAULT_TIMEOUT_S}.",
            },
            "user_agent": {
                "notes": f"Request User-Agent; default {DEFAULT_USER_AGENT}. Gamma "
                         "answers urllib's own agent string with 403.",
            },
            "retries": {
                "notes": "Extra attempts on 429/5xx and network faults, with "
                         "backoff honoring a numeric Retry-After, every wait "
                         f"capped at {MAX_BACKOFF_S:g} s; default {DEFAULT_RETRIES}.",
            },
            "pace_s": {
                "notes": f"Seconds between consecutive requests; default "
                         f"{DEFAULT_PACE_S}.",
            },
        }}

    # -- knobs -----------------------------------------------------------

    def resolve_knobs(self, config):
        """Validate config values and apply the pack's defaults.

        Parameters
        ----------
        config : dict
            Connector configuration after platform-reserved keys are
            removed.

        Returns
        -------
        dict
            Fully resolved knobs, every declared knob present.

        Raises
        ------
        AssetError
            Listing all malformed values.
        """
        if not isinstance(config, dict):
            raise AssetError([f"config must be a dict, got {type(config).__name__}"])
        problems = []
        knobs = {
            "series_slugs": self._str_list(problems, config, "series_slugs"),
            "slugs": self._str_list(problems, config, "slugs"),
            "token_ids": self._str_list(problems, config, "token_ids"),
            "hours": self._str_list(problems, config, "hours"),
            "start": self._iso(problems, config, "start"),
            "end": self._iso(problems, config, "end"),
            "closed": self._flag(problems, config, "closed", DEFAULT_CLOSED),
            "cleanup": self._flag(problems, config, "cleanup", DEFAULT_CLEANUP),
            "window_hours": self._positive(problems, config, "window_hours",
                                           DEFAULT_WINDOW_HOURS, float),
            "timeout_s": self._positive(problems, config, "timeout_s",
                                        DEFAULT_TIMEOUT_S, float),
            "page_limit": self._positive(problems, config, "page_limit",
                                         DEFAULT_PAGE_LIMIT, int),
            "books_chunk": self._positive(problems, config, "books_chunk",
                                          DEFAULT_BOOKS_CHUNK, int),
            "max_offset": self._positive(problems, config, "max_offset",
                                         DEFAULT_MAX_OFFSET, int, floor=0),
            "retries": self._positive(problems, config, "retries",
                                      DEFAULT_RETRIES, int, floor=0),
            "pace_s": self._positive(problems, config, "pace_s",
                                     DEFAULT_PACE_S, float, floor=0),
            "gamma_url": self._url(problems, config, "gamma_url", DEFAULT_GAMMA_URL),
            "clob_url": self._url(problems, config, "clob_url", DEFAULT_CLOB_URL),
            "user_agent": self._word(problems, config, "user_agent", DEFAULT_USER_AGENT),
            "hf_repo": self._word(problems, config, "hf_repo", DEFAULT_HF_REPO),
            "hf_path_pattern": self._word(problems, config, "hf_path_pattern",
                                          DEFAULT_HF_PATH_PATTERN),
            "token_env": self._word(problems, config, "token_env", DEFAULT_TOKEN_ENV),
        }
        for stamp in knobs["hours"]:
            try:
                if parse_utc(stamp) != _floor_hour(parse_utc(stamp)):
                    problems.append(f"config.hours entry {stamp!r} is not on an hour boundary")
            except AssetError:
                problems.append(f"config.hours entry {stamp!r} is not an ISO stamp")
        if knobs["start"] and knobs["end"] and parse_utc(knobs["end"]) <= parse_utc(knobs["start"]):
            problems.append(
                f"config.end {knobs['end']!r} must be after config.start {knobs['start']!r}"
            )
        if problems:
            raise AssetError(problems)
        return knobs

    def _str_list(self, problems, config, name):
        """Read a list of non-empty strings; absent means empty."""
        value = config.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            problems.append(f"config.{name} must be a list of non-empty strings, got {value!r}")
            return []
        return list(value)

    def _iso(self, problems, config, name):
        """Read an optional ISO stamp verbatim; absent means ``""``."""
        value = config.get(name, "")
        if value == "":
            return ""
        if not isinstance(value, str):
            problems.append(f"config.{name} must be an ISO string, got {value!r}")
            return ""
        try:
            parse_utc(value)
        except AssetError:
            problems.append(f"config.{name} must be an ISO date/datetime, got {value!r}")
            return ""
        return value

    def _flag(self, problems, config, name, default):
        """Read a real boolean."""
        value = config.get(name, default)
        if not isinstance(value, bool):
            problems.append(f"config.{name} must be true or false, got {value!r}")
        return value

    def _positive(self, problems, config, name, default, kind, floor=None):
        """Read a number of ``kind`` above zero, or at or above ``floor`` when given."""
        value = config.get(name, default)
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if kind is int and (not isinstance(value, int) or isinstance(value, bool)):
            problems.append(f"config.{name} must be an int, got {value!r}")
        elif not numeric or (value <= 0 if floor is None else value < floor):
            bound = "> 0" if floor is None else f">= {floor}"
            problems.append(f"config.{name} must be a number {bound}, got {value!r}")
        return value

    def _url(self, problems, config, name, default):
        """Read an http(s) root URL."""
        value = config.get(name, default)
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            problems.append(f"config.{name} must be an http(s) URL, got {value!r}")
        return value

    def _word(self, problems, config, name, default):
        """Read a non-empty string."""
        value = config.get(name, default)
        if not isinstance(value, str) or not value:
            problems.append(f"config.{name} must be a non-empty string, got {value!r}")
        return value

    # -- the seams -------------------------------------------------------

    def get_json(self, url, params, headers, timeout_s):
        """Fetch one JSON document over HTTP GET; the default is stdlib urllib.

        Override to inject a transport. A failed status must surface as
        ``urllib.error.HTTPError`` (the retry loop reads its code and
        ``Retry-After``) and a network fault as ``OSError``.

        Parameters
        ----------
        url : str
            Absolute URL without a query string.
        params : dict
            Query parameters, already spelled the way the venue wants
            (lowercase booleans).
        headers : dict
            Request headers, the browser-like ``User-Agent`` among them.
        timeout_s : float
            Socket timeout in seconds.

        Returns
        -------
        object
            The decoded JSON body.

        Raises
        ------
        AssetError
            When the body is not JSON.
        """
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(full, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read()
        return self._decode(body, url)

    def post_json(self, url, body, headers, timeout_s):
        """Send one JSON body over HTTP POST; the default is stdlib urllib.

        Same override and failure contract as :meth:`get_json`.

        Parameters
        ----------
        url : str
            Absolute URL.
        body : object
            JSON-serializable request body.
        headers : dict
            Request headers; ``Content-Type`` is added here.
        timeout_s : float
            Socket timeout in seconds.

        Returns
        -------
        object
            The decoded JSON reply.

        Raises
        ------
        AssetError
            When the reply is not JSON.
        """
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            reply = response.read()
        return self._decode(reply, url)

    def _decode(self, body, url):
        """Parse a response body as JSON or refuse, naming the endpoint."""
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AssetError([f"response from {_safe(url)} is not JSON: {exc}"]) from exc

    def download(self, repo, path, token):
        """Fetch one archive file from a Hugging Face dataset repository.

        The default imports ``huggingface_hub`` here and nowhere else;
        override to inject a fetcher.

        Parameters
        ----------
        repo : str
            Dataset repository id, e.g. ``phobia76/pmxt-l2-dump``.
        path : str
            File path inside the repository.
        token : str or None
            Hub token, or ``None`` for anonymous access.

        Returns
        -------
        str or None
            Local path of the downloaded file (a hub-cache symlink into
            the blob store), or ``None`` when the repository holds no
            such file.

        Raises
        ------
        AssetError
            When ``huggingface_hub`` is not installed, when the hub is
            unreachable and the file is not cached (never ``None`` — an
            outage is not an absent hour), or when the hub refuses the
            request. The token is never in the text.
        """
        try:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.utils import (
                EntryNotFoundError,
                HfHubHTTPError,
                LocalEntryNotFoundError,
            )
        except ImportError as exc:
            raise AssetError(
                [f"stream {ARCHIVE_STREAM!r} needs the optional huggingface_hub "
                 "package to fetch the archive (pip install huggingface_hub); it is "
                 "imported only here, so every other stream works without it"]
            ) from exc
        label = f"stream {ARCHIVE_STREAM!r}: {path} in {repo}"
        try:
            return hf_hub_download(repo_id=repo, filename=path, repo_type="dataset",
                                   token=token)
        except LocalEntryNotFoundError as exc:  # before EntryNotFoundError: it IS one
            raise AssetError(
                [f"{label}: the hub is unreachable and the file is not cached — the "
                 "hour may well exist, so check the connection rather than stepping "
                 "over it"]
            ) from exc
        except EntryNotFoundError:
            return None
        except HfHubHTTPError as exc:
            raise AssetError([f"{label}: the hub refused the request — {exc}"]) from exc

    def sleep(self, seconds):
        """Wait ``seconds``; pacing and backoff both come through here.

        Parameters
        ----------
        seconds : float
            How long to block.
        """
        time.sleep(seconds)

    def now(self):
        """Return the pull instant as an aware UTC datetime.

        Returns
        -------
        datetime.datetime
            Aware, in UTC; override to fix it in a test.
        """
        return datetime.now(timezone.utc)

    def _capture(self):
        """Return the pull's capture instant: ``now`` floored to the minute (module docs)."""
        return _floor_minute(self.now())

    # -- Gamma walks -----------------------------------------------------

    def _walk_events(self, client, knobs, series_slug):
        """Yield every Gamma event of one series across the declared window."""
        label = f"stream {EVENTS_STREAM!r}"
        start = parse_utc(knobs["start"])
        end = parse_utc(knobs["end"]) if knobs["end"] else self.now()
        step = timedelta(hours=knobs["window_hours"])
        low = start
        while low < end:
            high = min(low + step, end)
            offset = 0
            while True:
                if offset > knobs["max_offset"]:
                    raise AssetError(
                        [f"{label}: series {series_slug!r} window "
                         f"{low.strftime(_GAMMA_TIME)}..{high.strftime(_GAMMA_TIME)} needs "
                         f"offset {offset} > max_offset {knobs['max_offset']} — narrow "
                         "window_hours"]
                    )
                params = {
                    "closed": "true" if knobs["closed"] else "false",
                    "series_slug": series_slug,
                    "end_date_min": low.strftime(_GAMMA_TIME),
                    "end_date_max": high.strftime(_GAMMA_TIME),
                    "order": "endDate", "ascending": "true",
                    "limit": knobs["page_limit"], "offset": offset,
                }
                page = _page(client.get(knobs["gamma_url"], "/events", params, label), label)
                yield from page
                if len(page) < knobs["page_limit"]:
                    break
                offset += knobs["page_limit"]
            low = high

    def _lookup_events(self, client, knobs, slug):
        """Fetch the event(s) one explicit slug names; refuse an empty answer."""
        label = f"stream {EVENTS_STREAM!r}"
        page = _page(client.get(knobs["gamma_url"], "/events", {"slug": slug}, label), label)
        if not page:
            raise AssetError([f"{label}: slug {slug!r} returned no event"])
        return page

    def _series_rows(self, client, knobs, series_slug):
        """Deduplicated market rows of one series, in end_date order."""
        rows = {}
        for event in self._walk_events(client, knobs, series_slug):
            for row in _market_rows(event, series_slug):
                rows.setdefault(row["market_id"], row)
        return sorted(rows.values(), key=lambda r: (parse_utc(r["end_date"]), r["market_id"]))

    # -- the pullers: each yields RECORD/LOG messages, returns the cursor -

    def _pull_events(self, client, knobs, cursor):
        """One RECORD per market, never cursor-filtered; a closed row dates at its closedTime."""
        _require(EVENTS_STREAM, [
            (knobs["series_slugs"] or knobs["slugs"], "declare series_slugs and/or slugs"),
            (not knobs["series_slugs"] or knobs["start"],
             "start is required to window a series walk"),
        ])
        rows = {}
        for series_slug in knobs["series_slugs"]:
            for row in self._series_rows(client, knobs, series_slug):
                rows.setdefault(row["market_id"], row)
        for slug in knobs["slugs"]:
            for event in self._lookup_events(client, knobs, slug):
                for row in _market_rows(event, None):
                    rows.setdefault(row["market_id"], row)
        cursor_dt = parse_utc(cursor) if cursor else None
        emitted, emitted_dt = cursor, cursor_dt
        ordered = sorted(rows.values(), key=lambda r: (parse_utc(r["end_date"]), r["market_id"]))
        ahead = _future_closed(ordered, self.now())
        if ahead:
            raise AssetError(ahead)
        for row in ordered:
            if not row["closed"]:
                yield _record(EVENTS_STREAM, row["end_date"], "forecast", row)
                continue
            effective = _observed_at(row)
            effective_dt = parse_utc(effective)
            yield _record(EVENTS_STREAM, effective, "observation", row)
            if emitted_dt is None or effective_dt > emitted_dt:
                emitted, emitted_dt = effective, effective_dt
        return emitted

    def _pull_fees(self, client, knobs, cursor):
        """One RECORD per distinct fee regime per series, re-derived whole each pull."""
        _require(FEES_STREAM, [
            (knobs["series_slugs"], "declare series_slugs"),
            (knobs["start"], "start is required to window a series walk"),
        ])
        retrieved = self._capture().isoformat()
        for series_slug in knobs["series_slugs"]:
            seen = set()
            for row in self._series_rows(client, knobs, series_slug):
                rate, exponent = fee_rate_of(row["fee_schedule"])
                regime = (row["fees_enabled"], row["fee_type"], rate, exponent)
                if regime in seen:
                    continue
                seen.add(regime)
                yield _record(FEES_STREAM, retrieved, "observation", {
                    "series_slug": series_slug,
                    "from_end_date": row["end_date"],
                    "example_slug": row["slug"],
                    "fees_enabled": row["fees_enabled"],
                    "fee_type": row["fee_type"],
                    "fee_rate": rate,
                    "fee_exponent": exponent,
                    "retrieved": retrieved,
                })
        return retrieved

    def _book_row(self, book, poll_ms):
        """One ``books`` row: the pmxt shape plus poll provenance."""
        ts = _int_ms(book.get("timestamp"))
        return {
            "asset_id": str(book["asset_id"]),
            "ts": poll_ms if ts is None else ts,
            "event_type": "book",
            "bids": _levels(book.get("bids"), True),
            "asks": _levels(book.get("asks"), False),
            "price": None,
            "size": None,
            "side": None,
            "asof_ts_ms": poll_ms,
            "book_hash": book.get("hash"),
        }

    def _pull_books(self, client, knobs, cursor):
        """One RECORD per token per poll; the cursor is the max book stamp."""
        _require(BOOKS_STREAM, [(knobs["token_ids"], "declare token_ids")])
        label = f"stream {BOOKS_STREAM!r}"
        poll_ms = _ms(self.now())
        rows = []
        for chunk in _chunks(knobs["token_ids"], knobs["books_chunk"]):
            body = [{"token_id": token} for token in chunk]
            books = client.post(knobs["clob_url"], "/books", body, label)
            if not isinstance(books, list) or not all(isinstance(b, dict) for b in books):
                raise AssetError([f"{label}: CLOB response is not a list of books"])
            found = {}
            for book in books:
                if book.get("asset_id") is None:
                    raise AssetError([f"{label}: a book without asset_id came back"])
                found[str(book["asset_id"])] = book
            missing = [token for token in chunk if token not in found]
            if missing:
                yield _log(f"{label}: no book returned for token(s) {missing}")
            rows.extend(self._book_row(found[t], poll_ms) for t in chunk if t in found)
        rows.sort(key=lambda r: (r["ts"], r["asset_id"]))
        capture_ms = _ms(self._capture())
        cursor_ms = _ms(parse_utc(cursor)) if cursor else None
        emitted_ms = cursor_ms
        for row in rows:
            if cursor_ms is not None and row["ts"] <= cursor_ms:
                continue
            # ts stays the venue's stamp (the key); the effective date is
            # capped at the capture minute so one pull is one capture
            # (module docs) — acquired_at itself is the commit instant.
            effective = _iso_ms(min(row["ts"], capture_ms))
            yield _record(BOOKS_STREAM, effective, "observation", row)
            if emitted_ms is None or row["ts"] > emitted_ms:
                emitted_ms = row["ts"]
        return cursor if emitted_ms is None else _iso_ms(emitted_ms)

    def _archive_hours(self, knobs):
        """List the hours to walk: the explicit list, else complete hours in the window."""
        if knobs["hours"]:
            return sorted({parse_utc(stamp) for stamp in knobs["hours"]})
        hour = _floor_hour(parse_utc(knobs["start"]))
        end = parse_utc(knobs["end"]) if knobs["end"] else self.now()
        hours = []
        while hour + _HOUR <= end:
            hours.append(hour)
            hour += _HOUR
        return hours

    def _archive_tokens(self, client, knobs):
        """Return the token set to filter the archive to: declared ids, else Gamma's.

        Declared ``token_ids`` WIN outright — the resolution runs only
        when none are declared, and the two sets are never unioned.

        Parameters
        ----------
        client : _Client
            The paced HTTP client of this read.
        knobs : dict
            Resolved knobs.

        Returns
        -------
        set
            CLOB token id strings.

        Raises
        ------
        AssetError
            When nothing is declared and the walk resolves no market.
        """
        if knobs["token_ids"]:
            return set(knobs["token_ids"])
        tokens = set()
        for series_slug in knobs["series_slugs"]:
            for row in self._series_rows(client, knobs, series_slug):
                tokens.update(row["clob_token_ids"])
        for slug in knobs["slugs"]:
            for event in self._lookup_events(client, knobs, slug):
                for row in _market_rows(event, None):
                    tokens.update(row["clob_token_ids"])
        if not tokens:
            raise AssetError(
                [f"stream {ARCHIVE_STREAM!r}: no markets resolved for series_slugs "
                 f"{knobs['series_slugs']} / slugs {knobs['slugs']} over "
                 f"{knobs['start'] or 'the start'}..{knobs['end'] or 'the pull instant'} "
                 "— widen the window or declare token_ids"]
            )
        return tokens

    def _sync_state(self, knobs, token):
        """Read the mirror's sync state; LOG whatever it does not tell us.

        Parameters
        ----------
        knobs : dict
            Resolved knobs; ``hf_repo`` names the dataset.
        token : str or None
            Hub token, exactly as an hour fetch gets it.

        Yields
        ------
        dict
            A LOG when the document is absent or unreadable, and one
            naming stamps that do not parse. Never a refusal: the
            mirror's own bookkeeping must not be able to stop a pull.

        Returns
        -------
        tuple
            ``(gap_hours, latest_hour)`` — the hours the mirror could not
            fetch as a set of boundaries, and the newest hour it holds
            (``None`` when it does not say).
        """
        label = f"stream {ARCHIVE_STREAM!r}"
        local = self.download(knobs["hf_repo"], SYNC_STATE_PATH, token)
        state = None
        if local is not None:
            try:
                with open(local, encoding="utf-8") as handle:
                    state = json.load(handle)
            except (OSError, UnicodeDecodeError, ValueError):
                state = None
        if not isinstance(state, dict):
            yield _log(
                f"{label}: {SYNC_STATE_PATH} is absent or unreadable in "
                f"{knobs['hf_repo']} — walking every hour and stopping at the first "
                "one the mirror does not serve"
            )
            return set(), None
        gaps, ignored = _meta_hours(state.get("pending_gap_hours"))
        latest, unparsed = _meta_hours(state.get("latest_available_hour"))
        ignored += unparsed
        if ignored:
            yield _log(
                f"{label}: {SYNC_STATE_PATH} carries hour stamp(s) that do not parse: "
                f"{ignored} — ignoring them, the rest of the document still counts"
            )
        return gaps, max(latest, default=None)

    def _pull_archive(self, client, knobs, cursor):
        """One RECORD per archive row; the cursor is the last hour walked.

        Tokens are the declared ones or Gamma's
        (:meth:`_archive_tokens`); the mirror's sync state
        (:meth:`_sync_state`) decides what a missing hour MEANS — a
        listed gap is stepped over, an hour it has not reached stops the
        walk (module docs).
        """
        _require(ARCHIVE_STREAM, [
            (knobs["token_ids"] or knobs["series_slugs"] or knobs["slugs"],
             "declare token_ids, or series_slugs/slugs to resolve them from Gamma"),
            (knobs["hours"] or knobs["start"], "declare hours, or start (and end)"),
            (knobs["token_ids"] or not knobs["series_slugs"] or knobs["start"],
             "start is required to window the series walk that resolves token_ids"),
        ])
        cursor_dt = parse_utc(cursor) if cursor else None
        token = os.environ.get(knobs["token_env"]) or None
        tokens = self._archive_tokens(client, knobs)
        gaps, latest = yield from self._sync_state(knobs, token)
        emitted = cursor
        for hour in self._archive_hours(knobs):
            if cursor_dt is not None and hour <= cursor_dt:
                continue
            if hour in gaps:
                yield _log(
                    f"stream {ARCHIVE_STREAM!r}: {hour.isoformat()} is one of the "
                    "mirror's pending_gap_hours — it will never arrive, so the walk "
                    "skips it and the cursor advances past it"
                )
                emitted = hour.isoformat()
                continue
            if latest is not None and hour > latest:
                yield _log(
                    f"stream {ARCHIVE_STREAM!r}: {hour.isoformat()} is past the "
                    f"mirror's latest_available_hour {latest.isoformat()} — not "
                    f"mirrored yet; stopping so the cursor stays at "
                    f"{emitted or 'the start'} and the hour is retried next pull"
                )
                break
            path = hour.strftime(knobs["hf_path_pattern"])
            local = self.download(knobs["hf_repo"], path, token)
            if local is None:
                yield _log(
                    f"stream {ARCHIVE_STREAM!r}: {path} is absent from {knobs['hf_repo']}; "
                    f"stopping so the cursor stays at {emitted or 'the start'} and the "
                    "hour is retried next pull (list `hours` explicitly to step over it)"
                )
                break
            try:
                for row in _archive_rows(local, tokens):
                    yield _record(ARCHIVE_STREAM, _iso_ms(row["ts"]), "observation", row)
            finally:
                if knobs["cleanup"]:
                    _remove(local)
            emitted = hour.isoformat()
        return emitted

    _PULLERS = {
        EVENTS_STREAM: _pull_events,
        FEES_STREAM: _pull_fees,
        BOOKS_STREAM: _pull_books,
        ARCHIVE_STREAM: _pull_archive,
    }

    # -- the four verbs --------------------------------------------------

    def check(self, config):
        """Validate config and ping Gamma once.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means the venue answered with an event list.

        Raises
        ------
        AssetError
            If config is malformed or the probe fails.
        """
        knobs = self.resolve_knobs(config)
        client = _Client(self, knobs)
        _page(client.get(knobs["gamma_url"], "/events", {"limit": 1}, "check"), "check")

    def discover(self, config):
        """Describe the four streams without touching the network.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One declaration per stream, in :data:`STREAMS` order.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        self.resolve_knobs(config)
        return [{
            "stream": stream,
            "schema": {"fields": list(STREAM_FIELDS[stream])},
            "primary_key": list(STREAM_KEYS[stream]),
        } for stream in STREAMS]

    def read(self, config, streams, state, mode):
        """Emit SCHEMA, records and LOGs per stream, then one STATE.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams, any of :data:`STREAMS`.
        state : dict
            Prior checkpoint: ``{stream: {"cursor": <ISO>}}``.
        mode : str
            ``backfill`` or ``live``; the pack does not branch on it —
            the platform keys checkpoints per mode.

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            If arguments, config, a stream's preconditions, or a venue
            request fail.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        knobs = self.resolve_knobs(config)
        new_state = {key: dict(value) for key, value in state.items()}
        client = _Client(self, knobs)
        for stream in streams:
            puller = self._PULLERS.get(stream)
            if puller is None:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {list(STREAMS)}"]
                )
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": {"fields": list(STREAM_FIELDS[stream])}}
            cursor = state.get(stream, {}).get("cursor", "")
            emitted = yield from puller(self, client, knobs, cursor)
            new_state.setdefault(stream, {})["cursor"] = emitted
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

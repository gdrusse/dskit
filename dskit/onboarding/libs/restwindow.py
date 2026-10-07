"""``restwindow`` — declarative time-window REST connector (stdlib urllib; ADR-0240).

Some public JSON APIs serve history only as a time WINDOW: ``start`` and
``end`` query parameters, a cap on the rows a request may return, rows
that are positional arrays rather than objects, instants that are epoch
numbers, and sometimes a record list that sits under a key named after
the thing asked for. ``restapi`` paginates by token, page or offset and
wants object rows with an ISO string, so none of that fits it. This pack
declares those four shapes in config. It is STANDALONE: it subclasses
nothing in ``restapi`` and imports only the public names of ``base`` and
``connector``, so either pack can change inside without breaking the
other. Its messages are ``restapi``-shaped: ``SCHEMA``, rows sorted by
effective instant, one ``STATE`` whose per-stream ``cursor`` is the max
instant emitted. No credential: the endpoints it targets are public.

Config knobs (default-deny, per ``spec()``):

- ``base_url`` (required) — ``http(s)://…`` root; stream paths append.
- ``streams`` (required) — ``{name: declaration}``; each declaration is
  default-deny too:

  - ``path`` (required; no query string), ``params`` (static query
    params: strings or numbers), ``schema``, ``primary_key``, ``notes``.
  - ``effective_field`` (required) — the row key that holds the ISO
    effective instant. With ``epoch`` the connector WRITES it.
  - ``records_path`` — dot-path to the record list in the response body;
    absent means the body IS the list. One ``*`` is allowed, as the last
    segment: it matches the one key of that object whose value is a list
    (``result.*`` for ``{"result": {"<pair>": [...], "last": "..."}}``);
    no list, or several, refuse.
  - ``row_fields`` — names for the positions of an array row; the row
    must have exactly that many items. Without it a row must be an
    object, and with it a row must be an array.
  - ``epoch`` — ``{"field": <row key>, "unit": "s" | "ms"}``: the row
    key holds an epoch number; the ISO instant is written into
    ``effective_field`` beside it (the vendor's own value is kept).
  - ``pagination`` — ``{"strategy": …}``, a closed vocabulary:
    ``none`` (default; one request); ``window`` and ``cursor`` below.
- ``timeout`` — request timeout in seconds (default 30).
- ``max_retries`` — extra attempts on 429/5xx/network errors with
  exponential backoff (default 3); every single wait, a server's
  ``Retry-After`` included, is capped at ``MAX_BACKOFF_S`` seconds.
- ``pace_s`` — seconds slept between requests, never before the first
  (default 0).

``window`` — one request per window of ``step`` seconds, the instants
sent as ``start_param`` / ``end_param`` in ``time_format`` (``iso`` =
``YYYY-MM-DDTHH:MM:SSZ``, ``epoch_s``, ``epoch_ms``; default ``iso``).
``start`` is required; ``end`` is optional and means "now" (the clock,
sampled once per ``read``, to the whole second) minus ``lag`` seconds
(default 0). ``start``, ``end`` and ``step`` are whole seconds. The span
is ``[start, end)``, and once a stream has a checkpoint the walk begins
at the later of ``start`` and the checkpoint's second. Windows are
contiguous and half-open: each response is CLIPPED to its own window
``[lo, hi)``, so a vendor whose end bound is inclusive does not store
the shared row twice, and a row outside the span is dropped. Size
``step`` so a response fits the vendor's row cap (a vendor that counts
an inclusive end wants one row less). ``max_windows`` (default 1000) is
the refuse-not-truncate cap: a span that needs more windows refuses
BEFORE any request is made. ``truncated_path`` is a dot-path whose
non-empty value means the vendor cut the response short; that window is
refused rather than stored partial. ``lag`` exists for rows that are
still forming (the current candle): with no ``end``, the span stops
``lag`` seconds early, so a row is stored only once it is final — a
later pull cannot correct a row the checkpoint has already passed.

``cursor`` — a token read from each response at ``path`` is sent back as
query param ``param`` until a page comes back EMPTY, SHORT or carries no
token. ``page_size`` declares the vendor's full page (with ``size_param``
it is also sent, so the value is written once): a page of fewer rows is
the last, which is how a vendor that echoes its cursor at the head of its
data (it answers the final token with the final row again) is told apart
from a stuck one. ``start`` (with ``time_format``) renders the FIRST
request's value as the later of ``start`` and the checkpoint, rounded down
to the second so no row is skipped; without ``time_format`` the first
request carries no token. A token that repeats while rows still arrive,
and the page is not short, refuses (a loop or a misspelled ``param`` the
server ignores), and so does a walk that passes ``max_pages`` (default
500) with more to fetch. A vendor whose token is inclusive returns the
boundary row again at the top of the next page; a row identical to one on
the page just before is dropped as that repeat.

Cursor semantics are ``restapi``'s exactly: state maps stream ->
``{"cursor": <max effective instant emitted>}``; rows at or before it are
filtered client-side regardless of what the server returned; pages are
buffered and sorted by instant before emission, so the checkpoint
("everything before this is durable") stays honest. A failed request
abandons the pull: no STATE is ever emitted for it.

Transport. Every request goes through one injectable
``getter(url, params) -> decoded JSON``; pacing, retry and the walks sit
above it, so a scripted getter exercises all of them with no network and
no mock library. The default getter is stdlib urllib. Error messages name
the stream and window and carry the URL with its query stripped.

Import cost: stdlib only.
"""

import abc
import copy
import functools
import http.client
import json
import math
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

from ..base import MODES, AssetError, parse_utc
from ..connector import MAX_BACKOFF_S, PROTOCOL, Connector, backoff, retry_after, safe_url

__all__ = [
    "DEFAULT_LAG_S",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MAX_WINDOWS",
    "DEFAULT_PACE_S",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TIME_FORMAT",
    "RestWindowConnector",
]

#: Defaults, each named ONCE. The module docstring states the ones a reader
#: needs as static prose and ``spec()`` interpolates them; the tests pin
#: both against these names.
DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_PACE_S = 0
DEFAULT_MAX_WINDOWS = 1000
DEFAULT_MAX_PAGES = 500
DEFAULT_LAG_S = 0
DEFAULT_TIME_FORMAT = "iso"

#: HTTP statuses worth retrying — throttling and transient server faults.
_RETRY_STATUSES = (429, 500, 502, 503, 504)

#: Sent on every default-transport request; some public endpoints refuse a
#: request that names no client.
_USER_AGENT = "dskit-onboarding"

#: The pagination of a stream that declares none.
_NO_PAGINATION = {"strategy": "none"}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UNITS = {"s": timedelta(seconds=1), "ms": timedelta(milliseconds=1)}

_STREAM_KEYS = ("path", "params", "records_path", "row_fields", "epoch",
                "effective_field", "pagination", "primary_key", "schema", "notes")
_EPOCH_KEYS = ("field", "unit", "notes")


def _pluck(obj, path):
    """Follow a dot-path through nested dicts; None where any hop is absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class _Problems:
    """Collects every shape problem so a bad config reports them all at once."""

    def __init__(self):
        self.items = []

    def add(self, text):
        self.items.append(text)

    def raise_if(self):
        if self.items:
            raise AssetError(list(self.items))

    def mapping(self, where, value):
        if not isinstance(value, dict):
            self.add(f"{where} must be a dict, got {value!r}")
            return False
        return True

    def text(self, where, value):
        if not isinstance(value, str) or not value:
            self.add(f"{where} must be a non-empty string, got {value!r}")
            return False
        return True

    def whole(self, where, value, least):
        if isinstance(value, bool) or not isinstance(value, int) or value < least:
            self.add(f"{where} must be an int >= {least}, got {value!r}")
            return False
        return True

    def real(self, where, value, phrase, accepts):
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
              and (not isinstance(value, float) or math.isfinite(value)) and accepts(value))
        if not ok:
            self.add(f"{where} must be {phrase}, got {value!r}")
        return ok

    def choice(self, where, value, options):
        if not isinstance(value, str) or value not in options:
            self.add(f"{where} must be one of {sorted(options)}, got {value!r}")
            return False
        return True

    def unknown(self, where, mapping, allowed):
        extra = sorted(set(mapping) - set(allowed))
        if extra:
            self.add(f"{where}: unknown key(s) {extra} — allowed: {sorted(allowed)}")

    def instant(self, where, value):
        """Parse ``value`` as a whole-second UTC instant; None, with a problem recorded, if not."""
        try:
            moment = parse_utc(value)
        except AssetError:
            moment = None
        if moment is None or moment.microsecond:
            self.add(f"{where} must be an ISO instant in whole seconds, got {value!r}")
            return None
        return moment


# -- time formats: how a window bound is spelled on the wire ---------------------


class _TimeFormat(abc.ABC):
    """One spelling of an instant in a request parameter."""

    @abc.abstractmethod
    def render(self, instant):
        """Spell a whole-second UTC ``instant`` as the request value."""


class _IsoFormat(_TimeFormat):
    def render(self, instant):
        return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


class _EpochFormat(_TimeFormat):
    """Whole epoch units (the instants are whole seconds, so the division is exact)."""

    def __init__(self, unit):
        self._unit = _UNITS[unit]

    def render(self, instant):
        return (instant - _EPOCH) // self._unit


_TIME_FORMATS = {"iso": _IsoFormat(), "epoch_s": _EpochFormat("s"),
                 "epoch_ms": _EpochFormat("ms")}


# -- rows: where the list is, how a row becomes a dict, where its instant is -----


class _Instant(abc.ABC):
    """Where a row's effective instant comes from, and how it is written."""

    def __init__(self, field):
        self.field = field

    @abc.abstractmethod
    def read(self, record, where):
        """``(instant, ISO text)`` of one row dict; may add ``field`` to it."""


class _IsoInstant(_Instant):
    """The row already holds an ISO string under ``field`` — kept as the vendor spelled it."""

    def read(self, record, where):
        text = record.get(self.field)
        if not isinstance(text, str) or not text:
            raise AssetError([f"{where}: field {self.field!r} missing or empty — every "
                              "record needs its effective_date"])
        try:
            return parse_utc(text), text
        except AssetError as exc:
            raise AssetError([f"{where}: {exc.errors[0]}"]) from exc


class _EpochInstant(_Instant):
    """The row holds an epoch number under ``source``; ``field`` receives its ISO instant."""

    def __init__(self, field, source, unit):
        super().__init__(field)
        self.source = source
        self.unit = unit

    def read(self, record, where):
        if self.source not in record:
            raise AssetError([f"{where}: epoch field {self.source!r} is missing"])
        value = record[self.source]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or (isinstance(value, float) and not math.isfinite(value))):
            raise AssetError([f"{where}: epoch field {self.source!r} must be a finite "
                              f"number of {self.unit}, got {value!r}"])
        try:
            instant = _EPOCH + _UNITS[self.unit] * value
        except (OverflowError, ValueError) as exc:
            raise AssetError([f"{where}: epoch field {self.source!r} = {value!r} is not a "
                              f"representable epoch in {self.unit}"]) from exc
        if self.field in record:
            raise AssetError([f"{where}: row already holds {self.field!r} — the "
                              "effective_field would overwrite the vendor's value"])
        text = instant.isoformat()
        record[self.field] = text
        return instant, text


class _Rows:
    """How one response body becomes ordered rows: the list, the row shape, the instant."""

    def __init__(self, decl):
        self.path = decl.get("records_path")
        self.hops = self.path.split(".") if self.path else []
        self.fields = tuple(decl["row_fields"]) if "row_fields" in decl else None
        epoch = decl.get("epoch")
        field = decl["effective_field"]
        self.instant = (_EpochInstant(field, epoch["field"], epoch["unit"])
                        if epoch else _IsoInstant(field))

    @classmethod
    def check(cls, problems, where, decl):
        """Record every problem with the row-shaping knobs of one declaration."""
        effective = decl.get("effective_field")
        problems.text(f"{where}.effective_field", effective)
        if "records_path" in decl:
            cls._check_path(problems, f"{where}.records_path", decl["records_path"])
        fields = cls._check_fields(problems, where, decl)
        epoch = cls._check_epoch(problems, where, decl)
        if fields is None or not isinstance(effective, str):
            return
        if "epoch" not in decl:
            if effective not in fields:
                problems.add(f"{where}.effective_field must be one of row_fields (the "
                             f"positional rows carry it), got {effective!r}")
        elif epoch is not None:
            if epoch["field"] not in fields:
                problems.add(f"{where}.epoch.field must be one of row_fields "
                             f"{list(fields)}, got {epoch['field']!r}")
            if effective in fields:
                problems.add(f"{where}.effective_field may not be a row_fields name when "
                             f"epoch is declared (the connector writes it), got {effective!r}")

    @staticmethod
    def _check_path(problems, where, path):
        if not problems.text(where, path):
            return
        hops = path.split(".")
        if "" in hops:
            problems.add(f"{where} {path!r} has an empty segment")
        if hops.count("*") > 1 or ("*" in hops and hops[-1] != "*"):
            problems.add(f"{where} {path!r} may hold one '*', as the last segment")

    @staticmethod
    def _check_fields(problems, where, decl):
        if "row_fields" not in decl:
            return None
        value = decl["row_fields"]
        if (not isinstance(value, list) or not value
                or not all(isinstance(v, str) and v for v in value)
                or len(set(value)) != len(value)):
            problems.add(f"{where}.row_fields must be a non-empty list of distinct "
                         f"strings, got {value!r}")
            return None
        return tuple(value)

    @staticmethod
    def _check_epoch(problems, where, decl):
        """Return the epoch declaration when it is sound, else None (problems recorded)."""
        if "epoch" not in decl:
            return None
        here, epoch = f"{where}.epoch", decl["epoch"]
        if not problems.mapping(here, epoch):
            return None
        problems.unknown(here, epoch, _EPOCH_KEYS)
        field_ok = problems.text(f"{here}.field", epoch.get("field"))
        unit_ok = problems.choice(f"{here}.unit", epoch.get("unit"), _UNITS)
        if field_ok and epoch["field"] == decl.get("effective_field"):
            problems.add(f"{here}.field and effective_field must differ (the vendor's "
                         "epoch is kept beside the ISO instant, never overwritten)")
            return None
        return epoch if field_ok and unit_ok else None

    def declared_fields(self):
        """Return the field names the rows carry when ``row_fields`` says, else None."""
        if self.fields is None:
            return None
        written = [self.instant.field] if isinstance(self.instant, _EpochInstant) else []
        return list(self.fields) + written

    def extract(self, body, label):
        """Return the raw row list inside one response body; the path's failures are named."""
        if not self.hops:
            if not isinstance(body, list):
                raise AssetError([f"{label}: response body is not a list — declare "
                                  "records_path to point at the record list"])
            return body
        node = body
        for hop in self.hops:
            if hop == "*":
                node = self._the_list(node, label)
            else:
                node = node.get(hop) if isinstance(node, dict) else None
        if not isinstance(node, list):
            raise AssetError([f"{label}: records_path {self.path!r} does not lead to a "
                              "list in the response"])
        return node

    def _the_list(self, node, label):
        """Return the one list value of an object (None when ``node`` is not an object)."""
        if not isinstance(node, dict):
            return None
        keys = [key for key, value in node.items() if isinstance(value, list)]
        if len(keys) != 1:
            found = "no list" if not keys else f"{len(keys)} lists: " + ", ".join(
                repr(key) for key in keys)
            raise AssetError([f"{label}: records_path {self.path!r}: '*' matched "
                              f"{found} — it needs exactly one"])
        return node[keys[0]]

    def shape(self, raw, where):
        """``(instant, ISO text, record dict)`` for one raw row."""
        record = self._record(raw, where)
        instant, text = self.instant.read(record, where)
        return instant, text, record

    def _record(self, raw, where):
        """One raw row as a fresh dict (the decoded body is never mutated)."""
        if self.fields is None:
            if not isinstance(raw, dict):
                hint = (" — declare row_fields to read positional rows"
                        if isinstance(raw, list) else "")
                raise AssetError([f"{where} is not an object, got "
                                  f"{type(raw).__name__}{hint}"])
            return dict(raw)
        if not isinstance(raw, list):
            raise AssetError([f"{where} is not an array, got {type(raw).__name__} — "
                              "row_fields declares positional rows"])
        if len(raw) != len(self.fields):
            raise AssetError([f"{where} has {len(raw)} item{'s' if len(raw) != 1 else ''}, "
                              f"row_fields names {len(self.fields)}"])
        return dict(zip(self.fields, raw))


# -- pagination strategies --------------------------------------------------------


class _Paging(abc.ABC):
    """How one stream's requests chain; one subclass per ``pagination.strategy``.

    ``walk`` yields ``(records, lo, hi)`` per response, where ``lo`` / ``hi``
    bound the instants the response may contribute (``None``: unbounded).
    """

    #: Knobs beyond ``strategy`` and ``notes`` this strategy allows.
    keys = ()

    def __init__(self, decl, who):
        self.who = who

    @classmethod
    def check(cls, problems, where, decl, params):
        """Record every problem with one ``pagination`` declaration."""
        problems.unknown(where, decl, ("strategy", "notes") + cls.keys)
        cls.check_knobs(problems, where, decl, params)

    @classmethod
    @abc.abstractmethod
    def check_knobs(cls, problems, where, decl, params):
        """Shape-check this strategy's own knobs (``params`` are the static query params)."""

    def preflight(self, since, now):
        """Refuse, before any request, a pull this strategy knows it cannot finish."""

    @abc.abstractmethod
    def first_request(self, since, now):
        """``(params, label)`` of the first request, or None when nothing is to be pulled."""

    @abc.abstractmethod
    def walk(self, fetch, since, now):
        """Yield ``(records, lo, hi)`` for every response of the walk.

        ``fetch(params, label) -> (body, records)`` makes one request.
        """

    @staticmethod
    def collides(problems, where, name, params):
        if isinstance(name, str) and name in params:
            problems.add(f"{where} {name!r} collides with a static param of the stream")


class _NonePaging(_Paging):
    """One request."""

    @classmethod
    def check_knobs(cls, problems, where, decl, params):
        return None

    def first_request(self, since, now):
        return {}, "request"

    def walk(self, fetch, since, now):
        _body, records = fetch({}, "request")
        yield records, None, None


class _WindowPaging(_Paging):
    """One request per ``step``-second window of ``[start, end)``; rows clipped to it."""

    keys = ("start", "end", "step", "start_param", "end_param", "time_format",
            "max_windows", "lag", "truncated_path")

    def __init__(self, decl, who):
        super().__init__(decl, who)
        self.start = parse_utc(decl["start"])
        self.end = parse_utc(decl["end"]) if "end" in decl else None
        self.step = timedelta(seconds=decl["step"])
        self.start_param = decl["start_param"]
        self.end_param = decl["end_param"]
        self.format = _TIME_FORMATS[decl.get("time_format", DEFAULT_TIME_FORMAT)]
        self.max_windows = decl.get("max_windows", DEFAULT_MAX_WINDOWS)
        self.lag = timedelta(seconds=decl.get("lag", DEFAULT_LAG_S))
        self.truncated_path = decl.get("truncated_path")

    @classmethod
    def check_knobs(cls, problems, where, decl, params):
        start = problems.instant(f"{where}.start", decl.get("start"))
        end = problems.instant(f"{where}.end", decl["end"]) if "end" in decl else None
        if start is not None and end is not None and end <= start:
            problems.add(f"{where}.end must be after pagination.start")
        problems.whole(f"{where}.step", decl.get("step"), 1)
        names = [decl.get("start_param"), decl.get("end_param")]
        for key, name in zip(("start_param", "end_param"), names):
            if problems.text(f"{where}.{key}", name):
                cls.collides(problems, f"{where}.{key}", name, params)
        if names[0] is not None and names[0] == names[1]:
            problems.add(f"{where}: start_param and end_param must differ, got {names[0]!r}")
        if "time_format" in decl:
            problems.choice(f"{where}.time_format", decl["time_format"], _TIME_FORMATS)
        if "max_windows" in decl:
            problems.whole(f"{where}.max_windows", decl["max_windows"], 1)
        if "lag" in decl and problems.whole(f"{where}.lag", decl["lag"], 0) and "end" in decl:
            problems.add(f"{where}.lag applies only when end is omitted (a fixed end is "
                         "a fixed span)")
        if "truncated_path" in decl:
            problems.text(f"{where}.truncated_path", decl["truncated_path"])

    def _span(self, since, now):
        """``(first, last, count)``: the span still to pull and its window count."""
        last = self.end if self.end is not None else now - self.lag
        first = self.start if since is None else max(self.start, since.replace(microsecond=0))
        return first, last, max(0, -((first - last) // self.step))

    def preflight(self, since, now):
        first, last, count = self._span(since, now)
        if count > self.max_windows:
            raise AssetError([
                f"{self.who}: {count} windows needed to pull {first.isoformat()} to "
                f"{last.isoformat()} at step {int(self.step.total_seconds())} s, more than "
                f"pagination.max_windows ({self.max_windows}) — refusing to truncate; raise "
                "max_windows, widen step, or move start/end"])

    def _params(self, lo, hi):
        return {self.start_param: self.format.render(lo), self.end_param: self.format.render(hi)}

    @staticmethod
    def _label(index, count, lo, hi):
        return f"window {index}/{count} [{lo.isoformat()}, {hi.isoformat()})"

    def first_request(self, since, now):
        first, last, count = self._span(since, now)
        if not count:
            return None
        hi = min(first + self.step, last)
        return self._params(first, hi), self._label(1, count, first, hi)

    def walk(self, fetch, since, now):
        first, last, count = self._span(since, now)
        for index in range(count):
            lo = first + index * self.step
            hi = min(lo + self.step, last)
            label = self._label(index + 1, count, lo, hi)
            body, records = fetch(self._params(lo, hi), label)
            self._refuse_if_truncated(body, label)
            yield records, lo, hi

    def _refuse_if_truncated(self, body, label):
        if self.truncated_path is None:
            return
        marker = _pluck(body, self.truncated_path)
        if marker is None or marker is False or marker == "" or marker == [] or marker == {}:
            return
        raise AssetError([f"{self.who} {label}: the response says the vendor cut this "
                          f"window short (truncated: {self.truncated_path} = {marker!r}) — "
                          "refusing to store a partial window; lower pagination.step"])


class _CursorPaging(_Paging):
    """A token read from each response is sent back as a query param until the data ends."""

    keys = ("path", "param", "start", "time_format", "max_pages", "page_size", "size_param")

    def __init__(self, decl, who):
        super().__init__(decl, who)
        self.path = decl["path"]
        self.param = decl["param"]
        self.start = parse_utc(decl["start"]) if "start" in decl else None
        self.format = _TIME_FORMATS[decl["time_format"]] if "time_format" in decl else None
        self.max_pages = decl.get("max_pages", DEFAULT_MAX_PAGES)
        self.page_size = decl.get("page_size")
        self.size_param = decl.get("size_param")

    @classmethod
    def check_knobs(cls, problems, where, decl, params):
        problems.text(f"{where}.path", decl.get("path"))
        if problems.text(f"{where}.param", decl.get("param")):
            cls.collides(problems, f"{where}.param", decl["param"], params)
        if "start" in decl:
            problems.instant(f"{where}.start", decl["start"])
            if "time_format" not in decl:
                problems.add(f"{where}.start needs time_format (how to spell it on the wire)")
        if "time_format" in decl:
            problems.choice(f"{where}.time_format", decl["time_format"], _TIME_FORMATS)
        if "max_pages" in decl:
            problems.whole(f"{where}.max_pages", decl["max_pages"], 1)
        if "page_size" in decl:
            problems.whole(f"{where}.page_size", decl["page_size"], 1)
        if "size_param" in decl:
            if "page_size" not in decl:
                problems.add(f"{where}.size_param needs page_size (the value it carries)")
            if problems.text(f"{where}.size_param", decl["size_param"]):
                cls.collides(problems, f"{where}.size_param", decl["size_param"], params)
                if decl["size_param"] == decl.get("param"):
                    problems.add(f"{where}: param and size_param must differ, "
                                 f"got {decl['size_param']!r}")

    def _query(self, token):
        """Return the cursor and page-size params of one request."""
        query = {} if token is None else {self.param: token}
        if self.size_param is not None:
            query[self.size_param] = self.page_size
        return query

    def _last_page(self, records):
        """Whether ``records`` ends the walk: empty, or short of a declared ``page_size``."""
        return not records or (self.page_size is not None and len(records) < self.page_size)

    @staticmethod
    def _without_repeats(records, previous):
        """``(rows not in the previous page, this page's signatures)``.

        A vendor whose token is INCLUSIVE returns the boundary row again at
        the top of the next page; an identical row from the page just
        before is the same row, not a new one.
        """
        signatures = [json.dumps(row, sort_keys=True) for row in records]
        fresh = [row for row, sig in zip(records, signatures) if sig not in previous]
        return fresh, set(signatures)

    def _initial(self, since):
        """Return the first request's token: the later of ``start`` and the cursor, or None."""
        if self.format is None:
            return None
        floors = [] if since is None else [since.replace(microsecond=0)]
        known = floors if self.start is None else floors + [self.start]
        return self.format.render(max(known)) if known else None

    def first_request(self, since, now):
        return self._query(self._initial(since)), "page 1"

    def walk(self, fetch, since, now):
        sent = self._initial(since)
        seen = set() if sent is None else {str(sent)}
        previous, page = set(), 0
        while True:
            page += 1
            if page > self.max_pages:
                raise AssetError([
                    f"{self.who}: pagination.max_pages is {self.max_pages} and the token "
                    "still advances, so there is more to fetch — refusing to truncate; "
                    "raise max_pages or start later"])
            body, records = fetch(self._query(sent), f"page {page}")
            fresh, previous = self._without_repeats(records, previous)
            yield fresh, None, None
            token = _pluck(body, self.path)
            if self._last_page(records) or token is None or token == "":
                return
            if isinstance(token, bool) or not isinstance(token, (str, int, float)):
                raise AssetError([f"{self.who} page {page}: pagination token at "
                                  f"{self.path!r} must be a string or number, got "
                                  f"{type(token).__name__}"])
            if str(token) in seen:
                raise AssetError([f"{self.who} page {page}: pagination token {token!r} "
                                  "did not advance — refusing an infinite loop"])
            seen.add(str(token))
            sent = token


_PAGINATION = {"none": _NonePaging, "window": _WindowPaging, "cursor": _CursorPaging}


# -- one declared stream ------------------------------------------------------------


class _Stream:
    """One declared stream: its request, its rows and its pagination."""

    def __init__(self, name, decl):
        self.name = name
        self.who = f"stream {name!r}"
        self.path = decl["path"]
        self.params = dict(decl.get("params", {}))
        self.rows = _Rows(decl)
        pagination = decl.get("pagination", _NO_PAGINATION)
        self.paging = _PAGINATION[pagination["strategy"]](pagination, self.who)
        self.primary_key = list(decl.get("primary_key", []))
        self.schema = decl.get("schema")

    @classmethod
    def check(cls, problems, where, decl):
        """Record every problem with one stream declaration."""
        if not problems.mapping(where, decl):
            return
        problems.unknown(where, decl, _STREAM_KEYS)
        cls._check_request(problems, where, decl)
        _Rows.check(problems, where, decl)
        cls._check_primary_key(problems, where, decl)
        if "schema" in decl:
            problems.mapping(f"{where}.schema", decl["schema"])
        cls._check_pagination(problems, where, decl)

    @staticmethod
    def _check_request(problems, where, decl):
        path = decl.get("path")
        if problems.text(f"{where}.path", path) and ("?" in path or "#" in path):
            problems.add(f"{where}.path may not carry a query string — put query "
                         "values in params")
        if "params" in decl and problems.mapping(f"{where}.params", decl["params"]):
            for key, value in sorted(decl["params"].items()):
                scalar = (isinstance(value, (str, int, float)) and not isinstance(value, bool)
                          and (not isinstance(value, float) or math.isfinite(value)))
                if not scalar:
                    problems.add(f"{where}.params.{key} must be a string or number, "
                                 f"got {value!r}")

    @staticmethod
    def _check_primary_key(problems, where, decl):
        if "primary_key" not in decl:
            return
        key = decl["primary_key"]
        if not isinstance(key, list) or not all(isinstance(k, str) and k for k in key):
            problems.add(f"{where}.primary_key must be a list of field names, got {key!r}")
            return
        fields = decl.get("row_fields")
        if not isinstance(fields, list):
            return
        known = set(fields)
        effective = decl.get("effective_field")
        if "epoch" in decl and isinstance(effective, str):
            known.add(effective)
        missing = sorted(set(key) - known)
        if missing:
            problems.add(f"{where}.primary_key names {missing}, which no row carries")

    @staticmethod
    def _check_pagination(problems, where, decl):
        here = f"{where}.pagination"
        pagination = decl.get("pagination", _NO_PAGINATION)
        if not problems.mapping(here, pagination):
            return
        strategy = pagination.get("strategy")
        paging = _PAGINATION.get(strategy) if isinstance(strategy, str) else None
        if paging is None:
            problems.add(f"{here}.strategy must be one of {sorted(_PAGINATION)}, "
                         f"got {strategy!r}")
            return
        params = decl.get("params")
        paging.check(problems, here, pagination, params if isinstance(params, dict) else {})

    def declared_schema(self):
        """Return the schema config states or the row names imply; None when neither does."""
        if self.schema:
            return copy.deepcopy(self.schema)
        fields = self.rows.declared_fields()
        return None if fields is None else {"fields": fields}

    def schema_for(self, rows):
        """Return the SCHEMA payload: declared, else the sorted keys of the first row."""
        declared = self.declared_schema()
        if declared is not None:
            return declared
        return {"fields": sorted(rows[0][2]) if rows else []}

    def fetch(self, transport, base_url, params, label):
        """One request: ``(body, raw rows)``; failures name the stream and label."""
        label = f"{self.who} {label}"
        merged = dict(self.params)
        merged.update(params)
        url = base_url.rstrip("/") + "/" + self.path.lstrip("/")
        body = transport.get(url, merged, label)
        return body, self.rows.extract(body, label)

    def probe(self, transport, base_url, now):
        """Make the first request of a pull from scratch, if there is one."""
        request = self.paging.first_request(None, now)
        if request is not None:
            self.fetch(transport, base_url, *request)

    def collect(self, transport, base_url, since, now):
        """Every row of one pull, clipped to its windows, sorted by effective instant."""
        self.paging.preflight(since, now)
        fetch = functools.partial(self.fetch, transport, base_url)
        rows, index = [], 0
        for records, lo, hi in self.paging.walk(fetch, since, now):
            for raw in records:
                instant, text, record = self.rows.shape(raw, f"{self.who}: record {index}")
                index += 1
                if lo is None or lo <= instant < hi:
                    rows.append((instant, text, record))
        rows.sort(key=lambda row: row[0])
        return rows


# -- configuration and transport ------------------------------------------------------


class _Config:
    """A validated connector config: the streams as objects, the transport knobs resolved."""

    def __init__(self, config):
        problems = _Problems()
        if not problems.mapping("config", config):
            problems.raise_if()
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            problems.add(f"config.base_url must be an http(s) URL, got {base_url!r}")
        streams = config.get("streams")
        if problems.mapping("config.streams", streams):
            if not streams:
                problems.add("config.streams must declare at least one stream")
            for name in sorted(streams):
                _Stream.check(problems, f"config.streams.{name}", streams[name])
        self.timeout = config.get("timeout", DEFAULT_TIMEOUT_S)
        problems.real("config.timeout", self.timeout, "a positive number", lambda v: v > 0)
        self.max_retries = config.get("max_retries", DEFAULT_MAX_RETRIES)
        problems.whole("config.max_retries", self.max_retries, 0)
        self.pace_s = config.get("pace_s", DEFAULT_PACE_S)
        problems.real("config.pace_s", self.pace_s, "a number >= 0", lambda v: v >= 0)
        problems.raise_if()
        self.base_url = base_url
        self.streams = {name: _Stream(name, streams[name]) for name in sorted(streams)}


class _UrllibGetter:
    """The default getter: one stdlib urllib GET, the body decoded as JSON."""

    def __init__(self, timeout):
        self._timeout = timeout

    def __call__(self, url, params):
        import urllib.request

        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            raise
        return json.loads(body.decode("utf-8"))


class _Transport:
    """Pacing and retry above one getter; a pull makes one of these."""

    def __init__(self, getter, sleeper, cfg):
        self._getter = _UrllibGetter(cfg.timeout) if getter is None else getter
        self._sleeper = sleeper
        self._retries = cfg.max_retries
        self._pace_s = cfg.pace_s
        self._paced = False

    def _pace(self):
        if self._paced and self._pace_s > 0:
            self._sleeper(self._pace_s)
        self._paced = True

    def get(self, url, params, label):
        """GET with pacing, retry and backoff; the decoded JSON body, or AssetError."""
        self._pace()
        last = delay = None
        for attempt in range(self._retries + 1):
            if attempt:
                self._sleeper(delay)
            try:
                return self._getter(url, params)
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_STATUSES:
                    raise AssetError([f"{label}: HTTP {exc.code} from {safe_url(url)}"]) from exc
                last = f"HTTP {exc.code}"
                delay = retry_after(exc.headers, backoff(attempt + 1))
            except (OSError, http.client.HTTPException) as exc:
                # a dropped or truncated response (IncompleteRead is not an OSError)
                last = f"network error: {exc!r}"
                delay = backoff(attempt + 1)
            except ValueError as exc:
                raise AssetError([f"{label}: response from {safe_url(url)} is not "
                                  f"JSON: {exc}"]) from exc
        raise AssetError([f"{label}: giving up on {safe_url(url)} after "
                          f"{self._retries + 1} attempt(s) — last failure: {last}"])


# -- the connector ----------------------------------------------------------------------


class RestWindowConnector(Connector):
    """Time-window JSON REST: windowed or cursor pagination, array rows, epoch instants.

    Parameters
    ----------
    getter : callable or None
        ``getter(url, params) -> decoded JSON`` — ONE HTTP GET attempt:
        return the decoded body (an object or a list) on success, raise
        ``urllib.error.HTTPError`` on any other status,
        ``urllib.error.URLError`` (any ``OSError``) on a transport failure
        and ``ValueError`` on a body that is not JSON. ``url`` carries no
        query; ``params`` is the query as a dict. ``None`` means stdlib
        urllib under the ``timeout`` knob. Pacing, retry and pagination sit
        above the getter.
    sleeper : callable or None
        ``sleeper(seconds)`` for pacing and backoff; ``None`` means
        ``time.sleep``.
    clock : callable or None
        ``clock() -> datetime`` (aware) — sampled once per ``read`` and
        floored to the second as "now" for a window with no ``end``;
        ``None`` means the current time.

    Examples
    --------
    Pull one window of positional rows with an epoch time through a
    scripted transport::

        config = {
            "base_url": "https://api.example.test",
            "streams": {"bars": {
                "path": "/bars",
                "row_fields": ["time", "close"],
                "epoch": {"field": "time", "unit": "s"},
                "effective_field": "time_iso",
                "pagination": {"strategy": "window", "start": "2026-01-01T00:00:00Z",
                               "end": "2026-01-01T00:02:00Z", "step": 3600,
                               "start_param": "start", "end_param": "end"},
            }},
        }
        connector = RestWindowConnector(
            getter=lambda url, params: [[1767225660, 2.5], [1767225600, 2.0]],
            sleeper=lambda seconds: None,
        )
        messages = list(connector.read(config, ["bars"], {}, "backfill"))
        messages[1]["effective_date"]  # '2026-01-01T00:00:00+00:00'
        messages[2]["data"]["close"]   # 2.5
    """

    def __init__(self, getter=None, sleeper=None, clock=None):
        problems = [
            f"{name} must be callable, got {type(value).__name__}"
            for name, value in (("getter", getter), ("sleeper", sleeper), ("clock", clock))
            if value is not None and not callable(value)
        ]
        if problems:
            raise AssetError(problems)
        self._getter = getter
        self._sleeper = time.sleep if sleeper is None else sleeper
        self._clock = functools.partial(datetime.now, timezone.utc) if clock is None else clock

    def spec(self):
        """Declare the default-deny configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "base_url": {
                "required": True,
                "notes": "http(s) root URL; each stream's path appends to it.",
            },
            "streams": {
                "required": True,
                "notes": "stream name -> {path, params, effective_field, records_path "
                         "(one '*' allowed, last), row_fields, epoch {field, unit s|ms}, "
                         "pagination, primary_key, schema, notes}. pagination: "
                         "{strategy: none} (default), {strategy: window, start, end?, "
                         "step, start_param, end_param, time_format iso|epoch_s|epoch_ms, "
                         f"lag, max_windows (default {DEFAULT_MAX_WINDOWS}), "
                         "truncated_path} or {strategy: cursor, path, param, start?, "
                         "time_format, page_size, size_param, "
                         f"max_pages (default {DEFAULT_MAX_PAGES})}}; see the module docs.",
            },
            "timeout": {
                "notes": f"Request timeout in seconds; default {DEFAULT_TIMEOUT_S}.",
            },
            "max_retries": {
                "notes": "Extra attempts on 429/5xx/network errors, exponential "
                         f"backoff, every wait capped at {MAX_BACKOFF_S:g} s; default "
                         f"{DEFAULT_MAX_RETRIES}.",
            },
            "pace_s": {
                "notes": "Seconds slept between requests, never before the first; "
                         f"default {DEFAULT_PACE_S}.",
            },
        }}

    def _now(self):
        """Return the clock as a whole-second aware UTC datetime."""
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise AssetError([f"clock must return an aware datetime, got {now!r}"])
        return now.astimezone(timezone.utc).replace(microsecond=0)

    def _transport(self, cfg):
        return _Transport(self._getter, self._sleeper, cfg)

    def check(self, config):
        """Fail fast: shapes valid, then one probe request to the first declared stream.

        Parameters
        ----------
        config : dict
            Knobs already validated by ``check_config``.

        Raises
        ------
        AssetError
            On a bad config, a failed request or a response the declaration
            cannot read. The probe is the first window (or page) from
            scratch; the window-count cap is a ``read`` matter, since it
            depends on the checkpoint.
        """
        cfg = _Config(config)
        stream = cfg.streams[sorted(cfg.streams)[0]]
        stream.probe(self._transport(cfg), cfg.base_url, self._now())

    def discover(self, config):
        """List the declared streams, verbatim — no network.

        Parameters
        ----------
        config : dict
            Knobs already validated by ``check_config``.

        Returns
        -------
        list of dict
            ``{"stream", "schema", "primary_key"}`` per declared stream; a
            stream with no schema and no ``row_fields`` discovers as empty
            fields.
        """
        cfg = _Config(config)
        return [{"stream": name, "schema": stream.declared_schema() or {"fields": []},
                 "primary_key": list(stream.primary_key)}
                for name, stream in sorted(cfg.streams.items())]

    @staticmethod
    def _check_read_arguments(streams, state, mode):
        problems = _Problems()
        if problems.mapping("state", state):
            for name, value in sorted(state.items()):
                problems.mapping(f"state.{name}", value)
        if (not isinstance(streams, list) or not streams
                or not all(isinstance(s, str) for s in streams)):
            problems.add(f"streams must be a non-empty list of names, got {streams!r}")
        elif len(set(streams)) != len(streams):
            problems.add(f"streams names a stream more than once: {streams!r}")
        if mode not in MODES:
            problems.add(f"mode must be one of {list(MODES)}, got {mode!r}")
        problems.raise_if()

    @staticmethod
    def _cursor(state, name):
        """``(text, instant)`` of a stream's checkpoint; ``("", None)`` when it has none."""
        text = state.get(name, {}).get("cursor", "")
        if text in ("", None):
            return "", None
        if not isinstance(text, str):
            raise AssetError([f"state.{name}.cursor must be an ISO instant string, "
                              f"got {text!r}"])
        try:
            return text, parse_utc(text)
        except AssetError as exc:
            raise AssetError([f"state.{name}.cursor: {exc.errors[0]}"]) from exc

    def read(self, config, streams, state, mode):
        """Per stream: walk the pagination, buffer, sort by instant, emit; one STATE at the end.

        The cursor filter runs client-side whatever the server returned. Every
        stream's span is planned (and a window-count cap enforced) before the
        first request, and a failed request abandons the pull: no STATE is
        ever emitted for it.

        Parameters
        ----------
        config : dict
            Knobs already validated by ``check_config``.
        streams : list of str
            Which declared streams to pull, each once.
        state : dict
            ``{stream: {"cursor": <ISO instant>}}`` as a previous STATE left it.
        mode : str
            ``"backfill"`` or ``"live"``; both read the same way.

        Yields
        ------
        dict
            ``SCHEMA``, then ``RECORD`` messages (``kind`` observation), per
            stream; one ``STATE`` last.

        Raises
        ------
        AssetError
            On a bad argument or config, an unknown stream, a refused cap, a
            failed request or a row the declaration cannot read.
        """
        self._check_read_arguments(streams, state, mode)
        cfg = _Config(config)
        now = self._now()
        plan = []
        for name in streams:
            stream = cfg.streams.get(name)
            if stream is None:
                raise AssetError([f"unknown stream {name!r} — declared: "
                                  f"{sorted(cfg.streams)}"])
            cursor = self._cursor(state, name)
            stream.paging.preflight(cursor[1], now)
            plan.append((stream, cursor))
        transport = self._transport(cfg)
        new_state = {key: dict(value) for key, value in state.items()}
        for stream, (text, since) in plan:
            rows = stream.collect(transport, cfg.base_url, since, now)
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream.name,
                   "schema": stream.schema_for(rows)}
            emitted_max = text
            for instant, effective, record in rows:
                if since is not None and instant <= since:
                    continue  # already durable per the checkpoint
                yield {"protocol": PROTOCOL, "type": "RECORD", "stream": stream.name,
                       "effective_date": effective, "kind": "observation", "data": record}
                emitted_max = effective
            new_state.setdefault(stream.name, {})["cursor"] = emitted_max
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

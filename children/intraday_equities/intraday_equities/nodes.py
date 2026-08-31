"""Child pipeline kinds: store reader, windows, session features, scan.

Vendor transport stays in the connector packs. This module declares the
equity vocabulary, session policy, overlap score, the horizon-scan
go/no-go, and the Pyomo doorway.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from dskit.onboarding import parse_utc
from dskit.onboarding.libs.alpaca import BAR_KEY_FIELDS, BAR_STREAM
from dskit.onboarding.observations import scan_stream, stream_digest
from dskit.pipeline.libs.numpy import ReturnWindows, narrow_params
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.node import (
    Node,
    check_int_param,
    register_node_kind,
    reject_unknown_params,
)

__all__ = [
    "DEFAULT_MAX_GAP_MINUTES",
    "DEFAULT_PRICE_FIELD",
    "BarsFromStore",
    "FeedParity",
    "HorizonScan",
    "KeepSymbols",
    "LeadLabeledRows",
    "NODE_KINDS",
    "PortfolioSelect",
    "SessionFeatureRows",
    "Universe",
    "WindowRows",
    "horizon_leads",
    "session_feature_names",
]

DEFAULT_TS_FIELD = "ts"
DEFAULT_SHARED_FIELDS = ("symbol",)
DEFAULT_PRICE_FIELD = "close"
DEFAULT_MAX_GAP_MINUTES = 5
DEFAULT_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


def session_name(stamp, zone, rth_start_minutes, rth_end_minutes):
    """Name the regular-session bucket for one bar stamp.

    Parameters
    ----------
    stamp : str
        ISO timestamp.
    zone : str
        IANA timezone used for regular hours.
    rth_start_minutes, rth_end_minutes : int
        Clock minutes from midnight that bound regular hours.

    Returns
    -------
    str
        ``rth``, ``eth``, or ``closed``.
    """
    instant = parse_utc(stamp).astimezone(ZoneInfo(zone))
    minutes = instant.hour * 60 + instant.minute
    if instant.weekday() >= 5:
        return "closed"
    if rth_start_minutes <= minutes < rth_end_minutes:
        return "rth"
    return "eth"


def _child_root():
    """Return the child project root that owns this module."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_path(path):
    """Resolve ``path`` from cwd, then from the child root."""
    if os.path.isabs(path) and os.path.isfile(path):
        return path
    cwd = os.path.abspath(path)
    if os.path.isfile(cwd):
        return cwd
    child = os.path.join(_child_root(), path)
    if os.path.isfile(child):
        return child
    return path


def _load_json(path):
    """Load one JSON object from ``path``."""
    with open(_resolve_path(path), encoding="utf-8") as fh:
        return json.load(fh)


def _file_digest(path):
    """Return sha256 of the file bytes at ``path``."""
    with open(_resolve_path(path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _iso_date_ok(value):
    """Return whether ``value`` is a real ``YYYY-MM-DD`` string."""
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _string_list_ok(value):
    """Return whether ``value`` is a non-empty list of non-empty strings."""
    return (
        isinstance(value, (list, tuple))
        and bool(value)
        and all(isinstance(item, str) and item for item in value)
    )


def _session_problems(session):
    """Problems with a session-policy object, empty when none."""
    if not isinstance(session, dict):
        return [f"session must be an object, got {session!r}"]
    problems = []
    zone = session.get("tz")
    if not isinstance(zone, str) or not zone:
        problems.append(f"session.tz must be a non-empty string, got {zone!r}")
    for knob in ("rth_start_minutes", "rth_end_minutes"):
        value = session.get(knob)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            problems.append(f"session.{knob} must be an int >= 0, got {value!r}")
    start = session.get("rth_start_minutes")
    end = session.get("rth_end_minutes")
    if isinstance(start, int) and isinstance(end, int) and end <= start:
        problems.append(
            f"session.rth_end_minutes must be > rth_start_minutes, got {end} <= {start}"
        )
    return problems


def _scale_problems(scales):
    """Problems with the multi-scale list, empty when none."""
    if not isinstance(scales, (list, tuple)) or not scales:
        return [f"scales must be a non-empty list of objects, got {scales!r}"]
    problems = []
    for i, scale in enumerate(scales):
        prefix = f"scales[{i}]"
        if not isinstance(scale, dict):
            problems.append(f"{prefix} must be an object, got {scale!r}")
            continue
        width = scale.get("width")
        if isinstance(width, bool) or not isinstance(width, int) or width < 1:
            problems.append(f"{prefix}.width must be an int >= 1, got {width!r}")
        tag = scale.get("tag")
        if not isinstance(tag, str) or not tag:
            problems.append(f"{prefix}.tag must be a non-empty string, got {tag!r}")
        flag = scale.get("cross_session")
        if not isinstance(flag, bool):
            problems.append(
                f"{prefix}.cross_session must be a bool, got {flag!r}"
            )
    return problems


def _horizon_problems(horizon):
    """Problems with the horizon-scan object, empty when none."""
    if not isinstance(horizon, dict):
        return [f"horizon must be an object, got {horizon!r}"]
    problems = []
    for knob in ("lead_start", "lead_step", "lead_stop", "top_k", "band_leads"):
        value = horizon.get(knob)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(f"horizon.{knob} must be an int >= 1, got {value!r}")
    start = horizon.get("lead_start")
    stop = horizon.get("lead_stop")
    if isinstance(start, int) and isinstance(stop, int) and stop < start:
        problems.append(
            f"horizon.lead_stop must be >= lead_start, got {stop} < {start}"
        )
    anchors = horizon.get("anchors")
    if (
        not isinstance(anchors, (list, tuple))
        or not anchors
        or any(
            isinstance(lead, bool) or not isinstance(lead, int) or lead < 1
            for lead in anchors
        )
    ):
        problems.append(
            f"horizon.anchors must be a non-empty list of ints >= 1, got {anchors!r}"
        )
    se_mult = horizon.get("se_mult")
    if (
        isinstance(se_mult, bool)
        or not isinstance(se_mult, (int, float))
        or not math.isfinite(float(se_mult) if isinstance(se_mult, (int, float)) else 0)
        or (isinstance(se_mult, (int, float)) and se_mult <= 0)
    ):
        problems.append(f"horizon.se_mult must be a positive number, got {se_mult!r}")
    return problems


def _universe_problems(spec):
    """Problems with a universe document, empty when none."""
    if not isinstance(spec, dict):
        return [f"universe must be a JSON object, got {spec!r}"]
    problems = []
    for knob in ("symbols", "tradable", "reference"):
        if not _string_list_ok(spec.get(knob)):
            problems.append(
                f"{knob} must be a non-empty list of strings, got {spec.get(knob)!r}"
            )
    symbols = set(spec["symbols"]) if _string_list_ok(spec.get("symbols")) else None
    tradable = set(spec["tradable"]) if _string_list_ok(spec.get("tradable")) else None
    reference = set(spec["reference"]) if _string_list_ok(spec.get("reference")) else None
    if symbols is not None and tradable is not None and reference is not None:
        if tradable & reference:
            problems.append(
                "tradable and reference must be disjoint, "
                f"overlap {sorted(tradable & reference)}"
            )
        if symbols != tradable | reference:
            problems.append(
                "symbols must be exactly tradable ∪ reference, "
                f"got extra {sorted(symbols - (tradable | reference))} "
                f"missing {sorted((tradable | reference) - symbols)}"
            )
    holidays = spec.get("holidays")
    if (
        not isinstance(holidays, (list, tuple))
        or any(not _iso_date_ok(day) for day in holidays)
    ):
        problems.append(
            f"holidays must be a list of YYYY-MM-DD strings, got {holidays!r}"
        )
    lookback = spec.get("lookback")
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 2:
        problems.append(f"lookback must be an int >= 2, got {lookback!r}")
    gap = spec.get("max_gap_minutes")
    if (
        isinstance(gap, bool)
        or not isinstance(gap, (int, float))
        or not math.isfinite(float(gap) if isinstance(gap, (int, float)) else 0)
        or (isinstance(gap, (int, float)) and gap <= 0)
    ):
        problems.append(f"max_gap_minutes must be a positive number, got {gap!r}")
    for knob in ("period_ms",):
        value = spec.get(knob)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(f"{knob} must be an int >= 1, got {value!r}")
    offset = spec.get("offset_ms")
    period = spec.get("period_ms")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        problems.append(f"offset_ms must be an int >= 0, got {offset!r}")
    elif isinstance(period, int) and offset >= period:
        problems.append(
            f"offset_ms must be < period_ms, got {offset} >= {period}"
        )
    price_field = spec.get("price_field")
    if not isinstance(price_field, str) or not price_field:
        problems.append(
            f"price_field must be a non-empty string, got {price_field!r}"
        )
    problems.extend(_session_problems(spec.get("session")))
    problems.extend(_scale_problems(spec.get("scales")))
    problems.extend(_horizon_problems(spec.get("horizon")))
    return problems


class BarsFromStore(Node):
    """Emit the store's deduplicated bar records (role ``data``).

    The ``intraday_equities-bars`` kind. Adds the child's ``session``
    field; the scan itself is ADR-0037.

    Parameters
    ----------
    params : dict
        ``root``, ``source``, and ``universe`` (required strings),
        optional ``stream``, ``ts_field``, and ``shared_fields``.
        Session hours come from the universe file.

    Examples
    --------
    Point at an onboarding root::

        node = BarsFromStore("bars", {
            "root": "./ob",
            "source": "alpaca-sip",
            "universe": "configs/universe.json",
        })
        node.params["source"]  # 'alpaca-sip'
    """

    role = "data"
    outputs = ("records",)
    _PARAMS = (
        "root", "source", "universe", "stream", "ts_field", "shared_fields",
    )
    _snap = None

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in ("root", "source", "universe"):
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(
                    f"{name} is required and must be a non-empty string, "
                    f"got {value!r}"
                )
        universe = params.get("universe")
        if isinstance(universe, str) and universe:
            try:
                spec = _load_json(universe)
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"universe {universe!r} could not be read: {exc}")
            else:
                problems.extend(_session_problems(spec.get("session")))
        stream = params.get("stream", BAR_STREAM)
        if not isinstance(stream, str) or not stream:
            problems.append(f"stream must be a non-empty string, got {stream!r}")
        ts_field = params.get("ts_field", DEFAULT_TS_FIELD)
        if not isinstance(ts_field, str) or not ts_field:
            problems.append(
                f"ts_field must be a non-empty string, got {ts_field!r}"
            )
        shared = params.get("shared_fields", DEFAULT_SHARED_FIELDS)
        if (
            not isinstance(shared, (list, tuple))
            or any(not isinstance(field, str) or not field for field in shared)
        ):
            problems.append(
                f"shared_fields must be a list of field-name strings, "
                f"got {shared!r}"
            )
        return problems

    def _scan(self):
        """Memoize the flattened, session-tagged snapshot."""
        if self._snap is not None:
            return self._snap
        records = scan_stream(
            self.params["root"],
            self.params["source"],
            self.params.get("stream", BAR_STREAM),
            key_fields=BAR_KEY_FIELDS,
            ts_field=self.params.get("ts_field", DEFAULT_TS_FIELD),
            shared_fields=tuple(
                self.params.get("shared_fields", DEFAULT_SHARED_FIELDS)
            ),
        )
        policy = _load_json(self.params["universe"])["session"]
        tagged = []
        for record in records:
            row = dict(record)
            stamp = row.get(self.params.get("ts_field", DEFAULT_TS_FIELD))
            if isinstance(stamp, str) and stamp:
                row["session"] = session_name(
                    stamp,
                    policy["tz"],
                    policy["rth_start_minutes"],
                    policy["rth_end_minutes"],
                )
            tagged.append(row)
        self._snap = tagged
        return self._snap

    def fingerprint(self):
        """Return a content-derived data identity.

        Returns
        -------
        dict
            ``kind``, ``rows``, and ``sha256``.
        """
        records = self._scan()
        return {
            "kind": "intraday_equities-bars",
            "rows": len(records),
            "sha256": stream_digest(records),
            "universe": _file_digest(self.params["universe"]),
        }

    def run(self, ctx, inputs):
        """Emit the memoized bar records.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused source frame.
        inputs : dict
            Empty for a data node.

        Returns
        -------
        dict
            ``records`` list.
        """
        records = self._scan()
        self.log.info("emitting %d bar record(s)", len(records))
        return {"records": records}


class WindowRows(ReturnWindows):
    """Per-symbol lagged-return windows with a configurable label lead.

    Role ``transform`` — the ``intraday_equities-window`` kind. Cadence
    stays in ``event-grid``; this node only owns the equity spellings
    and the usable-price rule.

    Parameters
    ----------
    params : dict
        ``lookback`` (required int >= 2), optional ``price_field``,
        ``max_gap_minutes``, and ``label_lead``.

    Examples
    --------
    Five-minute labels on one-minute returns::

        node = WindowRows(
            "window",
            {"lookback": 30, "label_lead": 5, "max_gap_minutes": 5},
        )
        node.params["label_lead"]  # 5
    """

    role = "transform"
    outputs = ("records",)
    min_lookback = 2
    _PARAMS = narrow_params(
        ReturnWindows._PARAMS,
        "carry_fields",
        "drop_incomplete",
        "fields",
        "group_field",
        "label_name",
        "lag_prefix",
        "max_gap",
        "order_field",
        "require_fields",
        "return_kind",
    ) + ("max_gap_minutes", "price_field")

    def group_field(self):
        """Name the series key."""
        return "symbol"

    def order_field(self):
        """Name the ordering field."""
        return "asof_ms"

    def price_field(self):
        """Name the priced field."""
        return self.params.get("price_field", DEFAULT_PRICE_FIELD)

    def fields(self):
        """Lift only the priced field."""
        return (self.price_field(),)

    def max_gap_minutes(self):
        """Give the gap bound in minutes."""
        return float(self.params.get("max_gap_minutes", DEFAULT_MAX_GAP_MINUTES))

    def max_gap(self):
        """Give the same bound in epoch milliseconds."""
        return self.max_gap_minutes() * 60_000

    def carry_fields(self):
        """Carry identity fields downstream."""
        return (self.group_field(), self.order_field())

    def require_fields(self):
        """Require no extra identity fields."""
        return ()

    def drop_incomplete(self):
        """Emit only complete windows and labels."""
        return True

    def return_kind(self):
        """Use log returns."""
        return "log"

    def lag_prefix(self):
        """Name lag columns ``ret_lag_*``."""
        return "ret_lag_"

    def label_name(self):
        """Name the label column ``y_next``."""
        return "y_next"

    def keep_mask(self, arrays):
        """Keep bars whose price is finite and strictly positive.

        Parameters
        ----------
        arrays : dict
            One symbol's lifted arrays.

        Returns
        -------
        numpy.ndarray
            Boolean keep mask.
        """
        import numpy as np

        price = arrays[self.price_field()]
        return np.isfinite(price) & (price > 0.0)

    def sort_rows(self, rows):
        """Order by ``(asof_ms, symbol)``.

        Parameters
        ----------
        rows : list of dict
            Built window rows.

        Returns
        -------
        list of dict
            Time-major rows.
        """
        return sorted(
            rows,
            key=lambda row: (row[self.order_field()], row[self.group_field()]),
        )

    def emit(self, rows, metrics):
        """Package rows under this kind's ``records`` port.

        Parameters
        ----------
        rows : list of dict
            Ordered window rows.
        metrics : dict
            Pack summary; logged, not emitted.

        Returns
        -------
        dict
            ``{"records": rows}``.
        """
        return {"records": rows}

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        price_field = params.get("price_field", DEFAULT_PRICE_FIELD)
        if not isinstance(price_field, str) or not price_field:
            problems.append(
                f"price_field must be a non-empty string, got {price_field!r}"
            )
        gap = params.get("max_gap_minutes", DEFAULT_MAX_GAP_MINUTES)
        if (
            isinstance(gap, bool)
            or not isinstance(gap, (int, float))
            or not math.isfinite(gap)
            or gap <= 0
        ):
            problems.append(
                f"max_gap_minutes must be a positive number, got {gap!r}"
            )
        return problems


def session_feature_names(lookback, scales, reference):
    """Name every column SessionFeatureRows emits besides identity.

    Parameters
    ----------
    lookback : int
        How many tape-local one-minute lags to spell.
    scales : sequence of dict
        Each item has ``tag``.
    reference : sequence of str
        Feature-only symbols that receive a residual column.

    Returns
    -------
    tuple of str
        Feature names in emission order.
    """
    names = [f"ret_lag_{step}" for step in range(lookback)]
    for scale in scales:
        tag = scale["tag"]
        names.extend((f"ret_{tag}", f"rv_{tag}", f"range_{tag}"))
    names.extend((
        "minutes_from_open",
        "minutes_to_close",
        "is_first_rth",
        "is_last_rth",
        "overnight_gap",
        "session_gap_days",
        "after_holiday",
    ))
    for symbol in reference:
        names.append(f"ref_ret_{symbol}")
        names.append(f"residual_{symbol}")
    return tuple(names)


def horizon_leads(start, step, stop):
    """Return the inclusive lead grid declared by the universe.

    Parameters
    ----------
    start, step, stop : int
        Inclusive range from the universe ``horizon`` object.

    Returns
    -------
    tuple of int
        One lead per step through ``stop``.
    """
    return tuple(range(start, stop + 1, step))


def _ny_date_minutes(asof_ms, zone):
    """Split one epoch stamp into a calendar date and clock minutes."""
    instant = datetime.fromtimestamp(
        asof_ms / 1000.0, tz=timezone.utc
    ).astimezone(ZoneInfo(zone))
    return instant.date().isoformat(), instant.hour * 60 + instant.minute


def _cell(value):
    """JSON-safe number, or ``None`` when the value is not finite."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _session_feature_arrays(
    ms, opn, high, low, close, lookback, max_gap_ms, holidays, scales, session,
):
    """Build per-bar feature columns for one symbol's RTH tape."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    n = len(close)
    logp = np.full(n, np.nan)
    priced = close > 0
    logp[priced] = np.log(close[priced])
    ret1 = np.full(n, np.nan)
    if n > 1:
        ret1[1:] = logp[1:] - logp[:-1]
    gap = np.zeros(n, dtype=bool)
    if n > 1:
        gap[1:] = np.diff(ms) > max_gap_ms
    ret1[gap] = np.nan
    sess_start = np.zeros(n, dtype=np.int64)
    start = 0
    for i in range(n):
        if gap[i]:
            start = i
        sess_start[i] = start
    columns = {}
    idx = np.arange(n)
    for step in range(lookback):
        lagged = np.full(n, np.nan)
        src = idx - step
        ok = (src >= sess_start) & (src >= 0)
        lagged[ok] = ret1[src[ok]]
        columns[f"ret_lag_{step}"] = lagged
    for scale in scales:
        width = int(scale["width"])
        tag = scale["tag"]
        ret_s = np.full(n, np.nan)
        rv_s = np.full(n, np.nan)
        rng_s = np.full(n, np.nan)
        same_session = not scale["cross_session"]
        if n > width:
            ok = idx >= width
            if same_session:
                ok &= (idx - width) >= sess_start
            ret_s[ok] = logp[ok] - logp[idx[ok] - width]
        if n >= width:
            rv_s[width - 1:] = np.nanstd(
                sliding_window_view(ret1, width), axis=1, ddof=0
            )
            hi = np.max(sliding_window_view(high, width), axis=1)
            lo = np.min(sliding_window_view(low, width), axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                rng_s[width - 1:] = np.log(hi / lo)
            if same_session:
                start_at = idx[width - 1:] - (width - 1)
                bad = start_at < sess_start[width - 1:]
                ret_s[width - 1:][bad] = np.nan
                rv_s[width - 1:][bad] = np.nan
                rng_s[width - 1:][bad] = np.nan
        columns[f"ret_{tag}"] = ret_s
        columns[f"rv_{tag}"] = rv_s
        columns[f"range_{tag}"] = rng_s
    dates = []
    mins = []
    for stamp in ms:
        day, clock = _ny_date_minutes(int(stamp), session["tz"])
        dates.append(day)
        mins.append(clock)
    mins = np.asarray(mins, dtype=np.float64)
    columns["minutes_from_open"] = mins - session["rth_start_minutes"]
    columns["minutes_to_close"] = session["rth_end_minutes"] - mins
    is_first = np.zeros(n, dtype=np.float64)
    is_last = np.zeros(n, dtype=np.float64)
    if n:
        is_first[0] = 1.0
        is_last[-1] = 1.0
        if n > 1:
            changed = np.asarray(dates[1:]) != np.asarray(dates[:-1])
            is_first[1:] = changed.astype(np.float64)
            is_last[:-1] = changed.astype(np.float64)
    columns["is_first_rth"] = is_first
    columns["is_last_rth"] = is_last
    overnight = np.full(n, np.nan)
    gap_days = np.zeros(n, dtype=np.float64)
    after_h = np.zeros(n, dtype=np.float64)
    starts = [0] if n else []
    starts.extend(int(i) for i in np.flatnonzero(gap))
    hols = {date.fromisoformat(day) for day in holidays}
    ends = starts[1:] + [n]
    for start_i, end_i in zip(starts, ends):
        if start_i == 0:
            continue
        prev = date.fromisoformat(dates[start_i - 1])
        this = date.fromisoformat(dates[start_i])
        gap_days[start_i:end_i] = float((this - prev).days)
        after_h[start_i:end_i] = float(
            any(prev < day < this for day in hols)
        )
        prev_close = close[start_i - 1]
        this_open = opn[start_i]
        if prev_close > 0 and this_open > 0:
            overnight[start_i:end_i] = math.log(this_open / prev_close)
    columns["overnight_gap"] = overnight
    columns["session_gap_days"] = gap_days
    columns["after_holiday"] = after_h
    return columns


class SessionFeatureRows(Node):
    """Wide RTH feature rows: tape-local lags plus named session fields.

    Role ``transform`` — the ``intraday_equities-session-features`` kind.
    Every science knob arrives on the ``spec`` port from
    :class:`Universe`. One-minute lags never bridge a tape gap. The
    overnight move is its own field. A scale with ``cross_session``
    false stays inside a tape-continuous session; true reads back across
    the close.

    Parameters
    ----------
    params : dict
        Empty. Knobs come from ``spec``.

    Examples
    --------
    Wire the universe object, then run::

        node = SessionFeatureRows("features", {})
        node.outputs  # ('records',)
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ()

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        return problems

    def validate_inputs(self, inputs):
        """Require records and a universe spec.

        Parameters
        ----------
        inputs : dict
            ``records`` and ``spec``.

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        if not isinstance(inputs.get("records"), list):
            problems.append(
                f"records must be a list of rows, got {inputs.get('records')!r}"
            )
        spec = inputs.get("spec")
        if not isinstance(spec, dict):
            problems.append(f"spec must be the universe object, got {spec!r}")
        else:
            problems.extend(_universe_problems(spec))
        return problems

    def run(self, ctx, inputs):
        """Emit grid-aligned feature rows with a named overnight field.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            ``records`` of RTH bars and ``spec`` from the universe node.

        Returns
        -------
        dict
            ``records`` list.
        """
        import numpy as np

        spec = inputs["spec"]
        lookback = int(spec["lookback"])
        max_gap_ms = float(spec["max_gap_minutes"]) * 60_000
        period_ms = int(spec["period_ms"])
        offset_ms = int(spec["offset_ms"])
        holidays = tuple(spec["holidays"])
        scales = list(spec["scales"])
        session = spec["session"]
        reference = tuple(spec["reference"])
        grouped = {}
        for record in inputs["records"]:
            if not isinstance(record, dict):
                continue
            symbol = record.get("symbol")
            stamp = record.get("asof_ms")
            if not isinstance(symbol, str) or stamp is None:
                continue
            grouped.setdefault(symbol, []).append(record)
        built = {}
        ref_ret = {symbol: {} for symbol in reference}
        skip = set()
        for symbol in reference:
            skip.add(f"ref_ret_{symbol}")
            skip.add(f"residual_{symbol}")
        for symbol, rows in grouped.items():
            rows.sort(key=lambda row: row["asof_ms"])
            ms = np.asarray([int(row["asof_ms"]) for row in rows], dtype=np.int64)
            opn = np.asarray(
                [float(row.get("open", row.get("close", 0.0)) or 0.0) for row in rows],
                dtype=np.float64,
            )
            high = np.asarray(
                [float(row.get("high", row.get("close", 0.0)) or 0.0) for row in rows],
                dtype=np.float64,
            )
            low = np.asarray(
                [float(row.get("low", row.get("close", 0.0)) or 0.0) for row in rows],
                dtype=np.float64,
            )
            close = np.asarray(
                [float(row.get("close", 0.0) or 0.0) for row in rows],
                dtype=np.float64,
            )
            columns = _session_feature_arrays(
                ms, opn, high, low, close, lookback, max_gap_ms,
                holidays, scales, session,
            )
            keep = ((ms - offset_ms) % period_ms) == 0
            names = [name for name in columns if name not in skip]
            emitted = []
            for i, row in enumerate(rows):
                if not bool(keep[i]):
                    continue
                out = {
                    "symbol": symbol,
                    "asof_ms": int(ms[i]),
                    "close": _cell(close[i]),
                }
                for name in names:
                    out[name] = _cell(columns[name][i])
                emitted.append(out)
                if symbol in ref_ret and out.get("ret_lag_0") is not None:
                    ref_ret[symbol][out["asof_ms"]] = out["ret_lag_0"]
            built[symbol] = emitted
        records = []
        for symbol, rows in built.items():
            for row in rows:
                own = row.get("ret_lag_0")
                for ref in reference:
                    ref_r = ref_ret[ref].get(row["asof_ms"])
                    row[f"ref_ret_{ref}"] = ref_r
                    row[f"residual_{ref}"] = (
                        own - ref_r
                        if own is not None and ref_r is not None
                        else None
                    )
                records.append(row)
        records.sort(key=lambda row: (row["asof_ms"], row["symbol"]))
        self.log.info(
            "session features: %d row(s) from %d symbol(s)",
            len(records),
            len(built),
        )
        return {"records": records}


class FeedParity(Node):
    """Compare overlapping vendor bars by symbol and minute.

    Role ``score`` — the ``intraday_equities-feed-parity`` kind.

    Parameters
    ----------
    params : dict
        ``split`` (required) and optional ``fields``.

    Examples
    --------
    Score two tiny tapes::

        node = FeedParity("parity", {"split": "val"})
        out = node.run(ctx, {
            "left": [{"symbol": "AAPL", "asof_ms": 1, "close": 1.0}],
            "right": [{"symbol": "AAPL", "asof_ms": 1, "close": 1.0}],
        })
        out["metrics"]["n_overlap"]  # 1
    """

    role = "score"
    outputs = ("records", "metrics")
    _PARAMS = ("split", "fields")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if params.get("split") not in ("train", "val", "cal", "test"):
            problems.append(
                "split must declare which split this node reads "
                f"(train/val/cal/test), got {params.get('split')!r}"
            )
        fields = params.get("fields", DEFAULT_OHLCV_FIELDS)
        if (
            not isinstance(fields, (list, tuple))
            or not fields
            or any(not isinstance(field, str) or not field for field in fields)
        ):
            problems.append(
                f"fields must be a non-empty list of strings, got {fields!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Require two record lists.

        Parameters
        ----------
        inputs : dict
            ``left`` and ``right`` record lists.

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        for port in ("left", "right"):
            if not isinstance(inputs.get(port), list):
                problems.append(
                    f"{port} must be a list of records, got {inputs.get(port)!r}"
                )
        return problems

    def run(self, ctx, inputs):
        """Emit per-key diffs and coverage metrics.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused frame.
        inputs : dict
            Vendor record lists.

        Returns
        -------
        dict
            ``records`` and ``metrics``.
        """
        fields = tuple(self.params.get("fields", DEFAULT_OHLCV_FIELDS))
        left = {
            (row.get("symbol"), row.get("asof_ms")): row
            for row in inputs["left"]
            if isinstance(row, dict)
        }
        right = {
            (row.get("symbol"), row.get("asof_ms")): row
            for row in inputs["right"]
            if isinstance(row, dict)
        }
        keys = sorted(set(left) & set(right), key=lambda key: (key[1], key[0]))
        diffs = []
        totals = {field: 0.0 for field in fields}
        counted = {field: 0 for field in fields}
        for key in keys:
            row = {"symbol": key[0], "asof_ms": key[1]}
            for field in fields:
                left_value = left[key].get(field)
                right_value = right[key].get(field)
                if (
                    isinstance(left_value, (int, float))
                    and isinstance(right_value, (int, float))
                    and not isinstance(left_value, bool)
                    and not isinstance(right_value, bool)
                ):
                    delta = float(left_value) - float(right_value)
                    row[f"{field}_delta"] = delta
                    totals[field] += abs(delta)
                    counted[field] += 1
            diffs.append(row)
        metrics = {
            "n_left": len(left),
            "n_right": len(right),
            "n_overlap": len(keys),
            "coverage_left": (len(keys) / len(left)) if left else 0.0,
            "coverage_right": (len(keys) / len(right)) if right else 0.0,
        }
        for field in fields:
            metrics[f"mae_{field}"] = (
                totals[field] / counted[field] if counted[field] else 0.0
            )
        self.log.info(
            "feed-parity overlap %d (left %d, right %d)",
            len(keys), len(left), len(right),
        )
        return {"records": diffs, "metrics": metrics}


def _finite(value):
    """Return whether ``value`` is a usable number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _pearson(xs, ys):
    """Pearson correlation of two equal-length sequences, else 0."""
    import numpy as np

    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    n = int(x.size)
    if n < 2 or n != int(y.size):
        return 0.0
    xm = x - x.mean()
    ym = y - y.mean()
    den = math.sqrt(float(np.dot(xm, xm)) * float(np.dot(ym, ym)))
    if den == 0.0:
        return 0.0
    return float(np.dot(xm, ym) / den)


def _ranks(values):
    """Average ranks (1-based) so ties do not invent order."""
    import numpy as np

    values = np.asarray(values, dtype=np.float64)
    n = int(values.size)
    if n == 0:
        return []
    order = np.argsort(values, kind="mergesort")
    sorted_vals = values[order]
    bounds = np.flatnonzero(
        np.concatenate(([True], sorted_vals[1:] != sorted_vals[:-1], [True]))
    )
    ranks = np.empty(n, dtype=np.float64)
    for start, end in zip(bounds[:-1], bounds[1:]):
        ranks[order[start:end]] = (start + end - 1) / 2.0 + 1.0
    return ranks.tolist()


def _decision_metrics(picks, inputs):
    """Score one-pick policy vs labels for cadence and HPO."""
    records = inputs.get("records") or []
    labeled = inputs.get("labeled")
    if labeled is None:
        labeled = records
    signal = inputs.get("signal")
    stamps = {
        row.get("asof_ms")
        for row in records
        if isinstance(row, dict) and row.get("asof_ms") is not None
    }
    by_key = {}
    for row in labeled:
        if not isinstance(row, dict):
            continue
        key = (row.get("asof_ms"), row.get("symbol"))
        if key[0] is None or not isinstance(key[1], str):
            continue
        by_key[key] = row
    preds, realized = [], []
    if hasattr(signal, "predict"):
        for row in labeled:
            if not isinstance(row, dict) or not _finite(row.get("y_next")):
                continue
            pred = signal.predict(row)
            if not _finite(pred):
                continue
            preds.append(float(pred))
            realized.append(float(row["y_next"]))
    pick_ys = []
    for pick in picks:
        row = by_key.get((pick.get("asof_ms"), pick.get("symbol")))
        if row is not None and _finite(row.get("y_next")):
            pick_ys.append(float(row["y_next"]))
    hits = sum(1 for y in pick_ys if y > 0.0)
    cum = peak = max_dd = 0.0
    for y in pick_ys:
        cum += y
        if cum > peak:
            peak = cum
        drawdown = peak - cum
        if drawdown > max_dd:
            max_dd = drawdown
    changes = 0
    prev = None
    ordered = sorted(picks, key=lambda pick: pick.get("asof_ms"))
    for pick in ordered:
        symbol = pick.get("symbol")
        if prev is not None and symbol != prev:
            changes += 1
        prev = symbol
    n_turn = max(len(ordered) - 1, 0)
    return {
        "n_picks": float(len(picks)),
        "n_stamps": float(len(stamps)),
        "n_labeled": float(
            sum(
                1 for row in labeled
                if isinstance(row, dict) and _finite(row.get("y_next"))
            )
        ),
        "n_scored": float(len(preds)),
        "rank_ic": (
            _pearson(_ranks(preds), _ranks(realized)) if len(preds) >= 2 else 0.0
        ),
        "pick_hit_rate": (hits / len(pick_ys)) if pick_ys else 0.0,
        "pick_mean_y": (sum(pick_ys) / len(pick_ys)) if pick_ys else 0.0,
        "pick_sum_y": float(sum(pick_ys)),
        "pick_max_drawdown": max_dd,
        "turnover": (changes / n_turn) if n_turn else 0.0,
    }


def build_portfolio_model(per_t, tradable):
    """Build the child's one-pick program.

    Parameters
    ----------
    per_t : dict
        ``asof_ms -> {symbol: predicted return}``.
    tradable : sequence of str
        Symbols allowed to receive a pick.

    Returns
    -------
    pyomo.environ.ConcreteModel
        Binary assignment maximizing predicted return.
    """
    import pyomo.environ as pyo

    allowed = set(tradable)
    filtered = {
        stamp: {
            symbol: pred
            for symbol, pred in preds.items()
            if symbol in allowed
        }
        for stamp, preds in per_t.items()
    }
    filtered = {stamp: preds for stamp, preds in filtered.items() if preds}
    model = pyo.ConcreteModel()
    index = [
        (stamp, symbol)
        for stamp in sorted(filtered)
        for symbol in sorted(filtered[stamp])
    ]
    model.x = pyo.Var(index, domain=pyo.Binary)
    model.one_per_t = pyo.Constraint(
        sorted(filtered),
        rule=lambda m, stamp: sum(
            m.x[stamp, symbol] for symbol in sorted(filtered[stamp])
        ) == 1,
    )
    model.objective = pyo.Objective(
        expr=sum(
            filtered[stamp][symbol] * model.x[stamp, symbol]
            for stamp, symbol in index
        ),
        sense=pyo.maximize,
    )
    return model


class PortfolioSelect(PyomoSolve):
    """Pick one tradable symbol per timestamp from a signal.

    Role ``score`` — the ``intraday_equities-portfolio`` kind. Capital
    sizing is a later document; this doorway only interprets the pick.

    Parameters
    ----------
    params : dict
        ``split`` and ``tradable`` required; ``solver`` and
        ``solver_options`` from the doorway.

    Examples
    --------
    Declare the tradable cohort::

        node = PortfolioSelect("select", {
            "split": "val",
            "tradable": ["AAPL", "JPM", "XOM", "WMT", "LLY"],
        })
        node.params["tradable"][0]  # 'AAPL'
    """

    role = "score"
    outputs = ("picks", "metrics")
    _PARAMS = PyomoSolve._PARAMS + ("split", "tradable")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = super().validate_params(params)
        if params.get("split") not in ("train", "val", "cal", "test"):
            problems.append(
                "split must declare which split this node reads "
                f"(train/val/cal/test), got {params.get('split')!r}"
            )
        tradable = params.get("tradable")
        if tradable is not None and not _string_list_ok(tradable):
            problems.append(
                "tradable must be a non-empty list of symbols when declared, "
                f"got {tradable!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Require a predict-able signal and a record list.

        Parameters
        ----------
        inputs : dict
            ``signal`` and ``records``; optional ``labeled``.

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        if not hasattr(inputs.get("signal"), "predict"):
            problems.append(
                "signal must expose predict(), got "
                f"{type(inputs.get('signal')).__name__}"
            )
        if not isinstance(inputs.get("records"), list):
            problems.append(
                f"records must be a list of rows, got {inputs.get('records')!r}"
            )
        labeled = inputs.get("labeled")
        if labeled is not None and not isinstance(labeled, list):
            problems.append(
                f"labeled must be a list of rows when wired, got {labeled!r}"
            )
        tradable = inputs.get("tradable", self.params.get("tradable"))
        if not _string_list_ok(tradable):
            problems.append(
                "tradable must arrive as an input list or a params list, "
                f"got {tradable!r}"
            )
        return problems

    def build_model(self, inputs, params):
        """Assemble the one-pick program from the wired signal.

        Parameters
        ----------
        inputs : dict
            Validated ports.
        params : dict
            Node params.

        Returns
        -------
        pyomo.environ.ConcreteModel
            The assignment program.
        """
        per_t = {}
        for row in inputs["records"]:
            if not isinstance(row, dict):
                continue
            pred = inputs["signal"].predict(row)
            if pred is None:
                continue
            stamp = row.get("asof_ms")
            symbol = row.get("symbol")
            if stamp is None or not isinstance(symbol, str):
                continue
            per_t.setdefault(stamp, {})[symbol] = float(pred)
        if not per_t:
            raise RuntimeError("portfolio received no scorable rows")
        tradable = inputs.get("tradable", params.get("tradable"))
        return build_portfolio_model(per_t, tradable)

    def extract(self, model, results):
        """Read the solved picks.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            Solved model.
        results : object
            Solver results; unused beyond the doorway's checks.

        Returns
        -------
        dict
            ``picks`` and ``metrics``.
        """
        picks = []
        for stamp, symbol in model.x:
            if float(model.x[stamp, symbol].value or 0) < 0.5:
                continue
            picks.append({"asof_ms": stamp, "symbol": symbol})
        picks.sort(key=lambda row: (row["asof_ms"], row["symbol"]))
        return {
            "picks": picks,
            "metrics": {"n_picks": len(picks)},
        }

    def run(self, ctx, inputs):
        """Solve, then score the picks against labeled rows.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Run frame.
        inputs : dict
            ``signal``, ``records``, and optional ``labeled``.

        Returns
        -------
        dict
            ``picks`` plus decision metrics (IC, hit rate, no-cost
            return, drawdown, turnover). Fill rate and delay decay wait
            on a fill model.
        """
        out = super().run(ctx, inputs)
        out["metrics"] = _decision_metrics(out.get("picks") or [], inputs)
        return out


def _spearman(xs, ys):
    """Spearman rank correlation of two equal-length sequences."""
    return _pearson(_ranks(xs), _ranks(ys))


def _ic_se(n):
    """Null standard error of a Spearman IC with ``n`` labeled pairs."""
    if n < 2:
        return float("inf")
    return 1.0 / math.sqrt(n - 1)


def _passes_ic(ic, n, se_mult):
    """Return whether ``|ic|`` clears ``se_mult`` null standard errors."""
    return n >= 2 and abs(ic) > se_mult * _ic_se(n)


def _horizon_verdict(curve, anchors, se_mult, band_leads):
    """Apply the pre-registered go/no-go rule to one IC curve.

    Parameters
    ----------
    curve : list of dict
        One row per lead with ``lead``, ``ic_val``, ``n_val``, increasing
        in ``lead``.
    anchors : sequence of int
        The three pre-registered session leads.
    se_mult : float
        How many null SEs count as distinguishable from zero.
    band_leads : int
        Contiguous passing leads that make the shape test.

    Returns
    -------
    dict
        ``go``, ``go_anchor``, ``go_band``, ``peak``, ``farthest``.
    """
    flags = [_passes_ic(row["ic_val"], row["n_val"], se_mult) for row in curve]
    anchor_set = set(anchors)
    go_anchor = any(
        ok for row, ok in zip(curve, flags) if row["lead"] in anchor_set
    )
    run = 0
    go_band = False
    for ok in flags:
        run = run + 1 if ok else 0
        if run >= band_leads:
            go_band = True
            break
    passing = [row for row, ok in zip(curve, flags) if ok]
    peak = max(passing, key=lambda row: abs(row["ic_val"])) if passing else None
    farthest = None
    if peak is not None:
        thresh = abs(peak["ic_val"]) - _ic_se(peak["n_val"])
        for row in passing:
            if abs(row["ic_val"]) >= thresh:
                farthest = row
    return {
        "go": go_anchor or go_band,
        "go_anchor": go_anchor,
        "go_band": go_band,
        "peak": peak,
        "farthest": farthest,
    }


def _combo_ic(train_x, train_y, val_x, val_y, names, top_k):
    """Train-select ``top_k`` features, z-score, equal-weight, score both folds.

    Parameters
    ----------
    train_x, val_x : numpy.ndarray
        Rows x features, already finite-aligned with the label vectors.
    train_y, val_y : numpy.ndarray
        Labels.
    names : sequence of str
        Feature names matching the column order.
    top_k : int
        How many train-IC winners to average.

    Returns
    -------
    tuple
        ``(ic_train, ic_val, selected_names)``.
    """
    import numpy as np

    ics = [
        abs(_spearman(train_x[:, col], train_y))
        for col in range(train_x.shape[1])
    ]
    order = sorted(range(len(names)), key=lambda i: ics[i], reverse=True)
    picked = order[: max(1, min(top_k, len(order)))]
    selected = [names[i] for i in picked]
    mu = train_x[:, picked].mean(axis=0)
    sd = train_x[:, picked].std(axis=0)
    sd = np.where(sd == 0.0, 1.0, sd)

    def _score(matrix):
        z = (matrix[:, picked] - mu) / sd
        return z.mean(axis=1)

    return (
        _spearman(_score(train_x), train_y),
        _spearman(_score(val_x), val_y),
        selected,
    )


def _attach_lead_rows(
    bars, records, lead, price_field, split, train_end, val_start, val_end,
    label, features,
):
    """Copy feature rows that have a finite RTH-tape label at ``lead``.

    Labels count 1-minute RTH bars, so they cross the close. A label
    that would land after ``val_end`` is dropped (lockbox unread). Train
    also requires the landing stamp to be at or before ``train_end``.
    """
    import numpy as np

    tapes = {}
    for row in bars:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        stamp = row.get("asof_ms")
        price = row.get(price_field)
        if not isinstance(symbol, str) or stamp is None or not _finite(price):
            continue
        stamp = int(stamp)
        if stamp > val_end:
            continue
        tapes.setdefault(symbol, []).append((stamp, float(price)))
    arrays = {}
    for symbol, items in tapes.items():
        items.sort()
        arrays[symbol] = (
            np.asarray([item[0] for item in items], dtype=np.int64),
            np.asarray([item[1] for item in items], dtype=np.float64),
        )
    out = []
    for row in records:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        stamp = row.get("asof_ms")
        if not isinstance(symbol, str) or stamp is None:
            continue
        stamp = int(stamp)
        if stamp > val_end:
            continue
        if any(_cell(row.get(name)) is None for name in features):
            continue
        pair = arrays.get(symbol)
        if pair is None:
            continue
        t_ms, t_px = pair
        loc = int(np.searchsorted(t_ms, stamp))
        if loc >= t_ms.size or int(t_ms[loc]) != stamp:
            continue
        future = loc + lead
        if future >= t_ms.size:
            continue
        px0 = float(t_px[loc])
        px1 = float(t_px[future])
        if px0 <= 0.0 or px1 <= 0.0:
            continue
        y = math.log(px1 / px0)
        if not math.isfinite(y):
            continue
        future_ms = int(t_ms[future])
        if split == "train":
            keep = stamp <= train_end and future_ms <= train_end
        else:
            keep = val_start <= stamp <= val_end and future_ms <= val_end
        if not keep:
            continue
        attached = dict(row)
        attached[label] = y
        out.append(attached)
    return out


def _scan_aligned(bars, records, features, price_field, val_end):
    """Align finite feature rows to the 1-minute tape, dropping lockbox stamps."""
    import numpy as np

    tapes = {}
    for row in bars:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        stamp = row.get("asof_ms")
        price = row.get(price_field)
        if not isinstance(symbol, str) or stamp is None or not _finite(price):
            continue
        stamp = int(stamp)
        if stamp > val_end:
            continue
        tapes.setdefault(symbol, []).append((stamp, float(price)))
    grouped = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        stamp = row.get("asof_ms")
        if not isinstance(symbol, str) or stamp is None:
            continue
        stamp = int(stamp)
        if stamp > val_end:
            continue
        cells = [_cell(row.get(name)) for name in features]
        if any(value is None for value in cells):
            continue
        grouped.setdefault(symbol, []).append((stamp, cells))
    prepared = []
    for symbol, rows in grouped.items():
        tape = tapes.get(symbol)
        if not tape:
            continue
        tape.sort()
        t_ms = np.asarray([item[0] for item in tape], dtype=np.int64)
        t_px = np.asarray([item[1] for item in tape], dtype=np.float64)
        stamps = np.asarray([item[0] for item in rows], dtype=np.int64)
        x = np.asarray([item[1] for item in rows], dtype=np.float64)
        loc = np.searchsorted(t_ms, stamps)
        match = (loc < t_ms.size) & (t_ms[np.minimum(loc, t_ms.size - 1)] == stamps)
        prepared.append((stamps, x, loc, match, t_ms, t_px))
    return prepared


def _scan_fold(prepared, lead, train_end, val_start, val_end):
    """Collect train/val matrices for one lead; labels never land after val_end."""
    import numpy as np

    train_x, train_y, val_x, val_y = [], [], [], []
    n_features = prepared[0][1].shape[1] if prepared else 0
    for stamps, x, loc, match, t_ms, t_px in prepared:
        future = loc + lead
        ok = match & (future < t_ms.size)
        if not np.any(ok):
            continue
        loc_ok = loc[ok]
        fut_ok = future[ok]
        px0 = t_px[loc_ok]
        px1 = t_px[fut_ok]
        pos = (px0 > 0.0) & (px1 > 0.0)
        y = np.full(px0.shape, np.nan, dtype=np.float64)
        y[pos] = np.log(px1[pos] / px0[pos])
        finite = np.isfinite(y)
        if not np.any(finite):
            continue
        stamp = stamps[ok][finite]
        future_ms = t_ms[fut_ok][finite]
        x_ok = x[ok][finite]
        y = y[finite]
        train = (stamp <= train_end) & (future_ms <= train_end)
        val = (
            (stamp >= val_start)
            & (stamp <= val_end)
            & (future_ms <= val_end)
        )
        if np.any(train):
            train_x.append(x_ok[train])
            train_y.append(y[train])
        if np.any(val):
            val_x.append(x_ok[val])
            val_y.append(y[val])
    empty_x = np.zeros((0, n_features), dtype=np.float64)
    empty_y = np.zeros(0, dtype=np.float64)
    return (
        np.concatenate(train_x) if train_x else empty_x,
        np.concatenate(train_y) if train_y else empty_y,
        np.concatenate(val_x) if val_x else empty_x,
        np.concatenate(val_y) if val_y else empty_y,
    )


class HorizonScan(Node):
    """Rank-IC curve over the universe lead grid, plus a go/no-go.

    Role ``score`` — the ``intraday_equities-horizon-scan`` kind. Labels
    count RTH minutes on the 1-minute tape, so Friday 15:59 + 1 is Monday
    9:30. Horizon knobs and the feature list arrive on ``spec``. Train
    selects features; val scores them. The lockbox is unused.

    Go if any declared anchor has val |IC| above ``se_mult`` null SEs,
    or a contiguous band of ``band_leads`` passing grid points does.
    Peak is the strongest *passing* lead; farthest confident is the
    longest lead still within 1 SE of that peak.

    Parameters
    ----------
    params : dict
        ``train_end_ms``, ``val_start_ms``, ``val_end_ms`` required.

    Examples
    --------
    Cuts only — the grid lives on the universe port::

        node = HorizonScan("scan", {
            "split": "val",
            "train_end_ms": 10, "val_start_ms": 11, "val_end_ms": 20,
        })
        node.params["split"]  # 'val'
    """

    role = "score"
    outputs = ("records", "metrics")
    _PARAMS = ("split", "train_end_ms", "val_start_ms", "val_end_ms")

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if params.get("split") != "val":
            problems.append(
                "split must be 'val' (the lockbox is unread), got "
                f"{params.get('split')!r}"
            )
        for knob in ("train_end_ms", "val_start_ms", "val_end_ms"):
            check_int_param(problems, knob, params.get(knob), ge=0)
        return problems

    def validate_inputs(self, inputs):
        """Require feature rows, the 1-minute tape, and the universe spec.

        Parameters
        ----------
        inputs : dict
            ``records``, ``bars``, and ``spec``.

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        for port in ("records", "bars"):
            if not isinstance(inputs.get(port), list):
                problems.append(
                    f"{port} must be a list of rows, got {inputs.get(port)!r}"
                )
        spec = inputs.get("spec")
        if not isinstance(spec, dict):
            problems.append(f"spec must be the universe object, got {spec!r}")
        else:
            problems.extend(_universe_problems(spec))
        return problems

    def run(self, ctx, inputs):
        """Score every lead and apply the go/no-go rule.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            ``records`` (feature rows) and ``bars`` (RTH 1-minute closes).

        Returns
        -------
        dict
            ``records`` (one row per lead) and ``metrics``.
        """
        spec = inputs["spec"]
        horizon = spec["horizon"]
        features = list(
            spec.get("features")
            or session_feature_names(
                spec["lookback"], spec["scales"], spec["reference"]
            )
        )
        leads = horizon_leads(
            int(horizon["lead_start"]),
            int(horizon["lead_step"]),
            int(horizon["lead_stop"]),
        )
        anchors = tuple(horizon["anchors"])
        top_k = int(horizon["top_k"])
        se_mult = float(horizon["se_mult"])
        band_leads = int(horizon["band_leads"])
        train_end = int(self.params["train_end_ms"])
        val_start = int(self.params["val_start_ms"])
        val_end = int(self.params["val_end_ms"])
        price_field = spec["price_field"]
        prepared = _scan_aligned(
            inputs["bars"], inputs["records"], features, price_field, val_end,
        )
        curve = []
        for lead in leads:
            train_x, train_y, val_x, val_y = _scan_fold(
                prepared, lead, train_end, val_start, val_end,
            )
            n_train, n_val = train_y.size, val_y.size
            if n_train < 2 or n_val < 2:
                curve.append({
                    "lead": lead,
                    "ic_train": 0.0,
                    "ic_val": 0.0,
                    "n_train": float(n_train),
                    "n_val": float(n_val),
                    "se": _ic_se(n_val),
                    "pass_2se": 0.0,
                    "selected": "",
                })
                continue
            ic_train, ic_val, selected = _combo_ic(
                train_x, train_y, val_x, val_y, features, top_k,
            )
            curve.append({
                "lead": lead,
                "ic_train": ic_train,
                "ic_val": ic_val,
                "n_train": float(n_train),
                "n_val": float(n_val),
                "se": _ic_se(n_val),
                "pass_2se": float(_passes_ic(ic_val, n_val, se_mult)),
                "selected": ",".join(selected),
            })
        verdict = _horizon_verdict(curve, anchors, se_mult, band_leads)
        farthest = verdict["farthest"]
        peak = verdict["peak"]
        metrics = {
            "go": float(verdict["go"]),
            "go_anchor": float(verdict["go_anchor"]),
            "go_band": float(verdict["go_band"]),
            "n_leads": float(len(curve)),
            "n_anchors_pass": float(sum(
                1 for row in curve
                if row["lead"] in set(anchors) and row["pass_2se"]
            )),
            "peak_lead": float(peak["lead"]) if peak else 0.0,
            "peak_ic": float(peak["ic_val"]) if peak else 0.0,
            "farthest_confident_lead": float(farthest["lead"]) if farthest else 0.0,
            "rank_ic": float(farthest["ic_val"]) if farthest else 0.0,
            "n_val": float(farthest["n_val"]) if farthest else (
                float(peak["n_val"]) if peak else 0.0
            ),
        }
        self.log.info(
            "horizon scan: go=%s anchors=%s band=%s farthest=%s ic=%.4f",
            bool(verdict["go"]),
            bool(verdict["go_anchor"]),
            bool(verdict["go_band"]),
            metrics["farthest_confident_lead"],
            metrics["rank_ic"],
        )
        return {"records": curve, "metrics": metrics}


class LeadLabeledRows(Node):
    """Attach one RTH-tape lead return onto session-feature rows.

    Role ``transform`` — the ``intraday_equities-lead-labels`` kind.
    ``WindowRows`` cannot form a 1165-minute label: ``max_gap`` splits
    at the close, and a session is only 390 minutes. This node uses the
    same tape arithmetic as :class:`HorizonScan`.

    Parameters
    ----------
    params : dict
        ``lead`` (int >= 1), ``split`` (``train`` or ``val``),
        ``train_end_ms``, ``val_start_ms``, ``val_end_ms``. Optional
        ``label`` (str, default ``y_next``).

    Examples
    --------
    Val rows whose 2-minute label still lands by ``val_end``::

        node = LeadLabeledRows("labels", {
            "lead": 2, "split": "val",
            "train_end_ms": 10, "val_start_ms": 11, "val_end_ms": 20,
        })
        node.params["lead"]  # 2
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = (
        "lead", "split", "train_end_ms", "val_start_ms", "val_end_ms", "label",
    )

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        check_int_param(problems, "lead", params.get("lead"), ge=1)
        if params.get("split") not in ("train", "val"):
            problems.append(
                "split must be 'train' or 'val' (the lockbox is unread), "
                f"got {params.get('split')!r}"
            )
        for knob in ("train_end_ms", "val_start_ms", "val_end_ms"):
            check_int_param(problems, knob, params.get(knob), ge=0)
        label = params.get("label", "y_next")
        if "label" in params and (not isinstance(label, str) or not label):
            problems.append(
                f"label must be a non-empty row key, got {label!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Require feature rows, the 1-minute tape, and the universe spec.

        Parameters
        ----------
        inputs : dict
            ``records``, ``bars``, and ``spec``.

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        for port in ("records", "bars"):
            if not isinstance(inputs.get(port), list):
                problems.append(
                    f"{port} must be a list of rows, got {inputs.get(port)!r}"
                )
        spec = inputs.get("spec")
        if not isinstance(spec, dict):
            problems.append(f"spec must be the universe object, got {spec!r}")
        else:
            problems.extend(_universe_problems(spec))
        return problems

    def run(self, ctx, inputs):
        """Emit feature rows that carry a lockbox-safe lead label.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            ``records``, ``bars``, and ``spec``.

        Returns
        -------
        dict
            ``records`` with ``label`` attached.
        """
        spec = inputs["spec"]
        features = list(
            spec.get("features")
            or session_feature_names(
                spec["lookback"], spec["scales"], spec["reference"]
            )
        )
        rows = _attach_lead_rows(
            inputs["bars"],
            inputs["records"],
            int(self.params["lead"]),
            spec.get("price_field") or "close",
            self.params["split"],
            int(self.params["train_end_ms"]),
            int(self.params["val_start_ms"]),
            int(self.params["val_end_ms"]),
            self.params.get("label") or "y_next",
            features,
        )
        self.log.info(
            "lead-labels: %d row(s) at lead %s on split %s",
            len(rows),
            self.params["lead"],
            self.params["split"],
        )
        return {"records": rows}


class Universe(Node):
    """Load the one market/science knob file (role ``data``).

    The ``intraday_equities-universe`` kind. Adding a candidate name is
    an edit to this file (and the source configs that pull it), not to
    a node. The file digest is the data fingerprint, so a knob change
    moves the run hash.

    Parameters
    ----------
    params : dict
        ``path`` (required string).

    Examples
    --------
    Point at the shipped knob file::

        node = Universe("universe", {"path": "configs/universe.json"})
        node.params["path"]  # 'configs/universe.json'
    """

    role = "data"
    outputs = (
        "spec",
        "symbols",
        "tradable",
        "reference",
        "features",
        "lag_features",
        "lookback",
        "max_gap_minutes",
    )
    _PARAMS = ("path",)
    _spec = None

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        path = params.get("path")
        if not isinstance(path, str) or not path:
            problems.append(f"path is required and must be a non-empty string, got {path!r}")
            return problems
        try:
            spec = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"universe {path!r} could not be read: {exc}")
            return problems
        problems.extend(_universe_problems(spec))
        return problems

    def _load(self):
        """Memoize the universe object and derived feature names."""
        if self._spec is not None:
            return self._spec
        spec = dict(_load_json(self.params["path"]))
        spec["features"] = list(session_feature_names(
            spec["lookback"], spec["scales"], spec["reference"],
        ))
        self._spec = spec
        return self._spec

    def fingerprint(self):
        """Return a content hash of the snapshot this instance loaded.

        Returns
        -------
        dict
            ``kind`` and ``sha256``.
        """
        spec = self._load()
        payload = json.dumps(spec, sort_keys=True, default=str)
        return {
            "kind": "intraday_equities-universe",
            "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }

    def run(self, ctx, inputs):
        """Emit the universe object and the lists other nodes wire.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            Empty for a data node.

        Returns
        -------
        dict
            ``spec``, cohort lists, derived feature names, and the
            lookback knobs window nodes wire.
        """
        spec = self._load()
        self.log.info(
            "universe %d symbol(s) (%d tradable, %d reference)",
            len(spec["symbols"]),
            len(spec["tradable"]),
            len(spec["reference"]),
        )
        return {
            "spec": spec,
            "symbols": list(spec["symbols"]),
            "tradable": list(spec["tradable"]),
            "reference": list(spec["reference"]),
            "features": list(spec["features"]),
            "lag_features": [
                f"ret_lag_{step}" for step in range(spec["lookback"])
            ],
            "lookback": spec["lookback"],
            "max_gap_minutes": spec["max_gap_minutes"],
        }


class KeepSymbols(Node):
    """Keep records whose symbol field is in a wired allow-list.

    Role ``transform`` — the ``intraday_equities-keep-symbols`` kind.
    The allow-list is an input so widening the cohort does not edit this
    node.

    Parameters
    ----------
    params : dict
        ``field`` (required record field name).

    Examples
    --------
    Keep the tradable names::

        node = KeepSymbols("tradable", {"field": "symbol"})
        node.params["field"]  # 'symbol'
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("field",)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        field = params.get("field")
        if not isinstance(field, str) or not field:
            problems.append(
                f"field is required and must be a non-empty string, got {field!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Require records and a symbol list.

        Parameters
        ----------
        inputs : dict
            ``records`` and ``symbols``.

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        if not isinstance(inputs.get("records"), list):
            problems.append(
                f"records must be a list of rows, got {inputs.get('records')!r}"
            )
        if not _string_list_ok(inputs.get("symbols")):
            problems.append(
                f"symbols must be a non-empty list of strings, got {inputs.get('symbols')!r}"
            )
        return problems

    def run(self, ctx, inputs):
        """Keep rows whose field value is in the wired list.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            ``records`` and ``symbols``.

        Returns
        -------
        dict
            ``records`` list.
        """
        field = self.params["field"]
        allowed = set(inputs["symbols"])
        kept = [
            row for row in inputs["records"]
            if isinstance(row, dict) and row.get(field) in allowed
        ]
        self.log.info("keep-symbols kept %d/%d row(s)", len(kept), len(inputs["records"]))
        return {"records": kept}


NODE_KINDS = {
    "intraday_equities-bars": BarsFromStore,
    "intraday_equities-window": WindowRows,
    "intraday_equities-session-features": SessionFeatureRows,
    "intraday_equities-universe": Universe,
    "intraday_equities-keep-symbols": KeepSymbols,
    "intraday_equities-feed-parity": FeedParity,
    "intraday_equities-horizon-scan": HorizonScan,
    "intraday_equities-lead-labels": LeadLabeledRows,
    "intraday_equities-portfolio": PortfolioSelect,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

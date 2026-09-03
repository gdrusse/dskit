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
import random
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from dskit.onboarding import dir_digest, parse_utc
from dskit.onboarding.libs.alpaca import BAR_KEY_FIELDS, BAR_STREAM
from dskit.onboarding.observations import (
    scan_stream,
    stream_digest,
    stream_dir,
)
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.libs.numpy import (
    ReturnWindows,
    log_return,
    narrow_params,
    rolling_max,
    rolling_min,
    rolling_std,
    rolling_sum,
)
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.node import (
    Node,
    check_int_param,
    register_node_kind,
    reject_unknown_params,
)
from dskit.pipeline.runs import SKILL_FILE

__all__ = [
    "DEFAULT_MAX_GAP_MINUTES",
    "DEFAULT_PRICE_FIELD",
    "BarsFromStore",
    "FeedParity",
    "HorizonScan",
    "KeepSymbols",
    "LeadLabeledRows",
    "LookbackScan",
    "NODE_KINDS",
    "NoInformationScan",
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
#: One-sided level for :class:`NoInformationScan` (ADR-0058).
_NO_INFO_ALPHA = 0.05
#: ŷ must vary by at least this fraction of the label's own sd. Below it
#: the tree is a stump, ŷ is the mean, and every horizon scores IC=0 —
#: a mean-only model cannot answer a no-information test (A0013).
_DEGENERATE_YHAT_REL = 1e-8
_DAY_MS = 24 * 60 * 60 * 1000
#: Rolling windows the label transform reads, in 1-minute RTH bars
#: (ADR-0059). One session of vol; ten sessions of beta — long enough to
#: be a beta rather than the last hour's noise.
DEFAULT_VOL_WINDOW_MINUTES = 390
DEFAULT_BETA_WINDOW_MINUTES = 3900
#: A per-bar sd at or below this is a stale tape, not a quiet market;
#: dividing by it would manufacture an enormous label.
DEFAULT_VOL_FLOOR = 1e-8
#: What ``label_scale`` may say.
LABEL_SCALES = ("raw", "vol")
#: The label knobs, named ONCE (ADR-0059): :class:`NoInformationScan`
#: allows exactly these, :func:`_label_problems` validates them, and
#: :func:`_label_from_params` reads them. A knob added here and nowhere
#: else is a knob a document may set and nothing will honour.
LABEL_PARAMS = (
    "label_scale",
    "label_residual",
    "vol_window_minutes",
    "beta_window_minutes",
    "vol_floor",
)
#: The lead-grid knobs (ADR-0062), named ONCE. Declared on a run, they
#: override the universe's ``horizon`` block: the horizon is what a run
#: ASKS, not a fact about the cohort it asks over.
LEAD_PARAMS = ("lead_start", "lead_step", "lead_stop")


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
    label_lead = horizon.get("label_lead")
    if label_lead is not None and (
        isinstance(label_lead, bool)
        or not isinstance(label_lead, int)
        or label_lead < 1
    ):
        problems.append(
            f"horizon.label_lead must be an int >= 1, got {label_lead!r}"
        )
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
    problems.extend(_industry_problems(spec, tradable, symbols))
    problems.extend(_scan_problems(spec.get("scan"), lookback))
    problems.extend(_holdout_problems(spec.get("holdouts")))
    keep = spec.get("keep_features")
    if keep is not None and not _string_list_ok(keep):
        problems.append(
            f"keep_features must be a non-empty list of strings, got {keep!r}"
        )
    return problems


def _industry_problems(spec, tradable, symbols):
    """Problems with optional ``industry`` tags, empty when none."""
    industry = spec.get("industry")
    if industry is None:
        return []
    if not isinstance(industry, dict) or not industry:
        return [f"industry must be a non-empty object, got {industry!r}"]
    problems = []
    for symbol, tag in industry.items():
        if not isinstance(symbol, str) or not symbol:
            problems.append(f"industry keys must be symbols, got {symbol!r}")
        if not isinstance(tag, str) or not tag:
            problems.append(
                f"industry[{symbol!r}] must be a non-empty tag, got {tag!r}"
            )
    keys = set(industry)
    if symbols is not None:
        extra = sorted(keys - symbols)
        if extra:
            problems.append(f"industry names unknown symbols {extra}")
    if tradable is not None:
        missing = sorted(tradable - keys)
        if missing:
            problems.append(f"industry must tag every tradable name, missing {missing}")
    return problems


def _scan_problems(scan, lookback):
    """Problems with optional ``scan`` knobs, empty when absent."""
    if scan is None:
        return []
    if not isinstance(scan, dict):
        return [f"scan must be an object, got {scan!r}"]
    problems = []
    estimator = scan.get("estimator")
    if estimator is not None and (
        not isinstance(estimator, str) or "." not in estimator
    ):
        problems.append(
            "scan.estimator must be a dotted import path like "
            f"'lightgbm.LGBMRegressor', got {estimator!r}"
        )
    params = scan.get("estimator_params")
    if params is not None and not isinstance(params, dict):
        problems.append(
            f"scan.estimator_params must be an object, got {params!r}"
        )
    for knob, ge in (("l_start", 2), ("l_step", 1), ("lookback_stop", 2)):
        if knob not in scan:
            problems.append(f"scan.{knob} is required")
            continue
        value = scan.get(knob)
        if isinstance(value, bool) or not isinstance(value, int) or value < ge:
            problems.append(f"scan.{knob} must be an int >= {ge}, got {value!r}")
    start = scan.get("l_start")
    stop = scan.get("lookback_stop")
    if isinstance(start, int) and isinstance(stop, int) and stop < start:
        problems.append(
            f"scan.lookback_stop must be >= l_start, got {stop} < {start}"
        )
    if (
        isinstance(lookback, int)
        and isinstance(stop, int)
        and stop < lookback
    ):
        problems.append(
            f"scan.lookback_stop must be >= lookback, got {stop} < {lookback}"
        )
    for knob, lo, hi in (("keep_frac", 0.0, 1.0), ("keep_tau", 0.0, 1.0)):
        if knob not in scan:
            continue
        value = scan.get(knob)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not (lo < float(value) <= hi)
        ):
            problems.append(
                f"scan.{knob} must be in ({lo}, {hi}], got {value!r}"
            )
    picked = scan.get("picked_lookback")
    if picked is not None and (
        isinstance(picked, bool) or not isinstance(picked, int) or picked < 2
    ):
        problems.append(
            f"scan.picked_lookback must be an int >= 2, got {picked!r}"
        )
    return problems


def _holdout_problems(holdouts):
    """Problems with optional ``holdouts`` stamps, empty when absent."""
    if holdouts is None:
        return []
    if not isinstance(holdouts, dict):
        return [f"holdouts must be an object, got {holdouts!r}"]
    problems = []
    for knob in ("test_a_end_ms", "test_b_start_ms", "test_b_end_ms"):
        value = holdouts.get(knob)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(f"holdouts.{knob} must be an int >= 1, got {value!r}")
    a_end = holdouts.get("test_a_end_ms")
    b_start = holdouts.get("test_b_start_ms")
    b_end = holdouts.get("test_b_end_ms")
    if (
        isinstance(a_end, int)
        and isinstance(b_start, int)
        and b_start <= a_end
    ):
        problems.append(
            "holdouts.test_b_start_ms must be > test_a_end_ms, got "
            f"{b_start} <= {a_end}"
        )
    if (
        isinstance(b_start, int)
        and isinstance(b_end, int)
        and b_end < b_start
    ):
        problems.append(
            "holdouts.test_b_end_ms must be >= test_b_start_ms, got "
            f"{b_end} < {b_start}"
        )
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
    #: On the CLASS: a walk-forward builds a fresh source per fold, so an
    #: instance cache is never read twice. Measured on a 20-fold walk:
    #: 105 s per fold of re-scan (~60 s) plus re-hash (~45 s), invisible
    #: to every node timing because RESOLVE spends it before the fold's
    #: run dir exists. The key carries the store's CONTENT, so a stream
    #: that grew or was rewritten still re-scans — the cache answers
    #: "same bytes", never "same params".
    _cached_key = None
    _cached_snap = None
    _cached_fingerprint = None
    #: This instance's pin. Once a node has scanned, it answers from its
    #: own snapshot forever: the driver fingerprints at RESOLVE and runs
    #: at EXECUTE, and a store that grew in between must not move what
    #: this run's identity already covers.
    _key = None
    _fp = None

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

    def _cache_key(self):
        """Identity of the snapshot these params name, CONTENT included.

        The store's bytes are in the key, not its mtimes: an acquisition
        rewritten in place must invalidate the cache, and hashing 221 MB
        of gzip costs ~0.1 s against the ~140 s the scan it saves costs.
        """
        return (
            self.params["root"],
            self.params["source"],
            self.params.get("stream", BAR_STREAM),
            self.params.get("ts_field", DEFAULT_TS_FIELD),
            tuple(self.params.get("shared_fields", DEFAULT_SHARED_FIELDS)),
            self.params["universe"],
            _file_digest(self.params["universe"]),
            dir_digest(
                stream_dir(self.params["root"], self.params["source"])
            ),
        )

    def _scan(self):
        """Memoize the flattened, session-tagged snapshot."""
        if self._snap is not None:
            return self._snap
        cls = type(self)
        key = self._key = self._cache_key()
        if cls._cached_key == key and cls._cached_snap is not None:
            self._snap = cls._cached_snap
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
        cls._cached_key = key
        cls._cached_snap = tagged
        cls._cached_fingerprint = None  # a new snapshot, not yet hashed
        return self._snap

    def fingerprint(self):
        """Return a content-derived data identity.

        Hashing 8.9M records costs ~45 s, and RESOLVE asks for it once
        per fold, so the answer is cached beside the snapshot it
        describes — same key, same records, same digest.

        Returns
        -------
        dict
            ``kind``, ``rows``, and ``sha256``.
        """
        records = self._scan()
        if self._fp is not None:
            return dict(self._fp)
        cls = type(self)
        if cls._cached_key == self._key and cls._cached_fingerprint is not None:
            self._fp = dict(cls._cached_fingerprint)
            return dict(self._fp)
        self._fp = {
            "kind": "intraday_equities-bars",
            "rows": len(records),
            "sha256": stream_digest(records),
            "universe": _file_digest(self.params["universe"]),
        }
        if cls._cached_key == self._key:
            cls._cached_fingerprint = dict(self._fp)
        return dict(self._fp)

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


def session_feature_names(lookback, scales, reference, industries=()):
    """Name every column SessionFeatureRows emits besides identity.

    Parameters
    ----------
    lookback : int
        How many tape-local one-minute lags to spell.
    scales : sequence of dict
        Each item has ``tag``.
    reference : sequence of str
        Feature-only symbols that receive a residual column.
    industries : sequence of str
        Stable industry tags, one one-hot each.

    Returns
    -------
    tuple of str
        Feature names in emission order.
    """
    names = [f"ret_lag_{step}" for step in range(lookback)]
    for scale in scales:
        tag = scale["tag"]
        names.extend((
            f"ret_{tag}", f"rv_{tag}", f"range_{tag}",
            f"vol_{tag}", f"amihud_{tag}",
        ))
    names.extend((
        "clv",
        "minutes_from_open",
        "minutes_to_close",
        "tod_sin",
        "tod_cos",
        "dow_sin",
        "dow_cos",
        "month_sin",
        "month_cos",
        "is_first_rth",
        "is_last_rth",
        "overnight_gap",
        "session_gap_days",
        "after_holiday",
    ))
    for symbol in reference:
        names.append(f"ref_ret_{symbol}")
        names.append(f"residual_{symbol}")
    for tag in industries:
        names.append(f"industry_{tag}")
    return tuple(names)


def _emit_feature_names(lookback, scales, reference, industries, extra=()):
    """Session names plus vol-scaled momentum and extra-horizon ret/rv/mom."""
    names = list(session_feature_names(lookback, scales, reference, industries))
    names.extend(f"mom_{scale['tag']}" for scale in scales)
    for horizon in extra:
        tag = horizon["tag"]
        names.extend((f"ret_{tag}", f"rv_{tag}", f"mom_{tag}"))
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
    ms, opn, high, low, close, volume, lookback, max_gap_ms, holidays, scales,
    session, extra_horizons=(), scale_moms=False,
):
    """Build per-bar feature columns for one symbol's RTH tape."""
    import numpy as np

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
        vol_s = np.full(n, np.nan)
        amihud_s = np.full(n, np.nan)
        same_session = not scale["cross_session"]
        if n > width:
            ok = idx >= width
            if same_session:
                ok &= (idx - width) >= sess_start
            ret_s[ok] = logp[ok] - logp[idx[ok] - width]
        if n >= width:
            rv_s = rolling_std(ret1, width)
            hi = rolling_max(high, width)
            lo = rolling_min(low, width)
            with np.errstate(divide="ignore", invalid="ignore"):
                rng_s = np.log(hi / lo)
            vol_s = rolling_sum(volume, width)
            dollar = rolling_sum(close * volume, width)
            with np.errstate(divide="ignore", invalid="ignore"):
                amihud_s[width - 1:] = np.abs(ret_s[width - 1:]) / np.maximum(
                    dollar[width - 1:], 1e-12
                )
            if same_session:
                start_at = idx[width - 1:] - (width - 1)
                bad = start_at < sess_start[width - 1:]
                ret_s[width - 1:][bad] = np.nan
                rv_s[width - 1:][bad] = np.nan
                rng_s[width - 1:][bad] = np.nan
                vol_s[width - 1:][bad] = np.nan
                amihud_s[width - 1:][bad] = np.nan
        columns[f"ret_{tag}"] = ret_s
        columns[f"rv_{tag}"] = rv_s
        columns[f"range_{tag}"] = rng_s
        columns[f"vol_{tag}"] = vol_s
        columns[f"amihud_{tag}"] = amihud_s
    if scale_moms:
        with np.errstate(divide="ignore", invalid="ignore"):
            for scale in scales:
                tag = scale["tag"]
                columns[f"mom_{tag}"] = columns[f"ret_{tag}"] / columns[f"rv_{tag}"]
    for horizon in extra_horizons:
        width = int(horizon["width"])
        tag = horizon["tag"]
        same_session = not horizon["cross_session"]
        ret_s = np.full(n, np.nan)
        rv_s = np.full(n, np.nan)
        if n > width:
            ok = idx >= width
            if same_session:
                ok &= (idx - width) >= sess_start
            ret_s[ok] = logp[ok] - logp[idx[ok] - width]
        if n >= width:
            rv_s = rolling_std(ret1, width)
            if same_session:
                start_at = idx[width - 1:] - (width - 1)
                bad = start_at < sess_start[width - 1:]
                ret_s[width - 1:][bad] = np.nan
                rv_s[width - 1:][bad] = np.nan
        columns[f"ret_{tag}"] = ret_s
        columns[f"rv_{tag}"] = rv_s
        with np.errstate(divide="ignore", invalid="ignore"):
            columns[f"mom_{tag}"] = ret_s / rv_s
    if lookback == 0:
        columns["ret_lag_0"] = ret1
    spread = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        clv = np.where(spread > 0.0, (2.0 * close - high - low) / spread, 0.0)
    columns["clv"] = clv
    dates = []
    mins = []
    for stamp in ms:
        day, clock = _ny_date_minutes(int(stamp), session["tz"])
        dates.append(day)
        mins.append(clock)
    mins = np.asarray(mins, dtype=np.float64)
    columns["minutes_from_open"] = mins - session["rth_start_minutes"]
    columns["minutes_to_close"] = session["rth_end_minutes"] - mins
    span = max(float(session["rth_end_minutes"] - session["rth_start_minutes"]), 1.0)
    tod = 2.0 * math.pi * (mins - session["rth_start_minutes"]) / span
    columns["tod_sin"] = np.sin(tod)
    columns["tod_cos"] = np.cos(tod)
    parsed = [date.fromisoformat(day) for day in dates]
    dow = np.asarray([d.weekday() for d in parsed], dtype=np.float64)
    month = np.asarray([d.month for d in parsed], dtype=np.float64)
    columns["dow_sin"] = np.sin(2.0 * math.pi * dow / 7.0)
    columns["dow_cos"] = np.cos(2.0 * math.pi * dow / 7.0)
    columns["month_sin"] = np.sin(2.0 * math.pi * (month - 1.0) / 12.0)
    columns["month_cos"] = np.cos(2.0 * math.pi * (month - 1.0) / 12.0)
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


def _symbol_ohlcv(rows):
    """Sort one symbol's bars and lift OHLCV arrays."""
    import numpy as np

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
    volume = np.asarray(
        [float(row.get("volume", 0.0) or 0.0) for row in rows],
        dtype=np.float64,
    )
    return ms, opn, high, low, close, volume


def _grid_columns(
    ms, opn, high, low, close, volume, lookback, max_gap_ms, holidays,
    scales, session, offset_ms, period_ms, skip, extra_horizons, scale_moms,
):
    """Grid-aligned feature columns for one symbol; full-tape arrays drop."""
    columns = _session_feature_arrays(
        ms, opn, high, low, close, volume, lookback, max_gap_ms,
        holidays, scales, session, extra_horizons, scale_moms,
    )
    keep = ((ms - offset_ms) % period_ms) == 0
    names = [name for name in columns if name not in skip]
    kept_ms = ms[keep]
    kept_close = close[keep]
    col_kept = {name: columns[name][keep] for name in names}
    del columns
    return kept_ms, kept_close, col_kept


class SessionFeatureRows(Node):
    """Wide RTH feature rows: tape-local lags plus named session fields.

    Role ``transform`` — the ``intraday_equities-session-features`` kind.
    Every science knob arrives on the ``spec`` port from
    :class:`Universe`. One-minute lags never bridge a tape gap. The
    overnight move is its own field. A scale with ``cross_session``
    false stays inside a tape-continuous session; true reads back across
    the close. ``lookback`` 0 still keeps ``ret_lag_0`` internally for
    the SPY residual and does not emit lag columns.

    Parameters
    ----------
    params : dict
        Optional ``lookback`` (int >= 0) overrides ``spec.lookback``.
        Optional ``layout`` (``rows`` default, or ``columns``) keeps
        grid rows as numpy frames instead of one dict each. Optional
        ``momentum_horizons`` (non-empty list of scale objects) adds
        ``mom_{scale}`` on the universe scales plus ``ret``/``rv``/
        ``mom`` at each extra width.

    Examples
    --------
    Wire the universe object, then run::

        node = SessionFeatureRows("features", {})
        node.outputs  # ('records', 'tape')
    """

    role = "transform"
    outputs = ("records", "tape")
    _PARAMS = ("lookback", "layout", "momentum_horizons")

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
        lookback = params.get("lookback")
        if "lookback" in params and not is_node_ref(lookback):
            check_int_param(problems, "lookback", lookback, ge=0)
        layout = params.get("layout")
        if (
            "layout" in params
            and not is_node_ref(layout)
            and layout not in ("rows", "columns")
        ):
            problems.append(
                f"layout must be 'rows' or 'columns', got {layout!r}"
            )
        extra = params.get("momentum_horizons")
        if extra is not None and not is_node_ref(extra):
            problems.extend(
                p.replace("scales", "momentum_horizons", 1)
                for p in _scale_problems(extra)
            )
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
            extra = self.params.get("momentum_horizons")
            if extra:
                scale_tags = {
                    item.get("tag")
                    for item in spec.get("scales") or []
                    if isinstance(item, dict)
                }
                for i, horizon in enumerate(extra):
                    if not isinstance(horizon, dict):
                        continue
                    tag = horizon.get("tag")
                    if tag in scale_tags:
                        problems.append(
                            f"momentum_horizons[{i}].tag {tag!r} collides "
                            "with scales"
                        )
        return problems

    #: Last input signature and its build. A walk-forward runs this node
    #: once per fold, but nothing upstream of it reads ``$splits`` — the
    #: bars node memoizes its snapshot and the session filter is a pure
    #: function of that — so every fold rebuilds an identical frame. One
    #: build is reused for the rest of the walk.
    _cached_key = None
    _cached_out = None
    _cached_rows = 0

    def _input_signature(self, records, spec):
        """Cheap identity for the inputs, to key the fold cache.

        Hashing six million dicts would cost more than it saves, so this
        pins the input with its length, three sampled rows, and the spec
        and params verbatim. Two different record streams agreeing on
        all of those, from a deterministic upstream inside one process,
        is not a case that arises.
        """
        n = len(records)
        sample = (
            tuple(repr(records[i]) for i in (0, n // 2, n - 1)) if n else ()
        )
        return (
            n,
            sample,
            repr(sorted(spec.items(), key=lambda kv: kv[0])),
            repr(sorted(self.params.items(), key=lambda kv: kv[0])),
        )

    def run(self, ctx, inputs):
        """Emit grid-aligned feature rows with a named overnight field.

        Repeat calls with the same inputs return the first build. The
        outer list is copied so a caller may filter it, while the arrays
        are shared — copying those would defeat the point.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            ``records`` of RTH bars and ``spec`` from the universe node.

        Returns
        -------
        dict
            ``records`` (grid frames or rows) and ``tape`` (per-symbol
            1-minute ``asof_ms`` / ``close`` arrays).
        """
        import numpy as np

        spec = inputs["spec"]
        _sig = self._input_signature(inputs["records"], spec)
        if self._cached_key == _sig:
            cached = self._cached_out
            self.log.info(
                "session features: reusing cached build, %d row(s) "
                "from %d symbol(s)",
                self._cached_rows,
                len(cached["tape"]),
            )
            return {
                "records": list(cached["records"]),
                "tape": cached["tape"],
            }
        lookback = int(self.params["lookback"]) if "lookback" in self.params else int(spec["lookback"])
        max_gap_ms = float(spec["max_gap_minutes"]) * 60_000
        period_ms = int(spec["period_ms"])
        offset_ms = int(spec["offset_ms"])
        holidays = tuple(spec["holidays"])
        scales = list(spec["scales"])
        session = spec["session"]
        reference = tuple(spec["reference"])
        grouped = {}
        bar_rows = inputs["records"]
        for record in bar_rows:
            if not isinstance(record, dict):
                continue
            symbol = record.get("symbol")
            stamp = record.get("asof_ms")
            if not isinstance(symbol, str) or stamp is None:
                continue
            grouped.setdefault(symbol, []).append(record)
        # A live RTH tape is millions of dicts. Drop the shared list so
        # each symbol's bars become collectable after it emits. Probes
        # stay small and keep their input lists.
        if len(bar_rows) >= 256:
            bar_rows.clear()
        layout = self.params.get("layout", "rows")
        industry = spec.get("industry") or {}
        tags = tuple(sorted(set(industry.values())))
        extra = list(self.params.get("momentum_horizons") or ())
        scale_moms = bool(extra)
        if extra:
            feat_names = list(_emit_feature_names(
                lookback, scales, reference, tags, extra,
            ))
        else:
            feat_names = list(session_feature_names(
                lookback, scales, reference, tags,
            ))
        skip = set()
        for symbol in reference:
            skip.add(f"ref_ret_{symbol}")
            skip.add(f"residual_{symbol}")
        knobs = (
            lookback, max_gap_ms, holidays, scales, session,
            offset_ms, period_ms, skip, extra, scale_moms,
        )
        ref_ret = {symbol: {} for symbol in reference}
        order = [symbol for symbol in reference if symbol in grouped]
        seen = set(order)
        order.extend(
            symbol for symbol in grouped if symbol not in seen
        )
        records = []
        tape = []
        n_rows = 0
        for symbol in order:
            rows = grouped.pop(symbol)
            ms, opn, high, low, close, volume = _symbol_ohlcv(rows)
            del rows
            self.log.info("session features: %s", symbol)
            tape.append({
                "symbol": symbol,
                "asof_ms": ms,
                "close": close,
            })
            kept_ms, kept_close, col_kept = _grid_columns(
                ms, opn, high, low, close, volume, *knobs,
            )
            del opn, high, low, volume
            n = int(kept_ms.size)
            own = col_kept.get("ret_lag_0")
            if symbol in ref_ret and own is not None:
                table = ref_ret[symbol]
                for i, stamp in enumerate(kept_ms):
                    value = own[i]
                    if np.isfinite(value):
                        table[int(stamp)] = float(value)
            for ref in reference:
                ref_arr = np.full(n, np.nan)
                table = ref_ret[ref]
                for i in range(n):
                    value = table.get(int(kept_ms[i]))
                    if value is not None:
                        ref_arr[i] = value
                col_kept[f"ref_ret_{ref}"] = ref_arr
                residual = np.full(n, np.nan)
                if own is not None:
                    ok = np.isfinite(own) & np.isfinite(ref_arr)
                    residual[ok] = own[ok] - ref_arr[ok]
                col_kept[f"residual_{ref}"] = residual
            for tag in tags:
                col_kept[f"industry_{tag}"] = np.full(
                    n, 1.0 if industry.get(symbol) == tag else 0.0,
                )
            if layout == "columns":
                stacked = [
                    np.asarray(col_kept[name], dtype=np.float64)
                    if name in col_kept else np.full(n, np.nan)
                    for name in feat_names
                ]
                records.append({
                    "symbol": symbol,
                    "asof_ms": kept_ms,
                    "close": kept_close,
                    "names": feat_names,
                    "X": np.column_stack(stacked) if stacked else np.zeros((n, 0)),
                })
                del stacked
            else:
                for i in range(n):
                    row = {
                        "symbol": symbol,
                        "asof_ms": int(kept_ms[i]),
                        "close": _cell(kept_close[i]),
                    }
                    for name in feat_names:
                        arr = col_kept.get(name)
                        row[name] = _cell(arr[i]) if arr is not None else None
                    records.append(row)
            n_rows += n
            del col_kept
        if layout != "columns":
            records.sort(key=lambda row: (row["asof_ms"], row["symbol"]))
        self.log.info(
            "session features: %d row(s) from %d symbol(s) layout=%s",
            n_rows,
            len(tape),
            layout,
        )
        out = {"records": records, "tape": tape}
        # On the CLASS, not the instance: a walk-forward builds a fresh
        # node per fold, so an instance attribute would never be read
        # again. The signature keeps a second document from reading this
        # one's build.
        cls = type(self)
        cls._cached_key = _sig
        cls._cached_out = out
        cls._cached_rows = n_rows
        return {"records": list(records), "tape": tape}


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


def _fit_estimator(train_x, train_y, scan, categorical=None, feature_names=None):
    """Fit ``scan.estimator``, or a least-squares fallback (tests).

    ``feature_names`` names the columns of ``train_x`` in order. Like
    ``categorical_feature`` it is offered only to an estimator whose
    ``fit`` declares it (ADR-0061: a sequence model splits the row by
    NAME, and guessing that split by position is how a lag window
    silently becomes a feature vector).
    """
    import importlib

    import numpy as np

    path = scan.get("estimator")
    if path:
        module_name, _, attr = path.rpartition(".")
        try:
            cls = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise ValueError(
                f"scan.estimator {path!r} could not be imported ({exc})"
            ) from exc
        params = dict(scan.get("estimator_params") or {})
        params.pop("categorical_feature", None)
        model = cls(**params)
        # categorical_feature is a LightGBM-family kwarg; a linear
        # estimator refuses it, so only pass it where fit declares it.
        kwargs = {}
        if categorical is not None and _accepts_categorical(model):
            kwargs["categorical_feature"] = categorical
        if feature_names is not None and _accepts_kwarg(model, "feature_names"):
            kwargs["feature_names"] = list(feature_names)
        model.fit(train_x, train_y, **kwargs)
        return model


    class _Lstsq:
        """Intercept + linear map; stdlib stand-in when no estimator is set."""

        def fit(self, x, y):
            n = x.shape[0]
            design = np.column_stack([np.ones(n), x])
            self.coef_, *_ = np.linalg.lstsq(design, y, rcond=None)
            return self

        def predict(self, x):
            n = x.shape[0]
            design = np.column_stack([np.ones(n), x])
            return design @ self.coef_

    return _Lstsq().fit(train_x, train_y)


def _accepts_kwarg(model, name):
    """Report whether the estimator's fit accepts the keyword ``name``."""
    import inspect

    try:
        sig = inspect.signature(model.fit)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    return name in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _accepts_categorical(model):
    """Report whether the estimator's fit accepts ``categorical_feature``."""
    return _accepts_kwarg(model, "categorical_feature")


def _mspe(y, yhat):
    """Mean squared error of two equal-length sequences."""
    import numpy as np

    y = np.asarray(y, dtype=np.float64)
    yhat = np.asarray(yhat, dtype=np.float64)
    if y.size == 0:
        return 0.0
    return float(np.mean((y - yhat) ** 2))


def _fit_split_metrics(model, train_x, train_y, val_x, val_y):
    """Train vs val MSPE, Spearman IC, and ŷ spread at the training lead.

    <split>_yhat_sd is the forecast's own standard deviation. Zero
    means the estimator collapsed to a constant and no rank metric on it
    carries information.
    """
    import numpy as np

    out = {}
    for prefix, x, y in (("train", train_x, train_y), ("val", val_x, val_y)):
        if y.size < 2:
            out[f"{prefix}_mspe"] = 0.0
            out[f"{prefix}_ic"] = 0.0
            out[f"{prefix}_yhat_sd"] = 0.0
            continue
        hat = np.asarray(model.predict(x), dtype=np.float64)
        out[f"{prefix}_mspe"] = _mspe(y, hat)
        out[f"{prefix}_ic"] = float(_spearman(y, hat))
        out[f"{prefix}_yhat_sd"] = float(np.std(hat)) if hat.size else 0.0
    return out


def _hpo_cuts(train_end, val_days, embargo_days):
    """Inner tune window carved from fold train; fold val is unread."""
    inner_val_end = int(train_end)
    inner_val_start = inner_val_end - int(val_days) * _DAY_MS + 1
    inner_train_end = inner_val_start - int(embargo_days) * _DAY_MS - 1
    return inner_train_end, inner_val_start, inner_val_end


def _hpo_combos(base, space, trials, seed):
    """Draw unique random combos from a discrete space onto ``base``."""
    keys = sorted(space)
    rng = random.Random(seed)
    n_unique = 1
    for key in keys:
        n_unique *= len(space[key])
    target = min(int(trials), n_unique)
    seen = set()
    combos = []
    attempts = 0
    while len(combos) < target and attempts < target * 40:
        attempts += 1
        pick = tuple(rng.choice(space[key]) for key in keys)
        if pick in seen:
            continue
        seen.add(pick)
        row = dict(base)
        for key, value in zip(keys, pick):
            row[key] = value
        combos.append(row)
    return tuple(combos)


def _tune_estimator(
    scan, combos, train_x, train_y, val_x, val_y, categorical=None,
    objective="mspe", feature_names=None,
):
    """Pick a combo on the inner holdout under ``objective``.

    ``"mspe"`` takes the lowest squared error. At this signal-to-noise
    that systematically selects toward **underfitting**: the flattest
    model has the smallest error, and a constant forecast is the MSPE
    optimum outright. ``"ic"`` takes the highest Spearman rank
    correlation instead, which rewards ordering rather than magnitude
    and which a collapsed forecast cannot win — no rank variance
    scores exactly zero. Overfitting stays bounded either way, because
    the holdout is carved from train and the fold's own validation
    set is never read.

    Returns
    -------
    tuple
        ``(params, score)`` where score is the winning objective
        value — MSPE (lower better) or IC (higher better).
    """
    base = dict(scan.get("estimator_params") or {})
    best_params = base
    best = None
    for trial in combos:
        trial_scan = dict(scan)
        trial_scan["estimator_params"] = trial
        model = _fit_estimator(
            train_x, train_y, trial_scan, categorical=categorical,
            feature_names=feature_names,
        )
        hat = model.predict(val_x)
        if objective == "ic":
            score = -_spearman(val_y, hat)  # minimize the negative
        else:
            score = _mspe(val_y, hat)
        if best is None or score < best:
            best = score
            best_params = trial
    if best is None:
        return best_params, 0.0
    return best_params, float(-best if objective == "ic" else best)


def _model_ic(train_x, train_y, val_x, val_y, names, scan):
    """Fit the declared estimator and score Spearman IC on both folds."""
    import numpy as np

    model = _fit_estimator(train_x, train_y, scan)
    pred_tr = np.asarray(model.predict(train_x), dtype=np.float64)
    pred_va = np.asarray(model.predict(val_x), dtype=np.float64)
    importance = getattr(model, "feature_importances_", None)
    if importance is None:
        selected = list(names)
    else:
        order = sorted(
            range(len(names)), key=lambda i: float(importance[i]), reverse=True
        )
        selected = [names[i] for i in order[: min(8, len(order))]]
    return _spearman(pred_tr, train_y), _spearman(pred_va, val_y), selected


def _score_ic(train_x, train_y, val_x, val_y, names, spec):
    """Dispatch combo-IC or the declared estimator."""
    scan = spec.get("scan") or {}
    if scan.get("estimator"):
        return _model_ic(train_x, train_y, val_x, val_y, names, scan)
    top_k = int((spec.get("horizon") or {}).get("top_k") or 5)
    return _combo_ic(train_x, train_y, val_x, val_y, names, top_k)


def _is_frame(obj):
    """Report whether ``obj`` is a columnar frame, not a row dict."""
    if not isinstance(obj, dict):
        return False
    names = obj.get("names")
    return (
        isinstance(names, (list, tuple))
        and obj.get("X") is not None
        and getattr(obj.get("asof_ms"), "shape", None) is not None
    )


def _feature_names_for_rows(spec, records):
    """Session-feature names matching the rows' lag depth.

    ``spec.features`` wins when the rows were built at
    ``spec.lookback``. A lookback override on
    :class:`SessionFeatureRows` emits more ``ret_lag_*`` columns; those
    rows rebuild the name list so a scan never silently drops them.
    Columnar frames carry ``names`` directly.
    """
    if records and _is_frame(records[0]):
        return list(records[0]["names"])
    industries = tuple(sorted(set((spec.get("industry") or {}).values())))
    row_lookback = 0
    if records:
        while f"ret_lag_{row_lookback}" in records[0]:
            row_lookback += 1
    declared = spec.get("features")
    if declared and row_lookback <= int(spec["lookback"]):
        return list(declared)
    lookback = row_lookback if row_lookback >= 2 else int(spec["lookback"])
    return list(session_feature_names(
        lookback, spec["scales"], spec["reference"], industries,
    ))


_ALWAYS_KEEP_NAMES = frozenset({
    "minutes_from_open", "minutes_to_close",
    "tod_sin", "tod_cos", "dow_sin", "dow_cos", "month_sin", "month_cos",
})
_ALWAYS_KEEP_PREFIXES = ("industry_",)


def _always_keep(name):
    """Calendar and static columns survive every importance cut."""
    return name in _ALWAYS_KEEP_NAMES or name.startswith(_ALWAYS_KEEP_PREFIXES)


def _lookback_columns(names, lookback):
    """``ret_lag_0..L-1`` plus every non-lag column, in emission order."""
    lags = [f"ret_lag_{i}" for i in range(lookback) if f"ret_lag_{i}" in names]
    rest = [name for name in names if not name.startswith("ret_lag_")]
    return lags + rest


def _take_cols(matrix, names, wanted):
    """Slice ``matrix`` to ``wanted`` columns by name."""
    index = {name: i for i, name in enumerate(names)}
    return matrix[:, [index[name] for name in wanted]]


def _lookback_grid(scan, available, lead):
    """L values from the JSON floor up through ``min(available, stop, 2H)``."""
    start = int(scan.get("l_start", 2))
    step = int(scan.get("l_step", 5))
    stop = int(scan.get("lookback_stop", available))
    hi = min(available, stop)
    if lead:
        hi = min(hi, max(start, 2 * int(lead)))
    start = min(start, hi) if hi else 1
    if hi < 1:
        return (max(available, 1),)
    grid = tuple(range(start, hi + 1, step))
    return grid if grid else (hi,)


def _keep_by_importance(names, weights, keep_frac, keep_tau):
    """Keep until cumulative ``keep_frac``, or weight ≥ ``keep_tau`` of max."""
    if not names:
        return []
    max_w = max(float(w) for w in weights) if weights else 0.0
    total = sum(max(float(w), 0.0) for w in weights)
    tau_cut = keep_tau * max_w
    order = sorted(range(len(names)), key=lambda i: float(weights[i]), reverse=True)
    kept = set()
    acc = 0.0
    for i in order:
        name = names[i]
        weight = max(float(weights[i]), 0.0)
        if _always_keep(name):
            kept.add(name)
            acc += weight
            continue
        if total > 0.0 and acc < keep_frac * total:
            kept.add(name)
            acc += weight
            continue
        if tau_cut > 0.0 and weight >= tau_cut:
            kept.add(name)
    for name in names:
        if _always_keep(name):
            kept.add(name)
    return [name for name in names if name in kept]


def _column_weights(train_x, train_y, names, scan):
    """Train-only importance: estimator weights, else |Spearman IC|."""
    import importlib

    import numpy as np

    if scan.get("estimator"):
        path = scan["estimator"]
        module_name, _, attr = path.rpartition(".")
        cls = getattr(importlib.import_module(module_name), attr)
        model = cls(**dict(scan.get("estimator_params") or {}))
        model.fit(train_x, train_y)
        importance = getattr(model, "feature_importances_", None)
        if importance is not None:
            return [float(value) for value in importance]
        coef = getattr(model, "coef_", None)
        if coef is not None:
            return [abs(float(value)) for value in np.ravel(coef)]
    return [
        abs(_spearman(train_x[:, col], train_y))
        for col in range(len(names))
    ]


def _lookback_verdict(curve):
    """Smallest L whose |IC| is within 1 SE of the peak."""
    peak = max(curve, key=lambda row: abs(row["ic_val"]))
    thresh = abs(peak["ic_val"]) - peak["se"]
    within = [row for row in curve if abs(row["ic_val"]) >= thresh]
    picked = min(within, key=lambda row: row["lookback"])
    return peak, picked


def _attach_lead_rows(
    bars, records, lead, price_field, split, train_end, val_start, val_end,
    label, features,
):
    """Copy feature rows that have a finite RTH-tape label at ``lead``.

    Labels count 1-minute RTH bars, so they cross the close. A label
    that would land after ``val_end`` is dropped (lockbox unread). Train
    also requires the landing stamp to be at or before ``train_end``.
    Columnar frames emit compact row dicts (identity + ``features`` +
    the label), not a copy of ``X``.
    """
    import numpy as np

    arrays = _tapes_from_bars(bars, price_field, val_end)
    if records and _is_frame(records[0]):
        return _attach_lead_frames(
            arrays, records, lead, split, train_end, val_start, val_end,
            label, features,
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
        attached["y_up"] = 1.0 if y > 0.0 else 0.0
        out.append(attached)
    return out


def _attach_lead_frames(
    arrays, records, lead, split, train_end, val_start, val_end, label,
    features,
):
    """Label columnar frames and emit compact row dicts."""
    import numpy as np

    out = []
    for frame in records:
        symbol = frame.get("symbol")
        tape = arrays.get(symbol)
        if not isinstance(symbol, str) or tape is None:
            continue
        stamps, x = _frame_matrix(frame, features)
        if stamps.size == 0:
            continue
        t_ms, t_px = tape
        loc = np.searchsorted(t_ms, stamps)
        future = loc + lead
        ok = (
            (loc < t_ms.size)
            & (t_ms[np.minimum(loc, t_ms.size - 1)] == stamps)
            & (future < t_ms.size)
        )
        if not ok.any():
            continue
        idx = np.flatnonzero(ok)
        px0 = t_px[loc[idx]]
        px1 = t_px[future[idx]]
        valid_px = (px0 > 0.0) & (px1 > 0.0)
        y = np.full(idx.size, np.nan)
        y[valid_px] = np.log(px1[valid_px] / px0[valid_px])
        future_ms = t_ms[future[idx]]
        asof = stamps[idx]
        if split == "train":
            keep = (asof <= train_end) & (future_ms <= train_end)
        else:
            keep = (
                (val_start <= asof) & (asof <= val_end) & (future_ms <= val_end)
            )
        keep &= np.isfinite(y)
        for j in np.flatnonzero(keep):
            i = int(idx[j])
            row = {
                name: float(x[i, k]) for k, name in enumerate(features)
            }
            row["symbol"] = symbol
            row["asof_ms"] = int(asof[j])
            row[label] = float(y[j])
            row["y_up"] = 1.0 if y[j] > 0.0 else 0.0
            out.append(row)
    return out


def _tapes_from_bars(bars, price_field, val_end):
    """Per-symbol 1-minute close arrays, stamps after ``val_end`` dropped."""
    import numpy as np

    if (
        bars
        and getattr(bars[0].get("asof_ms"), "shape", None) is not None
        and "X" not in bars[0]
    ):
        arrays = {}
        for frame in bars:
            symbol = frame.get("symbol")
            if not isinstance(symbol, str):
                continue
            t_ms = np.asarray(frame["asof_ms"], dtype=np.int64)
            t_px = np.asarray(frame["close"], dtype=np.float64)
            keep = t_ms <= val_end
            arrays[symbol] = (t_ms[keep], t_px[keep])
        return arrays
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
    return arrays


def _frame_matrix(frame, features):
    """Slice one columnar frame to ``features``, dropping non-finite rows."""
    import numpy as np

    stamps = np.asarray(frame["asof_ms"], dtype=np.int64)
    matrix = np.asarray(frame["X"], dtype=np.float64)
    names = list(frame["names"])
    if names == list(features):
        x = matrix
    else:
        index = {name: i for i, name in enumerate(names)}
        missing = [name for name in features if name not in index]
        if missing:
            return stamps[:0], matrix[:0]
        x = matrix[:, [index[name] for name in features]]
    finite = (
        np.ones(stamps.size, dtype=bool)
        if x.ndim < 2 or x.shape[1] == 0
        else np.isfinite(x).all(axis=1)
    )
    return stamps[finite], x[finite]


def _symbol_codes(spec, prepared):
    """Stable integer codes: tradable order, then any extra names."""
    names = list(spec.get("tradable") or [])
    seen = set(names)
    for item in prepared:
        if item[0] not in seen:
            names.append(item[0])
            seen.add(item[0])
    return {name: i for i, name in enumerate(names)}


def _attach_symbol_codes(prepared, codes):
    """Append a last column of integer symbol codes for LightGBM."""
    import numpy as np

    out = []
    for item in prepared:
        symbol, stamps, x, loc, match, t_ms, t_px = item
        code = float(codes[symbol])
        stacked = np.column_stack([
            x, np.full(x.shape[0], code, dtype=np.float64),
        ])
        out.append((symbol, stamps, stacked, loc, match, t_ms, t_px))
    return out


def _scan_aligned(bars, records, features, price_field, val_end, arrays=None):
    """Align finite feature rows to the 1-minute tape, dropping lockbox stamps.

    ``arrays`` is :func:`_tapes_from_bars`'s return when the caller
    already built it (the label reads the same tapes); ``None`` builds it.
    """
    import numpy as np

    if arrays is None:
        arrays = _tapes_from_bars(bars, price_field, val_end)
    prepared = []
    if records and _is_frame(records[0]):
        for frame in records:
            symbol = frame.get("symbol")
            tape = arrays.get(symbol)
            if not isinstance(symbol, str) or tape is None:
                continue
            stamps, x = _frame_matrix(frame, features)
            keep = stamps <= val_end
            stamps, x = stamps[keep], x[keep]
            if stamps.size == 0:
                continue
            t_ms, t_px = tape
            loc = np.searchsorted(t_ms, stamps)
            match = (
                (loc < t_ms.size)
                & (t_ms[np.minimum(loc, t_ms.size - 1)] == stamps)
            )
            prepared.append((symbol, stamps, x, loc, match, t_ms, t_px))
        prepared.sort(key=lambda item: item[0])
        return prepared
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
    for symbol, rows in grouped.items():
        tape = arrays.get(symbol)
        if tape is None:
            continue
        t_ms, t_px = tape
        stamps = np.asarray([item[0] for item in rows], dtype=np.int64)
        x = np.asarray([item[1] for item in rows], dtype=np.float64)
        loc = np.searchsorted(t_ms, stamps)
        match = (loc < t_ms.size) & (t_ms[np.minimum(loc, t_ms.size - 1)] == stamps)
        prepared.append((symbol, stamps, x, loc, match, t_ms, t_px))
    prepared.sort(key=lambda item: item[0])
    return prepared


def _raw_lead_return(prices, loc, future):
    """Log return from each ``loc`` bar to its ``future`` bar.

    The ONE definition of the scan's untransformed label — both fold
    builders and :class:`_LeadLabel` read it, so a change to what "the
    return" means cannot land in one place and miss the other.

    Parameters
    ----------
    prices : numpy.ndarray
        One symbol's 1-minute closes.
    loc, future : numpy.ndarray of int
        Index pairs into ``prices``.

    Returns
    -------
    numpy.ndarray
        One float64 per pair, NaN wherever either end is not positive.
    """
    import numpy as np

    px0 = prices[loc]
    px1 = prices[future]
    out = np.full(px0.shape, np.nan, dtype=np.float64)
    pos = (px0 > 0.0) & (px1 > 0.0)
    out[pos] = np.log(px1[pos] / px0[pos])
    return np.where(np.isfinite(out), out, np.nan)


def _bar_returns(stamps, prices, period_ms):
    """One-bar log returns with session boundaries blanked."""
    import numpy as np

    returns = log_return(prices, 1)
    if stamps.size > 1:
        # A gap over two bars is an overnight or a halt: the move across
        # it is not a 1-minute return and must not enter sigma or beta.
        returns[1:][np.diff(stamps) > 2 * period_ms] = np.nan
    return returns


def _align_returns(stamps, ref_stamps, ref_returns):
    """Put a reference's per-bar returns on ``stamps``' index, NaN off-grid."""
    import numpy as np

    out = np.full(stamps.size, np.nan)
    if ref_stamps.size == 0 or stamps.size == 0:
        return out
    loc = np.searchsorted(ref_stamps, stamps)
    safe = np.minimum(loc, ref_stamps.size - 1)
    good = (loc < ref_stamps.size) & (ref_stamps[safe] == stamps)
    good[0] = False  # a per-bar return needs the bar before it too
    later = np.flatnonzero(good)
    if later.size:
        prev = np.maximum(loc[later] - 1, 0)
        keep = (loc[later] > 0) & (ref_stamps[prev] == stamps[later - 1])
        good[later[~keep]] = False
    out[good] = ref_returns[loc[good]]
    return out


def _label_problems(params):
    """Problems with the declared label knobs, empty when none (ADR-0059)."""
    problems = []
    scale = params.get("label_scale")
    if scale is not None and scale not in LABEL_SCALES:
        problems.append(
            f"label_scale must be one of {list(LABEL_SCALES)}, got {scale!r}"
        )
    reference = params.get("label_residual")
    if reference is not None and (
        not isinstance(reference, str) or not reference
    ):
        problems.append(
            "label_residual must be a reference symbol on the tape, got "
            f"{reference!r}"
        )
    for knob in ("vol_window_minutes", "beta_window_minutes"):
        if params.get(knob) is not None:
            check_int_param(problems, knob, params.get(knob), ge=2)
    floor = params.get("vol_floor")
    if floor is not None and (
        isinstance(floor, bool)
        or not isinstance(floor, (int, float))
        or floor <= 0.0
    ):
        problems.append(f"vol_floor must be a number > 0, got {floor!r}")
    return problems


def _lead_problems(params):
    """Problems with the declared lead grid, empty when none (ADR-0062)."""
    problems = []
    for knob in LEAD_PARAMS:
        if params.get(knob) is not None:
            check_int_param(problems, knob, params.get(knob), ge=1)
    start, stop = params.get("lead_start"), params.get("lead_stop")
    if (
        isinstance(start, int) and not isinstance(start, bool)
        and isinstance(stop, int) and not isinstance(stop, bool)
        and stop < start
    ):
        problems.append(
            f"lead_stop must be >= lead_start, got {stop} < {start}"
        )
    return problems


def _lead_grid(params, horizon):
    """Resolve the grid this run asks about, as ``(leads, train_lead)``.

    A declared knob wins over the universe's ``horizon`` block; an
    absent one falls back to it, so a document that declares nothing
    computes exactly what it did before ADR-0062.
    """
    def pick(knob):
        declared = params.get(knob)
        return int(horizon[knob] if declared is None else declared)

    start, step, stop = pick("lead_start"), pick("lead_step"), pick("lead_stop")
    return horizon_leads(start, step, stop), start


def _label_from_params(params, arrays, period_ms):
    """Build the run's :class:`_LeadLabel` from declared knobs (ADR-0059)."""
    return _LeadLabel(
        arrays,
        period_ms,
        scale=params.get("label_scale") or "raw",
        residual=params.get("label_residual"),
        vol_window=int(
            params.get("vol_window_minutes") or DEFAULT_VOL_WINDOW_MINUTES
        ),
        beta_window=int(
            params.get("beta_window_minutes") or DEFAULT_BETA_WINDOW_MINUTES
        ),
        vol_floor=float(params.get("vol_floor") or DEFAULT_VOL_FLOOR),
    )


class _LeadLabel:
    """The scan's label ``y(t, h)``: raw, market-residual, vol-normalised.

    ADR-0059. Both transforms are causal: ``beta`` and ``sigma`` read
    only bars at or before ``t``, while the label itself reads
    ``t -> t+h``. Composed, the residual is taken first and the vol of
    the RESIDUAL return scales it.

    Parameters
    ----------
    arrays : dict
        ``{symbol: (stamps, prices)}`` — :func:`_tapes_from_bars`'s return.
    period_ms : int
        One bar's width; a gap over twice this ends a session.
    scale : str, optional
        ``"raw"`` (default) or ``"vol"`` — divide by
        ``sigma_t * sqrt(h)``.
    residual : str, optional
        Reference symbol to subtract at ``beta_t``; ``None`` (default)
        keeps the name's own return.
    vol_window, beta_window : int, optional
        Rolling widths in bars, default 390 and 3900.
    vol_floor : float, optional
        Divisors at or below this refuse the row, default 1e-8.

    Raises
    ------
    ValueError
        On an unknown ``scale``, or a ``residual`` symbol with no tape —
        a missing reference is a refusal, never a silent NaN column.

    Examples
    --------
    Vol-normalised, market-residual labels off a two-bar tape::

        import numpy as np
        arrays = {
            "JPM": (np.array([0, 60_000]), np.array([10.0, 10.1])),
            "SPY": (np.array([0, 60_000]), np.array([20.0, 20.1])),
        }
        label = _LeadLabel(arrays, 60_000, scale="vol", residual="SPY")
        label.values("JPM", np.array([0]), np.array([1]))
        # -> array([nan])  # two bars cannot fill a 390-wide vol window
    """

    def __init__(
        self, arrays, period_ms, scale="raw", residual=None,
        vol_window=DEFAULT_VOL_WINDOW_MINUTES,
        beta_window=DEFAULT_BETA_WINDOW_MINUTES,
        vol_floor=DEFAULT_VOL_FLOOR,
    ):
        if scale not in LABEL_SCALES:
            raise ValueError(
                f"label_scale must be one of {LABEL_SCALES}, got {scale!r}"
            )
        if residual is not None and residual not in arrays:
            raise ValueError(
                f"label_residual {residual!r} has no tape — the reference "
                f"must be one of {sorted(arrays)}"
            )
        self.arrays = arrays
        self.period_ms = int(period_ms)
        self.scale = scale
        self.residual = residual
        self.vol_window = int(vol_window)
        self.beta_window = int(beta_window)
        self.vol_floor = float(vol_floor)
        self._prepared_by_symbol = {}

    @property
    def transformed(self):
        """Whether this label is anything but the raw log return."""
        return self.scale != "raw" or self.residual is not None

    def describe(self):
        """Name this label for a run record, as a short string."""
        parts = [self.scale]
        if self.residual is not None:
            parts.append(f"residual:{self.residual}")
        return "+".join(parts)

    def _prepare(self, symbol):
        """Per-bar ``(beta, sigma)`` for one symbol, computed once."""
        import numpy as np

        if symbol in self._prepared_by_symbol:
            return self._prepared_by_symbol[symbol]
        stamps, prices = self.arrays[symbol]
        own = _bar_returns(stamps, prices, self.period_ms)
        beta = None
        effective = own
        if self.residual is not None:
            ref_stamps, ref_prices = self.arrays[self.residual]
            ref = _align_returns(
                stamps, ref_stamps,
                _bar_returns(ref_stamps, ref_prices, self.period_ms),
            )
            # Zero-mean cross products: a 1-minute mean return is noise,
            # and blanking NaNs to 0 drops a bar from BOTH sums at once,
            # which rolling_sum (NaN-propagating by contract) will not do.
            own_0 = np.where(np.isfinite(own), own, 0.0)
            ref_0 = np.where(np.isfinite(ref), ref, 0.0)
            cov = rolling_sum(own_0 * ref_0, self.beta_window)
            var = rolling_sum(ref_0 * ref_0, self.beta_window)
            with np.errstate(divide="ignore", invalid="ignore"):
                beta = np.where(var > 0.0, cov / var, np.nan)
            effective = own - beta * ref
        sigma = (
            rolling_std(effective, self.vol_window)
            if self.scale == "vol" else None
        )
        self._prepared_by_symbol[symbol] = (beta, sigma)
        return self._prepared_by_symbol[symbol]

    def values(self, symbol, loc, future):
        """Label each ``(loc, future)`` bar pair of one symbol.

        Parameters
        ----------
        symbol : str
            Whose tape to read.
        loc, future : numpy.ndarray of int
            Index pairs into that symbol's tape.

        Returns
        -------
        numpy.ndarray
            One float64 per pair, NaN wherever the label is undefined —
            a non-positive price, a reference bar missing at either end,
            or a rolling window that has not filled.
        """
        import numpy as np

        stamps, prices = self.arrays[symbol]
        y = _raw_lead_return(prices, loc, future)
        if not self.transformed:
            return y
        beta, sigma = self._prepare(symbol)
        if beta is not None:
            ref_stamps, ref_prices = self.arrays[self.residual]
            size = ref_stamps.size
            j0 = np.searchsorted(ref_stamps, stamps[loc])
            j1 = np.searchsorted(ref_stamps, stamps[future])
            ok = (
                (j0 < size)
                & (j1 < size)
                & (ref_stamps[np.minimum(j0, size - 1)] == stamps[loc])
                & (ref_stamps[np.minimum(j1, size - 1)] == stamps[future])
            )
            y_ref = np.full(y.shape, np.nan)
            if np.any(ok):
                y_ref[ok] = _raw_lead_return(ref_prices, j0[ok], j1[ok])
            y = y - beta[loc] * y_ref
        if sigma is not None:
            own_sd = sigma[loc]
            with np.errstate(divide="ignore", invalid="ignore"):
                scaled = y / (own_sd * np.sqrt(np.maximum(future - loc, 1)))
            y = np.where(own_sd > self.vol_floor, scaled, np.nan)
        return np.where(np.isfinite(y), y, np.nan)


def _scan_fold(prepared, lead, train_end, val_start, val_end, label=None):
    """Collect train/val matrices for one lead; labels never land after val_end.

    ``label`` is the :class:`_LeadLabel` this run declared (ADR-0059);
    ``None`` means the raw log return.
    """
    import numpy as np

    train_x, train_y, val_x, val_y = [], [], [], []
    n_features = prepared[0][2].shape[1] if prepared else 0
    for item in prepared:
        stamps, x, loc, match, t_ms, t_px = item[-6:]
        future = loc + lead
        ok = match & (future < t_ms.size)
        if not np.any(ok):
            continue
        loc_ok = loc[ok]
        fut_ok = future[ok]
        y = (
            label.values(item[0], loc_ok, fut_ok) if label is not None
            else _raw_lead_return(t_px, loc_ok, fut_ok)
        )
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


def _scan_fold_stamped(
    prepared, lead, train_end, val_start, val_end, train_start=None,
    label=None,
):
    """Like :func:`_scan_fold`, plus val stamps aligned with val rows.

    ``train_start`` is the walk-forward's left training bound
    (``splits.train_start_ms``, ADR-0050). ``None`` means all-prior,
    which is what this builder did unconditionally before, and why a
    declared ``train_days`` had no effect on the fitted window.
    ``label`` is the :class:`_LeadLabel` this run declared (ADR-0059);
    ``None`` means the raw log return.
    """
    import numpy as np

    train_x, train_y, val_x, val_y, val_stamps = [], [], [], [], []
    n_features = prepared[0][2].shape[1] if prepared else 0
    for item in prepared:
        stamps, x, loc, match, t_ms, t_px = item[-6:]
        future = loc + lead
        ok = match & (future < t_ms.size)
        if not np.any(ok):
            continue
        loc_ok = loc[ok]
        fut_ok = future[ok]
        y = (
            label.values(item[0], loc_ok, fut_ok) if label is not None
            else _raw_lead_return(t_px, loc_ok, fut_ok)
        )
        finite = np.isfinite(y)
        if not np.any(finite):
            continue
        stamp = stamps[ok][finite]
        future_ms = t_ms[fut_ok][finite]
        x_ok = x[ok][finite]
        y = y[finite]
        train = (stamp <= train_end) & (future_ms <= train_end)
        if train_start is not None:
            train &= stamp >= train_start
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
            val_stamps.append(stamp[val])
    empty_x = np.zeros((0, n_features), dtype=np.float64)
    empty_y = np.zeros(0, dtype=np.float64)
    empty_t = np.zeros(0, dtype=np.int64)
    return (
        np.concatenate(train_x) if train_x else empty_x,
        np.concatenate(train_y) if train_y else empty_y,
        np.concatenate(val_x) if val_x else empty_x,
        np.concatenate(val_y) if val_y else empty_y,
        np.concatenate(val_stamps) if val_stamps else empty_t,
    )


def _blank_lead_row(symbol, lead, lags):
    """One no-information grid row with no usable pairs."""
    return {
        "symbol": symbol,
        "lead": lead,
        "p_value": 1.0,
        "t_stat": 0.0,
        "se": 0.0,
        "beats_mean": 0.0,
        "mspe_model": 0.0,
        "mspe_mean": 0.0,
        "n": 0.0,
        "lags": float(lags),
        "r2oos": 0.0,
        "dm_t": 0.0,
        "dm_p": 1.0,
    }


def _lead_skill(symbol, lead, h_steps, y, yhat, mu, mspe_mean, stamps):
    """One lead's ADR-0063 loss gaps, plus the fold's own DM verdict.

    ``d_t = (y-mu)^2 - (y-yhat)^2`` against the fold's TRAIN mean: the
    unadjusted difference whose sign is the sign of the realized MSPE
    gap, which is what decides whether a forecast is worth having.
    Clark-West answers a different question and stays beside it.

    Parameters
    ----------
    symbol : str
        The series these rows belong to.
    lead : int
        The horizon in minutes.
    h_steps : int
        The horizon in row-spacing steps (the DM overlap).
    y : list of float
        Realized labels, in time order.
    yhat : list of float
        Forecasts, same length and order.
    mu : float
        The fold's training mean of this series' label.
    mspe_mean : float
        The benchmark's mean squared error on these rows.
    stamps : list of int
        Row timestamps in ms, aligned with ``y``.

    Returns
    -------
    dict
        ``symbol``, ``lead``, ``h_steps``, ``q`` (the benchmark MSPE),
        ``stamps``, ``d``, plus this fold's ``r2oos``, ``dm_t``, ``dm_p``.
    """
    from dskit.pipeline.stats import diebold_mariano_test, dm_lags, dm_loss_series

    gaps = dm_loss_series(y, yhat, mu=mu)
    q = float(mspe_mean)
    mean_gap = sum(gaps) / len(gaps)
    dm = diebold_mariano_test(
        gaps, lags=dm_lags(len(gaps), h_steps), h_steps=h_steps,
    )
    return {
        "symbol": symbol,
        "lead": lead,
        "h_steps": h_steps,
        "q": q,
        "stamps": [int(s) for s in stamps],
        "d": [float(v) for v in gaps],
        "r2oos": mean_gap / q if q > 0.0 else 0.0,
        "dm_t": 0.0 if dm["t"] is None else float(dm["t"]),
        "dm_p": float(dm["p_value"]),
    }


def _walk_no_information_series(
    prepared_one, model, leads, train_end, val_start, val_end, period_minutes,
    train_start=None, label=None,
):
    """Sequential h* for one name; GO is that H or none.

    ``μ(h)`` is this series' train mean of ``y(h)``. The estimator is
    fitted outside (pooled train) and only scored here.
    """
    import numpy as np

    from dskit.pipeline.stats import max_informative_horizon, no_information_test

    symbol = prepared_one[0]
    curve = []
    ordered = []
    skill = []
    for lead in leads:
        lags = max(lead // period_minutes - 1, 0)
        if model is None:
            row = _blank_lead_row(symbol, lead, lags)
            curve.append(row)
            ordered.append({"horizon": lead, "p_value": 1.0})
            continue
        _, tr_y, val_x, val_y, val_stamps = _scan_fold_stamped(
            [prepared_one], lead, train_end, val_start, val_end,
            train_start=train_start, label=label,
        )
        mu = float(tr_y.mean()) if tr_y.size else 0.0
        if val_x.shape[0] < 2:
            row = _blank_lead_row(symbol, lead, lags)
            row["lags"] = float(lags)
            curve.append(row)
            ordered.append({"horizon": lead, "p_value": 1.0})
            continue
        yhat = np.asarray(model.predict(val_x), dtype=np.float64)
        if val_y.size < 2 or lags >= val_y.size:
            row = _blank_lead_row(symbol, lead, lags)
            row["n"] = float(val_y.size)
            curve.append(row)
            ordered.append({"horizon": lead, "p_value": 1.0})
            continue
        out = no_information_test(
            val_y.tolist(), yhat.tolist(), mu=mu, lags=lags, horizon=lead,
        )
        row = {
            "symbol": symbol,
            "lead": lead,
            "p_value": float(out["p_value"]),
            # The Clark-West statistic BEHIND the p-value: a verdict a
            # reader cannot see the t of is a verdict they must take on
            # faith, and the walk's whole output is a p per lead.
            "t_stat": float(out["t"]),
            "se": float(out["se"]),
            "beats_mean": float(out["beats_mean"]),
            "mspe_model": float(out["mspe_model"]),
            "mspe_mean": float(out["mspe_mean"]),
            "n": float(out["n"]),
            "lags": float(out["lags"]),
        }
        record = _lead_skill(
            symbol, lead, max(lead // period_minutes, 1),
            val_y.tolist(), yhat.tolist(), mu, out["mspe_mean"],
            val_stamps.tolist(),
        )
        skill.append(record)
        for key in ("r2oos", "dm_t", "dm_p"):
            row[key] = record[key]
        curve.append(row)
        ordered.append({"horizon": lead, "p_value": out["p_value"]})
    walked = max_informative_horizon(ordered, alpha=_NO_INFO_ALPHA)
    h_star = walked["h_star"]
    first = curve[0] if curve else {}
    go = 1.0 if h_star is not None else 0.0
    return curve, {
        "go": go,
        "h_star": float(h_star if h_star is not None else 0.0),
        "p_value": float(first.get("p_value", 1.0)),
        "t_stat": float(first.get("t_stat", 0.0)),
        "r2oos": float(first.get("r2oos", 0.0)),
        "dm_t": float(first.get("dm_t", 0.0)),
    }, skill


class HorizonScan(Node):
    """Rank-IC curve over the universe lead grid, plus a go/no-go.

    Role ``score`` — the ``intraday_equities-horizon-scan`` kind. Labels
    count RTH minutes on the 1-minute tape, so Friday 15:59 + 1 is Monday
    9:30. Horizon knobs and the feature list arrive on ``spec``. Train
    selects features; val scores them. The lockbox is unused.
    ``spec.scan.estimator``, when set, replaces the equal-weight top-k
    combo with that model's predictions.

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
        features = _feature_names_for_rows(spec, inputs["records"])
        leads = horizon_leads(
            int(horizon["lead_start"]),
            int(horizon["lead_step"]),
            int(horizon["lead_stop"]),
        )
        anchors = tuple(horizon["anchors"])
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
            ic_train, ic_val, selected = _score_ic(
                train_x, train_y, val_x, val_y, features, spec,
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
            "peak_lead": int(peak["lead"]) if peak else 0,
            "peak_ic": float(peak["ic_val"]) if peak else 0.0,
            "farthest_confident_lead": int(farthest["lead"]) if farthest else 0,
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


class NoInformationScan(Node):
    """One pooled ``ŷ`` at ``lead_start``; no-information walk per series.

    Role ``score`` — the ``intraday_equities-no-information-scan`` kind.
    One LightGBM on the pooled training label (``horizon.lead_start``)
    with the last column a symbol category. Optional short HPO is an
    inner split of **fold train** (not fold val). Each name then walks
    ``h`` against ``y(h)`` with
    :func:`~dskit.pipeline.stats.no_information_test`. ``μ(h)`` is that
    name's train mean of ``y(h)``. Sequential ``h*`` at α=0.05: GO iff
    the reject run starts at the first lead; H is the far end. Book
    collapse is deferred (``docs/adhoc/deferred_decisions.md``).

    Parameters
    ----------
    params : dict
        ``split`` (must be ``val``), ``train_end_ms``, ``val_start_ms``,
        ``val_end_ms``. Optional ``estimator`` (an import path) and
        ``estimator_params`` override the universe's ``scan`` block.
        Optional ``hpo_trials``, ``hpo_seed``, ``hpo_val_days``,
        ``hpo_embargo_days``, ``hpo_space`` run a discrete random search
        on an inner train holdout. Optional ``lead_start``, ``lead_step``
        and ``lead_stop`` override the universe's grid (ADR-0062);
        ``lead_start`` is the training label. Optional ``label_scale``
        (``"raw"`` default, or ``"vol"``), ``label_residual`` (a
        reference symbol), ``vol_window_minutes``,
        ``beta_window_minutes`` and ``vol_floor`` reshape the LABEL
        (ADR-0059) — MSPE is then in label units and comparable only
        with runs that declared the same ones.

    Examples
    --------
    Cuts only — the grid lives on the universe port::

        node = NoInformationScan("scan", {
            "split": "val",
            "train_end_ms": 10, "val_start_ms": 11, "val_end_ms": 20,
        })
        node.params["split"]  # 'val'
    """

    role = "score"
    outputs = ("records", "metrics")
    _PARAMS = (
        "split", "train_end_ms", "train_start_ms", "val_start_ms",
        "val_end_ms",
        "estimator", "estimator_params",
        "hpo_trials", "hpo_seed", "hpo_val_days", "hpo_embargo_days",
        "hpo_space", "hpo_objective",
    ) + LABEL_PARAMS + LEAD_PARAMS

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
        train_start = params.get("train_start_ms")
        if train_start is not None:
            check_int_param(
                problems, "train_start_ms", train_start, ge=0
            )
            train_end = params.get("train_end_ms")
            if (
                isinstance(train_start, int)
                and not isinstance(train_start, bool)
                and isinstance(train_end, int)
                and train_start >= train_end
            ):
                problems.append(
                    "train_start_ms must be < train_end_ms, got "
                    f"{train_start} >= {train_end}"
                )
        estimator = params.get("estimator")
        if estimator is not None and (
            not isinstance(estimator, str) or "." not in estimator
        ):
            problems.append(
                "estimator must be an import path like "
                f"'sklearn.linear_model.Ridge', got {estimator!r}"
            )
        extra = params.get("estimator_params")
        if extra is not None and not isinstance(extra, dict):
            problems.append(
                f"estimator_params must be an object, got {extra!r}"
            )
        objective = params.get("hpo_objective")
        if objective is not None and objective not in ("mspe", "ic"):
            problems.append(
                "hpo_objective must be 'mspe' or 'ic', got "
                f"{objective!r}"
            )
        trials = params.get("hpo_trials")
        if trials is not None:
            check_int_param(problems, "hpo_trials", trials, ge=0)
        if trials:
            for knob in ("hpo_seed", "hpo_val_days", "hpo_embargo_days"):
                ge = 1 if knob == "hpo_val_days" else 0
                if knob not in params:
                    problems.append(f"{knob} is required when hpo_trials > 0")
                else:
                    check_int_param(problems, knob, params.get(knob), ge=ge)
            space = params.get("hpo_space")
            if not isinstance(space, dict) or not space:
                problems.append(
                    "hpo_space must be a non-empty object of lists when "
                    f"hpo_trials > 0, got {space!r}"
                )
            else:
                for key, values in space.items():
                    if not isinstance(key, str) or not key:
                        problems.append(
                            f"hpo_space keys must be non-empty strings, got {key!r}"
                        )
                    if (
                        not isinstance(values, list) or not values
                        or any(
                            isinstance(v, bool) or not isinstance(v, (int, float))
                            for v in values
                        )
                    ):
                        problems.append(
                            f"hpo_space[{key!r}] must be a non-empty list of "
                            f"numbers, got {values!r}"
                        )
        problems.extend(_label_problems(params))
        problems.extend(_lead_problems(params))
        for knob in LABEL_PARAMS + LEAD_PARAMS:
            if knob in params and params[knob] is None:
                problems.append(
                    f"{knob} is present and null — a label or lead knob is "
                    "read only when declared, so drop the key rather than "
                    "nulling it"
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
        """Fit one pooled tree, then walk no-information per series.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            ``records`` (feature rows) and ``bars`` (RTH 1-minute closes).

        Returns
        -------
        dict
            ``records`` (one row per ``(symbol, lead)``) and ``metrics``
            (``n_series``, ``n_go``, ``go_frac``, pooled train/val MSPE
            and IC, plus ``go_<sym>``, ``h_star_<sym>``,
            ``p_value_<sym>``).
        """
        spec = inputs["spec"]
        horizon = spec["horizon"]
        features = _feature_names_for_rows(spec, inputs["records"])
        leads, train_lead = _lead_grid(self.params, horizon)
        train_end = int(self.params["train_end_ms"])
        train_start = self.params.get("train_start_ms")
        train_start = None if train_start is None else int(train_start)
        val_start = int(self.params["val_start_ms"])
        val_end = int(self.params["val_end_ms"])
        period_ms = int(spec["period_ms"])
        period_minutes = max(period_ms // 60_000, 1)
        arrays = _tapes_from_bars(inputs["bars"], spec["price_field"], val_end)
        label = _label_from_params(self.params, arrays, period_ms)
        if label.transformed:
            self.log.info("no-information scan: label %s", label.describe())
        prepared = _scan_aligned(
            inputs["bars"], inputs["records"], features,
            spec["price_field"], val_end, arrays=arrays,
        )
        codes = _symbol_codes(spec, prepared)
        prepared = _attach_symbol_codes(prepared, codes)
        categorical = (
            [prepared[0][2].shape[1] - 1] if prepared else None
        )
        # The design matrix IS the features plus the code column that
        # _attach_symbol_codes just appended; an estimator that splits
        # the row by name reads this, so it is built where the append is.
        column_names = list(features) + ["symbol_code"]
        base_scan = dict(spec.get("scan") or {})
        if self.params.get("estimator") is not None:
            # The MODEL is a property of the run, not of the cohort: two
            # documents comparing estimators must not need two universe
            # files, which would restate the cohort to say one thing.
            base_scan["estimator"] = self.params["estimator"]
        if self.params.get("estimator_params") is not None:
            base_scan["estimator_params"] = dict(self.params["estimator_params"])
        hpo_trials = int(self.params.get("hpo_trials") or 0)
        combos = ()
        if hpo_trials and base_scan.get("estimator"):
            combos = _hpo_combos(
                dict(base_scan.get("estimator_params") or {}),
                self.params["hpo_space"],
                hpo_trials,
                int(self.params["hpo_seed"]),
            )
            inner_train_end, inner_val_start, inner_val_end = _hpo_cuts(
                train_end,
                int(self.params["hpo_val_days"]),
                int(self.params["hpo_embargo_days"]),
            )
        curve = []
        n_go = 0
        metrics = {
            "n_leads": float(len(leads)),
            "n_series": float(len(prepared)),
        }
        tr_x, tr_y, va_x, va_y, _ = _scan_fold_stamped(
            prepared, train_lead, train_end, val_start, val_end,
            train_start=train_start, label=label,
        )
        metrics["n_train"] = float(tr_x.shape[0])
        metrics["n_val"] = float(va_x.shape[0])
        self.log.info(
            "no-information scan: train window %s n_train=%d",
            "ALL-PRIOR" if train_start is None
            else f"[{train_start}, {train_end}]",
            int(tr_x.shape[0]),
        )
        scan = dict(base_scan)
        model = None
        if tr_x.shape[0] >= 2:
            if combos:
                in_x, in_y, ho_x, ho_y, _ = _scan_fold_stamped(
                    prepared, train_lead, inner_train_end,
                    inner_val_start, inner_val_end,
                    train_start=train_start, label=label,
                )
                if in_x.shape[0] >= 2 and ho_x.shape[0] >= 2:
                    objective = self.params.get("hpo_objective", "mspe")
                    chosen, inner_score = _tune_estimator(
                        scan, combos, in_x, in_y, ho_x, ho_y,
                        categorical=categorical, objective=objective,
                        feature_names=column_names,
                    )
                    scan["estimator_params"] = chosen
                    metrics[f"hpo_{objective}"] = inner_score
                    self.log.info(
                        "hpo: %d combo(s) on %s, winner %s=%.6g",
                        len(combos), objective, objective, inner_score,
                    )
            model = _fit_estimator(
                tr_x, tr_y, scan, categorical=categorical,
                feature_names=column_names,
            )
            fit = _fit_split_metrics(model, tr_x, tr_y, va_x, va_y)
            metrics["train_mspe"] = fit["train_mspe"]
            metrics["val_mspe"] = fit["val_mspe"]
            metrics["train_ic"] = fit["train_ic"]
            metrics["val_ic"] = fit["val_ic"]
            metrics["train_yhat_sd"] = fit["train_yhat_sd"]
            metrics["val_yhat_sd"] = fit["val_yhat_sd"]
            self.log.info(
                "no-information scan: train_mspe=%.6g val_mspe=%.6g "
                "train_ic=%.4f val_ic=%.4f yhat_sd=%.3g n_train=%s n_val=%s",
                fit["train_mspe"],
                fit["val_mspe"],
                fit["train_ic"],
                fit["val_ic"],
                fit["train_yhat_sd"],
                int(tr_x.shape[0]),
                int(va_x.shape[0]),
            )
            label_sd = float(tr_y.std()) if tr_y.size else 0.0
            metrics["label_sd"] = label_sd
            if fit["train_yhat_sd"] <= _DEGENERATE_YHAT_REL * label_sd:
                raise ValueError(
                    "degenerate forecast: yhat is constant on train "
                    f"(sd={fit['train_yhat_sd']:.3g}, label sd={label_sd:.3g}, "
                    f"n_train={int(tr_x.shape[0])}). Every tree is a stump, so "
                    "the model predicts the mean and every horizon scores "
                    "IC=0. Check min_split_gain and reg_lambda against the "
                    "label variance before reading this fold."
                )
        skill_series = []
        for item in prepared:
            symbol = item[0]
            rows, series, skill = _walk_no_information_series(
                item, model, leads, train_end, val_start, val_end,
                period_minutes, train_start=train_start, label=label,
            )
            skill_series.extend(skill)
            curve.extend(rows)
            n_go += int(series["go"])
            metrics[f"go_{symbol}"] = series["go"]
            metrics[f"h_star_{symbol}"] = series["h_star"]
            metrics[f"p_value_{symbol}"] = series["p_value"]
            metrics[f"t_stat_{symbol}"] = series["t_stat"]
            # ADR-0063: the MSPE gap is the verdict, the Clark-West t is
            # the side column. Both are logged so the fold log carries
            # the number the walk will be judged on.
            metrics[f"r2oos_{symbol}"] = series["r2oos"]
            metrics[f"dm_t_{symbol}"] = series["dm_t"]
            self.log.info(
                "no-information %s: h*=%s p=%.4f cw_t=%.3f "
                "r2oos=%+.6f dm_t=%.3f (lead %s)",
                symbol,
                int(series["h_star"]),
                series["p_value"],
                series["t_stat"],
                series["r2oos"],
                series["dm_t"],
                int(leads[0]) if leads else 0,
            )
        if skill_series and ctx is not None:
            # ADR-0063 pools these gaps across the walk's folds;
            # carry.json is 20 kB of run-over-run STATE, so the evidence
            # goes through the artifact seam the reader knows to look in.
            # No ctx means no run dir — a scan scored in isolation has
            # nowhere to put the evidence, and the metrics still carry
            # the fold's own r2oos and DM t.
            self.write_artifact(
                ctx, SKILL_FILE,
                {"period_minutes": period_minutes, "series": skill_series},
            )
        n_series = len(prepared)
        metrics["n_go"] = float(n_go)
        metrics["go_frac"] = float(n_go / n_series) if n_series else 0.0
        self.log.info(
            "no-information scan: n_go=%s/%s go_frac=%.4f",
            n_go,
            n_series,
            metrics["go_frac"],
        )
        return {"records": curve, "metrics": metrics}


class LookbackScan(Node):
    """Vary lag depth L at a locked lead; emit lookback and survivors.

    Role ``score`` — the ``intraday_equities-lookback-scan`` kind. L
    walks ``scan.l_start`` by ``l_step`` through
    ``min(available, lookback_stop, 2H)``. Each trial keeps
    ``ret_lag_0..L-1`` plus every non-lag column. The 1-SE pick is the
    shortest L within one null SE of the peak |IC|. Survivors are
    train-only importance until cumulative ``keep_frac`` (default 0.95)
    or weight ≥ ``keep_tau`` of the max; calendar and industry columns
    always stay.

    Parameters
    ----------
    params : dict
        ``split`` (must be ``val``), ``lead`` (int >= 1),
        ``train_end_ms``, ``val_start_ms``, ``val_end_ms``.

    Examples
    --------
    Score L at a two-minute lead::

        node = LookbackScan("lscan", {
            "split": "val", "lead": 2,
            "train_end_ms": 10, "val_start_ms": 11, "val_end_ms": 20,
        })
        node.params["lead"]  # 2
    """

    role = "score"
    outputs = ("records", "metrics", "lookback", "features")
    _PARAMS = (
        "split", "lead", "train_end_ms", "val_start_ms", "val_end_ms",
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
        if params.get("split") != "val":
            problems.append(
                "split must be 'val' (the lockbox is unread), got "
                f"{params.get('split')!r}"
            )
        lead = params.get("lead")
        if not is_node_ref(lead):
            check_int_param(problems, "lead", lead, ge=1)
        for knob in ("train_end_ms", "val_start_ms", "val_end_ms"):
            value = params.get(knob)
            if not is_node_ref(value):
                check_int_param(problems, knob, value, ge=0)
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
        """Score the L grid at ``lead`` and emit the 1-SE pick.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused run frame.
        inputs : dict
            ``records``, ``bars``, and ``spec``.

        Returns
        -------
        dict
            ``records`` (one row per L), ``metrics``, ``lookback``,
            ``features``.
        """
        spec = inputs["spec"]
        scan = spec.get("scan") or {}
        names = _feature_names_for_rows(spec, inputs["records"])
        lead = int(self.params["lead"])
        train_end = int(self.params["train_end_ms"])
        val_start = int(self.params["val_start_ms"])
        val_end = int(self.params["val_end_ms"])
        prepared = _scan_aligned(
            inputs["bars"], inputs["records"], names,
            spec["price_field"], val_end,
        )
        train_x, train_y, val_x, val_y = _scan_fold(
            prepared, lead, train_end, val_start, val_end,
        )
        available = sum(1 for name in names if name.startswith("ret_lag_"))
        curve = []
        for lookback in _lookback_grid(scan, available, lead):
            wanted = _lookback_columns(names, lookback)
            n_train, n_val = train_y.size, val_y.size
            if n_train < 2 or n_val < 2 or not wanted:
                curve.append({
                    "lookback": lookback,
                    "ic_train": 0.0,
                    "ic_val": 0.0,
                    "n_train": float(n_train),
                    "n_val": float(n_val),
                    "se": _ic_se(n_val),
                    "selected": "",
                })
                continue
            ic_train, ic_val, selected = _score_ic(
                _take_cols(train_x, names, wanted),
                train_y,
                _take_cols(val_x, names, wanted),
                val_y,
                wanted,
                spec,
            )
            curve.append({
                "lookback": lookback,
                "ic_train": ic_train,
                "ic_val": ic_val,
                "n_train": float(n_train),
                "n_val": float(n_val),
                "se": _ic_se(n_val),
                "selected": ",".join(selected),
            })
        peak, picked = _lookback_verdict(curve)
        keep_frac = float(scan.get("keep_frac", 0.95))
        keep_tau = float(scan.get("keep_tau", 0.05))
        wanted = _lookback_columns(names, int(picked["lookback"]))
        if train_y.size >= 2 and wanted:
            weights = _column_weights(
                _take_cols(train_x, names, wanted), train_y, wanted, scan,
            )
            features = _keep_by_importance(
                wanted, weights, keep_frac, keep_tau,
            )
        else:
            features = list(wanted)
        metrics = {
            "lookback": int(picked["lookback"]),
            "peak_lookback": int(peak["lookback"]),
            "peak_ic": float(peak["ic_val"]),
            "rank_ic": float(picked["ic_val"]),
            "n_features": float(len(features)),
            "n_val": float(picked["n_val"]),
        }
        self.log.info(
            "lookback scan: L=%s ic=%.4f n_features=%s",
            picked["lookback"], picked["ic_val"], len(features),
        )
        return {
            "records": curve,
            "metrics": metrics,
            "lookback": int(picked["lookback"]),
            "features": features,
        }


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
        lead = params.get("lead")
        if not is_node_ref(lead):
            check_int_param(problems, "lead", lead, ge=1)
        if (
            params.get("split") not in ("train", "val")
            and not is_node_ref(params.get("split"))
        ):
            problems.append(
                "split must be 'train' or 'val' (the lockbox is unread), "
                f"got {params.get('split')!r}"
            )
        for knob in ("train_end_ms", "val_start_ms", "val_end_ms"):
            value = params.get(knob)
            if not is_node_ref(value):
                check_int_param(problems, knob, value, ge=0)
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
        declared = spec.get("features")
        features = (
            list(declared)
            if _string_list_ok(declared)
            else _feature_names_for_rows(spec, inputs["records"])
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
        industries = tuple(sorted(set((spec.get("industry") or {}).values())))
        derived = list(session_feature_names(
            spec["lookback"], spec["scales"], spec["reference"], industries,
        ))
        keep = spec.get("keep_features")
        spec["features"] = list(keep) if keep else derived
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
        records = inputs["records"]
        if records and _is_frame(records[0]):
            kept = [
                row for row in records
                if isinstance(row, dict) and row.get(field) in allowed
            ]
            n_in = sum(int(row["asof_ms"].shape[0]) for row in records)
            n_out = sum(int(row["asof_ms"].shape[0]) for row in kept)
        else:
            kept = [
                row for row in records
                if isinstance(row, dict) and row.get(field) in allowed
            ]
            n_in = len(records)
            n_out = len(kept)
        self.log.info("keep-symbols kept %d/%d row(s)", n_out, n_in)
        return {"records": kept}


NODE_KINDS = {
    "intraday_equities-bars": BarsFromStore,
    "intraday_equities-window": WindowRows,
    "intraday_equities-session-features": SessionFeatureRows,
    "intraday_equities-universe": Universe,
    "intraday_equities-keep-symbols": KeepSymbols,
    "intraday_equities-feed-parity": FeedParity,
    "intraday_equities-horizon-scan": HorizonScan,
    "intraday_equities-no-information-scan": NoInformationScan,
    "intraday_equities-lookback-scan": LookbackScan,
    "intraday_equities-lead-labels": LeadLabeledRows,
    "intraday_equities-portfolio": PortfolioSelect,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

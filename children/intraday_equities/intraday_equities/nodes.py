"""Child pipeline kinds: store reader, windows, feed parity, portfolio.

Vendor transport stays in the connector packs. This module declares the
equity vocabulary, session policy, overlap score, and the Pyomo
doorway.
"""

from __future__ import annotations

import math
from zoneinfo import ZoneInfo

from dskit.onboarding import parse_utc
from dskit.onboarding.libs.alpaca import BAR_KEY_FIELDS, BAR_STREAM
from dskit.onboarding.observations import scan_stream, stream_digest
from dskit.pipeline.libs.numpy import ReturnWindows, narrow_params
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.node import Node, register_node_kind, reject_unknown_params

__all__ = [
    "DEFAULT_MAX_GAP_MINUTES",
    "DEFAULT_PRICE_FIELD",
    "DEFAULT_SESSION_TZ",
    "BarsFromStore",
    "FeedParity",
    "NODE_KINDS",
    "PortfolioSelect",
    "WindowRows",
]

DEFAULT_TS_FIELD = "ts"
DEFAULT_SHARED_FIELDS = ("symbol",)
DEFAULT_PRICE_FIELD = "close"
DEFAULT_MAX_GAP_MINUTES = 5
DEFAULT_SESSION_TZ = "America/New_York"
DEFAULT_RTH_START_MINUTES = 9 * 60 + 30
DEFAULT_RTH_END_MINUTES = 16 * 60
DEFAULT_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


def session_name(stamp, zone=DEFAULT_SESSION_TZ):
    """Name the regular-session bucket for one bar stamp.

    Parameters
    ----------
    stamp : str
        ISO timestamp.
    zone : str
        IANA timezone used for regular hours.

    Returns
    -------
    str
        ``rth``, ``eth``, or ``closed``.
    """
    instant = parse_utc(stamp).astimezone(ZoneInfo(zone))
    minutes = instant.hour * 60 + instant.minute
    if instant.weekday() >= 5:
        return "closed"
    if DEFAULT_RTH_START_MINUTES <= minutes < DEFAULT_RTH_END_MINUTES:
        return "rth"
    return "eth"


class BarsFromStore(Node):
    """Emit the store's deduplicated bar records (role ``data``).

    The ``intraday_equities-bars`` kind. Adds the child's ``session``
    field; the scan itself is ADR-0037.

    Parameters
    ----------
    params : dict
        ``root`` and ``source`` (required strings), optional ``stream``,
        ``ts_field``, and ``shared_fields``.

    Examples
    --------
    Point at an onboarding root::

        node = BarsFromStore("bars", {"root": "./ob", "source": "alpaca-sip"})
        node.params["source"]  # 'alpaca-sip'
    """

    role = "data"
    outputs = ("records",)
    _PARAMS = ("root", "source", "stream", "ts_field", "shared_fields")
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
        for name in ("root", "source"):
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(
                    f"{name} is required and must be a non-empty string, "
                    f"got {value!r}"
                )
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
        tagged = []
        for record in records:
            row = dict(record)
            stamp = row.get(self.params.get("ts_field", DEFAULT_TS_FIELD))
            if isinstance(stamp, str) and stamp:
                row["session"] = session_name(stamp)
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
        if (
            not isinstance(tradable, (list, tuple))
            or not tradable
            or any(not isinstance(symbol, str) or not symbol for symbol in tradable)
        ):
            problems.append(
                "tradable must be a non-empty list of symbols, "
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
        return build_portfolio_model(per_t, params["tradable"])

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


NODE_KINDS = {
    "intraday_equities-bars": BarsFromStore,
    "intraday_equities-window": WindowRows,
    "intraday_equities-feed-parity": FeedParity,
    "intraday_equities-portfolio": PortfolioSelect,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

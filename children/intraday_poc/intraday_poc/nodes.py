"""``nodes`` — the child's pipeline seam: registered Node kinds.

Four kinds carry the whole PoC:

- ``intraday_poc-bars`` (role ``data``) — a thin wrapper over the
  toolkit's observations read seam (``dskit.onboarding.observations``,
  ADR-0037), which owns the codec resolution, the bitemporal dedup
  (latest ``acquired_at`` wins per ``(symbol, ts)``), and the
  single-copy memory discipline (the 14.3 GB lesson of the first
  2M-bar run). The wrapper only declares the domain: the key fields,
  the repeated ``symbol``, and the bar stamp flattening to ``asof_ms``.
  Content-derived fingerprint, one scan memoized per instance (the
  resolve/execute straddle rule).
- ``intraday_poc-window`` (role ``transform``) — per-symbol sliding
  windows of one-bar log returns: ``ret_lag_0`` (most recent) …
  ``ret_lag_{L-1}``, labelled with the NEXT bar's return ``y_next``.
  Windows never span a gap larger than ``max_gap_minutes`` (session
  boundaries, halted or untraded minutes) — such chains break, they are
  never bridged. The MECHANISM is the toolkit's
  (:class:`~dskit.pipeline.libs.numpy.ReturnWindows`, ADR-0040): this
  class supplies only the domain — the vocabulary its documents speak,
  and the one rule the pack cannot know, that a bar with no usable
  price is not a bar. It writes no chain arithmetic of its own, and
  inherits the causality screen it never had.
- ``intraday_poc-forecast`` (role ``score``) — applies a torch node's
  ``signal`` to labelled rows, one belief per row; a row the signal
  refuses (missing/non-finite feature) is skipped and counted, never
  fabricated.
- ``intraday_poc-select-one`` (role ``score``) — the PoC's one
  constraint as a pyomo program on the toolkit's :class:`PyomoSolve`
  doorway: at each timestamp pick EXACTLY ONE of the forecast symbols,
  maximizing predicted next-bar return. Role is ``score``, not
  ``capital``: this node scores the selection rule for the backtest — a
  child that SIZES CASH with it keeps the doorway's default ``capital``
  role and wires a ``stat_test`` survivors gate, per the pack's role
  doctrine.

Importing this module IS the registration (``--adapter intraday_poc``).
Import cost: stdlib + dskit — torch and pyomo stay inside ``run()``-path
methods, so documents naming these kinds plan on machines without them.
"""

from __future__ import annotations

import math

from dskit.onboarding.observations import scan_stream, stream_digest
from dskit.pipeline.libs.numpy import ReturnWindows, narrow_params
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.node import Node, register_node_kind, reject_unknown_params

from .connectors import BAR_KEY_FIELDS, BAR_STREAM

__all__ = [
    "DEFAULT_MAX_GAP_MINUTES",
    "DEFAULT_PRICE_FIELD",
    "BarsFromStore",
    "ForecastRows",
    "NODE_KINDS",
    "SelectOne",
    "WindowRows",
    "build_select_model",
]

#: Each default has ONE name, read by the knob gate AND by the accessor
#: the run resolves through — the `libs/torch.py` ``DEFAULT_EPOCHS``
#: idiom. Written twice, validation approves a value the run never uses
#: and nothing catches it. The serving loop holds no copy at all: it
#: CONSTRUCTS the document's window node and asks it (``live.py``), so
#: rebinding either name here moves training and serving together.
DEFAULT_PRICE_FIELD = "close"
DEFAULT_MAX_GAP_MINUTES = 5


class BarsFromStore(Node):
    """Emit the store's deduplicated bar records (role ``data``).

    The ``intraday_poc-bars`` kind.

    Params: ``root`` (REQUIRED) — the onboarding root; ``source``
    (REQUIRED) — the registered source name; ``stream`` — default
    ``BAR_STREAM`` (``"bars"``), the connector's own stream name. All
    literal, per the data-role rule.

    Records are the normalized rows' ``data`` payloads flattened, plus
    ``asof_ms`` (the bar timestamp as epoch ms — what split filters cut
    on). Ordered by ``(asof_ms, symbol, ts)`` — the seam's
    key-determined order (identical to ``(asof_ms, symbol)`` except
    for same-instant duplicate ``ts`` spellings, per ADR-0037).
    """

    role = "data"
    outputs = ("records",)

    _PARAMS = ("root", "source", "stream")

    #: Instance scan cache — set per instance on first read, so resolve
    #: (fingerprint) and execute (run) see one snapshot even while the
    #: live puller appends acquisitions underneath.
    _snap = None

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown knob, missing ``root``/``source``,
            or unusable ``stream``.
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
        return problems

    def _scan(self):
        if self._snap is not None:
            return self._snap
        # The generic seam (ADR-0037) owns the codec resolution, the
        # bitemporal dedup, and the single-copy memory discipline; this
        # wrapper only declares the domain shape.
        self._snap = scan_stream(
            self.params["root"],
            self.params["source"],
            self.params.get("stream", BAR_STREAM),
            key_fields=BAR_KEY_FIELDS,
            ts_field="ts",
            shared_fields=("symbol",),
        )
        return self._snap

    def fingerprint(self):
        """Answer this source's content-derived data identity.

        It moves whenever any bar a run would consume changes —
        count-only fingerprints are content-blind. ``stream_digest`` is
        byte-parity with the frozen whole-dump recipe; identity holds
        for any store with one ``ts`` spelling per instant (ADR-0037
        review amendments — same-instant spelling duplicates now order
        by the ``ts`` string, not scan order).

        Returns
        -------
        dict
            ``{"kind", "rows", "sha256"}`` — JSON-small, hashed into the
            run identity.
        """
        records = self._scan()
        return {"kind": "intraday_poc-bars", "rows": len(records),
                "sha256": stream_digest(records)}

    def run(self, ctx, inputs):
        """Emit the store's bar records.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused — a source reads only its params.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``{"records": [...]}`` — the memoized snapshot itself.
        """
        # The snapshot itself is emitted — no dict-per-row copy. Safe:
        # the driver runs the pinned instance ONCE per run (fingerprint
        # at resolve, run at execute, never again), and record streams
        # are read-only downstream by the single-pass doctrine.
        records = self._scan()
        self.log.info("emitting %d bar record(s)", len(records))
        return {"records": records}


class WindowRows(ReturnWindows):
    """Per-symbol lagged-return windows with a next-bar label.

    Role ``transform`` — the ``intraday_poc-window`` kind.

    Inputs: ``records`` — bar rows carrying ``symbol``, ``asof_ms`` and
    the price field. Params: ``lookback`` (REQUIRED, int >= 2) — window
    width in returns; ``price_field`` — default ``DEFAULT_PRICE_FIELD``
    (``"close"``); ``max_gap_minutes`` — default
    ``DEFAULT_MAX_GAP_MINUTES`` (5): consecutive bars further apart than
    this break the return chain (never bridged, never interpolated).
    The toolkit's ``causality_check`` and ``cuts`` ride too.

    Output rows: ``{symbol, asof_ms, ret_lag_0 .. ret_lag_{L-1}, y_next}``
    where ``ret_lag_0`` is the return ENDING at ``asof_ms`` and ``y_next``
    the return of the following bar — the label a next-bar model trains
    on. Sparse bars (missing/non-positive price) are dropped, counted
    (``n_dropped``, and the pack's log line) and never crashed on.

    **Everything the port changed about what this node computes**, all
    of it pinned in ``tests/test_nodes.py``:

    * two bars of one symbol on the SAME instant (two ``ts`` spellings
      flattening onto one ``asof_ms``) keep the STREAM's order, which is
      the store's own ``ts`` order — the pack breaks the tie by stream
      position, where the pre-ADR-0040 code sorted ``(asof_ms, price)``
      tuples and so ordered them by PRICE;
    * an EMPTY ``symbol`` is no series at all, where it used to be a
      series of its own — the pack keys on an identity the toolkit can
      hold, and a stream of ONLY such bars refuses by name;
    * a FLOAT ``asof_ms`` and an attribute-bearing (non-dict) record now
      LIFT, where the pre-port predicates dropped both.

    The last two are degenerate-input shapes no store this child reads
    produces; they are stated because a change to what the child
    computes is declared, not discovered.

    Everything above the domain is the pack's (ADR-0040): the grouping,
    the ordering, the gap-split, the log return, the lags, the forward
    label, the causality screen and the serving call all come from
    :class:`~dskit.pipeline.libs.numpy.ReturnWindows`. What is left here
    is what only this project knows — the SPELLINGS its documents use,
    and the rule that a bar with no usable price is not a bar. Each
    accessor it answers narrows that knob away, per the pack's rule.

    Parameters
    ----------
    params : dict
        ``lookback``, ``price_field``, ``max_gap_minutes``,
        ``causality_check``, ``cuts``.

    Examples
    --------
    Thirty one-minute log returns that never bridge a session break::

        node = WindowRows("window", {"lookback": 30, "max_gap_minutes": 5})
        out = node.run(ctx, {"records": bars})
        # -> {"records": [{"symbol": ..., "ret_lag_0": ..., "y_next": ...}]}
    """

    role = "transform"
    outputs = ("records",)

    #: A next-bar model needs at least two returns to have a window.
    min_lookback = 2

    _PARAMS = narrow_params(
        ReturnWindows._PARAMS,
        "carry_fields",
        "drop_incomplete",
        "fields",
        "group_field",
        "label_lead",
        "label_name",
        "lag_prefix",
        "max_gap",
        "order_field",
        "require_fields",
        "return_kind",
    ) + ("max_gap_minutes", "price_field")

    # -- the vocabulary this project's documents speak ---------------------

    def group_field(self):
        """Name the bar field the series is keyed on (str)."""
        return "symbol"

    def order_field(self):
        """Name the bar field the series is ordered by (str)."""
        return "asof_ms"

    def price_field(self):
        """Name the bar field windows are priced on (str)."""
        return self.params.get("price_field", DEFAULT_PRICE_FIELD)

    def fields(self):
        """Lift the declared price field, and nothing else."""
        return (self.price_field(),)

    def max_gap_minutes(self):
        """Give the gap bound in MINUTES — this project's unit (float)."""
        return float(self.params.get("max_gap_minutes",
                                     DEFAULT_MAX_GAP_MINUTES))

    def max_gap(self):
        """Give the same bound in the order field's units (epoch ms)."""
        return self.max_gap_minutes() * 60_000

    def carry_fields(self):
        """Carry the two identity fields downstream reads."""
        return (self.group_field(), self.order_field())

    def require_fields(self):
        """Require no id beyond the two carried: bars have no contract."""
        return ()

    def drop_incomplete(self):
        """Emit only rows whose whole window AND label are present."""
        return True

    def return_kind(self):
        """Take LOG returns — what the models here are trained on."""
        return "log"

    def label_lead(self):
        """Label with the NEXT bar's return: one step forward."""
        return 1

    def lag_prefix(self):
        """Name the lag columns ``ret_lag_0`` … (str)."""
        return "ret_lag_"

    def label_name(self):
        """Name the label column ``y_next`` (str)."""
        return "y_next"

    # -- the one rule the pack cannot know ---------------------------------

    def keep_mask(self, arrays):
        """Say which bars are bars: those with a usable price.

        A minute the vendor published no price for is not a data point
        to interpolate across — it is absent, and the SURVIVORS chain
        (the gap bound then judges the wider step they leave). Kept
        vectorized: a per-record predicate over a 2M-bar backfill is the
        cost this pack exists to avoid.

        Parameters
        ----------
        arrays : dict of str -> numpy.ndarray
            One symbol's lifted arrays.

        Returns
        -------
        numpy.ndarray
            A bool array: finite and strictly positive prices.
        """
        import numpy as np

        price = arrays[self.price_field()]
        return np.isfinite(price) & (price > 0.0)

    def sort_rows(self, rows):
        """Emit in ``(asof_ms, symbol)`` order — the backtest's order.

        Parameters
        ----------
        rows : list of dict
            The built window rows, in the input stream's order.

        Returns
        -------
        list of dict
            The same rows, time-major.
        """
        return sorted(rows, key=lambda row: (row[self.order_field()],
                                             row[self.group_field()]))

    def emit(self, rows, metrics):
        """Package the rows as this kind's ``records`` output.

        Parameters
        ----------
        rows : list of dict
            The ordered window rows.
        metrics : dict
            The pack's numeric summary; this kind's output contract has
            no place for it, and the log line already carries the counts.

        Returns
        -------
        dict
            ``{"records": rows}``.
        """
        return {"records": rows}

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        The pack's checks, plus this project's two knobs — each read
        through the SAME module constant the run resolves, so rebinding
        one moves the gate and the run together.

        Parameters
        ----------
        params : dict
            The node's declared params.

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
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) \
                or not math.isfinite(gap) or gap <= 0:
            problems.append(
                f"max_gap_minutes must be a positive number, got {gap!r}"
            )
        return problems


class ForecastRows(Node):
    """One belief per labelled row from a torch ``signal``.

    Role ``score`` — the ``intraday_poc-forecast`` kind.

    Inputs: ``signal`` — a torch node's signal output (its ``predict``
    answers a float or ``None``); ``records`` — the rows to predict on,
    each carrying ``symbol``, ``asof_ms`` and the signal's features.
    No params: the signal already owns its feature list.

    Output ``forecasts``: ``[{symbol, asof_ms, pred}]``; rows the signal
    declines (``None`` — no coverage) are skipped and counted.

    Params: ``split`` (REQUIRED, one of train/val/cal/test) — the
    planner's score-role rule: which split the wired rows come from must
    be READABLE from the document, and must agree with the upstream
    filter.
    """

    role = "score"
    outputs = ("forecasts",)

    _PARAMS = ("split",)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown knob, and one when ``split`` does
            not name which split these rows come from.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if params.get("split") not in ("train", "val", "cal", "test"):
            problems.append(
                f"split must declare which split this node reads "
                f"(train/val/cal/test), got {params.get('split')!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Problems with ``inputs``, empty when none.

        Parameters
        ----------
        inputs : dict
            ``records`` (a list) and ``signal`` (a torch signal).

        Returns
        -------
        list of str
            One problem per port that cannot be scored through.
        """
        problems = []
        if not isinstance(inputs.get("records"), list):
            problems.append(f"records must be a list of rows, got "
                            f"{inputs.get('records')!r}")
        if not hasattr(inputs.get("signal"), "predict"):
            problems.append(
                f"signal must be a torch signal (has .predict), got "
                f"{type(inputs.get('signal')).__name__}"
            )
        return problems

    def run(self, ctx, inputs):
        """Score every row the signal has coverage for.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused.
        inputs : dict
            ``signal`` and the ``records`` to predict on.

        Returns
        -------
        dict
            ``{"forecasts": [{"symbol", "asof_ms", "pred"}]}``; a row the
            signal declines is skipped and counted, never fabricated.
        """
        signal = inputs["signal"]
        forecasts = []
        skipped = 0
        for row in inputs["records"]:
            pred = signal.predict(row)
            if pred is None:
                skipped += 1
                continue
            forecasts.append({"symbol": row.get("symbol"),
                              "asof_ms": row.get("asof_ms"),
                              "pred": float(pred)})
        self.log.info("forecast %d row(s); %d without coverage skipped",
                      len(forecasts), skipped)
        return {"forecasts": forecasts}


def build_select_model(per_t: dict):
    """Build the PoC's one constraint as a pyomo ConcreteModel.

    For each timestamp pick EXACTLY ONE of its candidate symbols,
    maximizing the summed predicted return. Shared by
    :class:`SelectOne` (the backtest) and ``live.py`` (the forward loop,
    one timestamp at a time) so both sides decide with the SAME program.
    Imports pyomo — call only from run-path code.

    Parameters
    ----------
    per_t : dict
        ``asof_ms -> {symbol: predicted return}``; must be non-empty.

    Returns
    -------
    pyomo.environ.ConcreteModel
        The model, with binary ``x[t, s]`` and one equality per
        timestamp.
    """
    import pyomo.environ as pyo

    model = pyo.ConcreteModel()
    index = [(t, s) for t in sorted(per_t) for s in sorted(per_t[t])]
    model.x = pyo.Var(index, domain=pyo.Binary)
    model.one_per_t = pyo.Constraint(
        sorted(per_t),
        rule=lambda m, t: sum(m.x[t, s] for s in sorted(per_t[t])) == 1,
    )
    model.objective = pyo.Objective(
        expr=sum(per_t[t][s] * model.x[t, s] for t, s in index),
        sense=pyo.maximize,
    )
    return model


class SelectOne(PyomoSolve):
    """Pick exactly one symbol per timestamp by predicted return.

    The ``intraday_poc-select-one`` kind, on the toolkit's PyomoSolve
    doorway.

    Inputs: ``forecasts`` — ``[{symbol, asof_ms, pred}]`` (typically a
    ``concat`` of per-symbol forecast nodes); ``labeled`` — the window
    rows carrying ``y_next``, joined by ``(symbol, asof_ms)`` to realize
    each pick after the fact. Params: the doorway's own ``solver`` /
    ``solver_options``, plus ``split`` (REQUIRED, train/val/cal/test) —
    the planner's score-role declaration, matching the upstream filters.

    Outputs: ``picks`` — ``[{asof_ms, symbol, pred, realized}]``
    (``realized`` is ``None`` when no label row matches); ``metrics`` —
    ``n_picks`` / ``total_pred`` / ``n_realized`` / ``total_realized``
    (the walk-forward objective).

    Role ``score``, deliberately not ``capital`` — see the module
    docstring: this node scores the rule; a cash-sizing child keeps the
    doorway's default role and wires the stat_test gate.
    """

    role = "score"
    outputs = ("picks", "metrics")

    _PARAMS = PyomoSolve._PARAMS + ("split",)

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The doorway's problems, plus one when ``split`` does not
            name which split these forecasts come from.
        """
        problems = super().validate_params(params)
        if params.get("split") not in ("train", "val", "cal", "test"):
            problems.append(
                f"split must declare which split this node reads "
                f"(train/val/cal/test), got {params.get('split')!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Problems with ``inputs``, empty when none.

        Parameters
        ----------
        inputs : dict
            ``forecasts`` and ``labeled``, both lists.

        Returns
        -------
        list of str
            One problem per port that is not a list.
        """
        problems = []
        if not isinstance(inputs.get("forecasts"), list):
            problems.append(f"forecasts must be a list, got "
                            f"{inputs.get('forecasts')!r}")
        if not isinstance(inputs.get("labeled"), list):
            problems.append(f"labeled must be a list of window rows, got "
                            f"{inputs.get('labeled')!r}")
        return problems

    def _per_t(self, inputs) -> dict:
        per_t = {}
        for row in inputs["forecasts"]:
            if not isinstance(row, dict):
                continue
            symbol, asof, pred = row.get("symbol"), row.get("asof_ms"), \
                row.get("pred")
            if (not isinstance(symbol, str) or isinstance(asof, bool)
                    or not isinstance(asof, int) or isinstance(pred, bool)
                    or not isinstance(pred, (int, float))
                    or not math.isfinite(pred)):
                continue  # sparse forecast — cannot enter the program
            per_t.setdefault(asof, {})[symbol] = float(pred)
        return per_t

    def build_model(self, inputs, params):
        """Build the selection program over the grouped forecasts.

        Parameters
        ----------
        inputs : dict
            The node's inputs; already grouped by ``run``.
        params : dict
            This node's params; unused — the program has no knobs.

        Returns
        -------
        pyomo.environ.ConcreteModel
            The model :func:`build_select_model` returns, carrying the
            grouping for :meth:`extract`.
        """
        # run() grouped the forecasts once (streams are single-pass);
        # underscore-prefixed model attrs are plain bookkeeping for
        # extract(), invisible to pyomo — the BudgetedSelect precedent.
        model = build_select_model(self._per_t_cache)
        model._per_t = self._per_t_cache
        return model

    def extract(self, model, results):
        """Read the picks back out of the solved model.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            The solved model.
        results : object
            The solver's result object; unused — the values are on the
            model.

        Returns
        -------
        dict
            ``{"picks": [...], "metrics": {...}}``; ``realized`` is
            ``None`` where no label row matched.
        """
        import pyomo.environ as pyo

        per_t = model._per_t
        labels = {}
        for row in self._labeled:
            if isinstance(row, dict):
                labels[(row.get("symbol"), row.get("asof_ms"))] = \
                    row.get("y_next")
        picks = []
        total_pred = 0.0
        total_realized = 0.0
        n_realized = 0
        for t in sorted(per_t):
            for s in sorted(per_t[t]):
                if pyo.value(model.x[t, s]) < 0.5:
                    continue
                realized = labels.get((s, t))
                if isinstance(realized, bool) or \
                        not isinstance(realized, (int, float)):
                    realized = None
                else:
                    total_realized += float(realized)
                    n_realized += 1
                total_pred += per_t[t][s]
                picks.append({"asof_ms": t, "symbol": s,
                              "pred": per_t[t][s], "realized": realized})
        metrics = {"n_picks": len(picks), "total_pred": total_pred,
                   "n_realized": n_realized, "total_realized": total_realized}
        self.log.info(
            "picked %d timestamp(s); total predicted %.6f, realized %.6f "
            "over %d labelled pick(s)",
            len(picks), total_pred, total_realized, n_realized,
        )
        return {"picks": picks, "metrics": metrics}

    def run(self, ctx, inputs):
        """Group the forecasts, then solve — or select nothing, loudly.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame, handed on to the doorway.
        inputs : dict
            ``forecasts`` and the ``labeled`` rows to realize against.

        Returns
        -------
        dict
            ``{"picks": [...], "metrics": {...}}``. An empty forecast
            set selects nothing WITHOUT invoking the solver — the
            doorway's empty-gate doctrine.
        """
        # Group once — the forecast port is a record stream and streams
        # are single-pass. An empty forecast set selects nothing WITHOUT
        # invoking the solver — the doorway's empty-gate doctrine.
        per_t = self._per_t(inputs)
        if not per_t:
            self.log.info("no usable forecasts — nothing to select")
            return {"picks": [], "metrics": {
                "n_picks": 0, "total_pred": 0.0,
                "n_realized": 0, "total_realized": 0.0,
            }}
        self._per_t_cache = per_t
        self._labeled = list(inputs["labeled"])  # extract() joins post-solve
        return super().run(ctx, inputs)


#: kind name -> class: what the registry, the conformance suite, and a
#: document's ``uses`` all key off.
NODE_KINDS = {
    "intraday_poc-bars": BarsFromStore,
    "intraday_poc-window": WindowRows,
    "intraday_poc-forecast": ForecastRows,
    "intraday_poc-select-one": SelectOne,
}

# Import = registration (``owned`` deliberately NOT set).
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

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
  never bridged.
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
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.node import Node, register_node_kind

__all__ = [
    "BarsFromStore",
    "ForecastRows",
    "NODE_KINDS",
    "SelectOne",
    "WindowRows",
    "build_select_model",
]


def _reject_unknown(problems, params, allowed) -> None:
    """Default-deny on this class's own knobs — the child keeps its own
    copy of the toolkit idiom."""
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        problems.append(
            f"unknown param(s) {unknown} — this kind allows {sorted(allowed)}"
        )


class BarsFromStore(Node):
    """Emit the store's deduplicated bar records (role ``data``) — the
    ``intraday_poc-bars`` kind.

    Params: ``root`` (REQUIRED) — the onboarding root; ``source``
    (REQUIRED) — the registered source name; ``stream`` — default
    ``"bars"``. All literal, per the data-role rule.

    Records are the normalized rows' ``data`` payloads flattened, plus
    ``asof_ms`` (the bar timestamp as epoch ms — what split filters cut
    on). Ordered by ``(asof_ms, symbol)``.
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
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        for name in ("root", "source"):
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(
                    f"{name} is required and must be a non-empty string, "
                    f"got {value!r}"
                )
        stream = params.get("stream", "bars")
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
            self.params.get("stream", "bars"),
            key_fields=("symbol", "ts"),
            ts_field="ts",
            shared_fields=("symbol",),
        )
        return self._snap

    def fingerprint(self):
        """Content-derived: moves whenever any bar a run would consume
        changes — count-only fingerprints are content-blind.
        ``stream_digest`` is byte-parity with the frozen whole-dump
        recipe; identity holds for any store with one ``ts`` spelling
        per instant (ADR-0037 review amendments — same-instant spelling
        duplicates now order by the ``ts`` string, not scan order)."""
        records = self._scan()
        return {"kind": "intraday_poc-bars", "rows": len(records),
                "sha256": stream_digest(records)}

    def run(self, ctx, inputs):
        # The snapshot itself is emitted — no dict-per-row copy. Safe:
        # the driver runs the pinned instance ONCE per run (fingerprint
        # at resolve, run at execute, never again), and record streams
        # are read-only downstream by the single-pass doctrine.
        records = self._scan()
        self.log.info("emitting %d bar record(s)", len(records))
        return {"records": records}


class WindowRows(Node):
    """Per-symbol lagged-return windows with a next-bar label (role
    ``transform``) — the ``intraday_poc-window`` kind.

    Inputs: ``records`` — bar rows carrying ``symbol``, ``asof_ms`` and
    the price field. Params: ``lookback`` (REQUIRED, int >= 2) — window
    width in returns; ``price_field`` — default ``"close"``;
    ``max_gap_minutes`` — default 5: consecutive bars further apart than
    this break the return chain (never bridged, never interpolated).

    Output rows: ``{symbol, asof_ms, ret_lag_0 .. ret_lag_{L-1}, y_next}``
    where ``ret_lag_0`` is the return ENDING at ``asof_ms`` and ``y_next``
    the return of the following bar — the label a next-bar model trains
    on. Sparse rows (missing/non-positive price) are dropped and counted,
    never crashed on.
    """

    role = "transform"
    outputs = ("records",)

    _PARAMS = ("lookback", "price_field", "max_gap_minutes")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        lookback = params.get("lookback")
        if "lookback" not in params:
            problems.append("lookback is required — the window width must "
                            "be stated, there is no default")
        elif isinstance(lookback, bool) or not isinstance(lookback, int) \
                or lookback < 2:
            problems.append(f"lookback must be an int >= 2, got {lookback!r}")
        price_field = params.get("price_field", "close")
        if not isinstance(price_field, str) or not price_field:
            problems.append(
                f"price_field must be a non-empty string, got {price_field!r}"
            )
        gap = params.get("max_gap_minutes", 5)
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) \
                or not math.isfinite(gap) or gap <= 0:
            problems.append(
                f"max_gap_minutes must be a positive number, got {gap!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        if not isinstance(inputs.get("records"), list):
            return [f"records must be a list of bar rows, got "
                    f"{inputs.get('records')!r}"]
        return []

    def run(self, ctx, inputs):
        lookback = self.params["lookback"]
        price_field = self.params.get("price_field", "close")
        gap_ms = float(self.params.get("max_gap_minutes", 5)) * 60_000

        by_symbol = {}
        sparse = 0
        for row in inputs["records"]:
            symbol = row.get("symbol") if isinstance(row, dict) else None
            price = row.get(price_field) if isinstance(row, dict) else None
            asof = row.get("asof_ms") if isinstance(row, dict) else None
            if (not isinstance(symbol, str) or isinstance(price, bool)
                    or not isinstance(price, (int, float)) or price <= 0
                    or isinstance(asof, bool) or not isinstance(asof, int)):
                sparse += 1
                continue
            by_symbol.setdefault(symbol, []).append((asof, float(price)))

        out = []
        for symbol in sorted(by_symbol):
            bars = sorted(by_symbol[symbol])
            # Contiguous chains: a gap larger than gap_ms starts a new one.
            chains = []
            chain = []  # [(asof_ms, log_return)] — return ENDING at asof_ms
            for i in range(1, len(bars)):
                if bars[i][0] - bars[i - 1][0] > gap_ms:
                    if chain:
                        chains.append(chain)
                    chain = []
                    continue
                chain.append((bars[i][0], math.log(bars[i][1] / bars[i - 1][1])))
            if chain:
                chains.append(chain)
            for chain in chains:
                # Window of `lookback` returns ending at index i; the label
                # is the return at i+1 — a chain's last return has no label
                # and yields no row.
                for i in range(lookback - 1, len(chain) - 1):
                    row = {"symbol": symbol, "asof_ms": chain[i][0],
                           "y_next": chain[i + 1][1]}
                    for lag in range(lookback):
                        row[f"ret_lag_{lag}"] = chain[i - lag][1]
                    out.append(row)
        out.sort(key=lambda r: (r["asof_ms"], r["symbol"]))
        self.log.info(
            "windowed %d row(s) from %d bar(s) across %d symbol(s); "
            "%d sparse bar(s) dropped",
            len(out), len(inputs["records"]), len(by_symbol), sparse,
        )
        return {"records": out}


class ForecastRows(Node):
    """One belief per labelled row from a torch ``signal`` (role
    ``score``) — the ``intraday_poc-forecast`` kind.

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
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        if params.get("split") not in ("train", "val", "cal", "test"):
            problems.append(
                f"split must declare which split this node reads "
                f"(train/val/cal/test), got {params.get('split')!r}"
            )
        return problems

    def validate_inputs(self, inputs):
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
    """The PoC's one constraint as a pyomo ConcreteModel: for each
    timestamp pick EXACTLY ONE of its candidate symbols, maximizing the
    summed predicted return. ``per_t`` maps ``asof_ms -> {symbol: pred}``.

    Shared by :class:`SelectOne` (the backtest) and ``live.py`` (the
    forward loop, one timestamp at a time) so both sides decide with the
    SAME program. Imports pyomo — call only from run-path code.
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
    """Pick exactly one symbol per timestamp, maximizing predicted
    next-bar return — the ``intraday_poc-select-one`` kind, on the
    toolkit's PyomoSolve doorway.

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
        problems = super().validate_params(params)
        if params.get("split") not in ("train", "val", "cal", "test"):
            problems.append(
                f"split must declare which split this node reads "
                f"(train/val/cal/test), got {params.get('split')!r}"
            )
        return problems

    def validate_inputs(self, inputs):
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
        # run() grouped the forecasts once (streams are single-pass);
        # underscore-prefixed model attrs are plain bookkeeping for
        # extract(), invisible to pyomo — the BudgetedSelect precedent.
        model = build_select_model(self._per_t_cache)
        model._per_t = self._per_t_cache
        return model

    def extract(self, model, results):
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

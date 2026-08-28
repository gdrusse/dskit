"""``live`` — the forward loop: predict, select, place the paper order.

Run-path only (torch, pyomo, alpaca-py) — never imported by
``__init__``. One iteration per completed minute bar:

1. gate on the exchange clock (``get_clock()`` — never a hardcoded
   session);
2. pull the latest 1-minute bars for the configured symbols over REST
   (IEX feed — the free tier's real-time-eligible feed; bars publish
   ~2–3 s after the minute closes, and a minute in which a symbol did
   not print on IEX simply has no bar — the window helper refuses,
   never bridges);
3. restore each symbol's model from the run's artifact — sidecar
   verified (state_hash, S2-A) and the module class refused by name
   against the one the RUN declared, the pack's own load discipline;
4. decide with the SAME pyomo program the backtest scores
   (:func:`intraday_poc.nodes.build_select_model`, one timestamp);
5. flip the paper position when the pick changes: flatten the loser,
   market-buy the winner (TIF ``day``), through the paper endpoint.

**This loop re-declares nothing the run already declared.** The
modelling knobs — the price field, the gap discipline, the module class
— are READ from ``<run-dir>/config.json``, the whole training document
the driver writes, through the ENGINE's own
:func:`~dskit.pipeline.document.load_document` rather than a parse of
this loop's: the grammar is tier-1 truth, and re-deriving it here would
accept runs the engine refuses and then blame the document for it. The
vendor knobs AND the symbol universe come from the acquisition source
config the puller registered (``--source-config``), resolved through the
connector's own knob gate for the same reason. Only operational flags
(quantity, dry-run, log dir, artifact overrides, history window) live on
the CLI, and there is deliberately no third config file: it would
duplicate both of those.

Two vendor knobs come from neither file, for different reasons:

* the FEED — the store is built from SIP history and real-time SIP is
  not sold on the free tier, so the forward fetch must ask for IEX
  (:data:`LIVE_FEED`). That is the child's one declared train/serve
  vendor DIVERGENCE, carried in README's "What to know before trusting
  the numbers".
* the BAR INTERVAL — not a config knob on either side yet (a
  ``timeframe`` knob on the connector spec is an open TODO). It is ONE
  constant, ``connectors.BAR_INTERVAL``, and this loop asks for it
  through the connector's own ``bar_timeframe()``, so the served bars
  cannot drift from the ones the store was built from.

Every iteration appends one JSON line to ``decisions.jsonl`` in
``--log-dir`` — predictions, pick, action taken — so the forward run
leaves evidence the way a pipeline run does.

Usage::

    python -m intraday_poc.live --run-dir <run dir of run-train.json> \
        --source-config configs/source-backfill.json \
        --qty 1 [--once] [--dry-run] \
        [--artifact AAPL=artifacts/qhat_aapl]

Credentials are half read, half shared. The source config NAMES the two
env vars (``key_env``/``secret_env``), and what COUNTS as a credential
is the connector's own
:func:`~intraday_poc.connectors.resolve_credentials` — one rule for the
whole child, so a var that is missing or set to ``""`` is refused by
name on either side rather than authenticated blank. Where the VALUES
come from is the one thing the two paths do not share: the puller reads
the process environment only, while this loop materializes ``.env``
beside the CWD under it through the toolkit's ``env.py`` (process
environment winning) and re-derives no dotenv rule of its own.
Exporting the pair serves both.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time

from dskit.onboarding import AssetError
from dskit.pipeline.base import (
    EnvConfig,
    import_library_class,
    import_ref,
    is_class_ref,
)
from dskit.pipeline.document import load_document
from dskit.pipeline.env import load_env

from .connectors import (
    AlpacaBarsConnector,
    bar_timeframe,
    resolve_credentials,
)
from .nodes import (
    DEFAULT_MAX_GAP_MINUTES,
    DEFAULT_PRICE_FIELD,
    NODE_KINDS,
    WindowRows,
    build_select_model,
)

__all__ = [
    "LIVE_FEED",
    "artifact_dirs",
    "bar_series",
    "credentials",
    "declared_module",
    "fetch_bars",
    "latest_feature_row",
    "load_run_document",
    "main",
    "parse_artifact_overrides",
    "predict",
    "restore_model",
    "solve_pick",
    "source_knobs",
    "window_knobs",
]

#: The forward fetch's feed. Deliberately NOT read from the source
#: config: that config's ``feed`` is ``sip`` (free-tier history), and
#: real-time SIP is not sold on the free tier, so the serving fetch
#: cannot use the store's feed. This is the child's one declared
#: train/serve vendor difference — see the module docstring.
LIVE_FEED = "iex"

#: The kind name the training document uses for its window node, taken
#: from the registry the child registers rather than restated here.
_WINDOW_KIND = next(name for name, cls in NODE_KINDS.items()
                    if cls is WindowRows)


def credentials(knobs):
    """Materialize the key pair the source config's knobs NAME.

    Nothing here is restated. The NAMES are the connector's
    ``key_env``/``secret_env`` knobs. The VALUES come from dskit's own
    ``env.py`` loader — ``.env`` beside the CWD merged under the process
    environment, ``export `` prefixes and matched quotes handled the one
    way the toolkit documents. And what COUNTS as a credential is the
    connector's :func:`~intraday_poc.connectors.resolve_credentials`,
    called with those values: an env var set to ``""`` passes every
    presence check ``env.py`` makes, so without the shared rule the loop
    would authenticate blank while the puller refused the same pair.

    The one thing the two paths do not share is WHERE they look: the
    puller reads the process environment only, this loop reads ``.env``
    as well. Exporting the pair serves both.

    Parameters
    ----------
    knobs : dict
        Resolved source knobs, as :func:`source_knobs` returns them.

    Returns
    -------
    tuple of (str, str)
        The Alpaca key id and secret, in that order.

    Raises
    ------
    SystemExit
        When a NAMED variable is missing from both the process
        environment and ``.env``, when either resolves EMPTY (the
        connector's refusal, verbatim), or when the config names
        something that is not a valid environment variable name.
    """
    names = (knobs["key_env"], knobs["secret_env"])
    try:
        secrets = load_env(EnvConfig(require=names))
    except ValueError as exc:
        raise SystemExit(f"{exc} — fill in .env (see .env.example)") from exc
    try:
        return resolve_credentials(knobs, secrets.get)
    except AssetError as exc:
        raise SystemExit(str(exc)) from exc


def load_run_document(run_dir):
    """Read the training document the driver wrote into a run directory.

    The driver writes the document VERBATIM to ``<run-dir>/config.json``
    (lookback, gap discipline, the declared module class, every node's
    params), which is why this loop reads it instead of restating any of
    it — and reads it through the ENGINE's own loader, which already
    refuses non-JSON, refuses a document that is not an object, and
    validates the whole node-map grammar into typed
    :class:`~dskit.pipeline.document.NodeSpec`s. A parse of the child's
    own would accept runs the engine never planned and then blame the
    document for what it could not read.

    Parameters
    ----------
    run_dir : str
        A run directory produced by ``python -m dskit.pipeline run``.

    Returns
    -------
    dskit.pipeline.document.PipelineDocument
        The document as declared, typed and validated.

    Raises
    ------
    SystemExit
        When the file is missing or unreadable, or when the engine
        refuses it — the engine's message verbatim, since the grammar is
        its truth, not this loop's.
    """
    path = os.path.join(run_dir, "config.json")
    try:
        return load_document(path)
    except OSError as exc:
        raise SystemExit(
            f"cannot read {path}: {exc} — --run-dir must name a run "
            "directory the pipeline wrote"
        ) from exc
    except ValueError as exc:  # ConfigError is one; both name the path
        raise SystemExit(str(exc)) from exc


def _is_window(uses):
    """Say whether ``uses`` names WindowRows — kind name or class ref."""
    if uses == _WINDOW_KIND:
        return True
    if not is_class_ref(uses):
        return False
    try:
        return import_ref(uses) is WindowRows
    except ValueError:
        return False  # a class this machine cannot import is not ours


def _window_nodes(document):
    """Return the document's window nodes, by node key."""
    return {key: spec for key, spec in document.pipeline.items()
            if _is_window(spec.uses)}


def window_knobs(document):
    """How the run windowed its bars: the price field and the gap bound.

    Both are the WindowRows knobs, resolved exactly as that node
    resolves them — an undeclared knob falls back to the node's own
    module constant, never to a second copy of the value. The node is
    found by either spelling the document grammar allows: the registered
    kind name, or a class reference that imports to
    :class:`~intraday_poc.nodes.WindowRows`.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        A training document, as :func:`load_run_document` returns it.

    Returns
    -------
    tuple of (str, float)
        The price field the run trained on, and its gap bound in
        minutes.

    Raises
    ------
    SystemExit
        When the document declares no window node, or declares several
        that disagree — one forward loop cannot serve two windowings.
    """
    windows = _window_nodes(document)
    if not windows:
        raise SystemExit(
            f"the run declares no {_WINDOW_KIND} node (by kind name or by "
            "class reference) — this loop cannot tell how its features "
            "were built"
        )
    resolved = set()
    for spec in windows.values():
        resolved.add((
            spec.params.get("price_field", DEFAULT_PRICE_FIELD),
            float(spec.params.get("max_gap_minutes", DEFAULT_MAX_GAP_MINUTES)),
        ))
    if len(resolved) > 1:
        raise SystemExit(
            f"the run's {_WINDOW_KIND} nodes disagree on how bars are "
            f"windowed ({sorted(resolved)}) — one loop cannot serve both"
        )
    return resolved.pop()


def declared_module(document):
    """Read the module class path the run declared (ADR-0025's seam).

    The document names the class its trainers built, so the loop that
    restores those artifacts reads the name from there. A literal here
    would break serving the moment the declared class changed — which
    is the whole point of the seam.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        A training document, as :func:`load_run_document` returns it.

    Returns
    -------
    str
        The declared class path, e.g. ``"pkg.models:Net"``.

    Raises
    ------
    SystemExit
        When no node declares ``module``, or the trainers disagree.
    """
    declared = {spec.params["module"]
                for spec in document.pipeline.values()
                if "module" in spec.params}
    if not declared:
        raise SystemExit(
            "the run declares no module class — --run-dir must name the "
            "run of a document that trained models"
        )
    if len(declared) > 1:
        raise SystemExit(
            f"the run declares several module classes ({sorted(declared)}) "
            "— this loop restores one class per run"
        )
    return declared.pop()


def source_knobs(path):
    """Resolve the acquisition source config's vendor knobs.

    Read through the CONNECTOR's own public knob gate, so an undeclared
    knob lands on the connector's default rather than a copy of it
    living here, and a config this loop accepts is one the puller
    accepts too. The universe it serves comes from here for the same
    reason.

    Parameters
    ----------
    path : str
        The connector config the source was registered with.

    Returns
    -------
    dict
        The resolved knobs (``symbols``, ``start``, ``feed``,
        ``adjustment``, ``live_lookback_minutes``, the credential
        env-var names).

    Raises
    ------
    SystemExit
        When the file is missing, unreadable, or not JSON.
    AssetError
        When the knobs themselves are invalid — the connector's own
        refusal, unchanged.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except OSError as exc:
        raise SystemExit(
            f"cannot read the source config {path}: {exc} — "
            "--source-config names the config the source was registered "
            "with"
        ) from exc
    except ValueError as exc:
        raise SystemExit(
            f"the source config {path} is not valid JSON: {exc}"
        ) from exc
    # The connector's own PUBLIC gate, not a copy of it: same
    # vocabulary, same defaults, same refusals.
    return AlpacaBarsConnector().resolve_knobs(config)


def parse_artifact_overrides(values):
    """Turn ``SYMBOL=PATH`` CLI values into a mapping.

    Parameters
    ----------
    values : list of str
        Raw ``--artifact`` values, in order.

    Returns
    -------
    dict
        Symbol -> artifact directory (relative to the run dir, or
        absolute); a later value for one symbol wins.

    Raises
    ------
    SystemExit
        On a value that is not ``SYMBOL=PATH``.
    """
    overrides = {}
    for value in values:
        symbol, sep, path = value.partition("=")
        if not sep or not symbol or not path:
            raise SystemExit(
                f"--artifact wants SYMBOL=PATH, got {value!r}"
            )
        overrides[symbol] = path
    return overrides


def artifact_dirs(run_dir, symbols, overrides):
    """Where each symbol's trained artifact lives.

    The run's own layout is the default — ``artifacts/<node key>``, and
    the documents key their trainers by lowercased symbol — so no table
    of symbols is written here; ``--artifact`` bends any single symbol
    to a document that names its nodes differently.

    Parameters
    ----------
    run_dir : str
        The run directory holding the artifacts.
    symbols : sequence of str
        The symbols to serve.
    overrides : dict
        Symbol -> path, from :func:`parse_artifact_overrides`.

    Returns
    -------
    dict
        Symbol -> artifact directory.

    Raises
    ------
    SystemExit
        When an override names a symbol the universe does not carry —
        nothing would ever look it up, so the loop would restore the
        model the operator was replacing and say nothing.
    """
    unknown = sorted(set(overrides) - set(symbols))
    if unknown:
        raise SystemExit(
            f"--artifact names {unknown}, which the source config does not "
            f"declare (its universe is {list(symbols)}) — a mistyped ticker "
            "would silently keep the artifact it was meant to replace"
        )
    return {
        symbol: os.path.join(
            run_dir,
            overrides.get(symbol,
                          os.path.join("artifacts", f"qhat_{symbol.lower()}")),
        )
        for symbol in symbols
    }


def restore_model(artifact_dir, module_ref):
    """Restore one trained module from its verified artifact.

    The pack's load discipline, applied outside a pipeline run: the
    sidecar's ``state_hash`` (sha256 over the state bytes, a NUL, then
    the canonical JSON of every other sidecar field) must match, and the
    sidecar's declared class must be the one the RUN declared — which
    the caller read from the run dir, so the declared-model seam still
    swings.

    Parameters
    ----------
    artifact_dir : str
        Directory holding ``model.pt`` and ``model.json``.
    module_ref : str
        The class path the run declared, e.g. ``"pkg.models:Net"``.

    Returns
    -------
    tuple of (object, list of str)
        The restored module in eval mode, and its feature list.

    Raises
    ------
    SystemExit
        On a missing file, a state_hash mismatch, a sidecar declaring a
        different class, a class path that cannot be resolved to a
        module with ``forward``, or a sidecar with no feature list.
    """
    import torch

    state_path = os.path.join(artifact_dir, "model.pt")
    sidecar_path = os.path.join(artifact_dir, "model.json")
    for path in (state_path, sidecar_path):
        if not os.path.isfile(path):
            raise SystemExit(f"artifact incomplete: {path} is missing")
    with open(sidecar_path, encoding="utf-8") as fh:
        sidecar = json.load(fh)

    material = {k: v for k, v in sidecar.items() if k != "state_hash"}
    with open(state_path, "rb") as fh:
        digest = hashlib.sha256(fh.read())
    digest.update(b"\x00")
    digest.update(json.dumps(material, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))
    if digest.hexdigest() != sidecar.get("state_hash"):
        raise SystemExit(
            f"artifact {artifact_dir}: state_hash mismatch — the artifact "
            "was edited or corrupted; refusing to trade on it"
        )
    params = sidecar.get("params", {})
    declared = params.get("module", "")
    if declared != module_ref:
        raise SystemExit(
            f"artifact {artifact_dir}: declares module {declared!r}, not "
            f"the run's {module_ref!r} — wrong artifact for this run"
        )
    # The same resolver the torch pack builds with, so a declared class
    # that is not a module is refused by NAME, here, not by a failure
    # inside load_state_dict.
    try:
        cls = import_library_class(module_ref, f"artifact {artifact_dir}",
                                   requires=("forward",))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    module = cls(**params.get("module_params", {}))
    module.load_state_dict(torch.load(state_path, weights_only=True))
    module.eval()
    features = list(params.get("features", []))
    if not features:
        raise SystemExit(f"artifact {artifact_dir}: sidecar carries no "
                         "feature list")
    return module, features


def _restore_signals(run_dir, symbols, overrides, module_ref):
    """Restore every symbol's module, and the lookback they all share.

    Parameters
    ----------
    run_dir : str
        The run directory holding the artifacts.
    symbols : sequence of str
        The symbols to serve.
    overrides : dict
        Symbol -> artifact directory, from the ``--artifact`` flag.
    module_ref : str
        The class path the run declared.

    Returns
    -------
    tuple of (dict, int)
        Symbol -> ``(module, features)``, and the common lookback.

    Raises
    ------
    SystemExit
        When two artifacts disagree on lookback — they were trained
        against different windowings and cannot be compared.
    """
    dirs = artifact_dirs(run_dir, symbols, overrides)
    signals = {}
    lookback = None
    for symbol in symbols:
        module, features = restore_model(dirs[symbol], module_ref)
        signals[symbol] = (module, features)
        if lookback is None:
            lookback = module.lookback
        elif lookback != module.lookback:
            raise SystemExit("artifacts disagree on lookback — retrain")
    return signals, lookback


def latest_feature_row(bars, lookback, max_gap_minutes):
    """Build the newest ``ret_lag_*`` vector these bars can support.

    The same chain semantics as ``WindowRows``: a gap wider than
    ``max_gap_minutes`` breaks the chain and nothing is bridged, so the
    caller passes the bound the RUN declared rather than a default of
    its own.

    Parameters
    ----------
    bars : list of tuple
        ``(asof_ms, price)`` ascending, one symbol.
    lookback : int
        How many one-bar log returns the row carries.
    max_gap_minutes : float
        Bars further apart than this never chain.

    Returns
    -------
    dict or None
        ``{"ret_lag_0": ..., ...}`` with ``ret_lag_0`` the most recent
        return, or ``None`` when coverage or gap discipline refuses.
    """
    if len(bars) < lookback + 1:
        return None
    gap_ms = max_gap_minutes * 60_000
    tail = bars[-(lookback + 1):]
    rets = []
    for i in range(1, len(tail)):
        if tail[i][0] - tail[i - 1][0] > gap_ms:
            return None
        if tail[i][1] <= 0 or tail[i - 1][1] <= 0:
            return None
        rets.append(math.log(tail[i][1] / tail[i - 1][1]))
    return {f"ret_lag_{lag}": rets[len(rets) - 1 - lag]
            for lag in range(lookback)}


def predict(module, features, row) -> float | None:
    """Score one feature row, the TorchSignal contract.

    Parameters
    ----------
    module : object
        A restored torch module in eval mode.
    features : list of str
        The feature names, in the order the module was trained on.
    row : dict
        One feature row.

    Returns
    -------
    float or None
        The prediction, or ``None`` when the row misses coverage — a
        belief is never fabricated.
    """
    import torch

    values = [row.get(name) for name in features]
    if any(v is None for v in values):
        return None
    with torch.no_grad():
        out = module(torch.tensor([values], dtype=torch.float32))
    return float(out.reshape(-1)[0])


def solve_pick(preds: dict) -> str:
    """Pick one symbol with the program the backtest solves.

    Parameters
    ----------
    preds : dict
        Symbol -> predicted next-bar return; must be non-empty.

    Returns
    -------
    str
        The chosen symbol.

    Raises
    ------
    RuntimeError
        If the solver selects nothing, which a one-per-timestamp
        equality constraint makes impossible.
    """
    import pyomo.environ as pyo

    model = build_select_model({0: preds})
    solver = pyo.SolverFactory("appsi_highs")
    solver.solve(model)
    for s in sorted(preds):
        if pyo.value(model.x[0, s]) > 0.5:
            return s
    raise RuntimeError("solver returned no selection — should be impossible "
                       "with a non-empty prediction set")


def bar_series(bars, price_field):
    """Vendor bars -> ``(asof_ms, price)`` pairs on the declared field.

    Parameters
    ----------
    bars : iterable
        Vendor bar objects for one symbol.
    price_field : str
        The field the RUN trained on — read from its document, never
        assumed here.

    Returns
    -------
    list of tuple
        ``(asof_ms, price)`` ascending; a minute the vendor published
        no value for is absent, and the gap discipline sees that.

    Raises
    ------
    SystemExit
        When the vendor's bars carry no such field — pricing on nothing
        would masquerade as "no coverage".
    """
    series = []
    for bar in bars:
        if not hasattr(bar, price_field):
            raise SystemExit(
                f"price_field {price_field!r} is not a field of an Alpaca "
                "bar — the run trained on a price this vendor does not "
                "publish"
            )
        value = getattr(bar, price_field)
        if value is None:
            continue
        series.append((int(bar.timestamp.timestamp() * 1000), float(value)))
    return sorted(series)


def fetch_bars(symbols, minutes, price_field, adjustment, key, secret):
    """Fetch the last ``minutes`` of bars per symbol.

    The interval comes from the connector's ``bar_timeframe()`` — the
    store and the served series must be the same series, so it is one
    constant there, never a literal here.

    Parameters
    ----------
    symbols : sequence of str
        Symbols to fetch — the source config's universe.
    minutes : int
        How far back to ask.
    price_field : str
        The price the run trained on (see :func:`bar_series`).
    adjustment : str
        The corporate-action adjustment the SOURCE config declares, so
        the served series is adjusted the way the trained one was.
    key : str
        The Alpaca key id, from the env var the source config NAMES
        (see :func:`credentials`).
    secret : str
        The matching secret.

    Returns
    -------
    dict
        Symbol -> ``[(asof_ms, price)]`` ascending.
    """
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest

    client = StockHistoricalDataClient(key, secret)
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    bars = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=bar_timeframe(),
        start=start,
        feed=DataFeed(LIVE_FEED),
        adjustment=Adjustment(adjustment),
    ))
    return {symbol: bar_series(series, price_field)
            for symbol, series in bars.data.items()}


def _current_position(trading, symbol):
    """How many shares of ``symbol`` the paper account holds (0 if none)."""
    from alpaca.common.exceptions import APIError

    try:
        return float(trading.get_open_position(symbol).qty)
    except APIError:
        return 0.0


def _flip_to(trading, winner, losers, qty, dry_run):
    """Hold ``qty`` of the winner and nothing else.

    Parameters
    ----------
    trading : object
        The paper trading client.
    winner : str
        The picked symbol.
    losers : sequence of str
        Symbols to flatten.
    qty : float
        Shares to hold in the winner.
    dry_run : bool
        Decide and report, place no orders.

    Returns
    -------
    list of str
        The actions taken (or that would be taken under ``dry_run``).
    """
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    actions = []
    for symbol in losers:
        held = _current_position(trading, symbol)
        if held > 0:
            actions.append(f"close {symbol} ({held:g})")
            if not dry_run:
                trading.close_position(symbol)
    if _current_position(trading, winner) <= 0:
        actions.append(f"buy {qty:g} {winner}")
        if not dry_run:
            trading.submit_order(order_data=MarketOrderRequest(
                symbol=winner, qty=qty, side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            ))
    return actions


def _parser():
    """Build the CLI — operational flags only, by design."""
    parser = argparse.ArgumentParser(
        prog="python -m intraday_poc.live", description=__doc__)
    parser.add_argument("--run-dir", required=True,
                        help="run dir of run-train.json (holds the "
                             "per-symbol artifacts and the document)")
    parser.add_argument("--source-config", required=True,
                        help="the connector config the source was "
                             "registered with — the live fetch takes its "
                             "vendor knobs AND the symbol universe from it")
    parser.add_argument("--artifact", action="append", default=[],
                        metavar="SYMBOL=PATH",
                        help="artifact dir for one symbol, relative to "
                             "--run-dir or absolute; repeatable. Default: "
                             "artifacts/qhat_<symbol>")
    parser.add_argument("--qty", type=float, default=1.0,
                        help="shares to hold in the picked symbol")
    parser.add_argument("--log-dir", default=".",
                        help="where decisions.jsonl accumulates")
    parser.add_argument("--once", action="store_true",
                        help="one iteration, then exit (smoke test)")
    parser.add_argument("--dry-run", action="store_true",
                        help="decide and log, place no orders")
    parser.add_argument("--history-minutes", type=int, default=240,
                        help="bar window fetched per iteration")
    return parser


def main(argv=None) -> int:
    """Run the forward loop over the declared universe.

    The symbols are the source config's, not the CLI's: they are the
    universe the store was acquired for, and serving a different one is
    the same train/serve skew as serving a different price field.

    Parameters
    ----------
    argv : list of str or None
        Command-line arguments; ``None`` reads ``sys.argv``.

    Returns
    -------
    int
        Process exit code; ``0`` when ``--once`` completed one
        iteration.

    Raises
    ------
    SystemExit
        On missing credentials, or any refusal from the run dir,
        source config, or artifacts.
    """
    args = _parser().parse_args(argv)

    # Everything the run and the pull already declared, read back —
    # the credential env-var NAMES among them.
    document = load_run_document(args.run_dir)
    price_field, max_gap_minutes = window_knobs(document)
    module_ref = declared_module(document)
    knobs = source_knobs(args.source_config)
    symbols, adjustment = knobs["symbols"], knobs["adjustment"]
    key, secret = credentials(knobs)

    from alpaca.trading.client import TradingClient

    trading = TradingClient(key, secret, paper=True)

    signals, lookback = _restore_signals(
        args.run_dir, symbols,
        parse_artifact_overrides(args.artifact), module_ref)
    print(f"models restored for {list(signals)} ({module_ref}, lookback "
          f"{lookback}, {price_field} bars, adjustment {adjustment}, feed "
          f"{LIVE_FEED})")

    log_path = os.path.join(args.log_dir, "decisions.jsonl")
    while True:
        clock = trading.get_clock()
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        record = {"at": now, "is_open": clock.is_open}
        if not clock.is_open:
            record["action"] = "market closed — no decision"
            print(record["action"], f"(next open {clock.next_open})")
        else:
            bars = fetch_bars(symbols, args.history_minutes,
                              price_field, adjustment, key, secret)
            preds = {}
            for symbol, (module, features) in signals.items():
                row = latest_feature_row(bars.get(symbol, []), lookback,
                                         max_gap_minutes)
                if row is None:
                    continue  # coverage refused — no fabricated belief
                pred = predict(module, features, row)
                if pred is not None:
                    preds[symbol] = pred
            record["predictions"] = preds
            if not preds:
                record["action"] = "no coverage — holding as-is"
            else:
                winner = solve_pick(preds)
                losers = [s for s in symbols if s != winner]
                actions = _flip_to(trading, winner, losers, args.qty,
                                   args.dry_run)
                record["pick"] = winner
                record["action"] = "; ".join(actions) or f"already in {winner}"
                if args.dry_run:
                    record["action"] = "[dry-run] " + record["action"]
            print(json.dumps(record))
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        if args.once:
            return 0
        # Wake shortly after the next minute boundary — IEX bars publish
        # ~2–3 s after the minute closes.
        now_s = time.time()
        time.sleep(60 - (now_s % 60) + 5)


if __name__ == "__main__":
    sys.exit(main())

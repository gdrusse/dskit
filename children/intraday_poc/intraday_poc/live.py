"""``live`` — the forward loop: predict, select, place the paper order.

Run-path only (torch, pyomo, alpaca-py) — never imported by
``__init__``. One iteration per completed minute bar:

1. gate on the exchange clock (``get_clock()`` — never a hardcoded
   session);
2. pull the latest 1-minute bars for the configured symbols over REST
   (IEX feed — the free tier's real-time-eligible feed; bars publish
   ~2–3 s after the minute closes, and a minute in which a symbol did
   not print on IEX simply has no bar) and hand them to the RUN'S OWN
   window node (``latest_rows``), which refuses rather than bridges —
   there is one implementation of the chain, not a serving copy of it
   (ADR-0040);
3. restore each symbol's model from the run's artifact — found through
   the run's OWN trainer node keys, sidecar verified (state_hash, S2-A),
   the trainer identity refused by name against the one the RUN declared
   (zoo class or declared module — the pack's own load discipline), and
   the pair refused outright when the two artifacts were built from
   different architecture knobs (``arch_params`` / ``module_params``);
4. decide by RUNNING the run's own selector node
   (:class:`intraday_poc.nodes.SelectOne`, rebuilt from the document,
   one timestamp wide) — not a second solve written here, so the
   solver, its options and the pack's refusals in front of them are the
   ones the backtest and the search decided under, proven solvable
   before the first order rather than at the first minute;
5. flip the paper position when the pick changes: flatten the loser,
   market-buy the winner (TIF ``day``), through the paper endpoint.

**This loop re-declares nothing the run already declared.** The
modelling knobs — the price field, the gap discipline, the run's
trainer identity (zoo class or declared module), which trainer belongs
to which symbol, the selector's solver — are READ
from ``<run-dir>/config.json``, the whole training document
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
        [--artifact AAPL=<a dir other than the run's own trainer node>]

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
import json
import os
import sys
import time

from dskit.onboarding import AssetError
from dskit.pipeline.base import (
    ConfigError,
    EnvConfig,
    import_library_class,
    import_ref,
    is_class_ref,
)
from dskit.pipeline.document import foreach_slug, load_document
from dskit.pipeline.env import load_env
from dskit.pipeline.libs.pyomo import DEFAULT_SOLVER
from dskit.pipeline.libs.torch_ts import (
    NODE_KINDS as TS_NODE_KINDS,
    TimeSeriesPredict,
    TimeSeriesTrain,
    zoo_regime,
)
from dskit.pipeline.node import NodeContext

from .connectors import (
    AlpacaBarsConnector,
    bar_timeframe,
    resolve_credentials,
)
from .nodes import NODE_KINDS, SelectOne, WindowRows

#: Kind name the documents use; the class-ref spelling is the sidecar
#: identity :meth:`TimeSeriesTrain._class_ref` writes.
_ZOO_KIND = next(name for name, cls in TS_NODE_KINDS if cls is TimeSeriesTrain)
_ZOO_REF = TimeSeriesTrain._class_ref()
_ZOO_USES = frozenset({_ZOO_KIND, _ZOO_REF})

__all__ = [
    "LIVE_FEED",
    "artifact_dirs",
    "bar_series",
    "credentials",
    "declared_module",
    "fetch_bars",
    "load_run_document",
    "main",
    "parse_artifact_overrides",
    "predict",
    "preflight_selector",
    "restore_model",
    "selector_node",
    "solve_pick",
    "source_knobs",
    "window_knobs",
    "window_node",
    "window_records",
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

#: The selector kind's name, read the same way for the same reason.
_SELECT_KIND = next(name for name, cls in NODE_KINDS.items()
                    if cls is SelectOne)

#: The one timestamp a live solve carries. The selection program is
#: per-timestamp and a forward minute has exactly one, so its LABEL is
#: bookkeeping the picks are read back by — never a time. Which minute
#: it was is stamped on the ``decisions.jsonl`` line instead, where the
#: rest of the forward record lives.
_LIVE_T = 0


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
    (lookback, gap discipline, the run's trainer identity, every node's
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


def _declares(uses, kind, cls):
    """Say whether ``uses`` names ``cls`` — kind name or class ref.

    Both spellings the document grammar allows, in one place: a node is
    found by the registered kind name, or by a class reference that
    imports to the class itself.
    """
    if uses == kind:
        return True
    if not is_class_ref(uses):
        return False
    try:
        return import_ref(uses) is cls
    except ValueError:
        return False  # a class this machine cannot import is not ours


def _is_window(uses):
    """Say whether ``uses`` names the child's window node."""
    return _declares(uses, _WINDOW_KIND, WindowRows)


def _is_select(uses):
    """Say whether ``uses`` names the child's selector node."""
    return _declares(uses, _SELECT_KIND, SelectOne)


def _window_nodes(document):
    """Return the window nodes the run RAN (fan-out included), by key."""
    return {key: spec for key, spec in document.expanded.items()
            if _is_window(spec.uses)}


def window_node(document):
    """Rebuild the window node the run trained through.

    The serving path's whole feature construction, in one object: the
    node is CONSTRUCTED from the document's own params, so the price
    field, the gap bound, the lag orientation, the label horizon and the
    causality screen are the run's — not a second reading of what the
    run did. ``latest_rows`` on this node is what the loop calls, and it
    is the same code the training rows came out of. The node is found by
    either spelling the document grammar allows: the registered kind
    name, or a class reference that imports to
    :class:`~intraday_poc.nodes.WindowRows`.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        A training document, as :func:`load_run_document` returns it.

    Returns
    -------
    intraday_poc.nodes.WindowRows
        The node, constructed and therefore validated.

    Raises
    ------
    SystemExit
        When the document declares no window node, when it declares
        several that disagree — one forward loop cannot serve two
        windowings — or when the declared params do not validate.
    """
    windows = _window_nodes(document)
    if not windows:
        raise SystemExit(
            f"the run declares no {_WINDOW_KIND} node (by kind name or by "
            "class reference) — this loop cannot tell how its features "
            "were built"
        )
    built = {}
    for key, spec in sorted(windows.items()):
        try:
            node = WindowRows(key, spec.params)
        except ConfigError as exc:
            raise SystemExit(str(exc)) from exc
        built[(node.price_field(), node.max_gap_minutes(), node.lookback())] = node
    if len(built) > 1:
        raise SystemExit(
            f"the run's {_WINDOW_KIND} nodes disagree on how bars are "
            f"windowed ({sorted(built)}) — one loop cannot serve both"
        )
    return next(iter(built.values()))


def window_knobs(document):
    """How the run windowed its bars: the price field and the gap bound.

    Read off the node itself (:func:`window_node`), so an undeclared
    knob resolves through the node's own accessor to the node's own
    module constant — this loop holds no copy of either value, and a
    copy is what would keep feeding close returns into vwap-trained
    weights the day the document is retuned.

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
        Whatever :func:`window_node` refuses.
    """
    node = window_node(document)
    return node.price_field(), node.max_gap_minutes()


def _is_zoo_trainer(spec):
    """Say whether ``spec`` names the zoo trainer (arch or uses)."""
    if "arch" in spec.params:
        return True
    return spec.uses in _ZOO_USES or _declares(spec.uses, _ZOO_KIND,
                                              TimeSeriesTrain)


def _is_trainer(spec):
    """Return whether ``spec`` is a trainer (module or zoo)."""
    return "module" in spec.params or _is_zoo_trainer(spec)


def declared_module(document):
    """Read the trainer identity the run declared.

    Two seams, one return: a node that names ``params.module`` is
    ADR-0025's declared class (bespoke nets still live there); otherwise
    trainers that name the zoo share :meth:`TimeSeriesTrain._class_ref`.
    A literal here would break serving the moment that identity
    changed — which is the whole point of the seam.

    Read off the map the run RAN (``expanded``), not the one its author
    wrote: under a ``foreach`` fan-out (ADR-0039) the trainers are
    template instances and the declared map holds none of them. The two
    maps are the same object when a document declares no fan-out, so
    this is the ONE reading, never a special case.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        A training document, as :func:`load_run_document` returns it.

    Returns
    -------
    str
        The trainer identity, e.g. ``"pkg.models:Net"`` or
        ``TimeSeriesTrain``'s class ref.

    Raises
    ------
    SystemExit
        When no node declares a trainer identity, or the trainers
        disagree.
    """
    declared = {spec.params["module"]
                for spec in document.expanded.values()
                if "module" in spec.params}
    if declared:
        if len(declared) > 1:
            raise SystemExit(
                f"the run declares several module classes "
                f"({sorted(declared)}) — this loop restores one class "
                "per run"
            )
        return declared.pop()
    if any(_is_zoo_trainer(spec) for spec in document.expanded.values()):
        return _ZOO_REF
    raise SystemExit(
        "the run declares no module class — --run-dir must name the "
        "run of a document that trained models"
    )


def selector_node(document):
    """Rebuild the selector node the run scored with.

    The same move :func:`window_node` makes, for the other half of the
    decision: the node is CONSTRUCTED from the document's own params, so
    the program, the solver and its options are the run's — not a
    second implementation of them here. Everything the pack's doorway
    owns then happens in the pack, once: it refuses an unregistered or
    unavailable solver BY NAME before solving anything, and it applies
    ``solver_options`` through the ``_solver_options`` seam a subclass
    overrides to pin its own determinism or tolerances. A
    ``SolverFactory`` call written in this file drops all three, and the
    difference only shows on a machine missing the backend — mid-session,
    with a position open.

    Construction also validates, so a selector this loop cannot serve
    refuses at startup rather than at the first minute with coverage.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        A training document, as :func:`load_run_document` returns it.

    Returns
    -------
    intraday_poc.nodes.SelectOne
        The node, constructed and therefore validated.

    Raises
    ------
    SystemExit
        When the document declares no selector node, or more than one —
        there would be no single declaration to serve under — or when
        the declared params do not validate.
    """
    selectors = {key: spec for key, spec in document.expanded.items()
                 if _is_select(spec.uses)}
    if len(selectors) != 1:
        raise SystemExit(
            f"the run's document declares {len(selectors)} selector "
            f"({_SELECT_KIND}) nodes — this loop solves the program the run "
            "scored with, so it builds that node, and one run may only have "
            "declared it once"
        )
    key, spec = next(iter(selectors.items()))
    try:
        return SelectOne(key, spec.params)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


def preflight_selector(selector, ctx, symbols):
    """Solve one throwaway minute, so a bad solver refuses at STARTUP.

    The pack resolves a solver on the run path — the installed pyomo is
    the only thing that knows whether a name is registered and its
    backend present — so a document naming a solver this machine lacks
    can only be caught by trying. Trying HERE — before the credentials
    are read, before the trading client exists — is the difference
    between a refusal an operator reads at startup and an exception
    thrown at the first minute with coverage, on top of whatever
    position the previous flip opened.

    Parameters
    ----------
    selector : intraday_poc.nodes.SelectOne
        The run's selector node, from :func:`selector_node`.
    ctx : dskit.pipeline.node.NodeContext
        The frame nodes run under, as :func:`main` builds it.
    symbols : sequence of str
        The served universe — solved over as a whole, so the check
        covers the program's real width, not a one-symbol corner.

    Raises
    ------
    SystemExit
        When the declared solver is unknown to this pyomo or its
        backend is not installed, naming the solver and the remedy.
    """
    try:
        solve_pick(selector, ctx, {symbol: 0.0 for symbol in symbols})
    except ValueError as exc:
        raise SystemExit(
            f"the run's selector cannot solve on this machine: {exc} — "
            "install that solver's backend, or serve a run whose select "
            "node names a solver this machine has"
        ) from exc


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


def _trainer_keys(document):
    """Return the node keys whose trainers the run wrote artifacts for.

    A trainer is a node declaring ``module`` or naming the zoo — the
    same rule :func:`declared_module` reads the identity off, so the
    two never disagree about which nodes are trainers.
    """
    return tuple(key for key, spec in document.expanded.items()
                 if _is_trainer(spec))


def _fanned_owner(document):
    """Instance node key -> the ``foreach`` key it was BUILT from.

    READ from the engine, never recomposed: ``foreach_groups`` is the
    document's own derived ``template key -> instance keys`` map, built
    inside the expansion by zipping those names against ``foreach.keys``
    in that order, so the same zip here recovers the pairing from the
    single statement that created it. Spelling the instance names again
    (``f"{template}__{slug}"``) would be the engine's private
    ``_instance_key`` written twice with nothing pinning the pair —
    and the day the engine assembles a name differently, this map would
    match nothing that ran and every trainer would fall back to
    :func:`_trainer_key`'s suffix branch, silently.

    An exact map is what the fallback cannot be: ``qhat__brk_b`` is one
    name, and no rule reading it alone can tell the template ``qhat`` +
    key ``BRK.B`` from a template ``qhat__brk`` + key ``B``.

    Empty for a document with no fan-out, which is the whole answer for
    one: nothing there was generated, so nothing there has an owner.
    """
    fan = document.foreach
    if fan is None:
        return {}
    return {name: key
            for names in document.foreach_groups.values()
            for name, key in zip(names, fan.keys)}


def _trainer_key(document, symbol, trainers):
    """Return the trainer node key the run wrote for ONE symbol.

    Two rules, because the two spellings carry different evidence.

    A FANNED trainer answers for exactly the ``foreach`` key it was
    built from (:func:`_fanned_owner`) — the document names those keys,
    so the mapping is read, not guessed. A suffix test here would serve
    one symbol's weights for another's bars: ``qhat__brk_b`` ends in
    ``_b``, so the symbol ``B`` would restore BRK.B's model, and the
    pair-regime check could not see it because both symbols would be
    reading ONE artifact. Alpaca spells real tickers that way.

    A HAND-DECLARED trainer carries no such record — nothing on disk
    says where ``qhat_aapl``'s stem ends — so it keeps the documents'
    own spelling: a key ending in an underscore and the symbol's slug.
    That is a heuristic, and it stays honest by refusing rather than
    choosing when two keys both match.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        The run's own document.
    symbol : str
        The vendor symbol to serve.
    trainers : sequence of str
        The document's trainer node keys, from :func:`_trainer_keys`.

    Returns
    -------
    str
        The trainer's node key, which is also its artifact directory.

    Raises
    ------
    SystemExit
        When no trainer, or more than one, is the symbol's — naming
        every trainer the run wrote, which is what an operator would
        pass to ``--artifact``.
    """
    owner = _fanned_owner(document)
    tail = f"_{foreach_slug(symbol)}"
    matches = [key for key in trainers
               if (owner[key] == symbol if key in owner
                   else key.endswith(tail))]
    if len(matches) == 1:
        return matches[0]
    problem = "no trainer" if not matches else f"{len(matches)} trainers"
    raise SystemExit(
        f"the run trained {problem} for {symbol!r}: its document keys "
        f"trainers {list(trainers)}, and a serving path may not invent a "
        "name the run never wrote — serve with --artifact "
        f"{symbol}=artifacts/<one of those>, or run a document that trains "
        "this symbol"
    )


def artifact_dirs(document, run_dir, symbols, overrides):
    """Where each symbol's trained artifact lives.

    Read off the RUN, never restated: the run's layout is
    ``artifacts/<node key>`` and its own document says which trainer it
    keyed for which symbol, so a convention written here would be this
    loop restating a training knob (root CLAUDE.md) — and would go stale
    the moment a document renamed its nodes, which ``foreach`` does to
    every fanned trainer. ``--artifact`` still bends any single symbol,
    and is consulted FIRST: it is the answer to a document this rule
    cannot read, so the rule must not refuse before it is heard.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        The run's document, as :func:`load_run_document` returns it.
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
        model the operator was replacing and say nothing — or when the
        run trained no single trainer for a symbol with no override.
    """
    unknown = sorted(set(overrides) - set(symbols))
    if unknown:
        raise SystemExit(
            f"--artifact names {unknown}, which the source config does not "
            f"declare (its universe is {list(symbols)}) — a mistyped ticker "
            "would silently keep the artifact it was meant to replace"
        )
    trainers = _trainer_keys(document)
    dirs = {}
    for symbol in symbols:
        relative = overrides.get(symbol)
        if relative is None:
            relative = os.path.join(
                "artifacts", _trainer_key(document, symbol, trainers))
        dirs[symbol] = os.path.join(run_dir, relative)
    return dirs


def restore_model(artifact_dir, module_ref):
    """Restore one trained module from its verified artifact.

    The pack's load discipline, applied outside a pipeline run: the
    sidecar's ``state_hash`` (sha256 over the state bytes, a NUL, then
    the canonical JSON of every other sidecar field) must match, and the
    sidecar's trainer identity must be the one the RUN declared — which
    the caller read from the run dir, so the seam still swings.

    Zoo nets are rebuilt through :meth:`TimeSeriesTrain.build_module`
    (they are not importable). A sidecar that names
    ``params.module`` still takes ADR-0025's declared-class path.

    Parameters
    ----------
    artifact_dir : str
        Directory holding ``model.pt`` and ``model.json``.
    module_ref : str
        The trainer identity the run declared.

    Returns
    -------
    tuple of (object, list of str, dict)
        The restored module in eval mode, its feature list, and the
        regime the artifact was BUILT from — the knobs the caller
        cross-checks between symbols, read here because this is where
        the sidecar is opened.

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

    params = sidecar.get("params", {})
    recorded = sidecar.get("module_class", "")
    is_zoo = "arch" in params or recorded == _ZOO_REF
    if is_zoo:
        if recorded != module_ref or recorded != _ZOO_REF:
            raise SystemExit(
                f"artifact {artifact_dir}: declares module {recorded!r}, "
                f"not the run's {module_ref!r} — wrong artifact for "
                "this run"
            )
        try:
            module, sidecar = TimeSeriesPredict(
                "restore", {"artifact": state_path},
            )._load_artifact(state_path)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        params = sidecar.get("params") or {}
        if getattr(module, "seq_len", None) != params.get("seq_len"):
            raise SystemExit(
                f"artifact {artifact_dir}: module.seq_len "
                f"{getattr(module, 'seq_len', None)!r} disagrees with "
                f"params.seq_len {params.get('seq_len')!r}"
            )
        features = list(params.get("features", []))
        if not features:
            raise SystemExit(f"artifact {artifact_dir}: sidecar carries "
                             "no feature list")
        return module, features, zoo_regime(params)

    try:
        got = TimeSeriesTrain._state_hash(state_path, sidecar)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"artifact {artifact_dir}: sidecar is not hashable: {exc}"
        ) from exc
    if got != sidecar.get("state_hash"):
        raise SystemExit(
            f"artifact {artifact_dir}: state_hash mismatch — the artifact "
            "was edited or corrupted; refusing to trade on it"
        )
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
    module_params = dict(params.get("module_params", {}))
    module = cls(**module_params)
    module.load_state_dict(
        torch.load(state_path, map_location="cpu", weights_only=True),
    )
    module.eval()
    features = list(params.get("features", []))
    if not features:
        raise SystemExit(f"artifact {artifact_dir}: sidecar carries no "
                         "feature list")
    return module, features, module_params


def _restore_signals(document, run_dir, symbols, overrides, module_ref):
    """Restore every symbol's module, and the ONE regime they share.

    The pair's agreement is pinned HERE because here is where it lands.
    The documents declare one regime for every symbol — one ``foreach``
    template in the tuned document, a pinned twin pair in the backtest —
    but a search's winner is chosen per instance, so a grid that crosses
    two symbols' widths can ship a run whose artifacts were built from
    different ``arch_params`` (or ``module_params``). The selector then
    compares beliefs from two architectures and calls the difference a
    signal. Every knob is compared, not lookback alone: a pin that
    omits a knob claims coverage it lacks.

    Parameters
    ----------
    document : dskit.pipeline.document.PipelineDocument
        The run's document, for :func:`artifact_dirs`.
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
        When two artifacts were built from different ``arch_params``
        — different windowings, or different architectures — so the
        beliefs the selector ranks are not comparable.
    """
    dirs = artifact_dirs(document, run_dir, symbols, overrides)
    signals, regime, regime_of, lookback = {}, None, None, None
    for symbol in symbols:
        module, features, module_params = restore_model(dirs[symbol],
                                                        module_ref)
        signals[symbol] = (module, features)
        width = getattr(module, "seq_len", getattr(module, "lookback", None))
        if regime is None:
            regime, regime_of, lookback = module_params, symbol, width
            continue
        divergent = sorted(
            knob for knob in set(regime) | set(module_params)
            if regime.get(knob) != module_params.get(knob)
        )
        if divergent:
            raise SystemExit(
                f"{symbol} and {regime_of} were trained under different "
                f"regimes — their artifacts disagree on {divergent} "
                f"({symbol}: {module_params}, {regime_of}: {regime}). The "
                "documents declare ONE regime for every symbol, but a grid "
                "search crosses the fan-out's per-instance space keys, so "
                "this run's winner paired them differently and the driver "
                "applied it to these artifacts. Re-running the document as "
                "written reproduces it — the grid is enumerated and the "
                "loaders are seeded — so pick a SYMMETRIC trial out of the "
                "run's carry.json, set its width on the foreach template's "
                "arch_params (one edit, both symbols; the backtest's twin "
                "pair moves with it), narrow or drop the search, and re-run. "
                "If the symbols really should differ, hand-expand the "
                "fan-out so each is a declared node with arch_params of "
                "its own — an instance key cannot be overridden beside the "
                "template it comes from"
            )
    return signals, lookback


def window_records(node, symbol, series):
    """Turn one symbol's fetched series into records the window node reads.

    The whole adapter between the vendor fetch and the training node:
    the node consumes the same record shape the store emits, so the
    serving path hands it that shape and nothing else. It restates no
    chain arithmetic — there is only one implementation of that now, and
    it is the node's (ADR-0040) — and no FIELD NAME either. All three
    spellings come off the node's own accessors, which are the only
    things that know them: a literal ``"asof_ms"`` here survives a
    retuned ``order_field()``, and then every record is unlifted and the
    loop dies inside the pack, naming the fetch instead of the copy.

    Parameters
    ----------
    node : intraday_poc.nodes.WindowRows
        The RUN's own window node, built from its document. Its
        ``group_field()``, ``order_field()`` and ``price_field()`` name
        the three keys each record carries.
    symbol : str
        The symbol these bars belong to — the GROUP value, which is also
        the key ``latest_rows`` answers under.
    series : list of tuple
        ``(asof_ms, price)`` ascending, as :func:`bar_series` returns it.

    Returns
    -------
    list of dict
        One record per bar, carrying the node's three declared fields
        and nothing else.
    """
    group, order, price_field = (
        node.group_field(), node.order_field(), node.price_field()
    )
    return [{group: symbol, order: asof_ms, price_field: price}
            for asof_ms, price in series]


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


def solve_pick(selector, ctx, preds):
    """Pick one symbol by RUNNING the run's own selector node.

    Not "the same program under the same solver" — literally the same
    node, one timestamp wide (:func:`selector_node`). The minute is
    therefore decided by the object the backtest scored its folds with
    and the search graded its trials with, so nothing about the decision
    can drift between the three: not the program, not the solver, not
    the options, not the pack refusals in front of them.

    ``labeled`` is empty because a forward minute has no outcome yet —
    the node's ``realized`` field comes back ``None``, and the loop's
    ``decisions.jsonl`` is where the forward record lives instead.

    Parameters
    ----------
    selector : intraday_poc.nodes.SelectOne
        The run's selector node, from :func:`selector_node`.
    ctx : dskit.pipeline.node.NodeContext
        The frame nodes run under, as :func:`main` builds it.
    preds : dict
        Symbol -> predicted next-bar return; must be non-empty.

    Returns
    -------
    str
        The chosen symbol.

    Raises
    ------
    ValueError
        From the pack, when the declared solver is unknown to this
        pyomo or its backend is missing — :func:`preflight_selector`
        turns that into a startup refusal.
    RuntimeError
        If the program selects nothing, which a one-per-timestamp
        equality constraint makes impossible.
    """
    forecasts = [{"symbol": symbol, "asof_ms": _LIVE_T, "pred": pred}
                 for symbol, pred in sorted(preds.items())]
    picks = selector.run(ctx, {"forecasts": forecasts, "labeled": []})["picks"]
    if not picks:
        raise RuntimeError("the selection program returned no pick — should "
                           "be impossible with a non-empty prediction set")
    return picks[0]["symbol"]


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
                             "the run's own trainer node for that symbol, "
                             "under artifacts/")
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
    window = window_node(document)
    # The two knobs the FETCH needs, read the one way anything reads
    # them — off the node, through its accessors, never a copy here.
    price_field, max_gap_minutes = window_knobs(document)
    module_ref = declared_module(document)
    selector = selector_node(document)
    # The frame a node runs under. The selection program reads nothing
    # off it, but a served node is a node: handing it a real context
    # costs one line and does not bet on what the pack ignores today.
    ctx = NodeContext(
        name=document.name,
        asof=dt.datetime.now(dt.timezone.utc).date().isoformat(),
        run_dir=args.run_dir,
    )
    knobs = source_knobs(args.source_config)
    symbols, adjustment = knobs["symbols"], knobs["adjustment"]
    # As early as the universe is known: prove the run's own selector can
    # actually solve here. Its solver only resolves against the installed
    # pyomo, so an absent backend is either a refusal before the trading
    # client exists, or an exception on top of an open position.
    preflight_selector(selector, ctx, symbols)
    key, secret = credentials(knobs)

    from alpaca.trading.client import TradingClient

    trading = TradingClient(key, secret, paper=True)

    signals, lookback = _restore_signals(
        document, args.run_dir, symbols,
        parse_artifact_overrides(args.artifact), module_ref)
    if lookback != window.lookback():
        # One pin, not two: the artifacts and the document must agree on
        # the window, or the loop would build rows of one width and feed
        # them to weights trained on another — silently, as "no
        # coverage", forever.
        raise SystemExit(
            f"the artifacts were trained on lookback {lookback} but the "
            f"run's window node declares {window.lookback()} — retrain, or "
            "serve the run whose document matches these artifacts"
        )
    print(f"models restored for {list(signals)} ({module_ref}, lookback "
          f"{lookback}, {price_field} bars, {max_gap_minutes:g}-minute gap "
          f"bound, adjustment {adjustment}, feed {LIVE_FEED}, solver "
          f"{selector.params.get('solver', DEFAULT_SOLVER)})")

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
            # ONE call, through the run's own window node: the serving
            # rows and the training rows come out of the same code.
            rows = window.latest_rows([
                record for symbol, series in sorted(bars.items())
                for record in window_records(window, symbol, series)
            ])
            preds = {}
            for symbol, (module, features) in signals.items():
                row = rows.get(symbol)
                if row is None:
                    continue  # coverage refused — no fabricated belief
                pred = predict(module, features, row)
                if pred is not None:
                    preds[symbol] = pred
            record["predictions"] = preds
            if not preds:
                record["action"] = "no coverage — holding as-is"
            else:
                winner = solve_pick(selector, ctx, preds)
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

"""The multi-horizon, multi-underlying cell grid and its document generator (ADR-0187).

A cell is one (underlying, days-to-expiry bucket). Its walk-forward document
is an ADR-0182 real rung (``configs/run-real-<rung>.json``) with exactly the
cell's changes applied by :func:`grid_document`: the underlying reader, the
label horizon, the reference-scale multiplier, the folds, the embargo and,
for an underlying with an ex-dividend source, the chain reader and the
archived-quote backtest. ``configs/grid/`` holds what :func:`write_grid`
writes; the config test pins every file to its generator, so a cell document
changes only through this table or its base rung, never by hand.

Every cell also ships the other rungs, a per-cell zoo (:func:`zoo_document`,
the ADR-0097 protocol over the four rung documents) and two HPO documents
(:func:`hpo_document`, ``hpo-grid`` over a rung's own knobs under the cell's
walk-forward, ADR-0043) — the owner's model-zoo and hyperparameter-tuning
process on every cell (ADR-0187 owner question 4, expanded 2026-09-27).
:func:`cell_files` builds one cell's full document set; :func:`grid_files`
calls it for every cell in :data:`CELLS`.

Every cell that carries a backtest also ships a put-credit-spread document
(:func:`put_spread_document`, ADR-0193): the cell's document with only its
backtest node swapped for :class:`~index_options.nodes.PutSpreadQuoteBacktest`
at :data:`PUT_SPREAD_SHORT_Q` / :data:`PUT_SPREAD_WING_Z`.

ADR-0194 adds the entry-gate study, annotate-only: :func:`gate_study_document`
puts the VIX3M closes and :class:`~index_options.nodes.VolRegimeSignals` in
front of a document's backtest, which then records what standing aside on each
gate would have removed. It is applied to every put-spread document and, as a
new ``<cell>-gate.json``, to every backtest cell's condor document.

ADR-0195 adds the forecast-scored payoff selector: :func:`select_document` swaps
a cell document's backtest for :class:`~index_options.nodes.PayoffSelectQuoteBacktest`
over the pre-registered :data:`SELECT_STRUCTURES` x :data:`SELECT_SHORT_Q` x
:data:`SELECT_WING_Z` candidates and applies the gate study, for every backtest
cell on the har-vix rung (``<cell>-select.json``) and on the empirical rung, the
premium-only control (``<cell>-empirical-select.json``).
"""

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .nodes import VolRegimeSignals

__all__ = [
    "BUCKETS",
    "CARRY_RATE",
    "CELLS",
    "GRID_RUNG",
    "HPO_SPACES",
    "LABEL_REACH_DAYS",
    "PUT_SPREAD_SHORT_Q",
    "PUT_SPREAD_WING_Z",
    "RUNGS",
    "SELECT_SHORT_Q",
    "SELECT_STRUCTURES",
    "SELECT_WING_Z",
    "TERM_SOURCE",
    "TERM_SYMBOL",
    "UNDERLYINGS",
    "ZOO_RUNGS",
    "Bucket",
    "Cell",
    "Underlying",
    "cell_files",
    "gate_study_document",
    "grid_document",
    "grid_files",
    "hpo_document",
    "put_spread_document",
    "select_document",
    "write_grid",
    "zoo_document",
]

#: Calendar days added to a bucket's upper bound for its embargo. The settlement's
#: reach is ``dte_max`` days; the label's reach is what h sessions span in calendar
#: days, closures included — measured over 1999-2025 as :data:`LABEL_REACH_DAYS`,
#: which ``dte_max + 7`` covers for every bucket (three at equality). The config
#: test pins the embargo against its own restatement of that table, never this one.
EMBARGO_MARGIN_DAYS = 7
#: Label horizon (sessions) -> the maximum calendar days it spanned in 1999-2025
#: (weekday sessions minus NYSE closures; 2001-09-11..14 and Sandy dominate).
LABEL_REACH_DAYS = {1: 7, 2: 10, 3: 11, 5: 13, 10: 21, 15: 28, 22: 39}
#: The constant upper bound on the cash rate charged on an assigned short put
#: (ADR-0187, the 2008-2025 maximum; owner question 6).
CARRY_RATE = 0.055
#: The put credit spread's short-put quantile (ADR-0193 owner answer): about a
#: 16-delta short put, against the condor cells' 0.1.
PUT_SPREAD_SHORT_Q = 0.16
#: The put credit spread's long-put wing in standardized units (ADR-0193 owner
#: answer): about a 5-delta long put, against the condor cells' 0.5.
PUT_SPREAD_WING_Z = 0.65
#: The structures the payoff selector chooses among, in tie-break order (ADR-0195 owner
#: answers, pre-registered): the put spread, the call spread and the condor.
SELECT_STRUCTURES = ("put_spread", "call_spread", "condor")
#: The short-leg quantiles the payoff selector tries (ADR-0195, pre-registered): about 10 to
#: 30 delta at the two tails.
SELECT_SHORT_Q = (0.10, 0.16, 0.20, 0.25, 0.30)
#: The wings the payoff selector tries, in standardized units (ADR-0195, pre-registered).
SELECT_WING_Z = (0.35, 0.65, 1.0)
#: The onboarding root the cell documents read, relative to the working directory.
ROOT = "./ob"
#: The archive source: closes with ex-dividend amounts, and the end-of-day chains.
CHAIN_SOURCE = "optionshist-chain"
#: The Cboe source holding the vol-index term structure (ADR-0194): the wide recorder's
#: separate stream, because the pack keeps one cursor per stream and ``cboe-index`` (VIX
#: alone) is already backfilled.
TERM_SOURCE = "cboe-index-wide"
#: The second vol index the gate study reads from :data:`TERM_SOURCE`: the three-month VIX.
TERM_SYMBOL = "VIX3M"


class Bucket(NamedTuple):
    """One days-to-expiry bucket: calendar days to SETTLEMENT, and its label horizon.

    Parameters
    ----------
    label : str
        The bucket's name in file names (``"30-45"``).
    dte_min, dte_max : int
        Inclusive calendar-day bounds from the entry session to the
        settlement date; ``dte_min >= 1`` (no 0DTE).
    label_horizon : int
        The sessions the lower bound spans: the label's horizon ``h``.
    band : float
        The log-moneyness band that bounds the chain read.

    Examples
    --------
    The longest bucket::

        bucket = Bucket("30-45", 30, 45, 22, 0.20)
        bucket.embargo_days
        # -> 52
    """

    label: str
    dte_min: int
    dte_max: int
    label_horizon: int
    band: float

    @property
    def embargo_days(self):
        """Return the walk-forward embargo in calendar days (int)."""
        return self.dte_max + EMBARGO_MARGIN_DAYS


class Underlying(NamedTuple):
    """One ETF: where its raw closes may be read from, and its yearly folds.

    Parameters
    ----------
    symbol : str
        The ticker, as the archive spells it.
    since : str
        The first close read (ISO): the first row after the last split, or
        the first row when there is none.
    first : str
        The first validation window's start (ISO); windows are yearly.
    folds : int
        How many yearly validation windows.
    backtest : bool
        Whether the archive carries the ex-dividend amounts the American
        charge needs; without them a cell runs labels, forecasts and scores
        only.

    Examples
    --------
    The one without a dividend source::

        iwm = Underlying("IWM", "2005-07-01", "2008-01-01", 18, False)
    """

    symbol: str
    since: str
    first: str
    folds: int
    backtest: bool


class Cell(NamedTuple):
    """One (underlying, bucket) pair — one walk-forward document per rung.

    Parameters
    ----------
    underlying : Underlying
    bucket : Bucket

    Examples
    --------
    The worked cell::

        cell = Cell(UNDERLYINGS[0], BUCKETS[-1])
        cell.name
        # -> 'spy-30-45'
    """

    underlying: Underlying
    bucket: Bucket

    @property
    def name(self):
        """Return the cell's file-name stem, ``<symbol lower>-<bucket>`` (str)."""
        return f"{self.underlying.symbol.lower()}-{self.bucket.label}"

    @property
    def since_ms(self):
        """Return the read start as epoch milliseconds at UTC midnight (int)."""
        day = datetime.fromisoformat(self.underlying.since).replace(tzinfo=timezone.utc)
        return int(day.timestamp()) * 1000


BUCKETS = (
    Bucket("1", 1, 1, 1, 0.05),
    Bucket("2-3", 2, 3, 2, 0.07),
    Bucket("5", 5, 5, 3, 0.08),
    Bucket("7-10", 7, 10, 5, 0.10),
    Bucket("14", 14, 14, 10, 0.12),
    Bucket("21", 21, 21, 15, 0.15),
    Bucket("30-45", 30, 45, 22, 0.20),
)
UNDERLYINGS = (
    Underlying("SPY", "1999-11-01", "2008-01-01", 18, True),
    Underlying("QQQ", "2000-03-21", "2011-01-01", 15, True),
    Underlying("IWM", "2005-07-01", "2008-01-01", 18, False),
)
CELLS = tuple(Cell(underlying, bucket) for underlying in UNDERLYINGS for bucket in BUCKETS)

#: Rung id -> the ADR-0182 base document it is generated from.
RUNGS = {
    "empirical": "run-real-distribution.json",
    "vix": "run-real-vix.json",
    "har-vix": "run-real-har-vix.json",
    "lightgbm-vix": "run-real-lightgbm-vix.json",
}
#: The rung every cell ships (ADR-0182's frontier pick).
GRID_RUNG = "har-vix"
#: The rungs a cell's zoo compares, in the real zoo's order.
ZOO_RUNGS = ("empirical", "vix", "har-vix", "lightgbm-vix")
#: The rungs that ship a select document (ADR-0195): the cell's frontier rung and the empirical
#: rung, whose forecast is the unconditional distribution, so it is the premium-only control.
_SELECT_RUNGS = (GRID_RUNG, "empirical")
#: Rung id -> the ``hpo-grid`` space over that rung's OWN knobs (ADR-0044: a
#: fitted transform's member knobs are searchable; its ``fit_split`` is not).
HPO_SPACES = {
    "har-vix": {"model.ridge_alpha": [0.0, 0.1, 1.0, 10.0]},
    "lightgbm-vix": {
        "model.lgbm_params.num_leaves": [3, 7, 15],
        "model.lgbm_params.learning_rate": [0.03, 0.1],
        "model.lgbm_params.min_child_samples": [20, 50],
    },
}
#: The archived-quote backtest nodes the generators swap between, by dotted class path.
_CONDOR_BACKTEST = "index_options.nodes:CondorQuoteBacktest"
_PUT_SPREAD_BACKTEST = "index_options.nodes:PutSpreadQuoteBacktest"
_SELECT_BACKTEST = "index_options.nodes:PayoffSelectQuoteBacktest"
_ORDER = ("vix", "vix_by_date", "market", "rv", "labels", "fwd", "model", "score", "condor")
#: The proxy backtest knobs the quote backtest takes over unchanged.
_SHARED_BACKTEST_KNOBS = ("split", "short_q", "wing_z", "multiplier", "fee_per_leg",
                          "min_edge_usd", "cvar_alpha")


def _cell_file(cell, rung):
    """Return the file name of one cell's rung document."""
    return f"{cell.name}.json" if rung == GRID_RUNG else f"{cell.name}-{rung}.json"


def _put_spread_file(cell):
    """Return the file name of one cell's put-credit-spread document."""
    return f"{cell.name}-put-spread.json"


def _gate_file(cell):
    """Return the file name of one cell's condor gate-study document."""
    return f"{cell.name}-gate.json"


def _select_file(cell, rung):
    """Return the file name of one cell's payoff-selection document on ``rung``."""
    return f"{cell.name}-select.json" if rung == GRID_RUNG else f"{cell.name}-{rung}-select.json"


def _load(path):
    """Read one JSON document."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def grid_document(base, cell, rung=GRID_RUNG):
    """Return one cell's walk-forward document from a base rung.

    Parameters
    ----------
    base : dict
        The ADR-0182 real rung document :data:`RUNGS` names for ``rung``.
    cell : Cell
        The cell.
    rung : str
        A key of :data:`RUNGS`; names the document and its file.

    Returns
    -------
    dict
        The cell document: the base with the underlying reader, the label
        horizon, the scale multiplier, the folds and the embargo replaced,
        and the chain reader plus archived-quote backtest in place of the
        proxy backtest where the underlying carries dividends.

    Raises
    ------
    ValueError
        When ``rung`` is not a known rung.
    """
    if rung not in RUNGS:
        raise ValueError(f"rung must be one of {sorted(RUNGS)}, got {rung!r}")
    doc = copy.deepcopy(base)
    pipe, bucket, symbol = doc["pipeline"], cell.bucket, cell.underlying.symbol
    horizon = bucket.label_horizon
    doc["name"] = f"index-options-grid-{cell.name}-{rung}"
    doc["notes"] = (
        f"ADR-0187 cell {cell.name}: {symbol} condors {bucket.dte_min}-{bucket.dte_max} "
        f"calendar days to settlement, label horizon {horizon} sessions, chain read within "
        f"{bucket.band} log-moneyness, {cell.underlying.folds} yearly validation folds from "
        f"{cell.underlying.first}, embargo {bucket.embargo_days} days. The {rung} rung: "
        f"generated from {RUNGS[rung]} ({base['name']}) by index_options.grid.grid_document, "
        "so edit the base rung or the grid table and rewrite with write_grid, never this "
        "file. Needs a pulled "
        "./ob holding optionshist-chain (index_daily + option_chain) and cboe-index (VIX). "
        f"Run: python -m dskit.pipeline walkforward configs/grid/{_cell_file(cell, rung)} "
        "--asof <today>. Never decision-eligible."
    )
    ordered = {"underlying": {
        "uses": "index_options.observations:IndexCloseRows",
        "params": {"root": ROOT, "source": CHAIN_SOURCE, "symbol": symbol,
                   "since_ms": cell.since_ms},
        "notes": (f"{symbol} raw daily closes with each ex-date's dividend_amount (the "
                  f"optionshist index_daily stream, ADR-0187). since_ms is {cell.underlying.since} "
                  "UTC midnight: the first row after the last split, because raw prices are "
                  "the strike basis and the reader refuses a flagged split."),
    }}
    for key in _ORDER:
        ordered[key] = pipe[key]
    ordered["market"]["inputs"]["records"] = "$underlying.records"
    ordered["market"]["notes"] = (
        "Same-date VIX close as iv_index for every underlying: VXN and RVX start only in "
        "2009-09, and the S&P 500's implied vol is the proxy scale feature (ADR-0187); the "
        "implied book prices from each underlying's own chain iv instead. A date VIX lacks "
        "keeps iv_index null.")
    for key in ("labels", "fwd"):
        ordered[key]["params"]["horizon"] = horizon
    ordered["labels"]["notes"] = (
        f"ln(S_t+h / S_t) over h = {horizon} sessions, the bucket's label horizon (the "
        "sessions its lower DTE bound spans, ADR-0187); the embargo covers its calendar "
        "reach. Rows whose horizon runs past the data keep a null label.")
    ordered["fwd"]["notes"] = (
        f"Realized per-step vol over the same {horizon} sessions as the label: the target "
        "a scale rung regresses on. Every rung of a cell shares this pipeline.")
    ordered["model"]["params"]["scale_multiplier"] = math.sqrt(horizon)
    if cell.underlying.backtest:
        proxy = pipe["backtest"]["params"]
        ordered["chain"] = {
            "uses": "index_options.observations:ChainQuoteRows",
            "params": {"root": ROOT, "source": CHAIN_SOURCE, "symbol": symbol,
                       "dte_min": bucket.dte_min, "dte_max": bucket.dte_max,
                       "max_abs_log_moneyness": bucket.band},
            "notes": (f"{symbol}'s archived 16:00 ET quotes for this bucket only, bounded at "
                      "intake (root, 0.5 strike grid, settlement-date DTE, positive "
                      "underlying close, log-moneyness band); one parsed snapshot per "
                      "process serves every fold. The three bounds are restated on the "
                      "backtest and pinned equal by test."),
        }
        ordered["backtest"] = {
            "uses": _CONDOR_BACKTEST,
            "inputs": {"forecasts": "$model.rows", "chain": "$chain.records",
                       "underlying": "$underlying.records"},
            "params": {
                **{k: proxy[k] for k in _SHARED_BACKTEST_KNOBS[:5]},
                "dte_min": bucket.dte_min, "dte_max": bucket.dte_max,
                "max_abs_log_moneyness": bucket.band, "label_horizon": horizon,
                "carry_rate": CARRY_RATE,
                **{k: proxy[k] for k in _SHARED_BACKTEST_KNOBS[5:]},
            },
            "notes": (
                "ADR-0187 archived-quote condor backtest over val: one condor per entry "
                "date at the nearest listed expiry in the bucket, the next entry on or after "
                "its settlement date. Shorts at the forecast's short_q quantiles rescaled by "
                "sqrt(sessions / label_horizon), wings wing_z further out, every leg snapped "
                "outward onto a QUOTABLE listed strike (a short needs a bid and a bid size, a "
                "long an ask and an ask size); the implied book uses the chain's ATM iv. "
                "Settles at the last close within four days of the settlement date; the "
                "American charge (dividends on an ITM short call, carry at carry_rate on an "
                "ITM short put) comes off each book. Fills are the end-of-day touch at zero "
                "latency, so absolute P&L is indicative and the model / always / implied "
                "comparison on identical quotes is the signal. Never decision-eligible."),
        }
    doc["pipeline"] = ordered
    walk = doc["walkforward"]
    walk.update(first=cell.underlying.first, count=cell.underlying.folds,
                embargo_days=bucket.embargo_days)
    walk["notes"] = (
        f"Expanding train windows on session rows; {cell.underlying.folds} yearly validation "
        f"windows from {cell.underlying.first} (the archive's chains begin there for {symbol}). "
        f"embargo_days = dte_max + {EMBARGO_MARGIN_DAYS} = {bucket.embargo_days} covers both "
        f"the label's reach ({horizon} sessions spanned at most "
        f"{LABEL_REACH_DAYS[horizon]} calendar days over 1999-2025, closures included) and "
        f"the settlement's reach ({bucket.dte_max} days), so no train label or settlement "
        "reaches past the validation cut.")
    return doc


def put_spread_document(base, cell):
    """Return one cell's put-credit-spread document (ADR-0193).

    Parameters
    ----------
    base : dict
        The ADR-0182 real rung document :data:`RUNGS` names for
        :data:`GRID_RUNG`; it is copied, never changed.
    cell : Cell
        A cell whose underlying carries a backtest.

    Returns
    -------
    dict
        :func:`grid_document` for ``cell`` with only its backtest node
        swapped: ``uses`` :class:`~index_options.nodes.PutSpreadQuoteBacktest`,
        ``short_q`` :data:`PUT_SPREAD_SHORT_Q`, ``wing_z``
        :data:`PUT_SPREAD_WING_Z`, and the document's name and notes and the
        node's notes re-worded. Every other node, input and knob is the
        condor cell's, so the two backtests read one chain and one forecast.

    Raises
    ------
    ValueError
        When the cell's underlying has no dividend source, hence no backtest.
    """
    symbol = cell.underlying.symbol
    if not cell.underlying.backtest:
        raise ValueError(f"cell {cell.name}: {symbol} carries no dividend source, so it has "
                         "no backtest to derive a put credit spread from")
    doc = grid_document(base, cell)
    bucket = cell.bucket
    doc["name"] = f"index-options-grid-{cell.name}-put-spread"
    doc["notes"] = (
        f"ADR-0193 cell {cell.name}: {symbol} put credit spreads {bucket.dte_min}-"
        f"{bucket.dte_max} calendar days to settlement: the condor cell's put wing alone, a "
        f"short put at the forecast's {PUT_SPREAD_SHORT_Q} quantile (about 16 delta) with a "
        f"long put {PUT_SPREAD_WING_Z} standardized units further out (about 5 delta), on the "
        f"same forecasts, chain and settlement. Generated by "
        f"index_options.grid.put_spread_document from {RUNGS[GRID_RUNG]} ({base['name']}): "
        f"the cell's har-vix document with only the backtest node swapped, so edit the base "
        "rung or the grid table and rewrite with write_grid, never this file. Needs a "
        "pulled ./ob holding optionshist-chain (index_daily + option_chain) and cboe-index "
        f"(VIX). Run: python -m dskit.pipeline walkforward "
        f"configs/grid/{_put_spread_file(cell)} --asof <today>. Never decision-eligible.")
    node = doc["pipeline"]["backtest"]
    node["uses"] = _PUT_SPREAD_BACKTEST
    node["params"]["short_q"] = PUT_SPREAD_SHORT_Q
    node["params"]["wing_z"] = PUT_SPREAD_WING_Z
    node["notes"] = (
        "ADR-0193 archived-quote put credit spread backtest over val: the condor backtest's "
        "put wing alone, entered wherever that wing is quotable (a condor whose call side "
        "cannot be quoted is skipped; the spread is not). One spread per entry date at the "
        "nearest listed expiry in the bucket, the next entry on or after its settlement date; "
        "the short put at the forecast's short_q quantile (PUT_SPREAD_SHORT_Q, about 16 delta) "
        "rescaled by sqrt(sessions / label_horizon), the long put wing_z further out "
        "(PUT_SPREAD_WING_Z, about 5 delta), each snapped outward onto a QUOTABLE listed strike; "
        "the implied book's short put is the condor's. Settles at the last close within four "
        "days of the settlement date, less the American carry on an in-the-money short put "
        "(no call dividend). Two legs of fees. Each entered cell also carries a delta-matched "
        "stock P&L (Black-76 forward delta from the legs' own iv) and the metrics "
        "<book>_residual_mean_pnl_usd, _residual_t and _pnl_t say whether the spread beat that "
        "stock. Fills are the end-of-day touch at zero latency, so absolute P&L is indicative "
        "and the model / always / implied comparison on identical quotes is the signal. Never "
        "decision-eligible.")
    return doc


def gate_study_document(doc, name_suffix="-gate"):
    """Return a backtest document with the ADR-0194 entry-gate study on top.

    Parameters
    ----------
    doc : dict
        A cell document carrying a backtest that reads ``$model.rows`` and
        declares no ``gate_fields``: :func:`grid_document` for a cell with a
        dividend source, :func:`put_spread_document` or :func:`select_document`'s
        source. It is copied, never changed.
    name_suffix : str
        Appended to the document's name: ``"-gate"`` for a document whose file
        is the study alone, ``""`` where the caller's own file name already
        names the document (the put-spread and select documents).

    Returns
    -------
    dict
        The document with two nodes right after ``model`` — ``vix3m``
        (:class:`~index_options.observations.IndexCloseRows` over
        :data:`TERM_SYMBOL` from :data:`TERM_SOURCE`) and ``signals``
        (:class:`~index_options.nodes.VolRegimeSignals` with EVERY knob written
        out at the node's defaults, so the identity hash covers the
        thresholds, over ``$model.rows`` and ``$vix3m.records``) — the
        backtest's ``forecasts`` re-wired to ``$signals.rows`` and its
        ``gate_fields`` set to
        :attr:`~index_options.nodes.VolRegimeSignals.GATE_FIELDS`, the study's
        sentence APPENDED to the document's notes (its own description stays)
        and to the backtest's notes. Nothing else differs, so the study
        measures the same trades.

    Raises
    ------
    ValueError
        When the document has no ``model`` or ``backtest`` node (an
        underlying without a dividend source), already carries the study,
        declares ``gate_fields``, or its backtest does not read ``$model.rows``
        (the study would then re-point it at rows it never traded).
    """
    pipe = doc["pipeline"]
    for key in ("model", "backtest"):
        if key not in pipe:
            raise ValueError(f"{doc['name']}: the gate study needs a {key!r} node, and this "
                             "document has none (an underlying without a dividend source "
                             "carries no backtest)")
    if {"vix3m", "signals"} & set(pipe):
        raise ValueError(f"{doc['name']}: the document already carries the gate study")
    if "gate_fields" in pipe["backtest"]["params"]:
        raise ValueError(f"{doc['name']}: the backtest already declares gate_fields")
    if pipe["backtest"]["inputs"].get("forecasts") != "$model.rows":
        raise ValueError(f"{doc['name']}: the study re-points the backtest's forecasts at the "
                         "annotated model rows, so it must read '$model.rows' to begin with, "
                         f"not {pipe['backtest']['inputs'].get('forecasts')!r}")
    out = copy.deepcopy(doc)
    defaults = VolRegimeSignals.DEFAULTS
    out["name"] = f"{doc['name']}{name_suffix}"
    out["notes"] = doc["notes"] + (
        " ADR-0194 entry-gate study, annotate-only, added by index_options.grid."
        "gate_study_document to the document described above: the same forecasts, chain and "
        "trades, plus two nodes. vix3m reads the Cboe VIX3M closes and signals "
        "(index_options.nodes.VolRegimeSignals) gives every forecast row the PREVIOUS "
        "session's VIX / VIX3M ratio and its expanding percentile, the previous session's "
        "variance premium ((VIX/100)^2 less annualized realized variance) and four boolean "
        f"gates: the curve inverted (ratio >= {defaults['inverted_at']}), the ratio in its own "
        f"top tail (percentile >= {defaults['high_ratio_pct']}), a non-positive premium, and "
        "any of them; an input that is missing gives null, never false. The backtest reads "
        "those rows and, per book and gate, reports the traded P&L where the gate was closed "
        "(it would have stood aside), open, or unknown: no entry is skipped, so the metrics "
        "say what standing aside would have removed, not what a gated strategy earns. The "
        "lag is one session, so the 16:15 ET VIX close never informs a 16:00 ET entry. Needs "
        f"the store to hold what the description above needs plus {TERM_SOURCE} "
        f"({TERM_SYMBOL}; register and backfill configs/source-cboe-index-wide.json). Run "
        "THIS document, not the one it was derived from: python -m dskit.pipeline "
        "walkforward <this file> --asof <today>.")
    ordered = {}
    for key, node in out["pipeline"].items():
        ordered[key] = node
        if key == "model":
            ordered["vix3m"] = {
                "uses": "index_options.observations:IndexCloseRows",
                "params": {"root": ROOT, "source": TERM_SOURCE, "symbol": TERM_SYMBOL},
                "notes": (f"{TERM_SYMBOL} daily closes from the wide Cboe source "
                          f"({TERM_SOURCE}; the history starts 2007-12): the three-month "
                          "VIX, the far end of the curve the gate compares the VIX to. Read "
                          "only by signals."),
            }
            ordered["signals"] = {
                "uses": "index_options.nodes:VolRegimeSignals",
                "inputs": {"rows": "$model.rows", "term": "$vix3m.records"},
                "params": dict(defaults),
                "notes": ("Adds the term ratio, its percentile, the variance premium and "
                          "the four gates to every model row, each read from the PREVIOUS "
                          "session (lag_sessions 1). Every knob is written out (implied "
                          "iv_index, realized rv_22, 252 periods a year, the two "
                          "pre-registered thresholds, the 252-session percentile history) "
                          "so this document's identity hash covers them: change one here "
                          "and the hash, hence the run directory, moves."),
            }
    out["pipeline"] = ordered
    backtest = ordered["backtest"]
    backtest["inputs"]["forecasts"] = "$signals.rows"
    backtest["params"]["gate_fields"] = list(VolRegimeSignals.GATE_FIELDS)
    backtest["notes"] += (
        " ADR-0194: the forecast rows come from signals, and gate_fields names its four "
        "gates. Each ledger entry records the gates read from its entry row and every book "
        "reports <book>_<gate>_closed_n / _closed_mean_pnl_usd / _closed_t (gate true: it "
        "would have stood aside), the same three _open_ (false) and _unknown_n (null) over "
        "its traded cells. Annotation only: the trades are unchanged.")
    return out


def select_document(doc):
    """Return a cell document whose backtest selects its structure from the forecast (ADR-0195).

    Parameters
    ----------
    doc : dict
        A cell document whose backtest is the condor's
        :class:`~index_options.nodes.CondorQuoteBacktest`: :func:`grid_document`
        for a cell with a dividend source, on any rung. It is copied, never
        changed.

    Returns
    -------
    dict
        The document with only its backtest node swapped for
        :class:`~index_options.nodes.PayoffSelectQuoteBacktest`, which gains the
        three candidate knobs :data:`SELECT_STRUCTURES`, :data:`SELECT_SHORT_Q`
        and :data:`SELECT_WING_Z` (its ``short_q`` and ``wing_z`` stay the
        condor's: they define the implied benchmark book), then
        :func:`gate_study_document`'s study with no name suffix (the name is the
        source's plus ``-select``), so a later sizing step can sit beside
        ``signals``. The document's notes extend the source's; the backtest
        node's notes are re-worded, as :func:`put_spread_document` does, because
        the condor's no longer describe what its model and always books trade.

    Raises
    ------
    ValueError
        When the document has no backtest (an underlying without a dividend
        source), its backtest is not the condor's, or the study cannot apply
        (see :func:`gate_study_document`).
    """
    backtest = doc["pipeline"].get("backtest")
    if backtest is None or backtest["uses"] != _CONDOR_BACKTEST:
        raise ValueError(f"{doc['name']}: a payoff selector replaces the condor archived-quote "
                         f"backtest ({_CONDOR_BACKTEST}), and this document's backtest is "
                         f"{None if backtest is None else backtest['uses']!r}")
    out = copy.deepcopy(doc)
    out["name"] = f"{doc['name']}-select"
    out["notes"] = doc["notes"] + (
        " ADR-0195 payoff-selection study, generated by index_options.grid.select_document "
        "from the document described above: the same forecasts, chain, settlement, American "
        "charge and entry gates, but the backtest node is PayoffSelectQuoteBacktest. At each "
        f"entry it snaps every candidate ({len(SELECT_STRUCTURES)} structures "
        f"{list(SELECT_STRUCTURES)} x short_q {list(SELECT_SHORT_Q)} x wing_z "
        f"{list(SELECT_WING_Z)}: {len(SELECT_STRUCTURES) * len(SELECT_SHORT_Q) * len(SELECT_WING_Z)} "
        "candidates, pre-registered as grid.SELECT_*), prices it at bid/ask, drops what the "
        "credit gates refuse and enters the one with the highest E[P&L] / max loss, the "
        "expectation over THIS document's forecast draws at the horizon scale. The model book "
        "enters it when that expectation clears min_edge_usd, the always book regardless, and "
        "the implied book stays the VIX-implied condor at short_q / wing_z. The same document "
        "on the empirical rung is the premium-only control (its forecast is the unconditional "
        "distribution, so it selects on the quotes' premium alone): what the forecast adds is "
        "the DIFFERENCE between the two runs' model books, never either run's level.")
    node = out["pipeline"]["backtest"]
    node["uses"] = _SELECT_BACKTEST
    node["params"].update(candidate_structures=list(SELECT_STRUCTURES),
                          candidate_short_q=list(SELECT_SHORT_Q),
                          candidate_wing_z=list(SELECT_WING_Z))
    node["notes"] = (
        "ADR-0195 archived-quote payoff-selection backtest over val. The condor backtest's walk, "
        "expiry, settlement, strike snapping, credit gates, American charge, wing split and "
        "delta benchmark, but the model and always books trade the structure the forecast "
        "scores best rather than a fixed condor: every candidate (structure x candidate_short_q "
        "x candidate_wing_z, structures outermost and then short_q then wing_z; the order breaks "
        "exact ties) is snapped at the horizon scale, priced at bid/ask with fees and dropped if "
        "a credit gate refuses it, then scored as E[P&L] / max loss, the expectation over the "
        "forecast draws and the max loss the widest vertical in USD less the credit. Each model "
        "and always cell records the chosen structure, quantile, wing, score and how many "
        "candidates were scored; the metrics add <book>_n_<structure> and summarize each side "
        "over the traded cells that hold it. short_q and wing_z remain the implied condor's. "
        "Fills are the end-of-day touch at zero latency, so absolute P&L is indicative and the "
        "model / always / implied comparison on identical quotes, and the empirical rung's "
        "difference, is the signal. Never decision-eligible.")
    return gate_study_document(out, name_suffix="")


def hpo_document(cell_document, rung):
    """Return the per-fold re-tune document of one rung on one cell (ADR-0043).

    Parameters
    ----------
    cell_document : dict
        The rung's cell document, as :func:`grid_document` returns it.
    rung : str
        A key of :data:`HPO_SPACES`.

    Returns
    -------
    dict
        The cell document without its condor report, chain and backtest, plus
        a ``search`` node (``hpo-grid`` over the rung's own knobs, objective
        the val twCRPS). Every fold re-tunes independently: this measures
        the tuning procedure and ships nothing — a winner is shipped by
        pinning it into the cell document, which moves that document's hash
        by design.
    """
    doc = copy.deepcopy(cell_document)
    doc["name"] = f"{cell_document['name']}-hpo"
    doc["notes"] = (
        f"ADR-0043 per-fold re-tune of the {rung} rung on this cell: hpo-grid over the "
        "rung's own knobs, objective the val twCRPS, one exhaustive search per fold. This "
        "MEASURES the tuning procedure (the summary reports per-fold winners and how many "
        "were distinct); it is not a deployment estimate. Ship a winner by pinning it into "
        "the cell document (hash moves by design). The condor report, chain and backtest are "
        "left out: the winner-consistency rule re-runs every consumer of the model with the "
        "winner, and those nodes belong to the frozen document. Generated by "
        "index_options.grid.hpo_document; never edit this file.")
    for key in ("condor", "chain", "backtest"):
        doc["pipeline"].pop(key, None)
    doc["pipeline"]["search"] = {
        "uses": "hpo-grid",
        "params": {"space": copy.deepcopy(HPO_SPACES[rung]),
                   "objective": "$score.metrics.twcrps", "select": "min"},
        "notes": (
            "The space addresses the model's OWN knobs only (ADR-0044: fit_split is never "
            "searchable). A list value is exhaustive; add n_trials to subsample it. Lower "
            "twCRPS is better, so select is min."),
    }
    return doc


def zoo_document(zoo_base, cell, rungs=ZOO_RUNGS):
    """Return one cell's model zoo: the real zoo's protocol over the cell's rung documents.

    Parameters
    ----------
    zoo_base : dict
        ``run-real-zoo.json``.
    cell : Cell
        The cell.
    rungs : sequence of str
        The rung ids to compare, each a candidate of the base zoo.

    Returns
    -------
    dict
        The staged plan -> approval -> run -> compare document with each
        candidate re-pointed at the cell's rung file, the contract paths
        re-spelled for the cell's pipeline, and the attempt family named
        after the cell.
    """
    doc = copy.deepcopy(zoo_base)
    symbol = cell.underlying.symbol
    doc["name"] = f"index-options-grid-{cell.name}-zoo"
    doc["notes"] = (
        f"ADR-0187 zoo over cell {cell.name}: the ADR-0182 rungs on one underlying and "
        "bucket, identical except 'model' (contract_paths pin everything else). Run from the "
        f"child root: python -m dskit.pipeline staged configs/grid/{cell.name}-zoo.json "
        "--asof <today>. First run is plan-only: paste the printed inventory sha256 into "
        "approval, set approved_by, run again. Primary score is twCRPS (locked Path A0002); "
        "the backtest metrics ride along per fold as a sanity check, never a selection "
        "criterion. A candidate may carry no search node (ADR-0097): tune with the "
        "cell's hpo documents, pin the winner, then compare. Generated by "
        "index_options.grid.zoo_document; never edit this file.")
    doc["pipeline"] = {"market": {
        "uses": "index_options.observations:IndexCloseRows",
        "params": {"root": ROOT, "source": CHAIN_SOURCE, "symbol": symbol,
                   "since_ms": cell.since_ms},
        "notes": "Structural node only; each candidate document owns its own pipeline.",
    }}
    plan = doc["stages"]["plan"]["params"]
    by_id = {c["id"]: c for c in plan["candidates"]}
    plan["candidates"] = [dict(by_id[rung], path=_cell_file(cell, rung)) for rung in rungs]
    paths = []
    for path in plan["contract_paths"]:
        if path == "pipeline.spx":
            paths.append("pipeline.underlying")
        elif path == "pipeline.backtest":
            if cell.underlying.backtest:
                paths.extend(["pipeline.chain", "pipeline.backtest"])
        else:
            paths.append(path)
    plan["contract_paths"] = paths
    plan["protocol"] = dict(
        plan["protocol"], attempt_family=f"index-options-grid-{cell.name}",
        lockbox=(f"none held out: val folds run {cell.underlying.first} through the "
                 "archive's end (2025-12); a forward lockbox is the recorded-chain period "
                 "(ADR-0182)"))
    return doc


def cell_files(configs_dir, cell):
    """Return one cell's full document set: every rung, its zoo and its HPO documents.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory, holding the base rungs and zoo.
    cell : Cell
        The cell.

    Returns
    -------
    dict
        ``grid/<file>`` -> document, for the rungs in :data:`ZOO_RUNGS`, the
        zoo, one HPO document per key of :data:`HPO_SPACES` and, for a cell
        with a backtest, its put-credit-spread document and its condor gate
        document, both carrying :func:`gate_study_document`'s study, and its
        two :func:`select_document` documents (the frontier and the empirical
        rung).
    """
    configs_dir = Path(configs_dir)
    out = {}
    for rung in ZOO_RUNGS:
        out[f"grid/{_cell_file(cell, rung)}"] = grid_document(
            _load(configs_dir / RUNGS[rung]), cell, rung)
    out[f"grid/{cell.name}-zoo.json"] = zoo_document(_load(configs_dir / "run-real-zoo.json"),
                                                     cell)
    for rung in HPO_SPACES:
        out[f"grid/{cell.name}-hpo-{rung}.json"] = hpo_document(
            out[f"grid/{_cell_file(cell, rung)}"], rung)
    if cell.underlying.backtest:
        out[f"grid/{_put_spread_file(cell)}"] = gate_study_document(
            put_spread_document(_load(configs_dir / RUNGS[GRID_RUNG]), cell), name_suffix="")
        out[f"grid/{_gate_file(cell)}"] = gate_study_document(
            out[f"grid/{_cell_file(cell, GRID_RUNG)}"])
        for rung in _SELECT_RUNGS:
            out[f"grid/{_select_file(cell, rung)}"] = select_document(
                out[f"grid/{_cell_file(cell, rung)}"])
    return out


def grid_files(configs_dir):
    """Return everything ``configs/grid/`` ships: every cell's full document set.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory.

    Returns
    -------
    dict
        ``grid/<file>`` -> document, the put-credit-spread, condor gate and
        payoff-selection documents of the backtest cells included.
    """
    configs_dir = Path(configs_dir)
    base = _load(configs_dir / RUNGS[GRID_RUNG])
    out = {f"grid/{_cell_file(cell, GRID_RUNG)}": grid_document(base, cell, GRID_RUNG)
           for cell in CELLS}
    for cell in CELLS:
        out.update(cell_files(configs_dir, cell))
    return out


def write_grid(configs_dir, out_dir=None):
    """Write every grid document as canonical two-space JSON.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory (the bases are read from it).
    out_dir : str or Path, optional
        Where ``grid/`` is written; ``configs_dir`` by default.

    Returns
    -------
    list of str
        The ``grid/<file>`` paths written.
    """
    out_dir = Path(out_dir if out_dir is not None else configs_dir)
    written = []
    for relpath, document in grid_files(configs_dir).items():
        path = out_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written.append(relpath)
    return written

"""The multi-horizon, multi-underlying cell grid and its document generator (ADR-0187).

A cell is one (underlying, days-to-expiry bucket). Its walk-forward document
is an ADR-0182 real rung (``configs/run-real-<rung>.json``) with exactly the
cell's changes applied by :func:`grid_document`: the underlying reader, the
label horizon, the reference-scale multiplier, the folds, the embargo and,
for an underlying with an ex-dividend source, the chain reader and the
archived-quote backtest. ``configs/grid/`` holds what :func:`write_grid`
writes; the config test pins every file to its generator, so a cell document
changes only through this table or its base rung, never by hand.

The worked cell (:data:`WORKED_CELL`) also ships the other rungs, a per-cell
zoo (:func:`zoo_document`, the ADR-0097 protocol over the four rung
documents) and two HPO documents (:func:`hpo_document`, ``hpo-grid`` over a
rung's own knobs under the cell's walk-forward, ADR-0043) — the owner's
model-zoo and hyperparameter-tuning process on one cell. :func:`cell_files`
produces the same set for any other cell on request.
"""

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "BUCKETS",
    "CARRY_RATE",
    "CELLS",
    "GRID_RUNG",
    "HPO_SPACES",
    "RUNGS",
    "UNDERLYINGS",
    "WORKED_CELL",
    "ZOO_RUNGS",
    "Bucket",
    "Cell",
    "Underlying",
    "cell_files",
    "grid_document",
    "grid_files",
    "hpo_document",
    "write_grid",
    "zoo_document",
]

#: Calendar days added to a bucket's upper bound for its embargo: the label's
#: reach (h sessions, at most ceil(1.4 h) + 4 days) and the settlement's reach
#: (dte_max days) both sit inside ``dte_max + 7`` for every bucket; a test pins it.
EMBARGO_MARGIN_DAYS = 7
#: The constant upper bound on the cash rate charged on an assigned short put
#: (ADR-0187, the 2008-2025 maximum; owner question 6).
CARRY_RATE = 0.055
#: The onboarding root the cell documents read, relative to the working directory.
ROOT = "./ob"
#: The archive source: closes with ex-dividend amounts, and the end-of-day chains.
CHAIN_SOURCE = "optionshist-chain"


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
#: The cell that ships its zoo and HPO documents.
WORKED_CELL = "spy-30-45"
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
_ORDER = ("vix", "vix_by_date", "market", "rv", "labels", "fwd", "model", "score", "condor")
#: The proxy backtest knobs the quote backtest takes over unchanged.
_SHARED_BACKTEST_KNOBS = ("split", "short_q", "wing_z", "multiplier", "fee_per_leg",
                          "min_edge_usd", "cvar_alpha")


def _cell_file(cell, rung):
    """Return the file name of one cell's rung document."""
    return f"{cell.name}.json" if rung == GRID_RUNG else f"{cell.name}-{rung}.json"


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
            "uses": "index_options.nodes:CondorQuoteBacktest",
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
        f"the label's reach ({horizon} sessions, at most ceil(1.4 h) + 4 calendar days) and "
        f"the settlement's reach ({bucket.dte_max} days), so no train label or settlement "
        "reaches past the validation cut.")
    return doc


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
    """Return one cell's worked set: every rung, its zoo and its HPO documents.

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
        zoo and one HPO document per key of :data:`HPO_SPACES`.
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
    return out


def grid_files(configs_dir):
    """Return everything ``configs/grid/`` ships: 21 cell documents plus the worked cell's set.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory.

    Returns
    -------
    dict
        ``grid/<file>`` -> document.
    """
    configs_dir = Path(configs_dir)
    base = _load(configs_dir / RUNGS[GRID_RUNG])
    out = {f"grid/{_cell_file(cell, GRID_RUNG)}": grid_document(base, cell, GRID_RUNG)
           for cell in CELLS}
    worked = next(cell for cell in CELLS if cell.name == WORKED_CELL)
    out.update(cell_files(configs_dir, worked))
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

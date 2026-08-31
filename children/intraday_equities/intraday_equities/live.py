"""Paper limit-order loop that reads the shipped documents.

The training document and source configs are the only modelling truth.
This module emits paper intents; real-money enablement is refused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dskit.onboarding import AssetError
from dskit.pipeline.document import load_document

from .nodes import NODE_KINDS, PortfolioSelect, Universe, WindowRows

__all__ = ["intents", "main", "paper_intent"]


def _child_root():
    """Return this package's child root from the file location."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_json(path):
    """Load one JSON object from disk."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def paper_intent(pick, quantity, paper=True):
    """Build one paper limit-order intent.

    Parameters
    ----------
    pick : dict
        ``symbol`` and ``asof_ms`` from the portfolio node.
    quantity : int
        Share count; operational flag only.
    paper : bool
        Must stay true; real-money is not authorized.

    Returns
    -------
    dict
        Order intent.

    Raises
    ------
    AssetError
        If real-money mode is requested or the pick is unusable.
    """
    if not paper:
        raise AssetError(["real-money orders are not authorized by this child"])
    symbol = pick.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise AssetError([f"pick.symbol must be a non-empty string, got {symbol!r}"])
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity < 1
    ):
        raise AssetError([f"quantity must be an int >= 1, got {quantity!r}"])
    return {
        "venue": "paper",
        "side": "buy",
        "type": "limit",
        "symbol": symbol,
        "qty": quantity,
        "asof_ms": pick.get("asof_ms"),
        "tif": "day",
    }


def intents(run_doc, records, source_config=None, quantity=1, paper=True):
    """Score the wired window and emit paper intents from the run document.

    Parameters
    ----------
    run_doc : str
        Path to a shipped pipeline document.
    records : list of dict
        Bar rows already carrying ``symbol``, ``asof_ms``, and prices.
    source_config : str or None
        Optional source-config path; read only, never restated.
    quantity : int
        Operational share count.
    paper : bool
        Paper-only switch.

    Returns
    -------
    list of dict
        Paper intents.

    Raises
    ------
    AssetError
        If the document, source config, or paper gate fails.
    """
    if not paper:
        raise AssetError(["real-money orders are not authorized by this child"])
    if source_config is not None:
        _load_json(source_config)
    document = load_document(run_doc)
    window_spec = document.pipeline.get("window")
    select_spec = document.pipeline.get("select")
    universe_spec = document.pipeline.get("universe")
    if window_spec is None or select_spec is None or universe_spec is None:
        raise AssetError(
            ["run document must declare universe, window, and select nodes"]
        )
    universe = Universe("universe", dict(universe_spec.params)).run(None, {})
    window_params = dict(window_spec.params)
    window_params["lookback"] = universe["lookback"]
    window_params["max_gap_minutes"] = universe["max_gap_minutes"]
    window = WindowRows("window", window_params)
    if NODE_KINDS.get(select_spec.uses) is not PortfolioSelect:
        raise AssetError(
            [f"select node must use the child portfolio kind, got {select_spec.uses!r}"]
        )
    windowed = window.run(None, {"records": records})["records"]
    if not windowed:
        return []
    latest = max(row["asof_ms"] for row in windowed)
    latest_rows = [row for row in windowed if row["asof_ms"] == latest]
    tradable = set(universe["tradable"])
    picks = [
        {"symbol": row["symbol"], "asof_ms": row["asof_ms"]}
        for row in latest_rows
        if row["symbol"] in tradable
    ]
    if not picks:
        return []
    return [paper_intent(picks[0], quantity, paper=paper)]


def main(argv=None):
    """CLI entry for ``python -m intraday_equities.live``.

    Parameters
    ----------
    argv : list of str or None
        Arguments after the module name.

    Returns
    -------
    int
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="intraday_equities.live")
    parser.add_argument(
        "--run-doc",
        default=os.path.join(_child_root(), "configs", "run-train.json"),
    )
    parser.add_argument(
        "--source-config",
        default=os.path.join(_child_root(), "configs", "source-schwab-live.json"),
    )
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--paper", action="store_true", default=True)
    parser.add_argument("--real-money", action="store_true")
    parser.add_argument("--records", help="JSON list of bar rows")
    args = parser.parse_args(argv)
    try:
        if args.records:
            records = json.loads(args.records)
        else:
            records = []
        rows = intents(
            args.run_doc,
            records,
            source_config=args.source_config,
            quantity=args.qty,
            paper=not args.real_money,
        )
        print(json.dumps(rows, indent=2))
    except AssetError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

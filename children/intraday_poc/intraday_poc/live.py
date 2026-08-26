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
3. restore each symbol's LSTM from the run's artifact — sidecar
   verified (state_hash, S2-A) and the module class refused by name on
   mismatch, the pack's own load discipline;
4. decide with the SAME pyomo program the backtest scores
   (:func:`intraday_poc.nodes.build_select_model`, one timestamp);
5. flip the paper position when the pick changes: flatten the loser,
   market-buy the winner (TIF ``day``), through the paper endpoint.

Every iteration appends one JSON line to ``decisions.jsonl`` in
``--log-dir`` — predictions, pick, action taken — so the forward run
leaves evidence the way a pipeline run does.

Usage::

    python -m intraday_poc.live --run-dir <run dir of run-train.json> \
        --symbols AAPL MSFT --qty 1 [--once] [--dry-run]

Credentials come from the environment (``.env`` beside the CWD is read
first, process env winning — the toolkit's ``env.py`` precedence).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time

from .nodes import build_select_model

#: The trainer artifact directory names inside a run dir, per symbol —
#: run-train.json's node keys. A different document layout is a CLI flag
#: away (``--artifact SYMBOL=PATH``), never an edit here.
DEFAULT_ARTIFACTS = {"AAPL": "artifacts/qhat_aapl", "MSFT": "artifacts/qhat_msft"}

_SIP_FIELDS = ("open", "high", "low", "close", "volume")


def load_dotenv(path: str = ".env") -> None:
    """KEY=VALUE lines into ``os.environ`` — process env wins (the
    dotenv convention dskit's ``env.py`` follows)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def restore_model(artifact_dir: str):
    """``model.pt`` + verified ``model.json`` -> (module, features).

    The pack's load discipline, applied outside a pipeline run: the
    sidecar's ``state_hash`` (sha256 over the state bytes, a NUL, then
    the canonical JSON of every other sidecar field) must match, and the
    module class must be this child's LSTM — refused by name otherwise.
    """
    import torch

    from .models import NextBarLSTM

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
    if declared != "intraday_poc.models:NextBarLSTM":
        raise SystemExit(
            f"artifact {artifact_dir}: declares module {declared!r}, not "
            "intraday_poc.models:NextBarLSTM — wrong artifact for this loop"
        )
    module = NextBarLSTM(**params.get("module_params", {}))
    module.load_state_dict(torch.load(state_path, weights_only=True))
    module.eval()
    features = list(params.get("features", []))
    if not features:
        raise SystemExit(f"artifact {artifact_dir}: sidecar carries no "
                         "feature list")
    return module, features


def latest_feature_row(bars, lookback: int, max_gap_minutes: float = 5.0):
    """``[(asof_ms, close)]`` ascending, one symbol -> the newest
    ``ret_lag_*`` vector (``ret_lag_0`` most recent), or ``None`` when
    coverage or gap discipline refuses — the same chain semantics as
    ``WindowRows`` (a gap over ``max_gap_minutes`` breaks the chain;
    nothing is bridged)."""
    import math

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
    """One row in, a float out — ``None`` on missing coverage, the
    TorchSignal contract."""
    import torch

    values = [row.get(name) for name in features]
    if any(v is None for v in values):
        return None
    with torch.no_grad():
        out = module(torch.tensor([values], dtype=torch.float32))
    return float(out.reshape(-1)[0])


def solve_pick(preds: dict) -> str:
    """``{symbol: pred}`` -> the chosen symbol, via the SAME pyomo
    program the backtest's select-one node solves."""
    import pyomo.environ as pyo

    model = build_select_model({0: preds})
    solver = pyo.SolverFactory("appsi_highs")
    solver.solve(model)
    for s in sorted(preds):
        if pyo.value(model.x[0, s]) > 0.5:
            return s
    raise RuntimeError("solver returned no selection — should be impossible "
                       "with a non-empty prediction set")


def fetch_bars(symbols, minutes: int):
    """The last ``minutes`` of 1-minute IEX bars per symbol ->
    ``{symbol: [(asof_ms, close)]}`` ascending."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    client = StockHistoricalDataClient(
        os.environ["APCA_API_KEY_ID"], os.environ["APCA_API_SECRET_KEY"])
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    bars = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=TimeFrame(1, TimeFrameUnit.Minute),
        start=start,
        feed=DataFeed.IEX,
    ))
    out = {}
    for symbol, series in bars.data.items():
        rows = [(int(b.timestamp.timestamp() * 1000), float(b.close))
                for b in series]
        out[symbol] = sorted(rows)
    return out


def current_position(trading, symbol: str) -> float:
    from alpaca.common.exceptions import APIError

    try:
        return float(trading.get_open_position(symbol).qty)
    except APIError:
        return 0.0


def flip_to(trading, winner: str, losers, qty: float, dry_run: bool) -> list:
    """Flatten every non-winner we hold, then hold ``qty`` of the winner.
    Returns the action strings taken (paper endpoint only)."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    actions = []
    for symbol in losers:
        held = current_position(trading, symbol)
        if held > 0:
            actions.append(f"close {symbol} ({held:g})")
            if not dry_run:
                trading.close_position(symbol)
    if current_position(trading, winner) <= 0:
        actions.append(f"buy {qty:g} {winner}")
        if not dry_run:
            trading.submit_order(order_data=MarketOrderRequest(
                symbol=winner, qty=qty, side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            ))
    return actions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m intraday_poc.live", description=__doc__)
    parser.add_argument("--run-dir", required=True,
                        help="run dir of run-train.json (holds the "
                             "per-symbol artifacts)")
    parser.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT"])
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
    args = parser.parse_args(argv)

    load_dotenv()
    for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        if not os.environ.get(name):
            raise SystemExit(f"{name} is not set — fill in .env "
                             "(see .env.example)")

    from alpaca.trading.client import TradingClient

    trading = TradingClient(os.environ["APCA_API_KEY_ID"],
                            os.environ["APCA_API_SECRET_KEY"], paper=True)

    signals = {}
    lookback = None
    for symbol in args.symbols:
        rel = DEFAULT_ARTIFACTS.get(
            symbol, f"artifacts/qhat_{symbol.lower()}")
        module, features = restore_model(os.path.join(args.run_dir, rel))
        signals[symbol] = (module, features)
        if lookback is None:
            lookback = module.lookback
        elif lookback != module.lookback:
            raise SystemExit("artifacts disagree on lookback — retrain")
    print(f"models restored for {list(signals)} (lookback {lookback})")

    log_path = os.path.join(args.log_dir, "decisions.jsonl")
    while True:
        clock = trading.get_clock()
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        record = {"at": now, "is_open": clock.is_open}
        if not clock.is_open:
            record["action"] = "market closed — no decision"
            print(record["action"], f"(next open {clock.next_open})")
        else:
            bars = fetch_bars(args.symbols, args.history_minutes)
            preds = {}
            for symbol, (module, features) in signals.items():
                row = latest_feature_row(bars.get(symbol, []), lookback)
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
                losers = [s for s in args.symbols if s != winner]
                actions = flip_to(trading, winner, losers, args.qty,
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

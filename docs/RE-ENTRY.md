# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `feat/intraday-poc-child` (merged to `main`) · **Tests:**
child 69 pass, 8 skip · **ruff:** clean. The skips are structural —
the child declares no `capital`/`labels`/`train` kinds, so those
conformance blocks have nothing to probe.

**Landed this round: the first real child** — `children/intraday_poc/`,
the two-stock intraday PoC and the reference module set every future
child copies (`connectors / nodes / models / live / testing` + `configs`
+ `tests`).

- **Vendor settled by research: Alpaca for both legs, $0.** Free-tier
  **SIP** 1-minute bars back to 2016 cover the backhistory (the
  IEX-only restriction is *streaming*-only; the sole gate is `end` ≥15
  min old), and a free **paper** account gives real-time IEX bars *and*
  order placement through the same keys and the same SDK. Every
  alternative fails a leg: yfinance caps 1-min at 30 days, Finnhub
  paywalls candles, Tiingo is IEX-only with a ~2000-point cap, Polygon
  free is 2 years at 5 req/min.
- **One connector, two sources.** `AlpacaBarsConnector` (four verbs)
  runs `--mode backfill` (`feed=sip`, `end` clamped 16 min back) and
  `--mode live` (`feed=iex`); the platform keys cursors per
  (source, stream, mode), so the two never fight.
- **`nodes.py`** — `bars` / `window` / `forecast` / `select-one`. The
  selector is role `score` on the PyomoSolve doorway: pick exactly one
  symbol, maximize predicted Δ. **`models.py`** — `NextBarLSTM`,
  run-path only (the one sanctioned torch-at-top module).
- **`live.py`** — clock gate → IEX bars → restore the verified
  artifact → the *same* pyomo pick → paper order. `paper=True` is
  hard-wired.
- `.gitignore` now bans `.env` and the child run roots (`ob/`,
  `asset_store/`, `decisions.jsonl`).
- **dskit itself is untouched** — ADR-0021 held; no upstream ADR needed.

**Verified how far: stubs only.** `StubBarsConnector` drives the
four-verb contract, acquire→validate e2e, the domain math, and
train→live e2e with no network. **Nothing has touched real Alpaca** —
no `.env`, no `ob/`, no `asset_store/`, no backfill, no backtest run.

**Awaiting the user (blocking, manual):**
1. Sign up at https://app.alpaca.markets/signup → **Paper Trading** →
   generate keys → `cp children/intraday_poc/.env.example .env` and
   paste the pair. Credentials are the owner's to handle, not the agent's.
2. Confirm go-live intent before `live.py` runs without `--dry-run` —
   it places paper orders.

**Next session — the real-data acceptance run,** in order: backfill
acquire → validate → certify → publish → `walkforward run-backtest.json`
→ `run run-train.json` → `live.py --dry-run` before any `--qty`. Two
known frictions to watch: IEX minutes with no trades emit **no bar**
(mask, never bridge), and IEX-vs-SIP divergence of 5–50 bps means paper
PnL validates **signal direction, not fill realism**.

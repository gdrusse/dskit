# intraday_poc — a dskit child

The two-stock intraday proof of concept, and the worked example of the
child pattern: **AAPL and MSFT 1-minute bars from Alpaca, one LSTM per
symbol predicting the next bar's return, and a pyomo program that picks
exactly one symbol per minute** — backtested walk-forward, then run
forward against Alpaca paper trading with the same modelling core and the
same selection program. One declared skew: the backtest selects each
fold's best epoch by validation loss (`monitor`), while the production
fit ships its last epoch — the run documents' notes carry the
consequence.

A child consumes dskit, never modifies it: tier-3 code plus JSON configs
over the three seams — a connector (onboarding), registered node kinds
(pipeline), its own asset model (assets). The domain lives in `configs/`.

```
intraday_poc/
├── README.md                  # this file
├── CLAUDE.md                  # agent orientation
├── pyproject.toml             # dskit + alpaca-py, torch, pyomo, highspy
├── .env.example               # Alpaca paper key pair — copy to .env, fill in
├── intraday_poc/
│   ├── __init__.py            # import = registration of the node kinds
│   ├── connectors.py          # AlpacaBarsConnector (four verbs; sip/iex knobs)
│   ├── nodes.py               # bars / window / forecast / select-one kinds
│   ├── models.py              # NextBarLSTM (run-path only — torch at top)
│   ├── live.py                # the forward loop: predict → pick → paper order
│   └── testing.py             # StubBarsConnector — the connector minus the network
├── configs/
│   ├── source-backfill.json   # the ONE source config: SIP 1-min bars, both modes
│   ├── suite-bars.json        # validation over the bars stream
│   ├── run-backtest.json      # the walk-forward backtest document
│   ├── run-train.json         # the production fit the live loop restores
│   └── asset-model.json       # the child's catalog kinds
└── tests/
    ├── conftest.py            # sys.path bootstrap — in-repo and after graduation
    ├── test_connectors.py     # four-verb contract + acquire→validate e2e (stubbed)
    ├── test_nodes.py          # conformance + domain math + train→live e2e
    └── test_configs.py        # every config validates against its engine
```

## One-time setup

1. Sign up at https://app.alpaca.markets (free; paper needs no funding).
   Switch to **Paper Trading** → API Keys → Generate. The secret shows once.
2. `cp .env.example .env` and fill in the pair. `.env` is gitignored;
   configs only ever carry env-var *names*.
3. `pip install -e .` from this directory (pulls dskit + the run-path deps).

## The pulls (backhistory, then forward)

```bash
export $(grep -v '^#' .env | xargs)      # or source it however you prefer

python -m dskit.onboarding init --root ./ob
python -m dskit.onboarding register-source alpaca --catalog-source alpaca-src \
    --connector intraday_poc.connectors:AlpacaBarsConnector \
    --config @configs/source-backfill.json --activate
python -m dskit.onboarding acquire --root ./ob --source alpaca --stream bars --mode backfill
python -m dskit.onboarding validate --root ./ob --suite configs/suite-bars.json --snapshot <vid>
python -m dskit.onboarding certify --root ./ob --result <vid> --decision certified --by you
python -m dskit.onboarding publish --root ./ob --dataset alpaca-bars --certification <vid>

# the forward top-up: SAME source, SAME config, the other mode
python -m dskit.onboarding acquire --root ./ob --source alpaca --stream bars --mode live
```

**One source name, two modes.** The platform keys cursors per (source,
stream, mode), so backfill and live never fight — which is exactly why a
*second source* would be wrong here: observations land in
`observations/<source>/` and a run document reads one source, so bars
acquired under another name are invisible to the model.

The two modes differ in exactly one place: **where a pull with no cursor
starts.** Backfill starts at the config's `start` — all the history
there is. The live cursor is empty until its own first pull, so live
starts no further back than `live_lookback_minutes` (1440 — a day);
windowing it from `start` would re-fetch every bar since 2021 and write
them as a second full acquisition, so every later scan would carry two
copies of the whole store. The trade-off is the seam: bars older than
that window and newer than the backfill's cursor belong to NO pull, so
widen the knob or let the backfill catch up to within it before
switching live on. After that first pull the cursor carries it, and each
pull takes only what is new.

On the free tier this pull trails the tape by ~16 minutes, the SIP gate
the connector clamps for; the forward *loop* below does its own
real-time IEX fetch and does not wait for it.

## How the pulled data reaches the model

The two halves never import each other — they meet at a **directory
path**. `register-source` only files a record saying which class to
import; `acquire` writes rows to `observations/<source>/`; the pipeline
reads that same folder back. The only thing binding them is the pair of
strings `root` + `source` in the run document.

```
  register-source ──►  registry record: source_config          __main__.py:92
                       {name, connector, config}, draft→active
                       "pkg.module:Class" imported on demand   connector.py:291
                                  │
  acquire ─────────►  run_acquisition                          acquire.py:86
                       ├─ raw/<source>/<acq>/          WORM evidence
                       ├─ observations/<source>/<acq>/bars.jsonl
                       └─ state/<source>/bars-<mode>.json   cursor, written LAST
                                  │
              ┌───────────────────┴───────────────────┐
              │ MODELLING                             │ GOVERNANCE
              ▼                                       ▼
   scan_stream(root, source, …)          validate → certify → publish
     observations.py:214                   published/<dataset>/*.json
     os.path.join(root,"observations",source)          │      publish.py:51
              ▼                                        ▼
   BarsFromStore          nodes.py:110        sync_published  assets/sync.py
              ▼                                   (the catalog)
   window → DeclaredTrain → select-one
     run-train.json / run-backtest.json
```

**Certify and publish are NOT on the modelling path.** Nothing under
`dskit/pipeline` reads `published/` — that branch feeds the asset
catalog. The pipeline reads acquired observations directly, certified or
not. Publish for governance, not to unblock a run.

**`"alpaca"` is an unpinned string.** It is chosen at `register-source`,
becomes the folder name, and must then be retyped in every run
document's `bars` node. A typo REFUSES: `scan_stream` raises
`AssetError` naming the missing `observations/<source>/` directory
(`observations.py`), so a mistyped source can never quietly train a
model on nothing.

## Backtest, fit, go forward

```bash
# walk-forward backtest: 3 expanding folds, realized pick-return objective
python -m dskit.pipeline walkforward configs/run-backtest.json --asof 2026-08-25 --adapter intraday_poc

# production fit over everything published
python -m dskit.pipeline run configs/run-train.json --asof 2026-08-25 --adapter intraday_poc

# the forward loop (paper account only; --dry-run to decide without orders)
python -m intraday_poc.live --run-dir <run dir printed above> \
    --source-config configs/source-backfill.json --qty 1
```

Every minute the live loop: gates on the exchange clock, pulls the
latest IEX bars, restores each model through its hash-verified sidecar,
solves the SAME pyomo program the backtest scored, and flips the paper
position to the winner. Decisions land in `decisions.jsonl`.

**The loop declares nothing twice.** The price field, the gap bound and
the model class come from `<run-dir>/config.json` — the whole training
document, which the driver writes; the adjustment **and the symbol
universe** come from the source config the puller registered
(`--source-config`), so adding a ticker there and retraining is all it
takes. Only operational flags live on the CLI: `--qty`, `--log-dir`,
`--once`, `--dry-run`, `--history-minutes`, and `--artifact SYMBOL=PATH`
when a document names its trainer nodes something other than
`qhat_<symbol>`. There is no third config file, by doctrine: it would
duplicate both.

## What to know before trusting the numbers

- **The store is full SIP, in both modes** (free tier serves consolidated
  history; only the last 15 minutes are gated — the connector clamps for
  it). Training and backtesting therefore see one homogeneous series.
- **The forward loop's own fetch is IEX-only** (~2.5% of volume), because
  real-time SIP is not sold on the free tier: a minute with no IEX trade
  has no bar, and IEX prints can sit 5–50 bps off consolidated NBBO. That
  is the child's ONE declared train/serve vendor *divergence*
  (`LIVE_FEED` in `live.py`); every other vendor knob the loop uses comes
  from the source config. Paper fills simulate against this — treat
  forward PnL as signal validation, not execution realism.
- **The bar interval is a constant, not yet a knob** (`BAR_INTERVAL` in
  `connectors.py`): one minute, on both sides. The connector's pull and
  the loop's fetch both build their vendor `TimeFrame` from it, so they
  cannot drift; making it configurable (`timeframe` on `spec()`) is an
  open TODO, and would also have to move the loop's minute cadence and
  the window node's gap bound.
- Windows never bridge gaps (`max_gap_minutes`); rows the model cannot
  cover are skipped, never imputed.

## Tests

```bash
python -m pytest tests -q            # from the child root
```

Heavy deps gate their own tests: on a bare dskit install the suite still
passes (torch/pyomo tests skip); with the child's deps installed the
train→artifact→live-restore→select chain proves itself end to end.

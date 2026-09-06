# intraday_poc — a dskit child

The two-stock intraday proof of concept, and the worked example of the
child pattern: **AAPL and MSFT 1-minute bars from Alpaca, one LSTM per
symbol predicting the next bar's return, and a pyomo program that picks
exactly one symbol per minute** — backtested walk-forward, then run
forward against Alpaca paper trading with the same modelling core and the
same selection program. The two documents now declare the SAME trainer,
`monitor` included: both select the shipped epoch on validation loss
(ADR-0035). They do **not** monitor on comparable bands, and that is the
one skew left: `run-train.json` opens a third band for it (`mon_rows`,
read by nothing else), while `run-backtest.json` monitors on the very
val window it also forecasts and scores — so read its `total_realized`
as an **upper bound** (see "What to know before trusting the numbers").

A child consumes dskit, never modifies it: tier-3 code plus JSON configs
over the three seams — a connector (onboarding), registered node kinds
(pipeline), its own asset model (assets). The domain lives in `configs/`.
The action ledger and the process are
[`docs/decisioning/README.md`](docs/decisioning/README.md). Its generated
README displays the complete human-owner-only Path and latest 10 Actions;
CSV history is never deleted. Each Path record has an ID, label, purpose,
relevant files, and `LOCKED` (`Y`/`N`); Current Work is human-owner-only.
Pipeline/onboarding commands record themselves; research uses
`python -m dskit.journal research --topic T --name N`.

```
intraday_poc/
├── README.md                  # this file
├── CLAUDE.md                  # agent orientation
├── pyproject.toml             # dskit + alpaca-py, torch, pyomo, highspy, mlflow
├── .env.example               # Alpaca paper key pair — copy to .env, fill in
├── intraday_poc/
│   ├── __init__.py            # import = registration of the node kinds
│   ├── connectors.py          # thin policy wrapper over dskit's Alpaca pack
│   ├── nodes.py               # bars / window / forecast / select-one kinds
│   ├── models.py              # empty bespoke seam (zoo ships standard nets)
│   ├── live.py                # the forward loop: predict → pick → paper order
│   └── testing.py             # StubBarsConnector — the connector minus the network
├── configs/
│   ├── source-backfill.json   # the ONE source config: SIP 1-min bars, both modes
│   ├── suite-bars.json        # validation over the bars stream
│   ├── run-backtest.json      # the walk-forward backtest document
│   ├── run-train.json         # the TUNED production fit the live loop restores
│   └── asset-model.json       # the child's catalog kinds
├── journal.json              # dskit.journal marker (ADR-0056)
├── docs/decisioning/         # generated process + action ledger
├── docs/explanations/        # durable explanations; use record-explanation
├── docs/memos/               # decision memos; use memo
├── docs/research/            # topic folders; <date>-synthesis.md + dated notes
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
real-time IEX fetch and does not wait for it. That clamp also bounds the
knob: on `sip` a `live_lookback_minutes` of 16 or less would window from
`now - lookback` to `now - 16min` — an empty window, every pull, with
nothing acquired and nothing said — so the connector refuses that
combination outright.

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
   window → torch-ts-train (arch:lstm) → select-one
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

# production fit, hidden_size chosen by a 9-trial grid on a held-out tail
python -m dskit.pipeline run configs/run-train.json --asof 2026-08-28 --adapter intraday_poc

# what each RUN shipped, every run beside each other
python -m dskit.pipeline runs --metric select.metrics.total_realized

# the forward loop (paper account only; --dry-run to decide without orders)
python -m intraday_poc.live --run-dir <run dir printed above> \
    --source-config configs/source-backfill.json --qty 1
```

No `--artifact` flags: `run-train.json`'s trainers are `foreach`
instances, so their node keys carry the fan-out's double underscore
(`qhat__aapl`), and the loop finds them by reading the run's own document
rather than by any convention of its own. The flag remains the hatch for
serving a directory this run did not write.

The minute is decided by the run's own `select` node, rebuilt from the
document and run one timestamp wide — so the live pick, the backtest's
folds and the search's trials are solved by one program, under one
solver, with one set of options. The loop solves a throwaway minute
before it touches the trading client: a solver the document names and
this machine lacks is then a refusal you read at startup, not an
exception on top of an open position.

The fit **spans `[2026-01-01, splits.train_end_ms]`** rather than every
published bar, for two different reasons that both belong in the open:
the **start** is a deliberate fit-window bound (final-loss used to be
one unbatched forward — ADR-0045 batches it via `loader.eval_batch_size`,
so the bound can be widened when wanted), and the
**end** holds out what grades the search — a search whose objective
scores rows its trials trained on grades memorization. Three disjoint
bands follow from that: the fit, then the band that selects each trial's
checkpoint (`mon_rows`, the two weeks the splits leave between
`train_end_ms` and `val_start_ms`), then the selection window the
objective scores.

Where the numbers live: the winner is `nodes/NN-search.json` in the run
dir and the **nine per-trial scores are in `carry.json`** (the report and
the node record summarize the trial list, they do not enumerate it). The
mlflow sink (`tracking` — a local `sqlite:///mlruns.db`, no server)
carries one entry per RUN, and its two halves come from different passes:
**params are the DECLARED ones** (logged before the search runs) while
**metrics are the final pass's**. So a searched run whose winner differs
from its declaration shows `hidden_size 32` beside the score of the model
it shipped at 64 — which is exactly what this machine's tuned run
recorded — so compare architectures through the run dirs, not through the
sink. Trials never reach a sink at all: the driver silences the tracker
while it re-executes them.

The grid CROSSES the two symbols (nine trials, six of them asymmetric),
so a winner may pair 16 with 64. The live loop refuses such a pair rather
than trading on it, and recovering is a config edit, not a re-run: the
grid is enumerated and `loader.seed` pins every fit, so re-running
reproduces the same winner. Take a symmetric trial from `carry.json`, put
its width on the `foreach` template's `arch_params.lstm` (one edit, both
symbols) and on `run-backtest.json`'s twin pair, narrow the space, re-run.
Per-symbol widths cannot be declared beside the template they fan from at
all — that needs hand-expanding the fan-out.

Every minute the live loop: gates on the exchange clock, pulls the
latest IEX bars, restores each model through its hash-verified sidecar,
solves the SAME pyomo program the backtest scored, and flips the paper
position to the winner. Decisions land in `decisions.jsonl`.

**The loop declares nothing twice.** The price field, the gap bound and
the trainer identity come from `<run-dir>/config.json` — the whole training
document, which the driver writes; the adjustment, the symbol
**universe** and the credential env-var **names**
(`key_env`/`secret_env`) come from the source config the puller
registered (`--source-config`), so adding a ticker there and retraining
is all it takes.

Credentials: both sides read the same env-var **names** from that config
and share ONE rule for what counts as a credential
(`connectors.resolve_credentials` — a var set to `""` is refused by
name, never authenticated), but they do **not** read from the same
place. The puller reads the **process environment only**. The loop also
reads `.env` beside the CWD, through dskit's own `env.py` (process
environment winning, `export ` and quotes per its documented format);
it parses no dotenv of its own. So a key pair that lives only in `.env`
serves the forward loop while `acquire` refuses it by name — **export
the pair** (`set -a; . ./.env; set +a`) and both are served. Only
operational flags live on the CLI: `--qty`, `--log-dir`, `--once`,
`--dry-run`, `--history-minutes`, and `--artifact SYMBOL=PATH` when a
symbol's model should come from a directory this run did not write (an
override for a symbol the config does not declare is refused, never
ignored; so is a symbol the run trained no model for, naming the trainer
keys it did write). There is no third config file, by doctrine: it would
duplicate both.

## What to know before trusting the numbers

- **The walk-forward's `total_realized` is an UPPER BOUND, not a clean
  out-of-sample score.** In `run-backtest.json` each fold's `aapl_val` /
  `msft_val` feeds three consumers: the trainer's `val_rows` (which
  selects the epoch that ships), the forecaster's records, and `labeled`
  (which realizes every pick). One band therefore both picks the
  checkpoint and grades it, an optimism the objective does not correct —
  the document's own notes say so, and the fix (a third band) is what
  `run-train.json` carries. So the headline fold numbers are a ceiling on
  what a fold would have earned, not an estimate of it. The tuned fit's
  search score does not share the skew: it monitors on `mon_rows` and is
  scored on the selection window after it.
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
- **The bar interval is a `timeframe` knob** on the connector spec
  (default `BAR_INTERVAL`, Minute-only for this PoC). The connector's
  pull and the loop's fetch both build their vendor `TimeFrame` from
  the resolved pair, and the loop's wake cadence follows the amount.
  Changing the amount without wipe+re-backfill leaves training on the
  old series and live on the new one; retune `max_gap_minutes` with it.
- Windows never bridge gaps (`max_gap_minutes`); rows the model cannot
  cover are skipped, never imputed. Training and serving build them with
  the SAME node — the loop calls `latest_rows` on the window node it
  rebuilds from the run's own document (ADR-0040), and builds the records
  it hands over from that node's own `group_field()` / `order_field()` /
  `price_field()`, so no retuned field name can leave the loop pricing on
  the old series or keying on a name the node no longer reads.

## Tests

```bash
python -m pytest tests -q            # from the child root
```

Heavy deps gate their own tests: on a bare dskit install the suite still
passes (torch/pyomo tests skip); with the child's deps installed the
train→artifact→live-restore→select chain proves itself end to end.

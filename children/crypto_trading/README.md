# crypto_trading — a dskit child

Research child for **crypto trading opportunities**. Status: **stage A (data
pulls) is configured and tested offline, not yet pulled.** The target is a
calibrated short-horizon BTC/ETH distribution at each Kalshi crypto contract's
settlement (CF Benchmarks RTI, 60-second average), used to quote as maker.
Stage B (features, models) is not started, and the focus areas in
`docs/research/` still await owner ratification.

| Source (`configs/source-*.json`) | Pulls | Suite (`configs/suite-*.json`) |
|---|---|---|
| `kalshi-crypto` | KXBTC, KXBTCD, KXBTC15M, KXETH, KXETHD, KXETH15M: settled `markets`, `fee_schedules` | `kalshi-crypto-{markets,fees}` |
| `kalshi-crypto-candles-{btc,eth}` | 1-minute `candles`, one 15-minute series each | `kalshi-crypto-candles` |
| `kalshi-crypto-books-{15m,hourly}` | live `orderbooks` recorders (`captured_at` is a lower bound) | `kalshi-crypto-books` |
| `binance-{btcusdt,ethusdt}-1m` | Binance Vision spot 1-minute klines, daily zips to parquet | `binance-files` |
| `binance-{btc,eth}bvol` | Binance BVOL implied-vol index, daily zips to parquet (26 known missing days) | `binance-files` |

Existing packs only (`kalshi`, `httpblobs`); the child adds the zip-to-parquet
transform `crypto_trading/binance_vision.py` (interim, see ADR-0239). Binance Vision
is CC BY-NC-SA: research use only, not for live trading features. Exact commands:
`docs/plans/2026-10-06-wsl-data-pull-runbook.md`.

**Not in stage A.** The realised settlement value (the model's label), hourly-ladder
candles and Kalshi history before 2026-08-07 need PROPOSED ADR-0236; Coinbase, Deribit and
Kraken need ADR-0237; digest verification ADR-0238. They are dskit changes, not child code.

A child consumes dskit, never modifies it: tier-3 code plus JSON configs
over the three seams — a connector (onboarding), registered node kinds
(pipeline), its own asset model (assets). The domain lives in `configs/`.

## Running a model, end to end

Every command runs from the child's own root and none references where
the child lives. Steps 2–4 get data in; step 5 models it.

```bash
python -m pytest tests -q                        # 0. the suite (works uninstalled)
pip install -e .                                 # 1. install, once

python -m dskit.onboarding init --root ./ob      # 2. the onboarding root
python -m dskit.onboarding register-source mysource --root ./ob \
    --catalog-source mysource-src \
    --connector crypto_trading.connectors:SampleConnector \
    --config @configs/source-sample.json --activate

python -m dskit.onboarding acquire --root ./ob \  # 3. pull history, then check it
    --source mysource --stream samples --mode backfill
python -m dskit.onboarding validate --root ./ob \
    --suite configs/suite-sample.json --snapshot <snapshot-vid>

python -m dskit.onboarding certify --root ./ob \  # 4. OPTIONAL — governance only
    --result <result-vid> --decision certified --by you
python -m dskit.onboarding publish --root ./ob \
    --dataset mydata --certification <cert-vid>

python -m dskit.pipeline run configs/run-sample.json \   # 5. fit / score
    --asof 2026-01-01 --adapter crypto_trading
# execute rows land in docs/decisioning/actions.csv automatically

python -m dskit.journal research "a question" --topic a-question  # 5b. research note
python -m dskit.journal promote A0001 --criteria empirical  # owner path

python -m dskit.assets init --store ./crypto_trading_store \  # 6. OPTIONAL — a
    --model configs/asset-model.json                       #    governed catalog
```

Each command prints the `version_id` the next one wants.

**`--root` is on every onboarding command and defaults to
`./onboarding_root`.** Omit it after `init --root ./ob` and you register
into a second, empty root — the most common way this goes wrong.

**Steps 4 and 6 are not on the modelling path.** `acquire` writes
`observations/<source>/`, which the pipeline's data node reads back;
`publish` writes `published/<dataset>/`, read only by the assets
catalog. A run never waits on a certification.

**`--adapter crypto_trading` is just an import** — importing the package
registers its node kinds, which is what makes `crypto_trading-*` in the
document resolve.

**Going live:** register a SECOND source over the SAME connector class
with different knobs, then `acquire --mode live` on a cadence.
Checkpoints are keyed per (source, stream, mode), so the backfill and
live cursors never fight — two configs, never two classes.

**Walk-forward:** a document carrying a `walkforward` section runs with
`walkforward` in place of `run`.

Exit codes: `0` ran · `3` halted at a gate (a halt is a result) · `1`
error.

**Journal.** Every acquire / research / execute / production lands in
`docs/decisioning/`. Open that README for the process (it is generated
from CSV). Acquire and execute record themselves; research is
`python -m dskit.journal research`; production wraps `live.main`.
Path to production is owner `journal promote`.

**Memos.** Put durable implementation handoffs, completed-study evidence, and
operational caveats in `docs/memos/`. They are ordinary reviewed documents,
not ADRs and not journaled research. The skeleton keeps the folder present via
`.gitkeep`, so every copied child starts with it.

**Models.** Fitted artifacts that outlive a run directory — ML weights,
optimization solutions, serialized transforms — live in `models/`, gitignored:
reproducibility is the run document's identity hash, never a committed binary.
Run-scoped outputs stay in the run directory; a file belongs in `models/` only
when a later config or the serving loop must reload it by stable name.

**Plans.** Project-specific plan builds — the child's phased work plans and
closeout checklists — live in `docs/plans/`. The dskit repo's `docs/plans/` is
repo-level; this folder is the child's own, and it travels with graduation.

> The skeleton's sample data node is self-contained, so step 5 works
> before step 2. A real child's data node reads the store, so there the
> order is real.

**Worked instance:** `children/intraday_poc` is exactly this shape —
Alpaca bars (SIP for the backfill, IEX for the live pull), an LSTM per
symbol, and a forward loop that restores the trained artifacts and
trades paper.

> **Read it as a shape, not as a model of good code.** A 2026-08-27 audit
> found ~20 defects in it, six of them silent-wrong-behavior — including a
> live loop that re-implements its own training transform and has drifted
> from it. They are listed in the repo's `TODO.md`. Copy its *structure*;
> check `TODO.md` before copying any of its *code*.

## Serving it forward

A run that scored well is not yet a process. `configs/serve-sample.json`
declares one: which run to serve, where live rows enter it, which node keys
are the decision heads, and what must be true before anything is sent.

```bash
python -m dskit.pipeline run configs/run-sample.json --asof 2026-01-01 --adapter crypto_trading
# point serve-sample.json's serving.run_dir at the directory that wrote, then:
python -m dskit.production validate configs/serve-sample.json
python -m dskit.production plan     configs/serve-sample.json
python -m dskit.production serve    configs/serve-sample.json --once
```

The sample serves at **shadow**: it decides for real and sends nothing. That
is where every child starts, and promotion is a change to `rung` plus the
tier-3 seams the higher rungs require — never a flag.

Four templates are shipped fail-closed, and `tests/test_production.py` proves
they stay that way: `execution.py` (the venue), `accounting.py` (its books),
`approvals.py` (the trust root behind a maker-checker arm) and
`coordination.py` (a fenced lease). Each refuses until you implement it, so
copying this skeleton can never move money by accident.

`nodes.py` also shows the serving side of the node contract: `serving_effect`
answers the closed API the serving policy classifies every node with — the
source says `entry_read` because it is the one mutable read a tick may take,
the transform says `pure` — and `serving_contract` describes the source so a
tick can freeze and digest its rows. The default is `forbidden`, so a class
that stays silent can never appear in a served graph.

## Layout

```
crypto_trading/
├── pyproject.toml         # dependencies = ["dskit"]; extra `parquet` = pyarrow
├── README.md / CLAUDE.md  # this file; agent orientation
├── crypto_trading/           # tier-3 code; import = registration
│   ├── __init__.py        # curated re-exports
│   ├── binance_vision.py  # httpblobs transform: Binance daily zip-CSV to parquet
│   ├── connectors.py      # onboarding seam: the vendor pull (four verbs)
│   ├── nodes.py           # pipeline seam: node kinds, default-deny params
│   ├── execution.py       # production seam: the venue executor (fail-closed)
│   ├── accounting.py      # production seam: live books (fail-closed)
│   ├── approvals.py       # production seam: the trust root (fail-closed)
│   └── coordination.py    # production seam: a fenced lease (fail-closed)
├── configs/               # the domain, as self-documenting JSON
│   ├── asset-model.json   # the child's catalog kinds
│   ├── source-sample.json # a connector config object
│   ├── suite-sample.json  # a validation suite
│   ├── run-sample.json    # a pipeline document
│   ├── serve-sample.json  # a serve document — the run, served forward
│   ├── source-kalshi-crypto.json         # stage A: six crypto series, markets + fees
│   ├── source-kalshi-crypto-candles-{btc,eth}.json # stage A: 1-minute candles, one 15m series each
│   ├── source-kalshi-crypto-books-{15m,hourly}.json # stage A: live orderbook recorders
│   ├── source-binance-{btcusdt,ethusdt}-1m.json # stage A: spot klines
│   ├── source-binance-{btc,eth}bvol.json # stage A: BVOL implied-vol index
│   ├── binance_vision_dates.json         # the pinned day list the Binance sources pull
│   ├── suite-kalshi-crypto-{markets,candles,fees,books}.json # one suite per stream
│   └── suite-binance-files.json          # the httpblobs inventory gate
├── models/                # fitted ML/optimization artifacts (gitignored)
│   ├── README.md          # what belongs here vs the run directory
│   ├── .gitignore         # artifacts are rebuilt, never committed
│   └── .gitkeep
├── docs/decisioning/      # generated grid (CSV store; ADR-0056)
│   ├── README.md          # GENERATED — do not edit
│   ├── actions.csv
│   └── path.csv
├── docs/explanations/     # child-specific tutorials and walkthroughs
│   └── README.md          # use record-explanation
├── docs/memos/            # durable implementation and operational handoffs
│   ├── README.md          # use memo
│   └── .gitkeep
├── docs/plans/            # project-specific plan builds
│   ├── README.md          # the child's own phased work plans
│   ├── 2026-10-06-wsl-data-pull-runbook.md # stage A: exact commands, in order
│   └── .gitkeep
├── docs/research/         # research agent markdown
│   ├── README.md          # use record-research
│   └── .gitkeep
├── journal.json           # walk-up marker for dskit.journal
└── tests/                 # green in-repo AND after graduation, uninstalled
    ├── conftest.py        # sys.path bootstrap (position-independent)
    ├── test_binance_vision.py # the transform on tiny in-test zips + httpblobs e2e
    ├── test_configs.py    # every config validates against its engine; pins
    ├── test_connectors.py # four-verb contract + acquire→validate e2e
    ├── test_kalshi_crypto.py # kalshi sources + suites against a scripted venue
    ├── test_execution.py  # the venue executor's shape; the battery, one line away
    ├── test_nodes.py      # conformance suite + a document e2e
    └── test_production.py # the serve document validates; every template fail-closed
```

Graduation is a directory move — nothing here references its incubation
position (ADR-0021; see `children/README.md` in the dskit repo).

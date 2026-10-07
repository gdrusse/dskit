# crypto_trading — a dskit child

Research child for **crypto trading opportunities**. Status: **stage A (data
pulls) and stage B (feature table and kill test) are configured and tested offline
on synthetic stores; nothing has been pulled or run on real data.** The target is a
calibrated short-horizon BTC/ETH distribution at each Kalshi crypto contract's
settlement (CF Benchmarks RTI, 60-second average), used to quote as maker. Stage B
asks the first falsification question (does a simple realised-vol fair value beat the
market mid after fees?); models come only if it survives. The focus areas in
`docs/research/` still await owner ratification.

| Source (`configs/source-*.json`) | Pulls | Suite (`configs/suite-*.json`) |
|---|---|---|
| `kalshi-crypto` | KXBTC, KXBTCD, KXBTC15M, KXETH, KXETHD, KXETH15M: settled `markets`, `fee_schedules` | `kalshi-crypto-{markets,fees}` |
| `kalshi-crypto-candles-{btc,eth}` | 1-minute `candles`, one 15-minute series each | `kalshi-crypto-candles` |
| `kalshi-crypto-books-{15m,hourly}` | live `orderbooks` recorders (`captured_at` is a lower bound) | `kalshi-crypto-books` |
| `binance-{btcusdt,ethusdt}-1m` | Binance Vision spot 1-minute klines, daily zips to parquet (dskit's `zipcsv` transform, layout in `transform_params`) | `binance-files` |
| `binance-{btc,eth}bvol` | Binance BVOL implied-vol index, daily zips to parquet (26 known missing days) | `binance-files` |

Existing packs (`kalshi`, `httpblobs`) and dskit's `zipcsv` transform (ADR-0239): the Binance column layouts are
each source's `transform_params`, so the pull needs no child code. Binance Vision
is CC BY-NC-SA: research use only, not for live trading features. Exact commands:
`docs/plans/2026-10-06-wsl-data-pull-runbook.md`.

**Not in stage A.** The realised settlement value (the model's label), hourly-ladder
candles and Kalshi history before 2026-08-07 need PROPOSED ADR-0236; Coinbase, Deribit and
Kraken need ADR-0237. Each is a NEW dskit pack (existing packs stay untouched), not child code.
Digest verification stays a manual spot check in the runbook (child-side; ADR-0238 withdrawn).

## Stage B: features and the kill test

`configs/run-features-15m.json` (one pipeline document, `notes` on every node) reads the stage A
sources and writes one row per settled 15-minute market and declared lead (default 2, 5, 10
minutes before the close). Hourly series plug in by config once ADR-0236 supplies their candles.

| Node (`crypto_trading.<module>:<Class>`, or the dskit path) | Job |
|---|---|
| `kalshi_rows:MarketRows`, `CandleRows`, `FeeRows` | the `kalshi` pack's streams in the child's vocabulary: label 1/0, payoff geometry, strikes, epoch-ms instants; unsettled and TBD-strike markets dropped by name; candle end seconds to ms |
| `decisions:DecisionRows` | market x lead: information instant I = close - lead (strictly after the strike is known), fill at I + `exec_lag_s` |
| `anchors:StrikeAnchors` | the up/down strikes as observations of the settlement index (the strike IS the index's 60-second average at the open) |
| `dskit.pipeline.libs.parquet_series:StreamManifests` | puts the Binance streams into the run identity (ADR-0240) |
| `spot_features:SpotFeatures` | spot, the index-over-Binance basis from the latest anchor, ln(K/spot_brti), rolling-RMS / EWMA / high-low vol (dskit's variance estimators, ADR-0241, square-rooted here) and BVOL, all strictly before the decision |
| `market_state:MarketState` | yes bid / ask / mid / spread / volume / open interest from the candle that ended by I (the spot bar's minute) |
| `dskit.pipeline.binary_pricing:BinaryFairValue` | driftless lognormal P(YES) on the index-unit spot, with the 60-second settlement-average variance, priced from the fill (ADR-0243) |
| `fees:FeeColumns` | Kalshi taker fee per contract from the `fee_schedules` stream: the Kalshi fee-type mapping (`fee_types`: base rate and mechanic) over dskit's `fee_mechanics` (ADR-0242), rounded per order |
| `dskit.pipeline.binary_scoring:BucketedBinaryScore` | Brier and log-loss of each fair value vs the mid by bucket, lead and segment (cut on the close, epoch-ms bounds), calibration in the large, day-block cluster-robust errors beside per-event ones, plus the after-fee profit of a naive take rule; reports development only until `report_segments` is edited |

The table is written (dskit's `RecordsWriteRun`, ADR-0244) as JSON lines, one file and one stream per run
(`decision_features-<run>`, rows carry `run_id`), and published back as source `features-15m` through
`configs/source-features-15m.json` (`localtables`); commands are in the runbook, stage B.
The generic parts are dskit's (ADR-0239 to 0244, named in the table); this child keeps the Kalshi mapping, the row
vocabulary and the readers. The move from the child's interim copies changed no number:
`tests/test_migration_golden.py` pins every pre-migration row, column and score bit for bit (the identity hash and
the Binance declaration digests moved by design; the runbook states both).
Tests: `tests/synthetic.py` builds the offline stores; `test_zero_edge.py` runs a zero-edge world through the real nodes and must show no edge.
Known issues and what is not modelled: the runbook's last section.

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
├── pyproject.toml         # dependencies = ["dskit"]; extras `parquet` = pyarrow, `features` = numpy + pyarrow
├── README.md / CLAUDE.md  # this file; agent orientation
├── crypto_trading/           # tier-3 code; import = registration
│   ├── __init__.py        # curated re-exports
│   ├── fields.py          # stage B: the row field names every node shares, once
│   ├── ports.py           # stage B: ListPortsNode, the one list-port check
│   ├── clock.py           # stage B: ISO instant to epoch ms, the one conversion
│   ├── kalshi_rows.py     # stage B: MarketRows, CandleRows, FeeRows (ObservationRows subclasses)
│   ├── decisions.py       # stage B: DecisionRows, one row per market and lead
│   ├── anchors.py         # stage B: StrikeAnchors, strikes as index observations
│   ├── spot_features.py   # stage B: point-in-time spot, vol, BVOL, moneyness
│   ├── market_state.py    # stage B: quote state from ended candles
│   ├── fees.py            # stage B: FeeColumns, the Kalshi fee-type mapping over dskit's fee mechanics
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
│   ├── source-binance-{btcusdt,ethusdt}-1m.json # stage A: spot klines (zip layout in transform_params)
│   ├── source-binance-{btc,eth}bvol.json # stage A: BVOL implied-vol index (zip layout in transform_params)
│   ├── binance_vision_dates.json         # the pinned day list the Binance sources pull
│   ├── suite-kalshi-crypto-{markets,candles,fees,books}.json # one suite per stream
│   ├── suite-binance-files.json          # the httpblobs inventory gate
│   ├── run-features-15m.json             # stage B: the feature table and kill-test document
│   └── source-features-15m.json          # stage B: localtables registration of the written table
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
│   ├── 2026-10-06-wsl-data-pull-runbook.md # stage A and B: exact commands, in order
│   └── .gitkeep
├── docs/research/         # research agent markdown
│   ├── README.md          # use record-research
│   └── .gitkeep
├── journal.json           # walk-up marker for dskit.journal
└── tests/                 # green in-repo AND after graduation, uninstalled
    ├── conftest.py        # sys.path bootstrap (position-independent)
    ├── synthetic.py       # offline stores: scripted Kalshi, day-file parquet via localblobs
    ├── golden/            # the pre-migration stage B outputs (one JSON, never regenerated)
    ├── test_binance_vision.py # the two Binance layouts through dskit's zip transform + httpblobs e2e
    ├── test_anchors.py    # strikes as index observations
    ├── test_decisions.py  # market x lead rows
    ├── test_features_pipeline.py # run-features-15m.json end to end, publish and read back
    ├── test_migration_golden.py # the dskit migration changed no row, column or score
    ├── test_fees.py       # the Kalshi fee mapping, schedule reader, FeeColumns
    ├── test_kalshi_rows.py # readers: labels, exclusions, seconds to ms
    ├── test_market_state.py # candle state and the no-peeking rule
    ├── test_spot_features.py # strict-prior leak tests with a control
    ├── test_zero_edge.py  # a zero-edge world scores zero edge; a stale quote does not
    ├── test_ports_and_markers.py # one list-port owner; no INTERIM marker outlives its dskit module
    ├── test_configs.py    # every config validates against its engine; pins
    ├── test_connectors.py # four-verb contract + acquire→validate e2e
    ├── test_kalshi_crypto.py # kalshi sources + suites against a scripted venue
    ├── test_execution.py  # the venue executor's shape; the battery, one line away
    ├── test_nodes.py      # conformance suite + a document e2e
    └── test_production.py # the serve document validates; every template fail-closed
```

Graduation is a directory move — nothing here references its incubation
position (ADR-0021; see `children/README.md` in the dskit repo).

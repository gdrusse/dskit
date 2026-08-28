# CLAUDE.md — intraday_poc (a dskit child)

Agent orientation — see README.md for what the child does and the
one-command runs. This is the two-stock intraday PoC AND the reference
shape for future children: keep the module set stable
(connectors / nodes / models / live / testing + configs + tests).

## The child rules (ADR-0021)

- **Never edit dskit.** A missing capability is either a genuinely
  generic gap — propose an ADR upstream — or domain logic that stays
  here. There is no third option.
- **The domain lives here and in `configs/`.** dskit stays domain-blind;
  behavior is JSON the engines validate, self-documented via `notes`
  (the why, not the what), default-deny everywhere.
- **Tier-3 may import anything** — but keep heavy imports inside
  `run()`/`read()`: documents naming these kinds must PLAN on machines
  without torch/pyomo/alpaca-py (the conformance suite enforces it).
  The ONE exception is `models.py` (torch at module top — subclassing
  `nn.Module` needs it): it is run-path only, resolved by the declared
  seam inside `run()`, and must never be imported by `__init__`,
  `nodes`, or `connectors`.
- **Position-independent**: no `..` imports, no dskit-repo paths; the
  only coupling is `import dskit`. Graduation is a directory move.
- **Import = registration**: `intraday_poc/__init__.py` imports `nodes`,
  which registers the kinds (`owned` never set — that is toolkit
  doctrine). `--adapter intraday_poc` is exactly this import.

## Layout

```
intraday_poc/
├── connectors.py   # AlpacaBarsConnector — four verbs; _fetch is the ONLY
│                   #   vendor-touching method (testing.py doubles it)
├── nodes.py        # bars (data) / window (transform) / forecast (score)
│                   #   / select-one (score, on the PyomoSolve doorway)
├── models.py       # NextBarLSTM — run-path only, torch at top (see above)
├── live.py         # forward loop: clock gate → IEX bars → verified
│                   #   artifact restore → the SAME pyomo pick → paper order;
│                   #   every knob READ from the run dir + source config
└── testing.py      # StubBarsConnector — deterministic, no network
configs/            # source-backfill (the ONE source config) / suite-bars /
                    #   run-backtest (walkforward) / run-train / asset-model
tests/              # conftest bootstrap + connectors/nodes/configs suites
pyproject.toml      # dskit + alpaca-py/torch/pyomo/highspy (run-path only)
.env.example        # Alpaca paper keys — .env is gitignored, never committed
```

## Gotchas learned building this

- **Lookback is pinned in three places** — the window node's `lookback`,
  the LSTM's `module_params.lookback`, and the `ret_lag_*` feature list.
  `test_configs.py::test_lookback_agrees_everywhere` pins the agreement;
  the module also refuses a width mismatch at run.
- **Streams are single-pass**: `SelectOne.run` groups forecasts ONCE and
  caches for `build_model`; never re-iterate an input port.
- **`select-one` is role `score`, not `capital`** — deliberate: it scores
  the rule. Sizing cash with it means restoring the doorway's default
  `capital` role and wiring a `stat_test` survivors gate.
- **One source name, two modes**: `alpaca` is registered once with
  `configs/source-backfill.json` and BOTH `--mode backfill` and
  `--mode live` acquire through it (ADR-0014 keys the cursors). A second
  source name would put live bars in a tree the run documents never read
  — that bug shipped once; `test_one_source_name_carries_both_pulls`
  pins it shut. The modes differ in ONE place: a pull with no cursor
  starts at `start` (backfill) or at most `live_lookback_minutes` ago
  (live) — unbounded, a first live pull would re-commit the whole
  history as a second acquisition. On `sip` that lookback must also
  EXCEED the 16-minute clamp the window ends at; the gate refuses a
  smaller one rather than pulling an empty window forever, silently.
- **SIP vs IEX**: every STORE pull uses `feed=sip` (free, consolidated,
  end clamped 16 min back). Only the forward loop's own fetch uses IEX
  (`live.py:LIVE_FEED`), because real-time SIP is not on the free tier —
  sparse minutes possible, gap discipline handles it, never bridge.
- **`live.py` restates nothing**: price field, gap bound and module class
  come from `<run-dir>/config.json`; vendor knobs AND the symbol universe
  from the source config (`--source-config`), resolved through the
  connector's public `resolve_knobs` — the credential env-var NAMES
  (`key_env`/`secret_env`) among them, so the loop authenticates as the
  puller does, with the values loaded by dskit's `env.py` rather than a
  dotenv parser of the child's own. The bar interval is one constant
  (`connectors.BAR_INTERVAL`) both fetch paths build from.
  Node keys `qhat_aapl`/`qhat_msft` are the default
  artifact convention (`artifacts/qhat_<symbol>`); a document that names
  them differently is served with `--artifact SYMBOL=PATH`, never an
  edit to the loop — and an override for a symbol the config does not
  declare is refused, not dropped.
- `live.py` only ever constructs `TradingClient(..., paper=True)`.
  Keep it that way.

Keep this tree and README.md's current when files change.

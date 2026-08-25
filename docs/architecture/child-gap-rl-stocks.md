# Capability-gap report — rl_stocks as a dskit child

**Verdict: rl_stocks introduces zero new generic gaps — every generic fragment in
it is covered by dskit (or by pmquant's already-proposed ADRs), and the rest is a
small child.** Rebuilding it on dskit also retires most of its documented defects,
because the buggy code paths are exactly the ones dskit replaces.

Evidence base: full inventory 2026-08-25. rl_stocks is a 1,141-LOC phase-1 scaffold
(single commit, **zero tests**): Yahoo/GDELT pull → DynamoDB → FinBERT/OHLCV
preprocessing → joined `(dates, tickers, features)` tensor → S3 → torch Dataset.
The LSTM forecaster and Gym trading env exist but are unwired; PPO/MIO are absent.
Its own docs list eight known bugs (broken package imports, inverted trade signs,
…), most still in the tree; this investigation adds two more — six of eight
configs are dead (unreferenced by any code, one syntactically invalid) and
`{}.json` is an unformatted-path artifact.

Separately: **pmquant no longer consumes rl_stocks at all** (pmquant D-147/D-148 —
the `core/data/stores.py` seam is deleted; a hook merely guards edits). The "shared
data layer" in pmquant's CLAUDE.md is stale. rl_stocks stands alone.

## Classification by functional area

**COVERED** — the dskit mechanism exists.

| rl_stocks area | dskit mechanism |
|---|---|
| `base/base.py` `BaseDataSource` (config-validated pull ABC) | `dskit.onboarding.Connector` — the same idea with spec/check/discover/read, default-deny config, checkpoints |
| `scripts/fetch_and_write.py` (JSON-driven pull → store, `eval`-based class lookup) | `python -m dskit.onboarding acquire` + `pkg.module:Class` connector resolution (no `eval`) |
| `scripts/preprocess_and_write.py` (JSON-declared reflection stage runner — the repo's most dskit-shaped idea) | a pipeline **document**: same declaration, plus DAG wiring, identity hash, run dirs, default-deny params |
| `data/database.py` hashing (`generate_article_id` sha-256 composite keys) | content-addressed identity is native: `AssetRecord.version_id`, snapshot Merkle hashes |
| raw pull persistence + "did I already pull this" | WORM snapshots + per-(source,stream,mode) cursors + hash-keyed dedupe |
| ad-hoc data QA (none today) | declarative validation suites (`not_null`, `in_range`, `row_count`, `bitemporal`, …) |
| `preprocessing/joiner.py` (N `{key: {entity: value}}` streams → one dense tensor) | post-ADR-0022: `concat` + `join` over record streams; dense-tensor layout via a `tensor`-role node (`ArrayFeatures` base) |
| `configs/*.json` as behavior (2 of 8 actually load; one is invalid JSON) | the config IS the interface everywhere, and `validate` refuses a broken one loudly |
| effective-date vs pull-time confusion (`seendate`→`timestamp` munging) | bitemporal `(effective_date, acquired_at)` on every record, declared not inferred |

**WRAPPER** — stocks/GDELT/Yahoo logic; belongs in the child.

| rl_stocks area | child wrapper it becomes |
|---|---|
| `data/yahoofinance_datasource.py` | `YahooConnector` (yfinance inside `read()`) |
| `data/gdelt_datasource.py` | `GdeltConnector` (gdeltdoc inside `read()`) |
| DynamoDB persistence | not needed locally (file/sqlite/parquet stores cover it); if AWS is wanted, a tier-3 `Store` via the `backend="pkg.module:Class"` seam |
| `preprocessing/text_processing.py` (NER→ticker map→FinBERT) | a `transform`-role node; the ticker map is a `table-file` input, models named in params |
| `preprocessing/numeric_processing.py` (OHLCV pivot) | a `transform` node; the hardcoded column list becomes a param |
| `preprocessing/dataset_creation.py` (window/horizon panel dataset) | a `tensor`-role node in the child (windowing params in the document) |
| `rl/forecasting.py` (shared-LSTM, per-entity heads) | a `TorchTrain` subclass — `build_module(params)` is exactly this seam |
| `rl/environments.py` `TradingEnv` + future PPO/MIO | child modules behind `capital`-role nodes (`PyomoSolve` base for the MIO guardrail) |
| `configs/stocks_mapping.json`, universe lists | child configs / `table-file` inputs |

**GAP** — none new. The only generic-shaped code dskit lacks an exact home for is
the panel **windowing** utility (lookback/horizon slicing in `dataset_creation.py`)
— subsumed by the ADR-0025 discussion (declared torch training needs a loader
story); not worth its own ADR from this repo's evidence alone.

## rl_stocks as a thin child — sketch

`children/rl_stocks/` per ADR-0021:

```
children/rl_stocks/
├── pyproject.toml            # depends on dskit[torch,numpy]; yfinance/gdeltdoc/transformers free
├── rl_stocks/
│   ├── __init__.py           # import = registration of the node kinds below
│   ├── connectors.py         # YahooConnector + GdeltConnector (four-verb; heavy imports in read())
│   ├── nodes_text.py         # rl-news-score: NER → ticker map (table input) → FinBERT sentiment
│   ├── nodes_panel.py        # rl-panel: join/pivot streams → (dates, tickers, features) tensor + windows
│   ├── forecaster.py         # shared-LSTM per-entity-head module as a TorchTrain subclass
│   ├── env.py                # TradingEnv (sign conventions FIXED in the rewrite)
│   └── allocate.py           # the PPO/MIO guardrail when built, behind a capital node
├── configs/
│   ├── source-yahoo.json, source-gdelt.json
│   ├── suite-prices.json     # rows arrived, close not-null, bitemporal — the QA it never had
│   ├── asset-model.json      # dataset / model-checkpoint / allocation kinds
│   ├── tickers.json          # the ticker↔alias map, loaded via table-file
│   └── run-forecast.json     # pull → score → panel → train → validate document
└── tests/                    # connector conformance + node conformance + config validation
```

Migration note: this is a **rebuild on the seams, not a port** — at 1,141 LOC with
zero tests and phase-1 scope, wrapping the existing files as-is would preserve the
broken imports and dead configs. The child above is smaller than the original and
inherits testing, identity, provenance, and bitemporality for free.

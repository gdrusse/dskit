# Design proposal — intraday_equities as a dskit child

**Status: RATIFIED** — owner-approved 2026-08-30 for next-session
implementation under ADR-0046/0047.

Date: 2026-08-30. Goal: a practical US-equity intraday system that predicts
returns, then chooses cost-aware limit orders. Data acquisition comes first.

## 1. Decisions

- Trade five configured stocks: AAPL, JPM, XOM, WMT and LLY. Collect SPY as a
  feature-only market reference.
- Collect and store one-minute data. Coarser views are derived, never fetched.
- Backfill consolidated SIP bars through Alpaca; capture live bars through the
  authenticated Schwab account used for eventual execution.
- Keep vendor observations separate and immutable. Normalize at the pipeline
  boundary and prove overlap parity before combining evidence.
- Compare action/label windows of 1, 5, 15, 30 and 60 minutes in five pipeline
  documents. They must otherwise be identical.
- Research uses regular-session rows; collect all rows the providers return.
- No real-money order is authorized by this proposal. Paper limit-order
  execution follows only after data, parity and locked-test gates pass.

## 2. Source and feature inventory

| Source | Initial use | Confirmed fields | Constraint |
|---|---|---|---|
| Alpaca Basic, SIP | historical backfill from 2016 | 1m OHLCV, trade count, VWAP | latest 15 minutes unavailable; 200 calls/min |
| Alpaca SIP quotes/trades | later microstructure backfill | NBBO prices/sizes; trade price/size/conditions | high row volume; not Phase 1 |
| Schwab price history | live 1m polling and overlap | OHLCV, timestamp | OAuth; authenticated limits must be measured |
| Schwab Level One | later live microstructure | bid/ask, sizes, last, volume, venue/time fields | add only after historical/live parity |
| Schwab fundamentals | daily archive | market cap, shares, beta, average volume and related fields | current snapshots are not point-in-time history |
| Yahoo Finance | cross-check only | recent intraday/daily public display data | unofficial, shallow 1m retention; not a production source |

Phase-1 model features use only historical/live common truth:

1. log returns over configured elapsed-minute lookbacks;
2. open-close and high-low ranges, realized/downside volatility;
3. raw, relative and time-of-day volume;
4. gaps, missingness and stale-bar indicators;
5. stock return minus SPY return, market volatility and time-of-day.

Alpaca VWAP/trade count are stored but excluded until a Schwab-live equivalent is
demonstrated. Market cap/fundamentals are archived daily from authorization
forward; today's values must never be joined backward into history. Spread,
depth imbalance and quote intensity enter a later feature document only after
Alpaca historical quotes and Schwab Level One pass their own parity study.

## 3. dskit ownership

Every pull uses `dskit.onboarding`: registered source, declared mode, immutable
snapshot, validation, certification and `scan_stream`. Every experiment is a
pipeline JSON document. Runs and data products enter `dskit.assets`.

Generic capability lands upstream:

- ADR-0046: OAuth refresh, recurring REST acquisition, `watch`, and tier-2
  Alpaca/Schwab connector packs.
- ADR-0047: `event-grid`, separating event-time action cadence from label lead.
- Existing `ReturnWindows`, time splits, walk-forward, fitted transforms,
  sklearn/torch packs, Optuna and `PyomoSolve` remain the implementation seams.

The child owns only its cohort vocabulary, canonical field policy, session
rules, feed-parity tolerances, score/portfolio interpretation and live wrapper.

## 4. Complete proposed child tree

```text
children/intraday_equities/
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── intraday_equities/
│   ├── __init__.py
│   ├── auth.py
│   ├── connectors.py
│   ├── nodes.py
│   ├── models.py
│   ├── live.py
│   └── testing.py
├── configs/
│   ├── asset-model.json
│   ├── source-alpaca-backfill.json
│   ├── source-schwab-live.json
│   ├── suite-alpaca-bars.json
│   ├── suite-schwab-bars.json
│   ├── run-feed-parity.json
│   ├── run-action-01m.json
│   ├── run-action-05m.json
│   ├── run-action-15m.json
│   ├── run-action-30m.json
│   ├── run-action-60m.json
│   ├── run-hpo-linear.json
│   ├── run-hpo-tree.json
│   ├── run-hpo-tcn.json
│   ├── run-model-compare.json
│   └── run-train.json
└── tests/
    ├── conftest.py
    ├── test_connectors.py
    ├── test_nodes.py
    ├── test_configs.py
    └── test_live.py
```

File responsibilities:

- `README.md`: operator path from credentials through paper execution.
- `CLAUDE.md`: child invariants, extension points and the same tree.
- `pyproject.toml`: standalone package metadata and exact runtime dependencies.
- `.env.example`: environment-variable names with empty values; never secrets.
- `auth.py`: thin manual Schwab-authorization command over ADR-0046.
- `connectors.py`: thin cohort/schema subclasses of the dskit vendor packs.
- `nodes.py`: store reader, equity window vocabulary, feed-parity score and
  `PyomoSolve` portfolio doorway; no vendor transport.
- `models.py`: empty documented seam for a genuinely new architecture; initial
  documents name dskit's sklearn/torch families directly.
- `live.py`: loads the shipped run/source configs, computes the same graph and
  emits paper limit-order intents; no duplicated features or model settings.
- `testing.py`: deterministic connector and bar fixtures.
- Source/suite configs: one-minute vendor pulls and structural data gates.
- `run-feed-parity.json`: compare overlapping canonical bars by symbol/minute.
- `run-action-*.json`: one controlled horizon/cadence experiment each.
- `run-hpo-*.json`: one reduced-data search per model family.
- `run-model-compare.json`: full-fold comparison of the three pinned winners.
- `run-train.json`: winner pinned as ordinary params; no search node.
- Tests: connector conformance, config validation, cross-document pins,
  train/live parity and paper-only refusal.

## 5. Credential bootstrap

The 2026-08-30 environment check found all expected Alpaca and Schwab variables
missing. The operator must provide them without committing values:

```bash
export APCA_API_KEY_ID=...
export APCA_API_SECRET_KEY=...
export SCHWAB_APP_KEY=...
export SCHWAB_APP_SECRET=...
export SCHWAB_CALLBACK_URL=...
export SCHWAB_TOKEN_PATH="$HOME/.local/state/intraday_equities/schwab-token.json"
```

After ADR-0046 is implemented:

```bash
mkdir -p "$HOME/.local/state/intraday_equities"
chmod 700 "$HOME/.local/state/intraday_equities"
python -m intraday_equities.auth authorize \
  --source-config children/intraday_equities/configs/source-schwab-live.json
chmod 600 "$SCHWAB_TOKEN_PATH"
```

The command opens/prints Schwab's authorization URL. The operator signs in,
approves the app and completes the callback. Acquisition must refresh access
tokens automatically; an expired/revoked refresh grant stops loudly and asks
for this command again.

The source configs are pinned as follows:

- both request `[1, "Minute"]` bars for AAPL, JPM, XOM, WMT, LLY and SPY;
- Alpaca starts `2016-01-01`, uses `feed: "sip"` and `adjustment: "raw"` so
  overlap comparison is against the same live price scale;
- Schwab polls only closed bars, re-requests an overlap window, and relies on
  onboarding's bitemporal dedup rather than trusting an exact-once API;
- both store observation payloads with deterministic gzip.

## 6. First successful data pulls

These are the exact post-implementation steps. Keep data outside the repo:

```bash
export INTRADAY_DATA_ROOT="$HOME/.local/share/intraday_equities/onboarding"

python -m dskit.onboarding init --root "$INTRADAY_DATA_ROOT"

python -m dskit.onboarding register-source alpaca-sip \
  --root "$INTRADAY_DATA_ROOT" \
  --catalog-source alpaca-sip-source \
  --connector intraday_equities.connectors:AlpacaBars \
  --config @children/intraday_equities/configs/source-alpaca-backfill.json \
  --activate

python -m dskit.onboarding register-source schwab \
  --root "$INTRADAY_DATA_ROOT" \
  --catalog-source schwab-source \
  --connector intraday_equities.connectors:SchwabBars \
  --config @children/intraday_equities/configs/source-schwab-live.json \
  --activate

python -m dskit.onboarding acquire \
  --root "$INTRADAY_DATA_ROOT" \
  --source alpaca-sip --stream bars --mode backfill

python -m dskit.onboarding acquire \
  --root "$INTRADAY_DATA_ROOT" \
  --source schwab --stream bars --mode live

python -m dskit.onboarding verify --root "$INTRADAY_DATA_ROOT"
```

Validate each emitted snapshot ID with its matching suite, then certify it.
Only after one finite Schwab pull succeeds should recurring capture start:

```bash
python -m dskit.onboarding watch \
  --root "$INTRADAY_DATA_ROOT" \
  --source schwab --stream bars --mode live --every-seconds 60
```

`watch` runs only during declared market sessions under the operator's service
manager or a named tmux session. It must not be daemonized by child code. Each
iteration polls Schwab's one-minute price-history endpoint and keeps only newly
closed bars. A market-session pull is successful only when it emits a nonempty
WORM snapshot, advances the source/stream/mode cursor, passes the suite and
survives `verify`.

## 7. Parity and action-window study

Collect at least 30 regular sessions of overlap. `run-feed-parity.json` reports
timestamp coverage and per-field differences. OHLCV features graduate only when
the discrepancy distribution is stable and explained; tolerances are declared
in that document, never hidden in code.

The five action documents share the same:

- six-symbol input and five-symbol tradable cohort;
- one-minute source rows and elapsed-time feature lookbacks;
- chronological cuts, costs, capital and baseline ridge model;
- entry delay, limit-fill rule and regular-session filter.

They differ only in document identity/notes, `label_lead`, and
`event-grid.period_ms`. Tests compare their canonical objects and refuse any
other drift. First-pass cadence equals horizon; later decoupling requires new
documents.

Measure rank IC, net return, drawdown, turnover, fill rate and signal decay
under 1/3/5-second delays. Competition is not directly observable; fast decay,
poor delayed P&L and adverse passive fills are its declared proxies. Select the
longest cadence within one standard error of the best net validation score that
stays profitable under doubled spread/slippage assumptions.

## 8. HPO with one RTX GPU

Do not multiply horizon search by model search. Select the action window first
with fixed ridge parameters, then tune models.

1. Freeze one older 18-month training plus 3-month validation slice for HPO;
   it ends before the model-selection period and is never a random row sample.
2. Run 32 TPE trials for ridge/elastic-net, 40 for histogram gradient boosting
   and 24 sequential GPU trials for the small TCN.
3. Materialize each family winner as fixed params in `run-model-compare.json`.
4. Compare those three winners over all discovery folds; run the TCN winner
   with three fixed seeds and report its mean and dispersion.
5. Select once on the following six-month model-selection period and pin that
   winner in `run-train.json`; remove every search node.
6. Run the latest six-month lockbox once. Failed gates do not trigger retuning.

The objective is portfolio return net of spread, missed fills and costs, with a
fold-instability penalty. Cheap linear/tree trials run on CPU; one neural trial
uses the GPU at a time with declared train/eval batch sizes. The existing
Optuna pack has no per-epoch pruning, so the proposal spends fewer full TCN
trials rather than claiming a pruning strategy the framework does not have.

## 9. Limit-order and release gates

A limit caps price; it does not guarantee a fill. Historical fills require a
subsequent trade through the limit and are capped by observed executable
volume. Paper fills calibrate missed-fill, partial-fill and adverse-selection
assumptions.

Release order:

1. Alpaca backfill passes suite and store verification.
2. Schwab finite live pull passes suite and store verification.
3. Thirty-session feed parity passes.
4. One action window wins without touching the lockbox.
5. HPO winner is pinned and survives the lockbox at 1x and 2x costs.
6. Schwab paper limit orders run prospectively.
7. Real-money enablement requires a separate explicit owner decision.

## 10. Implementation order after approval

1. Red tests and implementation for ADR-0046; update onboarding docs/trees.
2. Red tests and implementation for ADR-0047; update pipeline docs.
3. Build the exact child tree above from `_skeleton`; no extra files.
4. Validate/plan every config and run child/root tests.
5. Operator completes Alpaca keys and Schwab authorization.
6. Execute one backfill and one finite live pull using §6.
7. Start recurring Schwab pulls only after both snapshots verify.

This proposal is the handoff contract. An implementation agent must not invent
another source, interval, cohort, feature or file without returning it for an
owner decision.

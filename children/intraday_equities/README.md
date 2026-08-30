# intraday_equities — a dskit child

US-equity intraday child: one-minute Alpaca SIP history, live Schwab
bars, a cadence study, then paper limit orders. dskit owns OAuth,
`watch`, the vendor packs, and `event-grid`. This package owns the
cohort, session policy, feed-parity score, and paper wrapper.

A child consumes dskit, never modifies it. The domain lives in `configs/`.

## Running, end to end

Every command runs from the child's own root.

```bash
python -m pytest tests -q
pip install -e .

# 1. credentials (names in .env.example; values never committed)
cp .env.example .env
# fill APCA_* and SCHWAB_* ; export so acquire sees them:
set -a; . ./.env; set +a
mkdir -p "$HOME/.local/state/intraday_equities"
chmod 700 "$HOME/.local/state/intraday_equities"
export SCHWAB_TOKEN_PATH="$HOME/.local/state/intraday_equities/schwab-token.json"
python -m intraday_equities.auth authorize \
  --source-config configs/source-schwab-live.json
# sign in, then:
python -m intraday_equities.auth authorize \
  --source-config configs/source-schwab-live.json --code '<callback URL>'
chmod 600 "$SCHWAB_TOKEN_PATH"

# 2. onboarding root (keep data outside the repo in production)
export INTRADAY_DATA_ROOT="${INTRADAY_DATA_ROOT:-./ob}"
python -m dskit.onboarding init --root "$INTRADAY_DATA_ROOT"

python -m dskit.onboarding register-source alpaca-sip --root "$INTRADAY_DATA_ROOT" \
    --catalog-source alpaca-sip-source \
    --connector intraday_equities.connectors:AlpacaBars \
    --config @configs/source-alpaca-backfill.json --activate
python -m dskit.onboarding register-source schwab --root "$INTRADAY_DATA_ROOT" \
    --catalog-source schwab-source \
    --connector intraday_equities.connectors:SchwabBars \
    --config @configs/source-schwab-live.json --activate

python -m dskit.onboarding acquire --root "$INTRADAY_DATA_ROOT" \
    --source alpaca-sip --stream bars --mode backfill
python -m dskit.onboarding acquire --root "$INTRADAY_DATA_ROOT" \
    --source schwab --stream bars --mode live
python -m dskit.onboarding verify --root "$INTRADAY_DATA_ROOT"

# 3. recurring live capture only after both snapshots verify
python -m dskit.onboarding watch --root "$INTRADAY_DATA_ROOT" \
    --source schwab --stream bars --mode live --every-seconds 60

# 4. experiments
python -m dskit.pipeline run configs/run-feed-parity.json \
    --asof 2026-08-30 --adapter intraday_equities
python -m dskit.pipeline run configs/run-action-01m.json \
    --asof 2026-08-30 --adapter intraday_equities
python -m dskit.pipeline run configs/run-train.json \
    --asof 2026-08-30 --adapter intraday_equities

# 5. paper intents (reads the shipped documents; refuses real money)
python -m intraday_equities.live --run-doc configs/run-train.json \
    --source-config configs/source-schwab-live.json --qty 1
```

Every `run-*.json` writes the local MLflow sink `sqlite:///mlruns.db`
(experiment `intraday_equities`). `tracking` is not identity. After a
run: `mlflow ui --backend-store-uri sqlite:///mlruns.db`.

`--root` is on every onboarding command. `--adapter intraday_equities`
is just an import.

Do not substitute Yahoo. Keep both vendors as separate sources.
`watch` is an operator service or tmux session, not a child daemon.

Exit codes: `0` ran · `3` halted at a gate · `1` error.

## Layout

```
intraday_equities/
├── README.md / CLAUDE.md / pyproject.toml / .env.example
├── intraday_equities/
│   ├── __init__.py      # import = registration
│   ├── auth.py          # manual Schwab authorize over ADR-0046
│   ├── connectors.py    # thin Alpaca/Schwab subclasses
│   ├── nodes.py         # bars, windows, feed-parity, portfolio
│   ├── models.py        # empty bespoke-architecture seam
│   ├── live.py          # paper intents from shipped configs
│   └── testing.py       # network-free connector doubles
├── configs/             # sources, suites, action/HPO/train documents
└── tests/
```

Graduation is a directory move — nothing here references its incubation
path.

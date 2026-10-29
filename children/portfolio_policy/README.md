# portfolio_policy — a dskit child

Research child for **hold-vs-exit decisions on a long-term Schwab
portfolio**: for each position, is the expected after-tax, after-cost value
of holding passively at least the value of cashing out into an active
strategy? Status: **base machinery complete (ADR-0279), skeptic-reviewed;
position summary (A0002) reviewed; risk inputs from Schwab daily bars (A0003)
built, not yet lens-reviewed; tax lots from Schwab transactions (A0004) built,
round-1 review corrections applied, not yet re-reviewed; the hold verdict page and
`weekly.sh` reviewed twice before the merge with both, not since; no real Schwab pull yet** —
every rate in the documents is a placeholder the owner sets. The sections after
"Scoring a past decision run" are the skeleton's generic walkthrough.

## Hold vs redeploy (ADR-0279)

### First real run from Schwab

From the child root, after `pip install -e .`. Nothing here places an order.

```bash
# 1. Authorize once. The OAuth values come from the environment, never a config; one token
#    file serves schwab_positions, schwab_transactions and schwab_daily alike.
export SCHWAB_APP_KEY=... SCHWAB_APP_SECRET=... SCHWAB_CALLBACK_URL=... SCHWAB_TOKEN_PATH=./schwab-token.json
python -m dskit.onboarding authorize --connector schwab_positions --config '{}'    # prints the login URL
# approve it in the browser, then pass the code (or the whole callback URL it redirected to):
python -m dskit.onboarding authorize --connector schwab_positions --config '{}' --code '<code or callback URL>'

# 2. Register the sources, once. A source has one active config: when the holdings change,
#    register the new symbol list under a NEW name (schwab_daily_2, say), point the
#    document's bars.source at it, and acquire it (it re-pulls from start).
python -m dskit.onboarding init --root ./ob
python -m dskit.onboarding register-source schwab --root ./ob \
    --catalog-source schwab-src --connector schwab_positions --config '{}' --activate
# the purchases the tax lots are rebuilt from: one source per connector (one alias is one
# connector) over the same OAuth environment; start = on or before your EARLIEST purchase
# still held (lower it later and the next acquire re-pulls from the new start)
python -m dskit.onboarding register-source schwab_trades --root ./ob \
    --catalog-source schwab-trades-src --connector schwab_transactions \
    --config '{"start": "2015-01-01"}' --activate
# symbols = every held symbol, funds and ETFs included, plus SPY (the benchmark); start >= 400 days before
# today (a year of returns needs 253 closes, with holidays to spare)
python -m dskit.onboarding register-source schwab_daily --root ./ob \
    --catalog-source schwab-daily-src --connector schwab_daily \
    --config '{"symbols": ["VTI", "AAPL", "SPY"], "start": "2025-09-01"}' --activate
# the active strategy's backtest: ./inputs/active/returns.jsonl (the file stem is the
# stream name), one row per period: {"period": ..., "day": "YYYY-MM-DD", "net_return": ...}
python -m dskit.onboarding register-source active --root ./ob \
    --catalog-source active-src --connector localfiles \
    --config '{"path": "./inputs/active", "effective_field": "day"}' --activate

# 3. Acquire, before every run. Positions: one snapshot per UTC day. Trades: every trade from
#    start (the cursor day is requested again, so a late posting lands; the census of fee
#    lines and skipped types prints). Daily bars: closed sessions only (today's never); live
#    re-requests the last 5 days, so corrections land.
python -m dskit.onboarding acquire --root ./ob --source schwab --stream positions --mode backfill
python -m dskit.onboarding acquire --root ./ob --source schwab_trades --stream trades --mode backfill
python -m dskit.onboarding acquire --root ./ob --source schwab_daily --stream bars --mode live
python -m dskit.onboarding acquire --root ./ob --source active --stream returns --mode backfill

# 4. Set the document: the decision day in FIVE places, all the positions pull's UTC day
#    (the rows' as_of; a pull after 20:00 New York, 19:00 in standard time, is dated the next
#    day), pinned equal by a test: snapshot.where[as_of], lots.as_of, tax.as_of,
#    risk_day.where[ts] and risk_latest.reference_date. Then the placeholders below.

# 5. Run.
mkdir -p decisions summary risk
python -m dskit.pipeline run configs/run-hold-vs-redeploy.json --asof <that day>

# 6. Or, in place of 4-5, the one command that also writes the verdict page ("Reading the
#    verdict" below). It needs steps 1-3 done first: it repeats the positions pull itself (the
#    day is that pull's UTC day), and reads the trades and the daily bars as step 3 left them.
#    It sets the five days in a temporary copy of the document and prints report/<run>.md.
#    One or the other for a day: a run's name is its day and hash, and a repeat refuses.
bash weekly.sh <that day>
```

Then read `summary/<run>.jsonl` (one row per lot, "The few numbers" below) and
`risk/<run>.jsonl` (one row per symbol: `as_of` is the session the risk was read at,
the last one before the decision day; `annual_vol`; `beta_raw` and the adjusted
`beta` that priced the hold return). For "should I keep holding this?":

- `decision` is the answer under the document's terms: `exit` only when selling,
  paying the tax and redeploying ends ahead of holding after tax at the horizon.
- `opportunity_cost` is by how much a year: above zero holding costs you that much,
  below zero holding wins. `break_even_return` is the return the active strategy
  must clear for exiting to tie; trust an `exit` only as far as you trust the
  strategy to beat it.
- `tax_cost` (and `embedded_gain`) is the one-off price of exiting.
- `volatility` and `beta` are the risk you keep by holding; a holding the decision
  keeps can still be too large: check the portfolio's `top_weight` (above ~10% is a
  concentrated position) alongside its volatility.

`configs/run-hold-vs-redeploy.json` holds every rate, horizon and field name;
set them before the first run. Neither return is a literal: `market` builds
the hold return from dividend yield + real growth + inflation + CAPE drift and
`betas` scales it per symbol (research A0001) by each holding's beta, wired from
the `schwab_daily` bars (`bars` -> `risk_features` -> `risk_day` -> `risk_latest`
-> `risk`, research A0003; no beta is typed; a bars pull more than 5 days stale is refused); `hurdle` deflates the active
backtest's mean by what `trials` variants of nothing would have found and
withholds it below `min_dsr`, in which case every holding holds. The hold
path is taxed once at the horizon (`terminal_tax_rate`); the active path pays
its short-term rate every year (`redeploy_tax_rate`, `annual`). Equities
only, and long only: the `snapshot` filter drops option rows (their basis
carries no multiplier) and short positions (`long_quantity > 0`), because the
kinds refuse a short or zero-value row by index rather than decide on it.
`hurdle.min_periods` (52, a year of weekly returns) is the fewest backtest
periods it will accept, and returns are fractions (0.01 = 1%), never percent.
`market.horizon_years` is the decision horizon (pinned equal to
`decide.horizon_years` by a test) and may not exceed `reversion_years`. Rows
land in `decisions/<run>.jsonl`, one per lot (account x symbol x `lot_id`),
advisory only. `decide.risk_premium` ships at 0.0 as a
placeholder, not as a claim that zero is safe: the hurdle's arithmetic mean
compounds geometrically, a gap of about sd^2 / 2 a year, so set it from the
backtest's annual volatility (`hurdle` summary `annual_sd`) before a real run.

**Tax lots (research A0004).** Schwab's positions carry no lots, so the
`trades` stream is replayed into open lots (`lots`, FIFO: a sale takes the
earliest shares first) and each lot is valued at the snapshot's price
(`lot_positions`). What changes in the numbers: every lot is taxed by its own
date and basis, so shares bought within the year pay the short-term rate on
their own gain instead of the whole holding taking `default_holding`; a
holding's tax is the sum of its lots', and the decision, summary and outcomes
are per lot (a young lot may hold while an old one exits). In the e2e example
VTI's tax rises from 119 (aggregate, all long) to 132.60 (99.96 on the 2023 lot,
32.64 on the lot bought three months earlier). Every holding must reconcile
share for share: its lots' shares equal the snapshot's `long_quantity`. One that
does not (bought before `start` or before the history Schwab serves,
transferred in, split, a reinvestment outside `TRADE`), or a sale the history
never bought, passes as its aggregate row instead: `allow_incomplete` is `true` on
BOTH `lots` and `lot_positions` as shipped (a test pins them equal), because the
first real pull (2026-10-29) found Schwab's transactions history starting in 2024.
Such a holding has a null `acquired`, so `default_holding` applies, and it is listed
in the `lot_positions` summary `fallback`. One that mixes older shares with later
buys falls back whole, so its recent shares are taxed as `default_holding` too. Set
both to `false` to refuse such a holding by name instead. That summary's `basis_gap` per holding
(lot basis minus Schwab's cost basis) is zero when FIFO matches Schwab's books.
A day's trades replay in execution-time order (then transaction id), so a buy and
the sale that closes it the same day work whatever the ids; a trade dated ON the
decision day counts. A buy whose cash is null (a transaction with several kept items)
is basis = quantity x price; the `lots` summary's `amount_fallbacks` counts them.

To go back to the aggregate path (no lots), edit `run-hold-vs-redeploy.json`: delete
`trades`, `lots` and `lot_positions`, point `tax.inputs.records` at `$snapshot.records`,
drop `tax.acquired_field`, and set `summary.key_fields` to `["account", "symbol"]`
(there is no `lot_id`). In `run-decision-outcomes.json` set `decisions.key_fields` to
`["account", "symbol", "as_of"]`, `outcomes.key_fields` to `["account", "symbol"]` and
delete `outcomes.match_fields`. `tests/test_hold_vs_redeploy.py` runs this recipe.

**Limits** (disclosed, not modelled):

- Every linked account is treated as taxable; the account type is discarded, so
  an IRA or 401(k) position is taxed as if sold in a brokerage account.
- The hold path's dividends are untaxed (only its terminal gain is), which
  favours holding.
- A split or spin-off between a decision and its scoring corrupts the
  per-share growth `DecisionOutcomes` reads; it is taken for a return.
- The snapshot keeps `EQUITY`, `COLLECTIVE_INVESTMENT` (ETFs) and `MUTUAL_FUND`, as
  Schwab typed them on the first real pull (2026-10-29). A mutual fund trades once a
  day at NAV; the decision treats it as liquid at the snapshot value.
- Concentration is by symbol, with no look-through: an S&P 500 fund at half the
  portfolio reads as one concentrated position, and overlapping index funds
  (SWPPX, VFIAX, SWTSX) count as separate holdings.
- Rows the filters drop (options, shorts, other asset types) are not counted or
  reported yet (the transactions pull does count the items it skips).
- Lots assume FIFO. Schwab's default is FIFO for stocks, but funds and some ETFs
  in accounts opened before October 2024 default to average cost, and any
  account may have elected another method or identified lots at sale: confirm
  the election; a non-zero `basis_gap` is the symptom (`lot_positions` logs a
  warning with the largest, and `basis_gap_max` in its summary is the number to read).
- Wash sales are not modelled: a loss disallowed by a repurchase within 30 days
  would raise the new lot's basis and tack the old holding period onto it, so
  that lot's basis and short-term class here are wrong, and the account shows a
  `basis_gap`.
- Only `VALID` transactions are trades. A PENDING or INVALID one in the pull
  refuses by index (a pending trade must not become a lot). A pending one clears
  when it settles; an INVALID (cancelled) one stays in Schwab's history and stops
  every pull whose window reaches it. Raising `start` past it is the only way round
  today (lots bought before it then fail the reconciliation); a skip is an owner
  decision (lane note).
- Schwab's transactions endpoint served nothing before 2024 on the first real
  pull, so most holdings fall back to their aggregate rows (`allow_incomplete`)
  until a lot export is onboarded.
- The trades pull keeps `EQUITY` items (the connector's default `asset_types`).
  Fund and ETF purchases also predate the history, so they fall back either way;
  widen `asset_types` when the history covers them.
- `long_term_days` 366 is "more than one year" except when the year spans 29
  February (then the lot's 366th day is still short-term); short sales and
  splits are not modelled (each refuses at the reconciliation).
- Whether Schwab's daily closes are split- or dividend-adjusted is undocumented: an
  unadjusted split would read as one huge daily return and inflate that holding's
  volatility and beta for a year. Check a holding with a recent split on the first pull.
- A stale bars pull is refused, not read silently: risk is read at each symbol's latest
  session on or before the decision day, and a symbol (SPY too) whose latest session is more
  than `risk_latest.max_lag_days` (5 calendar days; a long weekend is 4) before it stops the
  run by name with its last session and lag. Inside the bound `risk/<run>.jsonl` `as_of`
  shows the session each number was read at. The bound counts calendar days, not sessions
  (no trading calendar), and a session missing mid-window stretches the year by that session.
- A holding without a year of Schwab daily bars (253 closes), or left out of the
  `schwab_daily` symbols, stops the run by name: there is no default beta or
  volatility. A pull without SPY leaves every beta null, which stops it the same
  way (`betas input[...] must be a number, got None`). Whether mutual funds have
  Schwab daily history is unverified.
- The beta is one year of daily returns against SPY, Blume-adjusted (research
  A0003): noisier than a longer window and not the 5-year monthly figure a quote
  page shows. The volatility is the last year's, annualized; not a forecast.

### The few numbers

The same run writes `summary/<run>.jsonl` (research A0002): one row per lot
with the numbers below, and the portfolio's own in the `summary` node's
`metrics` (`carry.json` in the run directory; `python -m dskit.pipeline runs`
tables them run over run). Rates are a year; shares are of today's value.

| Number | Plain meaning | What you do with it |
|---|---|---|
| `weight` | this row's share of the portfolio (one lot of one account's holding) | the ~10% concentration rule is for the portfolio's `top_weight` (below), not for this |
| `expected_return` | what holding should earn a year, before tax (market blocks x beta) | compare holdings |
| `volatility` | how much it swings a year (one standard deviation of the last 252 daily returns) | about 0.15-0.2 is a broad index fund; a single stock is often 0.25-0.4 |
| `beta` | how hard it moves with SPY (one year of daily returns, adjusted a third of the way to 1) | above 1 swings more than the market; it sets the expected return |
| `embedded_gain` | how much of the value is untaxed gain | the higher it is, the more selling costs |
| `tax_cost` | what selling today loses to tax | the price of exiting, paid once |
| `break_even_return` | the annual return an alternative must earn, before its own cost and tax (the hurdle's basis), for exiting to tie holding | exit only if you trust the strategy to beat it |
| `opportunity_cost` | what holding costs a year, after tax, against the alternative | above zero: you lose that much a year by holding (`decision` is `exit`); below zero holding wins |

The portfolio adds `total_value`, the weighted `expected_return` and `beta`,
`weighted_volatility` (an upper bound: it assumes every pair moves together),
concentration (`hhi`, `effective_holdings` = 1 / HHI, `top_symbol` /
`top_weight`, accounts merged: above ~10% in `top_weight`, a symbol's share
across accounts, is a concentrated position, not a row's `weight`),
`embedded_gain` (losses net against gains) and `tax_cost` of selling everything
(a sum of each row's tax: losses do not net across holdings while the decision
document's `tax.loss_offset` is false, so it errs high), `break_even_return`,
`opportunity_cost` (selling everything against holding everything; negative
means holding wins) and the `exit_positions` / `hold_positions` counts. The
`positions`, `exit_positions` and `hold_positions` counts are LOTS (one per row), not
holdings: a holding of three lots is three positions. The
summary restates `decide`'s tax and cost terms, pinned equal by a test; betas
and volatilities are wired from the daily bars. When the hurdle is withheld,
`opportunity_cost` is null and `break_even_return` is still the bar a strategy
would have to clear.

### Scoring a past decision run

A week or more later, score the hold forecast of an earlier run against a
fresh snapshot (`configs/run-decision-outcomes.json`):

```bash
python -m dskit.onboarding register-source decisions --root ./ob \
    --catalog-source decisions-src --connector localtables \
    --config '{"path": "./decisions", "layout": "file", "effective_field": "as_of"}' --activate
python -m dskit.onboarding acquire --root ./ob --source decisions --stream <run-name> --mode backfill
python -m dskit.onboarding acquire --root ./ob --source schwab --stream positions --mode backfill
mkdir -p outcomes
# set decisions.stream to <run-name> and later.where[as_of] to the new pull's UTC day, then:
python -m dskit.pipeline run configs/run-decision-outcomes.json --asof <that day>
```

`later` keeps the same stock, ETF and mutual fund longs the decision did (a test pins the clauses
equal), but only decided positions are read: an undecided short or option row
in the snapshot is never scored and cannot stop the run, and a decided position
the filter no longer sees is listed `unmatched`.

`<run-name>` is the decision run's directory name (the stem of its
`decisions/<run>.jsonl`). `outcomes/<run>.jsonl` holds, per decided lot
(matched to its holding by account and symbol), the price growth the decision
assumed, the realized per-share growth, and the surprise. The run's summary counts
twice: `scored` / `mean_surprise` per lot (row), `holdings` / `holding_mean_surprise`
per holding (a holding of ten lots counts once, with the mean surprise of its lots),
each also split `by_decision`. The forecast is a
total return and the snapshot's market
value is price only, so `outcomes.income_rate` (0.013, pinned equal to the
decision document's `market.dividend_yield`) is removed from each year's
growth before it is compounded for the time elapsed. It scores the hold
forecast only.

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
    --connector portfolio_policy.connectors:SampleConnector \
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
    --asof 2026-01-01 --adapter portfolio_policy
# execute rows land in docs/decisioning/actions.csv automatically

python -m dskit.journal research "a question" --topic a-question  # 5b. research note
python -m dskit.journal promote A0001 --criteria empirical  # owner path

python -m dskit.assets init --store ./portfolio_policy_store \  # 6. OPTIONAL — a
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

**`--adapter portfolio_policy` is just an import** — importing the package
registers its node kinds, which is what makes `portfolio_policy-*` in the
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

**Re-entry.** `docs/RE-ENTRY.md` is the lane's one-screen handoff (Now, Next,
Blocked / owner decisions, Read first, Do not), at most 80 lines, rewritten
whole at every `/wrap`; the dskit index `docs/RE-ENTRY.md` carries one row per
lane and the replaced notes go to `docs/archive/re-entry/`.

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
python -m dskit.pipeline run configs/run-sample.json --asof 2026-01-01 --adapter portfolio_policy
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
portfolio_policy/
├── pyproject.toml         # dependencies = ["dskit"] — the only coupling
├── README.md / CLAUDE.md  # this file; agent orientation
├── portfolio_policy/           # tier-3 code; import = registration
│   ├── __init__.py        # curated re-exports
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
│   ├── run-hold-vs-redeploy.json   # ADR-0279: positions + trades -> lots -> tax; daily bars -> risk; market x betas, hurdle -> decide -> summary
│   ├── run-hold-report.json        # ADR-0279 amendment: a decision run's rows + risk -> summary -> the verdict page
│   └── run-decision-outcomes.json  # ADR-0279: a past decision run scored against a later snapshot
├── weekly.sh              # bash weekly.sh <day>: pull -> decide -> publish -> report
├── decisions/             # hold-vs-redeploy rows, one file per run (gitignored)
├── summary/               # the few numbers per holding, one file per run (gitignored)
├── risk/                  # volatility and beta per symbol from daily bars, one file per run (gitignored)
├── report/                # the hold verdict page, one markdown file per run (gitignored)
├── outcomes/              # scored hold forecasts, one file per run (gitignored)
├── models/                # fitted ML/optimization artifacts (gitignored)
│   ├── README.md          # what belongs here vs the run directory
│   ├── .gitignore         # artifacts are rebuilt, never committed
│   └── .gitkeep
├── docs/decisioning/      # generated grid (CSV store; ADR-0056)
│   ├── README.md          # GENERATED — do not edit
│   ├── actions.csv
│   └── path.csv
├── docs/RE-ENTRY.md       # the lane note: Now / Next / Blocked; rewritten whole at /wrap
├── docs/explanations/     # child-specific tutorials and walkthroughs
│   └── README.md          # use record-explanation
├── docs/memos/            # durable implementation and operational handoffs
│   ├── README.md          # use memo
│   └── .gitkeep
├── docs/plans/            # project-specific plan builds
│   ├── README.md          # the child's own phased work plans
│   └── .gitkeep
├── docs/research/         # research agent markdown
│   ├── README.md          # use record-research
│   ├── hold-return/       # A0001: building blocks over trailing means
│   ├── position-summary/  # A0002: the few numbers per position and portfolio
│   ├── risk-inputs/       # A0003: beta and volatility from Schwab daily bars
│   └── tax-lots/          # A0004: Schwab transactions, FIFO lots, holding periods
├── journal.json           # walk-up marker for dskit.journal
└── tests/                 # green in-repo AND after graduation, uninstalled
    ├── conftest.py        # sys.path bootstrap (position-independent)
    ├── test_configs.py    # every config validates against its engine
    ├── test_connectors.py # four-verb contract + acquire→validate e2e
    ├── test_execution.py  # the venue executor's shape; the battery, one line away
    ├── test_nodes.py      # conformance suite + a document e2e
    ├── test_hold_vs_redeploy.py  # both ADR-0279 documents e2e on a synthetic onboarding root
    ├── test_hold_report.py       # the report e2e, and weekly.sh run for real on a scratch root
    └── test_production.py # the serve document validates; every template fail-closed
```

Graduation is a directory move — nothing here references its incubation
position (ADR-0021; see `children/README.md` in the dskit repo).

## Reading the verdict

Once a week, after steps 1-3 of "First real run from Schwab" (OAuth, the sources, the pulls),
from the child root:

```bash
bash weekly.sh <snapshot day YYYY-MM-DD>    # prints report/<run>.md
```

It pulls positions, runs `run-hold-vs-redeploy.json` on that day (in a temporary copy, the day
set wherever the document pins it), publishes the decisions and the risk file back through
onboarding and runs `configs/run-hold-report.json` over them. The page lists every lot, highest
opportunity cost first, named symbol / lot id (the lot id is account:symbol:acquired:ordinal):
weight, expected return, volatility, beta (the decision run's own, from its risk file),
embedded gain, tax to sell, break-even and opportunity cost a year, then HOLD or EXIT
(`n/a — no admissible alternative` when the hurdle was withheld). A holding of several lots
shows once per lot, and a young lot may hold while an old one exits. Opportunity cost above
zero: the after-tax alternative beats holding by that much a year. Break-even: the annual
return an alternative must earn, before its own cost and tax (the hurdle's basis), for
exiting to tie holding. Expected return is before tax. Embedded gain is a share of value,
not of cost. Tax to sell is a share of value; "tax to sell all" ignores loss offsets unless
`loss_offset` is true. The portfolio block gives the total (labelled "stock, ETF and mutual fund longs":
the decision document's snapshot filter, not the whole account), weighted return, volatility
and beta, concentration (HHI, top holding; lots merge by symbol), tax to sell everything and
selling all against holding all. The title day is the decisions' own `as_of`. A step that
fails stops `weekly.sh` with its output, and a pipeline run's document and run directory, and
the script exits with that run's status (1 error, 3 halted). A day already run refuses (a
run's name is its day and hash): to repeat it, delete its two run directories under
`pipeline_runs/` and the four files named for them (`decisions/`, `summary/`, `risk/`,
`report/`).

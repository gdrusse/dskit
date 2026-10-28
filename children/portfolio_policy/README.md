# portfolio_policy — a dskit child

Research child for **hold-vs-exit decisions on a long-term Schwab
portfolio**: for each position, is the expected after-tax, after-cost value
of holding passively at least the value of cashing out into an active
strategy? Status: **base machinery complete (ADR-0279), skeptic-reviewed;
position summary added (A0002), not yet lens-reviewed; no real Schwab pull
yet** — every rate in the documents is a placeholder the owner sets. The
sections after "Scoring a past decision run" are the skeleton's generic
walkthrough.

## Hold vs redeploy (ADR-0279)

From the child root, after `pip install -e .` and a Schwab OAuth session:

```bash
python -m dskit.onboarding init --root ./ob
python -m dskit.onboarding register-source schwab --root ./ob \
    --catalog-source schwab-src --connector schwab_positions --config '{}' --activate
# the OAuth values are read from the environment (never from the config): the Schwab app key
# and secret, the callback URL registered for the app, and where the token file will be saved
export SCHWAB_APP_KEY=... SCHWAB_APP_SECRET=... SCHWAB_CALLBACK_URL=... SCHWAB_TOKEN_PATH=./schwab-token.json
python -m dskit.onboarding authorize --connector schwab_positions --config '{}'    # prints the login URL
# approve it in the browser, then pass the code (or the whole callback URL it redirected to):
python -m dskit.onboarding authorize --connector schwab_positions --config '{}' --code '<code or callback URL>'
python -m dskit.onboarding acquire --root ./ob --source schwab \
    --stream positions --mode backfill            # one snapshot per UTC day
# the active strategy's backtest: ./inputs/active/returns.jsonl (the file stem is the
# stream name), one row per period: {"period": ..., "day": "YYYY-MM-DD", "net_return": ...}
python -m dskit.onboarding register-source active --root ./ob \
    --catalog-source active-src --connector localfiles \
    --config '{"path": "./inputs/active", "effective_field": "day"}' --activate
python -m dskit.onboarding acquire --root ./ob --source active --stream returns --mode backfill
mkdir -p decisions summary
# set snapshot.where[as_of] AND tax.as_of to the pull's UTC day (pinned equal by a test):
python -m dskit.pipeline run configs/run-hold-vs-redeploy.json --asof <that day>
```

`configs/run-hold-vs-redeploy.json` holds every rate, horizon and field name;
set them before the first run. Neither return is a literal: `market` builds
the hold return from dividend yield + real growth + inflation + CAPE drift and
`betas` scales it per symbol (research A0001); `hurdle` deflates the active
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
land in `decisions/<run>.jsonl`, one per account x symbol, advisory only.
Aggregate positions carry no lots, so every holding takes `default_holding`
until a lot-level export is onboarded. `decide.risk_premium` ships at 0.0 as a
placeholder, not as a claim that zero is safe: the hurdle's arithmetic mean
compounds geometrically, a gap of about sd^2 / 2 a year, so set it from the
backtest's annual volatility (`hurdle` summary `annual_sd`) before a real run.

**Limits** (disclosed, not modelled):

- Every linked account is treated as taxable; the account type is discarded, so
  an IRA or 401(k) position is taxed as if sold in a brokerage account.
- The hold path's dividends are untaxed (only its terminal gain is), which
  favours holding.
- A split or spin-off between a decision and its scoring corrupts the
  per-share growth `DecisionOutcomes` reads; it is taken for a return.
- The `EQUITY` filter's reach for ETFs and mutual funds is unverified until a
  real pull: if Schwab labels them otherwise they are dropped silently.
- Rows the filters drop (options, shorts, other asset types) are not counted or
  reported yet.

### The few numbers

The same run writes `summary/<run>.jsonl` (research A0002): one row per holding
with the numbers below, and the portfolio's own in the `summary` node's
`metrics` (`carry.json` in the run directory; `python -m dskit.pipeline runs`
tables them run over run). Rates are a year; shares are of today's value.

| Number | Plain meaning | What you do with it |
|---|---|---|
| `weight` | this row's share of the portfolio (one account's slice of a symbol) | the ~10% concentration rule is for the portfolio's `top_weight` (below), not for this |
| `expected_return` | what holding should earn a year, before tax (market blocks x beta) | compare holdings |
| `volatility` | how much it swings a year (one standard deviation) | null until daily prices are onboarded |
| `beta` | how hard it moves with the market | above 1 swings more than the market |
| `embedded_gain` | how much of the value is untaxed gain | the higher it is, the more selling costs |
| `tax_cost` | what selling today loses to tax | the price of exiting, paid once |
| `break_even_return` | the active return at which exiting exactly ties holding, after every tax and cost | exit only if you trust the strategy to beat it |
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
summary restates `decide`'s tax and cost terms, pinned equal by a test; betas
are wired from the beta table. When the hurdle is withheld,
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

`later` keeps the same equity longs the decision did (a test pins the clauses
equal), but only decided positions are read: an undecided short or option row
in the snapshot is never scored and cannot stop the run, and a decided position
the filter no longer sees is listed `unmatched`.

`<run-name>` is the decision run's directory name (the stem of its
`decisions/<run>.jsonl`). `outcomes/<run>.jsonl` holds, per account and
symbol, the price growth the decision assumed, the realized per-share growth,
and the surprise. The forecast is a total return and the snapshot's market
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
│   ├── run-hold-vs-redeploy.json   # ADR-0279: positions -> tax, market -> betas, hurdle -> decide -> summary
│   └── run-decision-outcomes.json  # ADR-0279: a past decision run scored against a later snapshot
├── decisions/             # hold-vs-redeploy rows, one file per run (gitignored)
├── summary/               # the few numbers per holding, one file per run (gitignored)
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
│   └── position-summary/  # A0002: the few numbers per position and portfolio
├── journal.json           # walk-up marker for dskit.journal
└── tests/                 # green in-repo AND after graduation, uninstalled
    ├── conftest.py        # sys.path bootstrap (position-independent)
    ├── test_configs.py    # every config validates against its engine
    ├── test_connectors.py # four-verb contract + acquire→validate e2e
    ├── test_execution.py  # the venue executor's shape; the battery, one line away
    ├── test_nodes.py      # conformance suite + a document e2e
    ├── test_hold_vs_redeploy.py  # both ADR-0279 documents e2e on a synthetic onboarding root
    └── test_production.py # the serve document validates; every template fail-closed
```

Graduation is a directory move — nothing here references its incubation
position (ADR-0021; see `children/README.md` in the dskit repo).

## Reading the verdict

Once a week, after the one-time setup above (Schwab OAuth included), from the child root:

```bash
bash weekly.sh <snapshot day YYYY-MM-DD>    # prints report/<run>.md
```

It pulls positions, runs `run-hold-vs-redeploy.json` on that day (in a temporary copy),
publishes the decisions and runs `configs/run-hold-report.json`. The page lists every
holding, highest opportunity cost first: weight, expected return, volatility, beta,
embedded gain, tax to sell, break-even and opportunity cost a year, then HOLD or EXIT
(`n/a — no admissible alternative` when the hurdle was withheld). Opportunity cost above
zero: the after-tax alternative beats holding by that much a year. Break-even: the return
an alternative must clear to tie. Tax to sell: what leaving costs today. The portfolio block
gives the total, weighted return, concentration (HHI, top holding), tax to sell everything
and selling all against holding all. Beta and volatility read `—` until the verdict is
wired into the decision document.

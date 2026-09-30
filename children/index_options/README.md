# index_options

Offline, synthetic-only index-options research scaffold. S0 proves ingestion,
instrument matching and exact expiry cashflows, **not an edge or an executable
strategy**. No models, broker, vendor adapter, real market data or serving loop.
ADR-0182 adds a separate real-data research track (Cboe index history, a chain
recorder, VIX-proxy condor backtest; see below) — still no broker or serving.

## Install and test (WSL2)

The separate ADR-0189 CDF comparison is optional research work:
`python -m pip install -e '.[cdf]'`, then
`python -m index_options.cdf_study configs/run-predictive-cdf-comparison.json`
from this child directory, with the configured archives/caches available.
The checked-in configuration explicitly requires CUDA for the MLP; a CPU run
must explicitly change its `device` knobs. Output directories refuse overwrite.
`index_options/cdf_study.py` owns exact-expiry assembly and condor diagnostics;
`dskit.pipeline.libs.predictive_cdf` owns fitting/scoring. The fixed planned
calendar ignores future exceptional closures; actual settlement remains the
outcome and reporting date. This does not authorize trading or serving.

ADR-0190 HPO uses `configs/run-predictive-cdf-hpo.json` through the same CLI:

```bash
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage search --partition separate
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage search --partition pooled
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage search --partition heads
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage select
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage evaluate --partition development
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage evaluate --partition early
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage evaluate --partition middle
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage evaluate --partition late
python -m index_options.cdf_study configs/run-predictive-cdf-hpo.json --stage report
```

ADR-0191 compares QRF, Normal-CRP NGBoost and empirical–MLP blends with the
same CLI and `configs/run-predictive-cdf-methods.json`. Search partitions are
`forest`, `ngboost` and `blend`; evaluation partitions remain `development`,
`early`, `middle` and `late`. Each invocation is externally capped at 1800
seconds/6 GiB and uses the deterministic environment declared in the config.

The additive ADR-0191 refinement uses
`configs/run-predictive-cdf-refinement.json`. Its search partitions are
`weight`, `small`, `regularized` and `mixture`; each selects one development
finalist before the same four evaluation partitions. The fixed raw 25% blend
is the incumbent. Both empirical and incumbent controls disable calibration,
deliberately duplicating raw rows; deterministic selection chooses raw. Compare
the four group finalists with that incumbent on development before any later
evaluation.
Abort a capped stage rather than reducing the declared three-component search.

The downside iteration uses configs/run-predictive-cdf-downside.json. Its
floor group tests symmetric softplus scale floors and its heads group tests
index-specific output heads; each yields one development finalist. The modeled
quantity remains dimensionless terminal log return divided by reference scale,
not a z-scored residual. An external recorded protocol freezes the primary from
the two winners and raw incumbent before evaluation; the CLI evaluates those
winners and all three controls.

ADR-0192 uses `configs/run-predictive-cdf-option-surface.json` to test causal
entry-snapshot surface features, a proper left-tail CDF score and a
calibration-only adaptive empirical–MLP gate. Search partitions are `surface`,
`tail`, `adaptive`, `surface_tail`, `surface_adaptive` and `combined`; they keep
the three isolated mechanisms distinct from their combinations. Exact actual
DTE is calibration cell-weighting metadata only and is excluded from every
predictor. Run each partition through the same CLI and external resource caps,
then `select`, the four declared evaluation partitions, and `report`.

Use `timeout 1800` around each execution partition to enforce the research cap.
An interrupted directory is incomplete and cannot be selected; preserve it and
use a new declared output root for a fresh full experiment. Do not manufacture a
completion marker or silently omit a candidate. The pooled-head HPO search has
24 neural candidates; the ADR-0192 search has 13 candidates in six groups,
both plus repeated controls. Choices use exactly 133 pre-2019 cells
(QQQ/IWM day 26 absent); combined later evaluation requires all 135. Controls
must agree across partitions. Features, task mappings, years, resolutions and
all hyperparameters live in JSON, not custom execution scripts. Cached input
paths are local and must point to the real archive/artifacts before execution.

From this child directory, use a dedicated environment with a trusted DSKIT
checkout. Replace the placeholder with that checkout's absolute path:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e /absolute/path/to/dskit -e . pytest
python -m pytest tests -q
```

The synthetic demo needs no heavy dependencies. Do not install this child into another project's active
environment. After graduation, the same child and installed DSKIT suffice;
there is no import from a parent repository path.

## Temporary public-CLI demo

With that environment active, start in the child root. This uses only original
synthetic fixtures; every demo gets a fresh store, cursor and journal.

```bash
(
set -euo pipefail
index_options_child="$PWD"
index_options_demo=$(mktemp -d)
cp -R configs fixtures "$index_options_demo/"
cd "$index_options_demo"
export DSKIT_JOURNAL_ROOT="$index_options_demo"
python -m dskit.journal init --root .
python -m dskit.onboarding init --root ./ob
python -m dskit.onboarding register-source index-fixture \
  --catalog-source index-fixture-src --connector localfiles \
  --config @configs/source-fixture.json --activate --root ./ob
for stream in contracts quotes settlements; do
  acquired=$(python -m dskit.onboarding acquire --source index-fixture \
    --stream "$stream" --mode backfill --root ./ob)
  snapshot=$(python -c 'import json,sys; x=json.load(sys.stdin); assert x["records"] > 0; print(x["snapshot"])' <<< "$acquired")
  python -m dskit.onboarding validate --suite configs/suite-fixture.json \
    --snapshot "$snapshot" --root ./ob
done
python -m dskit.pipeline run configs/run-fixture.json --asof 2026-02-21
python - <<'PY'
import json
from pathlib import Path
from dskit.pipeline.driver import resolve_json_artifact
path = next(Path("pipeline_runs").glob("**/nodes/*-diagnostic.json"))
record = json.loads(path.read_text())
report = resolve_json_artifact(str(path.parent.parent), record["outputs"]["report"])
print(json.dumps(report, indent=2))
assert report["net_pnl_usd"] == "252"
PY
unset DSKIT_JOURNAL_ROOT
cd "$index_options_child"
)
```

The demo directory is retained for inspection. Field validation must pass on
each snapshot; expected counts are contracts=4, quotes=4, settlements=3. The
suite checks row fields, not presence of the other streams in a one-stream
snapshot. The complete automated demo asserts those counts, pass results,
artifact digest, output and real journal recording:

```bash
python -m pytest tests/test_integration.py::test_public_cli_round_trip_and_positive_journal_isolation -q
```

## Interfaces and limits

- LocalFilesConnector supplies acquisition. source-fixture.json names the path
  and effective-time field; no child connector exists.
- ContractRows, QuoteRows and SettlementRows retain only root, source and the
  optional as_of_acquisition_ms from ObservationRows. Fixed stream, revision
  keys and timestamp accessors are tested. All three are forbidden for serving.
- CondorPayoffDiagnostic takes three record lists and exact corpus/leg/quote/
  settlement references, positive count, and whole-outcome fees_usd. Its report
  is a JsonArtifact persisted by the parent driver. It too is forbidden for
  serving. Decimal strings are required for prices, strikes, levels and fees;
  multiplier/count/quantities are integers, never booleans.
- The report is synthetic_ex_post_diagnostic and decision_eligible=false.
  Long legs pay ask; short legs receive bid. This is a hypothetical package,
  not evidence of a fill. Settlement is deliberately an ex-post outcome.
- Quotes share one instant, precede last trade, and cover every leg's size.
  Contracts explicitly declare the settlement instant; no exchange calendar
  is inferred. The glossary gives the cashflow arithmetic.
- Readers use the parent's latest-eligible-acquisition winner per revision key;
  identical winning repeats pass, conflicting winning ties refuse. Every
  winning row is domain-validated. Direct selected references must be unique.
  Suite results are independent evidence, not a hidden reader gate.
- Event time, known_at and actual acquired_at are different clocks. Acquisition
  cutoff filters only the last. Fresh-root pulls are mandatory for this demo;
  strict effective-time cursor resumption does not capture backdated revisions.
- Fingerprint and execution share the parent's frozen snapshot. A new instance
  reads new bytes. No defense against hostile Python controlling the interpreter
  is claimed.

- Distribution harness (ADR-0168, synthetic only): `configs/run-synthetic-distribution.json`
  wires dskit's GJR path, realized-vol features, horizon label, a sample-set
  model and proper scores to `CondorDistributionReport`, which evaluates a
  condor declared in standardized strikes (`strikes_z`) under each forecast.
  Run `python -m dskit.pipeline walkforward configs/run-synthetic-distribution.json --asof 1978-06-01`
  (journal initialized). Swap the `model` node to test another rung; never
  decision-eligible.
- Distribution zoo (ADR-0181): `run-synthetic-har.json` (log-HAR scale) and
  `run-synthetic-lightgbm.json` (needs the `lightgbm` extra) differ from the
  harness only in `model`; `python -m dskit.pipeline staged
  configs/run-distribution-zoo.json --asof 1978-06-01` plans them, waits for
  the pasted inventory hash in `approval`, then runs and compares all three.
- Put spread, wing split and delta benchmark (ADR-0193, offline, never
  decision-eligible): `PutSpreadQuoteBacktest` is `CondorQuoteBacktest` on the
  put wing alone (same knobs, books and walk; two legs of fees; American put
  carry only; it enters where the condor's call side is unquotable). Every
  entered cell of either backtest carries `side_pnl_usd` (`put`/`call`, summing
  to `pnl_usd`; metrics `<book>_<side>_{total_pnl_usd,mean_pnl_usd,hit_rate}`)
  and a delta-matched stock benchmark (`delta`, `equity_pnl_usd`; metrics
  `<book>_delta_equity_total_usd`, `_residual_mean_pnl_usd`, `_residual_t`,
  `_pnl_t`, `_n_no_delta`; Black-76 hedge ratio at rate 0, an approximation
  that ignores dividends and early exercise). The 14 SPY/QQQ cells ship
  generated `configs/grid/<cell>-put-spread.json` (`short_q` 0.16, `wing_z`
  0.65). Run: `python -m dskit.pipeline walkforward configs/grid/spy-30-45-put-spread.json --asof <today>`.

## Real data: Cboe pull, chain recorder, zoo and VIX-proxy backtest (ADR-0182)

Cboe serves daily SPX/VIX history and a 15-minute-delayed option chain with no
credential (`dskit.onboarding.libs.cboe:CboeConnector`). Two sources, because
the pack's knobs are per source: `cboe-index` (`index_daily`, SPX + VIX) and
`cboe-chain` (`option_chain`, SPX + XSP, roots SPXW/XSP, 70 DTE). The run
configs read `./ob` relative to the working directory; keep the store durable
outside the repo and symlink it (`ob/` is git-ignored and outside the manifest).

```bash
# once: durable store, both sources registered and active
mkdir -p ~/data/index_options
python -m dskit.onboarding init --root ~/data/index_options/ob
python -m dskit.onboarding register-source cboe-index --catalog-source cboe-index \
  --connector cboe \
  --config @configs/source-cboe-index.json --activate --root ~/data/index_options/ob
python -m dskit.onboarding register-source cboe-chain --catalog-source cboe-chain \
  --connector cboe \
  --config @configs/source-cboe-chain.json --activate --root ~/data/index_options/ob
ln -s ~/data/index_options/ob ob

# pull (repeat any day to extend the history; the cursor resumes)
python -m dskit.onboarding acquire --source cboe-index --stream index_daily \
  --mode backfill --root ./ob

# recorder: schedule twice per trading day, 15:50 and 16:20 ET (cron/Task Scheduler)
python -m dskit.onboarding acquire --source cboe-chain --stream option_chain \
  --mode live --root ~/data/index_options/ob

# one rung (walk-forward: yearly val folds 1995 -> the pull date)
python -m dskit.journal init --root .     # once per working directory
python -m dskit.pipeline walkforward configs/run-real-har.json --asof 2026-09-23

# the zoo: first call plans only; paste the printed inventory sha256 and
# approved_by into run-real-zoo.json's approval stage, then run it again
python -m dskit.pipeline staged configs/run-real-zoo.json --asof 2026-09-23
```

- `IndexCloseRows` projects one symbol of `index_daily` into the envelope the
  feature nodes read; the configs key the VIX rows by date with dskit's `keyby`
  (node `vix_by_date`) and join them onto SPX with dskit's `join` (`how: left`),
  so a pre-1990 row keeps `iv_index` null.
- `CondorBacktest` (node `backtest`) reads each rung's forecast rows over val:
  one condor every `hold_steps` = 21 rows (the label horizon, non-overlapping),
  settled at `close x exp(label)`. Books `model` (forecast quantile strikes,
  entered when forecast expected P&L at proxy prices beats `min_edge_usd`),
  `always` (same strikes, every entry) and `implied` (VIX-lognormal strikes).
  Metrics are flat (`model_total_pnl_usd`, `implied_cvar_usd`, ...) per fold
  in `carry.json`; the report holds the per-trade ledger.
- Pricing is `vix_proxy` (dskit's `option_pricing.VolIndexSmileQuotes` at the
  VIX close: Black-76 on a calibrated smile clamped to [5%, 200%] IV, plus a
  half-spread), never a quote, so the report is never decision-eligible;
  absolute P&L is indicative and the model/always/implied comparison on
  identical prices is the signal. Recorded chains later calibrate the proxy.

[Explanation](docs/explanations/README.md) defines the instrument and example.
[Plan](docs/plans/README.md) describes the separately gated data/ML/MIO work.


### Historical end-of-day chains: options-dataset-hist (ADR-0182 amendment)

The MIT-licensed archive (SPY 2008-2025, QQQ 2011-2025, IWM 2008-2025; one
row per contract per day at the 16:00 ET close) enters the same
`option_chain` stream as the recorder through dskit's `optionshist` pack.
`configs/source-optionshist-chain.json` pins the mirror's commit and every
file's sha256; the pack refuses an unpinned, missing or changed file.

```bash
git clone https://github.com/anahatsingh-ui/options-dataset-hist.git   ~/data/options_archives/philippdubach_full
git -C ~/data/options_archives/philippdubach_full checkout 37f6c456fe1a4775c875673fb8ef907d5cd2fd66
python -m dskit.onboarding register-source optionshist-chain   --catalog-source optionshist-chain --connector optionshist   --config @configs/source-optionshist-chain.json --activate --root ~/data/index_options/ob
# each pull emits at most max_days (63) trading days after the cursor;
# repeat until a pull reports 0 records
python -m dskit.onboarding acquire --source optionshist-chain --stream option_chain   --mode backfill --root ~/data/index_options/ob
```

`quote_time` is the date's 16:00 New York close in UTC (early closes too);
`underlying_price` is the archive's daily close (None for SPY 2024-01-15, a
holiday with two stray rows); `last_trade_time` is None; the archive's
`in_the_money` column is wrong and never read. `acquired_at`, not
`quote_time`, is when this history became known.

The same source's second stream, `index_daily` (ADR-0187), is the archive's
`underlying_prices.parquet`: one row per (symbol, date) with the raw close,
`dividend_amount` and `split_coefficient`. Pull it once per source:

```bash
python -m dskit.onboarding acquire --source optionshist-chain --stream index_daily   --mode backfill --root ~/data/index_options/ob
```

### Multi-horizon ETF grid, archived-quote backtest, zoo and HPO (ADR-0187)

A cell is one underlying (SPY, QQQ, IWM) and one days-to-settlement bucket
({1}, {2,3}, {5}, {7..10}, {14}, {21}, {30..45}; no 0DTE). `configs/grid/`
holds one `har-vix` walk-forward document per cell (`<symbol>-<bucket>.json`),
generated from `run-real-har-vix.json` by `index_options.grid`: the
`underlying` reader (`IndexCloseRows` over the archive's `index_daily`, read
from the first row after the last split), the bucket's label horizon in
`labels`/`fwd` and `scale_multiplier = sqrt(h)`, yearly folds from the first
year the archive lists the underlying, and an embargo of `dte_max + 7` days.
SPY and QQQ cells add `chain` (`ChainQuoteRows`: that symbol's 16:00 ET quotes
bounded at intake to the bucket, the 0.5 strike grid and a log-moneyness band,
parsed once per process) and `backtest` (`CondorQuoteBacktest`: the nearest
listed expiry, strikes snapped outward onto quotable listed strikes, the
implied book from the chain's ATM iv, settlement on the last close within four
days of the settlement date, and a conservative early-exercise charge —
dividends on an in-the-money short call, carry on an in-the-money short put —
because ETF options are American). IWM has no dividend source, so its cells
run labels, forecasts and scores only. A fold in which nothing can enter
records zero trades with every reason counted; the two plumbing refusals are
a split with no forecast rows and an empty chain.

```bash
# the cell documents are generated; edit the base rung or the grid table, then
python -c "from index_options.grid import write_grid; write_grid('configs')"
python -m dskit.pipeline walkforward configs/grid/spy-30-45.json --asof <today>
# every cell: the other rungs, a zoo over the four, and per-fold HPO (owner
# question 4, expanded 2026-09-27 to run on all 21 cells, not one)
python -m dskit.pipeline staged configs/grid/spy-30-45-zoo.json --asof <today>
python -m dskit.pipeline walkforward configs/grid/spy-30-45-hpo-har-vix.json --asof <today>
```

The zoo is the ADR-0097 protocol (plan-only first; paste the inventory sha256
into `approval`). A zoo candidate may carry no search node, so tuning is its
own document: `spy-30-45-hpo-<rung>.json` runs `hpo-grid` over the rung's own
knobs (`model.ridge_alpha`; `model.lgbm_params.*`) with the val twCRPS as the
objective, re-tuned per fold — a measurement of the tuning procedure
(ADR-0043). Ship a winner by pinning it into the cell document.
`index_options.grid.cell_files(configs_dir, cell)` writes the same set for
any other cell. Before the grid runs, measure one bounded `ChainQuoteRows` scan
on the finished ingest (ADR-0187 step 0: at most 30 minutes and 6 GB).

## Layout

```text
pyproject.toml; .gitignore; README.md; AGENTS.md; CLAUDE.md
journal.json
index_options/             # __init__.py, contracts.py, observations.py, nodes.py,
                           # distribution.py (condor under a forecast, ADR-0168);
                           # grid.py (the ADR-0187 cell table + document generator);
                           # pricing, tail mean and drawdown are dskit's (ADR-0182)
configs/                   # source-fixture.json, suite-fixture.json, run-fixture.json,
                           # run-synthetic-distribution.json (ADR-0168 harness),
                           # run-synthetic-har/-lightgbm.json + run-distribution-zoo.json (ADR-0181),
                           # source-cboe-index/-chain.json, run-real-distribution/-har/-lightgbm.json
                           # + run-real-zoo.json (ADR-0182); run-real-vix/-har-vix/
                           # -lightgbm-vix.json (VIX as a scale feature); source-cboe-chain-wide/
                           # -index-wide.json (wide recorder + vol indices)
                           # source-optionshist-chain.json (EOD SPY/QQQ/IWM chain archive,
                           # sha256-pinned, ADR-0182 amendment)
configs/grid/              # ADR-0187, generated: 21 cell documents <symbol>-<bucket>.json
                           # (har-vix) + every cell's <symbol>-<bucket>-{empirical,vix,
                           # lightgbm-vix,zoo,hpo-har-vix,hpo-lightgbm-vix}.json (owner
                           # question 4, expanded 2026-09-27 from the one worked cell)
                           # + <symbol>-<bucket>-put-spread.json for the 14 SPY/QQQ cells (ADR-0193)
fixtures/                  # contracts.jsonl, quotes.jsonl, settlements.jsonl
docs/decisioning/           # actions.csv, owner path.csv, generated README.md
docs/explanations/README.md # glossary and worked synthetic payoff
docs/plans/README.md        # gated research stages
docs/memos/README.md        # execution-evidence convention; 2026-09-24 real-data closeout
docs/research/              # README.md, .gitkeep; distribution-modeling/, real-data-backtest/ notes
tests/                     # conftest.py; test_contracts, observations, nodes,
                           # configs, integration, distribution,
                           # synthetic_distribution_run, distribution_zoo, real_data,
                           # quote_backtest (.py)
```

Journal infrastructure starts empty. Only the human owner changes Path or
Current Work. The generated decisioning README shows the full Path and latest
10 Actions; actions.csv retains append-only history. Never hand-edit generated
files. Tests and the demo journal in isolated temporary directories.

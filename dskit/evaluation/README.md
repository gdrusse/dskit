# dskit.evaluation

A centralized backtest evaluator (ADR-0183). A backtest writes one
append-only **event log** (schema `dskit-eval-v1`); the report is a pure
function of it, so any past run re-renders from its `events.jsonl` alone.

```bash
python -m dskit.evaluation render run/events.jsonl --out run/report
```

writes `events.jsonl`, `summary.md` (a plain-English "What happened"
paragraph and a "How to read" note, then card, verdict, statistics,
census, top refusal reasons, provenance), `report.html` (one
self-contained file: inline CSS + SVG, no script, no URL), `decisions.csv`
and `trades.csv`.

Reading the report: trading P&L (net and gross) is the primary chart and
never includes deposits; account equity with each cash flow labelled is
drawn second. Time axes collapse market-closed gaps to a thin break with
the next session's date. The decision log leads with a summary by
(action, reason) and the most consequential decisions; the full log sits
in a collapsed table and, with every raw column, in `decisions.csv`.
Busy trades-on-price panels are thinned deterministically (every
refusal/skip and the largest wins and losses always drawn) and say so.

Phase 2 adds three readings. **Optimizer**: every `solve` event (status,
termination, objective, gap, seconds, how often each constraint bound).
**Inference diagnostics**: each candidate's score against its `outcome`
— calibration by score decile, hit rate per bucket, rank IC per instant
and per day; the only section that reads outcomes. **Guard findings**:
an order's `findings` (the production ledger's pre-trade checks) show as
the decision log's `guard_verdict` column and a (guard, verdict) summary.

## Writing the log

One JSON object per line. Envelope: `schema`, `seq` (strictly increasing),
`kind`, `ts_ms` (UTC epoch ms, never decreasing), `known_ms` (when the
information existed; `<= ts_ms` for a decision), `instrument` (or null).
Unknown fields are refused, every problem listed at once.

| kind | body |
|---|---|
| `run_start` | `run_id`, `tz` (IANA, display only); optional `title`, `project`, `config_hash`, `config`, `code`, `data`, `env` (`RunStart.capture_env()`), `criteria`, `trials`, `units` (`{"score": "return" \| "log_return" \| "bp" \| "prob" \| "usd" \| "z" \| "raw", "money": "USD"}`), `sources` (where filled provenance came from), `signed_prices` (flag: a fill may carry a negative price) |
| `decision` | `decision_id`, `action` (`enter/exit/hold/skip/refuse`), `reason`; optional `candidates` (`instrument, score, rank, eligible, reason`), `chosen`, `threshold`, `edge`, `detail`, `model` |
| `order` | `order_id`, `side`, `qty`; optional `decision_id`, `ref_price`, `legs`, `findings` (guard checks: `guard, measure, value, bound, verdict, reason, window, scope_key` — shown in the decision log) |
| `refusal` / `skip` | `reason` plus `decision_id` and/or `order_id`; optional `detail` |
| `fill` | `fill_id`, `side`, `qty`, `price` (0 is allowed: an option that expires worthless closes at 0; negative only under `run_start.signed_prices`); optional `order_id`, `fee`, `ref_price`, `tag` |
| `mark` | `price` |
| `cashflow` | `amount` (+ in, - out); optional `rule`, `detail` |
| `outcome` | `decision_id`, `realized`, per candidate `instrument`; optional `horizon` (read only by `InferenceSection`, never beside its decision) |
| `solve` | `solver`, `status`; optional `termination`, `objective`, `bound`, `gap`, `seconds`, `variables`, `constraints`, `model`, `binding` (`name, rows, binding, min_slack, dual`; the body of `libs.pyomo.SolveRecord.to_obj()`, rendered by `OptimizerSection`) |
| `run_end` | `status`; optional `wall_s` |

`EventLog.emit(kind, ts_ms, known_ms, instrument, **fields)` assigns `seq`
and the schema tag. Criteria are pre-registered in `run_start`:
`{name, stat, op, value, min_n}` over the names in
`statistics.STAT_NAMES`; the verdict is PASS / FAIL, or INCONCLUSIVE below
`min_n` or when the statistic is undefined.

## In a pipeline

Two nodes, both by dotted path. `RowsToEvents` maps the row lists a
backtest already emits — no project code — and `EvaluationReport` renders:

```json
"events": {
  "uses": "dskit.evaluation.nodes:RowsToEvents",
  "inputs": {"fills": "$backtest.fills", "marks": "$bars.records"},
  "params": {
    "run_start": {"tz": "America/New_York", "title": "Backtest",
                  "units": {"score": "return", "money": "USD"}},
    "map": {
      "fills": {"instrument": "symbol", "fields": {
        "fill_id": {"template": "f-{symbol}-{asof_ms}"},
        "side": "side", "qty": "qty", "price": "price", "fee": "fee"}},
      "marks": {"instrument": "symbol", "fields": {"price": "close"}}}}
},
"report": {
  "uses": "dskit.evaluation.nodes:EvaluationReport",
  "inputs": {"events": "$events.events"},
  "params": {"out_dir": "report", "sections": ["equity", "trades", "decisions"]}
}
```

Ports: `decisions`, `candidates` (grouped into their decision by
`decision_id`), `orders`, `refusals`, `skips`, `fills`, `marks`,
`cashflows`, `outcomes`, `solves`. Per port: `ts` (row field of the
instant, default `asof_ms`), `known`, `instrument`, `explode` (fan out a
list or dict field), and `fields` — each a row field name,
`{"const": v}` or `{"template": "..."}`; targets are the kind's own
schema fields, and a required one left out is refused at plan time.
Rows needing a domain join (which fill answers which decision) are
shaped upstream (`derive`, `join`) or by a project mapper
(`intraday_equities.evaluation.ReplayEvents`). `sections` names from
`summary, overview, equity, trades, decisions, optimizer, cash,
distributions, inference, periods, provenance`; `summary` and
`provenance` always render (CLI: `--sections a,b`).

```json
"report": {
  "uses": "dskit.evaluation.nodes:EvaluationReport",
  "inputs": {"events": "$replay_events.events"},
  "params": {"out_dir": "evaluation", "title": "Replay"}
}
```

A relative `out_dir` resolves against the RUN directory, so `"report"`
lands at `pipeline_runs/<run>/report/`; one beginning with
`pipeline_runs` is refused (it would nest a second runs directory). The
node fills a missing `code` (git commit + dirty of the run directory's
repository), `env` and `wall_s` and records each in `run_start.sources`.
`metrics` carries the verdict and key statistics to the tracking sinks.

Declare `units` in `run_start`: scores (forecasts, edges, thresholds) then
print in their unit — a return in basis points — and money with its
currency sign. The CSVs keep raw values and a `score_unit` column.

## Gating a report on its totals

`dskit.evaluation.gate:EventTotalsGate` (role `transform`) sits between the
events and `EvaluationReport`. It folds the events through the one P&L fold
and compares `net_pnl`, `final_equity`, `contributions`, `realised` or `fees`
to values wired on its `expected` input (for instance a pinned file's own
numbers, never typed into the document), each within a tolerance the
document declares; `fill_count`, `refusal_count` and `decision_count`
compare event counts the same way. A miss raises with every miss listed, so the report node
never runs and no file is written (a `GateRefusal`, under its own header, not
an "invalid event log"); otherwise `events` pass through
untouched and `comparison` lists each check.

```json
"gate": {
  "uses": "dskit.evaluation.gate:EventTotalsGate",
  "inputs": {"events": "$events.events", "expected": "$rows.expected"},
  "params": {"checks": {
    "net_pnl": {"expected": "pnl", "tolerance": 0.005},
    "final_equity": {"expected": "final_equity", "tolerance": 0.005}}}
}
```

**Tables.** `EvaluationReport` takes an optional `tables` param,
`{anchor: {title, columns, rows, formats?, note?}}`; `rows` is usually a
`"$node.port"` reference to rows an upstream node computed (a `groupby` of
fills by ticker and DTE band, say). Each table is a `TableSection` shown
before provenance and written as `<anchor>.csv`.

**Labelling a report whose basis differs.** `EvaluationReport` (and
`BacktestReport`) take three optional display params; absent, every file is
byte-identical to before. `caveats` (list of non-empty str) prints a banner
under the title of `summary.md` and `report.html` and again above the
Statistics table. `statistic_labels` (`{name in STAT_NAMES: label}`; an
unknown name is refused) replaces the name shown in the Statistics table
only. `narrative_driver` (bool, default true) false drops "Biggest driver"
from "What happened"; with no candidate score in the log that clause never
credits "the forecasts" and says net = gross less fees instead.

**Zero and negative fill prices.** A `fill` price is any finite number: an
option expiring worthless closes at 0. A NEGATIVE price (a spread bought for
a credit) is refused by the log unless `run_start.signed_prices` is true, so
an ordinary log's sign bug is still caught.

## Linking another run's outputs

`dskit.evaluation.links:ArtifactLinks` (role `report`) lists the expected
files of a different run in a finished report. Wire it after
`EvaluationReport` with `"inputs": {"paths": "$report.paths"}`:

```json
"stress_links": {
  "uses": "dskit.evaluation.links:ArtifactLinks",
  "inputs": {"paths": "$report.paths"},
  "params": {"root": "/abs/other-run",
             "files": [{"id": "report", "path": "report.md"},
                       {"id": "daybook", "path": "{case}/{unit}.md",
                        "axes": {"case": ["a", "b"], "unit": ["x"]}}],
             "pins": {"file": "pins.json", "files_key": "files"},
             "table": {"file": "summary.csv", "where": {"cohort": "c1"},
                       "case": "scenario", "unit": "method",
                       "cases": ["control"], "units": ["m1"],
                       "ok": {"column": "status", "equals": "complete"},
                       "checks": {"gate": ["control"]}}}}
```

- **Pending, never made up.** Each link shows its path, SHA-256 and byte
  count when the file exists and `pending` (no hash, no size) when it does not;
  a broken symlink is pending too.
- **Read-only on the root.** Files there are only opened for reading. A
  report directory inside the root (or the root inside it), a symlink that
  resolves outside the root, a `csv_name` that is not a bare `<name>.csv`, or
  one that collides with a report file, is refused.
- **Pins.** With `pins`, each link says `match`, `mismatch`, `unpinned` or
  `pending` against the pins file's `files_key` mapping.
- **Table checks.** `table` reads one CSV grid; each named check is
  `complete` (every cell ok, and the pins file confirms the table file),
  `incomplete`, `unverified` (pins contradict or omit the table file) or
  `pending` (table or pins file not written yet).
- **Output.** A marked block in `summary.md` and `report.html`, then
  `<csv_name>` (default `artifact-links.csv`) and `<csv_name>-cells.csv`
  beside the report. The node splices into the report files that
  `EvaluationReport` wrote in the same run, so a rerun needs a new asof or run
  directory (the report node rewrites its own files; the block would
  otherwise be replaced, never stacked).

## Extending

- A new section: subclass `sections.Section`, set `title`/`anchor`,
  implement `html(context)` (optionally `markdown(context)`), and pass the
  list to `BacktestReport(log, sections=[...])`.
- A table a document computed upstream (a `groupby` of fills by ticker and
  DTE bucket, say): `sections.TableSection.from_obj(anchor, {"title",
  "columns", "rows", "formats"})` in `BacktestReport(sections=[...])`
  shows it in `report.html` and writes `<anchor>.csv` beside the report
  (any section may add files through `Section.files`).
- A new chart: subclass `svg.XYChart` and implement `path(...)`, or
  `svg.Chart` and implement `body()`.
- A new estimator belongs in `dskit/pipeline/stats.py`, then a row in
  `statistics.py`.

## Contents

```
dskit/evaluation/
├── __init__.py    public surface; registers nothing
├── __main__.py    python -m dskit.evaluation render <events.jsonl> --out <dir>
├── events.py      schema v1 kinds, EventLog (order/look-ahead/reference checks),
│                  Links (decision -> order -> fill), Census, LocalTime
├── book.py        EvaluationBook: WindowBook-backed equity/cash/exposure,
│                  FIFO round trips, per-day rows, TWR via PerformanceCalculator
├── statistics.py  StatisticsTable over STAT_NAMES; SolveSummary; estimators
│                  from pipeline.stats
├── mapping.py     EventMapping / RowMap / FieldSource: rows -> events from JSON
├── diagnostics.py ForecastDiagnostics: candidate score x outcome pairs, deciles,
│                  hit rate by bucket, rank IC (pipeline.ordering)
├── criteria.py    Criterion / Verdict / Scorecard (PASS, FAIL, INCONCLUSIVE)
├── names.py       is_bare_file_name: the one bare-file-name rule (report, links)
├── units.py       number display: money, counts, ratios, declared score units
├── narrative.py   Narrative: the rule-written "What happened"; HOW_TO_READ
├── provenance.py  git_revision, fill_provenance (code/env/wall_s + sources),
│                  run_dir_provenance
├── svg.py         stdlib SVG: LineChart, StepChart, MarkerLayer, SessionScale
│                  (market-closed gaps collapsed), BarChart, Histogram,
│                  downsample; CSS-variable palette, light/dark
├── sections.py    Section ABC + Overview, Summary, Equity, TradesOnPrice,
│                  DecisionLog (+ guard findings), Optimizer, Cash,
│                  Distribution, Inference, Period, Provenance;
│                  TableSection (rows -> HTML table + <anchor>.csv)
├── report.py      BacktestReport -> the five files (+ section files), atomically
├── nodes.py       EvaluationReport (role report, `sections`, `tables`) + RowsToEvents
│                  (role transform) pipeline nodes
├── gate.py        EventTotalsGate node (role transform): events' totals vs expected,
│                  or refuse before any report is written
├── links.py       ArtifactLinks node (role report): another run's files by path +
│                  SHA-256, `pending` if absent, read-only on that root
├── README.md      this file
├── AGENTS.md      agent orientation
└── CLAUDE.md      identical to AGENTS.md
```

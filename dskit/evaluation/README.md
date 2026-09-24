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

## Writing the log

One JSON object per line. Envelope: `schema`, `seq` (strictly increasing),
`kind`, `ts_ms` (UTC epoch ms, never decreasing), `known_ms` (when the
information existed; `<= ts_ms` for a decision), `instrument` (or null).
Unknown fields are refused, every problem listed at once.

| kind | body |
|---|---|
| `run_start` | `run_id`, `tz` (IANA, display only); optional `title`, `project`, `config_hash`, `config`, `code`, `data`, `env` (`RunStart.capture_env()`), `criteria`, `trials`, `units` (`{"score": "return" \| "log_return" \| "bp" \| "prob" \| "usd" \| "z" \| "raw", "money": "USD"}`), `sources` (where filled provenance came from) |
| `decision` | `decision_id`, `action` (`enter/exit/hold/skip/refuse`), `reason`; optional `candidates` (`instrument, score, rank, eligible, reason`), `chosen`, `threshold`, `edge`, `detail`, `model` |
| `order` | `order_id`, `side`, `qty`; optional `decision_id`, `ref_price`, `legs` |
| `refusal` / `skip` | `reason` plus `decision_id` and/or `order_id`; optional `detail` |
| `fill` | `fill_id`, `side`, `qty`, `price`; optional `order_id`, `fee`, `ref_price`, `tag` |
| `mark` | `price` |
| `cashflow` | `amount` (+ in, - out); optional `rule`, `detail` |
| `outcome` | `decision_id`, `realized`; optional `horizon` (never rendered beside its decision) |
| `solve` | `solver`, `status`; optional `objective`, `bound`, `gap`, `binding`, `seconds` (phase 2) |
| `run_end` | `status`; optional `wall_s` |

`EventLog.emit(kind, ts_ms, known_ms, instrument, **fields)` assigns `seq`
and the schema tag. Criteria are pre-registered in `run_start`:
`{name, stat, op, value, min_n}` over the names in
`statistics.STAT_NAMES`; the verdict is PASS / FAIL, or INCONCLUSIVE below
`min_n` or when the statistic is undefined.

## In a pipeline

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

## Extending

- A new section: subclass `sections.Section`, set `title`/`anchor`,
  implement `html(context)` (optionally `markdown(context)`), and pass the
  list to `BacktestReport(log, sections=[...])`.
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
├── statistics.py  StatisticsTable over STAT_NAMES; estimators from pipeline.stats
├── criteria.py    Criterion / Verdict / Scorecard (PASS, FAIL, INCONCLUSIVE)
├── units.py       number display: money, counts, ratios, declared score units
├── narrative.py   Narrative: the rule-written "What happened"; HOW_TO_READ
├── provenance.py  git_revision, fill_provenance (code/env/wall_s + sources)
├── svg.py         stdlib SVG: LineChart, StepChart, MarkerLayer, SessionScale
│                  (market-closed gaps collapsed), BarChart, Histogram,
│                  downsample; CSS-variable palette, light/dark
├── sections.py    Section ABC + Overview, Summary, Equity, TradesOnPrice,
│                  DecisionLog, Cash, Distribution, Period, Provenance
├── report.py      BacktestReport -> the five files, atomically
├── nodes.py       EvaluationReport pipeline node (role report)
├── README.md      this file
├── AGENTS.md      agent orientation
└── CLAUDE.md      identical to AGENTS.md
```

# dskit.evaluation

A centralized backtest evaluator (ADR-0183). A backtest writes one
append-only **event log** (schema `dskit-eval-v1`); the report is a pure
function of it, so any past run re-renders from its `events.jsonl` alone.

```bash
python -m dskit.evaluation render run/events.jsonl --out run/report
```

writes `events.jsonl`, `summary.md` (card, verdict, statistics, census,
provenance, top refusal reasons), `report.html` (one self-contained file:
inline CSS + SVG, no script, no URL), `decisions.csv` and `trades.csv`.

## Writing the log

One JSON object per line. Envelope: `schema`, `seq` (strictly increasing),
`kind`, `ts_ms` (UTC epoch ms, never decreasing), `known_ms` (when the
information existed; `<= ts_ms` for a decision), `instrument` (or null).
Unknown fields are refused, every problem listed at once.

| kind | body |
|---|---|
| `run_start` | `run_id`, `tz` (IANA, display only); optional `title`, `project`, `config_hash`, `config`, `code`, `data`, `env` (`RunStart.capture_env()`), `criteria`, `trials` |
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

Relative `out_dir` lands under the run directory; `metrics` carries the
verdict and key statistics to the tracking sinks.

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
├── svg.py         stdlib SVG: LineChart, StepChart, MarkerLayer, BarChart,
│                  Histogram, downsample; CSS-variable palette, light/dark
├── sections.py    Section ABC + Summary, Equity, TradesOnPrice, DecisionLog,
│                  Cash, Distribution, Period, Provenance
├── report.py      BacktestReport -> the five files, atomically
├── nodes.py       EvaluationReport pipeline node (role report)
├── README.md      this file
├── AGENTS.md      agent orientation
└── CLAUDE.md      identical to AGENTS.md
```

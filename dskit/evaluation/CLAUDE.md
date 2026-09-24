# CLAUDE.md — `dskit/evaluation`

Orientation for an agent working inside the evaluator. Repo-wide rules live
in the root `CLAUDE.md`; the package README says what it does.

## The import rule (enforced by `tests/evaluation/test_purity.py`)

stdlib + `dskit.pipeline` + `dskit.production` + itself, at any depth.
Tier-2 packs (e.g. a matplotlib backend) only under `libs/`, which does not
exist yet. `dskit.pipeline` and `dskit.production` never import this
package. Importing it registers nothing; the node is used by dotted path.

## Conventions that bite

- **One P&L fold.** Equity and realised P&L come from
  `production.accounting.WindowBook`; `book.py` only adapts fills to its
  shape. Never add a second realised/unrealised computation. FIFO round
  trips are attribution; they equal the realised total only when flat.
- **Reuse, never restate.** Estimators live in `pipeline/stats.py`
  (Sharpe, PSR, DSR, profit factor, payoff, `across_fold_t`,
  `lower_tail_mean`, `max_drawdown`); TWR is `production.report.
  PerformanceCalculator`; canonical JSON is `production.base.
  canonical_bytes`; writes are `pipeline.node.atomic_write`; CSV is
  `pipeline.kinds_report.csv_text`; markdown cells are
  `pipeline.runs.render_cell`; runtime capture is
  `production.release.RuntimeFingerprint`.
- **Default-deny events.** A kind declares its `FIELDS`; anything else is
  refused with every problem listed. A new REQUIRED field or a changed
  meaning is a schema change (`SCHEMA`); an optional additive field
  (`run_start.units`, `run_start.sources`) is not.
- **Numbers display through `units.py`.** Money, counts, ratios, percents
  and declared score units have one formatter each; the CSVs stay raw.
- **Market-closed time is not drawn.** Timed charts default to
  `svg.SessionScale`; pass `gap_ms=False` only for a truly continuous axis.
- **The narrative is rules, not prose generation.** Add a sentence as a
  new `Narrative._rule` over numbers the report already shows.
- **No look-ahead in a render.** A section showing a decision never reads
  an `outcome` event; `InferenceSection` (via `diagnostics.py`) is the one
  reader and shows aggregates only. `decisions.csv` is pinned identical
  with and without outcomes.
- **Nothing silently dropped.** The census reports unaccounted decisions
  and orders; a capped render says what it cut and where the rest lives.
- **Charts are classes, colours are CSS.** SVG elements carry classes;
  `svg.CHART_CSS` maps them to variables with a dark-mode block. The HTML
  must stay free of any URL (a test asserts it).
- **Docstrings**: NumPy sections, `Examples` with `::` blocks, no type
  hints in signatures. `ruff` runs `D` here.

## Extension points

`sections.Section` (subclass, pass to `BacktestReport(sections=...)`),
`svg.XYChart.path` / `svg.Chart.body`, `events.Event` subclasses (a new
kind also needs an `EVENT_KINDS` entry and a schema bump), and
`statistics.StatisticsTable` rows (add the name to `STAT_NAMES`).

## Contents

```
dskit/evaluation/
├── __init__.py    public surface; registers nothing
├── __main__.py    python -m dskit.evaluation render <events.jsonl> --out <dir>
├── events.py      schema v1 kinds, EventLog, Links, Census, LocalTime
├── book.py        EvaluationBook (WindowBook adapter, FIFO trips, days, TWR)
├── statistics.py  StatisticsTable over STAT_NAMES; SolveSummary
├── diagnostics.py ForecastDiagnostics (score x outcome: deciles, hit rate, rank IC)
├── criteria.py    Criterion / Verdict / Scorecard
├── units.py       money / count / ratio / percent / declared score units
├── narrative.py   Narrative ("What happened" by explicit rules) + HOW_TO_READ
├── provenance.py  git_revision, fill_provenance
├── svg.py         stdlib SVG charts + SessionScale + CHART_CSS + downsample
├── sections.py    Section ABC + the eleven default sections
├── report.py      BacktestReport -> events.jsonl, summary.md, report.html, CSVs
├── nodes.py       EvaluationReport pipeline node
├── README.md
├── AGENTS.md      this file
└── CLAUDE.md      identical
```

Keep both trees (here and in README.md) current when files change.

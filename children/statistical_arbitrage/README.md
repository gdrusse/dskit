# Statistical arbitrage

DSKit child for structural equity networks and option relative-value research.
Phase one uses AAPL, AMZN, and GOOGL as tradable nodes; QQQ is a factor and
benchmark. MSFT/NVDA and Energy are separate later additions through JSON.

## What ships

- `configs/run-tech-structural.json` fits yearly precision and correlation
  graphs on prior QQQ residuals, and writes signed edges, weighted centrality,
  option surfaces and pair Z scores.
- `configs/run-tech-backtest.json` pins those outputs by SHA-256 and declares
  quote, fee, hedge, capital and calendar policy.
- `statistical_arbitrage/{data,network,surface,backtest,report}.py` wraps
  generic DSKit readers, graph, pair spread and option ledger mechanisms.
- `docs/decisioning/` records scope; `docs/memos/` records evaluation.
- `tests/` covers source clocks, orders, replay, reports and plots.

## Replay

From the DSKit worktree with its WSL Python environment:

```bash
PYTHONPATH=children/statistical_arbitrage:. python -m statistical_arbitrage.backtest children/statistical_arbitrage/configs/run-tech-backtest.json
```

The runner verifies every structural input hash before reading market sources,
reads one frozen Theta stream for ATM/skew precision, correlation and all-pairs
books, then writes
`evaluation.json` and four PNGs to a new output directory. It refuses a
duplicate output identity. The results are retrospective EOD quote proxies,
not executable fill guarantees or evidence of an edge. GraphicalLassoCV has
fit iterations and chronological CV scores; it has no neural epochs. The
evaluation reports annual edge selection frequency from fitted windows.

## Layout

```text
children/statistical_arbitrage/
  AGENTS.md  CLAUDE.md  README.md  pyproject.toml  journal.json
  configs/    source, calendar, structural and replay JSON
  statistical_arbitrage/
    __init__.py  data.py  network.py  surface.py  backtest.py  report.py
  tests/      test_configs.py  test_nodes.py
  docs/       RE-ENTRY.md  decisioning/  plans/  memos/
```

To extend the child, add symbols and policy to JSON, and keep reusable
mechanisms in `dskit/pipeline/libs/`. ADR-0270/0271 define the approved
phase-one scope and time rules.

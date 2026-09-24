# index_options — agent instructions

S0 is an offline, synthetic-only DSKIT child. No profitability claim.
Use README.md for installation, the temporary demo and focused tests.

- Work in WSL2; preserve other children, active runs and shared environments.
- JSON is the interface; unknown knobs and unsupported instruments refuse.
- Only European-exercise, PM cash-settled, same-expiry intact index condors.
- All data is explicitly synthetic. No provider pulls, training, HPO, replay,
  broker connection, paper/live execution, or serving without separate approval.
  ADR-0182 supersedes the synthetic-only and no-replay lines for its manifest
  only: the Cboe index/chain pulls, IndexCloseRows, the run-real-* rungs and the
  CondorBacktest VIX-proxy replay (never decision-eligible). contracts.py stays
  synthetic-only; IndexCloseRows bypasses it because closes are not option rows.
- contracts.py owns instrument/quote/settlement validation and exact money.
  observations.py subclasses ObservationRows; nodes.py delegates to that owner.
- Children are wrappers: Black-76 + VolIndexSmileQuotes (option_pricing),
  lower_tail_mean/max_drawdown (stats) and keyby (kinds_flow) live in
  dskit.pipeline; this child keeps only condor/index domain code.
- Reuse parent acquisition, deduplication, vintage, fingerprint, artifacts and
  journal seams. Generic gaps require an approved upstream proposal, not local
  plumbing. Do not add registries, readers, fit loops or execution loops.
- Three clocks differ: event time, known_at, actual acquired_at. The acquisition
  cutoff does not establish historical availability or tradability.
- Fresh roots only for fixture pulls. Strict cursor resume misses equal-time
  revisions. Suite evidence is separate from winning-row domain validation.
- No import-time registration or I/O. Dotted class paths resolve the child.
- Keep standalone: import dskit; never depend on this child's incubation path.
- Test only this child unless broader tests are authorized. Enable real journal
  writes in isolation tests with DSKIT_JOURNAL_TESTS=1 and a temporary
  DSKIT_JOURNAL_ROOT; prove positive recording, not only absence of damage.
- Path and Current Work are human-owner-only. Never alter/promote/regenerate
  Path rows. actions.csv is append-only; the decisioning README is generated.
  Its full Path and latest 10 Actions are display rules, not history deletion.
- Use record-explanation for tutorials, memo for durable execution handoffs,
  and the journal research CLI for dated findings. No hand-written research
  entries or hand-edited generated decisioning files.
- Keep AGENTS.md and CLAUDE.md identical and both layout trees current.
  No new files outside an owner-approved manifest.

## Layout

```text
pyproject.toml; .gitignore; README.md; AGENTS.md; CLAUDE.md
journal.json
index_options/             # __init__.py, contracts.py, observations.py, nodes.py,
                           # distribution.py (condor under a forecast, ADR-0168);
                           # pricing, tail mean and drawdown are dskit's (ADR-0182)
configs/                   # source-fixture.json, suite-fixture.json, run-fixture.json,
                           # run-synthetic-distribution.json (ADR-0168 harness),
                           # run-synthetic-har/-lightgbm.json + run-distribution-zoo.json (ADR-0181),
                           # source-cboe-index/-chain.json, run-real-distribution/-har/-lightgbm.json
                           # + run-real-zoo.json (ADR-0182); run-real-vix/-har-vix/
                           # -lightgbm-vix.json (VIX as a scale feature); source-cboe-chain-wide/
                           # -index-wide.json (wide recorder + vol indices)
fixtures/                  # contracts.jsonl, quotes.jsonl, settlements.jsonl
docs/decisioning/           # actions.csv, owner path.csv, generated README.md
docs/explanations/README.md # glossary and worked synthetic payoff
docs/plans/README.md        # gated research stages
docs/memos/README.md        # execution-evidence convention; 2026-09-24 real-data closeout
docs/research/              # README.md, .gitkeep; distribution-modeling/, real-data-backtest/ notes
tests/                     # conftest.py; test_contracts, observations, nodes,
                           # configs, integration, distribution,
                           # synthetic_distribution_run, distribution_zoo, real_data (.py)
```

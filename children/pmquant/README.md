# pmquant — a dskit child

Prediction-market ladders (Kalshi, Polymarket): find where the venue's
price is wrong and take the other side. One document runs the whole
thesis — acquired ladders → settlement labels → the banking spine → event
panels → a seeded transformer ensemble for q̂ → the D-138 edge gate
(toolkit-owned `validate` + studentized cluster-bootstrap `stat_test`,
BH across series) → the fractional-Kelly MIO over the survivors → the
run evaluator. dskit owns the connectors, the observations read seam,
the torch/pyomo doorways, the statistics and the report; this package
owns only what is a ladder's: books, fees, panels, the q̂ module, the
Kelly program. The domain lives in `configs/`.

A child consumes dskit, never modifies it (ADR-0021).

## The one document

`configs/run-e2e.json` is the proof: 22 nodes, run by `tests/test_e2e.py`
on the synthetic ladder world the child ships, relocating only `root`.
`configs/run-kalshi-ladders.json` is its real-data twin — same DAG; a
test pins that the two differ only in where the data lives, the fee
book, the eligibility bar and the training budget.

```bash
python -m pytest tests -q                    # the suite, uninstalled (~25 s)
pip install -e .                             # once; torch, pyomo+highspy, numpy, pyarrow

# synthetic world -> the proof document, in seconds
python -c "from pmquant.testing import acquire_synthetic; \
  acquire_synthetic('./onboarding_root', {'series': ['KXSYNA','KXSYNB'], 'events_per_series': 80})"
python -m dskit.pipeline run configs/run-e2e.json --asof 2026-04-01 --adapter pmquant

# real stores (the parent project's on-disk layout) -> the twin
python -m dskit.onboarding init --root ./onboarding_root
python -m dskit.onboarding register-source pit_ladders --root ./onboarding_root \
  --catalog-source ladders-src --connector localtables \
  --config @configs/source-pit-ladders.json --activate
python -m dskit.onboarding register-source settled_markets --root ./onboarding_root \
  --catalog-source ladders-src --connector localtables \
  --config @configs/source-settled-markets.json --activate
python -m dskit.onboarding acquire --root ./onboarding_root --source pit_ladders \
  --stream predexon_l2_pit --mode backfill
python -m dskit.onboarding acquire --root ./onboarding_root --source settled_markets \
  --stream markets --mode backfill
python -m dskit.onboarding validate --root ./onboarding_root \
  --suite configs/suite-ladders.json --snapshot <snapshot-vid>
python -m dskit.pipeline run configs/run-kalshi-ladders.json --asof 2026-09-04 --adapter pmquant
```

Exit codes: `0` ran · `3` halted at a NO-GO gate (a halt is a result) ·
`1` error. `report.md` and `artifacts/run_report/evidence.md` in the run
dir say what happened to the data and the capital.

**Live vendors.** `configs/source-kalshi.json` (public REST: markets,
candles, fee schedules, order books), `configs/source-polymarket.json`
(Gamma/CLOB + the pmxt L2 archive mirrored on the Hugging Face hub —
anonymous, or the token NAMED as `HF_TOKEN` in `.env.example`; the
`archive_hours` stream resolves its token ids from the series slugs and
streams ~360 MB hour files without keeping them) and
`configs/source-predexon.json` (historical L2; needs the key NAMED in
`.env.example`) register the same way with `--connector
kalshi|polymarket|predexon`; `acquire --mode live` on a cadence is the
recorder. The Polymarket slugs are the venue's own spellings
(`<city>-daily-weather`, `<city>-daily-lowest-temperature`), pinned by
test — a guessed slug pulls nothing and looks like an empty market.

## What the kinds are

| kind | role | does |
|---|---|---|
| `pmquant-ladder-source` | data | one `MarketRecord` per (contract, decision epoch) off the acquired ledger; `usable` recomputed from the ladders |
| `pmquant-settlement` | labels | contract → settled outcome, plus the strike rows |
| `pmquant-inventory` | report | usable / two-sided / tradeable event counts per series and lead |
| `pmquant-ladder-panels` | transform | one (T leads × C rungs × 41) panel per event, cut on the event's close |
| `pmquant-ladder-train` | train | `DeclaredTrain` pinned to the ladder module + adapter; best-epoch restore on `claims_val_event_ll` |
| `pmquant-ladder-predict` | signal | score a block's panels from an artifact |
| `pmquant-ensemble` | transform | loud per-cell mean over exactly `require` seed frames |
| `pmquant-signal-qhat` | signal | the frame as `predict(record) -> belief` |
| `pmquant-market-implied` | signal | the D-006 null: the market's own price |
| `pmquant-kelly-mio` | capital | exact-log fractional-Kelly tangent MILP per event over the survivors (`PyomoSolve`, HiGHS deterministic) |

`validate`, `stat_test` and `run-report` are the toolkit's, owned:
their statistics are not config-swappable.

## Configs

| file | what |
|---|---|
| `run-e2e.json` | the proof document (synthetic source alias) |
| `run-kalshi-ladders.json` | the real twin: imported stores, the venue fee book, bar 50 |
| `source-pit-ladders.json` / `source-settled-markets.json` | the on-disk stores through `localtables` |
| `source-kalshi.json` / `source-polymarket.json` / `source-predexon.json` | the live vendors |
| `suite-ladders.json` | structural gates over both imported streams |
| `asset-model.json` | the child's governed catalog kinds |

Every config carries its why in `notes`; a knob the code does not
declare is refused, never defaulted.

## Journal

Every run, acquisition and research note lands in `docs/decisioning/`
(ADR-0056; the README there is generated from CSV). Pipeline runs and
onboarding verbs record themselves; research is `python -m dskit.journal
research`; the path to production is owner `journal promote`.

## Layout

```
pmquant/
├── __init__.py          import = registration of every pmquant-* kind
├── books.py             ladders, DecisionEpochRecord, entry gate, walk_book
├── fees.py              venue fee rounding, dated FeeBook, resolve_fee_rates
├── mio.py               scenarios, payoffs, the tangent MILP, exact recompute
├── models.py            TokenEncoder, LawHead, LadderQhatModule, LadderPanelAdapter
├── nodes_data.py        pmquant-ladder-source / -settlement / -inventory
├── nodes_model.py       pmquant-ladder-panels / -train / -predict / -ensemble / -signal-qhat / -market-implied
├── nodes_capital.py     pmquant-kelly-mio
├── testing.py           the synthetic ladder world + connector + acquire_synthetic
└── ladder/
    ├── protocols.py     venues, settlement laws, LadderType, rung order, LeadGrid defaults
    └── panels.py        EventPanel, TokenFeaturizer (41 columns), panel items
configs/                 the documents and sources above
notebooks/               01-ladder-e2e.ipynb — the document, cell by cell
docs/decisioning/        actions.csv + path.csv; README generated
docs/research/           research notes (CLI-written)
tests/                   conftest bootstrap; one file per module + test_configs + test_e2e
journal.json             dskit.journal marker
pyproject.toml           dependencies: dskit, numpy, torch, pyomo, highspy, pyarrow
```

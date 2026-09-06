Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md — intraday_poc (a dskit child)

Agent orientation — see README.md for what the child does and the
one-command runs. This is the two-stock intraday PoC AND the reference
shape for future children: keep the module set stable
(connectors / nodes / models / live / testing + configs + tests).

## The child rules (ADR-0021)

- **Never edit dskit.** A missing capability is either a genuinely
  generic gap — propose an ADR upstream — or domain logic that stays
  here. There is no third option.
- **The domain lives here and in `configs/`.** dskit stays domain-blind;
  behavior is JSON the engines validate, self-documented via `notes`
  (the why, not the what), default-deny everywhere.
- **Tier-3 may import anything** — but keep heavy imports inside
  `run()`/`read()`: documents naming these kinds must PLAN on machines
  without torch/pyomo/alpaca-py (the conformance suite enforces it).
  `models.py` is the empty bespoke seam (no torch): add a class there
  only when the architecture is genuinely invented, and then keep
  torch inside that file — never imported by `__init__`, `nodes`, or
  `connectors`. The documents name `torch-ts-train` / `arch: lstm`.
- **Position-independent**: no `..` imports, no dskit-repo paths; the
  only coupling is `import dskit`. Graduation is a directory move.
- **Import = registration**: `intraday_poc/__init__.py` imports `nodes`,
  which registers the kinds (`owned` never set — that is toolkit
  doctrine). `--adapter intraday_poc` is exactly this import.
- **Decisioning is a journal (ADR-0056).** `journal.json` is the walk-up
  marker. Actions (acquire / research / execute / production) append
  `docs/decisioning/actions.csv`; README is generated. Path and Current Work
  are human-owner-only. Every Path row has ID, label, purpose, relevant files,
  and `LOCKED` (`Y`/`N`). The generated README displays the full Path and
  latest 10 Actions; CSV history is append-only. Pipeline and onboarding
  commands record themselves. Research uses
  `python -m dskit.journal research --topic T --name N` (never write
  `docs/research/` by hand; no markdown in that root — only
  `docs/research/<topic>/<YYYY-MM-DD>-<name>.md`, with
  `<date>-synthesis.md` as the task summary). Wrap `live.main` in
  `dskit.journal.hooks.production`. An uninitialized child refuses.
- **Standalone explanations live in `docs/explanations/`.** Put worked,
  self-contained explanations there rather than beside decision records or
  research notes.
- **Durable handoffs live in `docs/memos/`.** Keep implementation outcomes,
  operational evidence, and known caveats there. A memo is not an ADR and is
  not journaled research.

## Layout

```
intraday_poc/
├── connectors.py   # thin adjusted/minute-only policy wrapper over dskit's
│                   #   Alpaca bars pack (testing.py doubles its _fetch)
├── nodes.py        # bars (data) / window (transform) / forecast (score)
│                   #   / select-one (score, on the PyomoSolve doorway)
├── models.py       # empty bespoke seam — zoo ships standard nets
├── live.py         # forward loop: clock gate → IEX bars → verified
│                   #   artifact restore → the SAME pyomo pick → paper order;
│                   #   every knob READ from the run dir + source config
└── testing.py      # StubBarsConnector — deterministic, no network
configs/            # source-backfill (the ONE source config) / suite-bars /
                    #   run-backtest (walkforward) / run-train / asset-model
tests/              # conftest bootstrap + connectors/nodes/configs suites
journal.json        # dskit.journal marker (ADR-0056)
docs/decisioning/   # actions.csv + path.csv; README generated
docs/explanations/  # durable explanations; use record-explanation
docs/memos/         # decision memos; use memo
docs/research/      # topic folders; <date>-synthesis.md + dated notes
pyproject.toml      # dskit + alpaca-py/torch/pyomo/highspy/mlflow (run-path)
.env.example        # Alpaca paper keys — .env is gitignored, never committed
```

## Gotchas learned building this

- **`run-train.json` fans out; `run-backtest.json` does not.** The
  train document builds its per-symbol nodes from ONE `foreach`
  template (ADR-0039), so its trainers are `qhat__aapl`/`qhat__msft`
  — a DOUBLE underscore. `live.py` READS that mapping from the document's
  own `foreach_groups`, zipped against `foreach.keys` (`_fanned_owner`) —
  it does not reconstruct it, and it never reads a key backwards. Serving
  a fanned run DOES need `--artifact SYMBOL=artifacts/qhat__<slug>`,
  because the artifact directories carry the fanned node keys; the README
  gives the exact command. Do not match a fanned key by SUFFIX:
  `qhat__brk_b` ends in `_b`, so the symbol `B` would silently be served
  BRK.B's weights (both are real tickers, and the pair-regime check
  cannot see it — both symbols would share one artifact). Hand-declared
  trainers still match by suffix, because nothing on disk says where
  `qhat_aapl`'s stem ends; that rule refuses an ambiguous pair rather
  than choosing. Read a fanned document through `document.expanded`,
  never `document.pipeline`: the declared map holds the template, the
  derived map holds what RAN, and they are the same object only when
  there is no fan-out.
- **`foreach` pins the DECLARATION, not the tuned value.** One space
  key naming a template expands to one override per instance, so
  `hpo-grid` CROSSES them: three widths over two symbols is nine
  trials, SIX of them asymmetric, and a winner may pair 16 with 64.
  Nothing in the grammar ties two nodes' params to one value (there is
  no linked-key form, and duplicate hand-written keys cross exactly the
  same way), and the driver APPLIES the winner to the run's artifacts —
  so an asymmetric pairing ships unless something refuses it. `live.py`
  does: it compares the restored artifacts' `arch_params` and refuses
  the pair. Recovering from that refusal is a CONFIG edit, never a
  re-run — the grid is enumerated and `loader.seed` pins every fit, so
  a re-run reproduces the same winner. Take a symmetric trial from the
  run's `carry.json`, put its width on the template (one edit, both
  symbols) and on run-backtest.json's twin pair, narrow the space, and
  re-run. An ASYMMETRIC pairing cannot be promoted into the document at
  all: an instance key may not be declared beside the template it fans
  from, so wanting per-symbol widths means hand-expanding the fan-out.
- **Three bands, not two.** `run-train.json` fits to
  `splits.train_end_ms`, selects each trial's checkpoint on the band the
  cuts leave open after it (`mon_rows`, `monitor: val_loss`), and scores
  the search on the selection window after THAT. Wire the monitor to the
  scored rows and one set picks both the checkpoint and the
  architecture; leave it unset and the search compares nine last epochs.
  `run-backtest.json` still has only TWO bands — each fold's `*_val`
  feeds the trainer's `val_rows`, the forecaster AND `labeled` — which
  is why its `total_realized` is an upper bound, said in its notes, the
  README and `test_the_readme_carries_the_backtests_selection_skew`.
- **The mlflow sink is a HARD dependency of `run`.** The driver opens
  every declared sink before it resolves the run, so a child install
  without `mlflow` aborts `run-train.json` before node one — while
  `validate` and `plan` stay green. The child's own `pyproject.toml`
  therefore declares it, pinned by
  `test_the_child_installs_what_its_tracking_sinks_NEED`.
- **A sink's params are the DECLARED ones.** `log_params` runs before
  any node does, and trials execute with the tracker silenced, so a
  searched run's mlflow entry pairs baseline params with final-pass
  metrics. The winner is in `nodes/NN-search.json`, the per-trial scores
  in `carry.json` — not in the report, and not in the sink.

- **Lookback is pinned in three places** — the window node's `lookback`,
  the trainer's `seq_len`, and the `ret_lag_*` feature list.
  `test_configs.py::test_lookback_agrees_everywhere` pins the agreement;
  the pack refuses a width mismatch at plan, and `live.main`
  refuses a run whose artifacts and window node disagree.
- **`WindowRows` owns no chain arithmetic** (ADR-0040). It subclasses
  `dskit.pipeline.libs.numpy:ReturnWindows` and supplies only the
  domain: the knob SPELLINGS (`price_field`, `max_gap_minutes`), the
  column names, and `keep_mask` — "a bar with no usable price is not a
  bar", vectorized. Every accessor it overrides is narrowed out of
  `_PARAMS` (`narrow_params`), and the pack REFUSES the class if you
  forget. The serving path calls `latest_rows` on the SAME node, so
  there is no second implementation to drift — `latest_feature_row` is
  gone — and it restates none of the node's spellings either:
  `window_records` takes the NODE and reads `group_field()`,
  `order_field()` and `price_field()` off it, so a retuned field name
  cannot leave the loop emitting records the node will not lift.
  `keep_mask` cuts both ways: a priceless minute BEHIND the newest
  bar is read through (the survivors chain), while a priceless NEWEST
  minute makes the symbol absent from `latest_rows` rather than serving
  a one-minute-stale vector as current.
- **Same-instant bars follow the STREAM** — the semantic the port moved
  on well-formed input. Two `ts` spellings can flatten onto one
  `asof_ms`; the pack breaks that tie by stream position, which is the
  store's own `ts` order, where the pre-port code sorted
  `(asof_ms, price)` tuples and so ordered them by PRICE. Pinned in
  `tests/test_nodes.py::test_window_rows_orders_same_instant_bars_by_the_STREAM`.
- **Three DEGENERATE-input shapes moved too**, and the `WindowRows`
  docstring lists all five together: an empty `symbol` is now no series
  (it used to be one of its own, and a stream of only such bars now
  refuses by name), while a float `asof_ms` and a non-dict record now
  LIFT (both were dropped). Pinned in `tests/test_nodes.py::
  test_window_rows_admits_and_refuses_the_DEGENERATE_bars_it_now_does`.
- **A NON-FINITE price is now dropped** (`keep_mask` wants a finite
  positive price) where the pre-port `price <= 0` test — which neither
  `inf` nor `nan` satisfies — let it into the chain and every window
  overlapping it carried a non-finite return into training. The fifth
  divergence, and the only one on ordinary-looking input. Pinned in
  `tests/test_nodes.py::
  test_window_rows_drops_a_NON_FINITE_price_the_pre_port_node_KEPT`.
- **Streams are single-pass**: `SelectOne.run` groups forecasts ONCE and
  caches for `build_model`; never re-iterate an input port.
- **`select-one` is role `score`, not `capital`** — deliberate: it scores
  the rule. Sizing cash with it means restoring the doorway's default
  `capital` role and wiring a `stat_test` survivors gate.
- **One source name, two modes**: `alpaca` is registered once with
  `configs/source-backfill.json` and BOTH `--mode backfill` and
  `--mode live` acquire through it (ADR-0014 keys the cursors). A second
  source name would put live bars in a tree the run documents never read
  — that bug shipped once; `test_one_source_name_carries_both_pulls`
  pins it shut. The modes differ in ONE place: a pull with no cursor
  starts at `start` (backfill) or at most `live_lookback_minutes` ago
  (live) — unbounded, a first live pull would re-commit the whole
  history as a second acquisition. On `sip` that lookback must also
  EXCEED the 16-minute clamp the window ends at; the gate refuses a
  smaller one rather than pulling an empty window forever, silently.
- **SIP vs IEX**: every STORE pull uses `feed=sip` (free, consolidated,
  end clamped 16 min back). Only the forward loop's own fetch uses IEX
  (`live.py:LIVE_FEED`), because real-time SIP is not on the free tier —
  sparse minutes possible, gap discipline handles it, never bridge.
- **`live.py` restates nothing**: price field, gap bound, trainer
  identity (zoo class or declared module),
  which trainer belongs to which symbol, and the selector's solver all
  come from `<run-dir>/config.json`, read through the ENGINE's own
  `load_document` (typed `NodeSpec`s — never a parse of the child's, which
  would accept runs the engine refuses); vendor knobs AND the symbol
  universe from the source config (`--source-config`), resolved through
  the connector's public `resolve_knobs` — the credential env-var NAMES
  (`key_env`/`secret_env`) among them. The bar interval is the source
  config's ``timeframe`` knob (default ``connectors.BAR_INTERVAL``);
  both fetch paths build the vendor ``TimeFrame`` from the resolved
  pair.
  Each symbol's artifact is `artifacts/<the run's own trainer key>` — a
  fanned trainer answers for the ONE `foreach` key it was built from
  (READ off the document's public `foreach_groups`, never recomposed
  from the template and the slug: that spelling is the engine's private
  `_instance_key`, and a second copy of it falls back to the suffix rule
  the moment the engine changes how a name is assembled), a
  hand-declared one for the key ending in that symbol's slug; a symbol
  the run trained no model for is refused, naming the trainers it DID
  write.
  `--artifact SYMBOL=PATH` serves a directory this run did not write —
  and an override for a symbol the config does not declare is refused,
  not dropped.
- **The live minute is solved by the run's OWN selector node**, built
  from the document the way `window_node` builds the window node and
  then RUN (`selector_node` / `solve_pick`) — never a `SolverFactory`
  call written here. The pack's doorway owns solver resolution, its two
  refusals (unregistered name, missing backend) and the
  `_solver_options` seam a subclass overrides to pin determinism; a
  second solve in `live.py` drops all three, and the difference only
  shows on a machine missing the backend, mid-session, with a position
  open. `preflight_selector` solves one throwaway minute before the
  trading client is touched, so that machine refuses at STARTUP.
- **Credentials: one rule, two sources.** Both sides take the env-var
  NAMES from the source config and both refuse a var that is missing OR
  empty, by name, through the ONE shared
  `connectors.resolve_credentials` — presence is not authentication, and
  `.env.example` ships both keys empty. They differ in WHERE they read:
  the puller's connector reads `os.environ` only; `live.py` reads `.env`
  beside the CWD merged under the process environment, via dskit's
  `env.py`. Do not write "the loop authenticates as the puller does" —
  it did not, and does not; exporting the pair is what serves both.
- `live.py` only ever constructs `TradingClient(..., paper=True)`.
  Keep it that way.
- **`__all__` is the contract in this package too.** `live.py` declares
  one and `test_the_serving_loop_declares_its_public_surface` pins it:
  a new helper is underscored, or added to `__all__` on purpose.

Keep this tree and README.md's current when files change.
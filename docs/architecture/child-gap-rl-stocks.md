# Capability-gap report — rl_stocks as a dskit child

**Verdict: five generic gaps, found and CLOSED the same session (ADR-0025
accepted + ADR-0027…0030 implemented). Everything else is covered by an
existing dskit mechanism or is child material — and the rebuild retires whole
defect classes rl_stocks documents against itself.**

Evidence base: full re-inventory 2026-08-25 (four subsystem surveys: data
layer; features+alpha; RL+simulator+MIO; evaluation+operations) of
`gdrusse/rl-stocks` — ~66k LOC across 13 subpackages: 22 data sources over a
SQLite fetch manifest, a 107-feature PIT panel (schema 7.4), FT-Transformer/TFT
quantile alpha engine with walk-forward validation, an SAC agent
(stable-baselines3 + gymnasium) over a tax-aware simulator with MIO (pyomo)
inside `step()`, an evaluation/report stack, and a weekly operations layer.
**Supersedes** the earlier same-day report, which analyzed a stale 1,141-LOC
single-commit snapshot.

## COVERED — the dskit mechanism exists (and retires a documented defect)

| rl_stocks area | dskit mechanism | defect retired |
|---|---|---|
| `BaseDataSource` + 22 source classes; tenacity retry copy-pasted ~12×; three spellings of the pacing knob | `onboarding.Connector` four-verb + `restapi` pack (pagination, auth, retry, `since` — declared) | per-source reimplementation |
| upsert-in-place parquet, no temp+rename, schema mismatch silently drops history (their I-054) | WORM snapshots, `durable_write_*`, bitemporal records | torn/lost writes |
| data QA = null-count log lines | declarative validation suites → certify → publish | the QA it never had |
| open configs: dead keys (`label_resolved_trading_days`, `min_train_years`, `horizons_to_plot`), silently-defaulting `.get()` reads | default-deny documents + params, identity hash | typo → silent default |
| hand-written 3-stage weekly orchestrator; its config is 3 path strings | the pipeline document (DAG, run dirs, exit codes, `--asof`) | orchestration as code |
| model factory if/elif + processor wiring hardcoded in `build_features.py` | `uses: "pkg.module:Class"` + kind registries | config never names a class |
| PIT discipline "trust-based, not enforced" | numpy pack `ArrayMap`/`ArrayFeatures` + the mechanical causality guard | look-ahead by convention |
| MIO: pyomo + HiGHS/GLPK/CBC cascade + greedy fallback (root docs still say "Gurobi/CPLEX" — stale) | `PyomoSolve` doorway (solver knob, options pass-through) | — |
| Optuna TPE + SuccessiveHalving with a per-epoch pruning hook | `optuna-search` + `hpo-grid` + the `ctx.rerun` seam (continuous spaces now plan and run — declare a `{"low", "high"}` range; pruning stays absent until a per-epoch reporting seam exists) | two fold-declaration mechanisms |
| two INCOMPATIBLE checkpoint schemas; loader gloms newest-by-mtime; no feature-schema fingerprint checked at load | the torch artifact protocol (sidecar + content hash + refuse-by-name) + `dskit.assets` registry/lineage | silent checkpoint drift |
| TensorBoard + ad-hoc `summary.json` | `training_curve.json` curves (`trainlog.py`) + `run-report` evidence + the tracker sink seam | — |
| `.env` loading, `setup_logging` | `env.py` + Secrets, per-run `run.log` | — |
| schema-conformant synthetic panel generator | child code over its own schema (the toolkit's synthetic nodes cover engine demos) | — |

## GAP — generic, evidenced, and closed this session

| the need (rl_stocks evidence) | what shipped |
|---|---|
| Declared torch training: `build_model` if/elif; AdamW; early stopping on a val metric with best-weights restore; per-epoch curves; pinball/quantile losses; grad clipping; the `mps→cuda→cpu` cascade duplicated ~10×; multi-resolution lookback windows | **ADR-0025 (accepted)**: `torch-train`/`torch-predict` — the document names the `nn.Module` and the `torch.optim` optimizer; the adapter seam carries data-implied shapes; hash-pinned artifacts with refuse-by-name restore; `val_rows` + per-epoch `training_curve.json` via `trainlog.py`; a `device` knob; `metrics.py` gains `squared_error`/`absolute_error`. The bespoke extras a parallel build promised (quantile loss, early stopping w/ restore, grad_clip, device "auto", `sequence` windows) were superseded on merge by the faithful parent port (see the ADR-0025 amendment) — a child wanting them wraps `TorchTrain` tier-3 until a follow-up ADR |
| Walk-forward: expanding folds from config, a ~30-trading-day structural embargo before each val window, per-fold fresh training + checkpoints, an aggregate gate | **ADR-0027**: the `walkforward` document section + CLI verb (one full run dir per fold + an aggregate summary); `val_start_ms` embargo band on time splits, `embargo_days` on trailing. The finer per-HORIZON graduated label masking stays child-side — its loss owns it |
| RL: stable-baselines3 SAC over a gymnasium env; bare `.learn()`/`.save()`, no artifact protocol, no eval cadence | **ADR-0028**: the `sb3` pack — `sb3-train` (document names algo + policy + env class), `sb3-policy`, `sb3-eval`; hash-pinned artifacts |
| Charts: six figures maintained TWICE (matplotlib + plotly, aligned by discipline) plus five more in the alpha engine — no chart spec anywhere | **ADR-0029**: the `matplotlib` pack — `mpl-figure` declared marks + the `FigureNode` base. HTML/plotly parity deliberately out of scope |
| The fetch manifest: a `(source, ticker, day)` done-set with `no_data` tombstones, gap + refresh-cadence queries, reconcile-from-disk, drift audit — and a DOCUMENTED observation-range-inference blind spot | **ADR-0030**: `onboarding.CoverageLedger` — expected periods are declared by the caller, making that blind spot unrepresentable; `audit`/`reconcile` close the ledger-vs-store loop |

## WRAPPER — domain; lives in the child, composing the seams

| rl_stocks area | child shape |
|---|---|
| Yahoo/Polygon/FRED/SEC-EDGAR/FINRA/issuer-CSV pulls | `restapi` configs where the API is plain; four-verb connector classes where it is not (FINRA client-credentials, Schwab OAuth refresh) |
| feature/target processors' finance semantics (67→107 features, dividends, PIT shifts) | `ArrayMap`/`ArrayFeatures` subclasses + `derive`/`join`/`table-file` |
| the six model families (TFT, FT-Transformer, GRU, TCN, …) and the multi-resolution collate | `nn.Module` classes the declared seam names; a `TorchTrain` subclass where the `sequence` block is too simple |
| simulator: lot ledger, wash sale (IRC §1091), Almgren-Chriss, dividends, reward terms, tax engine | child engines behind `capital`/`score` nodes |
| the gymnasium env wrapping the simulator | exactly the class `sb3-train` names |
| defensive-loop statistics (IC/ICIR trust, Page-Hinkley, Ledoit-Wolf, robust QP) | child math (sklearn/scipy inside `run()`); the calibration/stats pack stays a below-the-line candidate (pmquant report) |
| Schwab read-only client, `portfolio.json`, lot store, sector map, recommendation-report content | child modules; tables as `table-file` inputs; rendering via `run-report` + `mpl-figure` |

## Below the line (no ADR yet — say the word)

- **restapi OAuth2 token-endpoint strategy** — FINRA client-credentials +
  Schwab refresh are the second and third vendor with the same shape; today
  each is a small child connector.
- **Grouped rank-correlation (cross-sectional IC)** as a shipped
  metric/stat helper — structurally generic, child-computable today.
- **ADR-0024 (event bounds) / ADR-0026 (report tables)** stay proposed:
  rl_stocks' leakage need is covered by the embargo; its report need by
  `run-report` + the figure pack.

## rl_stocks as a thin child — sketch (revised)

`children/rl_stocks/` per ADR-0021: `connectors.py` (REST configs + the two
OAuth connectors), `processors.py` (ArrayFeatures subclasses per feature
family), `models.py` (the module zoo `torch-train` names), `env.py` (the
gymnasium wrapper `sb3-train` names), `simulator/` + `tax/` (the domain
engines), `nodes_capital.py` (`MIOAllocator` on `PyomoSolve`), `configs/`
(universe/sector/fee tables as `table-file`; the alpha document WITH a
`walkforward` section; the SAC document; suites; the asset model), `tests/`.

Migration note: still a **rebuild on the seams, not a port** — the paths dskit
replaces are exactly the documented-buggy ones (write atomicity, checkpoint
drift, dead config keys, the stale S3/DynamoDB inspection tooling).

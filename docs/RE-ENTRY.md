# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-11-26: JuMP suite landed (ADR-0328 + amendment 1): Julia worker, live models, two reference programs; reviewed `42ddafca`; no real MIO model ported; C: at the 10 GiB reserve. |
| index_options | children/index_options/docs/RE-ENTRY.md | 2026-11-25: MIO calibration definitions saved; fitting/validation pending; next align PITs across assets/expiry ranks, then define copula. |
| crypto_trading | `children/crypto_trading/docs/RE-ENTRY.md` | 2026-10-19: historical 600-entry audit complete; method24 two-cell Kalshi recommendation beat market scores in later W4/W5; retrospective; protected tests closed; two final reviews passed |
| correlation_arb | `children/correlation_arb/docs/RE-ENTRY.md` | 2026-10-09: child + ADR-0256/0257 on main; Stage 1 kill test next |
| statistical_arbitrage | `children/statistical_arbitrage/docs/RE-ENTRY.md` | 2026-10-21: prediction-only studies ADR-0272..0275: ATM-spread reversion; best now the pooled-scaled blend on the vol-scaled label (ATM 20d R²oos 0.25→0.27, the most robust pass); Theta Greeks/flow/market features add nothing; skew none; no edge claim |
| stock_options | `children/stock_options/docs/RE-ENTRY.md` | 2026-10-01: bootstrap + AMZN conversion done; gap audit rerun next |
| intraday_equities | `children/intraday_equities/docs/RE-ENTRY.md` | 2026-10-12: fill-window relabel: next-bar-fill IC 0.01-0.02 (5-min rows), 0.002-0.005 (1-min rows); no fill-reachable edge |
| pmquant | `children/pmquant/docs/RE-ENTRY.md` | 2026-09-04: child landed; waits on PREDEXON_API_KEY and the real-data twin |
| portfolio_policy | `children/portfolio_policy/docs/RE-ENTRY.md` | 2026-10-30: first real Schwab verdict ran (46 lots, all hold, `--no-strategy`); zero-cash buys valued under `allow_zero_cash`; owner sets market blocks next |
| intraday_poc | `children/intraday_poc/docs/RE-ENTRY.md` | legacy worked example; no active lane |

## Core (dskit itself)

**Now.** 2026-11-26: the JuMP suite landed (ADR-0328 and its amendment 1).
`dskit/pipeline/libs/jump.py` is the Python doorway (`JumpSolve`, `JuliaWorker`,
`LiveModel`, `DataContract`); `julia/DskitJuMP/` is the Julia package it drives
through a long-lived subprocess speaking JSON lines (wire protocol v2, written
out in the amendment). A model is built once and changed in place: open, update,
fix, free, start, solve, search, close, a pooled model per shape, Arrow files
for large arrays. Two reference programs ship, `budgeted_select` and
`assignment`; no real MIO model is ported. Reviewed candidate `42ddafca`: two
clean final skeptic lenses, zero Critical or Major. Julia tests 363 passed;
`tests/pipeline_libs/test_jump.py` 449 passed, 7 skipped, with the cross-worker
conformance and the pyomo parity test run against real Julia 1.13.4. Measured
on the `assignment` program at 20,000 items: in-worker build 0.085 s against
pyomo 1.04 s, an `open` seen from Python 0.31 to 0.37 s, an in-place update
0.002 s, solves equal. The earlier storage and relocation state is unchanged
and is in the archive.

**Next.** Port the first real program (its own approval, a new formulation hash,
never a replacement of a locked pyomo result). Run the 200,000-item size of
`tools/jump/benchmark.py` once C: has headroom. Then the Minor backlog below.

**Blocked / owner decisions.** C: sits at the 10 GiB reserve; the staged
`C:\Users\russe\compact-wsl.cmd` (untested, needs the admin prompt) would
recover about 58 GiB. The WSL upgrade, sparse-reclaim pilot and full backup
remain unperformed. Owner to confirm the choices the builders made where the
amendment was silent (listed in its implementation notes): the `components`
field, `shape_changed` refusing rather than rebuilding, labels counting as
shape, the `internal` error kind, and the pool's byte cap reading Julia-side
memory only. The disclosed Minor backlog (not fixed) closes the amendment.

**Read first.** ADR-0328 and "ADR-0328 amendment 1" in
`docs/architecture/decision-log.md`; `dskit/pipeline/README.md` "What ships";
`tests/pipeline_libs/jump_fake_worker.py` (the executable protocol reference);
`/home/russell/storage-investigation/storage-policy.json`.

**Do not.** Run the Julia half of the suite without `~/.local/bin` on `PATH`
and call the skips a pass. Treat the measured speedups as evidence beyond
20,000 items, or as a solve speedup. Add WSL free space to C capacity, delete
protected results, or treat development review as deployment approval.

## Protocol

- **One note per lane**, `children/<child>/docs/RE-ENTRY.md`, at most 80 lines:
  `Now`, `Next`, `Blocked / owner decisions`, `Read first` (at most five links),
  `Do not`. Core work with no child lands under "Core" above.
- **`/wrap` rewrites the lane note whole** and updates that lane's row here (one
  line, dated). It never appends. The replaced note goes to the top of
  `docs/archive/re-entry/<lane>.md` (append-only, `union` merge).
- **A `keep-both` marker in a note means two sessions wrapped in parallel.**
  Prune to one note before committing: `tests/docs/test_budgets.py` refuses the
  marker and any note over budget, so the suite stays red until it is pruned.
- **Never read the archive at session start.** It answers "what happened on
  date X", nothing a fresh session needs.
- The same rules bind every agent: Claude Code (`CLAUDE.md`), Codex and other
  OpenAI models (`AGENTS.md`), Cursor (`.cursor/rules`), OpenCode (`.opencode`).

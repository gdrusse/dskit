# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-11-27: Verified Julia lifecycle and shape changes at `7999a6da` and all 48 solver pairs. HiGHS remains the default. |
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

**Now.** Candidate `7999a6da` keeps caller fixes separate from
program-owned domains during updates, pooled reopen, free and search.
The Julia suite passed 840/840. JSON and Arrow worker checks passed, and
the child guide picked `sample-0002` at objective 11.5. Two independent
reviews closed with zero unresolved findings. Large labelled and
positional shape signatures took 18.25% and 99.19% less time in paired
tests, respectively, and each allocated about 376 KiB less per call.
Lifecycle pairs showed no replicated slowdown.

The 48-pair HiGHS/SCIP comparison returned verified feasible points in
all 224 measured stages. SCIP had lower median warm optimizer time on
`budgeted_select`. Under the five-second budget, HiGHS had the higher
`assignment` objective in 40 of 48 aligned stages and tied eight. The
results depended on the fixture. Keep HiGHS as the default.

**Next.** Owner review of the feature PR against `main` before a merge.

**Blocked / owner decisions.** The real MIO formulation is pending.
Cash, joint scenarios, CVaR and hedge definitions remain open.

**Read first.** [Child Julia guide](../dskit/pipeline/EXTENDING.md#julia-programs-in-child-projects),
[shape evidence](</home/russell/data/dskit/runs/jump-shape/v20261126/summary.json>),
[lifecycle evidence](</home/russell/data/dskit/runs/jump-lifecycle/v20261126/HANDOFF.md>),
[solver evidence](</home/russell/data/dskit/runs/jump-solvers/v20261126/implementation-receipt.md>),
and [runtime design](</home/russell/reports/JuMP MIO runtime design.md>).

**Do not.** Generalize fixture timings to production or start an MIO
port, replay or deployment without separate approval.

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

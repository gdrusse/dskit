# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-11-26: Python JuMP vector packing reviewed at `1df99f63`; native shape work awaits RAM admission; lifecycle repairs and MIO port pending. |
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

**Now.** The reviewed Python candidate adds a three-line fast path for packing
rank-one built-in lists and tuples. Candidate
`1df99f63e75f49ef0fd95ba37568f59d463e9729`, based on
`9bb26e40eae09d5002305ef4b329b7998106db71`, has completed development review.
The 27 focused contract tests and Ruff passed. Two fresh Luna final reviews
reported zero Critical, Major, Minor and Nit findings. Two runs measured
2.89% and 5.03% faster Python preparation for a 4,096-entry tuple open.
The representative list result was inconclusive. The timings cover Python
preparation only. The landed ADR-0328 suite provides the Julia worker and the
`budgeted_select` and `assignment` reference programs. No new Julia model shipped.

The approved runtime direction uses HiGHS by default and benchmarks SCIP.
The local design keeps typed decision state and the Program/Search loop in
Julia, with independent verification and publication under host control.

**Next.** Recheck admission, then run the native shape baseline and RED steps
in the handoff. Continue with Sol implementation, speed and RAM checks, two
fresh Luna reviews, and Terra fixes for review findings. Preserve the unmerged
worktree `/home/russell/wt/codex-jump-shape-20261126`. Phase 0 cleared, and its
only change is 41 unexecuted test lines. Production is unchanged.

Repair two separate pre-existing Major lifecycle defects before relying on
pooled trading updates. Pooled updates can see stale bounds under caller
fixes, and free or search cleanup can erase a program-owned fixed equality.
Both findings have static witnesses and await native regression tests.

**Blocked / owner decisions.** At 2026-11-27 02:08 UTC, C: had 5.1 GiB free
and Windows had 3.1 GiB available RAM, below the 4 GiB reserve. No native
jobs launched. The owner approved a 5 GiB C: reserve for this task. The shared
10 GiB reserve remains in force. The mathematical contract and actual MIO
port are pending. See the immediately prior Core archive for outstanding
amendment choices, storage actions and the disclosed Minor backlog.

**Read first.** [Vector evidence](</home/russell/data/dskit/runs/jump-vector-pack/v20261126/receipt.md>),
[runtime design](</home/russell/reports/JuMP MIO runtime design.md>) (SHA-256
`d900dd05877a95942a1257ecd41711599c0c2c6f2641749920796cab63ab52f9`),
[shape handoff](</home/russell/data/dskit/runs/jump-shape/v20261126/HANDOFF.md>),
[lifecycle packet](</home/russell/research_notes/JuMP MIO transition experiments/lifecycle-next-packet.md>),
and [lifecycle review](</home/russell/research_notes/JuMP MIO transition experiments/lifecycle-design-review.md>).

**Do not.** Extrapolate Python preparation timings to Julia or solver runtime,
run native jobs before renewed admission, or treat development review as
approval for a real MIO port, replay, or deployment.

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

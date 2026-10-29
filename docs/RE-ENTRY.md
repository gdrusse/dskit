# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-10-26: main synced; 37 local/2 remote contained branches purged; unmerged/dirty work retained; sweep receipt below |
| index_options | children/index_options/docs/RE-ENTRY.md | 2026-10-29: ordinary MIO available-size wiring locked 922b53d0; four C0/M0 reviews, 334 focused tests; code/JSONs pushed; limited36 running; broad212 then seven-case stress follow-up configured |
| crypto_trading | `children/crypto_trading/docs/RE-ENTRY.md` | 2026-10-19: historical 600-entry audit complete; method24 two-cell Kalshi recommendation beat market scores in later W4/W5; retrospective; protected tests closed; two final reviews passed |
| correlation_arb | `children/correlation_arb/docs/RE-ENTRY.md` | 2026-10-09: child + ADR-0256/0257 on main; Stage 1 kill test next |
| statistical_arbitrage | `children/statistical_arbitrage/docs/RE-ENTRY.md` | 2026-10-21: prediction-only studies ADR-0272..0275: ATM-spread reversion; best now the pooled-scaled blend on the vol-scaled label (ATM 20d R²oos 0.25→0.27, the most robust pass); Theta Greeks/flow/market features add nothing; skew none; no edge claim |
| stock_options | `children/stock_options/docs/RE-ENTRY.md` | 2026-10-01: bootstrap + AMZN conversion done; gap audit rerun next |
| intraday_equities | `children/intraday_equities/docs/RE-ENTRY.md` | 2026-10-12: fill-window relabel: next-bar-fill IC 0.01-0.02 (5-min rows), 0.002-0.005 (1-min rows); no fill-reachable edge |
| pmquant | `children/pmquant/docs/RE-ENTRY.md` | 2026-09-04: child landed; waits on PREDEXON_API_KEY and the real-data twin |
| portfolio_policy | `children/portfolio_policy/docs/RE-ENTRY.md` | 2026-10-28: Schwab positions + daily bars + transactions → per-lot tax, vol/beta, hurdle, decision, summary, verdict page (`weekly.sh`); every slice Sonnet-loop CLEAN + final join lens CLEAN; merged; no real pull yet |
| intraday_poc | `children/intraday_poc/docs/RE-ENTRY.md` | legacy worked example; no active lane |

## Core (dskit itself)

**Now.** 2026-10-26: local main caught up 166 commits to published `8e14281c`.
The completed Fable and scenario work is included. Removed 37 contained local
branches and two contained remote branches; all 88 worktree directories remain.
The 12 unmerged branches, two unmerged detached histories and 20 dirty checkouts
are preserved (overlapping counts). No additional candidate with verified current
two-lens approval was established. No runtime or study changes in this sweep.

**Next.** Reconcile retained work one bounded candidate at a time, then obtain
current required reviews before merging. Dirty checkouts need their owner's
closeout; do not reset or discard them. Prior ADR-0268 performance follow-ups
and known baseline test failures remain historical open work, not retested here.

**Blocked / owner decisions.** No additional merge-ready candidate established;
see the exact retained heads and review gaps in the sweep memo.

**Read first.** [Branch sweep receipt](memos/2026-10-26-repository-branch-sweep.md).

**Do not.** Delete unmerged/dirty work or worktree artifacts; reopen protected
data windows or run studies as part of cleanup. Historical core state is in
`docs/archive/re-entry/dskit-core.md` when needed.

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

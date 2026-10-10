# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-10-10: repo cleanup landed; owner purges 7 remote branches |
| index_options | `children/index_options/docs/RE-ENTRY.md` | 2026-10-09: replay v2 net negative under the accepted fill; owner decides selector direction |
| crypto_trading | `children/crypto_trading/docs/RE-ENTRY.md` | 2026-10-08: 15m kill test, development read: no model beats the Kalshi mid; held-out is the owner's call |
| correlation_arb | `children/correlation_arb/docs/RE-ENTRY.md` | 2026-10-09: child + ADR-0256/0257 on main; Stage 1 kill test next |
| stock_options | `children/stock_options/docs/RE-ENTRY.md` | 2026-10-01: bootstrap + AMZN conversion done; gap audit rerun next |
| intraday_equities | `children/intraday_equities/docs/RE-ENTRY.md` | 2026-09-29: joint-MIO speedups landed; folds 2-19 run waits on owner rulings |
| pmquant | `children/pmquant/docs/RE-ENTRY.md` | 2026-09-04: child landed; waits on PREDEXON_API_KEY and the real-data twin |
| intraday_poc | `children/intraday_poc/docs/RE-ENTRY.md` | legacy worked example; no active lane |

## Core (dskit itself)

**Now.** 2026-10-10 cleanup: `docs/RE-ENTRY.md` was 469,597 lines (4,431 unique)
after keep-both merges doubled it at every merge; it is now this index plus one
note per lane, history in `docs/archive/re-entry/`. Root `CLAUDE.md`/`AGENTS.md`
shrank to the rules; the standards moved to `docs/standards/`. The branch
`claude/index-options-perf-pilots` is merged. ADR-0256/0257 are on main;
ADR-0258 is built in the index_options lane. Open pack minors: `TODO.md`.

**Next.** Owner, from a machine with push rights (the cloud proxy refuses ref
deletion): delete the contained remote branches `claude/correlation-arb-research`,
`claude/crypto-trading-child`, `claude/ct-kalshi-history`, `claude/ct-parquet-io`,
`claude/ct-restwindow`, `claude/ct-zipcsv`, `claude/index-options-perf-pilots`;
push the local tag `archive/codex-index-options-iterative-validation`, then delete
`codex/index-options-iterative-validation` (superseded; ADR-0215's
`CDFEstimatorModel` lives only on that tag).

**Blocked.** Nothing.

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

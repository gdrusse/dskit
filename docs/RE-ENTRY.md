# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-10-29: machine hook names the host at session start; ADR-0284 two-host dispatch built, parked unmerged on `docs/adr-two-host-runner` (4 Majors, path mapping) |
| index_options | children/index_options/docs/RE-ENTRY.md | 2026-10-31: ADR-0288 Q2 split-aware replay reviewed C0/M0, 402 tests; pinned OCC source and new document ready; C: compact and market run pending |
| crypto_trading | `children/crypto_trading/docs/RE-ENTRY.md` | 2026-10-19: historical 600-entry audit complete; method24 two-cell Kalshi recommendation beat market scores in later W4/W5; retrospective; protected tests closed; two final reviews passed |
| correlation_arb | `children/correlation_arb/docs/RE-ENTRY.md` | 2026-10-09: child + ADR-0256/0257 on main; Stage 1 kill test next |
| statistical_arbitrage | `children/statistical_arbitrage/docs/RE-ENTRY.md` | 2026-10-21: prediction-only studies ADR-0272..0275: ATM-spread reversion; best now the pooled-scaled blend on the vol-scaled label (ATM 20d R²oos 0.25→0.27, the most robust pass); Theta Greeks/flow/market features add nothing; skew none; no edge claim |
| stock_options | `children/stock_options/docs/RE-ENTRY.md` | 2026-10-01: bootstrap + AMZN conversion done; gap audit rerun next |
| intraday_equities | `children/intraday_equities/docs/RE-ENTRY.md` | 2026-10-12: fill-window relabel: next-bar-fill IC 0.01-0.02 (5-min rows), 0.002-0.005 (1-min rows); no fill-reachable edge |
| pmquant | `children/pmquant/docs/RE-ENTRY.md` | 2026-09-04: child landed; waits on PREDEXON_API_KEY and the real-data twin |
| portfolio_policy | `children/portfolio_policy/docs/RE-ENTRY.md` | 2026-10-30: first real Schwab verdict ran (46 lots, all hold, `--no-strategy`); zero-cash buys valued under `allow_zero_cash`; owner sets market blocks next |
| intraday_poc | `children/intraday_poc/docs/RE-ENTRY.md` | legacy worked example; no active lane |

## Core (dskit itself)

**Now.** 2026-10-29: `tools/session/machine.sh` names the host at session start
(Claude `SessionStart` hook on main, `c1dc38ee`; Codex reads it through a
user-level `~/.codex/hooks.json`, once trusted in `/hooks`). ADR-0284, two-host
fold dispatch over SSH with a path map below identity, was approved and built on
branch `docs/adr-two-host-runner` (head `4e2a338e`, base `db39f872`, Mac clone
only), then parked unmerged: review round 2 left 4 Majors, all path mapping. Only
the ADR is on main. Compute stays on the PC, driven from the Mac by `ssh desktop`.
Mac-only base reds, not regressions: 9 `test_folds.py` and 2 intraday cap tests
(macOS refuses `RLIMIT_AS`), production 133 failed + 23 errors
(`RuntimeFingerprint` libc), no `hypothesis`. `tools/sweep/sweep` crashes when
its git grep times out.

**Next.** PC/WSL clones: pull for the machine hook. Retained work from the
2026-10-26 branch sweep still reconciles one candidate at a time.

**Blocked / owner decisions.** ADR-0284: one redesigned round (inventory every
recorded-path reader and writer first; drop it if review still finds a Major)
or leave it parked.

**Read first.** ADR-0284's "Review checkpoint" in the decision log;
[Branch sweep receipt](memos/2026-10-26-repository-branch-sweep.md).

**Do not.** Merge the ADR-0284 branch without a fresh two-lens review at zero
Critical/Major (rebase it first; main's ADR-0284 text wins). Delete unmerged or
dirty work. Run capped folds on a macOS host. History:
`docs/archive/re-entry/dskit-core.md`.

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

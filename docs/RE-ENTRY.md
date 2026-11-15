# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-11-15: ADR-0296 device selection and ADR-0317 fault study reviewed and integrated; storage/worktree receipts retain unfinished work and frozen runtimes. |
| index_options | children/index_options/docs/RE-ENTRY.md | 2026-11-15: stress replay and fitted-method refusal fix integrated; ADR-0314 comparison remains provisional pending shared hedge defect audit. |
| crypto_trading | `children/crypto_trading/docs/RE-ENTRY.md` | 2026-10-19: historical 600-entry audit complete; method24 two-cell Kalshi recommendation beat market scores in later W4/W5; retrospective; protected tests closed; two final reviews passed |
| correlation_arb | `children/correlation_arb/docs/RE-ENTRY.md` | 2026-10-09: child + ADR-0256/0257 on main; Stage 1 kill test next |
| statistical_arbitrage | `children/statistical_arbitrage/docs/RE-ENTRY.md` | 2026-10-21: prediction-only studies ADR-0272..0275: ATM-spread reversion; best now the pooled-scaled blend on the vol-scaled label (ATM 20d R²oos 0.25→0.27, the most robust pass); Theta Greeks/flow/market features add nothing; skew none; no edge claim |
| stock_options | `children/stock_options/docs/RE-ENTRY.md` | 2026-10-01: bootstrap + AMZN conversion done; gap audit rerun next |
| intraday_equities | `children/intraday_equities/docs/RE-ENTRY.md` | 2026-10-12: fill-window relabel: next-bar-fill IC 0.01-0.02 (5-min rows), 0.002-0.005 (1-min rows); no fill-reachable edge |
| pmquant | `children/pmquant/docs/RE-ENTRY.md` | 2026-09-04: child landed; waits on PREDEXON_API_KEY and the real-data twin |
| portfolio_policy | `children/portfolio_policy/docs/RE-ENTRY.md` | 2026-10-30: first real Schwab verdict ran (46 lots, all hold, `--no-strategy`); zero-cash buys valued under `allow_zero_cash`; owner sets market blocks next |
| intraday_poc | `children/intraday_poc/docs/RE-ENTRY.md` | legacy worked example; no active lane |

## Core (dskit itself)

**Now.** 2026-11-15: reusable device selection (ADR-0296, candidate
`94e785ac`) and saved-fit forecast faults (ADR-0317, `c38e2b28`)
passed two fresh independent final lenses each, with zero Critical/Major.
Explicit-device defaults and frozen studies are unchanged. The stress replay
and fitted-method refusal fix are also integrated; PR #19 is merged.

**Next.** Reconcile remaining reusable candidates individually from the
review-eligibility manifests. Keep canonical data outside new worktrees and
check the storage guard before bulk work. Maintenance receipts distinguish
retired code copies from retained results, dirty work and runtime pins.
The shared .venv retains an editable import path to the preserved
`/home/russell/wt/claude-sonnet-5-5` checkout; verify import locations before
using new main code and do not repoint an environment underneath running jobs.

**Blocked / owner decisions.** Unreviewed experiments retain their gates.
ADR-0284's last recorded Mac-only dispatch review has four path-mapping
Majors; this maintenance did not clear it. Windows C: remains low even after
WSL files were removed; compaction needs a planned interruption.

**Read first.** ADR-0296/0317 in `docs/architecture/decision-log.md`;
`/home/russell/storage-investigation/2026-11-15/worktree-closeout/maintenance-summary.json`;
`/home/russell/storage-investigation/storage-policy.json`.

**Do not.** Rewrite frozen source pins to accept changed code, delete protected
results or unique unfinished work, or treat development review as real-run or
deployment approval. ADR-0284 still needs fresh clean reviews. Earlier notes:
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

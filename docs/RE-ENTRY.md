# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-11-15: reviewed integrations on main; C: compaction recovered 113.2 GiB; storage monitoring installed; automatic reclaim and legacy migration remain paused. |
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

**Now.** 2026-11-15: ADR-0296 device selection (`94e785ac`) and
ADR-0317 saved-fit forecast faults (`c38e2b28`) remain reviewed and integrated,
with two clean final lenses each. PR #19 is merged; explicit-device defaults
and frozen study identities remain unchanged. Host maintenance recovered
113.2 GiB on C: after WSL compaction; recorders and mounts were restored.
Disk/RAM admission, a data catalog, shared agent guidance and a 15-minute
storage monitor are installed locally; those workstation tools are outside
this repository. The alert/hook follow-up passed 25 focused tests.

**Next.** Continue reusable candidates only through their existing review
gates. Keep canonical data outside new worktrees and check the storage guard
before bulk work. Classify legacy data before migration. The shared .venv
still imports from the preserved `/home/russell/wt/claude-sonnet-5-5`
checkout; verify imports before using new main code and never repoint an
active environment. Existing worktree receipts retain unique work and pins.

**Blocked / owner decisions.** Storage work is incomplete and paused at the
owner's request. Retirement of the empty 384 GiB D: image and its six pending
ADR-0313 jobs was approved, as was a WSL upgrade and isolated reclaim test;
none was executed before the pause. Keep the current MIO batch running.
WSL remains 2.7.10; automatic disk shrinking is not enabled, and a full distro
backup was not made. Resume maintenance only when the owner resumes it.
ADR-0284 still has four recorded Mac path-mapping Majors; this work did not
clear them. Unreviewed experiments retain their design and study gates.

**Read first.** ADR-0296/0317 in `docs/architecture/decision-log.md`;
`/home/russell/storage-investigation/2026-11-15/worktree-closeout/maintenance-summary.json`;
`/home/russell/storage-investigation/storage-policy.json`;
`/home/russell/storage-investigation/storage-migration-plan.json`;
`/home/russell/storage-investigation/2026-11-15/followup/verification.json`.

**Do not.** Count WSL free space as additional C: capacity, claim automatic
reclaim is active, delete protected results or rewrite frozen source pins.
Development review is not real-run or deployment approval. Earlier notes:
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

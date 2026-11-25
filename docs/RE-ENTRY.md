# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-11-24: non-DSKit relocation complete; shared data retained; C17.3 GB/D734.2 GB free; upgrade/compaction not performed. |
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

**Now.** 2026-11-24: non-DSKit relocation completed: 10.64 GB verified on D,
old paths preserved, DSKit/shared inputs retained on C. Gears5 and the verified
empty384 GiB D image were retired by owner request. Windows measured C17.3 GB
and D734.2 GB free. Normal admission passes; the migration exception is retired.
No WSL shutdown, upgrade or compaction occurred. The index_options lane holds
the unfinished mathematical MIO and cadence-study re-entry.

**Next.** Follow that lane note; check storage and host RAM before bulk work.
The shared .venv imports from a preserved older checkout. Bind imports explicitly
to reviewed code; never repoint an active environment.

**Blocked / owner decisions.** The WSL upgrade/sparse-reclaim pilot and full
backup remain unperformed; wrap does not resume them. Preserve other sessions'
unpublished commits, active jobs and frozen pins. Earlier ADR-0284 Mac
path-mapping findings were not addressed here.

**Read first.** `/home/russell/storage-investigation/non-dskit-migration-20261124/completion-receipt.json`;
`/home/russell/pmquant/docs/data-locations.md`;
`/home/russell/storage-investigation/storage-policy.json`;
`/home/russell/storage-investigation/storage-migration-plan.json`.

**Do not.** Add WSL free space to C capacity, claim full backup or automatic
reclaim was enabled, delete protected results, or treat development review as
empirical evidence or deployment approval. Normal reserves are C10/D150 GiB.

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

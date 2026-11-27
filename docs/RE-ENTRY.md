# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-11-27: Static MIO template and validator reviewed at `211dedc` on the local branch. State transitions and custom algorithm design are next. |
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

**Now.** ADR-0329 provides a seven-section template and a version 1 JSON
declaration for one static optimization instance. The Python API and CLI
validate references, index scopes, fixed bounds and derived cycles. The CLI
reads strict JSON. Mathematical text stays opaque and requires human review.

Candidate `211dedc` is reviewed on the local branch, with 68 focused
formulation and strict JSON checks passing. Both final Terra reviewers
reported zero findings after one correction batch. The separate purity
check still reports the baseline `libs/parquet.py` import of
`dskit.onboarding.snapshot`, reproduced on the unchanged base.

At 10,000 declarations, median validation took 31.851 ms for the forward
chain and 33.110 ms for the wide sparse case. Peak process RSS was
58.84 MiB including startup and fixture construction. The cold example
CLI median was 216.892 ms. These are measurements of synthetic fixtures.
The preceding Julia runtime work landed in PR #20.

**Next.** Review the design for state transitions between instances and
custom algorithms with the owner before Julia generation.

**Blocked / owner decisions.** The real MIO formulation still needs decisions
on cash, joint scenarios, CVaR and hedges.

**Read first.** [Template and validator](../dskit/pipeline/EXTENDING.md#static-optimization-formulation),
[facility example](../examples/pipeline/formulation-facilities.json),
[ADR-0329](architecture/decision-log.md#adr-0329--declare-and-validate-one-static-mixed-integer-optimization-formulation-accepted-2026-11-27),
and [review and benchmark evidence](</home/russell/data/dskit/runs/mio-formulation/v20261127/HANDOFF.md>).

**Do not.** Use structural validation as proof of mathematical correctness
or use fixture timings to infer solver performance. Obtain separate approval
before implementing a real MIO port, running a replay or deploying.

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

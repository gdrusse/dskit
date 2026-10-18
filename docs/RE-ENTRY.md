# RE-ENTRY — where every lane stands

One screen, rewritten at every `/wrap`. At session start read THIS file, then
ONLY the note of the lane you are about to work. Nothing else: the archive is for
history questions, the memos and ADRs are reached from the lane note's links.

| Lane | Note | State (one line, dated) |
|---|---|---|
| dskit core | "Core" below | 2026-10-16: ADR-0268 perf sweep landed 471dd655 (26 units, two clean lenses each); owner purges remote branches |
| index_options | `children/index_options/docs/RE-ENTRY.md` | 2026-10-16: R6seed29 packet reviewed;44tests,107pins; exactmanifest/order approval pending; no training/R5retry |
| crypto_trading | `children/crypto_trading/docs/RE-ENTRY.md` | 2026-10-18: training/window explanation complete; exact scores and selection provenance; two reviews, no open packet findings; retain24/fixed03, no confirmed edge |
| correlation_arb | `children/correlation_arb/docs/RE-ENTRY.md` | 2026-10-09: child + ADR-0256/0257 on main; Stage 1 kill test next |
| stock_options | `children/stock_options/docs/RE-ENTRY.md` | 2026-10-01: bootstrap + AMZN conversion done; gap audit rerun next |
| intraday_equities | `children/intraday_equities/docs/RE-ENTRY.md` | 2026-10-12: fill-window relabel: next-bar-fill IC 0.01-0.02 (5-min rows), 0.002-0.005 (1-min rows); no fill-reachable edge |
| pmquant | `children/pmquant/docs/RE-ENTRY.md` | 2026-09-04: child landed; waits on PREDEXON_API_KEY and the real-data twin |
| intraday_poc | `children/intraday_poc/docs/RE-ENTRY.md` | legacy worked example; no active lane |

## Core (dskit itself)

**Now.** 2026-10-16: the ADR-0268 equivalence-preserving performance sweep landed
(`main` fast-forwarded to `471dd655`, from `claude/perf-sweep-20261014`). 34 units:
26 landed, each through its own merge commit carrying the review record (frozen
candidate, two Sonnet skeptic lenses, zero unresolved Critical/Major); 5 dropped
(`pipeline-trust`, `pipeline-kinds`, `child-index-options-a/-b`, `libs-pyomo-rl-nlp`);
3 no change (`production-execution`, `-guards`, `-cli-bundles`). Merged vs main:
core tests and all 8 child suites fail exactly main's known reds and pass 516 more
(the new pins). Record and per-unit outcomes: ADR-0268 in the decision log.
Behaviour change to know: the faster sweep scorer can finish a big commit's
commit-msg check that used to hit the hook's 12 s cap and pass unchecked.
Known reds on main, untouched, each its own bounded task:
`test_purity.py::test_core_never_reaches_outside_the_toolkit_at_any_depth`
(`libs/parquet.py` imports `dskit.onboarding.snapshot`) and, on Python 3.13,
`test_uncertainty_set.py::...::test_replace_without_the_arrays_cannot_recover_them`.

**Next.** Owner, from a machine with push rights: delete the contained remote
branches listed in the archived 2026-10-11 note, plus `claude/perf-sweep-20261014`
if still present. Optional follow-ups, each its own bounded task: the deferred
pins named in the ADR-0268 merge commits; unproven saved patches (open-order index
for `PaperExecutor._march`, money-check fast path) if long paper replays matter.

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

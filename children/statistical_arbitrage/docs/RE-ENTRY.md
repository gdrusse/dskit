# RE-ENTRY — statistical_arbitrage

## Now

2026-10-20: frozen code candidate `76b4179e1719fdf6598a8b40930069be180eb55f`
has two independent clean skeptic lenses: final_v6_correctness (112 targeted
tests) and final_v6_integration (190 focused tests, Ruff clean), zero unresolved
Critical/Major. The v6 quote replay and evaluation memo are complete.
ATM precision returned -21.96%, skew precision -33.94% in 2020–2025;
all-pairs baselines lost -23.97%/-35.88%, correlation gates lost
-22.37%/-32.47%, equal-weight tech gained +282.23%, QQQ +197.61%.
No deployable edge. Memo: `docs/memos/2026-10-20-tech-phase-one-evaluation.md`.
Branch integration is pending at the time of this note.

## Next

Integrate the reviewed candidate and this editorial evidence, push main, verify
remote containment, then purge the contained task branch/worktree. Any later
research requires a new pre-registered hypothesis and untouched cohort.

## Blocked / owner decisions

No code/review blocker remains. Paper/live edge claims remain blocked by
negative net results and no untouched final test. Disclosed Minor backlog:
split crossings unmodeled; hedge cashflows displayed at close, not ex-date;
stale stock-source prose. No v6 position overlapped a known split.

## Read first

- Root AGENTS.md and docs/skills/implementation-workflow.md
- docs/skills/skeptic-review.md
- docs/memos/2026-10-20-tech-phase-one-evaluation.md
- docs/architecture/decision-log.md, ADR-0270 and ADR-0271

## Do not

Do not revive 2026-10-19 diagnostics or v4/v5 results: causal and execution
corrections voided them. Do not infer neural epochs from solver iterations,
or describe EOD NBBO proxies as live fills.

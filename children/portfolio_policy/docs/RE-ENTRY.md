# RE-ENTRY — portfolio_policy

Updated: 2026-10-30 by Claude Opus 5.5 (wrap after the first real Schwab run).
Rewritten whole at every `/wrap`; the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

The owner's first real hold verdict ran on 2026-10-30 from the desktop (WSL2,
`ssh desktop`, worktree `~/wt/portfolio-first-pull-20261029`, root `./ob`):
`bash weekly.sh 2026-10-30 --no-strategy` -> 46 lot rows, all `hold` (no
strategy declared), page `report/portfolio_policy-hold-report-2026-10-30-fa7996e1.md`
(gitignored; the repo is PUBLIC, so no values, symbols or prices in commits).
Evidence: 5 positions reconciled to lots (max |basis_gap| 4.8e-05), 18 aggregate
fallbacks, 1 zero-cash buy, risk inputs as of 2026-10-28. Record: the
"First real run from Schwab" section of
`docs/memos/2026-10-27-adr-0279-base-machinery.md`.
Four ADR-0279 amendments made it run, each Sonnet-reviewed to CLEAN and on
`main`: empty account; funds/ETFs in the verdict; `--no-strategy` (null
`redeploy_return`, `78102357`); a buy that moved no cash, `TaxLots`
`allow_zero_cash` (`0fef0fed`, final lens CLEAN, 19 mutants killed).

## Next

- Each week: README step 3 pulls (positions are repeated by the script), then
  `bash weekly.sh <UTC day> --no-strategy`. Pull bars in New York daytime
  (see Blocked).
- Owner sets the market blocks (the shipped ones give the market ~3%, under the
  4% risk-free rate, so higher beta reads as lower expected return).
- Once a strategy has a year of weekly net returns: register `active`
  (README step 2) and drop `--no-strategy`.
- A week after a run: score it with `run-decision-outcomes.json`.

## Blocked / owner decisions

- `schwab_daily` refuses when, just after midnight New York, Schwab returns the
  latest session twice (00:00 Central and 00:00 Eastern stamps, same close,
  different volume). Which candle wins is an open connector decision.
- Market blocks, `risk_free`, horizon, `redeploy_cost`, `risk_premium`.
- Lot method per account (FIFO ships; average cost shows as a basis gap).
- Which child's backtest feeds `active`, and its honest `trials`.
- Backlog (Minor): a null-amount buy at price 0.0 opens a zero-basis lot;
  `averagePrice` 0 reads as a 100% gain; the `in` clause value is unvalidated;
  the page does not tell "none declared" from "withheld"; no shipped test of
  report/outcomes over fund rows; numpy/pandas runtime deps undeclared (a
  separate session holds that change, awaiting the owner).

## Read first

- `README.md` ("First real run from Schwab", "Reading the verdict", Limits)
- `docs/memos/2026-10-27-adr-0279-base-machinery.md` (incl. the first real run)
- `docs/architecture/decision-log.md` § ADR-0279 and amendments
- `docs/research/` — hold-return (A0001), position-summary (A0002),
  risk-inputs (A0003), tax-lots (A0004)

## Do not

- No live orders, ever (research child; decisions are advisory).
- No personal figures, symbols with prices, or account facts in commits: the
  repo is public. Numbers go to the owner in chat.
- Do not run the Schwab OAuth login for the owner; it is their step (the
  refresh token lapses after 7 days).
- Do not change the decision day in one place only (five, pinned by a test).

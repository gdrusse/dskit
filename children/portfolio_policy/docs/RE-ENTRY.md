# RE-ENTRY — portfolio_policy

Updated: 2026-10-28 by Claude Fable 5.1 (wrap after the final convergence lens).
Rewritten whole at every `/wrap`; the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

Everything needed to pull from Schwab and answer "what is it worth to hold
this" is on `main`, skeptic-reviewed: each slice had its own Sonnet
review/fix loop closed CLEAN (base 4 rounds; summary 2; risk inputs 2; tax
lots 2; verdict 3) and a final Sonnet lens on the integrated join returned
CLEAN (1006 dskit + 118 child tests; one lot hand-verified from trades to
verdict). Tier 2: `schwab_positions`, `schwab_daily`, `schwab_transactions`
on one OAuth token. Tier 1 (by import path, `pure` except the writer):
`liquidation.py`, `hold_return.py`, `active_hurdle.py`,
`decision_outcomes.py`, `position_summary.py`, `tax_lots.py`,
`hold_verdict.py`, `kinds_flow.DateOrderedGroupBy`. Child (JSON + one
script): `run-hold-vs-redeploy.json` (23 nodes: positions + trades → FIFO
lots → per-lot tax; Schwab daily bars → 252-day vol and Blume beta vs SPY;
market blocks × beta → hold return; onboarded backtest → deflated hurdle;
decide per lot → `decisions/`, `risk/`, `summary/`), `run-hold-report.json`
(→ `report/<run>.md`, one line per lot: HOLD/EXIT with the few numbers),
`run-decision-outcomes.json` (scores past hold forecasts), `weekly.sh <day>`.
The decision day lives in five pinned places; `weekly.sh` moves them all.
No real Schwab pull yet; every rate in the documents is a placeholder.
Record: `docs/memos/2026-10-27-adr-0279-base-machinery.md`.

## Next

Owner's first real run: README "First real run from Schwab" (Schwab app
keys → `authorize` once → register and `acquire` positions, trades, daily
bars → set the placeholders and the day → `bash weekly.sh <day>` → read
`report/<run>.md`); record it with the memo skill. On that first pull
check: `lot_positions` `basis_gap_max` (lot method), the trades census
(`COLLECTIVE_INVESTMENT` for ETFs?), `risk/<run>.jsonl` `as_of`, and a
holding with a recent split.

## Blocked / owner decisions

- Placeholders: market blocks, `risk_free`, tax rates, horizon, `trials`,
  `min_dsr`, `min_periods`, `redeploy_cost`, `risk_premium`, trades `start`.
- Lot method per account (FIFO ships; average cost shows as a basis gap);
  `allow_incomplete` if Schwab serves < full history; INVALID transactions.
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Open Minors from the final lens: the aggregate-path recipe omits the
  report document's keys; an empty snapshot day fails late in `weekly.sh`;
  same-day re-pull is a backfill no-op (use `--mode live`).
- Cadence: a weekly scheduled `pipeline run` vs a serve document (deferred).

## Read first

- `README.md` ("First real run from Schwab", "Reading the verdict", Limits)
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments
- `docs/research/` — hold-return (A0001), position-summary (A0002),
  risk-inputs (A0003), tax-lots (A0004)

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not type a beta or a return into the document: all arrive wired.
- Do not change the decision day in one place only (five, pinned by a test).

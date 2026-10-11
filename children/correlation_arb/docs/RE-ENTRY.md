# RE-ENTRY — correlation_arb

Updated: 2026-10-10 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/correlation_arb.md` (dskit repo).

## Now

- The child landed on main 2026-10-09 (Stage-0 source configs on existing
  packs, survey, inventory, staged plan) with five additive dskit modules:
  `pipeline/binary_decisions.py` + `libs/binary_market_rows.py` (ADR-0256);
  `pipeline/digital_bounds.py`, `pipeline/binary_curve.py`,
  `libs/binary_coherence.py` (ADR-0257). No existing dskit code changed.
- Owner rulings 2026-10-09: a one-sided quote stays in its relations with fair
  value `None` and status `bad_quote`; projection `min_spread` is in [0.01, 1]
  and every projection is certified by a duality-gap bound, refused above 1e-2.
- Final candidate `93f81a31`: correctness lens 0 Critical / 0 Major / 2 Minor /
  3 Nit; tests lens 0 / 0 / 6 / 6. Child suite 67 passed, 9 skipped. Nothing is
  pulled beyond Stage 0.

## Next

Stage 1, the kill test, per `docs/plans/2026-10-07-implementation-plan.md`:
1. wire the venue field map into `BinaryMarketRows`, `QuoteBarRows` and
   `DecisionRows` in the child configs;
2. run `DigitalBounds` against the binary quotes.

## Blocked / owner decisions

Open minors (full ledger in ADR-0257): the certificate rarely refuses a correct
solve (3 in 16k worlds with one-sided members) and a refused projection loses
the LP verdict; `FeeColumns` uses the newest schedule and treats a missing lag
as 0 (look-ahead-optimistic); inclusive end stamps read up to one stamp unit
ahead; `CurveSurvival` crashes on log-axis underflow; test gaps (relation index
after a skip, `by_status`, leg-order determinism).

## Read first

- `docs/plans/2026-10-07-implementation-plan.md`
- `README.md`
- ADR-0256 and ADR-0257 in `docs/architecture/decision-log.md`

## Do not

- No trading, no live orders; Stage 0 pulls only what its configs declare.

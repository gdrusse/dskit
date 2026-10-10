# RE-ENTRY archive — correlation_arb

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

# Active handoff — 2026-10-09: correlation_arb child + ADR-0256/0257, merged to main

**Landed.** New child `children/correlation_arb` (Stage-0 source configs on existing packs, survey, inventory, staged
plan). Five additive dskit modules:
- `pipeline/binary_decisions.py` and `pipeline/libs/binary_market_rows.py` (ADR-0256);
- `pipeline/digital_bounds.py`, `pipeline/binary_curve.py` and `pipeline/libs/binary_coherence.py` (ADR-0257).

The ADRs were renumbered from 0248/0249 at merge. No existing dskit code changed (docs only).

**Owner rulings 2026-10-09.**
- One-sided quotes: (B). The contract stays in its relations, with fair value None and status `bad_quote`.
- Projection: `min_spread` must be in [0.01, 1]. Every projection is certified by a duality-gap bound and refused
  above 1e-2.

**Review.** Sonnet builders and lenses. The QP hang is fixed (whitened QP and HiGHS limits). The wrong-optimum
family is closed by the owner's domain and certificate. Final candidate `93f81a31` (plus comment-only `aba047bc`):
- correctness lens: 0 Critical, 0 Major, 2 Minor, 3 Nit;
- tests lens: 0 Critical, 0 Major, 6 Minor, 6 Nit.

The dskit suites' FAILED set equals origin/main's (environment: missing optional libraries). The child suite gives
67 passed, 9 skipped.

**Open Minors** (full ledger in ADR-0257):
- the certificate rarely refuses a correct solve (3 in 16k worlds with one-sided members), and a refused projection
  loses the LP verdict;
- FeeColumns uses the newest schedule and treats a missing lag as 0, both look-ahead-optimistic;
- inclusive end stamps are read up to one stamp unit ahead;
- `CurveSurvival` crashes on log-axis underflow;
- test gaps (relation index after a skip, `by_status`, leg-order determinism).

**Next.** correlation_arb Stage 1 (kill test), per `children/correlation_arb/docs/plans/2026-10-07-implementation-plan.md`:
1. Wire the venue field map into `BinaryMarketRows`, `QuoteBarRows` and `DecisionRows` in child configs.
2. Then run `DigitalBounds` against the binary quotes.

This file was rebuilt at merge: main's copy held three concatenated halves from two keep-both merges.

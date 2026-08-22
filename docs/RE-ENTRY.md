# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `docs/package2-design` (merged to `main`) · **Tests:** 1506 pass, 82 skip

**Landed:** Package 2 **designed** (not built). Research-grounded
(connector contracts, outbox/maildir handoff, bitemporal/snapshot/validation
patterns) → `docs/architecture/onboarding-design.md` + ADR-0012…0015
(**proposed, awaiting ratification**) + `onboarding-model.json` — the P2
domain model as config, validates against the engine (`a8775903`).
Key rulings: pull-scan handoff (published root IS the outbox); P2 reuses
the assets engine; **backfill vs live is a first-class declared `mode`**
(user requirement) with per-(source, stream, mode) checkpoints and
`(effective_date, acquired_at)` on every record. OQ-2/4/6 closing on
those ADRs; OQ-7 open with a leaning (P2 entity-free). ADR-0011 logged
earlier: store packs deferred pending OQ-4 (now settling).

**Next:** user ratifies/amends ADR-0012…0015 and the OQ-7 leaning → then
build `dskit/onboarding` in the proven loop (brief → discuss → approve →
write, one file at a time), plan-first per the working agreement.

**Decisions awaiting user:** ratify ADR-0012…0015; confirm OQ-7 leaning.

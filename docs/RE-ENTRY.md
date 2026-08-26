# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `docs/pmquant-child-proposal` (merged to `main`) · **Tests:**
children + purity gates green (20 passed); docs-only change, so the full
suite was not re-run · **ruff:** untouched (no code).

**Landed this round: the pmquant child design proposal** —
`docs/children_design_proposals/pmquant.md` (~1,270 lines), the plan of
record for building `children/pmquant/`. No code was written; nothing in
`dskit/` changed.

- **What it is.** pmquant — the Kalshi/Polymarket ladder-mispricing
  program — re-expressed *entirely* in dskit's three seams: 6 connectors,
  **38 node kinds** in four modules, an asset model, and ~20 JSON
  documents. Nothing is a port: every capability is a connector, a kind,
  an asset-model kind, or a config. Zero dskit edits are needed to start.
- **The doctrine survives translation.** §9 maps every load-bearing
  invariant to the mechanism that enforces it — event-clustered testing,
  never-pool, all-21-leads, N0-only, ≥50-or-no-test, write-once banking
  with monotone look records, frozen-ϖ, exact venue-dispatched fees,
  strict-PIT replay.
- **Thirteen generic gaps** are flagged as TODOs (now in `TODO.md`, full
  statements in §13), each with a named dskit home and interim child-side
  handling. **TODO-2** (studentized cluster bootstrap-t on the owned
  `stat_test`) is the one that structurally matters: until it lands, the
  D-138 deploy verdict cannot authorize sizing inside one document, so
  deployment stays operator-mediated.
- **Eight owner questions** are open in §14 — docs/22 registration, `q_hold`,
  cross-venue dedup, the child coverage bar, MIO tuning, the recorder host,
  CI-robust entry activation, and **Tier-B ratification** (bulk book
  streams bypass onboarding's WORM chain on a stated size argument:
  ~96× on the gz archives).
- `docs/architecture/child-gap-pmquant.md` now carries a supersession note
  pointing at the proposal; `CLAUDE.md`'s layout tree gains the directory.

**How it was validated: eleven adversarial review rounds**, five lenses
each (framework-fit, completeness, doctrine, purity, precision), every
finding verified against both repos. ~150 defects found and fixed; round
11 returned **CLEAR on all five lenses**.

**Next session:** the proposal is PROPOSED, not ratified. It needs the
owner's read and the §14 answers before P0 (scaffold from `_skeleton`)
starts. Nothing is blocked in dskit itself.

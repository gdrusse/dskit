# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `feat/todo-graduation-adr-0033-0036` (merged to `main`) ·
**Tests:** full suite green (2389 passed, 108 optional-lib skips) ·
**ruff:** clean.

**Landed this round: the §14 rulings + four graduated TODO clusters
(ADR-0033…0036), adversarially reviewed.**

- **§14 answered.** All eight owner questions ruled conceptually and
  recorded in the proposal (`docs/children_design_proposals/pmquant.md`
  §14): hold-at-PROPOSED, `q_hold` unset, high-precision cross-venue
  dedup, coverage bar at graduation, no MIO HPO, recorder on a VPS
  (host TBD), E6a OFF, **Tier B ratified with sunset at TODO-8**. The
  proposal stays PROPOSED; P0 still needs the owner's go.
- **ADR-0033** — stats seam: `stat_test` evidence self-description, the
  studentized recentered cluster bootstrap-t as a `method` (closed
  tuple), `register_correction` + `weighted-bh` with a `weights` input
  port. **TODO-2, the deploy→size blocker, is closed.**
- **ADR-0034** — the `cal` split band: fourth split name in the val
  window's tail, trailing `cal_days` (+1 boundary discipline),
  straddle-ledger + planner + walkforward guards.
- **ADR-0035** — torch `monitor` + best-state restore; trainlog's
  silent fallback removed; divergence-safe metric monitors.
- **ADR-0036** — onboarding codec: extension-declared gzip, reserved
  `storage` config block, deterministic members, pre-commit decode
  guard. The ratified Tier-B sunset path exists.
- **Validated:** a 26-agent adversarial review (5 lenses, refute-by
  -default verification); 16 confirmed findings all fixed same-day, with
  review-amendment records inside each ADR. Identity/hash freeze held —
  zero movement for existing artifacts.

**Hands-off:** `children/intraday_poc/` is owned by another live process.
Two review findings live THERE (its score kinds refuse `"cal"`; its
replay reads `observations/*.jsonl` by literal glob and would miss
`.jsonl.gz`) — relayed to the owner, not fixed here. Neither bites
until a config opts in.

**Next session:** pmquant ratification/P0 on the owner's word (rulings
1–3, 5–7 bind the build); remaining §13 gaps 5/6/7/9/10/11/12 stay in
`TODO.md`, ADR-less until graduated.

# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 2092 pass, 95 skip · **ruff:** clean

**Landed (two parallel sessions, reconciled by merge `8e9705f`):**
(1) This session — ADR-0024 ported (split policies + event bounds:
`split_policy.py`, hash-neutral `policy` knob, `Node.event_bounds()` +
driver binding, straddle proof) and ADR-0025 ported faithfully from
the parent (declared `torch-train`/`torch-predict`/`transformers-fit`,
adapter seam, hash-pinned artifacts, `trainlog.py` TrainingCurve +
probability metrics; a document naming `torch.nn.Linear` trains with
no subclass) — each skeptic-reviewed, every surviving mutant killed by
a new pinned test. (2) The parallel session ("rl_stocks bones") —
ADR-0027 `walkforward` section + verb with the `val_start_ms` embargo,
ADR-0028 `sb3` pack, ADR-0029 `matplotlib` pack, ADR-0030 onboarding
`CoverageLedger`, regression metrics, and a re-inventoried
`child-gap-rl-stocks.md` (the real ~66k-LOC tree, superseding the
earlier stale-snapshot report). (3) The reconciliation — both sessions
implemented ADR-0025; the ratified ADR text ("port faithfully from
the parent") arbitrated: the parent port won its surfaces, 4 bespoke
tests were salvaged, everything else of theirs kept in full. The
embargo band is test-pinned to the policy-selected instant, and a
declared event policy alongside `walkforward` now refuses loudly
instead of silently running folds under `record` (ADR-0027 merge
note).

**Decisions awaiting user:** ADR-0026 (report renderer parity) — the
one remaining proposal. Deferred in TODO: driver curve-streaming,
walkforward policy pass-through, the move-plant listing residual.

**Next session:** rule on 0026 if wanted; incubate `children/pmquant`
per its sketch; consider a fold policy-pass-through ADR.

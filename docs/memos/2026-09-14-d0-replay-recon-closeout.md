# D0 — replay ADR/plan reconciliation closeout (2026-09-14)

## Outcome

Packet D0 produced and landed the replay ADR/plan reconciliation mapping
(`docs/memos/2026-09-14-d0-replay-adr-reconciliation.md`, candidate `fbdcfac`).
Documentation only: no replay code implemented, no signed/runtime identity or
protocol-semantic change, no R1–R3 closure claimed, and
`origin/codex/r5-replay-ops-20260911` is preserved.

## Reconciliation summary

- **ADR-0126 collision:** source `97900ef` uses ADR-0126 = "Deferred terminal
  projection for fenced replay" (replay V2); main uses ADR-0126 = "F4
  development-broker Major threat model is single-surface". The manifest's
  ADR-0126 citations are the replay ADR; the label is human-ambiguous but the
  manifest pins commit `97900ef…`, keeping the replay decision resolvable.
- **Collision-free identity:** replay V2 → `ADR-0127`; partially amended V1
  (source ADR-0124) → `ADR-0128`. `0127`/`0128` verified unallocated across all
  refs; main's highest accepted ADR is `0126`.
- **Still-missing V2 rules / already-integrated text / runtime-schema
  references:** inventoried in the memo; ADR-0120/0121 already integrated;
  V1 seams and the R5 child bypass present on main, all V2 symbols absent.

## Model / reviewer attribution

- Primary implementer: **deepseek-v4-pro** (`opencode-go/deepseek-v4-pro`).
- Phase 0 skeptic, corrector, and both final reviewers: **gpt-5.6-luna**
  (`opencode-go/gpt-5.6-luna`, reasoning high), fresh instances, sequential.

**Blocker recorded (owner-approved substitution):** `gpt-5.6-terra` is
unavailable in this environment (only `gpt-5.6-luna` in the gpt-5.6 family).
The owner authorized substituting `gpt-5.6-luna` (reasoning high) for all
review/correction roles; recorded as required.

## Review outcomes

- **Phase 0 skeptic** (lens: design): FAIL — 0 Critical, 3 Major, 1 Minor,
  1 Nit. Majors: incomplete V2 schema inventory; overstated "superseded" V1
  disposition; missing manifest-citation reconciliation. A gpt-5.6-luna
  corrector applied all five findings.
- **Final lens 1 — correctness/authority:** PASS — 0 Critical, 0 Major,
  1 Minor (deferred), 0 Nit. The deferred Minor notes §2 blends manifest-only
  route/R5 rules into the source-ADR inventory; facts verified correct.
- **Final lens 2 — test-quality/integration:** PASS — 0 Critical, 0 Major,
  0 Minor, 0 Nit.

Zero unresolved in-scope Critical/Major findings; one Minor backlog recorded
(source-vs-manifest attribution clarity in §2), deferred.

## Checks

```bash
git diff --check   # clean
# scope: exactly one file added (docs/memos/2026-09-14-d0-replay-adr-reconciliation.md)
# protected files unchanged: decision-log.md, manifest, review-evidence, path.csv
```

## Limits / next

- No code, no replay run, no R1–R3 closure; D0 is not R1's ReviewExit.
- Follow-on (recorded, not done here): migrate the manifest's `ADR-0126`
  citations to the renumbered replay ADR and reconcile any plan-blob/evidence
  identity referencing ADR-0126.
- `origin/codex/r5-replay-ops-20260911` preserved.
- Next: R1 (bootstrap and atomic storage) — still blocked on F2/F3/F4/F5a/C0
  dependencies per the master DAG.

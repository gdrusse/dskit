# Implement research build

Turns a research finding or plan document into code, following dskit's
existing tier-placement rules (see the root `CLAUDE.md`/`AGENTS.md`
"Working agreement" — not re-derived here).

## Do this

1. Read the source document in full — a `docs/research/*.md` finding, or a
   plan the user points at. Identify what it says should be built.
2. Decide placement using the repo's existing tiering rule: capability
   useful to a project that has never heard of this problem domain →
   graduates into `dskit/` (tier 1 core or tier 2 library pack, per the
   existing rules). Logic specific to this child's own domain → the child
   (`children/<child>/`), never `dskit/`.
3. **Always apply TDD** (`docs/skills/test-drive-development.md`) — write
   the failing test before the implementation, for every piece of code this
   produces. No exception.
4. **Always run the skeptic-review-loop** (`docs/skills/skeptic-review.md`)
   before considering the build done — independent review is not optional
   here, regardless of how the build was invoked.

## Approval gates

- **Invoked interactively** (a normal session, not via `/chain`): follow the
  repo's existing gates exactly as any other work in this session would —
  ADR-before-code for a significant design, explicit approval before a new
  package or any unrequested file. Stop and ask before crossing either.
- **Invoked as a `/chain` stage**: do **not** pause for that human approval.
  The chain already runs under auto-approve by design (see
  `docs/skills/chain.md`'s Safety section) — the mandatory TDD and
  skeptic-review-loop passes from Step 3/4 are the substitute quality gate
  in this context, not a human sign-off mid-chain.

## Journal it

On completion, record the action:

```bash
python -m dskit.journal record --category execute --step "<short label>" \
  --inputs <source research/plan doc path> \
  --outputs <files built, space- or comma-separated> --root .
```

## Never

- Write into `docs/research/` yourself, or hand-edit
  `docs/decisioning/README.md` — those belong to `dskit.journal` alone
  (same restriction as `record-research`).
- Skip TDD or the skeptic-review-loop because the build "looked simple."

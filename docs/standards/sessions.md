# Sessions: worktrees, merges, commits, closure

The long form of the root file's "Session workflow" and "Automatic
implementation and closure" sections.

## Session workflow

- **Start:** fetch remote refs first (automated by the `SessionStart` hook in
  `.claude/settings.json`).
- **Isolation:** every session works from its own git worktree — never
  directly on `main` in a shared checkout, and never in a checkout another
  session is using. Use your platform's native worktree tool if you have
  one, otherwise `git worktree add`. Sessions sharing one working tree is
  the repeat cause of cross-session conflicts and stepped-on work (owner
  note, 2026-09-07). Only merge or push from the worktree once the work is
  ready to land, per this section's other rules.
- **Parallel merges (ADR-0109):** the child ledgers, the generated child
  decisioning READMEs, and the lane notes `children/*/docs/RE-ENTRY.md` merge through the drivers in
  `tools/merge/` — run `tools/merge/install.sh` once per clone (and again
  after the repo moves; the configured paths are absolute). A refused ledger
  merge is USUALLY an id collision, not an edit: `next_id` is `max + 1`, so
  two lanes branching from one base allocate the SAME id. The driver writes
  the conflict itself — both sides between `<<<<<<<`/`=======`/`>>>>>>>` —
  so renumber one side's rows by hand and drop the markers. It will never
  renumber for you: ids are cited from prose (`framework.md` names A2850),
  and moving one silently would break references no test covers. A file
  carrying a `journal-union: CONFLICT COULD NOT BE WRITTEN` line holds ONE
  SIDE ONLY — never `git add` it; recover with `git checkout --merge`.
- **`/wrap`:** rewrite the lane note `children/<child>/docs/RE-ENTRY.md` whole
  (80 lines at most), update its row in `docs/RE-ENTRY.md`, move the replaced
  note to `docs/archive/re-entry/<lane>.md`, merge into `main` when the work is
  coherent and tests pass, push. Defined in `docs/skills/wrap.md`.
- **Every commit names the SPECIFIC model that wrote it** (owner ruling,
  2026-09-05; tightened 2026-09-07): the exact model AND version the session is
  running — `Claude Opus 5`, never a bare `Claude`, and never the shared `Codex`
  identity from the ambient git config. Set it in BOTH places, because a rebase
  or squash rewrites the author but carries the trailer through:
  - author — `git -c user.name=<model> -c user.email=<model>@opencode.ai commit`
  - trailer — `Co-Authored-By: <model> <noreply@…>` in the message body

  Cannot determine your own exact model and version? **Ask.** Never guess, and
  never fall back to the family name — a commit that says only `Claude` cannot
  be attributed to the session that made it, which is the whole point.

## Automatic implementation and closure

For every implementation, review, or wrap task, read
`docs/skills/implementation-workflow.md` before acting. It routes TDD,
skeptic review, convergence checkpoints, and delivery through the canonical
shared procedures. This is the owner-adopted 2026-09-14 audit workflow:
zero unresolved in-scope Critical/Major, two independent final lenses,
recorded minors, family-wide corrections, and a precise candidate lock.
It supersedes older general process wording, not approved feature contracts,
dependency/evidence requirements, real-execution gates, or explicit task rulings.

Read `docs/RE-ENTRY.md` and the lane's own note for the next bounded slice. On a resumed old branch,
fetch and read the current `origin/main` workflow/skeptic/wrap files first;
do not rely solely on stale local prompts. Honor prior authorization to
commit/merge/push/wrap without asking again. A local checkpoint commit is
not readiness approval. Keep `AGENTS.md` and `CLAUDE.md` aligned.

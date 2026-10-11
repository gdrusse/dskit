# Markdown standards

## Every package ships its own docs

When a new package is created it **must** include, at the package level:

- `README.md` — what it does, and **how to leverage it**: how to write the
  configuration files that drive it, and how to build wrappers/adapters that
  extend it. Include a **directory tree of the package's contents**.
- `CLAUDE.md` — the same orientation aimed at an agent working inside it:
  conventions, extension points, gotchas. Also with a **directory tree**.

Keep both trees current when files are added or removed.

## Math in Markdown (must render on GitHub)

Fixed repeatedly (`d85a377`, `8c269a4`, the `\tag` sweep). For any `.md`:

- **Display math: a ```` ```math ```` fence** — never `$$…$$` or `\[…\]`.
  GitHub applies markdown escapes inside `$$`, so `\{ \} \; \, \\` lose
  their backslash and `aligned`/`cases` blocks break.
- **Inline math: `$…$`** — never `\(…\)` or bare `(…)`. No backslash before
  punctuation inside it: write `\lbrace`/`\rbrace`, or use `` $`…`$ ``.
  Never put `_` right after `}` or `)`; GitHub reads it as italics: write
  `Q_j^{\text{hi}}`, not `Q^{\text{hi}}_j`.
- **No `\tag{n}`.** GitHub serves math as native MathML, where a tag becomes
  `<mlabeledtr>`, which browsers do not draw: every symbol stacks on its own
  line. Number an equation as `\qquad\text{(n)}`.

## Budgets and the archive (owner rule, 2026-10-10)

Long files cost focus: the single `docs/RE-ENTRY.md` reached 469,597 lines
(4,431 unique) because every keep-both merge doubled it and nothing pruned.
The rules now:

- **Instruction files carry rules, not essays.** Root `CLAUDE.md`/`AGENTS.md`
  hold one line per rule and point here; a package's `CLAUDE.md` holds its
  conventions and contents tree and points at an `EXTENDING.md` for the seams
  and gotchas (`dskit/pipeline/` is the model).
- **Every `CLAUDE.md`/`AGENTS.md` pair is one document under two names.**
  Edit one, run `python tools/docs/mirror_pairs.py` (`--source AGENTS.md` when
  the other was edited). The comparison rule lives in that tool.
- **Re-entry is one screen per lane**: `docs/RE-ENTRY.md` is the index (one
  dated row per lane plus the Core block); `children/<child>/docs/RE-ENTRY.md`
  is the lane note (Now, Next, Blocked / owner decisions, Read first, Do not).
  `/wrap` rewrites the note whole and moves the replaced one to the top of
  `docs/archive/re-entry/<lane>.md`. Nothing in `docs/archive/` is read at
  session start.
- **The budgets are pinned, once, in `tests/docs/test_budgets.py`** (`BUDGETS`);
  that test also refuses a committed merge marker in any note and a drifted
  pair. Over budget means move detail out (standards, EXTENDING, a memo, the
  archive), never raise the number without the owner.
- **Closed work leaves `TODO.md`**: a section whose items are all done moves to
  `docs/archive/todo/` verbatim, dated. Root-level result files belong to a
  child's memos or to `docs/archive/results/`, never to the repo root.

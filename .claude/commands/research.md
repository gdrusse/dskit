---
description: Record a child research finding through dskit.journal (ADR-0056).
---

Research is a journalled action. Follow `.claude/skills/record-research/SKILL.md`.

1. Child root = directory with `journal.json` (`--root` if not cwd).
2. Draft outside `docs/research/`.
3. `python -m dskit.journal research "SHORT TITLE" --topic T --name N --body-file <draft> --root .`
4. Layout: `docs/research/<topic>/<YYYY-MM-DD>-<name>.md`; synthesis is
   `<date>-synthesis.md`. No markdown in the research root.
5. Never Write `docs/research/` yourself. Never edit the generated README.

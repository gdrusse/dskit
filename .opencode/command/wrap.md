---
description: Refresh the re-entry doc, merge if sensible, push.
---

Wrap up this session (follow the `wrap` skill):

1. Refresh `docs/RE-ENTRY.md` — branch, test status, what landed, next step,
   decisions awaiting the user. Keep it BRIEF.
2. Commit all outstanding work with a clear message (only when the user has
   authorized committing).
3. Merge the working branch into `main` if the work is coherent and tests pass;
   skip the merge if it is mid-stream or failing, and say why.
4. Push the branch and (if merged) `main`, with `-u origin <branch>`.
5. Report in ≤300 chars: what landed, what is pending.

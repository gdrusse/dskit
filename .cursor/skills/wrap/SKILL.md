---
name: wrap
description: Refresh the dskit re-entry note and finish a coherent work session. Use when the user asks to wrap up, hand off, commit, merge, push, or close the current session.
disable-model-invocation: true
---

# Wrap dskit work

1. Refresh `docs/RE-ENTRY.md`: branch, test status, landed work, next step, and decisions awaiting the user. Keep it brief.
2. Commit outstanding work with a clear message only when the user has authorized committing.
3. Merge into `main` only when the work is coherent, tests pass, and the user has authorized merging. Otherwise report why it remains unmerged.
4. Push only when the user has authorized pushing. Use `-u origin <branch>` for a new branch.
5. Report in 300 characters or fewer: landed work and what remains.

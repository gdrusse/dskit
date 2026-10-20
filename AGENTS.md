Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md

Guidance for every agent working in this repository. Claude Code reads the copy
named `CLAUDE.md`; Codex and the other OpenAI models, Cursor and OpenCode read
the copy named `AGENTS.md`. The two are one file under two names, pinned by
`tests/docs/test_budgets.py`: edit one, then `python tools/docs/mirror_pairs.py`.

## What dskit is

A toolkit of **generalizable packages for data-science and ML projects**: every
package must serve a project that has never heard of the domain it was written
for. Nothing is written for one project: no domain names, hardcoded paths,
thresholds, columns or model choices in code. Behavior comes from **JSON
configuration** at run time; a new project means a new config, never an edit.
Wrapping a standard DS/ML library generically is encouraged (`dskit/pipeline/libs/`).

## Working agreement

One line per rule; the rationale is `docs/standards/working-agreement.md`.

- **Inventory before you build**: the package README's "What ships" and the
  matching `libs/` pack. The node registry is not an inventory.
- **Sweep before you build**: `tools/sweep/sweep <name> <synonyms>` before any
  new file, public symbol, kind or config; the commit carries a `Sweep:` trailer.
- **What is configurable is the class's params tuple** (`_PARAMS`, or
  `_BASE_PARAMS` + `_EXTRA_PARAMS` up the bases), never the config file.
- **Missing capability graduates INTO dskit, always.** Children are thin
  wrappers plus JSON; the bar is "might a second project want this".
- **Library plumbing graduates to the tier-2 pack**: mechanism belongs to the
  pack, the domain constraint to the child.
- **ADR before code means write it and wait**: the entry in
  `docs/architecture/decision-log.md`, approval, then code. A new package also
  needs its file-by-file structure approved first.
- **Design with the pillars**: subclass a hook, never branch on kind;
  `@abstractmethod`, not `NotImplementedError`; `__all__` plus `_` is the API;
  one job per method; objects over loose functions; a function is never
  repeated across modules (find the owner and import it).
- **Never hardcode what could change**; a value that must appear twice is
  pinned by a test or a runtime refusal.
- **Ask before writing new files.** Always. No unrequested files.
- **Be brief**: answers and documents stay under about 300 characters unless
  depth is asked. Questions as multiple choice. Problems as problem + proposal.

## Session workflow

- **Start**: the `SessionStart` hook fetches (`.claude/settings.json`,
  `.cursor/hooks.json`, `.opencode/plugin/session-pull.ts`). Then read
  `docs/RE-ENTRY.md` (one screen) and ONLY the note of the lane you will work,
  `children/<child>/docs/RE-ENTRY.md`. Never the archive at session start.
- **Path first**: before designing, proposing or running anything in a child,
  read its `docs/decisioning/path.csv` (or the Path table of the generated
  `README.md`). Rows with `locked=Y` are owner decisions: cite them, follow
  them, never contradict them silently; an unlocked row is context, not
  authority. Propose a change in words and wait; do not re-derive a locked one.
- **Isolation**: your own git worktree. Never `main` in a shared checkout,
  never a checkout another session is using.
- **Merges** go through the ADR-0109 drivers (`tools/merge/install.sh` once per
  clone): ledgers, generated decisioning READMEs, lane notes. A refused ledger
  merge is an id collision: renumber one side by hand. Long form, including the
  "CONFLICT COULD NOT BE WRITTEN" recovery: `docs/standards/sessions.md`.
- **`/wrap`** (`docs/skills/wrap.md`): rewrite the lane note whole (80 lines
  at most), update its row in `docs/RE-ENTRY.md`, move the replaced note to
  `docs/archive/re-entry/<lane>.md`, merge into `main` when the work is
  coherent and tests pass, push.
- **Every commit names the exact model that wrote it**, as author AND trailer:
  `git -c user.name='Claude Opus 5.5' -c user.email='claude-opus-5-5@opencode.ai'
  commit` plus `Co-Authored-By: Claude Opus 5.5 <noreply@…>`. Never a bare
  `Claude`, never the ambient `Codex` identity. Unsure of your version? Ask.
- **Implementation, review or wrap**: read `docs/skills/implementation-workflow.md`
  first (TDD, skeptic review, two independent final lenses, zero unresolved
  Critical/Major, a precise candidate lock). Prior authorization to commit,
  merge, push or wrap persists; a local checkpoint commit is not approval.

## Standards

Read the one the task touches. Each is short.

| Subject | File |
|---|---|
| Code: PEP 8, docstrings, tiering, purity, duplication | `docs/standards/code.md` |
| JSON configs: identity hash, `notes`, default-deny | `docs/standards/configuration.md` |
| Data enters through onboarding | `docs/standards/data-onboarding.md` |
| Markdown: package docs, GitHub math, budgets, archive | `docs/standards/markdown.md` |
| Repository layout | `docs/standards/layout.md` |
| Worktrees, merges, commits, closure (long form) | `docs/standards/sessions.md` |

**Owner-only**: `docs/decisioning/path.csv` and `Current Work` in every child.
Agents never add, edit or regenerate a Path row. `actions.csv` is append-only;
the generated README shows the full Path and the latest 10 Actions (display only).

## Skills index

Every skill's procedure lives in `docs/skills/<name>.md`. Claude Code and Cursor
carry a thin stub (`.claude/skills/<name>/`, `.cursor/skills/<name>/`); Codex and
OpenCode have no per-repo skill directory, so this table is their entry point.
TDD and skeptic review are automatic gates, not slash-command-only skills.

| Trigger | Canonical doc |
|---|---|
| Any implementation, review, or wrap | `docs/skills/implementation-workflow.md` |
| `/chain`, or a described sequence of agent actions to run one after another | `docs/skills/chain.md` |
| Implement/build a research finding or plan | `docs/skills/implement-research-build.md` |
| Explicit request for deep, evidence-backed research | `docs/skills/deep-research.md` |
| "Plain English" / "layman's terms" / `/layman-explain` | `docs/skills/layman-explain.md` |
| Record a run/build/migration/deployment/benchmark outcome | `docs/skills/memo.md` |
| Quick status check / `/progress` / "where are we" | `docs/skills/progress.md` |
| Save a tutorial, toy example, or worked explanation for a child | `docs/skills/record-explanation.md` |
| Research, investigate, or write to `docs/research/` | `docs/skills/record-research.md` |
| Refresh a child's skeleton infrastructure | `docs/skills/refresh-child-infra.md` |
| Bring a child up to the ADR-0056 action journal | `docs/skills/refresh-child-journal.md` |
| High-risk design before code, or code about to be shipped/committed/merged/deployed | `docs/skills/skeptic-review.md` |
| Implementing any feature or bugfix, before writing code | `docs/skills/test-drive-development.md` |
| `/wrap`, wrap up, hand off, close the session | `docs/skills/wrap.md` |

## Commands

```bash
pip install -e ".[dev]"            # core is pure stdlib; dev adds pytest/hypothesis/pytest-cov/ruff
pip install -e ".[all]"            # every optional library the packs can use

python -m pytest -q                # full suite (child suites run by subprocess)
python -m pytest tests/pipeline -q # tier-1 core + purity gate
python -m pytest tests/docs -q     # markdown budgets + CLAUDE/AGENTS pair identity

python -m dskit.pipeline nodemap                 # synthetic demo run
python -m dskit.pipeline run  <doc.json> --asof <YYYY-MM-DD> [--adapter yourpkg]
python -m dskit.pipeline walkforward <doc.json> --asof <YYYY-MM-DD>  # one run per fold + summary
python -m dskit.pipeline plan <doc.json>         # resolved DAG, no execution
python -m dskit.pipeline runs [--root DIR]       # cross-run table: name/asof/hashes/metrics
python -m dskit.pipeline skill <walk dir>        # forecast vs the training mean (ADR-0067)
python -m dskit.pipeline ordering <walk dir>     # size (calibration slope) vs order (ADR-0068)
python -m dskit.pipeline bar <walk dir>...       # the many-attempts bar (ADR-0069)
python -m dskit.pipeline validate <doc.json>     # shape + identity hash
# also: demo / synthetic (legacy stage-list grammar)

python -m dskit.assets     init|validate-model|register|get|list|state|transition|lineage|ingest-run|sync-published
python -m dskit.onboarding init|register-source|acquire|validate|certify|publish|verify
python -m dskit.journal    init|record|research|promote|render|exec
python -m dskit.production validate|plan|serve|ready|status|verify|reconcile|adopt
python -m dskit.production arm-request|approve-arm|disarm|halt|reduce|resume
python -m dskit.production flatten-request|approve-flatten|execute-flatten
python -m dskit.production outcomes|report|replay     # score the decisions; read-only, no writer lock
python -m dskit.production ack|silence|approve-hold   # authenticated, each grants only what it names
python tools/docs/mirror_pairs.py                     # regenerate every AGENTS.md from its CLAUDE.md
```

Exit codes: **0** ran · **1** error · **3** halted (a NO-GO gate, a `validate`
gated `block`, or a tripped serve breaker; a halt is a result) · **4** already
running · **5** refused (a readiness NO-GO, or a control verb the series state
forbids). 4 and 5 are `dskit.production`'s deliberate extension.

# Agent skill chaining, cross-platform sweep, implement-research-build, skeptic-review parallelism

Date: 2026-09-07
Status: approved design, pending implementation

## Motivation

The user works across four CLI agent platforms (Claude Code, Codex, Cursor,
OpenCode) and wants to chain multi-stage workflows (research → build → review
→ execute → memo → explain) across them from a single typed request, with
each stage able to use a different model/skill. A pre-existing audit surfaced
that the repo's skills have drifted out of alignment across platforms (only
1 of 12 project skills exists on the Claude side; a live duplicate file
already exists: `.cursor/skills/wrap` and `wrap-session`). This spec covers
four related pieces of work, built together because they touch the same file
surface:

1. `/chain` — a cross-platform, cross-process chaining skill.
2. A skill sweep: reconcile all project skills onto one canonical-content +
   thin-stub architecture across Claude Code, Cursor, Codex, and OpenCode.
3. `implement-research-build` — a new skill that turns a research doc or plan
   into code, respecting dskit's existing tier-placement rules.
4. A parallel/sequential dispatch option added to the skeptic-review-loop
   skill (dskit project copies only).

## 1. `/chain`

### Interface

A stage is `/<primary-skill> [--model] <freeform instruction>`. Top-level
stages are separated by a newline or `;`. Any `/<skill-name>` mentioned
*inside* a stage's freeform text (not at a stage boundary) is a **sub-technique**
applied within the enclosing stage, not a new stage — e.g. TDD and
skeptic-review-loop invoked while building are sub-techniques of the "build"
stage, not separate stages.

Stage parsing is **interpreted by the driving agent, not parsed by a formal
grammar** — ambiguity (stage boundary vs. nested reference) requires
judgment a regex/script cannot reliably resolve. To keep this safe and
predictable, `/chain` **always echoes the parsed plan** (stage → tool/model →
skill(s) → prompt) back to the user before dispatching anything.

Model flags (`--opus 5`, `--sonnet 5`, `--haiku`, or a platform's own model
names) select the model for that stage's dispatch; they are tier-mapped
per-platform via `.agent-chain/tools.json` (below), since not every model
exists on every platform.

### Dispatch mechanism

- If a stage's target platform is the one currently driving the chain,
  dispatch as a **native subagent** of that platform (e.g. Claude Code's
  Agent tool with a `model` override) — no process spawn.
- If a stage names a different platform, dispatch **cross-process**: shell
  out headlessly to that platform's CLI and capture stdout.
- Each stage's captured output is threaded into the next stage's prompt
  automatically.

### Per-platform adapter table — `.agent-chain/tools.json`

One JSON file, read by every platform's `/chain` stub, holding for each of
claude/codex/cursor/opencode: binary name, prompt-passing convention,
model-flag name and tier→concrete-model mapping, auto-approve flag, and
output-capture method. This is the single source of truth for CLI flags —
duplicating it as prose in 4 separate skill docs is exactly the "value in two
places with nothing pinning them" defect this repo already tracks as its
recurring bug shape.

Confirmed non-interactive invocations (research done 2026-09-07):

| Platform | Binary | Headless flag | Auto-approve |
|---|---|---|---|
| Claude Code | `claude` | `-p "<prompt>"` | `--permission-mode bypassPermissions` |
| Codex | `codex` | `exec "<prompt>"` | `--full-auto` |
| Cursor | `agent` | `-p "<prompt>"` | `--force` |
| OpenCode | `opencode` | `run "<prompt>"` | `--auto` |

### Safety

Auto-approve flags mean a dispatched stage can edit files / run commands
without per-action confirmation. Per owner decision, this is accepted as the
cost of "fully automated" — the parsed-plan echo before dispatch is the
safeguard, not a permission gate per action.

### Files

```
.agent-chain/
├── tools.json     # per-platform adapter table (source of truth for CLI flags)
├── dispatch.py    # thin, tested utility: (tool, model-tier, prompt[, cwd]) ->
│                   #   builds the subprocess call from tools.json, runs headlessly,
│                   #   returns captured stdout. The ONLY part that is code; stage
│                   #   planning stays agent judgment, not a parser.
└── README.md      # what this is, the stage/nesting syntax, how to add a 5th tool

tests/agent_chain/test_dispatch.py   # mocked-subprocess unit tests: correct flags
                                       #   per tool, model-tier mapping, error surfacing
```

`.claude/skills/chain/` and `.cursor/skills/chain/` are thin stubs per the
canonical architecture in §2, pointing at `docs/skills/chain.md`.

## 2. Skill sweep: canonical content + thin stubs

### Problem

`.cursor/skills/` has 12 skills; `.claude/skills/` has 1 (byte-identical to
its Cursor counterpart, proving duplication is viable but unmanaged: no
mechanism keeps them in sync on edit, and a real duplicate — `wrap` vs.
`wrap-session`, identical name/description/content — already exists
uncaught). Codex and OpenCode have no dedicated skill files; both read
`AGENTS.md` at the repo root, so they need an index rather than duplicated
content.

### Architecture

One canonical content file per skill under `docs/skills/<name>.md`. Every
platform's own skill surface becomes a **thin stub**: frontmatter (name,
description/trigger) plus a pointer telling the agent to read the canonical
file before acting — the same "read this other file first" pattern the repo
already uses successfully (`memo` → `layman-explain` dependency).

```
docs/skills/
├── chain.md
├── implement-research-build.md
├── deep-research.md
├── layman-explain.md
├── memo.md
├── progress.md
├── record-explanation.md
├── record-research.md
├── refresh-child-infra.md
├── refresh-child-journal.md
├── skeptic-review.md              # includes the parallel/sequential option, §4
├── test-drive-development.md
└── wrap.md                        # wrap-session retired as a duplicate

.claude/skills/<name>/SKILL.md     # thin stub, one per file above (11 new + chain +
.cursor/skills/<name>/SKILL.md     #   implement-research-build; record-research's stub
                                    #   already exists on both sides and gets migrated
                                    #   to point at the canonical file too)
```

`AGENTS.md` and `CLAUDE.md` (kept in sync by the existing pre-commit hook)
each gain a **Skills index** section: trigger phrase → `docs/skills/<name>.md`.
This is Codex's and OpenCode's entry point, since neither reads a per-repo
skill directory — both read the root instruction file directly.

`.claude/commands/wrap.md` and `.claude/commands/research.md` are a third,
overlapping mechanism (Claude Code slash commands, not skills) duplicating
what the new `wrap` and `record-research` skill stubs cover. They are
**retired** as part of the sweep — leaving them would recreate the same
drift this sweep exists to remove.

### Journal classification

Per owner rule: project-specific skills that perform a decisioning-relevant
child action always log to `dskit.journal`; skills that are techniques or
session rituals, used inside or around an action rather than being one
themselves, do not get their own entry.

| Journals | Doesn't journal | Why |
|---|---|---|
| `record-research`, `refresh-child-infra`, `refresh-child-journal` (journal maintenance skills), **`implement-research-build`** (new, category `execute`) | `deep-research`, `layman-explain`, `progress`, `skeptic-review`, `test-drive-development`, `wrap`, `chain`, `memo`, `record-explanation` | `memo`/`record-explanation` keep their existing explicit "not a journal action" carve-out (durable handoffs, not ADR-0056 decisioning actions) — the owner confirmed this stands despite the general rule. Techniques (TDD, skeptic-review, deep-research) and rituals (wrap, progress) journal through whichever action skill uses them, not on their own. |

## 3. `implement-research-build` (new)

Reads a research finding or plan document and builds it, applying dskit's
existing tier-placement discipline (already documented in `CLAUDE.md`/`AGENTS.md`,
not re-derived here): capability useful beyond one project's domain → graduates
into `dskit/` (tier 1/2, per the existing rules); project's own domain logic →
the child (tier 3). Sourced input is a `docs/research/*.md` finding or a
plan document the user points it at.

Rules:

- **Always applies TDD and the skeptic-review-loop** — write tests first, and
  run the independent skeptic pass before the build is considered done. No
  exceptions, chain-invoked or interactive.
- **Approval gates depend on invocation context.** Interactively, it follows
  the repo's existing gates: ADR-before-code for significant designs, and
  explicit approval before a new package or unrequested files, same as any
  other work in this session. **When invoked as a `/chain` stage**, it does
  not pause for that human approval — the chain already accepts
  auto-approve, and the mandatory TDD + skeptic-review-loop pass is the
  substitute quality gate in that context, per owner decision.
- Journals its action: `python -m dskit.journal record --category execute
  --step "<short label>" --inputs <source research/plan doc> --outputs
  <files built> --root .` on completion.
- Never writes into `docs/research/` or hand-edits `docs/decisioning/README.md`
  (owned by `dskit.journal`, same restriction as `record-research`).

## 4. skeptic-review-loop: parallel/sequential dispatch option

Scope: **dskit project copies only** (`docs/skills/skeptic-review.md` and its
`.claude`/`.cursor` stubs) — the user's personal global
`~/.claude/skills/skeptic-review-loop` (used across every other project) is
left untouched.

Default stays **parallel** (dispatch ≥2 independent skeptics concurrently, as
today's implicit behavior already does via simultaneous subagent calls). Add
an explicit **sequential** option for when parallel dispatch is undesirable
(e.g. resource/rate-limit constraints, or a cross-process chain stage where
concurrent headless CLI calls aren't practical) — invoked by naming
`sequential` when the loop is requested, otherwise parallel is assumed.

## File manifest (complete)

```
docs/skills/*.md                      (13 files: 11 migrated + chain + implement-research-build)
.claude/skills/<name>/SKILL.md        (12 new/rewritten stubs; record-research migrated)
.cursor/skills/<name>/SKILL.md        (12 rewritten stubs; wrap-session removed)
.agent-chain/tools.json
.agent-chain/dispatch.py
.agent-chain/README.md
tests/agent_chain/test_dispatch.py
AGENTS.md                             (+ Skills index section)
CLAUDE.md                             (+ matching Skills index section)
.claude/commands/wrap.md              (removed — superseded by .claude/skills/wrap)
.claude/commands/research.md          (removed — superseded by .claude/skills/record-research)
```

## Out of scope / follow-ups

- No dedicated OpenCode directory — confirmed OpenCode reads root `AGENTS.md`
  directly, same as Codex, so no separate file is needed today.
- A saved/reusable named-chain-spec format (vs. always typing the chain
  inline) is a possible future extension, not built now (YAGNI — nothing in
  this request asked for it).

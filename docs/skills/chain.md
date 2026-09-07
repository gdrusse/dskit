# /chain — multi-stage, cross-platform agent chaining

Runs an ordered sequence of stages, each naming a skill, an optional model
tier, and an instruction, threading each stage's output into the next.
Stages can dispatch to a different CLI platform than the one currently
driving the chain.

## Syntax

A stage is:

```
/<primary-skill> [--model tier] <freeform instruction>
```

Top-level stages are separated by a **newline or `;`**. Any `/<skill-name>`
mentioned *inside* a stage's freeform instruction (not at a stage boundary)
is a **sub-technique** applied within that stage — not a new stage. Example:

```
/chain
/record-research --opus 5
/implement-research-build --sonnet 5 using /test-driven-development and /skeptic-review-loop for review; kick off the research job on the stocks that came out of gate 3 in the most recent run
/record-memo --sonnet 5 summarize the output that just occurred
```

Here `/test-driven-development` and `/skeptic-review-loop` are sub-techniques
of the "build" stage — they do not start new stages.

`--model` accepts a logical tier (`opus`, `sonnet`, `haiku`) or a platform's
own model name. If the current platform has no confirmed mapping for a
requested tier (see `.agent-chain/tools.json`), that stage runs on the
target tool's own default model instead of guessing a model string.

## Before dispatching anything: echo the plan

Stage parsing is **read by judgment, not a formal parser** — resolving
"is this a new stage or a nested reference" requires understanding intent,
which only the driving agent can do. Because of that, `/chain` always
**echoes the parsed plan** back to the user first: each stage's tool,
model, skill(s), and instruction, in order. Only dispatch after showing
this — it is the safeguard for what's otherwise an unattended, auto-approved
run (see Safety below).

## Dispatch mechanism

For each stage, in order:

1. **Same platform as the one currently driving the chain** (no other tool
   named): dispatch as a native subagent of that platform (e.g. Claude
   Code's Agent tool with a `model` param). No process spawn.
2. **A different platform named**: dispatch cross-process via
   `.agent-chain/dispatch.py`'s `dispatch(tool, model_tier, prompt, cwd=...)`
   — this builds the right headless CLI call from `.agent-chain/tools.json`
   and returns the captured stdout.
3. Thread the returned output into the next stage's prompt (prepend it as
   context, e.g. "Prior stage output:\n<output>\n\nYour instruction:
   <this stage's instruction>").
4. If a stage's dispatch raises (non-zero exit, timeout), stop the chain and
   report which stage failed and why — do not silently continue to later
   stages with missing context.

## Safety

Cross-process stages run with each tool's full auto-approve flag
(`--permission-mode bypassPermissions`, `--full-auto`, `--force`, `--auto`)
— by design, this is what "fully automated" requires, and each stage can
edit files or run commands with no per-action confirmation. The parsed-plan
echo above is the safeguard: review the plan, not each individual action.

## Platform notes

- Claude Code, Cursor: this skill exists natively (`.claude/skills/chain`,
  `.cursor/skills/chain`).
- Codex, OpenCode: no per-repo skill directory on either platform — both
  read the root `AGENTS.md` directly, which carries a pointer to this file
  under its Skills index section.

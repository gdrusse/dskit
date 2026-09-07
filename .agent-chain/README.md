# .agent-chain

The dispatch primitive behind the `/chain` skill (`docs/skills/chain.md`).

- `tools.json` — the only place CLI flags for claude/codex/cursor/opencode
  live. If a flag changes on any platform, edit it here; never restate it
  as prose in a SKILL.md.
- `dispatch.py` — `dispatch(tool, model_tier, prompt, cwd=None,
  prior_output=None, allow_mutation=False)` runs one chain stage headlessly
  and returns its output. Prior output is wrapped as untrusted data. Full-auto
  flags are disabled unless the user has approved mutation and the caller sets
  `allow_mutation=True`. Timeouts terminate the stage's process tree.

Callable directly after the approval required by `docs/skills/chain.md`:

```
python .agent-chain/dispatch.py --tool codex --model openai/gpt-5 \
  --prompt "..." --allow-mutation
```

## Adding a 5th tool

Add a key to `tools.json` with `binary`, `promptArg` ("positional" or a
named flag like "-p"), `modelFlag`, `models` (tier → concrete model string,
can start empty), `autoApproveFlags` (list), and `cwdFlag` (a flag name, or
null if the tool has no working-directory override). No code change needed.

## Model tiers

`models` maps opus/sonnet/haiku to that platform's own model strings. Only
Claude's mapping is filled in (the tiers are native there). For the other
platforms, an unmapped logical tier uses the tool default; a concrete model
name is passed through unchanged.

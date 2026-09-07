# .agent-chain

The dispatch primitive behind the `/chain` skill (`docs/skills/chain.md`).

- `tools.json` — the only place CLI flags for claude/codex/cursor/opencode
  live. If a flag changes on any platform, edit it here; never restate it
  as prose in a SKILL.md.
- `dispatch.py` — `dispatch(tool, model_tier, prompt, cwd=None)` runs one
  chain stage headlessly against the named tool and returns its output.
  Callable directly: `python .agent-chain/dispatch.py --tool codex --model
  sonnet --prompt "..."`.

## Adding a 5th tool

Add a key to `tools.json` with `binary`, `promptArg` ("positional" or a
named flag like "-p"), `modelFlag`, `models` (tier → concrete model string,
can start empty), `autoApproveFlags` (list), and `cwdFlag` (a flag name, or
null if the tool has no working-directory override). No code change needed.

## Model tiers

`models` maps opus/sonnet/haiku to that platform's own model strings. Only
Claude's mapping is filled in (the tiers are native there). For the other
three platforms, confirm the exact model string your account can use before
filling it in — `dispatch.py` omits the model flag entirely for any tier
without a confirmed entry, so an empty map is safe, just less specific.

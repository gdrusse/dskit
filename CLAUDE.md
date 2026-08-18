# CLAUDE.md

Guidance for Claude Code working in this repository.

## What dskit is

A toolkit of **generalizable packages for data-science and ML projects**. Every
package must be useful to a project that has never heard of the problem domain it
was written for.

**Nothing here is written for one project.** No domain names, no personal or
organization-specific logic, no hardcoded paths, thresholds, columns, or model
choices in code. Behavior is supplied by **configuration files (JSON)** at run
time. Once code is set, using it on a new project means writing a new config —
never editing the package.

Wrapping a standard DS/ML library is encouraged when it earns its place
(see `dskit/pipeline/libs/`: numpy, sklearn, torch, transformers, optuna, pyomo).
Wrap the *library*, generically — never a project's use of it.

## Working agreement

- **Ask before writing new files.** Always. No unrequested files.
- **A new package requires an approved plan first.** Present the full structure —
  every file and its purpose — and get approval before creating anything. No
  package enters this repo without that.
- **Be brief.** Answers ≤ ~300 characters.
- **Problems:** state the problem, state the proposed solution. Nothing else.

## Repository layout

```
dskit/
├── CLAUDE.md                  # this file — repo-wide standards
├── README.md                  # what dskit is, install, the 60-second path
├── pyproject.toml             # core has ZERO required deps; heavy libs are extras
├── TODO.md
├── docs/
│   ├── agent-master-specifications.md   # Package 1 & 2 master specs (verbatim)
│   └── architecture/                    # architecture-first design work
│       ├── README.md                    # ecosystem + deliverables roadmap
│       ├── context-and-ownership.md     # who owns what; data flow
│       ├── decision-log.md              # ADRs — no decision undocumented
│       └── open-questions.md            # blocks implementation until closed
├── dskit/
│   ├── __init__.py
│   └── pipeline/              # package 1 of N — the execution engine
│       └── libs/              # tier-2 wrappers for standard DS/ML libraries
├── examples/
│   └── pipeline/              # runnable configs, one per capability
└── tests/
    ├── pipeline/              # tier-1 core + purity gate
    └── pipeline_libs/         # tier-2 library packs
```

## Every package ships its own docs

When a new package is created it **must** include, at the package level:

- `README.md` — what it does, and **how to leverage it**: how to write the
  configuration files that drive it, and how to build wrappers/adapters that
  extend it. Include a **directory tree of the package's contents**.
- `CLAUDE.md` — the same orientation aimed at an agent working inside it:
  conventions, extension points, gotchas. Also with a **directory tree**.

Keep both trees current when files are added or removed.

## Configuration standards

- **JSON is the interface.** A config declares the whole process; the code reads it.
- **Comment rigorously and explanatorily.** Standard JSON has no `//` syntax and
  the loader uses `json.load`, so comments use the first-class **`notes`** field,
  supported at document, node, and splits level and **excluded from the identity
  hash** (so documentation never changes a config's identity).
- A `notes` string should say *why*, not restate the key. Explain intent,
  trade-offs, and how to change the behavior.
- **Default-deny params.** A node lists the knobs it allows and refuses the rest,
  so a typo is an error, not a silent default.

```jsonc
{
  "name": "my-run",
  "notes": "What this config is for and how to adapt it — the 'why'.",
  "pipeline": {
    "<key>": {
      "uses": "<kind or pkg.module:Class>",
      "inputs": { "port": "$other_node.output" },
      "params": { },
      "notes": "Why this node is wired this way; what to change and when."
    }
  }
}
```

## Code standards

- **PEP 8.** `ruff` is the dev dependency; keep the tree clean.
- Match the surrounding code's naming, docstring, and comment density.
- **Tiering** — one test decides where code goes: *could a project that has never
  heard of your problem domain use it?*

  | Tier | Path | Rule |
  |---|---|---|
  | 1. Core | `dskit/<pkg>/*.py` | stdlib only; domain-neutral |
  | 2. Library packs | `dskit/<pkg>/libs/<lib>.py` | generic wrappers for standard DS/ML libraries; name the library **only inside `run()`** |
  | 3. Adapters | your own package, outside `dskit` | domain-specific; may import anything |

- **The core stays importable with nothing installed.** Heavy imports go *inside*
  `run()`, never at module top. Enforced by `tests/pipeline/test_purity.py`.
- The toolkit never imports an adapter; adapters import the toolkit.

## Commands

```bash
pip install -e ".[dev]"            # core is pure stdlib; dev adds pytest/ruff
pip install -e ".[all]"            # every optional library the packs can use

python -m pytest -q                # full suite
python -m pytest tests/pipeline -q # tier-1 core + purity gate

python -m dskit.pipeline nodemap                 # synthetic demo run
python -m dskit.pipeline run  <doc.json> --asof <YYYY-MM-DD>
python -m dskit.pipeline plan <doc.json>         # resolved DAG, no execution
python -m dskit.pipeline validate <doc.json>     # shape + identity hash
```

Exit codes: **0** ran · **3** halted at a NO-GO gate (a halt is a result) · **1** error.

## Design work in progress

Two further packages are specified in `docs/agent-master-specifications.md` and are
**in architecture, not implementation** — the specs require approved design before
code. See `docs/architecture/`. Open questions marked high-risk block the
components they touch.

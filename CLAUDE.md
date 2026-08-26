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
(see `dskit/pipeline/libs/`: numpy, sklearn, torch, transformers, optuna, pyomo,
sb3, matplotlib).
Wrap the *library*, generically — never a project's use of it.

## Working agreement

- **Ask before writing new files.** Always. No unrequested files.
- **A new package requires an approved plan first.** Present the full structure —
  every file and its purpose — and get approval before creating anything. No
  package enters this repo without that.
- **Be brief.** Answers ≤ ~300 characters. **Docs too** — every document stays
  brief unless I ask for depth.
- **Questions:** offer multiple-choice options, not open-ended prompts.
- **Problems:** state the problem, state the proposed solution. Nothing else.

## Session workflow

- **Start:** pull from the remote first (automated by the `SessionStart` hook in
  `.claude/settings.json`).
- **`/wrap`:** refresh `docs/RE-ENTRY.md`, merge into `main` when the work is
  coherent and tests pass, push. Defined in `.claude/commands/wrap.md`.

## Repository layout

```
dskit/
├── CLAUDE.md                  # this file — repo-wide standards
├── .claude/
│   ├── settings.json          # SessionStart hook: git pull
│   └── commands/wrap.md       # /wrap
├── README.md                  # the three pillars + the child pattern, 60-second paths
├── pyproject.toml             # core has ZERO required deps; heavy libs are extras
├── TODO.md
├── docs/
│   ├── RE-ENTRY.md            # where we are — read first, refreshed by /wrap
│   ├── agent-master-specifications.md   # Package 1 & 2 master specs (verbatim)
│   ├── children_design_proposals/       # per-child build proposals (owner-ratified
│   │   └── pmquant.md                   #   before code); pmquant = the ladder-market child
│   └── architecture/                    # architecture-first design work
│       ├── README.md                    # ecosystem + deliverables roadmap
│       ├── context-and-ownership.md     # who owns what; data flow
│       ├── decision-log.md              # ADRs — no decision undocumented
│       ├── open-questions.md            # all closed; kept as the record
│       ├── onboarding-design.md         # Package 2 ratified design
│       ├── onboarding-model.json        # the ratified P2 model document
│       ├── child-gap-pmquant.md         # capability-gap report: pmquant as a child
│       └── child-gap-rl-stocks.md       # capability-gap report: rl_stocks as a child
├── dskit/
│   ├── __init__.py
│   ├── pipeline/              # the execution engine; own README + CLAUDE.md;
│   │   └── libs/              #   libs/ = tier-2 DS/ML library packs
│   ├── assets/                # the Data Asset Platform (spec Package 1):
│   │   └── libs/              #   config-driven registry engine; own README +
│   │                          #   CLAUDE.md; libs/ = tier-2 store packs
│   │                          #   (sqlite, parquet)
│   └── onboarding/            # Acquisition & Onboarding (spec Package 2):
│       └── libs/              #   connectors/snapshots/validation/publication;
│                              #   own README + CLAUDE.md; libs/ = connector packs
├── children/                  # child projects (ADR-0021): incubated at repo root,
│   ├── README.md              #   never imported by dskit; the guide
│   └── _skeleton/             #   the pinned, runnable template a child copies
├── examples/
│   ├── pipeline/              # runnable configs, one per capability
│   ├── assets/                # a worked custom asset model
│   └── onboarding/            # a worked connector config + validation suite
└── tests/
    ├── pipeline/              # tier-1 core + purity gate
    ├── pipeline_libs/         # tier-2 library packs
    ├── assets/                # assets engine: purity, hash-parity, e2e ingest + sync
    ├── assets_libs/           # tier-2 store packs (sqlite, parquet)
    ├── onboarding/            # onboarding: purity, model pin, conformance, CLI e2e
    └── children/              # skeleton pin + per-child subprocess runs
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
  supported on every config object (documents, nodes, splits, asset-model kinds,
  suites, …) and **excluded from the identity hash** (so documentation never
  changes a config's identity).
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
pip install -e ".[dev]"            # core is pure stdlib; dev adds pytest/hypothesis/pytest-cov/ruff
pip install -e ".[all]"            # every optional library the packs can use

python -m pytest -q                # full suite (child suites run by subprocess)
python -m pytest tests/pipeline -q # tier-1 core + purity gate

python -m dskit.pipeline nodemap                 # synthetic demo run
python -m dskit.pipeline run  <doc.json> --asof <YYYY-MM-DD> [--adapter yourpkg]
python -m dskit.pipeline walkforward <doc.json> --asof <YYYY-MM-DD>  # one run per fold + summary
python -m dskit.pipeline plan <doc.json>         # resolved DAG, no execution
python -m dskit.pipeline validate <doc.json>     # shape + identity hash
# also: demo / synthetic (legacy stage-list grammar)

python -m dskit.assets     init|validate-model|register|get|list|state|transition|lineage|ingest-run|sync-published
python -m dskit.onboarding init|register-source|acquire|validate|certify|publish|verify
```

Exit codes: **0** ran · **3** halted at a NO-GO gate / `validate` gated `block`
(a halt is a result) · **1** error.

## Design work

Both master-spec packages are **built**: Package 1 → `dskit/assets`
(ADR-0007…0011), Package 2 → `dskit/onboarding` (ADR-0012…0016). The
open-questions register is clear. ADRs continue past the specs: packs
(0017…0019), integrity parity (0020), the child convention (0021),
engine-parity ports (0022/0023), split policies + event bounds (0024),
the rl_stocks-driven capability set (0025 + 0027…0030: the
declared-model seam + trainlog, walk-forward + embargoed splits, the
sb3 and matplotlib packs, the onboarding coverage ledger) and the
proposal still awaiting the owner (0026). New
significant design decisions still require an ADR in
`docs/architecture/decision-log.md` before code — no decision
undocumented.

Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md

Guidance for Codex working in this repository.

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
sb3, matplotlib, mlflow).
Wrap the *library*, generically — never a project's use of it.

## Working agreement

- **Inventory before you build.** "dskit has no X" needs a search behind
  it: read the package README's "What ships" and open any `dskit/*/libs/`
  pack whose subject matches. **The node registry is not an inventory** —
  packs wired by import path register nothing (`libs/numpy.py` sets
  `NODE_KINDS = ()`), so an empty `registry.kinds()` result proves nothing.
- **To find what is configurable, read the class's params tuple, never the
  config file.** Usually `_PARAMS`; the torch/model families split it into
  `_BASE_PARAMS` + `_EXTRA_PARAMS`, so check both and walk the base classes.
  The document shows what someone USED; the tuple is what is AVAILABLE, and
  default-deny makes it exhaustive. Diffing the two is the highest-yield
  review move here.
- **Missing capability graduates INTO dskit. Always.** Build it generic in
  `dskit/` and let the child call it; never solve it child-side and move
  on. **Children are wrappers** — thin tier-3 code plus JSON configs, never
  a home for capability. The bar is "*might* a second project want this",
  not "will it". ADR before code for significant designs.
- **Same for `libs/`.** Library plumbing graduates to the tier-2 pack, not
  into the child. Pick the tier by what the code IS: domain-neutral stdlib
  → core; generic wrapping of a library → pack; the project's own domain →
  the child. Inside a child: **mechanism belongs to the pack, the domain
  constraint belongs to the child** (`SelectOne` subclasses the
  `PyomoSolve` doorway and supplies only its own program).
- **"ADR before code" means WRITE IT AND WAIT.** Add the entry to
  `docs/architecture/decision-log.md`, get it approved, then implement — a
  proposal is not an approval. (A whole new *package* additionally needs its
  full file-by-file structure approved first.)
- **Design with the pillars — encapsulation, inheritance, polymorphism,
  abstraction — to keep code decoupled and modular.** This repo already
  runs on them (`Node`, `Connector`, `Store`, `PyomoSolve`, `TorchAdapter`),
  so extend a seam rather than inventing plumbing beside it. In practice:
  - **Subclass a hook, don't branch.** A new `if kind ==` / `if mode ==`
    chain in a `run()` is the smell — that behavior is a subclass, or a
    registry entry, or a strategy object passed in.
  - **Abstract means abstract.** A hook that only raises
    `NotImplementedError` lets an incomplete subclass construct fine and
    fail later; use `@abstractmethod` so it refuses at construction.
  - **Encapsulate at the boundary.** `__all__` plus the `_` prefix IS the
    public API contract here — 62 of 69 modules declare it and no
    underscore name leaks into one. Keep that true.
  - **One job per method.** If you need comment headers to mark sections
    inside a body, those sections are the methods.
  - **Prefer objects to one-off functions** (owner ruling, 2026-09-04).
    Behavior belongs to a class with a hook a subclass can supply, not to
    a loose module function; a module-level function is for a pure rule
    with ONE owner that several classes import. **A function is never
    repeated across modules** — the second copy is the bug. Find the
    owner (`records.number_ok`, `node.reject_unknown_params`,
    `libs.numpy.narrow_params`, the onboarding `_check_*` family) and
    import it; if none exists, give the rule one home and import that.
- **Never hardcode what could change.** A literal is only acceptable when
  the value is certain never to vary. If a value must appear twice, PIN the
  agreement with a test (`test_lookback_agrees_everywhere`) or a runtime
  refusal — the failure to design against is one copy changing silently.
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
  `.cursor/hooks.json`).
- **`/wrap`:** refresh `docs/RE-ENTRY.md`, merge into `main` when the work is
  coherent and tests pass, push. Defined in `.cursor/skills/wrap/SKILL.md`.
- **Commits are authored under the agent's own model name** (owner ruling,
  2026-09-05): set the author explicitly — `git -c user.name=<model-name> -c
  user.email=<model-name>@opencode.ai commit` — never the shared `Codex`
  identity from the ambient git config.

## Repository layout

```
dskit/
├── AGENTS.md                  # this file — repo-wide standards
├── .cursor/
│   ├── hooks.json          # SessionStart hook: git pull
│   └── skills/wrap/SKILL.md       # /wrap
├── README.md                  # the three pillars + the child pattern, 60-second paths
├── pyproject.toml             # core has ZERO required deps; heavy libs are extras
├── TODO.md
├── docs/
│   ├── RE-ENTRY.md            # where we are — read first, refreshed by /wrap
│   ├── agent-master-specifications.md   # Package 1 & 2 master specs (verbatim)
│   ├── children_design_proposals/       # per-child build proposals (owner-ratified
│   │   ├── intraday_equities.md         #   before code); five-stock intraday child
│   │   └── pmquant.md                   #   ladder-market child
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
│   ├── pipeline/              # the execution engine; own README + AGENTS.md;
│   │   └── libs/              #   libs/ = tier-2 DS/ML library packs
│   ├── assets/                # the Data Asset Platform (spec Package 1):
│   │   └── libs/              #   config-driven registry engine; own README +
│   │                          #   AGENTS.md; libs/ = tier-2 store packs
│   │                          #   (sqlite, parquet)
│   ├── onboarding/            # Acquisition & Onboarding (spec Package 2):
│   │   └── libs/              #   connectors/snapshots/validation/publication;
│   │                          #   own README + AGENTS.md; libs/ = connector packs
│   ├── production/            # the production layer (ADR-0090/0091): serve a
│   │   └── libs/              #   release forward on a cadence — guards, executor,
│   │                          #   hash-chained ledger, monitors, alerts, health;
│   │                          #   own README + AGENTS.md; libs/ = phase-2 packs
│   └── journal/               # child action ledger (ADR-0056); CSV + generated md
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
    ├── production/            # production: purity, oop + producer closure, every
    │                          #   module, and a shadow/paper/live_limited e2e
    ├── production_libs/       # tier-2 packs (sqlite ledger, parquet reference)
    ├── journal/               # action ledger: purity, CSV, locate, CLI e2e
    └── children/              # skeleton pin + per-child subprocess runs
```

## Every package ships its own docs

When a new package is created it **must** include, at the package level:

- `README.md` — what it does, and **how to leverage it**: how to write the
  configuration files that drive it, and how to build wrappers/adapters that
  extend it. Include a **directory tree of the package's contents**.
- `AGENTS.md` — the same orientation aimed at an agent working inside it:
  conventions, extension points, gotchas. Also with a **directory tree**.

Keep both trees current when files are added or removed.

## Configuration standards

- **JSON is the interface.** A config declares the whole process; the code reads it.
- **The identity hash is what a config IS.** sha256 over the config's canonical
  JSON with `notes` stripped and `env`/`outputs`/`schedule`/`tracking` excluded
  — so documentation and placement never change identity, but any change to what
  the run COMPUTES does. It names the run directory, keys the `$prev` run series,
  and is what a store pins at init. **Moving a hash orphans every prior run and
  every stored artifact keyed to it**, which is why adding or removing a graded
  field is a breaking change even when no behavior changed. Optional fields are
  emitted ONLY WHEN PRESENT for exactly this reason.
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

## Child decisioning

- **Path is human-owner-only.** Agents never add, edit, regenerate, or
  otherwise update `docs/decisioning/path.csv`, including `Current Work`.
  The owner alone maintains those rows.
- Every Path row has an ID, short label, purpose, relevant files (pipeline
  run, research markdown, or other material evidence), and `LOCKED` as `Y`
  or `N`. The generated decisioning README displays the complete Path.
- The generated Actions table displays only its latest 10 rows. This is
  display-only: `actions.csv` remains complete and append-only; never delete
  journal or Path history.

## Code standards

- **PEP 8.** `ruff` is the dev dependency; keep the tree clean.
- Match the surrounding code's naming and comment density. **Docstrings follow
  the standard below, not the surrounding file** — "match the neighbours" is
  what let `assets` reach ~50% NumPy-sectioned while `pipeline/libs` sat at
  ~0.5%.

### Docstrings

New code, and any function you meaningfully edit. No retrofit sweep.

- **Modules: prose** — they carry the *why*, which sections would fragment.
- **Classes: NumPy sections + an `Examples` block that INSTANTIATES the class**,
  so a reader can copy it and have a working object.
- **Functions: `Parameters` / `Returns` / `Raises`,** with types in the
  docstring TEXT. **No type hints in signatures** — not on parameters, not on
  returns. The `Returns` section already states the type; an annotation would
  be the same fact in two places with nothing pinning them. Existing
  annotations stay; they are simply never required.
- **Private `_`-helpers: one line.** `D103` exempts non-public names.

```python
class WindowRows(Node):
    """Per-symbol lagged-return windows with a next-bar label.

    Parameters
    ----------
    params : dict
        ``lookback`` (int >= 2, required), ``price_field`` (str, default
        ``"close"``), ``max_gap_minutes`` (float, default 5).

    Examples
    --------
    Build 30-bar windows that never bridge a session gap::

        node = WindowRows("window", {"lookback": 30, "max_gap_minutes": 5})
        out = node.run(ctx, {"records": bars})
    """
```

**Examples are illustrative and must NEVER use `>>>`** — use an indented `::`
block. `>>>` promises a suite verified it, and nothing here collects doctests.
Mark expected output with a leading `# ->` comment on its own line — the
general form, and required when the value spans lines or follows a raised
exception — or, for a short single-line value whose producing line has a free
trailing-comment slot, a trailing `# value` comment on that line instead.
Never leave a bare unmarked line, which reads as more code.

**Enforced** by ruff `D` (numpy), with a `per-file-ignores` entry per
unconverted module. **Delete the entry when you convert the module** — that
list is the remaining work.
- **Tiering** — one test decides where code goes: *could a project that has never
  heard of your problem domain use it?*

  | Tier | Path | Rule |
  |---|---|---|
  | 1. Core | `dskit/<pkg>/*.py` | stdlib only; domain-neutral |
  | 2. Library packs | `dskit/<pkg>/libs/<lib>.py` | generic wrappers for standard DS/ML libraries; name the library **only inside a method** (`run()` for a node pack; `libs/mlflow.py` is a tracking SINK and has none) |
  | 3. Adapters | your own package, outside `dskit` | domain-specific; may import anything |

- **The core stays importable with nothing installed.** Heavy imports go *inside*
  `run()`, never at module top. Enforced by `tests/pipeline/test_purity.py`.
- The toolkit never imports an adapter; adapters import the toolkit.

### Duplication that diverges

Every hardcoding defect in the 2026-08-27 audit was one shape: **a value in
two places with nothing pinning them.** Instances live in `TODO.md`; the
rules:

- **A default belongs to ONE name.** `params.get(k, <literal>)` in both
  `validate_params` and `run` is the commonest defect here — validation then
  approves a value the run never uses. Name it once (`DEFAULT_EPOCHS`).
- **If a value MUST appear twice, pin the agreement** with a test or a
  runtime refusal. Unpinned duplication is a scheduled bug.
- **A pinning test that omits a knob is worse than none** — it claims
  coverage it lacks. Add the knob to the tuple when you add the knob.
- **A tier-2 pack never restates tier-1 truth.** Import the rule; a pack that
  re-derives a core validator drifts the moment core loosens a bound.
- **A serving path never restates a training knob** — read it from the run
  dir or source config. A parity test over the *mechanism* will not catch a
  differing FIELD.
- **Never document an escape hatch you did not build.**

The exception: **deliberate independent restatement is correct.** A
validation suite must NOT read its expected vocabulary from the thing it
validates — an assertion sourced from its subject asserts nothing.

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
```

Exit codes: **0** ran · **1** error · **3** halted (a NO-GO gate, a `validate`
gated `block`, or a tripped serve breaker — a halt is a result) · **4** already
running · **5** refused (a readiness NO-GO, or a control verb the series state
forbids). 4 and 5 are `dskit.production`'s deliberate extension: a halted series
needs an operator, while a readiness NO-GO only means the checklist is not yet
satisfied.

## Design work

Both master-spec packages are **built**: Package 1 → `dskit/assets`
(ADR-0007…0011), Package 2 → `dskit/onboarding` (ADR-0012…0016). The
open-questions register is clear. ADRs continue past the specs: packs
(0017…0019), integrity parity (0020), the child convention (0021),
engine-parity ports (0022/0023), split policies + event bounds (0024),
the rl_stocks-driven capability set (0025 + 0027…0030: the
declared-model seam + trainlog, walk-forward + embargoed splits, the
sb3 and matplotlib packs, the onboarding coverage ledger) and the
proposal still awaiting the owner (0026), the §14/§13 graduation round
(0033…0036), the observations read seam (0037), and the 2026-08-28
closeout round (0038…0043: the `TrainableNode` mode-dispatch seam, the
`foreach` fan-out grammar, gap-aware windows + the fitted-transform
family, the time-series architecture zoo, feature selection, and
HPO × walk-forward semantics), plus the follow-on (0044: per-knob searchability for
`fitted_transform`, so ADR-0042's flow 2 can run). New
significant design decisions still require an ADR in
`docs/architecture/decision-log.md` before code — no decision
undocumented.
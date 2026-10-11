# Repository layout

```
dskit/
├── CLAUDE.md / AGENTS.md      # the rules, one document under two names (Claude Code / Codex, Cursor, OpenCode)
├── .claude/
│   ├── settings.json          # SessionStart hook: git fetch (.cursor/hooks.json and .opencode/ do the same)
│   └── skills/                # /chain, /wrap, /record-research, … (stubs; see docs/skills/)
├── README.md                  # the three pillars + the child pattern, 60-second paths
├── pyproject.toml             # core has ZERO required deps; heavy libs are extras
├── TODO.md
├── docs/
│   ├── RE-ENTRY.md            # one-screen index: a dated row per lane + the Core block; the lane
│   │                          #   notes are children/<child>/docs/RE-ENTRY.md (rewritten at /wrap)
│   ├── standards/             # code, configuration, data-onboarding, markdown, layout, sessions,
│   │                          #   working-agreement — the long form the root file points at
│   ├── archive/               # re-entry/ (per-lane history), todo/ (closed sections), results/
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
│   ├── pipeline/              # the execution engine; own README + CLAUDE.md;
│   │   └── libs/              #   libs/ = tier-2 DS/ML library packs
│   ├── assets/                # the Data Asset Platform (spec Package 1):
│   │   └── libs/              #   config-driven registry engine; own README +
│   │                          #   CLAUDE.md; libs/ = tier-2 store packs
│   │                          #   (sqlite, parquet)
│   ├── onboarding/            # Acquisition & Onboarding (spec Package 2):
│   │   └── libs/              #   connectors/snapshots/validation/publication;
│   │                          #   own README + CLAUDE.md; libs/ = connector packs
│   ├── production/            # the production layer (ADR-0090/0091): serve a
│   │   └── libs/              #   release forward on a cadence — guards, executor,
│   │                          #   hash-chained ledger, monitors, alerts, health;
│   │                          #   own README + CLAUDE.md; libs/ = phase-2 packs
│   ├── evaluation/            # backtest evaluator (ADR-0183): event log ->
│   │                          #   summary.md + self-contained report.html + CSVs;
│   │                          #   own README + CLAUDE.md
│   └── journal/               # child action ledger (ADR-0056); CSV + generated md
├── tools/
│   ├── merge/                 # ADR-0109 merge drivers + install.sh
│   ├── sweep/                 # the sweep tool + its commit-msg hook
│   └── docs/                  # mirror_pairs.py: every AGENTS.md from its CLAUDE.md
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
    ├── evaluation/            # evaluator: schema, census, book, stats, SVG,
    │                          #   self-contained HTML, CLI + node e2e, purity
    ├── journal/               # action ledger: purity, CSV, locate, CLI e2e
    ├── children/              # skeleton pin + per-child subprocess runs
    └── docs/                  # markdown budgets, no merge markers, CLAUDE/AGENTS pair identity
```

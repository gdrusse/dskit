# CLAUDE.md — yourproject (a dskit child)

Agent orientation template — see README.md for what the child does.

## The child rules (ADR-0021)

- **Never edit dskit.** A missing capability is either a genuinely
  generic gap — propose an ADR upstream — or domain logic that stays
  here. There is no third option.
- **The domain lives here and in `configs/`.** dskit stays domain-blind;
  behavior is JSON the engines validate, self-documented via `notes`
  (the why, not the what), default-deny everywhere.
- **Tier-3 may import anything** — but keep heavy imports inside
  `run()`/`read()`: documents naming these kinds must PLAN on machines
  without the heavy libraries (the conformance suite enforces it).
- **Position-independent**: no `..` imports, no dskit-repo paths; the
  only coupling is `import dskit`. Graduation is a directory move.
- **Import = registration**: `yourproject/__init__.py` imports `nodes`,
  which registers the kinds (`owned` never set — that is toolkit
  doctrine). `--adapter yourproject` is exactly this import.
- The skeleton's file list is pinned in dskit's
  `tests/children/test_skeleton.py` — reshaping the SKELETON means
  updating that pin in the same commit (copies are unpinned).

## Layout

```
yourproject/           # tier-3 code (connectors.py, nodes.py)
configs/               # asset-model / source-sample / suite-sample / run-sample
tests/                 # conftest bootstrap + configs/connectors/nodes tests
pyproject.toml         # dependencies = ["dskit"]
```

Keep this tree and README.md's current when files change.

# Code standards

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

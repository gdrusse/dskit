# Shared brief for every `dskit.production` build agent

You are one agent in an autonomous overnight build of a new package,
`dskit/production/`, in the repo at `/home/user/dskit` (branch
`claude/dskit-production-build-3g17vw`). Read this whole file first.

## The contract

- **The plan is `docs/new_package_proposals/production.md`** (3072 lines). It is
  the contract. §1–§3 ruling (D1–D24); §4 serve document + identity; §5 seams
  (per-module contracts: §5.0 vocab/base/redact, §5.1 clock/sessions/cadence,
  §5.2 feed, §5.3 decider, §5.3.1 release, §5.4 records, §5.5 guards, §5.6
  breaker+arming, §5.7 executor, §5.7.1 accounting, §5.7.2 coordination, §5.8
  ledger+control, §5.8.1 state, §5.9 reconcile, §5.10 monitors, §5.11
  alerts+health, §5.11.1 metrics, §5.12 resilience, §5.13 loop/readiness,
  §5.13.1 leg+Authority+compose, §5.14 policy+verifier, §5.15 OOP ruling,
  §5.16 producer table); §6 ledger records; §7 CLI; §8 file-by-file structure
  incl. the test file list with what each test proves; §9 changes outside the
  package (§9.1 pipeline seam ADR-0091, §9.2 skeleton, §9.3 docs); §10 TDD
  order; §11 phases. Use `grep -n "^### 5\.\|^## " docs/new_package_proposals/production.md`
  to find sections, then `sed -n A,Bp` to read the ones you need IN FULL.
- Repo law: `CLAUDE.md` (root) — read "Working agreement", "Code standards",
  "Docstrings", "Duplication that diverges". Summary of the parts that bite:
  - **Default-deny params**: every configurable class lists `_PARAMS` and
    refuses unknown keys via `reject_unknown_params` (import it from
    `dskit.pipeline.node`, never copy it).
  - **`__all__` + `_` prefix is the API contract.** Every module declares
    `__all__`; no underscore name is exported; nothing imports another module's
    private name.
  - **Docstrings, NumPy convention, enforced by ruff `D`** (`select = E4,E7,E9,F,D`,
    `convention = numpy`): module docstring in prose (the *why*); every public
    class has NumPy sections (`Parameters`, `Attributes` when useful) **and an
    `Examples` block that instantiates the class** using an indented `::` block
    (NEVER `>>>`), expected values marked with `# -> value` or a trailing
    `# value` comment; every public function has `Parameters` / `Returns` /
    `Raises` with types in the TEXT. **No type hints in signatures** (no
    parameter annotations, no `->` return annotations). Private `_helpers`: a
    one-line docstring. Tests are exempt from `D` (`tests/**` ignore).
  - **Abstract means `@abstractmethod`.** A seam ABC's hooks are abstract so an
    incomplete subclass refuses to construct.
  - **Subclass a hook, don't branch.** No `if kind ==` / `if mode ==` /
    `if rung ==` chains — a strategy object, a registry entry, or a subclass.
    `compose.py` is the ONLY module allowed to read the rung.
  - **A default belongs to ONE name** (a module constant), read by
    `validate_params` and the run alike. **No literal thresholds in code**:
    every threshold is a document knob; code holds only named defaults.
  - **Never repeat a function across modules** — find the owner and import it.
  - **Ask before writing new files**: the plan's §8 lists every file; write only
    files the plan names (or your task names). No stray helpers, no scratch
    files in the repo — use `/tmp/claude-0/-home-user-dskit/67043ba2-0781-50c8-8c6b-7689bc954704/scratchpad/` for anything temporary.
  - Python 3.11, stdlib only for `dskit/production/*.py` (see import rule).
    `hypothesis` and `pytest` are available for tests.

## Package import rule (enforced by `tests/production/test_purity.py`)

`dskit/production/*.py` may import: stdlib, `dskit.pipeline.*`,
`dskit.onboarding.*`, `dskit.assets.*`, `dskit.production.*`. `dskit.journal`
only at FUNCTION depth (inside a function body), never at module level.
Nothing else (no numpy, no pyarrow, no requests). `dskit/production/libs/*.py`
(phase 2) may name its library only inside a method. `dskit/pipeline` must
NEVER import `dskit.production`.

## Conventions fixed for the whole package (so groups built in parallel agree)

- `ProductionError(problems)` in `base.py`: subclass of `ValueError`, takes a
  LIST of problem strings (same shape as `dskit.pipeline.base.ConfigError` and
  `dskit.assets.base.AssetError`), exposes `.problems`, `str()` joins them with
  `"; "`. Validation ACCUMULATES every problem then raises once.
- `base.py` also provides: `Registry` (below), `canonical_bytes(obj)` (JSON:
  sorted keys, `separators=(",", ":")`, `ensure_ascii=True`, `allow_nan=False`;
  `Decimal` rendered as its `str()`; tuples as lists; refuses anything else
  with `ProductionError`), `canonical_hash(obj)` = sha256 hex of
  `canonical_bytes`, `record_hash(prev_hash, envelope)` = sha256 hex of
  `prev_hash.encode() + canonical_bytes(envelope without "hash")`,
  `now_ms()` / `utc_iso(ms)` / `parse_utc_ms(text)` helpers (naive datetimes
  refused), and re-exports `reject_unknown_params` (from
  `dskit.pipeline.node`) and `_check_str`, `_check_dict`, `_check_unknown`,
  `_raise_if` from `dskit.assets.base` (the same re-export idiom
  `dskit/onboarding/base.py` uses, with `# noqa: F401`).
- **Registries.** `base.Registry(family, abc)`: `register(name, cls)` refuses a
  duplicate name and a non-subclass of `abc` (`ProductionError`);
  `resolve(uses)` accepts a registered name or a `pkg.module:Class` reference
  (use `dskit.pipeline.base.is_class_ref` / `import_ref`) and returns the
  class, refusing an unknown name or a non-subclass; `kinds()` returns the
  sorted tuple of names; `name in registry` works; `family` attribute.
  Each seam module defines its registry at module bottom, e.g.
  `CLOCK_KINDS = Registry("clock", Clock)` then
  `CLOCK_KINDS.register("wall", WallClock)`. Import = registration. The 20
  registry names are fixed by §4.3.
- **Seam-class construction.** A registry-resolved class is constructed as
  `cls(params)` where `params` is the `{uses, params}` site's `params` dict
  (default `None` → `{}`), validated by a `validate_params(cls, params)`
  classmethod (default-deny over `_PARAMS`) that `__init__` calls and raises
  `ProductionError` on problems. Collaborators that are objects (clock,
  ledger, calendar, executor, …) arrive as keyword arguments after `params`
  and are named in the class docstring; `compose.py` supplies them. Classes
  the plan gives an explicit constructor for (`Arming(document, release)`,
  `Readiness(document, release)`, `LegPipeline(...)`, `Authority(...)`,
  `ServeLoop(...)`, `Tick(...)`, `SubmissionVerifier(...)`, `Recovery(...)`,
  `ServeRoot(root, series_id)`) use exactly that signature.
- **Value objects** (`records.py` and everywhere): `@dataclass(frozen=True)`;
  money/qty/price fields are `decimal.Decimal` (rendered as strings in JSON,
  parsed back by `from_obj`); instants are epoch-ms `int`s (never floats);
  every record has `to_obj()` (JSON-ready dict) and a `from_obj(obj)`
  classmethod that is default-deny (unknown key refuses) and refuses a
  non-finite number. Field ORDER in the dataclass is the canonical order
  digests are computed over.
- **Digests**: every `*_digest` uses `base.canonical_hash` over the record's
  `to_obj()` (or the stated subset) — one recipe, defined once.
- **Closed vocabularies** live ONLY in `vocab.py` as module-level tuples (the
  full list is §8's `vocab.py` entry). Every other module imports them; a
  test fails any closed set defined elsewhere. `vocab.py` has no imports
  beyond stdlib and no logic beyond building the index maps it names.
- **Clock**: nothing calls `time.time()` outside `clock.py`; every class
  needing time takes an injected `clock` (a `Clock`) and calls
  `clock.now_ms()` / `clock.monotonic()`.
- Money never touches float. Ratios (`bankroll_fraction`, `confidence`) may.
- Logging via `logging.getLogger("dskit.production.<module>")`; every log
  line, alert body and recorded `reason` passes through `redact.redact`.

## Tests

- Live in `tests/production/test_<module>.py`; plain pytest + hypothesis;
  **no network, no sleeping on the wall clock, no real time** — use
  `TestClock`. Shared fixtures go in `tests/production/conftest.py` (built by
  the group that needs them first; extend, never duplicate).
- A test must be able to FAIL: assert values the plan states (bounds, orders,
  digests, refusals), never a literal the test itself computed the same way.
  Prefer asserting refusals (`pytest.raises(ProductionError)`) and exact
  return shapes. Each test's name says what it proves.
- Tests are written from §4–§7 alone. Where the plan is silent on a needed
  detail, do NOT invent silently: pick the reading the plan's neighbours
  imply, and REPORT it as a plan gap (see below).
- Run with `python -m pytest -q tests/production/test_<module>.py`.
  `python -m ruff check dskit/production tests/production` must be clean.

## When the plan is wrong (from the owner's instructions)

1. Bookkeeping (missing producer row, wrong count, naming slip): note it in
   your report; the orchestrator fixes the plan.
2. Safety-critical (money movement, authority, barriers, digests, freshness,
   crash recovery): STOP that piece, write the finding up precisely (what the
   plan says, why it is wrong, the smallest correct fix), and report. Do not
   silently pick.
3. Never edit `docs/new_package_proposals/production.md` yourself unless your
   task says so.

## Report format (your final message — the orchestrator reads only this)

Keep it under ~350 words:
1. **Files written/changed** (paths).
2. **Test status**: the exact pytest summary line(s) you observed.
3. **Plan gaps found**: numbered, each tagged `[bookkeeping]` or
   `[safety-critical]`, with the section and what you did/recommend.
4. **Decisions you made** that a later group must know (constructor shapes,
   helper names, fixture names).
5. **Open items** you could not finish.

# Working agreement (long form)

The root `CLAUDE.md`/`AGENTS.md` carries each rule in one line; this is the
rationale and the worked detail behind every line, unchanged from the root
file before the 2026-10-10 cleanup.

- **Inventory before you build.** "dskit has no X" needs a search behind
  it: read the package README's "What ships" and open any `dskit/*/libs/`
  pack whose subject matches. **The node registry is not an inventory** —
  packs wired by import path register nothing (`libs/numpy.py` sets
  `NODE_KINDS = ()`), so an empty `registry.kinds()` result proves nothing.
- **Sweep the repo before building** (owner rule, 2026-09-25: we keep
  redoing work). Before a new file, public class/function, kind or config,
  run `tools/sweep/sweep <name> <synonyms>` — it searches origin/main, every
  branch and every worktree. A commit adding any of those needs a
  `Sweep: <what you searched; why it is not a duplicate>` trailer; the
  commit-msg hook (`tools/sweep/install.sh`) refuses it otherwise.
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

"""``python -m dskit.pipeline`` — THE universal execution file.

One command line for every project and venue (docs/24 §9, D-145 ruling
3). The node-map commands:

* ``run <config.json> [--asof YYYY-MM-DD]`` — the 6-step lifecycle
  (LOAD → IMPORT → PLAN → RESOLVE → EXECUTE → RECORD). Exit code
  reflects the outcome: 0 ran, 3 halted at a NO-GO gate, 1 error.
* ``walkforward <config.json> [--asof YYYY-MM-DD]`` — run the document's
  declared ``walkforward`` section (ADR-0027): one full run per fold
  cutoff plus an aggregate summary dir. Exit 0 all folds ran, 3 a fold
  halted, 1 a fold errored.
* ``plan <config.json>`` — print the resolved DAG (plan.json), no
  execution.
* ``validate <config.json>`` — shape + hash only. Dispatches on the
  document's own shape: a ``pipeline`` node map validates under the
  docs/24 grammar; anything else falls through to the stage-list
  validator.

``--adapter MODULE`` (repeatable) applies to all three: it imports the
named module BEFORE the document is read, which is how a document that
names registered adapter kinds (``<venue>-kelly-mio``) resolves them.
Documents referencing components by class path
(``yourproject.nodes:KellyMIOSize`` — a CHILD package, ADR-0032) import
themselves and need no flag — adapters ship components, never CLIs
(D-145 ruling 3).
* ``nodemap`` — the full banking document (data → … → stat_test →
  capital → report) against the synthetic Node set, in a temporary
  directory; prints the verdict-first report.

Stage-list commands, unchanged while the migration completes:

* ``demo`` (default) — build a stage-list config, prove the canonical
  round-trip, print the identity hash and canonical JSON.
* ``synthetic`` — the staged pipeline against the synthetic venue.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile

from dskit.pipeline.base import (
    DataConfig,
    ModelConfig,
    OptimizationConfig,
    PipelineConfig,
    TimeSplitConfig,
    ValidationConfig,
)


def demo_config(data_dir="~/dskit_data") -> PipelineConfig:
    """The grammar showcase: every section, tagged families included.

    Deliberately on the toolkit's OWN reference venue (D-146): a demo in
    the pure core that named an adapter's venue tag and its real tickers
    made the toolkit read as if it knew what that venue was. It never
    did — and now nothing in tiers 1–2 says otherwise.
    """
    return PipelineConfig(
        name="demo-synthetic",
        data=DataConfig(
            venue="synthetic",
            data_dir=data_dir,
            instruments=("SYN-A", "SYN-B"),
        ),
        splits=TimeSplitConfig(
            train_end_ms=1_750_000_000_000,
            val_end_ms=1_753_000_000_000,
            test_end_ms=1_756_000_000_000,
        ),
        model=ModelConfig(
            name="causal-beta",
            model_dir="~/dskit_data/processed/demo_models",
            params={"min_train": 30},
        ),
        optimization=OptimizationConfig(
            kind="fractional-kelly-mio",
            params={
                "bankroll": 1_000.0,
                "deploy_frac": 0.3,
                "kelly_fraction": 0.5,
                "min_lot": 1,
            },
        ),
        notes="demo config — notes are excluded from the hash",
    )


def cmd_demo() -> int:
    cfg = demo_config()
    roundtrip = PipelineConfig.from_obj(cfg.to_obj())
    assert roundtrip.hash == cfg.hash, "canonical round-trip must preserve identity"
    print(f"pipeline config OK — hash {cfg.hash[:16]}…  (round-trip verified)")
    print(json.dumps(cfg.to_obj(), indent=2))
    return 0


def cmd_synthetic() -> int:
    from dskit.pipeline.runner import Runner
    from dskit.pipeline.testing import (
        TEST_END_MS,
        TRAIN_END_MS,
        VAL_END_MS,
        register_synthetic,
    )

    register_synthetic()
    with tempfile.TemporaryDirectory() as tmp:
        cfg = PipelineConfig(
            name="synthetic-demo",
            data=DataConfig(venue="synthetic", data_dir=tmp),
            splits=TimeSplitConfig(
                train_end_ms=TRAIN_END_MS,
                val_end_ms=VAL_END_MS,
                test_end_ms=TEST_END_MS,
            ),
            model=ModelConfig(
                name="true-q",
                model_dir=os.path.join(tmp, "models"),
                params={"n_events": 90},
            ),
            validation=ValidationConfig(min_events=20),
            optimization=OptimizationConfig(kind="equal-weight"),
        )
        result = Runner(cfg, asof="2026-01-01").run()
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            print(fh.read())
        print(f"(artifacts were written under {result.run_dir} — temporary dir)")
    return 0


def _legacy_validate(path, adapters) -> int:
    from dskit.pipeline.io import load_config

    try:
        # ImportError included so an unimportable --adapter reads the same
        # on every command: printed, exit 1. It used to escape as a
        # traceback here while run/plan handled it.
        cfg = load_config(path, adapters=tuple(adapters))
    except (ImportError, ValueError, OSError) as exc:
        print(exc)
        return 1
    strict = "strict" if cfg.optimization is None or adapters else "shape-only*"
    print(f"OK — {path}")
    print(f"  name:  {cfg.name}")
    print(
        f"  venue: {cfg.data.venue}  splits: {cfg.splits.kind}  "
        f"model: {cfg.model.name}  "
        f"optimizer: {cfg.optimization.kind if cfg.optimization else '(none)'}"
    )
    print(f"  hash:  {cfg.hash}  ({strict})")
    if strict != "strict":
        print(
            "  * optimizer params validate strictly only with the kind's "
            "adapter imported — pass --adapter <module> "
            "(e.g. --adapter yourproject, your child package)"
        )
    return 0


def _doc_validate(path) -> int:
    from dskit.pipeline.document import load_document

    try:
        doc = load_document(path)
    except (ValueError, OSError) as exc:
        print(exc)
        return 1
    sections = [
        s
        for s in (
            "splits",
            "clock",
            "schedule",
            "env",
            "outputs",
            "tracking",
            "walkforward",
        )
        if getattr(doc, s) is not None
    ]
    print(f"OK — {path}")
    print(f"  name:  {doc.name}")
    print(f"  nodes: {len(doc.pipeline)}  sections: {', '.join(sections) or '—'}")
    print(f"  hash:  {doc.hash}")
    return 0


def cmd_validate(path, adapters) -> int:
    """Dispatch on the document's own shape: a ``pipeline`` node map is
    the docs/24 grammar; everything else is the stage list."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        doc = None  # the legacy loader produces the path-naming error
    if isinstance(doc, dict) and "pipeline" in doc:
        try:
            # Node-map validation is shape + hash and resolves no `uses`, so
            # the import changes no outcome — but a flag that silently does
            # nothing on the grammar it was widened for is a trap. Import it,
            # and report it failing the same way the other commands do —
            # including an adapter that raises at import (a duplicate kind
            # registration raises ValueError, not ImportError).
            _import_adapters(adapters)
        except (ImportError, ValueError, OSError) as exc:
            print(exc)
            return 1
        return _doc_validate(path)
    return _legacy_validate(path, adapters)


def _import_adapters(adapters) -> None:
    """Import each named adapter module — import IS registration.

    A document may name components two ways: a class reference
    (``pkg.module:Class``), which imports itself when it resolves, or a
    registered kind name, which exists only after its owning package has
    been imported. This is how the ONE command line reaches the second
    kind without an adapter ever shipping a CLI of its own (D-145 ruling 3).
    """
    import importlib

    for module in adapters:
        importlib.import_module(module)


def cmd_plan(path, adapters=()) -> int:
    from dskit.pipeline.document import load_document
    from dskit.pipeline.planner import plan

    try:
        _import_adapters(adapters)
        resolved = plan(load_document(path))
    except (ImportError, ValueError, OSError) as exc:
        print(exc)
        return 1
    print(json.dumps(resolved.to_obj(), indent=2))
    return 0


def cmd_run(path, asof, adapters=()) -> int:
    from dskit.pipeline.driver import run_document

    try:
        _import_adapters(adapters)
        result = run_document(path, asof=asof)
    except (ImportError, ValueError, OSError) as exc:
        print(exc)
        return 1
    with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
        print(fh.read())
    print(f"(run dir: {result.run_dir})")
    return result.exit_code


def cmd_walkforward(path, asof, adapters=()) -> int:
    from dskit.pipeline.driver import run_walk_forward

    try:
        _import_adapters(adapters)
        result = run_walk_forward(path, asof=asof)
    except (ImportError, ValueError, OSError) as exc:
        print(exc)
        return 1
    with open(os.path.join(result.summary_dir, "report.md"), encoding="utf-8") as fh:
        print(fh.read())
    print(f"(summary dir: {result.summary_dir})")
    return result.exit_code


def cmd_nodemap() -> int:
    from dskit.pipeline.base import OutputsConfig
    from dskit.pipeline.driver import run_document
    from dskit.pipeline.synthetic_nodes import demo_document, demo_registry

    with tempfile.TemporaryDirectory() as tmp:
        doc = demo_document(outputs=OutputsConfig(run_root=tmp))
        result = run_document(doc, asof="2026-01-01", registry=demo_registry())
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            print(fh.read())
        print(f"(artifacts were written under {result.run_dir} — temporary dir)")
    return result.exit_code


def _add_adapter_flag(parser) -> None:
    parser.add_argument(
        "--adapter",
        action="append",
        default=[],
        metavar="MODULE",
        help="adapter module(s) to import first (import = registration), "
        "e.g. yourproject — a child package, never a dskit subpackage",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dskit.pipeline")
    sub = parser.add_subparsers(dest="command")
    run_p = sub.add_parser(
        "run", help="execute a node-map document end to end (docs/24 §9)"
    )
    run_p.add_argument("path", help="path to the document JSON")
    run_p.add_argument(
        "--asof",
        default=None,
        metavar="YYYY-MM-DD",
        help="the run's asof (defaults to today, UTC)",
    )
    _add_adapter_flag(run_p)
    wf_p = sub.add_parser(
        "walkforward",
        help="run the document's declared walkforward section: one run per "
        "fold + an aggregate summary (ADR-0027)",
    )
    wf_p.add_argument("path", help="path to the document JSON")
    wf_p.add_argument(
        "--asof",
        default=None,
        metavar="YYYY-MM-DD",
        help="the evaluation's asof (defaults to today, UTC)",
    )
    _add_adapter_flag(wf_p)
    plan_p = sub.add_parser("plan", help="print the resolved DAG, no execution")
    plan_p.add_argument("path", help="path to the document JSON")
    _add_adapter_flag(plan_p)
    sub.add_parser(
        "nodemap",
        help="the full node-map banking run against the synthetic Node set",
    )
    sub.add_parser("demo", help="build, round-trip, and hash a demo config")
    sub.add_parser("synthetic", help="the full staged run on the synthetic venue")
    val = sub.add_parser("validate", help="load + validate a config JSON file")
    val.add_argument("path", help="path to the config JSON")
    _add_adapter_flag(val)
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args.path, args.asof, args.adapter)
    if args.command == "walkforward":
        return cmd_walkforward(args.path, args.asof, args.adapter)
    if args.command == "plan":
        return cmd_plan(args.path, args.adapter)
    if args.command == "nodemap":
        return cmd_nodemap()
    if args.command == "synthetic":
        return cmd_synthetic()
    if args.command == "validate":
        return cmd_validate(args.path, args.adapter)
    return cmd_demo()


if __name__ == "__main__":
    raise SystemExit(main())

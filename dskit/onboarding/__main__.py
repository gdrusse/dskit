"""``python -m dskit.onboarding`` — acquisition & onboarding, one command line.

The full loop, in command order:

* ``init`` — create an onboarding root (directories + P2 store pinned
  to the onboarding model). Exactly once per root.
* ``register-source <name> --catalog-source <alias> --connector <ref>
  [--config <json|@file>] [--activate]`` — register the operational
  ``source_config`` (and optionally move it draft -> active).
* ``acquire --source <name> --stream <s> --mode backfill|live`` — one
  connector pull: WORM snapshot + normalized rows + evidence records +
  a mode-keyed checkpoint.
* ``validate --suite <file> --snapshot <vid>`` — run a declarative
  suite; register the content-addressed result.
* ``certify --result <vid> --decision certified|refused [--by <who>]``
  — record the decision over one result.
* ``publish --dataset <alias> --certification <vid> [--name <label>]``
  — write the version manifest into the outbox P1 scans (ADR-0012).
* ``verify [--source <name>]`` — re-hash raw snapshots against their
  manifests; tamper evidence for WORM storage.

``--root`` names the onboarding root (default ``./onboarding_root``);
``--model`` names a custom model file when the root was created with one.

Exit codes, mirroring the pipeline: **0** ok · **3** ``validate`` ended
at gating ``block`` (a block is a RESULT, not an error) · **1** error
(every problem listed, one per line). ``certify --decision refused``
exits 0 — a refusal is a recorded decision, not a failure.
"""

from __future__ import annotations

import argparse
import json
import sys

from dskit.assets.model import load_model

from .acquire import run_acquisition
from .base import AssetError, MODES
from .certify import DECISIONS, certify
from .layout import OnboardingRoot
from .publish import publish_version
from .snapshot import verify_snapshot
from .validate import load_suite, run_suite


def _root(args) -> OnboardingRoot:
    return OnboardingRoot(args.root)


def _model(args):
    return None if args.model is None else load_model(args.model)


def _registry(args):
    return _root(args).registry(_model(args))


def _parse_config(text) -> dict:
    """Inline JSON, or ``@path`` to read it from a file — the assets
    CLI's payload idiom."""
    if text.startswith("@"):
        try:
            with open(text[1:], encoding="utf-8") as fh:
                obj = json.load(fh)
        except OSError as exc:
            raise AssetError([f"cannot read config file {text[1:]!r}: {exc}"]) from exc
        except ValueError as exc:
            raise AssetError(
                [f"config file {text[1:]!r} is not valid JSON: {exc}"]
            ) from exc
    else:
        try:
            obj = json.loads(text)
        except ValueError as exc:
            raise AssetError([f"--config is not valid JSON: {exc}"]) from exc
    if not isinstance(obj, dict):
        raise AssetError([f"config must be a JSON object, got {type(obj).__name__}"])
    return obj


# -- commands --------------------------------------------------------------


def cmd_init(args) -> int:
    root = OnboardingRoot.create(args.root, _model(args), backend=args.backend)
    print(json.dumps(root.registry(_model(args)).store.model_pin(), indent=2))
    return 0


def cmd_register_source(args) -> int:
    registry = _registry(args)
    vid = registry.register(
        "source_config",
        {
            "name": args.name,
            "catalog_source": args.catalog_source,
            "connector": args.connector,
            "config": _parse_config(args.config),
        },
        origin=args.origin,
    )
    if args.activate:
        # Idempotent convenience: an already-active re-register stays put.
        if registry.state(vid) == "draft":
            registry.transition(vid, "active", origin=args.origin)
    print(vid)
    return 0


def cmd_acquire(args) -> int:
    summary = run_acquisition(
        _root(args), _registry(args), args.source, args.stream, args.mode,
        origin=args.origin,
    )
    print(json.dumps(summary, indent=2))
    return 0


def cmd_validate(args) -> int:
    outcome = run_suite(
        _root(args), _registry(args), load_suite(args.suite), args.snapshot,
        origin=args.origin,
    )
    print(json.dumps(outcome, indent=2))
    return 3 if outcome["gating"] == "block" else 0


def cmd_certify(args) -> int:
    vid = certify(
        _registry(args), args.result, args.decision,
        certified_by=args.by, origin=args.origin,
    )
    print(vid)
    return 0


def cmd_publish(args) -> int:
    summary = publish_version(
        _root(args), _registry(args), args.dataset, args.certification,
        name=args.name, origin=args.origin,
    )
    print(json.dumps(summary, indent=2))
    return 0


def cmd_verify(args) -> int:
    import os

    root = _root(args)
    raw = os.path.join(root.root, "raw")
    sources = [args.source] if args.source else sorted(os.listdir(raw))
    checked, problems = 0, []
    for source in sources:
        source_dir = os.path.join(raw, source)
        if not os.path.isdir(source_dir):
            continue
        for acq_id in sorted(os.listdir(source_dir)):
            snap_dir = os.path.join(source_dir, acq_id)
            if not os.path.isfile(os.path.join(snap_dir, "manifest.json")):
                continue
            checked += 1
            problems.extend(verify_snapshot(snap_dir))
    print(json.dumps({"snapshots_checked": checked, "problems": problems}, indent=2))
    # Tampered WORM storage is an emergency, not a result.
    return 1 if problems else 0


# -- wiring ----------------------------------------------------------------


def _add_common(parser) -> None:
    parser.add_argument("--root", default="./onboarding_root",
                        help="onboarding root (default ./onboarding_root)")
    parser.add_argument("--model", default=None,
                        help="model JSON file; absent = the built-in onboarding model")
    parser.add_argument("--origin", default="cli",
                        help="provenance stamp on registered records")


def main(argv=None) -> int:
    top = argparse.ArgumentParser(prog="python -m dskit.onboarding",
                                  description=__doc__.splitlines()[0])
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create an onboarding root (exactly once)")
    p.add_argument("--backend", default="file",
                   help="P2 store backend: file (default), sqlite, parquet, or pkg.module:Class")
    _add_common(p)
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("register-source",
                       help="register an operational source_config")
    p.add_argument("name")
    p.add_argument("--catalog-source", required=True,
                   help="the P1 catalog Source this operates for (ADR-0003)")
    p.add_argument("--connector", required=True,
                   help="registered kind (e.g. localfiles) or pkg.module:Class")
    p.add_argument("--config", default="{}", help="inline JSON object, or @file")
    p.add_argument("--activate", action="store_true",
                   help="transition draft -> active immediately")
    _add_common(p)
    p.set_defaults(fn=cmd_register_source)

    p = sub.add_parser("acquire", help="one connector pull (snapshot + evidence)")
    p.add_argument("--source", required=True)
    p.add_argument("--stream", required=True)
    p.add_argument("--mode", required=True, choices=MODES,
                   help="backfill pulls history, live pulls forward — "
                        "independent checkpoints (ADR-0014)")
    _add_common(p)
    p.set_defaults(fn=cmd_acquire)

    p = sub.add_parser("validate", help="run a suite against a snapshot")
    p.add_argument("--suite", required=True, help="suite JSON file")
    p.add_argument("--snapshot", required=True, help="snapshot version_id")
    _add_common(p)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("certify", help="record a decision over one result")
    p.add_argument("--result", required=True, help="validation_result version_id")
    p.add_argument("--decision", required=True, choices=DECISIONS)
    p.add_argument("--by", default="", help="who decided")
    _add_common(p)
    p.set_defaults(fn=cmd_certify)

    p = sub.add_parser("publish",
                       help="write a version manifest into the outbox (ADR-0012)")
    p.add_argument("--dataset", required=True, help="P1 dataset alias")
    p.add_argument("--certification", required=True,
                   help="certification version_id (decision must be certified)")
    p.add_argument("--name", default="", help="version label; default <dataset>@NNNNNNNN")
    _add_common(p)
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("verify", help="re-hash raw snapshots against manifests")
    p.add_argument("--source", default="", help="limit to one source")
    _add_common(p)
    p.set_defaults(fn=cmd_verify)

    args = top.parse_args(argv)
    try:
        return args.fn(args)
    except AssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

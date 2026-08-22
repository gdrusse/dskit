"""``python -m dskit.assets`` — the asset platform's one command line.

Same thesis as the pipeline CLI: one entry point, every project; a store
plus a model document IS the platform. Commands:

* ``init`` — create a store root pinned to a model (exactly once).
* ``validate-model [path]`` — shape-check a model file (or the built-in
  default) and print its governance hash.
* ``register <kind> --payload <json|@file> [--ref name=vid ...]`` —
  validate + persist one asset version; prints its version_id.
* ``get <vid>`` / ``list [kind]`` / ``state <vid>`` — read the record,
  the catalog, the derived lifecycle state.
* ``transition <vid> <to>`` — one default-deny lifecycle move.
* ``lineage <vid> [--show edges|parents|children|ancestors|descendants]``
  — the graph around one asset.
* ``ingest-run <run_dir>`` — ADR-0008's seam: observe a completed
  pipeline run from its directory.

``--store`` names the store root (default ``./asset_store``);
``--model`` names a model file, absent meaning the built-in default —
either way the registry verifies it against the store's pin.

Exit codes: **0** ok · **1** error (every problem listed, one per line).
"""

from __future__ import annotations

import argparse
import json
import sys

from .base import AssetError
from .default_model import default_model
from .ingest import ingest_run
from .lineage import Lineage
from .model import load_model, model_hash
from .registry import Registry
from .store import FileStore


def _load_model(path):
    """A model file when named, the built-in default when not."""
    return default_model() if path is None else load_model(path)


def _registry(args) -> Registry:
    return Registry(FileStore(args.store), _load_model(args.model))


def _parse_payload(text) -> dict:
    """Inline JSON, or ``@path`` to read it from a file."""
    if text.startswith("@"):
        try:
            with open(text[1:], encoding="utf-8") as fh:
                obj = json.load(fh)
        except OSError as exc:
            raise AssetError([f"cannot read payload file {text[1:]!r}: {exc}"]) from exc
        except ValueError as exc:
            raise AssetError([f"payload file {text[1:]!r} is not valid JSON: {exc}"]) from exc
    else:
        try:
            obj = json.loads(text)
        except ValueError as exc:
            raise AssetError([f"--payload is not valid JSON: {exc}"]) from exc
    if not isinstance(obj, dict):
        raise AssetError([f"payload must be a JSON object, got {type(obj).__name__}"])
    return obj


def _parse_refs(pairs) -> dict:
    refs = {}
    for pair in pairs:
        name, eq, vid = pair.partition("=")
        if not eq or not name or not vid:
            raise AssetError([f"--ref must be name=version_id, got {pair!r}"])
        refs[name] = vid
    return refs


# -- commands --------------------------------------------------------------


def cmd_init(args) -> int:
    model = _load_model(args.model)
    store = FileStore.create(args.store, model)
    print(json.dumps(store.model_pin(), indent=2))
    return 0


def cmd_validate_model(args) -> int:
    model = _load_model(args.path)
    print(json.dumps(
        {"name": model.name, "kinds": sorted(model.kinds), "model_hash": model_hash(model)},
        indent=2,
    ))
    return 0


def cmd_register(args) -> int:
    vid = _registry(args).register(
        args.kind,
        _parse_payload(args.payload),
        refs=_parse_refs(args.ref),
        origin=args.origin,
        notes=args.notes,
    )
    print(vid)
    return 0


def cmd_get(args) -> int:
    reg = _registry(args)
    record = reg.get(args.version_id)
    obj = record.to_obj()
    obj["state"] = reg.state(args.version_id)  # None -> null: record-only
    print(json.dumps(obj, indent=2))
    return 0


def cmd_list(args) -> int:
    reg = _registry(args)
    for vid in reg.list(args.kind):
        record = reg.get(vid)
        print(f"{vid}  {record.kind:<16} {record.payload.get('name', '')}")
    return 0


def cmd_state(args) -> int:
    state = _registry(args).state(args.version_id)
    print("record-only" if state is None else state)
    return 0


def cmd_transition(args) -> int:
    reg = _registry(args)
    reg.transition(args.version_id, args.to, origin=args.origin)
    print(reg.state(args.version_id))
    return 0


def cmd_lineage(args) -> int:
    lineage = Lineage(_registry(args))
    result = getattr(lineage, args.show)(args.version_id)
    print(json.dumps(result, indent=2))
    return 0


def cmd_ingest_run(args) -> int:
    summary = ingest_run(_registry(args), args.run_dir, origin=args.origin)
    print(json.dumps(summary, indent=2))
    return 0


# -- wiring ----------------------------------------------------------------


def _add_common(parser) -> None:
    parser.add_argument("--store", default="./asset_store",
                        help="store root (default ./asset_store)")
    parser.add_argument("--model", default=None,
                        help="model JSON file; absent = the built-in default model")


def main(argv=None) -> int:
    top = argparse.ArgumentParser(prog="python -m dskit.assets",
                                  description=__doc__.splitlines()[0])
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a store root pinned to a model")
    _add_common(p)
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("validate-model", help="shape-check a model file, print its hash")
    p.add_argument("path", nargs="?", default=None,
                   help="model JSON file; absent = the built-in default")
    p.set_defaults(fn=cmd_validate_model)

    p = sub.add_parser("register", help="validate + persist one asset version")
    p.add_argument("kind")
    p.add_argument("--payload", required=True, help="inline JSON object, or @file")
    p.add_argument("--ref", action="append", default=[], metavar="NAME=VID",
                   help="reference to a registered asset (repeatable)")
    p.add_argument("--origin", default="cli")
    p.add_argument("--notes", default="")
    _add_common(p)
    p.set_defaults(fn=cmd_register)

    p = sub.add_parser("get", help="print one record (+ derived state)")
    p.add_argument("version_id")
    _add_common(p)
    p.set_defaults(fn=cmd_get)

    p = sub.add_parser("list", help="list version_ids, kind and alias")
    p.add_argument("kind", nargs="?", default=None)
    _add_common(p)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("state", help="print an asset's derived lifecycle state")
    p.add_argument("version_id")
    _add_common(p)
    p.set_defaults(fn=cmd_state)

    p = sub.add_parser("transition", help="one default-deny lifecycle move")
    p.add_argument("version_id")
    p.add_argument("to")
    p.add_argument("--origin", default="cli")
    _add_common(p)
    p.set_defaults(fn=cmd_transition)

    p = sub.add_parser("lineage", help="the graph around one asset")
    p.add_argument("version_id")
    p.add_argument("--show", default="edges",
                   choices=("edges", "parents", "children", "ancestors", "descendants"))
    _add_common(p)
    p.set_defaults(fn=cmd_lineage)

    p = sub.add_parser("ingest-run", help="observe a completed pipeline run (ADR-0008)")
    p.add_argument("run_dir")
    p.add_argument("--origin", default="ingest-run")
    _add_common(p)
    p.set_defaults(fn=cmd_ingest_run)

    args = top.parse_args(argv)
    try:
        return args.fn(args)
    except AssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

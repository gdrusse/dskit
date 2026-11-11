r"""Turn an integration template into a runnable MIO document (ADR-0313 I8).

A template under ``docs/plans/mio-json/*.template.json`` carries the arm (``study_overlay``) and the owner's six-node
packet pipeline with ``${study}`` and ``${output_root}`` placeholders. The owner's route to a run document is the one
the quarter documents already follow: the study object of the quarter's own resolved run document (its ``plan``
node) is the base, and this tool deep-merges the overlay into that study's ``domain`` and expands the pipeline with
``dskit.pipeline.workflow.expand``. Dicts merge key by key; every other value is replaced, by design: the overlay's
``methods`` REPLACES the base's formulations in the instantiated document (the nine ran in the base's own document;
the arm document holds only its new method ids).

It refuses what would override locked content, each with a stable code (the rules live in
``dskit.pipeline.method_blocks.MethodBlocks``): ``locked_method_ids_changed`` (the base, queue or merged
declaration differs from the ordered A0010 ids), ``locked_method_id_collision`` (an overlay id matches a locked
or existing base id after normalization), ``locked_formulation_spec_changed`` (a base spec or queue digest
differs from its pin), ``development_seal_violation`` (a base or overlay clock, or the template's own
``development_seal``, violates ``DEVELOPMENT_SEAL``) and
``unreplaced_placeholder`` (a leftover marker, see :func:`is_placeholder`).

Run it from the repository root::

    PYTHONPATH=.:children/index_options python children/index_options/tools/mio_instantiate.py TEMPLATE.json \\
        --base QUARTER_RUN_DOC.json --name NAME --output-root DIR --edge-maps-pin PIN.json --out DOC.json \\
        [--decision-week YYYY-MM-DD] [--cap-at-development-seal]

It lives outside the pinned ``index_options`` package (like ``mio_parallel_queue.py``), so editing it never moves a
study's code pin. It reads no market data.
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL, MethodBlocks
from dskit.pipeline.workflow import expand
from index_options.ledger_studies import LOCKED_FORMULATION_HASHES

__all__ = ["deep_merge", "instantiate", "is_placeholder", "main"]

#: The keys of a template that are not part of the document it makes.
TEMPLATE_ONLY = ("kind", "placeholders", "development_seal", "study_overlay")
#: The ONE placeholder convention: a string that starts ``PLACEHOLDER``, is a lone ``<marker>``, starts ``TODO``/``TBD``,
#: or still holds a ``${...}`` reference. Anything the tool fills is replaced; any survivor refuses.
_PLACEHOLDER = re.compile(r"^\s*(PLACEHOLDER|TODO|TBD)\b|^\s*<[^<>]+>\s*$|\$\{")
#: The checked-in queue declaration, which must agree with the study lock.
QUEUE_CONFIG = Path(__file__).resolve().parents[1] / "docs/plans/mio-parallel/queue.example.json"


def is_placeholder(value):
    """Return whether ``value`` is a string still carrying a template placeholder marker."""
    return isinstance(value, str) and _PLACEHOLDER.search(value) is not None


def deep_merge(base, overlay):
    """Return ``base`` with ``overlay`` merged in: mappings recursively, anything else replaced; ``notes`` is skipped."""
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if key == "notes":
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _base_study(base):
    """Return the base document's study object; every node that carries one must carry the same."""
    studies = [node["params"]["study"] for node in base["pipeline"].values()
               if isinstance(node.get("params"), dict) and "study" in node["params"]]
    if not studies or any(s != studies[0] for s in studies):
        raise ValueError("base_document_studies_differ: the base run document must carry one study object")
    return studies[0]


def _leftovers(value, path=""):
    """Find the paths of placeholder keys or values in the expanded document."""
    if isinstance(value, str):
        return [path] if is_placeholder(value) else []
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    hits = []
    for key, item in items:
        if key == "notes" and isinstance(item, str):
            continue
        field = f"{path}/{key}"
        if is_placeholder(key):
            hits.append(field)
        hits.extend(_leftovers(item, field))
    return hits


def _refuse(problems):
    """Raise the first problem's code with every problem in the message."""
    if problems:
        raise ValueError("; ".join(problems))


def instantiate(template, base, *, name, output_root, edge_maps_pin=None, decision_week=None,
                queue_config=QUEUE_CONFIG, cap_at_development_seal=False):
    """Return the run document of ``template`` on the quarter of ``base``; refuses while any placeholder is unresolved."""
    overlay = copy.deepcopy(template["study_overlay"])
    domain = overlay["domain"]
    if cap_at_development_seal:
        domain["params"].setdefault("bridge", {})["protected_end_before"] = DEVELOPMENT_SEAL
    if "edge_maps" in domain.get("sources", {}):
        if edge_maps_pin is None:
            raise ValueError("edge_maps_pin_missing: this template binds the walk's maps; pass the onboarding pin")
        domain["sources"]["edge_maps"]["pin"] = copy.deepcopy(edge_maps_pin)
    if "decision_weeks" in domain.get("params", {}):
        if decision_week is None:
            raise ValueError("decision_week_missing: this template lists exactly one decision week; pass it")
        domain["params"]["decision_weeks"] = [decision_week]
    study = _base_study(copy.deepcopy(base))
    params = study["domain"]["params"]
    # `clocks` is not a study param (the packet owns the clocks); here it is the overlay's seal claim, checked, never merged.
    clocks = domain["params"].pop("clocks", {})
    ends = [params["bridge"]["end_before"]] + [p["end_before"] for p in study["packets"]]
    claimed = [template.get("development_seal", DEVELOPMENT_SEAL), clocks.get("tail_end_before", DEVELOPMENT_SEAL)]
    _refuse(MethodBlocks.seal_violations(ends + claimed) + (
        [] if claimed[0] == DEVELOPMENT_SEAL else
        [f"development_seal_violation: the template's development_seal {claimed[0]!r} differs from {DEVELOPMENT_SEAL}"]))
    pinned = LOCKED_FORMULATION_HASHES
    queue = json.loads(Path(queue_config).read_text())
    _refuse(MethodBlocks.lock_problems(pinned, queue.get("selection_formulations"), ()))
    if queue.get("selection_formulation_hashes") != pinned:
        raise ValueError("locked_formulation_spec_changed: queue formulation digests differ from the A0010 pins")
    if "locked_method_ids" in params:
        _refuse(MethodBlocks.lock_problems(pinned, params["locked_method_ids"], ()))
    _refuse(MethodBlocks.locked_spec_problems(pinned, params["methods"]))
    study["domain"] = deep_merge(study["domain"], domain)
    merged = study["domain"]["params"]
    _refuse(MethodBlocks.lock_problems(pinned, merged.get("locked_method_ids"),
                                       [m["id"] for m in merged["methods"]], [m["id"] for m in params["methods"]]))
    body = {key: value for key, value in template.items() if key not in TEMPLATE_ONLY}
    document = expand(body, {"study": study, "output_root": str(output_root)})
    document["name"] = name
    left = _leftovers(document)
    if left:
        raise ValueError(f"unreplaced_placeholder: {left}")
    return document


def main(argv=None):
    """Run the command line; return the exit code (0 written, 1 refused)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("template")
    parser.add_argument("--base", required=True, help="the quarter's resolved MIO run document")
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--edge-maps-pin", help="JSON file holding the onboarding pin of the published edge maps")
    parser.add_argument("--decision-week")
    parser.add_argument("--cap-at-development-seal", action="store_true",
                        help="cap the research packet's settlement tail at the development seal")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    load = lambda path: json.loads(Path(path).read_text())  # noqa: E731
    pin = load(args.edge_maps_pin) if args.edge_maps_pin else None
    try:
        document = instantiate(load(args.template), load(args.base), name=args.name, output_root=args.output_root,
                               edge_maps_pin=pin, decision_week=args.decision_week,
                               cap_at_development_seal=args.cap_at_development_seal)
    except ValueError as error:
        print(error)
        return 1
    Path(args.out).write_text(json.dumps(document, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

r"""Turn an integration template into a runnable MIO document (ADR-0313 I8).

A template under ``docs/plans/mio-json/*.template.json`` carries the arm (``study_overlay``) and the owner's six-node
packet pipeline with ``${study}`` and ``${output_root}`` placeholders. The owner's route to a run document is the one
the quarter documents already follow: the study object of the quarter's own resolved run document (its ``plan``
node) is the base, and this tool deep-merges the overlay into that study's ``domain`` and expands the pipeline with
``dskit.pipeline.workflow.expand``. Dicts merge key by key; every other value (a list such as ``methods``) is replaced.

Run it from the repository root::

    PYTHONPATH=.:children/index_options python children/index_options/tools/mio_instantiate.py TEMPLATE.json \\
        --base QUARTER_RUN_DOC.json --name NAME --output-root DIR --edge-maps-pin PIN.json --out DOC.json \\
        [--decision-week YYYY-MM-DD]

It lives outside the pinned ``index_options`` package (like ``mio_parallel_queue.py``), so editing it never moves a
study's code pin. It reads no market data.
"""

import argparse
import copy
import json
import sys
from pathlib import Path

from dskit.pipeline.workflow import expand

__all__ = ["deep_merge", "instantiate", "main"]

#: The keys of a template that are not part of the document it makes.
TEMPLATE_ONLY = ("kind", "placeholders", "development_seal", "study_overlay")
PLACEHOLDER = "PLACEHOLDER"


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
    """Every string of ``value`` that is still a template placeholder, with its path."""
    if isinstance(value, str):
        return [path] if value.startswith(PLACEHOLDER) else []
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    return [hit for key, item in items for hit in _leftovers(item, f"{path}/{key}")]


def instantiate(template, base, *, name, output_root, edge_maps_pin=None, decision_week=None):
    """Return the run document of ``template`` on the quarter of ``base``; refuses while any placeholder is unresolved."""
    overlay = copy.deepcopy(template["study_overlay"])
    domain = overlay["domain"]
    if "edge_maps" in domain.get("sources", {}):
        if edge_maps_pin is None:
            raise ValueError("edge_maps_pin_missing: this template binds the walk's maps; pass the onboarding pin")
        domain["sources"]["edge_maps"]["pin"] = copy.deepcopy(edge_maps_pin)
    if "decision_weeks" in domain.get("params", {}):
        if decision_week is None:
            raise ValueError("decision_week_missing: this template lists exactly one decision week; pass it")
        domain["params"]["decision_weeks"] = [decision_week]
    study = _base_study(base)
    # `clocks` is not a study param (the packet owns the clocks); here it is the seal the base must already respect.
    seal = domain["params"].pop("clocks", {}).get("tail_end_before", template["development_seal"])
    ends = [study["domain"]["params"]["bridge"]["end_before"]] + [p["end_before"] for p in study["packets"]]
    if max(ends) > seal:
        raise ValueError(f"development_seal_exceeded: the base reaches {max(ends)}, the seal is {seal}")
    study["domain"] = deep_merge(study["domain"], domain)
    body = {key: value for key, value in template.items() if key not in TEMPLATE_ONLY}
    document = expand(body, {"study": study, "output_root": str(output_root)})
    document["name"] = name
    left = _leftovers(document)
    if left:
        raise ValueError(f"placeholder_unresolved: {left}")
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
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    load = lambda path: json.loads(Path(path).read_text())  # noqa: E731
    pin = load(args.edge_maps_pin) if args.edge_maps_pin else None
    try:
        document = instantiate(load(args.template), load(args.base), name=args.name, output_root=args.output_root,
                               edge_maps_pin=pin, decision_week=args.decision_week)
    except ValueError as error:
        print(error)
        return 1
    Path(args.out).write_text(json.dumps(document, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

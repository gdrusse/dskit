# dskit

A data-science toolkit. Its first subpackage, **`dskit.pipeline`**, is a
venue-agnostic pipeline engine: **one JSON document declares a whole process**
(data → ingest → predict → optimize → report) and **one command runs any such
document**. More subpackages are added alongside it as the toolkit grows.

The design thesis: *it's just a pipeline and a config file.* Everything a run
does — which code, how it's wired, how it splits, what gates deployment — lives
in one hashable document, and the same command line runs every one.

## Install

```bash
pip install -e .            # the tier-1 core is pure stdlib — no heavy deps
pip install -e ".[dev]"     # + pytest / hypothesis / ruff for development
pip install -e ".[all]"     # + every optional library the tier-2 packs can use
```

The core deliberately has **zero required dependencies**: a document must stay
*plannable* on a machine with nothing installed, because planning imports the
node classes. Heavy libraries (numpy, scikit-learn, torch, transformers,
optuna, pyomo) are optional extras, imported only inside a node's `run()`.

## The 60-second path

```bash
python -m dskit.pipeline nodemap                                   # full demo run on synthetic nodes
python -m dskit.pipeline run  examples/pipeline/nodemap-minimal.json --asof 2026-01-01
python -m dskit.pipeline plan examples/pipeline/nodemap-minimal.json    # the resolved DAG, no execution
python -m dskit.pipeline validate <document.json>                 # shape + identity hash
```

Exit codes: **0** ran · **3** halted at a NO-GO gate (a halt is a result) ·
**1** error. That command line is identical for every project — adapters ship
components, never their own CLIs.

## The three tiers

One test decides where code goes: *could a project that has never heard of your
problem domain use it?*

| Tier | Path | Rule |
|---|---|---|
| 1. Core | `dskit/pipeline/*.py` | stdlib only; domain-neutral |
| 2. Library packs | `dskit/pipeline/libs/<lib>.py` | generic wrappers for common DS libraries (numpy, sklearn, torch, transformers, optuna, pyomo); may name their library **only inside `run()`** |
| 3. Adapters | your own package (a sibling of `dskit`) | domain-specific; may import anything |

The core imports nothing outside itself and nothing heavy — a rule enforced by
`tests/pipeline/test_purity.py`. Adapters import the toolkit; the toolkit never
imports an adapter.

## Writing your own node

Everything a document can reference is a `Node` subclass:

```python
from dskit.pipeline.node import Node

class MyModel(Node):
    role = "train"                       # from the fixed ROLES; the class declares it
    outputs = ("signal", "artifact")     # the contract run() must return exactly

    @classmethod
    def validate_params(cls, params):    # default-DENY: list allowed knobs, refuse the rest
        return [] if params.get("epochs", 1) > 0 else ["epochs must be > 0"]

    def run(self, ctx, inputs):
        import torch                      # heavy imports go INSIDE run()
        ...
        return {"signal": sig, "artifact": path}
```

Reference it from a document two ways, both first-class:

- **by import path** — `"uses": "yourpkg.nodes:MyModel"` — resolves itself, no
  registration needed;
- **by registered kind name** — call `register_node_kind("my-model", MyModel)`
  at your package's import, then run with
  `python -m dskit.pipeline run doc.json --adapter yourpkg` (importing the
  module *is* the registration).

Point the reusable conformance suite at your registry and the node contracts are
checked mechanically:

```python
from dskit.pipeline.conformance import conformance_suite
TestConformance = conformance_suite(registry=MY_NODE_KINDS, module="yourpkg.nodes")
```

## Adding another subpackage

`dskit` is a plain package; drop a new subpackage beside `pipeline/`
(`dskit/<name>/`) and it ships with the distribution. `dskit.pipeline` stays the
engine; siblings add capabilities.

## The document, in brief

```jsonc
{
  "name": "my-run",                      // series name -> run dirs {name}-{asof}-{hash8}
  "pipeline": {                          // the process: a keyed node map
    "<key>": {
      "uses": "<kind or pkg.module:Class>",
      "inputs": { "port": "$other_node.output" },   // wiring; order = topo sort
      "params": { ... },                            // the class's knobs
      "mode": "train"                               // trainable roles only
    }
  },
  "splits": { "kind": "time|random|trailing", ... },
  "outputs": { "run_root": "" }          // "" -> ./pipeline_runs
}
```

Identity is a sha256 over the canonical JSON (`notes`/`env`/`outputs` excluded).
Same hash = same experiment. A run leaves behind `config.json`, `plan.json`,
`resolved.json`, `result.json`, a verdict-first `report.md`, per-node records
and artifacts under one run directory.

## Tests

```bash
pip install -e ".[dev,all]"
python -m pytest -q                      # full suite
python -m pytest tests/pipeline -q       # tier-1 core + purity gate
python -m pytest tests/pipeline_libs -q  # tier-2 library packs
```

# index_options

Offline, synthetic-only index-options research scaffold. S0 proves ingestion,
instrument matching and exact expiry cashflows, **not an edge or an executable
strategy**. No models, broker, vendor adapter, real market data or serving loop.

## Install and test (WSL2)

From this child directory, use a dedicated environment with a trusted DSKIT
checkout. Replace the placeholder with that checkout's absolute path:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e /absolute/path/to/dskit -e . pytest
python -m pytest tests -q
```

No heavy dependencies. Do not install this child into another project's active
environment. After graduation, the same child and installed DSKIT suffice;
there is no import from a parent repository path.

## Temporary public-CLI demo

With that environment active, start in the child root. This uses only original
synthetic fixtures; every demo gets a fresh store, cursor and journal.

```bash
(
set -euo pipefail
index_options_child="$PWD"
index_options_demo=$(mktemp -d)
cp -R configs fixtures "$index_options_demo/"
cd "$index_options_demo"
export DSKIT_JOURNAL_ROOT="$index_options_demo"
python -m dskit.journal init --root .
python -m dskit.onboarding init --root ./ob
python -m dskit.onboarding register-source index-fixture \
  --catalog-source index-fixture-src --connector localfiles \
  --config @configs/source-fixture.json --activate --root ./ob
for stream in contracts quotes settlements; do
  acquired=$(python -m dskit.onboarding acquire --source index-fixture \
    --stream "$stream" --mode backfill --root ./ob)
  snapshot=$(python -c 'import json,sys; x=json.load(sys.stdin); assert x["records"] > 0; print(x["snapshot"])' <<< "$acquired")
  python -m dskit.onboarding validate --suite configs/suite-fixture.json \
    --snapshot "$snapshot" --root ./ob
done
python -m dskit.pipeline run configs/run-fixture.json --asof 2026-02-21
python - <<'PY'
import json
from pathlib import Path
from dskit.pipeline.driver import resolve_json_artifact
path = next(Path("pipeline_runs").glob("**/nodes/*-diagnostic.json"))
record = json.loads(path.read_text())
report = resolve_json_artifact(str(path.parent.parent), record["outputs"]["report"])
print(json.dumps(report, indent=2))
assert report["net_pnl_usd"] == "252"
PY
unset DSKIT_JOURNAL_ROOT
cd "$index_options_child"
)
```

The demo directory is retained for inspection. Field validation must pass on
each snapshot; expected counts are contracts=4, quotes=4, settlements=3. The
suite checks row fields, not presence of the other streams in a one-stream
snapshot. The complete automated demo asserts those counts, pass results,
artifact digest, output and real journal recording:

```bash
python -m pytest tests/test_integration.py::test_public_cli_round_trip_and_positive_journal_isolation -q
```

## Interfaces and limits

- LocalFilesConnector supplies acquisition. source-fixture.json names the path
  and effective-time field; no child connector exists.
- ContractRows, QuoteRows and SettlementRows retain only root, source and the
  optional as_of_acquisition_ms from ObservationRows. Fixed stream, revision
  keys and timestamp accessors are tested. All three are forbidden for serving.
- CondorPayoffDiagnostic takes three record lists and exact corpus/leg/quote/
  settlement references, positive count, and whole-outcome fees_usd. Its report
  is a JsonArtifact persisted by the parent driver. It too is forbidden for
  serving. Decimal strings are required for prices, strikes, levels and fees;
  multiplier/count/quantities are integers, never booleans.
- The report is synthetic_ex_post_diagnostic and decision_eligible=false.
  Long legs pay ask; short legs receive bid. This is a hypothetical package,
  not evidence of a fill. Settlement is deliberately an ex-post outcome.
- Quotes share one instant, precede last trade, and cover every leg's size.
  Contracts explicitly declare the settlement instant; no exchange calendar
  is inferred. The glossary gives the cashflow arithmetic.
- Readers use the parent's latest-eligible-acquisition winner per revision key;
  identical winning repeats pass, conflicting winning ties refuse. Every
  winning row is domain-validated. Direct selected references must be unique.
  Suite results are independent evidence, not a hidden reader gate.
- Event time, known_at and actual acquired_at are different clocks. Acquisition
  cutoff filters only the last. Fresh-root pulls are mandatory for this demo;
  strict effective-time cursor resumption does not capture backdated revisions.
- Fingerprint and execution share the parent's frozen snapshot. A new instance
  reads new bytes. No defense against hostile Python controlling the interpreter
  is claimed.

- Distribution harness (ADR-0168, synthetic only): `configs/run-synthetic-distribution.json`
  wires dskit's GJR path, realized-vol features, horizon label, a sample-set
  model and proper scores to `CondorDistributionReport`, which evaluates a
  condor declared in standardized strikes (`strikes_z`) under each forecast.
  Run `python -m dskit.pipeline walkforward configs/run-synthetic-distribution.json --asof 1978-06-01`
  (journal initialized). Swap the `model` node to test another rung; never
  decision-eligible.

[Explanation](docs/explanations/README.md) defines the instrument and example.
[Plan](docs/plans/README.md) describes the separately gated data/ML/MIO work.

## Layout

```text
pyproject.toml; .gitignore; README.md; AGENTS.md; CLAUDE.md
journal.json
index_options/             # __init__.py, contracts.py, observations.py, nodes.py,
                           # distribution.py (condor under a forecast, ADR-0168)
configs/                   # source-fixture.json, suite-fixture.json, run-fixture.json,
                           # run-synthetic-distribution.json (ADR-0168 harness)
fixtures/                  # contracts.jsonl, quotes.jsonl, settlements.jsonl
docs/decisioning/           # actions.csv, owner path.csv, generated README.md
docs/explanations/README.md # glossary and worked synthetic payoff
docs/plans/README.md        # gated research stages
docs/memos/README.md        # execution-evidence convention
docs/research/              # README.md, .gitkeep; distribution-modeling/ notes
tests/                     # conftest.py; test_contracts, observations, nodes,
                           # configs, integration, distribution,
                           # synthetic_distribution_run (.py)
```

Journal infrastructure starts empty. Only the human owner changes Path or
Current Work. The generated decisioning README shows the full Path and latest
10 Actions; actions.csv retains append-only history. Never hand-edit generated
files. Tests and the demo journal in isolated temporary directories.

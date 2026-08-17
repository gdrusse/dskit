"""The toolkit's own kinds under the toolkit's own conformance suite.

The suite (``dskit/pipeline/conformance.py``) was built for adapter
packs; this file is the dogfood run — the seven toolkit-owned/shipped
kinds held to the same bar, most pointedly the default-deny check (a
knob the document sets and the node never reads is a lie the run tells
about itself; every one of these classes accepted any unknown key until
2026-08-14).

Two deliberate deviations from a typical adapter hookup:

* ``registry`` is an EXPLICIT ``(name, cls)`` pairs tuple, never
  ``DEFAULT_NODE_KINDS`` — the global registry's content is import-order
  dependent (an adapter registers into it whenever any test
  imports it), and a conformance bar over a registry whose membership
  depends on what ran first is not a bar.
* no ``module=`` — the kinds span three modules, and tier-1 import
  purity (stdlib-only, heavy libraries blocked) is already enforced for
  every ``dskit/pipeline`` module by ``tests/pipeline/test_purity.py``.

Every probe is built from small in-memory records (plain dicts — the
kinds accept dicts and attribute-bearing records interchangeably); no
data file, no tmp fixture root. ``stream_ports`` is set EXPLICITLY on
every probe that carries ``inputs``, ``()`` where no port is a stream.
"""

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.kinds_flow import BankingReport, Eligibility, EventBank, Filter
from dskit.pipeline.kinds_report import RunReport
from dskit.pipeline.kinds_search import HpoGrid
from dskit.pipeline.kinds_stats import StatTest, Validate

#: The eight toolkit kinds, paired explicitly (see module docstring for
#: why this is not DEFAULT_NODE_KINDS).
TOOLKIT_NODE_KINDS = (
    ("stat_test", StatTest),
    ("validate", Validate),
    ("filter", Filter),
    ("event-bank", EventBank),
    ("eligibility", Eligibility),
    ("banking-report", BankingReport),
    ("hpo-grid", HpoGrid),
    ("run-report", RunReport),
)

#: The independent role census (docs/24 §5 + the pipeline CLAUDE.md kind
#: table) — cross-checked against the classes so a mislabelled role
#: cannot silently exit its checks.
TOOLKIT_ROLES = {
    "stat_test": "stat_test",
    "validate": "score",
    "filter": "transform",
    "event-bank": "accrual",
    "eligibility": "gate",
    "banking-report": "report",
    "hpo-grid": "search",
    "run-report": "report",
}


def _record(instrument, contract, asof_ms, **extra):
    """One in-memory record — the plain-dict shape every record-consuming
    toolkit kind accepts (their shared ``_field`` accessors serve dicts
    and attribute-bearing objects through one code path)."""
    return {
        "instrument": instrument,
        "contract": contract,
        "asof_ms": asof_ms,
        **extra,
    }


def _records():
    """A tiny two-instrument stream with the fields the kinds test:
    ``usable``/``mid`` for the filter, ``group`` for the bank's
    ``distinct_by``, ``cluster`` for the scorer's pairing."""
    return [
        _record("AAA", "AAA-1", 10, usable=True, mid=0.4, group="AAA:g0", cluster="c0"),
        _record("AAA", "AAA-2", 11, usable=True, mid=0.6, group="AAA:g0", cluster="c0"),
        _record("BBB", "BBB-1", 12, usable=True, mid=0.5, group="BBB:g0", cluster="c1"),
    ]


#: Settled outcomes for the stream above — presence marks settledness.
_OUTCOMES = {"AAA-1": True, "AAA-2": False, "BBB-1": True}


def probes(tmp_path):
    """One NodeProbe per kind. No probe needs ``tmp_path`` (everything is
    in-memory), but the factory signature is the suite's contract."""
    records = _records()
    return {
        "stat_test": NodeProbe(
            params={"alpha": 0.05, "correction": "bh", "n_boot": 1000, "seed": 0},
            inputs={
                "scores": {
                    "AAA": {"c0": 0.21, "c1": 0.09},
                    "BBB": {"c0": -0.05, "c1": 0.02},
                }
            },
            stream_ports=(),  # scores is a materialized dict, not a stream
            runnable=True,
        ),
        "validate": NodeProbe(
            params={"split": "val", "metric": "brier", "min_events": 1},
            required=("split",),
            inputs={
                "records": records,
                "signal": {"AAA-1": 0.8, "AAA-2": 0.3, "BBB-1": 0.6},
                "baseline": {"AAA-1": 0.5, "AAA-2": 0.5, "BBB-1": 0.5},
                "outcomes": dict(_OUTCOMES),
            },
            stream_ports=("records",),
            runnable=True,
        ),
        "filter": NodeProbe(
            params={
                "require_usable": True,
                "where": [{"field": "mid", "op": ">", "value": 0.1}],
            },
            inputs={"records": records, "instruments": ["AAA", "BBB"]},
            stream_ports=("records",),
            runnable=True,
        ),
        "event-bank": NodeProbe(
            params={"count": "settled", "distinct_by": "group", "strictly_before": 100},
            inputs={"events": records, "outcomes": dict(_OUTCOMES)},
            stream_ports=("events",),
            runnable=True,
        ),
        "eligibility": NodeProbe(
            params={"min_events": 2},
            required=("min_events",),
            inputs={"banked": {"AAA": 3, "BBB": 1}},
            stream_ports=(),  # counts dict, not a stream
            runnable=True,
        ),
        "banking-report": NodeProbe(
            params={"min_events": 2},
            required=("min_events",),
            inputs={
                "banked": {"AAA": 3, "BBB": 1},
                "family": ["AAA"],
                "extents": {"AAA": {"first_ms": 10, "last_ms": 11}},
            },
            stream_ports=(),  # counts/family/extents are materialized values
            runnable=True,
        ),
        "run-report": NodeProbe(
            params={"title": "conformance run"},
            # The probe deliberately wires a SURVIVING instrument against
            # zero lots: the I-232 condition is the kind's whole reason to
            # exist, so the conformance run exercises it rather than a
            # quiet path where no flag is raised.
            inputs={
                "validation": {
                    "stage": "validation",
                    "split": "val",
                    "totals": {"rows_seen": 3, "rows_scored": 3},
                    "instruments": {"AAA": {"loss": 0.11, "n_scored": 2}},
                },
                "edge": {
                    "stage": "edge test",
                    "totals": {"family_size": 2, "n_survivors": 1},
                    "instruments": {"AAA": {"p_value": 0.001, "survived": True}},
                },
                "sizing": {
                    "stage": "sizing",
                    "totals": {"lots": 0, "n_entered": 1},
                    "instruments": {"AAA": {"lots": 0, "n_entered_zero_lots": 1}},
                },
                "survivors": ["AAA"],
                "lots": 0,
            },
            stream_ports=(),  # every port is a materialized value
            runnable=True,
        ),
        "hpo-grid": NodeProbe(
            # objective's VALUE is a $-reference string BY DESIGN — the
            # default-deny closes unknown KEYS, never reference values.
            params={
                "space": {"train.lr": [1e-4, 3e-4]},
                "objective": "$validate.metrics.loss",
                "select": "min",
                "n_trials": 2,
                "seeds": [0, 1],
                "seed": 0,
            },
            required=("space", "objective"),
            # NOT runnable: run() refuses without the driver-injected
            # ctx.rerun seam — exactly as designed, and exactly why the
            # run-contract check cannot exercise it here.
        ),
    }


TestToolkitConformance = conformance_suite(
    registry=TOOLKIT_NODE_KINDS,
    probes=probes,
    expected_roles=TOOLKIT_ROLES,
    name="TestToolkitConformance",
)

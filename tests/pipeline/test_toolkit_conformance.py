"""The toolkit's own kinds under the toolkit's own conformance suite.

The suite (``dskit/pipeline/conformance.py``) was built for adapter
packs; this file is the dogfood run — every toolkit-owned/shipped kind
held to the same bar, most pointedly the default-deny check (a knob the
document sets and the node never reads is a lie the run tells about
itself; every one of these classes accepted any unknown key until
2026-08-14).

The relational three (``concat``/``join``/``derive``) are here for a
second reason. They are the kinds a JOINT document leans on, and their
whole value is what they REFUSE — an untagged union, an overlapping
namespace, an unmatched row, a defaulted branch. A refusal that stops
firing is invisible, so the probes below exercise them on real merges
and the negative cases live in ``test_kinds_flow.py`` next to them.

Two deliberate deviations from a typical adapter hookup:

* ``registry`` is an EXPLICIT ``(name, cls)`` pairs tuple, never
  ``DEFAULT_NODE_KINDS`` — the global registry's content is import-order
  dependent (an adapter registers into it whenever any test
  imports it), and a conformance bar over a registry whose membership
  depends on what ran first is not a bar.
* no ``module=`` — the kinds span six modules, and tier-1 import
  purity (stdlib-only, heavy libraries blocked) is already enforced for
  every ``dskit/pipeline`` module by ``tests/pipeline/test_purity.py``.

Every probe is built from small in-memory records (plain dicts — the
kinds accept dicts and attribute-bearing records interchangeably); only
the table pair touches ``tmp_path``, because a digest-pinned file on
disk IS that pair's subject. ``stream_ports`` is set EXPLICITLY on
every probe that carries ``inputs``, ``()`` where no port is a stream.
"""

import hashlib
import json

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.kinds_banking import BankingReport, Eligibility, EventBank
from dskit.pipeline.kinds_flow import Concat, Derive, Filter, Join
from dskit.pipeline.kinds_report import RunReport
from dskit.pipeline.kinds_search import HpoGrid
from dskit.pipeline.kinds_stats import StatTest, Validate
from dskit.pipeline.kinds_table import TableFile, TableWrite

#: The thirteen toolkit kinds, paired explicitly (see module docstring
#: for why this is not DEFAULT_NODE_KINDS).
TOOLKIT_NODE_KINDS = (
    ("stat_test", StatTest),
    ("validate", Validate),
    ("filter", Filter),
    ("event-bank", EventBank),
    ("eligibility", Eligibility),
    ("banking-report", BankingReport),
    ("hpo-grid", HpoGrid),
    ("run-report", RunReport),
    ("concat", Concat),
    ("join", Join),
    ("derive", Derive),
    ("table-file", TableFile),
    ("table-write", TableWrite),
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
    # The relational three are transforms: they combine rows, they never
    # decide, score or spend.
    "concat": "transform",
    "join": "transform",
    "derive": "transform",
    # The table pair: the reader supplies a value (transform), the
    # writer materialises one and proves it (report).
    "table-file": "transform",
    "table-write": "report",
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


def _book(tmp_path):
    """A digest-pinned table on disk, plus the params that name it — the
    fixture the table pair's probes read and write under ``tmp_path``
    (the one pair whose subject IS a file)."""
    raw = json.dumps({"AAA": 0.07, "BBB": 0.02}).encode("utf-8")
    (tmp_path / "book.json").write_bytes(raw)
    (tmp_path / "produced").mkdir(exist_ok=True)
    return {
        "path": str(tmp_path / "book.json"),
        "source": "https://example.invalid/fee-schedule",
        "retrieved": "2026-01-01",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "expect": 2,
    }


def probes(tmp_path):
    """One NodeProbe per kind. Only the table pair needs ``tmp_path``
    (everything else is in-memory); the factory signature is the suite's
    contract."""
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
        # The joint-document merge, in miniature: two ports of records
        # from two named sources, unioned once, with the identity fields
        # whose namespaces must not collide declared out loud.
        "concat": NodeProbe(
            params={
                "shape": "records",
                "provenance": "source",
                "key": ["instrument", "contract"],
                "allow_overlap": False,
                "allow_empty": False,
            },
            required=("shape",),
            inputs={"alpha": records[:2], "beta": records[2:]},
            stream_ports=("alpha", "beta"),
            runnable=True,
        ),
        # A side table declared in the document rather than wired: the
        # shape a per-series fee schedule takes (I-001 rates are document
        # data, not another node's output).
        "join": NodeProbe(
            params={
                "key": "contract",
                "how": "strict",
                "tables": {"settled_yes": dict(_OUTCOMES)},
            },
            required=("key", "how"),
            inputs={"records": records},
            stream_ports=("records",),
            runnable=True,
        ),
        # Every row must hit a case — the last one is the EXPLICIT
        # catch-all, which is the only default this kind has.
        "derive": NodeProbe(
            params={
                "field": "fee_schedule",
                "cases": [
                    {
                        "when": [{"field": "instrument", "op": "==", "value": "AAA"}],
                        "value": "schedule-a",
                    },
                    {"when": [], "value": "schedule-b"},
                ],
            },
            required=("field", "cases"),
            inputs={"records": records},
            stream_ports=("records",),
            runnable=True,
        ),
        # The provenance-pinned book on disk: params name the file AND its
        # digest, so the probe's fixture is written by the factory itself.
        "table-file": NodeProbe(
            params=_book(tmp_path),
            required=("path", "source", "retrieved", "sha256"),
            inputs={},  # a source-shaped transform: it reads params["path"]
            stream_ports=(),
            runnable=True,
        ),
        # The write half: a materialized mapping in, an atomic file out —
        # the parent directory exists (the factory made it), the target
        # does not, so the no-clobber default holds for the real run.
        "table-write": NodeProbe(
            params={
                "path": str(tmp_path / "produced" / "table.json"),
                "source": "the conformance run's produced table",
                "expect": 3,
            },
            required=("path", "source"),
            inputs={"table": {"AAA": 1, "BBB": 2, "CCC": 3}},
            stream_ports=(),  # the table is a materialized mapping
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

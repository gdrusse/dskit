"""dskit.pipeline — the venue-agnostic pipeline TOOLKIT (pure abstraction).

Owner-directed design (2026-08-13, second pass): this package is the clean
abstraction layer other trading projects build on — and it contains ZERO
venue code. The purity rule is hard and test-enforced
(``tests/pipeline/test_purity.py``): no module here imports anything from
the owning distribution outside this package, and nothing heavy
(numpy/pandas/torch). Venue adapters are CHILD packages outside dskit
that import the toolkit and register themselves (ADR-0021/0032,
``children/README.md``) — the retired ``pipeline_<venue>`` sibling
form must NOT come back, in dskit or beside it; extracting this
toolkit to its own repo someday is a file move.

It uses frozen-dataclass config patterns on purpose (frozen
dataclasses, ``__post_init__`` shape validation, unknown-key rejection,
canonical ``to_obj``/``from_obj``, content-hash identity, the
shape-vs-resolve split).

Layout:

* :mod:`~dskit.pipeline.base` — the config layer: one root
  :class:`PipelineConfig`, strategy slots as tagged families (split
  variants; optimizer ``kind`` + params with a validator registry).
* :mod:`~dskit.pipeline.records` — the venue-neutral
  :class:`MarketRecord` envelope + the accounting split
  (:class:`BinaryAccounting` vs :class:`MarkToMarketAccounting`).
* :mod:`~dskit.pipeline.protocols` — the six seams a backend implements.
* :mod:`~dskit.pipeline.registry` — venue tag -> backend factory;
  adapters self-register on import.
* :mod:`~dskit.pipeline.resolve` — config -> :class:`ResolvedPipeline`
  (fingerprints, hash identity, run-dir writing).
* :mod:`~dskit.pipeline.runner` — the staged Runner
  (train -> validate -> stat_test -> optimize -> backtest) with deploy
  gates.
* :mod:`~dskit.pipeline.metrics` / :mod:`~dskit.pipeline.stats` —
  scoring rules and the cluster-bootstrap/multiplicity machinery.
* :mod:`~dskit.pipeline.testing` — the deterministic synthetic venue
  (reference adapter, demo, and test double in one).

**The node-map grammar (docs/24)** — the successor this package is
migrating to, built spec-first against a synthetic Node set (D-145):

* :mod:`~dskit.pipeline.document` — the node-map document:
  :class:`PipelineDocument` / :class:`NodeSpec`, ``$node.output`` +
  ``$prev`` references, the document split family, clock/schedule.
* :mod:`~dskit.pipeline.node` — the :class:`Node` ABC (D-145 ruling
  1), the kind registry, ``uses`` resolution.
* :mod:`~dskit.pipeline.planner` — IMPORT + PLAN: deterministic topo
  order, role rules as DAG checks, plan.json.
* :mod:`~dskit.pipeline.driver` — the universal execution engine
  (LOAD → IMPORT → PLAN → RESOLVE → EXECUTE → RECORD);
  :func:`run_document` → :class:`DocumentRunResult`.
* :mod:`~dskit.pipeline.runs` — the other direction: reading run
  directories BACK for cross-run comparison; :func:`scan_runs` →
  :class:`RunSummary`, :func:`format_runs`.
* :mod:`~dskit.pipeline.synthetic_nodes` — one deterministic Node per
  role; the set the runner is proven against.

Runnable commands (``python -m dskit.pipeline ...``): ``run <doc>
[--asof]`` / ``walkforward <doc>`` / ``plan <doc>`` / ``validate
<config>`` (dispatches on the document's shape) / ``runs`` (tabulate a
run root) / ``nodemap`` (the banking demo on synthetic nodes);
stage-list: ``demo`` (default) and ``synthetic``.
"""

from dskit.pipeline.base import (
    DEFAULT_SPLIT_POLICY,
    NON_IDENTITY_SECTIONS,
    NULLED_IDENTITY_SECTIONS,
    OPTIMIZER_KINDS,
    SINK_KINDS,
    SPLIT_KINDS,
    SPLIT_POLICIES,
    STAGES,
    TRANSFORM_KINDS,
    ConfigError,
    DataConfig,
    EnvConfig,
    EventBounds,
    FeatureConfig,
    FeatureStepConfig,
    HPOConfig,
    ModelConfig,
    OptimizationConfig,
    OutputsConfig,
    PipelineConfig,
    RandomSplitConfig,
    SinkConfig,
    StatTestConfig,
    TimeSplitConfig,
    TrackingConfig,
    ValidationConfig,
    config_hash,
    event_bounds_from_records,
    import_ref,
    is_class_ref,
    is_ref,
    merge_event_bounds,
    parse_ref,
    parse_stage_entry,
    policy_instant,
    register_optimizer_kind,
    register_sink_kind,
    register_split_policy,
    register_transform_kind,
    resolve_refs,
    split_from_obj,
)
from dskit.pipeline.split_policy import straddle_report
from dskit.pipeline.stats import register_correction
from dskit.pipeline.document import (
    ClockConfig,
    ForeachSpec,
    NodeSpec,
    PipelineDocument,
    RandomSplitSpec,
    ScheduleConfig,
    TrailingSplitSpec,
    WalkForwardSpec,
    load_document,
    save_document,
)
from dskit.pipeline.driver import (
    DocumentRunResult,
    WalkForwardRunResult,
    run_document,
    run_walk_forward,
)
from dskit.pipeline.env import Secrets, load_env
from dskit.pipeline.features import apply_stream_steps
from dskit.pipeline.io import load_config, save_config
from dskit.pipeline.kinds_banking import BankingReport, Eligibility, EventBank
from dskit.pipeline.kinds_banking import register as _register_banking_kinds
from dskit.pipeline.kinds_flow import Concat, Derive, Filter, Join
from dskit.pipeline.kinds_flow import register as _register_flow_kinds
from dskit.pipeline.kinds_report import RunReport
from dskit.pipeline.kinds_report import register as _register_report_kinds
from dskit.pipeline.kinds_search import HpoGrid
from dskit.pipeline.kinds_search import register as _register_search_kinds
from dskit.pipeline.kinds_stats import StatTest, Validate
from dskit.pipeline.kinds_stats import register as _register_stats_kinds
from dskit.pipeline.kinds_table import TableFile
from dskit.pipeline.kinds_table import register as _register_table_kinds
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    NodeContext,
    NodeKindRegistry,
    TrainableNode,
    register_node_kind,
    resolve_uses,
)
from dskit.pipeline.planner import Plan, plan
from dskit.pipeline.protocols import (
    Accounting,
    DataSource,
    ExecutionModel,
    SettlementSource,
    SignalProvider,
    Sizer,
    Tracker,
)
from dskit.pipeline.records import (
    BinaryAccounting,
    MarketRecord,
    MarkToMarketAccounting,
    PositionOutcome,
    settle_position,
)
from dskit.pipeline.registry import DEFAULT_REGISTRY, Backend, BackendRegistry
from dskit.pipeline.resolve import (
    ResolvedPipeline,
    pipeline_hash,
    resolve,
    write_run_dir,
)
from dskit.pipeline.runner import RunContext, Runner, RunResult, StageResult
from dskit.pipeline.runs import (
    RunProblem,
    RunSummary,
    format_runs,
    scan_runs,
)

#: The toolkit-owned kinds claim their names the moment the package
#: imports (idempotent — a re-import never re-registers): stat_test,
#: validate and run-report as OWNED doctrine kinds, plus filter /
#: derive / concat / join / event-bank / eligibility / banking-report /
#: hpo-grid / table-file / table-write.
#:
#: EVERY kinds module's register() must be called here: the flow verbs
#: and the banking chain ship from two modules, and a document naming a
#: kind whose register() was left out stops resolving while every unit
#: test still passes.
_register_stats_kinds()
_register_flow_kinds()
_register_banking_kinds()
_register_search_kinds()
_register_report_kinds()
_register_table_kinds()

__all__ = [
    "BankingReport",
    "ClockConfig",
    "Concat",
    "DEFAULT_NODE_KINDS",
    "DocumentRunResult",
    "Derive",
    "Eligibility",
    "EventBank",
    "Filter",
    "ForeachSpec",
    "HpoGrid",
    "Join",
    "Node",
    "NodeContext",
    "NodeKindRegistry",
    "NodeSpec",
    "PipelineDocument",
    "Plan",
    "RandomSplitSpec",
    "RunReport",
    "ScheduleConfig",
    "StatTest",
    "TableFile",
    "TrailingSplitSpec",
    "TrainableNode",
    "Validate",
    "WalkForwardRunResult",
    "WalkForwardSpec",
    "load_document",
    "plan",
    "register_node_kind",
    "resolve_uses",
    "run_document",
    "run_walk_forward",
    "RunProblem",
    "RunSummary",
    "format_runs",
    "scan_runs",
    "save_document",
    "parse_stage_entry",
    "is_class_ref",
    "import_ref",
    "load_env",
    "Secrets",
    "OutputsConfig",
    "NON_IDENTITY_SECTIONS",
    "NULLED_IDENTITY_SECTIONS",
    "EnvConfig",
    "FeatureConfig",
    "FeatureStepConfig",
    "SINK_KINDS",
    "STAGES",
    "SinkConfig",
    "TRANSFORM_KINDS",
    "Tracker",
    "TrackingConfig",
    "apply_stream_steps",
    "is_ref",
    "parse_ref",
    "register_sink_kind",
    "register_transform_kind",
    "resolve_refs",
    "Accounting",
    "Backend",
    "BackendRegistry",
    "BinaryAccounting",
    "ConfigError",
    "DEFAULT_REGISTRY",
    "DataConfig",
    "DataSource",
    "ExecutionModel",
    "HPOConfig",
    "MarkToMarketAccounting",
    "MarketRecord",
    "ModelConfig",
    "OPTIMIZER_KINDS",
    "OptimizationConfig",
    "PipelineConfig",
    "PositionOutcome",
    "RandomSplitConfig",
    "ResolvedPipeline",
    "RunContext",
    "RunResult",
    "Runner",
    "SPLIT_KINDS",
    "SPLIT_POLICIES",
    "DEFAULT_SPLIT_POLICY",
    "EventBounds",
    "event_bounds_from_records",
    "merge_event_bounds",
    "policy_instant",
    "register_correction",
    "register_split_policy",
    "straddle_report",
    "SettlementSource",
    "SignalProvider",
    "Sizer",
    "StageResult",
    "StatTestConfig",
    "TimeSplitConfig",
    "ValidationConfig",
    "config_hash",
    "load_config",
    "pipeline_hash",
    "register_optimizer_kind",
    "resolve",
    "save_config",
    "settle_position",
    "split_from_obj",
    "write_run_dir",
]

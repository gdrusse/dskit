"""`vocab.py` is the ONE home of every closed set (plan §5.0, §8).

Three things are proved here, and the third is the one that bites:

1. **Shape** — every named vocabulary is a tuple of unique, non-empty
   snake_case tokens (the maps are checked as maps), so a stray list, a
   duplicate, or a `"Live Limited"` fails.
2. **Members** — where the plan states the members, they are pinned
   literally: the ladder order of `RUNGS`, the `allow < … < halt`
   lattice, the eleven statuses and their six terminal ones, the ten
   `Tick` phase names in order, the eight `LegPipeline` step names in
   order, the pinned severity level map, the exit codes.
3. **Completeness** — the §8 list of closed-set names is restated here
   LITERALLY and every name must exist and be exported. §5.16 forbids a
   shipped test from reading the proposal in `docs/`, which would make
   that document a permanent build artifact of the package; deliberate
   independent restatement is what a validation suite owes its subject
   (CLAUDE.md, "Duplication that diverges").

`SERVING_EFFECTS` is deliberately absent: the serving-effect vocabulary
is pipeline-side (§5.3/§9.1), not production's to close.
"""

import ast
import pathlib
import re

import pytest

from dskit.production import vocab

#: §8's `vocab.py` entry, restated literally (§5.16 — a shipped test never
#: reads the plan file), plus the three index maps §5.0 names alongside the
#: tuples (`VERDICT_ORDER`, `SEVERITY_LEVELS`, `EXIT_CODES`).
CLOSED_SET_NAMES = (
    "RUNGS",
    "VERDICTS",
    "VERDICT_ORDER",
    "STATUSES",
    "TERMINAL_STATUSES",
    "TIFS",
    "SIDES",
    "FILL_STATUSES",
    "SEVERITIES",
    "SEVERITY_LEVELS",
    "HEALTH_STATES",
    "BREAKER_STATES",
    "LOOP_STATES",
    "TICK_STATUSES",
    "RECORD_KINDS",
    "BREAK_CLASSES",
    "BREAK_SEVERITIES",
    "DIVERGENCE_CLASSES",
    "MONITOR_STATUSES",
    "RESPONSES",
    "FEED_STATUSES",
    "LINK_STATES",
    "OUTCOME_KINDS",
    "RISK_EFFECTS",
    "OPERATIONS",
    "APPROVAL_PURPOSES",
    "ORDER_EVENTS",
    "CANCEL_OUTCOMES",
    "AUTHORITY_ROLES",
    "COMMAND_STATUSES",
    "LIQUIDITY",
    "POSITION_SOURCES",
    "PROBE_SCOPES",
    "EXIT_CODES",
    "PULL_MODES",
    "ALERT_STATUSES",
    "PLAN_RESULTS",
    "FSYNC_MODES",
    "ROTATE_BY",
    "ON_BREACH",
    "LIMIT_SCOPES",
    "NAN_POLICY",
    "FILL_RULES",
    "RESTING_RULES",
    "SIZE_CAPS",
    "FEE_KIND_NAMES",
    "DEDUPE_MODES",
    "POSITION_MODELS",
    "FENCING_MODES",
    "RESILIENCE_OUTCOMES",
    "RETRY_DECISIONS",
    "JITTER_MODES",
    "RETRY_AFTER_MODES",
    "RETRY_WRITE_MODES",
    "OVERRUN_POLICIES",
    "TICK_PHASES",
    "LEG_STEPS",
    "LEG_LATENCY_BUCKETS",
    "WINDOW_KINDS",
    "CALENDAR_WINDOWS",
    "LEG_ORIGINS",
    "GUARD_STATE_KINDS",
    "PROCESS_EVENTS",
    "ECONOMIC_ATTRS",
    "CASH_FLOW_KINDS",
    "READINESS_VERDICTS",
    "METRIC_NAMES",
    "METRIC_LABEL_VALUES",
    "BREAK_ORIGINS",
    "AT_TIMES_RELATIVE",
    "ON_MISMATCH",
    "RECON_ACTIONS",
    "TRIP_REASONS",
    "CIRCUIT_STATES",
    # Ruled into vocab.py after §8's list was written.
    "MONEY_FIELDS",
    "CACHE_STATES",
)

#: The names whose value is a MAP, not a tuple — checked by their own tests.
MAP_NAMES = ("VERDICT_ORDER", "SEVERITY_LEVELS", "EXIT_CODES", "METRIC_LABEL_VALUES")

#: A vocabulary token: snake_case, lowercase, starting with a letter.
TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")

#: `TICK_STATUSES` is the one vocabulary whose members are qualified
#: (`skipped:stale`, §5.13) — one colon, snake_case either side.
QUALIFIED_TOKEN = re.compile(r"^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)?$")

#: Members the plan states outright. Compared as sets plus length, so a
#: duplicate or a missing member fails while a defensible ordering does not;
#: the vocabularies whose ORDER carries meaning get their own tests below.
EXPECTED_MEMBERS = {
    "RUNGS": ("shadow", "paper", "live_limited", "live"),
    "VERDICTS": ("allow", "warn", "amend", "refuse", "hold", "halt"),
    "TIFS": ("ioc", "fok", "gtc", "gtd", "day"),
    "SIDES": ("buy", "sell", "none"),
    "FILL_STATUSES": ("pending", "final", "reversed"),
    "SEVERITIES": ("info", "warning", "error", "critical"),
    "HEALTH_STATES": ("starting", "ready", "degraded", "unhealthy", "stopping"),
    "BREAKER_STATES": ("active", "reducing", "halted"),
    "FEED_STATUSES": ("live", "degraded", "stale", "dead", "closed"),
    "LINK_STATES": ("connected", "recovering", "disconnected"),
    "OUTCOME_KINDS": ("settled", "marked", "voided", "partial", "corrected"),
    "RISK_EFFECTS": ("increase", "neutral", "reduce"),
    "OPERATIONS": ("submit", "cancel", "query", "reconcile"),
    "APPROVAL_PURPOSES": (
        "arm_request",
        "arm_approval",
        "reduce",
        "flatten_request",
        "flatten_approval",
        "execute_flatten",
        "resume",
        "adopt",
    ),
    "ORDER_EVENTS": (
        "not_sent",
        "ack",
        "reject",
        "fill",
        "partial_fill",
        "cancel",
        "expire",
        "replaced_by_venue",
        "unknown",
        "status",
    ),
    "CANCEL_OUTCOMES": ("none", "submitted", "failed", "partial", "unknown"),
    "AUTHORITY_ROLES": ("ordinary", "reduction"),
    "COMMAND_STATUSES": ("applied", "rejected"),
    "LIQUIDITY": ("maker", "taker", "unknown"),
    "POSITION_SOURCES": ("derived", "venue"),
    "PROBE_SCOPES": ("local", "dependency"),
    "PULL_MODES": ("acquire", "store"),
    "ALERT_STATUSES": ("firing", "resolved"),
    "PLAN_RESULTS": ("submit", "not_sent"),
    "ROTATE_BY": ("size", "day", "process"),
    "ON_BREACH": ("refuse", "amend", "pause", "hold", "halt"),
    "NAN_POLICY": ("refuse", "allow"),
    "FILL_RULES": ("touch", "cross", "mid"),
    "RESTING_RULES": ("touch", "through"),
    "SIZE_CAPS": ("none", "quote_size", "frac"),
    "DEDUPE_MODES": ("replays", "rejects", "window", "none"),
    "POSITION_MODELS": ("netting", "hedging"),
    "FENCING_MODES": ("none", "submit_token"),
    "RESILIENCE_OUTCOMES": ("ok", "transient", "throttled", "fatal", "ambiguous"),
    "RETRY_DECISIONS": ("retry", "give_up", "reconcile"),
    "JITTER_MODES": ("full", "equal", "none"),
    "RETRY_AFTER_MODES": ("honor", "ignore"),
    "RETRY_WRITE_MODES": ("never", "idempotent_only"),
    "OVERRUN_POLICIES": ("skip", "coalesce", "queue"),
    "CIRCUIT_STATES": ("closed", "open", "half_open", "forced_open", "metrics_only"),
    "MONITOR_STATUSES": ("ok", "warn", "alarm", "insufficient"),
    "RESPONSES": ("log", "warn", "halt"),
    "BREAK_CLASSES": (
        "timing",
        "missing_in_ledger",
        "missing_at_venue",
        "quantity",
        "price",
        "fee",
        "state",
        "settlement",
        "cash",
    ),
    "BREAK_SEVERITIES": ("info", "warn", "block"),
    "BREAK_ORIGINS": ("ours", "external"),
    "ON_MISMATCH": ("halt", "refuse"),
    "DIVERGENCE_CLASSES": (
        "data",
        "nondeterminism",
        "version",
        "guard",
        "state",
        "execution",
    ),
    "WINDOW_KINDS": ("none", "duration", "count", "calendar"),
    "CALENDAR_WINDOWS": ("session", "day", "event"),
    "AT_TIMES_RELATIVE": ("open", "close", "clock"),
    "LEG_ORIGINS": ("model", "reduction"),
    "GUARD_STATE_KINDS": ("hold", "pause"),
    "PROCESS_EVENTS": ("start", "stop", "recovered"),
    "ECONOMIC_ATTRS": ("positions", "working", "balances"),
    "CASH_FLOW_KINDS": ("deposit", "withdrawal", "adjustment"),
    "READINESS_VERDICTS": ("go", "no_go"),
    "MONEY_FIELDS": (
        "qty",
        "notional",
        "limit",
        "price",
        "fee",
        "avg_price",
        "filled_qty",
        "remaining_qty",
        "amount",
        "total",
        "available",
        "payout",
        "avg_cost",
        "reference_price",
        "exposure",
        "nav",
        "bid",
        "ask",
        "mid",
    ),
    "CACHE_STATES": ("current", "stale"),
    "TICK_STATUSES": (
        "decided",
        "skipped:closed",
        "skipped:stale",
        "skipped:skew",
        "skipped:halted",
        "skipped:degraded",
        "skipped:no_coverage",
        "refused",
        "failed",
    ),
}

#: §5.11.1's phase-1 declared table: metric name -> its label NAMES.
EXPECTED_METRIC_LABELS = {
    "ticks_total": ("status",),
    "tick_seconds": ("phase",),
    "decisions_total": ("result",),
    "proposals_total": ("verdict",),
    "submits_total": ("rung", "risk_effect", "outcome"),
    "refusals_total": ("reason",),
    "alert_sink_failures_total": ("sink",),
    "alerts_suppressed_total": ("why",),
    "monitor_verdicts_total": ("monitor", "status"),
    "recon_breaks_total": ("class",),
    "ledger_append_seconds": (),
    "metrics_label_cardinality_dropped_total": (),
}


def _tuples():
    """Every vocabulary in `__all__` whose value is a tuple."""
    return {
        name: getattr(vocab, name)
        for name in vocab.__all__
        if isinstance(getattr(vocab, name), tuple)
    }


# ---------------------------------------------------------------------------
# Completeness and encapsulation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CLOSED_SET_NAMES)
def test_every_closed_set_the_plan_names_is_defined_and_exported(name):
    assert hasattr(vocab, name), (
        f"§8 names {name} as a closed set living in vocab.py; it is missing"
    )
    assert name in vocab.__all__, (
        f"{name} is defined but not in vocab.__all__ — `__all__` IS the API "
        "contract (CLAUDE.md)"
    )


def test_all_is_the_whole_surface_and_leaks_no_private_name():
    assert vocab.__all__, "vocab.py must declare __all__"
    assert sorted(vocab.__all__) == sorted(set(vocab.__all__)), "duplicate in __all__"
    assert not [n for n in vocab.__all__ if n.startswith("_")]
    missing = [n for n in vocab.__all__ if not hasattr(vocab, n)]
    assert not missing, f"__all__ names nothing: {missing}"


def test_serving_effects_is_not_a_production_vocabulary():
    """The serving-effect set (`pure | entry_read | release_read |
    forbidden`) is the pipeline's closed producer API (§5.3, §9.1). A copy
    here would be the second copy that diverges."""
    assert not hasattr(vocab, "SERVING_EFFECTS")
    assert "SERVING_EFFECTS" not in vocab.__all__


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_every_vocabulary_is_a_tuple_of_unique_non_empty_snake_case_tokens():
    problems = []
    for name, value in _tuples().items():
        pattern = QUALIFIED_TOKEN if name == "TICK_STATUSES" else TOKEN
        if not value:
            problems.append(f"{name}: empty")
            continue
        if len(set(value)) != len(value):
            problems.append(f"{name}: duplicate member")
        for member in value:
            if not isinstance(member, str) or not pattern.match(member):
                problems.append(f"{name}: bad member {member!r}")
    assert not problems, problems


@pytest.mark.parametrize("name", sorted(EXPECTED_MEMBERS))
def test_members_match_the_plan(name):
    expected = EXPECTED_MEMBERS[name]
    actual = getattr(vocab, name)
    assert isinstance(actual, tuple), f"{name} must be a tuple, got {type(actual)}"
    assert len(actual) == len(expected), f"{name}: {actual} != {expected}"
    assert set(actual) == set(expected), f"{name}: {actual} != {expected}"


@pytest.mark.parametrize("name", MAP_NAMES)
def test_the_index_maps_are_mappings_not_tuples(name):
    assert isinstance(getattr(vocab, name), dict)


# ---------------------------------------------------------------------------
# The vocabularies whose ORDER is the content
# ---------------------------------------------------------------------------


def test_rungs_are_exactly_four_in_ladder_order():
    """§5.0: backtest is NOT a fifth rung — it is a replay configuration
    of shadow (recorded clock, feed, executor)."""
    assert vocab.RUNGS == ("shadow", "paper", "live_limited", "live")
    assert "backtest" not in vocab.RUNGS


def test_verdict_order_is_the_allow_to_halt_lattice():
    assert vocab.VERDICTS == ("allow", "warn", "amend", "refuse", "hold", "halt")
    assert vocab.VERDICT_ORDER == {
        "allow": 0,
        "warn": 1,
        "amend": 2,
        "refuse": 3,
        "hold": 4,
        "halt": 5,
    }
    assert list(vocab.VERDICT_ORDER) == list(vocab.VERDICTS)
    strictest = max(("warn", "halt", "allow"), key=vocab.VERDICT_ORDER.__getitem__)
    assert strictest == "halt"


def test_statuses_are_eleven_and_terminal_is_the_six_member_subset():
    assert vocab.STATUSES == (
        "pending",
        "open",
        "partial",
        "pending_cancel",
        "filled",
        "cancelled",
        "expired",
        "rejected",
        "replaced",
        "unknown",
        "not_sent",
    )
    assert vocab.TERMINAL_STATUSES == (
        "filled",
        "cancelled",
        "expired",
        "rejected",
        "replaced",
        "not_sent",
    )
    assert set(vocab.TERMINAL_STATUSES) < set(vocab.STATUSES)
    assert "unknown" not in vocab.TERMINAL_STATUSES, (
        "a venue lacking a state collapses toward LESS certainty (§5.4): "
        "`unknown` is not terminal"
    )


def test_tick_phases_are_the_ten_tick_methods_in_order():
    assert vocab.TICK_PHASES == (
        "gate",
        "verify_release",
        "fetch",
        "read_entry",
        "coverage",
        "evaluate",
        "candidates",
        "quotes",
        "account",
        "propose",
    )


def test_leg_steps_are_the_eight_pipeline_methods_in_order():
    assert vocab.LEG_STEPS == (
        "guard",
        "refresh",
        "rebind",
        "plan",
        "intent",
        "authorize",
        "act",
        "fold",
    )


def test_leg_latency_buckets_are_the_three_spans_not_the_step_names():
    """§5.13.1: the two tuples are separate because three step names and
    three bucket names collide while meaning different spans —
    `guard` covers steps (1)-(3), `authorize` (4)-(6), `act` (7)."""
    assert vocab.LEG_LATENCY_BUCKETS == ("guard", "authorize", "act")
    assert vocab.LEG_LATENCY_BUCKETS != vocab.LEG_STEPS
    assert set(vocab.LEG_LATENCY_BUCKETS) < set(vocab.LEG_STEPS)


def test_loop_states_carry_the_lifecycle_plus_halted_and_faulted():
    """§5.13: `init → locked → leased → reconciling → ready → {waiting ⇄
    ticking} → stopping → stopped`, plus persisted `halted` and
    restartable `faulted`."""
    assert set(vocab.LOOP_STATES) == {
        "init",
        "locked",
        "leased",
        "reconciling",
        "ready",
        "waiting",
        "ticking",
        "stopping",
        "stopped",
        "halted",
        "faulted",
    }
    assert vocab.LOOP_STATES[0] == "init"


def test_record_kinds_are_the_twenty_five_ledger_kinds_of_the_record_table():
    assert set(vocab.RECORD_KINDS) == {
        "process",
        "tick_start",
        "tick",
        "decision",
        "decision_plan",
        "intent",
        "authorization",
        "control_request",
        "control_approval",
        "authority",
        "authority_use",
        "order_event",
        "fill",
        "cash_flow",
        "outcome",
        "guard_state",
        "readiness",
        "recon",
        "trip",
        "adoption",
        "command_result",
        "monitor",
        "alert",
        "health",
        "snapshot",
    }


# ---------------------------------------------------------------------------
# The maps
# ---------------------------------------------------------------------------


def test_severity_levels_pin_otel_syslog_and_logging():
    """§5.0/§5.11: `SEVERITIES` is PagerDuty's own vocabulary, and the
    level map pins OTel `SeverityNumber` (9/13/17/21), syslog (6/4/3/2)
    and `logging` (20/30/40/50) so an operator's pager, collector and log
    file agree on what `critical` means."""
    assert tuple(vocab.SEVERITY_LEVELS) == vocab.SEVERITIES
    assert {s: vocab.SEVERITY_LEVELS[s]["otel"] for s in vocab.SEVERITIES} == {
        "info": 9,
        "warning": 13,
        "error": 17,
        "critical": 21,
    }
    assert {s: vocab.SEVERITY_LEVELS[s]["syslog"] for s in vocab.SEVERITIES} == {
        "info": 6,
        "warning": 4,
        "error": 3,
        "critical": 2,
    }
    assert {s: vocab.SEVERITY_LEVELS[s]["logging"] for s in vocab.SEVERITIES} == {
        "info": 20,
        "warning": 30,
        "error": 40,
        "critical": 50,
    }


def test_exit_codes_keep_halted_and_refused_apart():
    """§5.13: the root convention gives 3 three meanings at once. This
    package keeps 3 for HALTED (operator action needed), takes 5 for a
    readiness NO-GO or a refused control verb, and 4 for already-running."""
    assert vocab.EXIT_CODES == {
        "stopped": 0,
        "error": 1,
        "halted": 3,
        "already_running": 4,
        "refused": 5,
    }
    assert vocab.EXIT_CODES["halted"] != vocab.EXIT_CODES["refused"]


def test_metric_names_and_label_values_carry_the_phase_one_table():
    assert set(vocab.METRIC_NAMES) == set(EXPECTED_METRIC_LABELS)
    assert set(vocab.METRIC_LABEL_VALUES) == set(vocab.METRIC_NAMES), (
        "every declared metric needs a label entry (an empty mapping for an "
        "unlabelled one) — §5.11.1 closes label NAMES at declaration"
    )
    for name, labels in EXPECTED_METRIC_LABELS.items():
        assert set(vocab.METRIC_LABEL_VALUES[name]) == set(labels), name


def test_metric_label_values_are_closed_sets_of_tokens():
    problems = []
    for metric, labels in vocab.METRIC_LABEL_VALUES.items():
        for label, values in labels.items():
            if not isinstance(values, tuple) or not values:
                problems.append(f"{metric}.{label}: not a non-empty tuple")
                continue
            for value in values:
                if not isinstance(value, str) or not QUALIFIED_TOKEN.match(value):
                    problems.append(f"{metric}.{label}: bad value {value!r}")
    assert not problems, problems


def test_metric_label_values_reuse_the_vocabularies_they_name():
    """A label set is a closed vocabulary like any other: restating one
    inline is the second copy that diverges (CLAUDE.md)."""
    labels = vocab.METRIC_LABEL_VALUES
    assert set(labels["ticks_total"]["status"]) == set(vocab.TICK_STATUSES)
    assert set(labels["tick_seconds"]["phase"]) == set(vocab.TICK_PHASES)
    assert set(labels["decisions_total"]["result"]) == set(vocab.PLAN_RESULTS)
    assert set(labels["proposals_total"]["verdict"]) == set(vocab.VERDICTS)
    assert set(labels["submits_total"]["rung"]) == set(vocab.RUNGS)
    assert set(labels["submits_total"]["risk_effect"]) == set(vocab.RISK_EFFECTS)
    assert set(labels["monitor_verdicts_total"]["status"]) == set(
        vocab.MONITOR_STATUSES
    )
    assert set(labels["recon_breaks_total"]["class"]) == set(vocab.BREAK_CLASSES)
    assert set(labels["refusals_total"]["reason"]) <= set(vocab.TICK_STATUSES) | set(
        vocab.VERDICTS
    ), "§5.11.1: refusal reasons are TICK_STATUSES members plus guard verdicts"


def test_metric_names_are_prometheus_shaped():
    """§5.11.1: base units in the suffix, no units elsewhere."""
    for name in vocab.METRIC_NAMES:
        assert TOKEN.match(name), name
        assert not name.endswith("_ms"), f"{name}: seconds are the base unit"
        assert "_seconds_" not in name and "_total_" not in name, name


# ---------------------------------------------------------------------------
# The module itself
# ---------------------------------------------------------------------------


def _vocab_tree():
    return ast.parse(
        pathlib.Path(vocab.__file__).read_text(encoding="utf-8"), type_comments=False
    )


def test_vocab_imports_nothing_but_stdlib():
    """§5.0: `vocab.py` has no imports beyond stdlib — every other module
    imports IT, so an import here would invert the dependency."""
    import sys

    offenders = []
    for node in ast.walk(_vocab_tree()):
        if isinstance(node, ast.Import):
            offenders += [
                a.name for a in node.names if a.name.split(".")[0] not in
                sys.stdlib_module_names
            ]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in sys.stdlib_module_names:
                offenders.append(node.module or f"level-{node.level} relative")
    assert not offenders, f"vocab.py imports beyond stdlib: {offenders}"


def test_vocab_defines_no_function_and_no_class():
    """§5.0: no logic beyond building the index maps it names — a
    vocabulary that can COMPUTE is a vocabulary that can disagree."""
    offenders = [
        node.name
        for node in ast.walk(_vocab_tree())
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        )
    ]
    assert not offenders, f"vocab.py must hold data only, found: {offenders}"

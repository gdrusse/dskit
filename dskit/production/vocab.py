"""Every closed vocabulary of ``dskit.production``, in one module.

A closed set that lives in two places is a closed set that can disagree,
so this module is the ONLY home of every vocabulary the package closes
(plan §5.0, §8): every other module imports these tuples, and
``tests/production/test_vocab.py`` fails a closed set defined anywhere
else. The module holds data only — no functions, no classes, no imports
beyond the standard library — because a vocabulary that can compute is a
vocabulary that can diverge.

Three kinds of value live here:

* **Tuples** — the members of a closed set, in the order the plan gives
  them where that order carries meaning (``RUNGS`` is the ladder,
  ``VERDICTS`` the lattice, ``TICK_PHASES`` and ``LEG_STEPS`` the walk a
  ``Tick`` and a ``LegPipeline`` take, ``STATUSES`` the life of an order).
* **Index maps** built from those tuples — ``VERDICT_ORDER`` (the
  ``allow < … < halt`` rank), ``SEVERITY_LEVELS`` (the pinned PagerDuty /
  OTel / syslog / ``logging`` levels) and ``EXIT_CODES``.
* **The metrics tables** ``METRIC_NAMES`` and ``METRIC_LABEL_VALUES``
  (§5.11.1), which reuse the vocabularies above rather than restate them;
  ``metrics.py`` reads them and declares nothing of its own.

``TICK_STATUSES`` is the one vocabulary whose members are colon-qualified
(``skipped:stale``, §5.13); every other member is a snake_case token.
"""

__all__ = [
    "ALERT_STATUSES",
    "ALERT_SUPPRESSIONS",
    "APPROVAL_PURPOSES",
    "AT_TIMES_RELATIVE",
    "AUTHORITY_EVENTS",
    "AUTHORITY_ROLES",
    "BREAKER_STATES",
    "BREAK_CLASSES",
    "BREAK_ORIGINS",
    "BREAK_SEVERITIES",
    "CACHE_STATES",
    "CALENDAR_WINDOWS",
    "CANCEL_OUTCOMES",
    "CONTROL_PURPOSES",
    "CASH_FLOW_KINDS",
    "CIRCUIT_STATES",
    "COMMAND_STATUSES",
    "DEDUPE_MODES",
    "DIVERGENCE_CLASSES",
    "ECONOMIC_ATTRS",
    "ESCALATION_LEVELS",
    "EXIT_CODES",
    "FEED_STATUSES",
    "FEE_KIND_NAMES",
    "FENCING_MODES",
    "FILL_RULES",
    "FILL_STATUSES",
    "FSYNC_MODES",
    "GUARD_STATE_KINDS",
    "HEALTH_STATES",
    "JITTER_MODES",
    "LEG_LATENCY_BUCKETS",
    "LEG_ORIGINS",
    "LEG_STEPS",
    "LIMIT_SCOPES",
    "LINK_STATES",
    "LIQUIDITY",
    "LOOP_STATES",
    "METRIC_LABEL_VALUES",
    "METRIC_NAMES",
    "MONEY_FIELDS",
    "MONITOR_STATUSES",
    "NAN_POLICY",
    "ON_BREACH",
    "ON_MISMATCH",
    "OPERATIONS",
    "ORDER_EVENTS",
    "OUTCOME_KINDS",
    "OUTCOME_SOURCES",
    "OVERRUN_POLICIES",
    "PLAN_RESULTS",
    "POSITION_MODELS",
    "POSITION_SOURCES",
    "PROBE_SCOPES",
    "PROCESS_EVENTS",
    "PULL_MODES",
    "READINESS_EVIDENCE",
    "READINESS_VERDICTS",
    "RECON_ACTIONS",
    "RECORD_KINDS",
    "RESILIENCE_OUTCOMES",
    "RESPONSES",
    "RESTING_RULES",
    "RETRY_AFTER_MODES",
    "RETRY_DECISIONS",
    "RETRY_WRITE_MODES",
    "RISK_EFFECTS",
    "ROTATE_BY",
    "RUNGS",
    "SEVERITIES",
    "SEVERITY_LEVELS",
    "SIDES",
    "SIGNER_ALGORITHMS",
    "SILENCE_STATES",
    "SIZE_CAPS",
    "STATUSES",
    "TERMINAL_STATUSES",
    "TICK_PHASES",
    "TICK_STATUSES",
    "TIFS",
    "TRANSITION_CAUSES",
    "TRIP_REASONS",
    "UNWAIVABLE_ITEMS",
    "VERDICTS",
    "VERDICT_ORDER",
    "WINDOW_KINDS",
]

# ---------------------------------------------------------------------------
# The ladder, the lattice, the order life cycle (§5.0, §5.4, §5.5)
# ---------------------------------------------------------------------------

#: Exactly four, and the order IS the ladder. Backtest is not a rung: it is
#: a replay configuration of ``shadow`` (recorded clock, feed and executor).
RUNGS = ("shadow", "paper", "live_limited", "live")

#: The guard lattice, weakest first; a composite verdict is the strictest.
VERDICTS = ("allow", "warn", "amend", "refuse", "hold", "halt")

#: ``allow < warn < amend < refuse < hold < halt`` as a rank map.
VERDICT_ORDER = {verdict: rank for rank, verdict in enumerate(VERDICTS)}

#: Eleven order statuses; a venue lacking a state collapses toward LESS
#: certainty (``unknown``), never toward more.
STATUSES = (
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

#: The six statuses an order never leaves. ``unknown`` is deliberately not
#: one of them — it is the absence of certainty, not an end.
TERMINAL_STATUSES = ("filled", "cancelled", "expired", "rejected", "replaced", "not_sent")

#: Time-in-force spellings a proposal may carry.
TIFS = ("ioc", "fok", "gtc", "gtd", "day")

#: ``none`` is the abstaining side of a no-op proposal.
SIDES = ("buy", "sell", "none")

#: A fill is pending until the venue finalises it; ``reversed`` undoes it.
FILL_STATUSES = ("pending", "final", "reversed")

# ---------------------------------------------------------------------------
# Alerting, health, breaker, loop (§5.6, §5.11, §5.13)
# ---------------------------------------------------------------------------

#: PagerDuty's own severity vocabulary (§5.11).
SEVERITIES = ("info", "warning", "error", "critical")

#: The pinned level map: what each severity means to PagerDuty, an OTel
#: collector (``SeverityNumber``), syslog and the ``logging`` module, so an
#: operator's pager, collector and log file agree on what ``critical`` is.
SEVERITY_LEVELS = {
    "info": {"pagerduty": "info", "otel": 9, "syslog": 6, "logging": 20},
    "warning": {"pagerduty": "warning", "otel": 13, "syslog": 4, "logging": 30},
    "error": {"pagerduty": "error", "otel": 17, "syslog": 3, "logging": 40},
    "critical": {"pagerduty": "critical", "otel": 21, "syslog": 2, "logging": 50},
}

#: ``starting → {ready | degraded | unhealthy} → stopping``.
HEALTH_STATES = ("starting", "ready", "degraded", "unhealthy", "stopping")

#: The breaker's three states; ``reducing`` admits only risk-reducing legs.
BREAKER_STATES = ("active", "reducing", "halted")

#: ``init → locked → leased → reconciling → ready → {waiting ⇄ ticking} →
#: stopping → stopped``, plus persisted ``halted`` and restartable ``faulted``.
LOOP_STATES = (
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
)

#: The ticks that ended without a decision, by the gate that refused them.
_SKIPPED = (
    "skipped:closed",
    "skipped:stale",
    "skipped:skew",
    "skipped:halted",
    "skipped:degraded",
    "skipped:no_coverage",
)

#: Every started tick ends in exactly one of these (§5.13). The only
#: colon-qualified vocabulary in the package.
TICK_STATUSES = ("decided",) + _SKIPPED + ("refused", "failed")

#: What tripped the breaker (§5.6, plus §5.10's monitor ``alarm`` with
#: ``response: halt``), spelled ``<source>_<condition>``.
TRIP_REASONS = (
    "guard_halt",
    "feed_dead",
    "executor_link_lost",
    "reconcile_mismatch",
    "monitor_alarm",
    "operator",
)

#: The breaker transition causes ``TransitionPolicy`` judges (§5.14).
TRANSITION_CAUSES = ("reduce", "flatten_request", "trip", "halt", "resume")

#: A resilience circuit's states — distinct from ``BREAKER_STATES``, which
#: is the SERIES breaker; this is one network scope's.
CIRCUIT_STATES = ("closed", "open", "half_open", "forced_open", "metrics_only")

# ---------------------------------------------------------------------------
# Ledger record kinds and their closed fields (§6)
# ---------------------------------------------------------------------------

#: §6's twenty-five record kinds in table order, plus ``cancel_outcome``
#: (ruling R6: a halting ``trip`` is barriered BEFORE the cancel I/O, so
#: what the cancel came to is a record of its own) and phase 2's ``silence``
#: and ``alert_ack`` (§5.11.2: alert state rides in the fold, in whatever
#: ledger the document declared, never in an alert database beside it that
#: could disagree with the chain after a crash) — twenty-eight.
RECORD_KINDS = (
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
    "cancel_outcome",
    "adoption",
    "command_result",
    "monitor",
    "alert",
    "silence",
    "alert_ack",
    "health",
    "snapshot",
)

#: The ``process`` record's ``event``.
PROCESS_EVENTS = ("start", "stop", "recovered")

#: The ``authority`` record's ``event``: issue / disarm / revoke / expire.
AUTHORITY_EVENTS = ("issue", "disarm", "revoke", "expire")

#: The ``authority`` record's ``kind`` — an ordinary arm or a reduction right.
AUTHORITY_ROLES = ("ordinary", "reduction")

#: The ``order_event`` record's ``event``. ``replaced_by_venue`` is observed
#: only (D10): no executor verb initiates it.
ORDER_EVENTS = (
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
)

#: What cancelling working orders on a halt came to.
CANCEL_OUTCOMES = ("none", "submitted", "failed", "partial", "unknown")

#: The ``outcome`` record's ``kind`` — label arrival, mark or correction.
OUTCOME_KINDS = ("settled", "marked", "voided", "partial", "corrected")

#: Where an ``outcome`` came from (§5.13.2): the venue's own settlement,
#: a derived label stream, or an operator who recorded it by hand. It is
#: NOT the ``outcome_source`` registry's key set — the registry is the open
#: doorway of the classes that PRODUCE outcomes, and ``operator`` has no
#: class, because a hand-recorded outcome polls nothing.
OUTCOME_SOURCES = ("settlement", "label", "operator")

#: The ``cash_flow`` record's ``kind`` — the only kinds ``adopt`` can emit.
CASH_FLOW_KINDS = ("deposit", "withdrawal", "adjustment")

#: The ``guard_state`` record's ``kind``. ``released`` is phase 2's
#: (§5.5.1): ``approve-hold`` ends one hold before its ttl, and the fold
#: DROPS the pair it names. It is a record rather than a deletion because
#: an operator overriding a safety verdict is exactly the act the chain
#: has to keep.
GUARD_STATE_KINDS = ("hold", "pause", "released")

#: The ``readiness`` record's ``verdict``.
READINESS_VERDICTS = ("go", "no_go")

#: §5.13's six foundations — release/runtime verification, executor
#: conformance, authenticated execution-scope equality, a clean startup
#: reconciliation, fenced lease capability and the required safety
#: controls. A checklist that omits one, marks one optional, or waives one
#: refuses: they are what the rest of the ladder stands on.
UNWAIVABLE_ITEMS = (
    "release_verified",
    "executor_conformant",
    "scope_authenticated",
    "startup_reconciled",
    "lease_fenced",
    "safety_controls",
)

#: [phase 2] §5.13.4's third kind of checklist evidence: the names the
#: SERIES itself can prove, from the ``outcome`` fold of §5.13.2 and the
#: outcome monitors of §5.10.1. They are ORDINARY WAIVABLE items — a
#: shadow series has no outcomes and a new release starts with none — so
#: they are deliberately not in :data:`UNWAIVABLE_ITEMS`; what they add is
#: that a live series can be REQUIRED by its own checklist to prove its
#: decisions have been scored. ``readiness.py`` keys its rule table by
#: these names, so a name here with no rule (or a rule with no name)
#: refuses at import.
READINESS_EVIDENCE = ("outcome_coverage", "outcome_freshness", "calibration_current")

#: The ``command_result`` record's ``status``.
COMMAND_STATUSES = ("applied", "rejected")

#: The ``decision_plan`` record's ``result``.
PLAN_RESULTS = ("submit", "not_sent")

#: What a leg does to risk; ``Accounting.classify`` returns exactly one.
RISK_EFFECTS = ("increase", "neutral", "reduce")

#: The verbs the action policy vetoes or allows.
OPERATIONS = ("submit", "cancel", "query", "reconcile")

#: Which pipeline built a leg — the model's, or a signed reduction.
LEG_ORIGINS = ("model", "reduction")

#: The purposes an ``ApprovalVerifier`` may be asked to verify (§5.6).
APPROVAL_PURPOSES = (
    "arm_request",
    "arm_approval",
    "reduce",
    "flatten_request",
    "flatten_approval",
    "execute_flatten",
    "resume",
    "adopt",
    # Phase 2 (§5.11.2): a page an unauthenticated caller could suppress is
    # an outage with no evidence, so acknowledging one and silencing one are
    # proof-carrying acts like every other member here.
    "ack",
    "silence",
    # Phase 2 (§5.5.1): a hold is a guard's refusal to keep acting on a
    # scope, so ending one early is an operator overriding a safety
    # verdict — the class of act D11 requires a verifier for.
    "approve_hold",
)

#: What the control spool carries (§5.8): the ten authenticated
#: ``APPROVAL_PURPOSES`` plus the four §7 verbs that queue without a
#: maker-checker proof. One home, so the inbox and every handler agree
#: on what a command may ask for.
CONTROL_PURPOSES = APPROVAL_PURPOSES + (
    "halt",
    "disarm",
    "reconcile",
    "ready",
    # §5.13.2: the ``outcomes`` verb MUTATES — it appends ``outcome``
    # records — so it takes the same one path, with no proof: it records
    # observed facts rather than an operator's claim.
    "outcomes",
)

#: The ``alert`` record's ``status``.
ALERT_STATUSES = ("firing", "resolved")

#: What a checkpoint or cache is, validated against the ledger fold.
CACHE_STATES = ("current", "stale")

# ---------------------------------------------------------------------------
# Execution and accounting (§5.7, §5.7.1, §5.9)
# ---------------------------------------------------------------------------

#: A fill's liquidity flag.
LIQUIDITY = ("maker", "taker", "unknown")

#: Where a position came from: our fill fold, or the venue's own report.
POSITION_SOURCES = ("derived", "venue")

#: How an executor nets positions.
POSITION_MODELS = ("netting", "hedging")

#: How a venue treats a re-used client reference (§5.7 capability).
DEDUPE_MODES = ("replays", "rejects", "window", "none")

#: Whether the lease fence rides on submits.
FENCING_MODES = ("none", "submit_token")

#: The executor's link to its venue.
LINK_STATES = ("connected", "recovering", "disconnected")

#: Paper fill and resting rules, size caps, fee strategies (§5.7).
FILL_RULES = ("touch", "cross", "mid")
RESTING_RULES = ("touch", "through")
SIZE_CAPS = ("none", "quote_size", "frac")
#: The five fee strategies; ``FEE_KINDS`` (executor.py) registers exactly
#: these names, and a test pins the two key sets equal (§4.3).
FEE_KIND_NAMES = ("none", "per_unit", "bps", "maker_taker_bps", "pxq_rate")

#: What the reconciler found, and how bad (§5.9).
BREAK_CLASSES = (
    "timing",
    "missing_in_ledger",
    "missing_at_venue",
    "quantity",
    "price",
    "fee",
    "state",
    "settlement",
    "cash",
)
BREAK_SEVERITIES = ("info", "warn", "block")
#: An unknown venue order is ``external``, never silently made ours.
BREAK_ORIGINS = ("ours", "external")
#: The automatic mismatch policy admits only these.
ON_MISMATCH = ("halt", "refuse")
#: The ``recon`` record's ``action``: what the run did about its breaks —
#: nothing (clean or informational), refuse submits, or trip the breaker.
RECON_ACTIONS = ("none", "refuse", "halt")

#: The economic attributes a recon compares and a snapshot restores.
ECONOMIC_ATTRS = ("positions", "working", "balances")

# ---------------------------------------------------------------------------
# Guards, monitors, feed (§5.5, §5.10, §5.2)
# ---------------------------------------------------------------------------

#: A ``Limit``'s window family, scope, breach response and NaN policy.
WINDOW_KINDS = ("none", "duration", "count", "calendar")
CALENDAR_WINDOWS = ("session", "day", "event")
LIMIT_SCOPES = ("aggregate", "per_key", "group")
ON_BREACH = ("refuse", "amend", "pause", "hold", "halt")
NAN_POLICY = ("refuse", "allow")

#: A monitor's verdict and its configured response.
MONITOR_STATUSES = ("ok", "warn", "alarm", "insufficient")
RESPONSES = ("log", "warn", "halt")

#: The feed's health as the loop sees it.
FEED_STATUSES = ("live", "degraded", "stale", "dead", "closed")

#: How the feed pulls — through onboarding acquisition, or a store read.
PULL_MODES = ("acquire", "store")

# ---------------------------------------------------------------------------
# Schedule and loop mechanics (§5.1, §5.13, §5.13.1)
# ---------------------------------------------------------------------------

#: ``AtTimes`` anchors: relative to the session open, close, or the clock.
AT_TIMES_RELATIVE = ("open", "close", "clock")

#: What the loop does with ticks it could not run on time.
OVERRUN_POLICIES = ("skip", "coalesce", "queue")

#: The ten ``Tick`` phase methods, in the order ``run`` walks them.
TICK_PHASES = (
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

#: The eight ``LegPipeline`` step methods, in the order ``run`` walks them.
LEG_STEPS = ("guard", "refresh", "rebind", "plan", "intent", "authorize", "act", "fold")

#: The three §6 latency spans over ``LEG_STEPS`` — ``guard`` covers steps
#: (1)–(3), ``authorize`` (4)–(6), ``act`` (7). Spans, not step names, which
#: is why this is a separate vocabulary from ``LEG_STEPS``.
LEG_LATENCY_BUCKETS = ("guard", "authorize", "act")

# ---------------------------------------------------------------------------
# Durability, resilience, health probes (§5.8, §5.12, §5.11)
# ---------------------------------------------------------------------------

#: ``document.durability.fsync``; ``none`` is legal only at ``shadow``.
FSYNC_MODES = ("every", "batch", "none")

#: ``placement.rotate.by``.
ROTATE_BY = ("size", "day", "process")

#: A transport outcome's class — distinct from ``OUTCOME_KINDS`` (labels).
RESILIENCE_OUTCOMES = ("ok", "transient", "throttled", "fatal", "ambiguous")
RETRY_DECISIONS = ("retry", "give_up", "reconcile")
JITTER_MODES = ("full", "equal", "none")
RETRY_AFTER_MODES = ("honor", "ignore")
RETRY_WRITE_MODES = ("never", "idempotent_only")

#: The hash a ``Signer`` may compute its HMAC with (§5.12.1). Two members
#: and no more: an algorithm is a venue's requirement, not a preference,
#: and the ones a venue asks for are these. A weaker digest would be a knob
#: whose only effect is to make a signature easier to forge.
SIGNER_ALGORITHMS = ("sha256", "sha512")

#: A health probe's scope.
PROBE_SCOPES = ("local", "dependency")

#: Why a replay diverged from the tape (§5.13's report).
DIVERGENCE_CLASSES = ("data", "nondeterminism", "version", "guard", "state", "execution")

# ---------------------------------------------------------------------------
# Money, exit codes, metrics
# ---------------------------------------------------------------------------

#: The field names under which a float is never legal, at any depth of a
#: record (ratios such as ``confidence`` are floats and are not named here).
MONEY_FIELDS = (
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
)

#: 3 keeps HALTED (operator action needed), 5 takes a readiness NO-GO or a
#: refused control verb, 4 is already-running — the root convention's three
#: meanings of 3 kept apart (§5.13).
EXIT_CODES = {"stopped": 0, "error": 1, "halted": 3, "already_running": 4, "refused": 5}

#: Why the alert router did not deliver an alert — §5.11's five mechanisms
#: plus §5.11.2's two, in the order ``process`` consults them backwards:
#: ``silenced`` (an operator's window), ``inhibited`` (a louder alert is
#: already firing), then the phase-1 five. Every member is COUNTED, because
#: a page withheld and not counted is indistinguishable from one never
#: raised.
ALERT_SUPPRESSIONS = (
    "dedup",
    "group_wait",
    "repeat_interval",
    "rate_limit",
    "queue_full",
    "inhibited",
    "silenced",
)

#: The three states a :class:`~dskit.production.records.Silence` DERIVES
#: from its two instants (§5.11.2). Nothing stores one: a stored state
#: would be wrong from the moment nothing wrote it, and no process is
#: scheduled to.
SILENCE_STATES = ("pending", "active", "expired")

#: The escalation ladder ``document.alerting.escalation`` may declare
#: (§5.11.2), and the ORDER is the content — levels are climbed in order
#: and never skipped. Closed for the same reason ``SEVERITIES`` is: an
#: operator cannot invent a fourth rung whose delivery no test covers.
ESCALATION_LEVELS = ("primary", "secondary", "final")

#: §5.11.1's phase-1 table: metric name -> {label name -> permitted values}.
#: Label NAMES are closed at declaration; an undeclared VALUE drops to
#: ``metrics.RESERVED_LABEL_VALUE`` (``other``), which no set here may
#: contain. Every value set is a vocabulary above, never restated — except
#: the two labels named after document-selected objects, ``sink`` and
#: ``monitor``, whose values are the core KIND names (the registry keys of
#: ``ALERT_SINK_KINDS`` and ``MONITOR_KINDS``), never a document's instance
#: names: a child class referenced by path falls to the reserved value by
#: the normal cardinality rule. A later group pins each tuple equal to its
#: registry's ``kinds()``, as §4.3 does for ``FEE_KIND_NAMES``.
METRIC_LABEL_VALUES = {
    "ticks_total": {"status": TICK_STATUSES},
    "tick_seconds": {"phase": TICK_PHASES},
    "decisions_total": {"result": PLAN_RESULTS},
    "proposals_total": {"verdict": VERDICTS},
    "submits_total": {"rung": RUNGS, "risk_effect": RISK_EFFECTS, "outcome": STATUSES},
    # The tick statuses that carry a ``refusal_reason`` (a skipped or refused
    # tick — ``failed`` carries an ``error`` instead) plus the guard verdicts.
    "refusals_total": {"reason": _SKIPPED + ("refused",) + VERDICTS},
    "alert_sink_failures_total": {"sink": ("log", "memory", "email", "webhook")},
    "alerts_suppressed_total": {"why": ALERT_SUPPRESSIONS},
    "monitor_verdicts_total": {
        "monitor": (
            "staleness",
            "decision_rate",
            "coverage",
            "latency",
            "refusals",
            "page_hinkley",
            "tracking_signal",
            "psi",
            "ks",
        ),
        "status": MONITOR_STATUSES,
    },
    "recon_breaks_total": {"class": BREAK_CLASSES},
    "ledger_append_seconds": {},
    "metrics_label_cardinality_dropped_total": {},
}

#: The declared metric names — the keys of the table above, once.
METRIC_NAMES = tuple(METRIC_LABEL_VALUES)

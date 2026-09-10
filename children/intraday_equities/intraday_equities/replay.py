"""Developmental equity replay policies over existing production seams (ADR-0117).

Gate 5a already proved ``ServeLoop`` + ``ReplayFeed`` + ``ReplayClock`` +
``PaperExecutor`` drive deterministic historical ticks; no generic hook
is missing and this module does not subclass ``ServeLoop``. Equity policy
(bar choice, next-bar-open quotes, ``(symbol, lead)`` identity, Schwab
costs) is injected into those objects: a ``BarTape`` supplies recorded
pulls, one shared ``ManualTime`` drives ``ReplayClock`` and
``ReplayFeed``, ``ServeLoop`` is the scheduler, ``PaperExecutor`` submits
through ``LegPipeline``, and the ledger is ``JsonlLedger``. Overlap/expiry
is ADR-0117 **proposed**.

Every fill-model value is a field of ``configs/fill-policy.json``. This
file has no default for those knobs — a missing or unknown name refuses.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from dskit.pipeline.base import config_hash, import_ref
from dskit.pipeline.node import (
    ConfigError,
    Node,
    check_int_param,
    register_node_kind,
    reject_unknown_params,
)
from dskit.pipeline.records import number_ok
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.bundles import (
    Data,
    Decision,
    Execution,
    Invocation,
    Observability,
    Recording,
    ReplayTape,
    Safety,
    Schedule,
)
from dskit.production.cadence import Overrun
from dskit.production.clock import ManualTime, ReplayClock
from dskit.production.control import ControlInbox
from dskit.production.document import ServeDocument
from dskit.production.executor import PaperExecutor
from dskit.production.feed import ReplayFeed
from dskit.production.health import InstanceLock
from dskit.production.ids import ReleaseIdSource
from dskit.production.ledger import Checkpoint, JsonlLedger, ServeRoot
from dskit.production.leg import SimulatedAuthority
from dskit.production.loop import ServeLoop
from dskit.production.policy import ActionPolicy, TransitionPolicy
from dskit.production.arming import ConjunctionResult
from dskit.production.coordination import LeasePermit
from dskit.production.records import (
    AccountState,
    EntryBatch,
    ExecutionScope,
    FeedResult,
    InputWatermark,
    Proposal,
    Quote,
    ScopeVerdict,
)
from dskit.production.release import ReleaseManifest, RuntimeFingerprint, artifact_digest
from dskit.production.sessions import AlwaysOpen
from dskit.production.state import SeriesState

from .nodes_capital import SchwabCostModel

__all__ = [
    "BarTape",
    "DevelopmentReplay",
    "FillPolicy",
    "HorizonBook",
    "ReplayAdapter",
]


def _child_root():
    """Return this package's child root from the file location."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _HashView:
    """Hand a mapping to ``config_hash``, which requires ``to_obj``."""

    def __init__(self, obj):
        self._obj = obj

    def to_obj(self):
        return self._obj


class FillPolicy:
    """Validated fill-model bundle. Values come from the document, never defaults.

    Parameters
    ----------
    params : dict
        Every name in :attr:`_PARAMS` is required. Closed vocabularies
        are the ADR-0117 proposed/ruled members; any other member
        refuses. ``notes`` is allowed.

    Examples
    --------
    Load the shipped developmental bundle::

        policy = FillPolicy.from_path(
            os.path.join(_child_root(), "configs", "fill-policy.json")
        )
        policy.fill_bar_offset  # 1 — from the JSON, not a Python default
    """

    _PARAMS = (
        "fill_bar_offset",
        "decision_price_field",
        "fill_price_field",
        "order_type",
        "partial_fills",
        "rejections",
        "halt_field",
        "halt_handling",
        "halted_true",
        "forced_exit_at",
        "forced_exit_horizon_basis",
        "forced_exit_price_field",
        "spread_bps",
        "taf_per_share",
        "sec31_bps",
        "min_price",
        "cost_model",
        "mark_source",
        "same_lead_overlap",
        "different_lead_overlap",
        "same_tick_order",
        "horizon_field",
        "symbol_field",
        "qty_field",
        "side_field",
        "paper_fill_rule",
        "paper_fees",
        "p_fill_on_touch",
        "submit_latency_ms",
        "cancel_latency_ms",
        "seed",
        "fill_suffix_bars",
        "fill_suffix_weekdays",
    )
    _VOCAB = {
        "order_type": ("market",),
        "rejections": ("none",),
        "halt_handling": ("skip", "queue"),
        "forced_exit_at": ("horizon_expiry",),
        "forced_exit_horizon_basis": ("fill",),
        "mark_source": ("fill_bar_open",),
        "same_lead_overlap": ("refuse", "override"),
        "different_lead_overlap": ("concurrent",),
        "same_tick_order": ("exits_then_entries",),
        "paper_fill_rule": ("touch",),
        "paper_fees": ("none",),
        "cost_model": ("intraday_equities.nodes_capital:SchwabCostModel",),
    }

    def __init__(self, params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError(problems)
        self._params = dict(params)
        for name in self._PARAMS:
            setattr(self, name, params[name])
        cost_cls = import_ref(params["cost_model"])
        self.costs = cost_cls({name: params[name] for name in SchwabCostModel._PARAMS})

    @classmethod
    def from_path(cls, path):
        """Load and validate one JSON fill-policy document."""
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in cls._PARAMS:
            if name not in params:
                problems.append(f"{name} is required")
        if problems:
            return problems
        check_int_param(problems, "fill_bar_offset", params["fill_bar_offset"], ge=1)
        check_int_param(problems, "submit_latency_ms", params["submit_latency_ms"], ge=0)
        check_int_param(problems, "cancel_latency_ms", params["cancel_latency_ms"], ge=0)
        check_int_param(problems, "seed", params["seed"], ge=0)
        check_int_param(problems, "fill_suffix_bars", params["fill_suffix_bars"], ge=1)
        check_int_param(problems, "fill_suffix_weekdays", params["fill_suffix_weekdays"], ge=1)
        if params["partial_fills"] is not False:
            problems.append(
                f"partial_fills must be false for this fill model, got {params['partial_fills']!r}"
            )
        if params["halted_true"] is not True:
            problems.append(
                f"halted_true must be true (JSON true) for this fill model, got "
                f"{params['halted_true']!r}"
            )
        if not number_ok(params["p_fill_on_touch"]) or params["p_fill_on_touch"] != 1.0:
            problems.append(
                f"p_fill_on_touch must be 1.0 for this fill model, got "
                f"{params['p_fill_on_touch']!r}"
            )
        for name, allowed in cls._VOCAB.items():
            if params[name] not in allowed:
                problems.append(
                    f"{name} must be one of {list(allowed)}, got {params[name]!r}"
                )
        for name in ("decision_price_field", "fill_price_field", "forced_exit_price_field",
                     "halt_field", "horizon_field", "symbol_field", "qty_field", "side_field"):
            if not isinstance(params[name], str) or not params[name]:
                problems.append(f"{name} must be a non-empty string, got {params[name]!r}")
        problems.extend(SchwabCostModel.validate_params({
            name: params[name] for name in SchwabCostModel._PARAMS
        }))
        return problems

    def to_obj(self):
        """Return the document mapping this policy was built from."""
        return dict(self._params)

    def digest(self):
        """Identity hash of the fill-policy document (notes stripped)."""
        return config_hash(_HashView(self.to_obj()), exclude=())

    def paper_params(self):
        """``PaperExecutor`` knobs copied from this document — no literals."""
        return {
            "fill_rule": self.paper_fill_rule,
            "fees": {"kind": self.paper_fees},
            "partial_fills": self.partial_fills,
            "p_fill_on_touch": self.p_fill_on_touch,
            "latency_ms": {
                "submit": self.submit_latency_ms,
                "cancel": self.cancel_latency_ms,
            },
            "seed": self.seed,
        }


class HorizonBook:
    """Open lots keyed by ``(symbol, lead)`` under the proposed overlap rule.

    Parameters
    ----------
    policy : FillPolicy
        Closed overlap and expiry vocabularies.

    Examples
    --------
    Open one h2 lot and read its expiry index::

        book = HorizonBook(FillPolicy.from_path(
            os.path.join(_child_root(), "configs", "fill-policy.json")
        ))
        book.open_lot("AAA", 2, qty=10, side="buy", fill_index=1)
        book.expiry_index("AAA", 2)  # 3  — fill index + lead
    """

    def __init__(self, policy):
        self._policy = policy
        self._lots = {}

    def is_open(self, symbol, lead):
        """Return whether ``(symbol, lead)`` already holds a lot."""
        return (symbol, lead) in self._lots

    def open_lot(self, symbol, lead, qty, side, fill_index):
        """Record a new lot. Return False when the same-lead rule refuses."""
        key = (symbol, lead)
        overlap = self._policy.same_lead_overlap
        if key in self._lots:
            if overlap == "refuse":
                return False
            if overlap == "override":
                self.close_lot(symbol, lead)
            else:
                raise ConfigError([f"same_lead_overlap {overlap!r} has no dispatch"])
        if self._policy.forced_exit_horizon_basis == "fill":
            expiry_index = fill_index + lead
        else:
            raise ConfigError([
                f"forced_exit_horizon_basis {self._policy.forced_exit_horizon_basis!r} "
                "has no expiry rule"
            ])
        self._lots[key] = {
            "qty": qty,
            "side": side,
            "fill_index": fill_index,
            "expiry_index": expiry_index,
        }
        return True

    def expiry_index(self, symbol, lead):
        """Bar index of the forced exit for an open lot."""
        return self._lots[(symbol, lead)]["expiry_index"]

    def close_lot(self, symbol, lead):
        """Remove an open lot and return it."""
        return self._lots.pop((symbol, lead))

    def expiring(self, symbol, index):
        """Lots on ``symbol`` whose expiry bar is due (``expiry_index <= index``)."""
        return [
            (lead, lot)
            for (sym, lead), lot in tuple(self._lots.items())
            if sym == symbol and lot["expiry_index"] <= index
        ]

    def unclosed(self):
        """Open lots still on the book, as ``((symbol, lead), lot)`` pairs."""
        return tuple(self._lots.items())


class BarTape(ReplayTape):
    """Historical bars as D20 tape data: feed results, not a scheduler."""

    def __init__(self, bars, source_config_hash):
        times = sorted({int(bar["asof_ms"]) for bar in bars})
        self._times = tuple(times)
        self._source = source_config_hash
        self._results = tuple(
            FeedResult(
                status="live",
                acq_id=f"bar-{ts}",
                records_added=sum(1 for bar in bars if int(bar["asof_ms"]) == ts),
                source_config_hash=source_config_hash,
                at_ms=ts,
            )
            for ts in self._times
        )

    def start_ms(self):
        """Return the first bar instant, or 0 when the tape is empty."""
        return self._times[0] if self._times else 0

    def feed_results(self):
        """Return one live pull per unique bar timestamp."""
        return self._results

    def id_allocations(self):
        """Return no recorded ids — ``ReleaseIdSource`` allocates live."""
        return ()


class _TapeCadence:
    """Tick exactly at the tape's timestamps."""

    def __init__(self, times):
        self._times = tuple(times)

    def next_tick(self, after_ms, calendar):
        """Return the next tape instant strictly after ``after_ms``."""
        for ts in self._times:
            if ts > after_ms:
                return ts
        return None


class _Ready:
    """Health that is already ready so the gate may pass."""

    state = "ready"

    def evaluate(self, now_ms):
        return "ready"

    def can_act(self):
        return True

    def can_heartbeat(self):
        return True

    def mark_unhealthy(self, cause, now_ms):
        return None

    def stop(self):
        self.state = "stopping"


class _Go:
    """Readiness that always answers GO (shadow/paper developmental)."""

    def verdict_for(self, view, at_ms):
        return "go"


class _Heart:
    """Heartbeat that records nothing."""

    def start(self):
        return None

    def ready(self):
        return None

    def stopping(self):
        return None

    def close(self):
        return None

    def beat(self, now_ms):
        return True

    def note_tick_completed(self, monotonic_s):
        return None


class _Metrics:
    """Metrics sink that ignores every observation."""

    def counter(self, name, labels=()):
        return self

    def gauge(self, name, labels=()):
        return self

    def histogram(self, name, labels=(), buckets=None):
        return self

    def inc(self, n=1, **labels):
        return None

    def set(self, value, **labels):
        return None

    def observe(self, value, **labels):
        return None

    def flush(self, at_ms, tick_id):
        return True


class _Alerts:
    """Alert router that never pages."""

    def raise_alert(self, alert):
        return True

    def process(self, now_ms):
        return ()

    def close(self):
        return None


class _Breaker:
    """Series breaker that stays active unless tripped."""

    def __init__(self):
        self.state = "active"

    def current(self, view):
        return self.state

    def halt_sentinel_present(self):
        return False

    def trip(self, reason, actor, control_request_id=None, principal_digest=None,
             proof_digest=None, cause="trip"):
        self.state = "halted"
        return 1

    def cancel_working(self, view, trip_id):
        return None


class _Arming:
    """Paper arming: conjunction satisfied, scope not_armed."""

    def current(self, view, at_ms):
        return None

    def expire_if_due(self, view, at_ms):
        return None

    def apply_scope(self, proposal, arming_state):
        return ScopeVerdict(
            allowed=True, scope_key=proposal.instrument, reason="shadow_unarmed"
        )

    def check_conjunction(self, invocation, view, origin, reduction, rung, at_ms):
        return ConjunctionResult(satisfied=True, reason="")


class _Guards:
    """Pass-through guards so equity proposals reach the paper venue."""

    guards = {}

    def requirements(self, candidates, at_ms, calendar):
        return ()

    def check_all(self, proposal, state):
        return proposal, ()

    def check_authority_scope(self, proposal, state, arm):
        return ScopeVerdict(allowed=True, scope_key=proposal.instrument, reason="")

    def new_holds(self, findings, state_view, at_ms, calendar):
        return ()


class _Accounting:
    """Snapshot with a real digest so the permit digest gate passes."""

    def __init__(self, digest):
        self._digest = digest

    def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
        return AccountState(
            risk_version=state_view.risk_version,
            asof_ms=at_ms,
            evidence_digest=self._digest,
            balances=(),
            positions=(),
            working=(),
            measure_evidence={},
            source_digests={},
        )

    def value(self, state_view, quotes, at_ms):
        return Decimal("0")

    def classify(self, proposal, state):
        return "increase"


class _Lease:
    """Lease that never expires during a developmental tape."""

    LIVE_CAPABLE = False

    def __init__(self, expires_ms):
        self.permit = None
        self._expires = expires_ms

    def acquire(self, scope, holder, ttl_ms):
        self.permit = LeasePermit(
            scope=scope, holder=holder, fencing_token=1, expires_ms=self._expires
        )
        return self.permit

    def renew(self, permit):
        return permit

    def current(self, scope):
        return self.permit

    def release(self, permit):
        self.permit = None


class _Reconcile:
    """Reconciler that is never due."""

    def due(self, now, last):
        return False

    def run(self, *args, **kwargs):
        return None

    def apply_policy(self, report):
        return None


class _Verifier:
    """Submission verifier that never disables the paper venue."""

    def __init__(self):
        self.disabled = False

    def reset_after_reconcile(self):
        self.disabled = False

    def refuse_until_reconciled(self, reason):
        self.disabled = True


class _AuthorityTable:
    """One SimulatedAuthority for every origin."""

    def __init__(self, authority):
        self._authority = authority

    def for_origin(self, origin, breaker):
        return self._authority


class _PaperVenue:
    """PaperExecutor that records equity fill rows after LegPipeline submits."""

    def __init__(self, inner, owner):
        self._inner = inner
        self._owner = owner

    def submit(self, intent, permit, state):
        ack = self._inner.submit(intent, permit, state)
        self._owner._on_ack(intent, ack)
        return ack

    def __getattr__(self, name):
        return getattr(self._inner, name)


class EquityReplay:
    """Compose ServeLoop + ReplayFeed + shared ReplayClock around the equity book."""

    def __init__(self, policy):
        self._policy = policy
        self.proposer = self
        self.serving_hash = policy.digest()
        self.fills = []
        self.skipped = []
        self.refused = []
        self._by_symbol = {}
        self._index_of = {}
        self._pending = {}
        self._book = HorizonBook(policy)
        self._venue = None
        self._source = policy.digest()
        self._asof = 0
        self._last_bar = {}
        self._queued = []
        self._pending_by_id = {}
        self._fault = None

    def run(self, bars, decisions):
        """Drive ServeLoop over ``bars`` and return fills/skips/refusals."""
        policy = self._policy
        by_symbol = defaultdict(list)
        halt_field = policy.halt_field
        for bar in bars:
            row = dict(bar)
            row["asof_ms"] = int(row["asof_ms"])
            if halt_field in row:
                row[halt_field] = _halt_flag(row[halt_field], halt_field, row["asof_ms"])
            by_symbol[row[policy.symbol_field]].append(row)
        for seq in by_symbol.values():
            seq.sort(key=lambda row: row["asof_ms"])
            times = [row["asof_ms"] for row in seq]
            if len(set(times)) != len(times):
                symbol = seq[0][policy.symbol_field]
                raise ConfigError([
                    f"duplicate asof_ms on {symbol!r}: each bar on a name must have a unique timestamp"
                ])
        self._by_symbol = dict(by_symbol)
        self._index_of = {
            symbol: {bar["asof_ms"]: i for i, bar in enumerate(seq)}
            for symbol, seq in self._by_symbol.items()
        }
        self._pending = {symbol: defaultdict(list) for symbol in self._by_symbol}
        self._last_bar = {symbol: seq[0]["asof_ms"] for symbol, seq in self._by_symbol.items()}
        for decision in decisions:
            symbol = decision[policy.symbol_field]
            lead = decision[policy.horizon_field]
            if symbol not in self._by_symbol:
                self.refused.append({
                    "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                    "reason": "unknown_symbol",
                })
                continue
            if isinstance(lead, bool) or not isinstance(lead, int) or lead < 1:
                self.refused.append({
                    "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                    "reason": "lead",
                })
                continue
            decision_index = self._index_of[symbol].get(int(decision["asof_ms"]))
            seq = self._by_symbol[symbol]
            if decision_index is None:
                self.refused.append({
                    "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                    "reason": "unknown_decision_bar",
                })
                continue
            if policy.decision_price_field not in seq[decision_index]:
                self.refused.append({
                    "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                    "reason": "decision_price_field",
                })
                continue
            fill_index = decision_index + policy.fill_bar_offset
            if fill_index >= len(seq):
                self.refused.append({
                    "symbol": symbol, "asof_ms": decision["asof_ms"], "lead": lead,
                    "reason": "fill_bar_past_tape",
                })
                continue
            self._pending[symbol][fill_index].append(decision)
        if not self._by_symbol:
            return self._result()
        self._run_loop(bars)
        last = {symbol: seq[-1]["asof_ms"] for symbol, seq in self._by_symbol.items()}
        for (sym, lead), lot in self._book.unclosed():
            self.refused.append({
                "symbol": sym, "asof_ms": last.get(sym), "lead": lead, "qty": lot["qty"],
                "reason": "expiry_past_tape",
            })
            self._book.close_lot(sym, lead)
        return self._result()

    def _result(self):
        self.fills.sort(key=lambda row: (
            row["asof_ms"], row["symbol"], row["lead"], 0 if row["kind"] == "exit" else 1,
        ))
        return {"fills": self.fills, "skipped": self.skipped, "refused": self.refused}

    def _run_loop(self, bars):
        policy = self._policy
        digest = policy.digest()
        tape = BarTape(bars, self._source)
        times = tape._times
        shared = ManualTime(tape.start_ms())
        clock = ReplayClock(manual_time=shared)
        feed = ReplayFeed({}, tape=tape.feed_results(), time=shared)
        inner = PaperExecutor(
            policy.paper_params(),
            clock=clock,
            scope=ExecutionScope(venue="paper", account="replay"),
        )
        self._venue = _PaperVenue(inner, self)
        work = tempfile.mkdtemp(prefix="gate5-replay-")
        series_id = str(uuid.uuid4())
        try:
            run_dir = os.path.join(work, "run")
            os.makedirs(run_dir, exist_ok=True)
            artifact = os.path.join(run_dir, "policy")
            with open(artifact, "w", encoding="utf-8") as fh:
                json.dump(policy.to_obj(), fh)
            document = ServeDocument.from_obj(
                _shadow_document(run_dir, series_id, sorted(self._by_symbol))
            )
            release = _release_for(
                run_dir, artifact, sorted(self._by_symbol), digest, self._source,
                document, tape.start_ms(),
            )
            serve = ServeRoot(os.path.join(work, "serve"), series_id)
            lock = InstanceLock(serve.lock_path)
            lock.acquire()
            state = SeriesState(series_id)
            ledger = JsonlLedger(
                serve, "replay-1", release.release_hash, clock=clock, state=state, lock=lock,
            )
            inbox = ControlInbox(serve, clock)
            calendar = AlwaysOpen({})
            arming = _Arming()
            health = _Ready()
            lease = _Lease(tape.start_ms() + 86_400_000 * 3650)
            venue = self._venue
            authority = SimulatedAuthority(
                clock, calendar, arming, lease, health, venue, document, release, ledger, inbox,
            )
            accounting = _Accounting(digest)
            schedule = Schedule(
                clock=clock,
                calendar=calendar,
                cadence=_TapeCadence(times),
                overrun=Overrun({}),
            )
            data = Data(feed=feed, decider=self)
            decision = Decision(guards=_Guards(), monitors={})
            safety = Safety(
                breaker=_Breaker(),
                arming=arming,
                authorities=_AuthorityTable(authority),
                readiness=_Go(),
                invocation=Invocation(
                    armed=False, env_release_hash=None, once=False, max_ticks=len(times),
                ),
                action_policy=ActionPolicy(),
                transition_policy=TransitionPolicy(),
                submission_verifier=_Verifier(),
            )
            execution = Execution(
                executor=venue, accounting=accounting, lease=lease, resilience=object(),
            )
            recording = Recording(
                ledger=ledger,
                state=state,
                inbox=inbox,
                reconciler=_Reconcile(),
                checkpoint=Checkpoint(
                    release_hash=release.release_hash,
                    last_tick_at=None,
                    last_completed_tick_at=None,
                    pending=(),
                    positions_snapshot_at=None,
                    schema_version=1,
                    head_seq=0,
                    head_hash="0" * 64,
                ),
                journal_hook=_journal_noop,
                id_source=ReleaseIdSource(release.release_hash),
            )
            observability = Observability(
                metrics=_Metrics(), alerts=_Alerts(), health=health, heartbeat=_Heart(),
            )
            loop = ServeLoop(
                document, release, schedule, data, decision, safety, execution,
                recording, observability, lock=lock, process_id="replay-1",
            )
            code = loop.run()
            ledger.close()
            lock.release()
            if self._fault is not None:
                raise self._fault
            if code != 0:
                raise ConfigError([
                    f"ServeLoop exited {code} state={loop.state!r}"
                ])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def read_entry(self, tick_at_ms):
        """Freeze every name's bar at this tick as the entry batch."""
        asof = int(tick_at_ms)
        records = []
        watermarks = {}
        for symbol, seq in self._by_symbol.items():
            idx = self._index_of[symbol].get(asof)
            if idx is not None:
                self._last_bar[symbol] = asof
                records.append(seq[idx])
            mark_at = self._last_bar[symbol]
            watermarks[symbol] = InputWatermark(
                key=symbol, latest_asof_ms=mark_at, source_digest=canonical_hash(symbol),
            )
        self._asof = asof
        keys = sorted(watermarks)
        digest = self._policy.digest()
        return EntryBatch(
            outputs={"records": records},
            watermarks_by_key=watermarks,
            required_keys_digest=canonical_hash(keys),
            coverage_digest=digest,
            data_asof_ms=min(w.latest_asof_ms for w in watermarks.values()),
            inputs_digest=digest,
            source_config_hash=self._source,
        )

    def evaluate(self, batch):
        """Queue equity exits/entries at this tape instant for LegPipeline."""
        try:
            asof = self._asof
            policy = self._policy
            if policy.forced_exit_at != "horizon_expiry":
                raise ConfigError([f"forced_exit_at {policy.forced_exit_at!r} has no dispatch"])
            if policy.mark_source != "fill_bar_open":
                raise ConfigError([f"mark_source {policy.mark_source!r} has no dispatch"])
            if policy.different_lead_overlap != "concurrent":
                raise ConfigError([
                    f"different_lead_overlap {policy.different_lead_overlap!r} has no dispatch"
                ])
            for symbol, seq in self._by_symbol.items():
                idx = self._index_of[symbol].get(asof)
                if idx is None:
                    continue
                self._apply_bar(symbol, seq[idx], idx)
            return {"records": list(batch.outputs["records"])}, self._policy.digest()
        except (ConfigError, ProductionError) as exc:
            if self._fault is None:
                self._fault = exc
            raise

    def candidates(self, head_outputs):
        """No extra candidates — equity size is already on the queued proposals."""
        return ()

    def quotes(self, head_outputs):
        """Return next-bar-open quotes so the tick's QuoteSet is non-empty."""
        policy = self._policy
        asof = self._asof
        found = []
        for symbol, seq in self._by_symbol.items():
            idx = self._index_of[symbol].get(asof)
            if idx is None:
                continue
            price = Decimal(str(seq[idx][policy.fill_price_field]))
            quote = Quote(instrument=symbol, bid=price, ask=price, mid=price, asof_ms=asof)
            self._venue.on_quote(quote)
            found.append(quote)
        return found

    def proposals(self, head_outputs, candidates, account, provenance):
        """Return this tick's queued equity orders for LegPipeline."""
        found = list(self.quotes(head_outputs))
        quote_digest = canonical_hash([quote.to_obj() for quote in found])
        digest = self._policy.digest()
        queued, self._queued = self._queued, []
        out = []
        for meta in queued:
            out.append(self._proposal_for(meta, digest, quote_digest))
        return out

    def _apply_bar(self, symbol, bar, index):
        """Apply exits then entries, or halt skip/queue, at one fill bar."""
        policy = self._policy
        halted = self._halted(bar)
        pending = self._pending[symbol]
        if halted:
            for lead, _lot in self._book.expiring(symbol, index):
                self.skipped.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "halted",
                })
            incoming = list(pending.pop(index, ()))
            if policy.halt_handling == "skip":
                for decision in incoming:
                    self.skipped.append({
                        "symbol": symbol, "asof_ms": bar["asof_ms"], "reason": "halted",
                    })
            elif policy.halt_handling == "queue":
                pending[index + 1].extend(incoming)
            else:
                raise ConfigError([f"halt_handling {policy.halt_handling!r} has no dispatch"])
            return
        if policy.same_tick_order != "exits_then_entries":
            raise ConfigError([f"same_tick_order {policy.same_tick_order!r} has no dispatch"])
        self._process_exits(symbol, bar, index)
        self._process_entries(symbol, bar, index, pending.pop(index, ()))

    def _halted(self, bar):
        """Return whether ``bar`` is halted, accepting JSON and numpy bools."""
        field = self._policy.halt_field
        if field not in bar:
            raise ConfigError([f"bar missing halt field {field!r} at asof_ms={bar.get('asof_ms')!r}"])
        flag = _halt_flag(bar[field], field, bar.get("asof_ms"))
        return flag == self._policy.halted_true

    def _process_exits(self, symbol, bar, index):
        """Force-exit lots whose expiry bar is this fill bar."""
        policy = self._policy
        price = bar[policy.forced_exit_price_field]
        for lead, lot in self._book.expiring(symbol, index):
            side = "sell" if lot["side"] == "buy" else "buy"
            fee = (
                policy.costs.sell_per_share(price) if side == "sell" else policy.costs.buy_per_share(price)
            ) * lot["qty"]
            self._queue_fill(
                "exit", symbol, lead, side, lot["qty"], price, bar["asof_ms"], fee, index
            )
            self._book.close_lot(symbol, lead)

    def _process_entries(self, symbol, bar, index, incoming):
        """Open new lots after exits on this fill bar."""
        policy = self._policy
        price = bar[policy.fill_price_field]
        for decision in incoming:
            lead = decision[policy.horizon_field]
            qty = decision[policy.qty_field]
            side = decision[policy.side_field]
            if policy.costs.below_floor(price):
                self.refused.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "min_price",
                })
                continue
            if not self._book.open_lot(symbol, lead, qty, side, index):
                self.refused.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "same_lead_open",
                })
                continue
            fee = (
                policy.costs.buy_per_share(price) if side == "buy" else policy.costs.sell_per_share(price)
            ) * qty
            self._queue_fill(
                "entry", symbol, lead, side, qty, price, bar["asof_ms"], fee, index
            )

    def _queue_fill(self, kind, symbol, lead, side, qty, price, asof_ms, fee, index):
        """Remember one fill so ``proposals`` can hand it to LegPipeline."""
        prefix = "0" if kind == "exit" else "1"
        client_ref = f"{prefix}-{kind}-{symbol}-{lead}-{index}"
        meta = {
            "id": client_ref,
            "kind": kind,
            "symbol": symbol,
            "lead": lead,
            "side": side,
            "qty": qty,
            "price": price,
            "asof_ms": asof_ms,
            "fee": fee,
        }
        self._pending_by_id[client_ref] = meta
        self._queued.append(meta)

    def _proposal_for(self, meta, digest, quote_digest):
        """Build the Proposal LegPipeline submits for one queued fill."""
        policy = self._policy
        px = Decimal(str(meta["price"]))
        qty_d = Decimal(str(meta["qty"]))
        return Proposal(
            id=meta["id"],
            instrument=meta["symbol"],
            side=meta["side"],
            qty=qty_d,
            notional=None,
            limit=None,
            tif="ioc",
            expires_ms=meta["asof_ms"] + 86_400_000,
            reference_price=px,
            exposure=qty_d * px,
            direction="long" if meta["side"] == "buy" else "short",
            confidence=0.0,
            prediction=0.0,
            baseline=0.0,
            expected_value=0.0,
            inputs_asof_ms=meta["asof_ms"],
            inputs_digest=digest,
            coverage_digest=digest,
            quote_asof_ms=meta["asof_ms"],
            quote_digest=quote_digest,
            extra={"order_type": policy.order_type, "kind": meta["kind"], "lead": meta["lead"]},
        )

    def _on_ack(self, intent, ack):
        """Record one filled equity row after LegPipeline reaches the venue."""
        try:
            meta = self._pending_by_id.pop(intent.proposal.id, None)
            if meta is None:
                return
            if ack.status != "filled":
                raise ConfigError([
                    f"replay fill model forbids rejections, got status={ack.status!r} "
                    f"reason={ack.reason!r} for {intent.proposal.id}"
                ])
            self.fills.append(_fill_row(
                meta["kind"], meta["symbol"], meta["lead"], meta["side"],
                meta["qty"], meta["price"], meta["asof_ms"], meta["fee"], ack,
            ))
        except ConfigError as exc:
            if self._fault is None:
                self._fault = exc
            raise


def _halt_flag(value, field, asof_ms):
    """Return a Python bool from a JSON or numpy bool; otherwise refuse."""
    if isinstance(value, bool):
        return value
    try:
        import numpy as np
        if isinstance(value, np.generic) and getattr(value.dtype, "kind", "") == "b":
            return bool(value)
    except ImportError:
        pass
    raise ConfigError([
        f"halt field {field!r} must be a JSON bool, got {value!r} at asof_ms={asof_ms!r}"
    ])


def _fill_row(kind, symbol, lead, side, qty, price, asof_ms, fee, ack):
    """One output fill; price/qty from the adapter, fee from SchwabCostModel."""
    return {
        "kind": kind,
        "symbol": symbol,
        "lead": lead,
        "side": side,
        "qty": qty,
        "price": float(ack.avg_price) if ack.avg_price is not None else float(price),
        "asof_ms": asof_ms,
        "fee": float(fee),
    }


def _journal_noop(**kwargs):
    """D22 hook that does not write a child journal row."""
    return None


def _shadow_document(run_dir, series_id, symbols):
    """Smallest shadow ServeDocument the loop will construct."""
    universe = list(symbols) or ["AAA"]
    return {
        "name": "intraday-equities-development-replay-serve",
        "series_id": series_id,
        "rung": "shadow",
        "serving": {
            "run_dir": run_dir,
            "adapter": "intraday_equities",
            "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
            "heads": ["select"],
            "required_universe": universe,
            "proposer": {"uses": "intent-rows"},
            "max_artifact_age": "P100000D",
        },
        "feed": {"uses": "replay"},
        "schedule": {
            "clock": {"uses": "replay"},
            "calendar": {"uses": "always-open"},
            "cadence": {"uses": "fixed-interval", "params": {"period_ms": 1000}},
            "dead_after_ms": 600000,
            "max_staleness_ms": 86_400_000,
            "max_quote_age_ms": 86_400_000,
        },
        "guards": {},
        "execution": {"uses": "shadow", "submit_timeout_ms": 5000},
        "accounting": {"uses": "paper", "max_valuation_age_ms": 86_400_000},
        "arming": {"max_duration_s": 14400, "approval": {"uses": "deny-all"}},
        "coordination": {
            "scope": {"venue": "paper", "account": "replay"},
            "lease": {"uses": "process"},
            "ttl_ms": 86_400_000,
            "renew_every_ms": 10_000,
            "renew_timeout_ms": 2000,
        },
        "reconcile": {
            "on_start": False,
            "every_s": 86400,
            "on_mismatch": "halt",
            "lookback_ms": 86400000,
        },
        "monitors": {},
        "health": {
            "failure_threshold": 3,
            "success_threshold": 1,
            "timeout_s": 1.0,
            "probes": {"disk": {"uses": "ledger-writable"}},
        },
        "durability": {"fsync": "none"},
        "resilience": {
            "retry": {
                "max_attempts": 3,
                "base_s": 0.05,
                "throttle_base_s": 1.0,
                "cap_s": 20.0,
                "jitter": "full",
                "retry_after": "honor",
                "retry_writes": "idempotent_only",
                "budget": {
                    "capacity": 500,
                    "transient_cost": 14,
                    "throttle_cost": 5,
                    "refund": 1,
                },
            },
            "breaker": {"min_calls": 5, "failure_rate": 0.5, "open_s": 30},
            "limiter": {
                "submit": {"rate_per_s": 5, "burst": 5, "max_in_flight": 1},
                "cancel": {"rate_per_s": 10, "burst": 10, "reserved": True},
            },
            "transport": {"uses": "urllib"},
        },
        "lifecycle": {"cooling_off_s": 900, "shutdown_grace_s": 30},
        "readiness": {
            "checklist": "configs/readiness.json",
            "waivers": [],
            "valid_for_s": 86400,
        },
        "alerting": {
            "sinks": {"ops": {"uses": "memory"}},
            "routes": [{"severity": "critical", "sinks": ["ops"]}],
        },
        "placement": {"ledger_root": "./serve"},
    }


def _release_for(run_dir, artifact, symbols, digest, source_hash, document, created_ms):
    """Build a ReleaseManifest verify_release accepts for this tape."""
    hex64 = digest
    return ReleaseManifest(
        series_id=document.series_id,
        doc_hash=document.doc_hash,
        run_hash=hex64,
        serving_hash=hex64,
        artifacts={"policy": {"digest": artifact_digest(artifact), "timestamp_ms": created_ms}},
        classes={"replay": {"ref": "intraday_equities.replay:FillPolicy", "code_digest": hex64}},
        adapter={"name": "intraday_equities", "digest": hex64},
        feed_spec={
            "source_binding": {"source": "replay", "connector": "intraday_equities.replay:BarTape"},
            "entity_key_fields": ["symbol"],
            "event_time_field": "asof_ms",
            "digest_recipe": {"kind": "stream-digest"},
            "required_keys": list(symbols),
            "required_keys_digest": canonical_hash(list(symbols)),
            "source_config_hash": source_hash,
            "source_config_version": "1",
        },
        source_config={"hash": source_hash, "version": "1"},
        execution_scope=ExecutionScope(venue="paper", account="replay"),
        approval_fingerprint=hex64,
        lease_fingerprint=hex64,
        checklist_digest=hex64,
        runtime_fingerprint=RuntimeFingerprint.capture(),
        created_ms=created_ms if created_ms > 0 else 1,
    )


class ReplayAdapter:
    """Drive ServeLoop with next-bar-open quotes and the overlap book.

    Parameters
    ----------
    policy : FillPolicy
        The fill-model bundle.

    Examples
    --------
    One h1 buy filled at the next bar's open::

        policy = FillPolicy.from_path(
            os.path.join(_child_root(), "configs", "fill-policy.json")
        )
        adapter = ReplayAdapter(policy)
        out = adapter.replay(
            [{"symbol": "AAA", "asof_ms": 1000, "open": 10.0, "close": 10.5, "halted": False},
             {"symbol": "AAA", "asof_ms": 2000, "open": 11.0, "close": 11.5, "halted": False},
             {"symbol": "AAA", "asof_ms": 3000, "open": 12.0, "close": 12.5, "halted": False}],
            [{"symbol": "AAA", "asof_ms": 1000, "lead": 1, "qty": 10, "side": "buy"}],
        )
        out["fills"][0]["price"]  # 11.0
    """

    def __init__(self, policy):
        self._policy = policy

    def replay(self, bars, decisions):
        """Replay ``decisions`` over ``bars`` through :class:`ServeLoop`.

        Parameters
        ----------
        bars : list of dict
            Per-symbol bars carrying the policy's price and halt fields.
        decisions : list of dict
            Decision-bar rows carrying symbol, lead, side, qty, asof_ms.

        Returns
        -------
        dict
            ``fills``, ``skipped``, ``refused``.
        """
        return EquityReplay(self._policy).run(bars, decisions)

class DevelopmentReplay(Node):
    """Pipeline doorway for developmental replay: ineligible, evidence-bounded.

    Parameters
    ----------
    params : dict
        ``deployment_eligible`` (must be JSON false), ``evidence_end``
        (``YYYY-MM-DD``, last included UTC date), ``fill_policy`` (path
        relative to the child root), ``fill_policy_sha256`` (must match
        the loaded document), ``caps`` (must be ``development-only``).

    Examples
    --------
    Construct the shipped developmental document's node::

        node = DevelopmentReplay("replay", {
            "deployment_eligible": False,
            "evidence_end": "2025-10-16",
            "fill_policy": "configs/fill-policy.json",
            "fill_policy_sha256": FillPolicy.from_path(
                os.path.join(_child_root(), "configs", "fill-policy.json")
            ).digest(),
            "caps": "development-only",
        })
        node.params["deployment_eligible"]  # False
    """

    role = "transform"
    outputs = ("fills", "skipped", "refused")
    _PARAMS = (
        "deployment_eligible",
        "evidence_end",
        "fill_policy",
        "fill_policy_sha256",
        "caps",
    )

    def __init__(self, key, params=None, **kwargs):
        super().__init__(key, params, **kwargs)
        self._policy = FillPolicy.from_path(self._resolved_fill_policy_path(self.params))

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the node as a pure input/param reader."""
        return "pure"

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in cls._PARAMS:
            if name not in params:
                problems.append(f"{name} is required")
        if problems:
            return problems
        if params["deployment_eligible"] is not False:
            problems.append(
                "deployment_eligible must be false — developmental replay is not "
                f"deployment evidence, got {params['deployment_eligible']!r}"
            )
        if params["caps"] != "development-only":
            problems.append(
                "caps must be 'development-only' until confirmed caps exist, got "
                f"{params['caps']!r}"
            )
        if not isinstance(params["evidence_end"], str):
            problems.append(f"evidence_end must be YYYY-MM-DD, got {params['evidence_end']!r}")
        else:
            try:
                datetime.strptime(params["evidence_end"], "%Y-%m-%d")
            except ValueError:
                problems.append(f"evidence_end must be YYYY-MM-DD, got {params['evidence_end']!r}")
        if not isinstance(params["fill_policy"], str) or not params["fill_policy"]:
            problems.append("fill_policy must be a non-empty path")
        else:
            path = cls._resolved_fill_policy_path(params)
            if not os.path.isfile(path):
                problems.append(f"fill_policy path does not exist: {path}")
            elif not isinstance(params["fill_policy_sha256"], str):
                problems.append("fill_policy_sha256 must be the fill-policy digest")
            else:
                loaded = FillPolicy.from_path(path)
                if loaded.digest() != params["fill_policy_sha256"]:
                    problems.append(
                        "fill_policy_sha256 does not match the loaded fill-policy digest"
                    )
        return problems

    @classmethod
    def _resolved_fill_policy_path(cls, params):
        """Join a relative fill-policy path onto the child root."""
        path = params["fill_policy"]
        if os.path.isabs(path):
            return path
        return os.path.join(_child_root(), path)

    def _exclusive_end_ms(self):
        """First UTC instant after ``evidence_end`` (epoch ms)."""
        return self._utc_day_end_ms(1)

    def _fill_weekdays_from(self, asof_ms):
        """Count UTC weekdays from the day after evidence_end through the bar's date."""
        evidence = datetime.strptime(self.params["evidence_end"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        bar = datetime.fromtimestamp(int(asof_ms) / 1000, tz=timezone.utc)
        bar_day = datetime(bar.year, bar.month, bar.day, tzinfo=timezone.utc)
        if bar_day <= evidence:
            return 0
        days = 0
        cursor = evidence + timedelta(days=1)
        while cursor <= bar_day:
            if cursor.weekday() < 5:
                days += 1
            cursor += timedelta(days=1)
        return days

    def _utc_day_end_ms(self, extra_days):
        """Midnight UTC ``extra_days`` after ``evidence_end``, as epoch ms."""
        day = datetime.strptime(self.params["evidence_end"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        return int((day + timedelta(days=extra_days)).timestamp() * 1000)

    def validate_inputs(self, inputs):
        """Require ``bars`` and ``decisions`` lists."""
        problems = []
        for port in ("bars", "decisions"):
            if port not in inputs:
                problems.append(f"{port} is required")
            elif not isinstance(inputs[port], (list, tuple)):
                problems.append(f"{port} must be a list, got {type(inputs[port]).__name__}")
        return problems

    def run(self, ctx, inputs):
        """Refuse out-of-window decisions/bars, then replay through :class:`ReplayAdapter`."""
        exclusive = self._exclusive_end_ms()
        suffix_bars = int(self._policy.fill_suffix_bars)
        suffix_weekdays = int(self._policy.fill_suffix_weekdays)
        late_decisions = [
            row for row in inputs["decisions"]
            if int(row["asof_ms"]) >= exclusive
        ]
        if late_decisions:
            raise ConfigError([
                f"evidence_end {self.params['evidence_end']!r} excludes "
                f"{len(late_decisions)} decision(s) at or after {exclusive}"
            ])
        per_symbol = {}
        for bar in inputs["bars"]:
            asof_ms = int(bar["asof_ms"])
            if asof_ms < exclusive:
                continue
            when = datetime.fromtimestamp(asof_ms / 1000, tz=timezone.utc)
            if when.weekday() >= 5:
                raise ConfigError([
                    f"evidence_end {self.params['evidence_end']!r} excludes "
                    f"weekend fill-only bar(s) at {asof_ms}"
                ])
            if self._fill_weekdays_from(asof_ms) > suffix_weekdays:
                raise ConfigError([
                    f"evidence_end {self.params['evidence_end']!r} excludes "
                    f"bar(s) beyond fill_suffix_weekdays={suffix_weekdays}"
                ])
            symbol = str(bar["symbol"])
            per_symbol[symbol] = per_symbol.get(symbol, 0) + 1
            if per_symbol[symbol] > suffix_bars:
                raise ConfigError([
                    f"evidence_end {self.params['evidence_end']!r} excludes "
                    f"bar(s) beyond fill_suffix_bars={suffix_bars}"
                ])
        return ReplayAdapter(self._policy).replay(list(inputs["bars"]), list(inputs["decisions"]))


NODE_KINDS = {
    "intraday_equities-development-replay": DevelopmentReplay,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

"""Developmental equity replay policies over existing production seams (ADR-0117).

Gate 5a already proved ``ServeLoop`` + ``ReplayFeed`` + ``ReplayClock`` +
``PaperExecutor`` drive deterministic historical ticks; no generic hook
is missing and this module does not subclass ``ServeLoop``. Equity policy
(bar choice, next-bar-open quotes, ``(symbol, lead)`` identity, Schwab
costs) is injected into ``compose.bundles_for``: a ``BarTape`` is the
D20 tape (clock + feed), ``ReleaseIdSource`` allocates because this is
not a recorded series, ``_TapeCadence`` ticks at bar instants, this
object is the decider, and ``_PaperVenue`` records fills from compose's
``PaperExecutor``. Overlap/expiry is ADR-0117 **proposed**.

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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from dskit.onboarding import OnboardingRoot
from dskit.pipeline.base import config_hash, import_ref
from dskit.pipeline.node import (
    ConfigError,
    Node,
    ServingContract,
    check_int_param,
    register_node_kind,
    reject_unknown_params,
)
from dskit.pipeline.records import number_ok
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.bundles import Data, Invocation, ReplayTape
from dskit.production.cadence import Cadence
from dskit.production.compose import bundles_for
from dskit.production.document import ServeDocument
from dskit.production.executor import PaperExecutor
from dskit.production.health import InstanceLock
from dskit.production.ids import ReleaseIdSource
from dskit.production.ledger import ServeRoot
from dskit.production.loop import ServeLoop
from dskit.production.records import (
    EntryBatch,
    ExecutionScope,
    FeedResult,
    InputWatermark,
    Proposal,
    Quote,
)
from dskit.production.release import ReleaseManifest, RuntimeFingerprint, artifact_digest

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


class _TapeCadence(Cadence):
    """Tick exactly at the tape's timestamps."""

    def __init__(self, times):
        super().__init__({})
        self._times = tuple(times)

    def next_tick(self, after_ms, calendar):
        """Return the next tape instant strictly after ``after_ms``."""
        for ts in self._times:
            if ts > after_ms:
                return ts
        return None


class _TapeEntry(Node):
    """Source root so ``Decider.prepare`` can classify the developmental entry.

    Compose requires a serving run document with one ``entry_read``. The
    tick never executes this node: ``EquityReplay`` overlays
    ``Data.decider`` after ``bundles_for`` returns.

    Parameters
    ----------
    params : dict
        ``since_ms`` (int or null), ``root`` / ``source`` / ``stream``
        (non-empty str) — the contract ``OnboardingRoot`` opens.

    Examples
    --------
    Construct the entry compose will classify::

        node = _TapeEntry("bars", {
            "since_ms": None, "root": "/tmp/ob", "source": "replay", "stream": "bars",
        })
        node.role
        # -> 'data'
    """

    role = "data"
    outputs = ("records",)
    _PARAMS = ("since_ms", "root", "source", "stream")

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify as the tick's one mutable read."""
        return "entry_read"

    @classmethod
    def serving_contract(cls, params, verified_run_evidence):
        """Bind the empty onboarding root compose opens, then overlays away."""
        return ServingContract(
            source_binding={
                "kind": "onboarding-stream",
                "root": params["root"],
                "source": params["source"],
                "stream": params["stream"],
            },
            entity_key_fields=("symbol",),
            event_time_field="asof_ms",
            digest_recipe={"kind": "stream-digest"},
        )

    @classmethod
    def validate_params(cls, params):
        """Require the contract fields; ``since_ms`` may be JSON null."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        for name in ("root", "source", "stream"):
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string")
        if params.get("since_ms") is not None:
            check_int_param(problems, "since_ms", params.get("since_ms"), ge=0)
        return problems

    def run(self, ctx, inputs):
        """Emit no rows — the overlay decider never asks this node to run."""
        return {"records": []}


class _TapeHead(Node):
    """Pure head so the served graph has a descendant of the entry.

    Parameters
    ----------
    params : dict
        None. ``notes`` is allowed.

    Examples
    --------
    Wire the entry through::

        node = _TapeHead("select", {})
        node.run(None, {"records": [{"symbol": "AAA"}]})
        # -> {'records': [{'symbol': 'AAA'}]}
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ()

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify as a pure input reader."""
        return "pure"

    @classmethod
    def validate_params(cls, params):
        """Refuse unknown knobs."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + ("notes",))
        return problems

    def run(self, ctx, inputs):
        """Pass records through."""
        return {"records": list(inputs.get("records") or [])}


class _PaperVenue:
    """PaperExecutor that records equity fill rows after LegPipeline submits."""

    def __init__(self, inner, owner):
        self._inner = inner
        self._owner = owner

    def submit(self, intent, permit, state):
        meta = self._owner._pending_by_id.get(intent.proposal.id)
        if meta is not None:
            px = Decimal(str(meta["price"]))
            self._inner.on_quote(Quote(
                instrument=meta["symbol"], bid=px, ask=px, mid=px, asof_ms=meta["asof_ms"],
            ))
        ack = self._inner.submit(intent, permit, state)
        self._owner._on_ack(intent, ack)
        return ack

    def __getattr__(self, name):
        return getattr(self._inner, name)


class EquityReplay:
    """Drive compose.bundles_for + ServeLoop around the equity book."""

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
            for field in (
                policy.fill_price_field,
                policy.forced_exit_price_field,
                policy.decision_price_field,
            ):
                if field in row:
                    _refuse_non_finite_price(row[field], field, row["asof_ms"])
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
        symbols = sorted(self._by_symbol)
        work = tempfile.mkdtemp(prefix="gate5-replay-")
        series_id = str(uuid.uuid4())
        try:
            run_dir, artifact, ob_root = _write_serving_run(work, policy)
            document = ServeDocument.from_obj(
                _serve_document(run_dir, series_id, symbols, times)
            )
            release = _release_for(
                run_dir, artifact, symbols, digest, self._source,
                document, tape.start_ms(), ob_root,
            )
            serve = ServeRoot(os.path.join(work, "serve"), series_id)
            lock = InstanceLock(serve.lock_path)
            lock.acquire()
            invocation = Invocation(
                armed=False, env_release_hash=None, once=False,
                max_ticks=max(len(times), 1),
            )
            try:
                schedule, data, decision, safety, execution, recording, observability = bundles_for(
                    document,
                    release,
                    None,
                    serve_root=serve,
                    secrets={},
                    invocation=invocation,
                    process_id="replay-1",
                    lock=lock,
                    journal_hook=_journal_noop,
                    tape=tape,
                )
            except ProductionError as exc:
                raise ConfigError(list(exc.problems)) from exc
            self._venue = _PaperVenue(
                PaperExecutor(
                    policy.paper_params(),
                    clock=schedule.clock,
                    scope=document.coordination.scope,
                ),
                self,
            )
            schedule = replace(schedule, cadence=_TapeCadence(times))
            data = Data(feed=data.feed, decider=self)
            execution = replace(execution, executor=self._venue)
            recording = replace(
                recording, id_source=ReleaseIdSource(release.release_hash),
            )
            loop = ServeLoop(
                document, release, schedule, data, decision, safety, execution,
                recording, observability, lock=lock, process_id="replay-1",
            )
            code = loop.run()
            failed = []
            for envelope in recording.ledger.scan(kind="tick"):
                body = envelope.get("body") or {}
                if body.get("status") == "failed" or body.get("status") == "refused":
                    err = body.get("error") or {}
                    cls = err.get("class") or body.get("status") or "tick"
                    text = err.get("text") or body.get("refusal_reason") or ""
                    failed.append(f"{cls}: {text}")
            leftover = list(self._queued) + list(self._pending_by_id)
            recording.ledger.close()
            lock.release()
            if self._fault is not None:
                raise self._fault
            if failed:
                raise ConfigError([
                    f"ServeLoop tick status failed: {failed[0]}"
                ])
            if leftover:
                raise ConfigError([
                    f"{len(leftover)} fill(s) queued but never submitted"
                ])
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
        except ConfigError as exc:
            if self._fault is None:
                self._fault = exc
            raise
        except (KeyError, TypeError, ValueError, ArithmeticError, ProductionError) as exc:
            wrapped = ConfigError([str(exc)])
            if self._fault is None:
                self._fault = wrapped
            raise wrapped from exc

    def candidates(self, head_outputs):
        """No extra candidates — equity size is already on the queued proposals."""
        return ()

    def quotes(self, head_outputs):
        """Return next-bar-open quotes so the tick's QuoteSet is non-empty."""
        try:
            policy = self._policy
            asof = self._asof
            found = []
            for symbol, seq in self._by_symbol.items():
                idx = self._index_of[symbol].get(asof)
                if idx is None:
                    continue
                bar = seq[idx]
                if self._halted(bar):
                    continue
                if policy.fill_price_field not in bar:
                    raise ConfigError([
                        f"bar missing {policy.fill_price_field!r} at asof_ms={asof!r}"
                    ])
                price = Decimal(str(bar[policy.fill_price_field]))
                quote = Quote(instrument=symbol, bid=price, ask=price, mid=price, asof_ms=asof)
                self._venue.on_quote(quote)
                found.append(quote)
            return found
        except ConfigError as exc:
            if self._fault is None:
                self._fault = exc
            raise
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            wrapped = ConfigError([str(exc)])
            if self._fault is None:
                self._fault = wrapped
            raise wrapped from exc

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
                nxt = index + 1
                if nxt >= len(self._by_symbol[symbol]):
                    for decision in incoming:
                        self.refused.append({
                            "symbol": symbol,
                            "asof_ms": bar["asof_ms"],
                            "lead": decision[self._policy.horizon_field],
                            "reason": "fill_bar_past_tape",
                        })
                else:
                    pending[nxt].extend(incoming)
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
            if isinstance(qty, bool) or isinstance(qty, str) or not number_ok(qty) or qty <= 0:
                raise ConfigError([
                    f"qty must be a positive number, got {qty!r} at asof_ms={bar['asof_ms']!r}"
                ])
            if policy.fill_price_field not in bar:
                raise ConfigError([
                    f"bar missing {policy.fill_price_field!r} at asof_ms={bar['asof_ms']!r}"
                ])
            if self._book.is_open(symbol, lead) and policy.same_lead_overlap == "override":
                lot = self._book.close_lot(symbol, lead)
                exit_side = "sell" if lot["side"] == "buy" else "buy"
                exit_px = bar[policy.forced_exit_price_field]
                exit_fee = (
                    policy.costs.sell_per_share(exit_px) if exit_side == "sell"
                    else policy.costs.buy_per_share(exit_px)
                ) * lot["qty"]
                self._queue_fill(
                    "exit", symbol, lead, exit_side, lot["qty"], exit_px, bar["asof_ms"],
                    exit_fee, index,
                )
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


def _refuse_non_finite_price(value, field, asof_ms):
    """Refuse a present non-finite price; None/'' stay for halt-skip quotes."""
    if value is None or value == "":
        return
    if isinstance(value, Decimal):
        ok = value.is_finite()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        ok = number_ok(value)
    else:
        return
    if ok:
        return
    raise ConfigError([
        f"{field} must be a finite number, got {value!r} at asof_ms={asof_ms!r}"
    ])


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


def _write_serving_run(work, policy):
    """Write the run dir compose.Decider.prepare loads, plus an empty onboarding root."""
    run_dir = os.path.join(work, "run")
    os.makedirs(run_dir, exist_ok=True)
    ob = OnboardingRoot.create(os.path.join(work, "ob"))
    artifact = os.path.join(run_dir, "policy")
    with open(artifact, "w", encoding="utf-8") as fh:
        json.dump(policy.to_obj(), fh)
    config = {
        "name": "intraday-equities-development-replay-serving",
        "pipeline": {
            "bars": {
                "uses": "intraday_equities.replay:_TapeEntry",
                "params": {
                    "since_ms": None,
                    "root": ob.root,
                    "source": "replay",
                    "stream": "bars",
                },
            },
            "select": {
                "uses": "intraday_equities.replay:_TapeHead",
                "inputs": {"records": "$bars.records"},
            },
        },
    }
    with open(os.path.join(run_dir, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return run_dir, artifact, ob.root


def _serve_document(run_dir, series_id, symbols, times):
    """Shadow ServeDocument compose.bundles_for accepts; cadence is overlaid to the tape."""
    universe = list(symbols) or ["AAA"]
    span = (int(times[-1]) - int(times[0]) + 1) if times else 1
    horizon_ms = max(span * 2, 1)
    renew_every_ms = 10_000
    renew_timeout_ms = 2_000
    ttl_ms = max(horizon_ms, 2 * (renew_every_ms + renew_timeout_ms) + 1)
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
            "proposer": {
                "uses": "intent-rows",
                "params": {
                    "output": "records",
                    "fields": {"instrument": "symbol", "side": "side", "qty": "qty"},
                    "ttl_ms": 86400000,
                    "default_tif": "ioc",
                },
            },
            "max_artifact_age": "P100000D",
        },
        "feed": {"uses": "replay"},
        "schedule": {
            "clock": {"uses": "replay"},
            "calendar": {"uses": "always-open"},
            "cadence": {"uses": "fixed-interval", "params": {"period_ms": 1000}},
            "dead_after_ms": horizon_ms,
            "max_staleness_ms": horizon_ms,
            "max_quote_age_ms": horizon_ms,
        },
        "guards": {},
        "execution": {"uses": "shadow", "submit_timeout_ms": 5000},
        "accounting": {"uses": "paper", "max_valuation_age_ms": horizon_ms},
        "arming": {"max_duration_s": 14400, "approval": {"uses": "deny-all"}},
        "coordination": {
            "scope": {"venue": "paper", "account": "replay"},
            "lease": {"uses": "process"},
            "ttl_ms": ttl_ms,
            "renew_every_ms": renew_every_ms,
            "renew_timeout_ms": renew_timeout_ms,
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


def _release_for(run_dir, artifact, symbols, digest, source_hash, document, created_ms, ob_root):
    """Build a ReleaseManifest whose feed_spec matches ``_TapeEntry``'s contract."""
    hex64 = digest
    keys = list(symbols) or ["AAA"]
    return ReleaseManifest(
        series_id=document.series_id,
        doc_hash=document.doc_hash,
        run_hash=hex64,
        serving_hash=hex64,
        artifacts={"policy": {"digest": artifact_digest(artifact), "timestamp_ms": created_ms}},
        classes={"replay": {"ref": "intraday_equities.replay:FillPolicy", "code_digest": hex64}},
        adapter={"name": "intraday_equities", "digest": hex64},
        feed_spec={
            "source_binding": {
                "kind": "onboarding-stream",
                "root": ob_root,
                "source": "replay",
                "stream": "bars",
            },
            "entity_key_fields": ["symbol"],
            "event_time_field": "asof_ms",
            "digest_recipe": {"kind": "stream-digest"},
            "required_keys": keys,
            "required_keys_digest": canonical_hash(keys),
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

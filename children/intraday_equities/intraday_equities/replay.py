"""Developmental equity replay policies over existing production seams (ADR-0117).

Gate 5a already proved ``ServeLoop`` + ``ReplayFeed`` + ``ReplayClock`` +
``PaperExecutor`` drive deterministic historical ticks; no generic hook
is missing and this module does not subclass ``ServeLoop``. It owns the
equity fill/overlap book (bar choice, next-bar-open timing, forced
exits, ``(symbol, lead)`` identity, Schwab costs). A synthetic
``ReplayAdapter.replay`` driver exists to exercise that book on bars;
it is not the production scheduler, ledger, or account fold. Phase 5
items 3–5 (crash/restart identity, post-fill solvency, ServeLoop
composition) remain unbuilt. Overlap/expiry is ADR-0117 **proposed**.

Every fill-model value is a field of ``configs/fill-policy.json``. This
file has no default for those knobs — a missing or unknown name refuses.
"""

from __future__ import annotations

import json
import os
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
from dskit.production.clock import ReplayClock
from dskit.production.executor import PaperExecutor
from dskit.production.records import Intent, Proposal, Quote, RiskVersion, SimulatedPermit
from dskit.production.state import TickState

from .nodes_capital import SchwabCostModel

__all__ = [
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
    )
    _VOCAB = {
        "order_type": ("market",),
        "rejections": ("none",),
        "halt_handling": ("skip",),
        "forced_exit_at": ("horizon_expiry",),
        "forced_exit_horizon_basis": ("fill",),
        "mark_source": ("fill_bar_open",),
        "same_lead_overlap": ("refuse",),
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
        if self._policy.same_lead_overlap == "refuse" and key in self._lots:
            return False
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


class ReplayAdapter:
    """Drive ``PaperExecutor`` with next-bar-open quotes and the overlap book.

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
        """Replay ``decisions`` over ``bars``. Return fills, skips, and refusals.

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
        policy = self._policy
        by_symbol = defaultdict(list)
        for bar in bars:
            by_symbol[bar[policy.symbol_field]].append(bar)
        for seq in by_symbol.values():
            seq.sort(key=lambda row: row["asof_ms"])

        fills, skipped, refused = [], [], []
        for decision in decisions:
            symbol = decision[policy.symbol_field]
            if symbol not in by_symbol:
                refused.append({
                    "symbol": symbol,
                    "asof_ms": decision["asof_ms"],
                    "lead": decision[policy.horizon_field],
                    "reason": "unknown_symbol",
                })
        for symbol, seq in by_symbol.items():
            self._replay_symbol(symbol, seq, decisions, fills, skipped, refused)
        fills.sort(key=lambda row: (
            row["asof_ms"], row["symbol"], row["lead"], 0 if row["kind"] == "exit" else 1,
        ))
        return {"fills": fills, "skipped": skipped, "refused": refused}

    def _replay_symbol(self, symbol, seq, decisions, fills, skipped, refused):
        """Walk one name's bars: exits, then entries, through ``PaperExecutor``."""
        policy = self._policy
        index_of = {bar["asof_ms"]: i for i, bar in enumerate(seq)}
        if len(index_of) != len(seq):
            raise ConfigError([
                f"duplicate asof_ms on {symbol!r}: each bar on a name must have a unique timestamp"
            ])
        pending = defaultdict(list)
        for decision in decisions:
            if decision[policy.symbol_field] != symbol:
                continue
            lead = decision[policy.horizon_field]
            if isinstance(lead, bool) or not isinstance(lead, int) or lead < 1:
                refused.append({
                    "symbol": symbol,
                    "asof_ms": decision["asof_ms"],
                    "lead": lead,
                    "reason": "lead",
                })
                continue
            decision_index = index_of.get(decision["asof_ms"])
            if decision_index is None:
                refused.append({
                    "symbol": symbol,
                    "asof_ms": decision["asof_ms"],
                    "lead": lead,
                    "reason": "unknown_decision_bar",
                })
                continue
            if policy.decision_price_field not in seq[decision_index]:
                refused.append({
                    "symbol": symbol,
                    "asof_ms": decision["asof_ms"],
                    "lead": lead,
                    "reason": "decision_price_field",
                })
                continue
            fill_index = decision_index + policy.fill_bar_offset
            if fill_index >= len(seq):
                refused.append({
                    "symbol": symbol,
                    "asof_ms": decision["asof_ms"],
                    "lead": lead,
                    "reason": "fill_bar_past_tape",
                })
                continue
            pending[fill_index].append(decision)

        clock = ReplayClock()
        venue = PaperExecutor(policy.paper_params(), clock=clock)
        book = HorizonBook(policy)
        digest = "a" * 64
        for index, bar in enumerate(seq):
            clock.time.set(bar["asof_ms"])
            halted = self._halted(bar)
            if halted and policy.halt_handling == "skip":
                for lead, _lot in book.expiring(symbol, index):
                    skipped.append({
                        "symbol": symbol,
                        "asof_ms": bar["asof_ms"],
                        "lead": lead,
                        "reason": "halted",
                    })
                for decision in pending.get(index, ()):
                    skipped.append({
                        "symbol": symbol,
                        "asof_ms": bar["asof_ms"],
                        "reason": "halted",
                    })
                continue
            if policy.same_tick_order == "exits_then_entries":
                self._process_exits(symbol, bar, index, book, venue, digest, fills)
                self._process_entries(
                    symbol, bar, index, pending.get(index, ()), book, venue, digest, fills, refused
                )
            else:
                raise ConfigError([
                    f"same_tick_order {policy.same_tick_order!r} has no dispatch"
                ])
        if policy.different_lead_overlap != "concurrent":
            raise ConfigError([
                f"different_lead_overlap {policy.different_lead_overlap!r} has no dispatch"
            ])
        for (sym, lead), lot in book.unclosed():
            refused.append({
                "symbol": sym,
                "asof_ms": seq[-1]["asof_ms"] if seq else None,
                "lead": lead,
                "qty": lot["qty"],
                "reason": "expiry_past_tape",
            })
            book.close_lot(sym, lead)

    def _halted(self, bar):
        """Return whether ``bar`` is halted, comparing by value (not identity)."""
        field = self._policy.halt_field
        if field not in bar:
            raise ConfigError([f"bar missing halt field {field!r} at asof_ms={bar.get('asof_ms')!r}"])
        flag = bar[field]
        if not isinstance(flag, bool):
            raise ConfigError([
                f"halt field {field!r} must be a JSON bool, got {flag!r} "
                f"at asof_ms={bar.get('asof_ms')!r}"
            ])
        return flag == self._policy.halted_true

    def _process_exits(self, symbol, bar, index, book, venue, digest, fills):
        """Force-exit lots whose expiry bar is this fill bar."""
        policy = self._policy
        price = bar[policy.forced_exit_price_field]
        for lead, lot in book.expiring(symbol, index):
            side = "sell" if lot["side"] == "buy" else "buy"
            ack = self._submit(
                venue, bar, symbol, side, lot["qty"], price, digest, f"exit-{symbol}-{lead}-{index}"
            )
            book.close_lot(symbol, lead)
            fee = (
                policy.costs.sell_per_share(price) if side == "sell" else policy.costs.buy_per_share(price)
            ) * lot["qty"]
            fills.append(self._fill_row("exit", symbol, lead, side, lot["qty"], price, bar["asof_ms"], fee, ack))

    def _process_entries(self, symbol, bar, index, incoming, book, venue, digest, fills, refused):
        """Open new lots after exits on this fill bar."""
        policy = self._policy
        price = bar[policy.fill_price_field]
        for decision in incoming:
            lead = decision[policy.horizon_field]
            qty = decision[policy.qty_field]
            side = decision[policy.side_field]
            if policy.costs.below_floor(price):
                refused.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "min_price",
                })
                continue
            if not book.open_lot(symbol, lead, qty, side, index):
                refused.append({
                    "symbol": symbol, "asof_ms": bar["asof_ms"], "lead": lead, "reason": "same_lead_open",
                })
                continue
            ack = self._submit(
                venue, bar, symbol, side, qty, price, digest, f"entry-{symbol}-{lead}-{index}"
            )
            fee = (
                policy.costs.buy_per_share(price) if side == "buy" else policy.costs.sell_per_share(price)
            ) * qty
            fills.append(self._fill_row("entry", symbol, lead, side, qty, price, bar["asof_ms"], fee, ack))

    def _submit(self, venue, bar, symbol, side, qty, price, digest, client_ref):
        """Quote the open (as bid=ask=mid) and submit one marketable order."""
        policy = self._policy
        px = Decimal(str(price))
        qty_d = Decimal(str(qty))
        quote = Quote(instrument=symbol, bid=px, ask=px, mid=px, asof_ms=bar["asof_ms"])
        venue.on_quote(quote)
        proposal = Proposal(
            id=client_ref,
            instrument=symbol,
            side=side,
            qty=qty_d,
            notional=None,
            limit=None,
            tif="ioc",
            expires_ms=bar["asof_ms"] + 1,
            reference_price=px,
            exposure=qty_d * px,
            direction="long" if side == "buy" else "short",
            confidence=0.0,
            prediction=0.0,
            baseline=0.0,
            expected_value=0.0,
            inputs_asof_ms=bar["asof_ms"],
            inputs_digest=digest,
            coverage_digest=digest,
            quote_asof_ms=bar["asof_ms"],
            quote_digest=digest,
            extra={"order_type": policy.order_type},
        )
        version = RiskVersion(economic_seq=1, executor_token=None, accounting_tokens=None)
        intent = Intent(
            client_ref=client_ref,
            decision_plan_id="replay",
            decision_plan_digest=digest,
            proposal=proposal,
            created_ms=bar["asof_ms"],
            authority_id="replay",
            release_hash=digest,
            inputs_asof_ms=bar["asof_ms"],
            inputs_digest=digest,
            coverage_digest=digest,
            quote_asof_ms=bar["asof_ms"],
            quote_digest=digest,
            evidence_asof_ms=bar["asof_ms"],
            evidence_digest=digest,
            risk_version=version,
            risk_state_digest=digest,
        )
        permit = SimulatedPermit(
            plan_id="replay",
            decision_plan_digest=digest,
            client_ref=client_ref,
            valid_until_ms=bar["asof_ms"] + 1,
        )
        ack = venue.submit(intent, permit, TickState(
            view=None, account=None, feed_status="live", feed_ages=(), calendar=None,
        ))
        if ack.status != "filled":
            raise ConfigError([
                f"replay fill model forbids rejections, got status={ack.status!r} "
                f"reason={ack.reason!r} for {client_ref}"
            ])
        return ack

    @staticmethod
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

    def _fill_only_end_ms(self):
        """First UTC instant after the one fill-only day following evidence_end."""
        return self._utc_day_end_ms(2)

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
        fill_end = self._fill_only_end_ms()
        late_decisions = [
            row for row in inputs["decisions"]
            if int(row["asof_ms"]) >= exclusive
        ]
        if late_decisions:
            raise ConfigError([
                f"evidence_end {self.params['evidence_end']!r} excludes "
                f"{len(late_decisions)} decision(s) at or after {exclusive}"
            ])
        late_bars = [
            bar for bar in inputs["bars"]
            if int(bar["asof_ms"]) >= fill_end
        ]
        if late_bars:
            raise ConfigError([
                f"evidence_end {self.params['evidence_end']!r} excludes "
                f"{len(late_bars)} bar(s) at or after fill-only end {fill_end}"
            ])
        return ReplayAdapter(self._policy).replay(list(inputs["bars"]), list(inputs["decisions"]))


NODE_KINDS = {
    "intraday_equities-development-replay": DevelopmentReplay,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

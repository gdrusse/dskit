"""A deterministic synthetic venue — the toolkit's reference adapter.

Three jobs, one implementation: (1) the toolkit's own tests exercise the
full Runner against it with zero heavy deps and zero disk data; (2)
``python -m dskit.pipeline synthetic`` runs it end-to-end as the
package's runnable demo; (3) the companion notebook walks it cell by
cell. It is also the smallest complete EXAMPLE of what an adapter must
provide — a real adapter (your adapter package) has exactly this
surface, with the application's own machinery behind each method.

The synthetic market is deliberately mispriced: each contract's true
probability ``q_true`` is BIMODAL (0.05 or 0.95 — near-certain worlds),
but the quoted mid is shrunk toward 0.5
(``mid = 0.5 + SHRINK * (q_true - 0.5)``) — a stylized favorite-longshot
bias, exaggerated so the edge is decisive at test-sized samples (an
honest bootstrap should CONFIRM this edge, not hover at p ≈ 0.2). A
model that knows ``q_true`` ("true-q") therefore beats the market
baseline out-of-sample; a model that just repeats the market
("market-copy") does not — giving tests both a deploy path and a
no-edge halt path. Everything derives from sha256 hashes of
``(seed, contract)``: no RNG state, byte-identical across machines.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib

from dskit.pipeline.base import (
    OPTIMIZER_KINDS,
    SINK_KINDS,
    register_optimizer_kind,
    register_sink_kind,
)
from dskit.pipeline.records import BinaryAccounting, MarketRecord, settle_position
from dskit.pipeline.registry import DEFAULT_REGISTRY

__all__ = [
    "MemoryTracker",
    "SyntheticBackend",
    "TEST_END_MS",
    "TRAIN_END_MS",
    "VAL_END_MS",
    "register_synthetic",
]

#: The synthetic clock: three equal windows on a fixed axis, so configs in
#: tests/notebooks can pin splits without magic numbers.
TRAIN_END_MS = 1_000_000
VAL_END_MS = 2_000_000
TEST_END_MS = 3_000_000

#: How far mids are shrunk toward 0.5 relative to the truth (the built-in
#: mispricing the "true-q" model exploits).
SHRINK = 0.4

_DEFAULT_INSTRUMENTS = ("SYN-A", "SYN-B", "SYN-C")


def _unit(tag) -> float:
    """Deterministic uniform in [0, 1) from a string tag."""
    digest = hashlib.sha256(tag.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _q_true(seed, contract) -> float:
    return 0.95 if _unit(f"q:{seed}:{contract}") >= 0.5 else 0.05


def _outcome(seed, contract) -> bool:
    return _unit(f"out:{seed}:{contract}") < _q_true(seed, contract)


def _mid(seed, contract) -> float:
    return 0.5 + SHRINK * (_q_true(seed, contract) - 0.5)


class _TrueQSignal:
    """The informed model: knows each contract's true probability."""

    def __init__(self, seed):
        self._seed = seed

    def q_hat(self, contract, p_mid, asof_ms, lead_frac=None):
        return _q_true(self._seed, contract)


class _MarketCopySignal:
    """The uninformed model (and the venue baseline): repeats the mid."""

    def q_hat(self, contract, p_mid, asof_ms, lead_frac=None):
        return float(p_mid)


class _InMemorySource:
    """DataSource seam over pre-built envelopes."""

    def __init__(self, by_instrument):
        self._by_instrument = by_instrument

    def records_for(self, instrument, start_ms, end_ms):
        for rec in self._by_instrument.get(instrument, ()):
            if start_ms <= rec.asof_ms <= end_ms:
                yield rec


class _DictSettlement:
    """SettlementSource seam over a pre-built outcome map."""

    def __init__(self, by_instrument):
        self._by_instrument = by_instrument

    def outcomes_for(self, instrument):
        return dict(self._by_instrument.get(instrument, {}))


class SyntheticBackend:
    """The reference adapter: every Backend surface, purely in memory.

    Knobs (all hash-material, read from the config): ``model.seed`` drives
    every deterministic draw; ``model.params["n_events"]`` (default 90) is
    events per instrument, spread evenly over the three windows;
    ``model.name`` picks "true-q" (informed) or "market-copy"
    (uninformed). One contract per event, so the event IS the cluster.
    """

    venue = "synthetic"
    supported_split_kinds = ("time", "random")
    supported_optimizer_kinds = ("equal-weight",)
    supported_transform_kinds = ()  # stream kinds are toolkit-registered

    def __init__(self, config):
        self._cfg = config
        self._seed = config.model.seed
        self._n_events = int(config.model.params.get("n_events", 90))
        names = config.data.instruments or _DEFAULT_INSTRUMENTS
        self._records, self._outcomes = {}, {}
        step = TEST_END_MS // (self._n_events + 1)
        for inst in names:
            recs, outs = [], {}
            for e in range(self._n_events):
                contract = f"{inst}-EV{e:03d}-C1"
                recs.append(
                    MarketRecord(
                        venue=self.venue,
                        instrument=inst,
                        contract=contract,
                        asof_ms=(e + 1) * step,
                        usable=True,
                        reason="ok",
                        group=f"{inst}-EV{e:03d}",
                        mid=_mid(self._seed, contract),
                        lead_frac=0.5,
                    )
                )
                outs[contract] = _outcome(self._seed, contract)
            self._records[inst] = tuple(recs)
            self._outcomes[inst] = outs

    # -- resolve hooks -----------------------------------------------------

    def discover_instruments(self, data_root):
        return tuple(sorted(self._records))

    def fingerprint(self, data_root, instruments, asof):
        return {
            inst: {
                "events": len(self._records.get(inst, ())),
                "max_ms": max(
                    (r.asof_ms for r in self._records.get(inst, ())), default=None
                ),
            }
            for inst in instruments
        }

    # -- seams -------------------------------------------------------------

    def data_source(self, ctx):
        return _InMemorySource(self._records)

    def baseline(self, ctx):
        name = self._cfg.validation.baseline
        if name != "market":
            raise ValueError(
                f"synthetic backend knows no baseline {name!r} — supported: ['market']"
            )
        return _MarketCopySignal()

    def accounting(self, ctx):
        return BinaryAccounting()

    def settlement(self, ctx):
        return _DictSettlement(self._outcomes)

    # -- venue stages ------------------------------------------------------

    def train(self, ctx):
        name = self._cfg.model.name
        if name == "true-q":
            return _TrueQSignal(self._seed)
        if name == "market-copy":
            return _MarketCopySignal()
        raise NotImplementedError(
            f"synthetic backend cannot train {name!r} — supported: "
            "['true-q', 'market-copy']"
        )

    def load(self, ctx):
        """Inference-only path: the synthetic families are stateless, so
        loading reconstructs the same signal training would build (the
        pinned ``model.artifact`` is accepted as-is — nothing on disk to
        verify)."""
        return self.train(ctx)

    def optimize(self, ctx, signal, survivors):
        """equal-weight: one lot of every test-window contract whose belief
        clears its mid."""
        positions = {}
        for inst in survivors:
            for rec in self._records[inst]:
                if ctx.config.splits.split_of(rec) != "test":
                    continue
                q = signal.q_hat(rec.contract, rec.mid, rec.asof_ms, rec.lead_frac)
                if q > rec.mid:
                    positions[rec.contract] = 1
        return {"kind": "equal-weight", "positions": positions}

    def backtest(self, ctx, signal, survivors):
        """Settle the equal-weight book through the shared arithmetic."""
        accounting = self.accounting(ctx)
        book = self.optimize(ctx, signal, survivors)["positions"]
        net = 0.0
        for inst in survivors:
            outcomes = self._outcomes[inst]
            for rec in self._records[inst]:
                if rec.contract not in book:
                    continue
                out = settle_position(
                    rec.contract,
                    qty=1,
                    cost=rec.mid,
                    fee=0.0,
                    payout_per_unit=accounting.payout_per_unit(outcomes[rec.contract]),
                )
                net += out.pnl
        return {"n_positions": len(book), "net_pnl": net}


def _equal_weight_validator(params):
    unknown = sorted(set(params) - {"notes"})
    return (
        [f"optimization.params: equal-weight takes no params, got {unknown}"]
        if unknown
        else []
    )


class MemoryTracker:
    """The toolkit's in-memory tracking sink (kind ``"memory"``): collects
    everything so tests and notebooks can assert on exactly what a real
    sink would have received."""

    instances = []

    def __init__(self, params):
        self.params = dict(params)
        self.logged_params = {}
        self.metrics = []  # (stage, {name: number})
        self.closed = False
        MemoryTracker.instances.append(self)

    def log_params(self, mapping):
        self.logged_params.update(mapping)

    def log_metrics(self, stage, mapping):
        self.metrics.append((stage, dict(mapping)))

    def close(self):
        self.closed = True


def register_synthetic(registry=DEFAULT_REGISTRY) -> None:
    """Idempotently register the synthetic venue, its optimizer kind, and
    the memory tracking sink."""
    if "synthetic" not in registry:
        registry.register("synthetic", SyntheticBackend)
    if "equal-weight" not in OPTIMIZER_KINDS:
        register_optimizer_kind("equal-weight", _equal_weight_validator)
    if "memory" not in SINK_KINDS:
        register_sink_kind("memory", lambda params: [], MemoryTracker)

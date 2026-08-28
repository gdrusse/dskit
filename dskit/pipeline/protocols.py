"""The six venue seams — what a backend must implement, and nothing more.

These are ``typing.Protocol``s (structural): a backend never imports or
inherits from this package — matching method signatures is membership.
That keeps any adapter's existing classes first-class and keeps this
package dependency-free in both directions (the toolkit names no venue;
an adapter package provides the seam-by-seam binding to its own
machinery).

The record/decision/fill payload TYPES stay ``object`` at the seams; the
venue-neutral envelope they normally carry is
:class:`~dskit.pipeline.records.MarketRecord` (see that module for the
schema ruling — envelopes over natives, accounting split isolated to the
:class:`Accounting` seam).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = [
    "Accounting",
    "DataSource",
    "ExecutionModel",
    "SettlementSource",
    "SignalProvider",
    "Sizer",
    "Tracker",
]


@runtime_checkable
class DataSource(Protocol):
    """Yields decision-epoch records for one instrument over a time window."""

    def records_for(self, instrument, start_ms, end_ms):
        """Yield record objects with ``asof_ms`` ascending in the window."""
        ...


@runtime_checkable
class SignalProvider(Protocol):
    """The model seam: a causal belief about one instrument at one instant."""

    def q_hat(self, instrument, p_mid, asof_ms, lead_frac=None):
        """Return the model's probability/return belief, using ONLY
        information available at ``asof_ms`` (the causality contract every
        implementation carries — leakage here invalidates everything
        downstream)."""
        ...


@runtime_checkable
class Sizer(Protocol):
    """The allocation seam: market state + beliefs -> intended positions."""

    def __call__(self, rows, **kwargs):
        """Return a batch-allocation result exposing per-event ``positions``
        (signed deltas). The ``allocate_fn`` contract."""
        ...


@runtime_checkable
class ExecutionModel(Protocol):
    """The fill seam: an intended order against observed liquidity."""

    def __call__(self, book, order):
        """Return a fill exposing ``filled``, ``vwap``, ``fee``,
        ``net_cost``, ``is_partial`` (the ``fill_fn`` contract; a live
        run passes its order router here)."""
        ...


@runtime_checkable
class SettlementSource(Protocol):
    """The outcome seam: what each position was ultimately worth."""

    def outcomes_for(self, instrument):
        """Return ``{position_id: outcome}`` for settled/closed positions.
        Binary venues: ``contract -> bool`` (settled YES?). Mark-to-market
        venues: the realization the accounting layer prices against."""
        ...


@runtime_checkable
class Tracker(Protocol):
    """The metric-tracking seam: where run metrics land, beyond the always-
    written run-dir artifacts. Sinks are declared in ``TrackingConfig`` and
    built by their registered factories (``register_sink_kind``)."""

    def log_params(self, mapping):
        """Record run identity/parameters — MERGE, never replace.

        The document driver calls this twice with DISJOINT keys: the run's
        identity at run start (undotted ``name``/``asof``/hashes/
        ``nodes``, so an aborted run still lands something), then the
        hyperparameters the run actually ran with once the nodes are done
        (dotted ``"<node>.<param.path>"`` — a search node's winner is not
        known until it has run). No key is ever sent twice, so a sink that
        refuses to restate a param is safe."""
        ...

    def log_metrics(self, stage, mapping):
        """Record one stage's numeric metrics (flat ``name -> number``)."""
        ...

    def close(self):
        """Flush and end the tracked run (always called, success or not)."""
        ...


@runtime_checkable
class Accounting(Protocol):
    """The valuation seam: a venue outcome -> currency per unit held.

    The ONE place binary settle-to-$1 and mark-to-market venues genuinely
    differ (design ruling, :mod:`dskit.pipeline.records`): everything
    downstream of this mapping is shared arithmetic
    (:func:`~dskit.pipeline.records.settle_position`). Implementations
    must REFUSE the other family's outcome type — coercing across the
    families hides venue-wiring bugs.
    """

    def payout_per_unit(self, outcome) -> float:
        """Currency one held unit pays given the venue outcome (a
        settled-YES bool for binary venues, the mark itself for
        mark-to-market ones)."""
        ...

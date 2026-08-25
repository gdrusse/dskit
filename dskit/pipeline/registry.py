"""The backend registry — how ``DataConfig.venue`` becomes running code.

Design ruling (2026-08-13 design session, question 2, revised for the
toolkit/adapter split): the toolkit ships the registry MECHANISM and ZERO
venues. An adapter package — a CHILD outside dskit, never a
``pipeline_<venue>`` sibling (ADR-0032; e.g. ``yourproject``) —
registers its venue tags into :data:`DEFAULT_REGISTRY` at import time —
so "registration" is exactly "import your adapter", and the toolkit
never imports venue code in any direction.

Binding is two-phase, because resolution itself needs venue knowledge:

1. ``factory(config) -> Backend`` — construction is CHEAP (no data, no
   heavy imports; adapters keep those function-local). Looked up by the
   ``DataConfig.venue`` tag.
2. The :class:`Backend` then serves three surfaces:

   * **resolve hooks** — ``discover_instruments`` (the venue's
     auto-universe rule, used when ``data.instruments`` is empty),
     ``fingerprint`` (the venue's data-snapshot identity, hashed into the
     pipeline hash), and the two ``supported_*`` declarations resolve
     checks the config against (a split kind or optimizer kind the venue
     cannot honor fails at resolve, loudly — this is where a causal venue
     refuses a randomized split);
   * **seams** — accessors returning the venue's
     :mod:`~dskit.pipeline.protocols` implementations, which the
     Runner's GENERIC stages (validate, stat-test) consume;
   * **venue stages** — ``train``/``optimize``/``backtest``: the stages
     whose machinery already exists per venue and must be REUSED, not
     reimplemented. The Runner orders and gates them; the backend owns
     their internals.

:class:`Backend` is a structural Protocol like every other seam — an
adapter never has to import this module to be a member.

Import cost: stdlib only.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Backend", "BackendRegistry", "DEFAULT_REGISTRY"]


@runtime_checkable
class Backend(Protocol):
    """What a venue adapter must provide (structural; see module docstring).

    ``ctx`` arguments are the Runner's ``RunContext`` (resolved pipeline +
    run dir + prior stage products); typed as ``object`` here to keep the
    dependency arrow pointing runner -> registry, never back.
    """

    venue: str
    #: Split kinds this venue can honor (e.g. ``("time",)`` for a causal
    #: market venue — refusing "random" is doctrine, not a limitation).
    supported_split_kinds: tuple
    #: Optimizer kinds this venue's optimize stage implements.
    supported_optimizer_kinds: tuple

    # -- resolve hooks -----------------------------------------------------
    def discover_instruments(self, data_root):
        """The venue's auto-universe rule: every instrument under
        ``data_root`` this backend could run (used when
        ``data.instruments`` is empty). Sorted tuple of ids."""
        ...

    def fingerprint(self, data_root, instruments, asof):
        """The venue's data-snapshot identity at ``asof`` — JSON-small,
        hash-material (per-instrument counts/extents). Must move whenever
        the data a run would consume changes."""
        ...

    # -- seams (consumed by the Runner's generic stages) -------------------
    def data_source(self, ctx):
        """The venue's :class:`~dskit.pipeline.protocols.DataSource`,
        yielding :class:`~dskit.pipeline.records.MarketRecord` envelopes
        (natives inside)."""
        ...

    def baseline(self, ctx):
        """The null :class:`~dskit.pipeline.protocols.SignalProvider`
        named by ``validation.baseline``."""
        ...

    def accounting(self, ctx):
        """The venue's :class:`~dskit.pipeline.protocols.Accounting`."""
        ...

    def settlement(self, ctx):
        """The venue's :class:`~dskit.pipeline.protocols.SettlementSource`."""
        ...

    # -- venue stages (ordered and gated by the Runner) --------------------
    def train(self, ctx):
        """Fit and return the venue's SignalProvider for this run — REUSING
        the venue's existing training machinery."""
        ...

    def load(self, ctx):
        """Return the SignalProvider for ``model.mode == "load"`` —
        inference-only from the config's pinned ``model.artifact``, never
        refitting. A venue without a persistable model family refuses
        loudly."""
        ...

    def optimize(self, ctx, signal, survivors):
        """Size intended positions on the gate-surviving instruments only.
        Returns a JSON-ready payload dict."""
        ...

    def backtest(self, ctx, signal, survivors):
        """Replay the test window on the surviving instruments through the
        venue's existing loop. Returns a JSON-ready payload dict."""
        ...


class BackendRegistry:
    """``venue tag -> factory(config) -> Backend``, fail-loud both ways."""

    def __init__(self):
        self._factories = {}

    def register(self, venue, factory) -> None:
        """Bind ``venue`` to ``factory``. A duplicate tag raises — two
        factories silently fighting over one venue is exactly the parallel
        path this package exists to prevent."""
        if not isinstance(venue, str) or not venue:
            raise ValueError(f"venue must be a non-empty string, got {venue!r}")
        if not callable(factory):
            raise ValueError(f"factory for {venue!r} must be callable, got {factory!r}")
        if venue in self._factories:
            raise ValueError(
                f"venue {venue!r} is already registered — refusing to shadow the "
                "existing backend (unregister deliberately, never implicitly)"
            )
        self._factories[venue] = factory

    def __contains__(self, venue) -> bool:
        return venue in self._factories

    def venues(self) -> tuple:
        """Registered tags, sorted."""
        return tuple(sorted(self._factories))

    def create(self, config):
        """Build the backend for ``config.data.venue``.

        Raises
        ------
        ValueError
            On an unknown venue, naming every registered tag — and the
            registration rule (import the adapter) — so the fix is in the
            message.
        """
        venue = config.data.venue
        factory = self._factories.get(venue)
        if factory is None:
            raise ValueError(
                f"no backend registered for venue {venue!r} — registered: "
                f"{sorted(self._factories)}. Registration happens when an "
                "adapter package is imported (`import yourproject` — your "
                "child package — registers the tags it serves)."
            )
        return factory(config)


#: The registry adapters register into on import. Tests build private
#: instances to stay isolated.
DEFAULT_REGISTRY = BackendRegistry()

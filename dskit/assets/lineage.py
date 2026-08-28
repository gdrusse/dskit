"""The Lineage Registry — one global graph, native in the engine.

ADR-0004: three producers (onboarding, execution, and whatever comes
later), ONE authoritative graph. An edge is an assertion by some
producer that ``dst`` derives from ``src``, stamped with provenance —
``phase`` (which layer asserted it: ``"onboarding"``, ``"execution"``),
``origin`` (who), ``at`` (when). Edges live in the store's append-only
event log as ``"event": "lineage"`` entries: the graph is immutable
history, and needs no new surface on the :class:`~dskit.assets.store.Store`
seam.

Consistent with ADR-0007, ``relation`` and ``phase`` are structure-only
free strings — the engine keeps the graph well-formed (endpoints
resolve, no duplicates, no cycles); what a relation MEANS belongs to the
producer that asserted it.

Edge direction reads ``src`` --(relation)--> ``dst``: the source is
upstream, the destination derives from it. ``parents`` walks upstream,
``children`` downstream; ``ancestors``/``descendants`` are their
transitive closures — the end-to-end questions the spec demands.

Import cost: stdlib + this package.
"""

from __future__ import annotations

from .base import AssetError, _check_str, _raise_if, utc_now
from .registry import Registry

__all__ = ["Lineage"]


class Lineage:
    """The lineage graph over one registry's store.

    Parameters
    ----------
    registry : Registry
        The registry whose store holds the graph; endpoint resolution
        and kind checks ride on it.

    Examples
    --------
    Add a lineage edge, add it again idempotently, and reject a cycle::

        import tempfile
        from dskit.assets.default_model import default_model
        from dskit.assets.registry import Registry
        from dskit.assets.store import FileStore
        m = default_model()
        reg = Registry(FileStore.create(tempfile.mkdtemp(), m), m)
        lin = Lineage(reg)
        run = reg.register("run_observation", {"name": "run-1"})
        out = reg.register("output", {"name": "signal"}, refs={"run": run})
        lin.add(run, out, relation="produced", phase="execution", origin="doctest")
        # -> True
        lin.add(run, out, relation="produced", phase="execution")   # idempotent
        # -> False
        lin.parents(out) == [run] and lin.descendants(run) == [out]
        # -> True
        lin.add(out, run, relation="loops", phase="execution")
        # -> dskit.assets.base.AssetError: invalid asset operation (1 problem):
        # ->   edge 2fa48efe1878... -> 493116b3745e... would create a cycle — lineage is a DAG
    """

    def __init__(self, registry):
        if not isinstance(registry, Registry):
            raise AssetError(
                [f"registry must be a Registry, got {type(registry).__name__}"]
            )
        self.registry = registry

    # -- writes ------------------------------------------------------------

    def add(self, src, dst, relation, phase, origin="") -> bool:
        """Assert one edge: ``dst`` derives from ``src``.

        Parameters
        ----------
        src, dst : str
            version_ids of records present in the store.
        relation : str
            What the edge asserts (``"produced"``, ``"input"``, ...) —
            free string, meaning owned by the asserting producer.
        phase : str
            Which lineage layer asserts it (``"onboarding"``,
            ``"execution"``, ...).
        origin : str
            Provenance: who asserted the edge.

        Returns
        -------
        bool
            True if the edge was appended; False if an identical
            ``(src, dst, relation, phase)`` edge already exists.

        Raises
        ------
        AssetError
            If an endpoint does not resolve, ``src == dst``, or the edge
            would create a cycle — the graph stays a DAG.
        """
        errors = []
        _check_str(errors, "relation", relation)
        _check_str(errors, "phase", phase)
        _check_str(errors, "origin", origin, non_empty=False)
        _raise_if(errors)
        # Endpoints must resolve (and be of kinds the model declares).
        self.registry.get(src)
        self.registry.get(dst)
        if src == dst:
            raise AssetError([f"edge {src[:12]}... -> itself is not lineage"])
        if any(
            e["src"] == src and e["dst"] == dst
            and e["relation"] == relation and e["phase"] == phase
            for e in self.edges()
        ):
            return False
        # A path dst ->* src already existing means this edge closes a loop.
        if src in self.descendants(dst):
            raise AssetError(
                [f"edge {src[:12]}... -> {dst[:12]}... would create a cycle — "
                 "lineage is a DAG"]
            )
        self.registry.store.append_event(
            {
                "event": "lineage", "src": src, "dst": dst,
                "relation": relation, "phase": phase,
                "origin": origin, "at": utc_now(),
            }
        )
        return True

    # -- reads -------------------------------------------------------------

    def edges(self, version_id=None) -> list:
        """Every edge, or every edge touching ``version_id``, in assert order.

        Returns
        -------
        list of dict
            ``{"src", "dst", "relation", "phase", "origin", "at"}`` each.
        """
        out = []
        for event in self.registry.store.iter_events():
            if event.get("event") != "lineage":
                continue
            if version_id is None or version_id in (event.get("src"), event.get("dst")):
                out.append({k: v for k, v in event.items() if k != "event"})
        return out

    def parents(self, version_id) -> list:
        """Direct upstream version_ids (what this derives from), sorted."""
        self.registry.get(version_id)
        return sorted({e["src"] for e in self.edges(version_id) if e["dst"] == version_id})

    def children(self, version_id) -> list:
        """Direct downstream version_ids (what derives from this), sorted."""
        self.registry.get(version_id)
        return sorted({e["dst"] for e in self.edges(version_id) if e["src"] == version_id})

    def ancestors(self, version_id) -> list:
        """Every upstream version_id, transitively, sorted."""
        return self._closure(version_id, upstream=True)

    def descendants(self, version_id) -> list:
        """Every downstream version_id, transitively, sorted."""
        return self._closure(version_id, upstream=False)

    def _closure(self, version_id, upstream) -> list:
        """BFS over the edge list, rebuilt per call — an O(edges) scan,
        priced for the tier-1 store's declared ~10^4 scale."""
        self.registry.get(version_id)
        step = {}
        for e in self.edges():
            key, val = (e["dst"], e["src"]) if upstream else (e["src"], e["dst"])
            step.setdefault(key, set()).add(val)
        seen, frontier = set(), [version_id]
        while frontier:
            nxt = set()
            for vid in frontier:
                nxt |= step.get(vid, set()) - seen
            seen |= nxt
            frontier = sorted(nxt)
        return sorted(seen)

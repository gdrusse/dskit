"""The fitted-transform family — state LEARNED from a declared split.

A fitted transform learns something from one split and applies it to the
others: a scaler's means, a selector's surviving columns, an encoder's
vocabulary. That is structurally forbidden in the pure-transform family,
whose causality guard DEPENDS on ``apply`` being pure — it re-runs the
method on truncated prefixes and refuses when the answer moves, which a
fitted transform trips by design. So this is an explicit SIBLING of that
family, never a slot in it (ADR-0040).

**Leakage is the one hard rule.** A transform fitted on validation rows
leaks invisibly: nothing fails, the scores just come out better. The
seam therefore makes "which split did you fit on" DECLARED and
checkable — ``fit_split``, the same shape the ``score`` role already
uses for ``split`` — and refuses at PLAN where the document can be read,
at run otherwise:

* under train mode ``fit_split`` is REQUIRED and must name a declared
  split;
* **a FITTING document with no splits section at all refuses at plan**,
  rather than quietly fitting on everything — a ``walkforward`` section
  counts, because its folds materialize their own cuts;
* under load mode nothing is fit, so none of the plan rules apply — and
  a ``fit_split`` that IS present is checked at run against the
  sidecar's record of what the restored state ACTUALLY saw, alongside
  :meth:`FittedTransform.state_problems`, the member's own half of the
  rule. A document can restate what a state is, never misdescribe it;
* under a CLUSTER-KEYED cut every fit-candidate row must carry an
  identity, or the fit refuses: a random split assigns by hashing the
  cluster, so identity-less rows all hash alike and land in ONE bucket
  — and a ``fit_split`` catching that bucket catches the whole stream.

**The ``rows`` port carries EVERY input row, transformed, in both
modes.** ``fit_split`` governs what the state is LEARNED FROM, never
what is emitted — the deliberate departure from the ``score`` role's
skip-outside-the-split precedent, because a scaler that emitted only its
fit slice would silently truncate the stream its downstream reads.
Applying a train-fit state to val and test rows is the REQUIRED
behaviour; the leak would be FITTING on them.

**Two hooks, and purity lives per hook.** :meth:`FittedTransform.fit`
answers a JSON-able state from the fit rows;
:meth:`FittedTransform.apply_state` projects rows through it and must be
PURE and ROW-INDEPENDENT. :attr:`purity_check` (default true, the
``causality_check`` idiom) is a mechanical SCREEN, not a proof: it
re-applies ``apply_state`` to a sampled row ALONE and refuses when the
answer differs from that row's answer in the full call — which catches
the family's classic leak, an ``apply_state`` that recomputes a
statistic over the rows it was handed. The comparison is NaN-EQUAL
(:func:`_same`), because marking a value absent the way the rest of this
repo marks one is not drift. It cannot prove purity, and turning it off
is a decision the document owns.

:class:`ApplyTransform` (kind ``apply-transform``) projects a SECOND
stream through a carrier this family emits. ONE apply kind serves every
member, so a document that scales its training rows and then scales its
serving rows wires the same carrier twice rather than fitting twice.

Import cost: stdlib only — this is tier-1, the ``codec.py`` /
``observations.py`` precedent. The numerics a member needs live in the
member; nothing here imports a library.
"""

from __future__ import annotations

import json
import math
import os
from abc import abstractmethod

from dskit.pipeline.document import SPLIT_NAMES, is_node_ref
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    TrainableNode,
    class_ref,
    reject_unknown_params,
)
from dskit.pipeline.records import (
    ASOF_FIELD,
    CLUSTER_FIELD,
    CONTRACT_FIELD,
    cluster_of,
    number_ok,
)
from dskit.pipeline.split_policy import SplitFrame

__all__ = [
    "ApplyTransform",
    "DEFAULT_ORDER_FIELD",
    "DEFAULT_PURITY_CHECK",
    "FittedTransform",
    "NODE_KINDS",
    "SIDECAR_NAME",
    "Standardize",
    "TransformCarrier",
    "register",
]

#: The file a fitted state is persisted as, under the node's artifact
#: directory. Named once: the writer and the loader read this name, and
#: a pin may point at either the file or the directory holding it.
SIDECAR_NAME = "fitted.json"

#: The row field naming its DECISION INSTANT. Fit rows are ordered by it
#: (so a fit that depends on row order is reproducible) and the split
#: frame is built from it (so a stream with a foreign vocabulary is cut
#: on the instant it actually carries). Read from the ENVELOPE's own
#: name: the numpy pack defaults to the same field for the same rows,
#: and a fitted transform wired downstream of a window node whose
#: default was retuned would cut on a field its rows no longer carry.
DEFAULT_ORDER_FIELD = ASOF_FIELD

#: The purity screen is ON unless the document says otherwise.
DEFAULT_PURITY_CHECK = True


def _field(row, name):
    """Read one field of a row, attr-or-key (the ``kinds_flow`` convention)."""
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _numeric(value):
    """``value`` as a float by the envelope's own number rule, else ``None``."""
    return float(value) if number_ok(value) else None


def _same(a, b):
    """Say whether two transformed values agree, a NaN counting as itself.

    NaN-as-absent is this repo's convention (``libs/numpy.py`` emits
    warm-up NaNs), and ``nan == nan`` is False — so a plain ``==`` over
    two transformed rows reads a member that MARKS an absence as one
    that read the rows it was handed. The screen must widen what
    compares equal without widening what passes, so this recurses into
    the containers a row is built from and falls back to ``==``
    everywhere else. The numpy half solves the same problem with
    ``_prefix_equal``; tier-1 cannot import numpy, hence the stdlib
    twin.
    """
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return len(a) == len(b) and all(
            key in b and _same(value, b[key]) for key, value in a.items()
        )
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return (
            isinstance(b, type(a))
            and len(a) == len(b)
            and all(_same(x, y) for x, y in zip(a, b))
        )
    return a == b


def _sample_positions(n):
    """Choose the purity screen's probe positions — deterministic, no RNG."""
    return sorted({0, n // 2, n - 1}) if n else []


def _carrier_row_problems(carrier, rows):
    """Ask a carrier for its member's row rule; silent when it has none."""
    ask = getattr(carrier, "row_problems", None)
    return list(ask(rows)) if callable(ask) else []


#: The split KIND whose assignment is a pure function of the row's
#: cluster. Named here because the leak it causes is this module's to
#: refuse; ``tests/pipeline/test_fitted.py`` pins it against what the
#: configs actually do, so the name cannot drift from the behaviour.
_CLUSTER_KEYED_KIND = "random"


def _assigns_by_cluster(splits):
    """Say whether a split assignment READS the row's cluster (bool).

    Two shapes do, and both make an identity-less row unassignable: the
    random family hashes the cluster by construction, and a time split
    under an event policy resolves the event's bounds by cluster
    (``needs_event_bounds`` — the driver asks the same way).
    """
    return (getattr(splits, "kind", None) == _CLUSTER_KEYED_KIND
            or bool(getattr(splits, "needs_event_bounds", False)))


class TransformCarrier:
    """A fitted transform bound to its state — what ``transform`` carries.

    The pairing a downstream consumer needs and nothing more: the class
    that knows how to project, and the state it learned. Calling
    :meth:`apply` runs the SAME code the fitting node ran, screen
    included, so a second stream cannot drift from the first.

    Parameters
    ----------
    node : FittedTransform
        The node that fitted the state; its ``apply_state`` and params
        are what :meth:`apply` uses.
    state : dict
        The fitted state, JSON-able.

    Examples
    --------
    Project a second stream through a carrier a fit produced::

        carrier = fitted.run(ctx, {"rows": train_rows})["transform"]
        served = carrier.apply(live_rows)
    """

    __slots__ = ("_node", "state")

    def __init__(self, node, state):
        self._node = node
        self.state = state

    @property
    def node_class(self):
        """The class this state belongs to (a type)."""
        return type(self._node)

    def apply(self, rows):
        """Project ``rows`` through the carried state.

        Parameters
        ----------
        rows : list
            The stream to transform.

        Returns
        -------
        list
            One transformed row per input row, in order.

        Raises
        ------
        ValueError
            When the transform breaks its own contract — a row count
            that moved, or a purity screen that caught row dependence.
        """
        return self._node.applied(self.state, list(rows))

    def row_problems(self, rows):
        """Ask the fitting node whether it can project ``rows``.

        Parameters
        ----------
        rows : list
            The second stream, before anything is projected.

        Returns
        -------
        list of str
            Whatever :meth:`FittedTransform.row_problems` answers — the
            member's own rule, reaching the stream it never fitted.
        """
        return list(self._node.row_problems(rows))

    def __repr__(self):
        """Name the class and the state's keys — never the values."""
        return f"TransformCarrier({self.node_class.__name__}, {sorted(self.state)})"


class FittedTransform(TrainableNode):
    """A transform whose state is LEARNED from a declared split.

    Abstract: a member implements :meth:`fit` and :meth:`apply_state` and
    inherits everything else — the split selection, the persistence, the
    restore, the purity screen and the metrics. It subclasses
    :class:`~dskit.pipeline.node.TrainableNode` and overrides NEITHER
    template method, so ``mode`` is handled once, in one place, and no
    member or consumer of this family ever sees it (ADR-0038/0040).

    Parameters
    ----------
    params : dict
        ``fit_split`` (which split the state is learned from; required
        under train mode), ``order_field`` (str, default ``"asof_ms"`` —
        the field carrying each row's decision instant, which is both
        what fit rows are ordered by and what the split cuts on),
        ``purity_check`` (bool, default ``True``), plus whatever the
        member declares.

    Examples
    --------
    A member is two methods; the base is the rest::

        class Demean(FittedTransform):
            def fit(self, rows, params):
                return {"mean": sum(r["x"] for r in rows) / len(rows)}

            def apply_state(self, state, rows, params):
                return [{**r, "x": r["x"] - state["mean"]} for r in rows]

        node = Demean("demean", {"fit_split": "train"})
        out = node.run(ctx, {"rows": rows})
        # -> {"transform": ..., "rows": [...], "metrics": {...}}
    """

    role = "fitted_transform"
    outputs = ("transform", "rows", "metrics")

    _PARAMS = ("fit_split", "order_field", "purity_check")

    # -- the knobs ---------------------------------------------------------

    def fit_split(self):
        """Name the split the state is learned from, or ``None``."""
        return self.params.get("fit_split")

    def order_field(self):
        """Name the field carrying a row's decision instant (str).

        :meth:`frame_of` cuts on it, and the fit rows are sorted by it —
        with ONE stated exception. When the document DECLARES this knob,
        a fit row whose value cannot be read refuses by name. When it
        does not, the answer is the envelope's own
        :data:`DEFAULT_ORDER_FIELD` — a guess, not a promise — and a
        CLUSTER-KEYED cut reads no instant at all, so unreadable values
        leave the fit rows in the input stream's order rather than
        refusing a run whose split never wanted them.
        """
        return self.params.get("order_field", DEFAULT_ORDER_FIELD)

    def purity_check(self):
        """Say whether the purity screen runs (bool)."""
        return bool(self.params.get("purity_check", DEFAULT_PURITY_CHECK))

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        The split NAME is checked here; whether the document declares
        that split, and whether this node needed one at all, are plan-time
        questions the planner answers because only it can see the
        document.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        split = params.get("fit_split")
        if split is not None and not is_node_ref(split) and split not in SPLIT_NAMES:
            problems.append(
                f"fit_split must name one of {list(SPLIT_NAMES)} — the split "
                f"the state is LEARNED from, got {split!r}"
            )
        field = params.get("order_field", DEFAULT_ORDER_FIELD)
        if not is_node_ref(field) and (not isinstance(field, str) or not field):
            problems.append(f"order_field must be a non-empty string, got {field!r}")
        flag = params.get("purity_check", DEFAULT_PURITY_CHECK)
        if not is_node_ref(flag) and not isinstance(flag, bool):
            problems.append(f"purity_check must be a bool, got {flag!r}")
        return problems

    def validate_common_inputs(self, inputs):
        """Problems that hold in either mode, empty when none.

        Parameters
        ----------
        inputs : dict
            ``rows`` (the stream) and the optional ``artifact_path`` pin.

        Returns
        -------
        list of str
            One problem when ``rows`` is not a list, the member's own
            :meth:`row_problems`, and the wired-pin check every
            artifact-restoring node shares.
        """
        problems = []
        rows = (inputs or {}).get("rows")
        if not isinstance(rows, list):
            problems.append(
                "rows must be a list of rows (a one-shot iterable is refused, "
                f"never consumed by validation), got {type(rows).__name__}"
            )
        else:
            problems.extend(self.row_problems(rows))
        problems.extend(
            self.pin_port_problems(
                inputs, "artifact_path",
                hint="wire the run dir's fitted sidecar, or pin it node-level",
            )
        )
        return problems

    def row_problems(self, rows):
        """Ways ``rows`` break THIS member's own shape rule; none by default.

        Asked in TWO places: when this node validates its own stream,
        and when :class:`ApplyTransform` validates the SECOND stream a
        carrier of this class was wired to. One rule, both doorways —
        otherwise the sibling half of the family projects unvalidated
        rows and dies at execute inside ``apply_state``, naming neither
        the node nor the port.

        Parameters
        ----------
        rows : list
            The stream, already known to be a list.

        Returns
        -------
        list of str
            One problem per broken row; empty when the member can
            project them.
        """
        return []

    # -- the hooks a member implements -------------------------------------

    @abstractmethod
    def fit(self, rows, params):
        """Learn this transform's state from the fit split's rows.

        Parameters
        ----------
        rows : list
            The rows of the declared ``fit_split``. Never empty — an
            empty fit split is refused before this is called. They
            arrive in :meth:`order_field` order whenever every one of
            them carries a readable value there; a DECLARED
            ``order_field`` they do not refuses rather than degrading,
            and a merely DEFAULTED one under a cluster-keyed cut (which
            consults no instant) leaves them in the input stream's
            order. A member whose fit depends on order should say so.
        params : dict
            ``self.params``, passed through for convenience.

        Returns
        -------
        dict
            The state, JSON-able: it is written to the run's artifact
            and restored verbatim under load mode.
        """
        raise NotImplementedError

    @abstractmethod
    def apply_state(self, state, rows, params):
        """Project rows through a fitted state.

        MUST be pure and ROW-INDEPENDENT: the answer for a row may
        depend on that row and on ``state``, and on nothing else. The
        purity screen re-applies this method to a sampled row ALONE and
        refuses when the answer moves — which is what catches the
        family's classic leak, a method that recomputes a statistic over
        the rows it was handed.

        Parameters
        ----------
        state : dict
            What :meth:`fit` learned, or what load mode restored.
        rows : list
            EVERY row of the input stream, whatever split it came from.
        params : dict
            ``self.params``, passed through for convenience.

        Returns
        -------
        list
            One transformed row per input row, in the same order.
        """
        raise NotImplementedError

    def state_problems(self, state):
        """Ways a RESTORED state contradicts this document; none by default.

        The member's half of "a document may restate what a state is,
        never misdescribe it". The base already checks the two facts it
        owns — the class that fitted the state and the split it saw —
        but only the member knows whether its own knobs DESCRIBE the
        state or merely sit beside it. A knob that ``apply_state`` never
        reads is exactly where train/serve skew hides: nothing differs,
        nothing fails, and the served rows are quietly wrong.

        Parameters
        ----------
        state : dict
            The restored state, as :meth:`fit` returned it.

        Returns
        -------
        list of str
            One problem per disagreement; empty when the document and
            the state describe the same transform.
        """
        return []

    def state_metrics(self, state):
        """Numeric metrics describing a fitted state; none by default.

        Parameters
        ----------
        state : dict
            The fitted state.

        Returns
        -------
        dict
            Extra entries for the ``metrics`` output.
        """
        return {}

    # -- the two mode hooks (ADR-0038 dispatches to these) -----------------

    def run_train(self, ctx, inputs):
        """Fit on the declared split, apply to everything, persist.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; its ``splits`` decide which rows are fit on.
        inputs : dict
            ``rows``, the whole stream.

        Returns
        -------
        dict
            ``transform`` (a :class:`TransformCarrier`), ``rows`` (every
            input row, transformed) and ``metrics``.

        Raises
        ------
        ValueError
            When ``fit_split`` is absent, names a split the run did not
            materialize, or matches no row.
        """
        rows = inputs["rows"]
        fit_rows = self._fit_rows(ctx, rows)
        state = self._checked_state(self.fit(fit_rows, self.params))
        path = self.write_artifact(ctx, SIDECAR_NAME, {
            "node_class": class_ref(type(self)),
            "fit_split": self.fit_split(),
            "n_fit_rows": len(fit_rows),
            "state": state,
        })
        self.log.info(
            "fitted on %d of %d row(s) from split %r; state written to %s",
            len(fit_rows), len(rows), self.fit_split(), path,
        )
        return self._emit(state, rows, len(fit_rows))

    def run_load(self, ctx, inputs):
        """Restore a fitted state and apply it — never fit.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused — nothing about a restore depends on
            the run's splits, which is the whole point of the pin.
        inputs : dict
            ``rows``, and optionally a wired ``artifact_path``.

        Returns
        -------
        dict
            The same three outputs a fit returns, with ``n_fit_rows`` 0.

        Raises
        ------
        ValueError
            When nothing pins an artifact, when the sidecar belongs to
            another class, when a declared ``fit_split`` contradicts
            what the state actually saw, or when the member's own knobs
            misdescribe the restored state.
        """
        payload = self._sidecar(
            self.pinned_artifact(
                wired=(inputs or {}).get("artifact_path"),
                missing="mode='load' needs the artifact this transform was "
                        "fitted into — pin it node-level, or wire artifact_path",
            )
        )
        rows = inputs["rows"]
        self.log.info(
            "restored a state fitted on %s row(s) of split %r",
            payload.get("n_fit_rows"), payload.get("fit_split"),
        )
        return self._emit(payload["state"], rows, 0)

    # -- the base's own machinery ------------------------------------------

    def applied(self, state, rows):
        """Project ``rows`` through ``state``, held to the contract.

        The single path every projection takes — the fit's own emission,
        a load's, and :meth:`TransformCarrier.apply` — so the row-count
        contract and the purity screen cannot be true of one and not the
        others.

        Parameters
        ----------
        state : dict
            The fitted state.
        rows : list
            The stream to transform.

        Returns
        -------
        list
            One transformed row per input row.

        Raises
        ------
        ValueError
            When the row count moved, or the purity screen caught row
            dependence.
        """
        out = list(self.apply_state(state, rows, self.params))
        if len(out) != len(rows):
            raise ValueError(
                f"{self.key}: apply_state returned {len(out)} row(s) for "
                f"{len(rows)} — one row out per row in; a transform that "
                "drops rows would silently truncate the stream its "
                "downstream reads (filter them upstream instead)"
            )
        if self.purity_check():
            self._screen(state, rows, out)
        return out

    def _screen(self, state, rows, out):
        """Re-apply to a sampled row alone and refuse any drift."""
        for i in _sample_positions(len(rows)):
            alone = list(self.apply_state(state, [rows[i]], self.params))
            if len(alone) == 1 and _same(alone[0], out[i]):
                continue
            raise ValueError(
                f"{self.key}: apply_state is not row-independent — row {i} "
                "transformed differently on its own than it did in the full "
                "call, which means the method reads the ROWS IT WAS HANDED "
                "and not only the fitted state. That is this family's classic "
                "leak: the answer for a serving row would depend on which "
                "other rows happened to arrive with it. Fix apply_state; "
                "purity_check=false skips this screen, and turning it off is "
                "a decision the document owns."
            )

    def _emit(self, state, rows, n_fit_rows):
        """Build the three outputs from a state and the whole stream."""
        transformed = self.applied(state, rows)
        metrics = {"n_rows": len(transformed), "n_fit_rows": n_fit_rows}
        metrics.update(self.state_metrics(state))
        return {
            "transform": TransformCarrier(self, state),
            "rows": transformed,
            "metrics": metrics,
        }

    def frame_of(self, row):
        """Build the frame the run's splits assign this row by.

        The instant comes from the DECLARED :meth:`order_field`, never a
        hardcoded name: the pack's other half exists so a stream with a
        foreign vocabulary can enter, and a fitted transform that read
        ``asof_ms`` regardless would put every such row in NO split and
        then blame the split bounds.

        The instant is held to :func:`_numeric` on the way in, the same
        rule ``_ordered`` sorts the fit rows by: this module must have
        ONE answer about what the declared order field may carry. A
        string timestamp — the ordinary shape of a CSV- or table-sourced
        foreign stream — handed straight to a time cut died as a bare
        ``TypeError`` naming neither the node nor the field; unreadable
        becomes ``None`` here and :meth:`_refuse_unassignable` names it.

        A cluster-keyed cut reads only the identity, BY DESIGN, so the
        instant half of the frame is never consulted there and an
        unreadable one is no error — the one case in which the fit rows
        may reach :meth:`fit` in the stream's order instead of this
        field's (see :meth:`order_field`).

        The identity is :func:`~dskit.pipeline.records.cluster_of` — the
        envelope's own rule, IMPORTED rather than restated. It matters
        which: an envelope publishes the derived ``cluster`` as a
        property, but the toolkit's feature rows carry the RAW
        ``group``/``contract`` fields instead, so a frame reading only
        ``cluster`` fell through to the per-ROW contract and cut a
        cluster-keyed run by an identity no other split-assignment site
        in the engine uses — every event straddling the fit boundary,
        silently.

        Parameters
        ----------
        row : dict or object
            One input row, read attr-or-key.

        Returns
        -------
        dskit.pipeline.split_policy.SplitFrame
            The frame handed to ``split_of``; its instant is the row's
            own value when that value is a real finite number, and
            ``None`` when the field carries something no cut can read.
        """
        instant = _field(row, self.order_field())
        return SplitFrame(
            instant if _numeric(instant) is not None else None, cluster_of(row)
        )

    def _fit_rows(self, ctx, rows):
        """Select the declared split's rows, ordered — or refuse by name."""
        split = self.fit_split()
        if split is None:
            raise ValueError(
                f"{self.key}: fit_split is required when this node FITS — an "
                "undeclared fit split is a leak nobody can see, so it is "
                "stated in the document or not run at all"
            )
        splits = getattr(ctx, "splits", None)
        if splits is None:
            raise ValueError(
                f"{self.key}: fit_split={split!r} names a split but the run "
                "materialized none — a fitted transform with no splits would "
                "fit on EVERYTHING, which is the leak this knob exists to "
                "refuse"
            )
        frames = [self.frame_of(row) for row in rows]
        self._refuse_unassignable(splits, rows, frames)
        keep = [row for row, frame in zip(rows, frames)
                if splits.split_of(frame) == split]
        if not keep:
            raise ValueError(
                f"{self.key}: fit_split={split!r} matched no row of the "
                f"{len(rows)} wired — each row's split is read from its "
                f"{self.order_field()!r} instant and its cluster identity "
                f"('cluster', {CLUSTER_FIELD!r} or {CONTRACT_FIELD!r}), so "
                "check those before the cuts. Fitting on nothing "
                "would emit a state no reader can question, so it refuses "
                "instead"
            )
        return self._ordered(keep)

    def _refuse_unassignable(self, splits, rows, frames):
        """Refuse a row the run's splits cannot honestly place.

        Whichever half of the frame this run's cut READS must be
        readable, and the two halves fail differently.

        Under a CLUSTER-KEYED cut every row without a USABLE identity
        hashes the same string, so they all land in ONE bucket — and
        when that bucket is ``fit_split`` the fit silently sees the
        whole stream, val and test included, with ordinary-looking
        metrics and no refusal anywhere. That is the exact leak this
        family exists to make impossible, so it is refused by name.

        Usable, not merely present: ``""`` and ``0`` hash exactly the
        way a missing value does, so :func:`~dskit.pipeline.records.
        cluster_of` has already dropped them and the check reads its
        answer rather than testing the raw field.

        Under a TIME cut the instant is what is read, and a value the
        bounds cannot compare used to reach ``split_of`` and die there
        as a bare ``TypeError`` — naming neither this node, nor the
        port, nor the declared field it came from.
        """
        if _assigns_by_cluster(splits):
            for i, frame in enumerate(frames):
                if frame.cluster is not None:
                    continue
                raise ValueError(
                    f"{self.key}: row {i} carries no usable split identity "
                    f"(none of 'cluster', {CLUSTER_FIELD!r}, "
                    f"{CONTRACT_FIELD!r} holds a non-empty string) and this "
                    "run cuts BY CLUSTER — every such row is assigned by "
                    "hashing the same unusable value, so they all land in "
                    "ONE split and a fit_split that catches them fits on the "
                    "WHOLE stream, val and test included. Carry the identity "
                    "onto the rows, or cut this run on time"
                )
            return
        field = self.order_field()
        for i, frame in enumerate(frames):
            if frame.asof_ms is not None:
                continue
            raise ValueError(
                f"{self.key}: row {i} carries no readable instant under its "
                f"declared order_field {field!r} (got "
                f"{_field(rows[i], field)!r}) and this run cuts ON TIME — a "
                "value the cuts cannot compare places the row in NO split, "
                "so the fit would quietly proceed on whatever was left. "
                "Declare the field these rows carry their instant under, or "
                "convert it to epoch milliseconds upstream"
            )

    def _declares_order(self):
        """Say whether the order field was CHOSEN, not defaulted to the envelope's."""
        return "order_field" in self.params or self.order_field() != DEFAULT_ORDER_FIELD

    def _ordered(self, rows):
        """Fit rows in order: refuse an unreadable DECLARED field, else stream order."""
        field = self.order_field()
        keyed = [(_numeric(_field(row, field)), i, row) for i, row in enumerate(rows)]
        unreadable = next((i for key, i, _row in keyed if key is None), None)
        if unreadable is None:
            return [row for _key, _i, row in sorted(keyed, key=lambda t: (t[0], t[1]))]
        if not self._declares_order():
            # Nothing was promised: the envelope's own name is a GUESS this
            # module makes, and a cluster-keyed cut consults no instant at
            # all, so the stream's order is the honest answer.
            return rows
        raise ValueError(
            f"{self.key}: row {unreadable} of the {self.fit_split()!r} fit "
            f"slice carries no readable value under its DECLARED order_field "
            f"{field!r} (got {_field(rows[unreadable], field)!r}) — fit rows "
            "are handed to fit() in that order, so a value this cannot sort "
            "on would silently degrade the fit to STREAM order and the state "
            "this run persists could not be reproduced. Declare the field "
            "these rows carry their instant under, or convert it upstream"
        )

    def _checked_state(self, state):
        """Hold a fitted state to its contract: a JSON-able dict."""
        if not isinstance(state, dict):
            raise ValueError(
                f"{self.key}: fit() must return a dict of JSON-able state, got "
                f"{type(state).__name__} — the state is persisted verbatim and "
                "restored under mode='load'"
            )
        try:
            json.dumps(state, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.key}: fit() returned a state that is not JSON-able "
                f"({exc}) — a state no artifact can hold is a state serving "
                "can never restore"
            ) from exc
        return state

    def _sidecar(self, ref):
        """Read and verify the sidecar behind an artifact reference."""
        path = os.path.join(ref, SIDECAR_NAME) if os.path.isdir(ref) else ref
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"{self.key}: cannot read the fitted state at {path}: {exc}"
            ) from exc
        declared = class_ref(type(self))
        if payload.get("node_class") != declared:
            raise ValueError(
                f"{self.key}: {path} records node_class "
                f"{payload.get('node_class')!r}, not {declared!r} — wrong "
                "artifact for this node; a state means nothing apart from the "
                "class that projects with it"
            )
        split = self.fit_split()
        if split is not None and payload.get("fit_split") != split:
            raise ValueError(
                f"{self.key}: fit_split={split!r} but the restored state was "
                f"fitted on {payload.get('fit_split')!r} — a document may "
                "restate what a state saw, never misdescribe it"
            )
        if not isinstance(payload.get("state"), dict):
            raise ValueError(
                f"{self.key}: {path} carries no fitted state"
            )
        problems = self.state_problems(payload["state"])
        if problems:
            raise ValueError(
                f"{self.key}: {'; '.join(problems)} — a document may restate "
                "what a state is, never misdescribe it"
            )
        return payload


class ApplyTransform(Node):
    """Project a SECOND stream through a wired carrier (kind ``apply-transform``).

    ONE apply kind serves every member of the family: a document that
    fits a scaler on training rows and needs the same scaling on another
    stream wires the fit node's ``transform`` output here rather than
    fitting twice. Role ``transform`` — nothing is learned, so nothing
    about a split matters.

    Parameters
    ----------
    params : dict
        None: the carrier already holds everything.

    Examples
    --------
    Scale a second stream with the state the first fit learned::

        node = ApplyTransform("scale_test", {})
        out = node.run(ctx, {"transform": carrier, "rows": test_rows})
        # -> {"rows": [...]}
    """

    role = "transform"
    outputs = ("rows",)

    _PARAMS = ()

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none — this kind has no knobs.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem naming any knob that was set.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        return problems

    def validate_inputs(self, inputs):
        """Problems with ``inputs``, empty when none.

        Parameters
        ----------
        inputs : dict
            ``transform`` (a carrier) and ``rows`` (the stream).

        Returns
        -------
        list of str
            One problem per port that cannot be projected through —
            including the MEMBER's own row rule, asked of the carrier,
            so the second stream is held to the same shape the fitting
            node holds its own to.
        """
        problems = []
        rows = (inputs or {}).get("rows")
        if not isinstance(rows, list):
            problems.append(f"rows must be a list of rows, got {type(rows).__name__}")
        carrier = (inputs or {}).get("transform")
        if not callable(getattr(carrier, "apply", None)):
            problems.append(
                "transform must be a fitted transform's carrier (has "
                f".apply), got {type(carrier).__name__} — wire a "
                "fitted_transform node's 'transform' output"
            )
        elif isinstance(rows, list):
            problems.extend(_carrier_row_problems(carrier, rows))
        return problems

    def run(self, ctx, inputs):
        """Transform the wired stream with the wired state.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; unused — a projection reads no run state.
        inputs : dict
            ``transform`` and ``rows``.

        Returns
        -------
        dict
            ``{"rows": [...]}`` — one row per input row.
        """
        rows = inputs["transform"].apply(inputs["rows"])
        self.log.info("projected %d row(s) through a fitted transform", len(rows))
        return {"rows": rows}


class Standardize(FittedTransform):
    """Centre and scale declared features on the fit split's statistics.

    The family's first member, and the shape every later one follows:
    two methods, no mode handling, no leakage rule of its own. The state
    is a mean and a standard deviation per feature, learned from
    ``fit_split`` alone and applied to every row — which is exactly the
    train/val/test discipline a hand-rolled scaler gets wrong.

    A zero-variance feature scales by 1.0 rather than dividing by zero,
    so a constant column becomes a constant zero instead of NaN. A row
    whose feature is absent or non-numeric keeps its absence: no value is
    invented to make the arithmetic work. But a feature with no usable
    value ANYWHERE in the fit split REFUSES by name — nothing to learn
    from is indistinguishable from a misspelt name, and the 0.0/1.0
    identity a lenient fit would learn is exactly the silent wrong run
    default-deny exists to prevent.

    Parameters
    ----------
    params : dict
        ``features`` (a non-empty list of row field names, required)
        plus :class:`FittedTransform`'s knobs.

    Examples
    --------
    Scale two features on the training split::

        node = Standardize("scaler", {
            "fit_split": "train", "features": ["ret_lag_0", "ret_lag_1"],
        })
        out = node.run(ctx, {"rows": rows})
        # -> {"transform": ..., "rows": [...], "metrics": {...}}
    """

    _PARAMS = FittedTransform._PARAMS + ("features",)

    def features(self):
        """Name the row fields this scaler standardizes (tuple of str)."""
        return tuple(self.params["features"])

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The base's problems, plus one when ``features`` is missing or
            is not a non-empty list of DISTINCT field names.

        Notes
        -----
        The distinctness half is not a tidiness rule. ``fit`` learns one
        entry per NAME, so a repeated name is silently collapsed — and
        :meth:`state_problems` then compares the declared LIST against
        the state's covered keys, so the very document that fitted and
        wrote the sidecar refuses its own artifact on the load-mode
        rerun, blaming the state for a typo in the plan. The sibling
        pack refuses the identical shape one tier over
        (``_ArrayApply._fields_problems``); a document is read here, so
        this is where it is answered.
        """
        problems = super().validate_params(params)
        features = params.get("features")
        if is_node_ref(features):
            return problems
        if (
            not isinstance(features, (list, tuple))
            or not features
            or any(not isinstance(name, str) or not name for name in features)
        ):
            problems.append(
                "features is required and must be a non-empty list of row "
                f"field names — there is no default, got {features!r}"
            )
            return problems
        dupes = sorted({f for f in features if list(features).count(f) > 1})
        if dupes:
            problems.append(f"features repeats {dupes} — declare each field once")
        return problems

    def row_problems(self, rows):
        """Refuse a stream this scaler cannot honestly project.

        Two ways, and the second is the one that matters. A row that is
        not a mapping cannot be rebuilt. And a declared feature that NOT
        ONE row of a non-empty stream carries has no honest reading: the
        fit doorway already refuses it (``fit``, "nothing to learn from
        is indistinguishable from a misspelt one") and the load doorway
        refuses it (:meth:`state_problems`), while the apply doorway
        projected the stream untouched and said nothing — so the model
        downstream is fed a raw column forever, which is exactly the
        train/serve skew this hook exists to close on BOTH doorways.

        Per-row absence stays the documented policy: a row missing the
        feature keeps its absence. An EMPTY stream is not a missing
        feature either — nothing to look in is not looking and not
        finding.

        Parameters
        ----------
        rows : list
            The stream, whichever doorway asked.

        Returns
        -------
        list of str
            One problem naming the first non-mapping row, or one naming
            the declared features the whole stream lacks; none when the
            scaler can project the stream.
        """
        for i, row in enumerate(rows):
            if isinstance(row, dict):
                continue
            return [
                f"rows[{i}] is a {type(row).__name__} — "
                f"{type(self).__name__} rebuilds each row as a mapping, so "
                "every row must be one"
            ]
        if not rows:
            return []
        absent = [
            name for name in self.features()
            if all(_numeric(_field(row, name)) is None for row in rows)
        ]
        if absent:
            return [
                f"not one of the {len(rows)} row(s) carries a usable number "
                f"for {absent} — a declared feature the whole stream lacks "
                "would ride through unscaled while every document said it "
                "was scaled. Check the spelling against the rows upstream "
                "emits, or drop the feature"
            ]
        return []

    def state_problems(self, state):
        """Refuse a restored state that does not cover the declared features.

        ``apply_state`` iterates the STATE, so a serving document naming
        a feature the state never learned would scale nothing and say
        nothing: the model is fed one raw column forever, the metrics
        look identical, and no refusal fires. That is the train/serve
        skew this family exists to make impossible.

        Parameters
        ----------
        state : dict
            The restored ``{"mean": ..., "std": ...}``.

        Returns
        -------
        list of str
            One problem when the declared features and the state's
            covered features are not the same set.
        """
        covered = sorted(state.get("mean", {}))
        declared = sorted(self.features())
        if covered == declared:
            return []
        return [
            f"features {declared} but the restored state covers {covered}"
        ]

    def state_metrics(self, state):
        """Report how many features the state covers.

        Parameters
        ----------
        state : dict
            The fitted state.

        Returns
        -------
        dict
            ``{"n_features": ...}``.
        """
        return {"n_features": len(state.get("mean", {}))}

    def fit(self, rows, params):
        """Learn a mean and a standard deviation per declared feature.

        Parameters
        ----------
        rows : list of dict
            The fit split's rows.
        params : dict
            This node's params; the features are read through the accessor.

        Returns
        -------
        dict
            ``{"mean": {feature: float}, "std": {feature: float}}``, one
            entry per declared feature.

        Raises
        ------
        ValueError
            When a declared feature has NO usable value anywhere in the
            fit split. Learning 0.0/1.0 for it would be an identity
            transform nobody asked for: a typo'd name emits a clean,
            successful, wrong run — the real column unscaled, the
            phantom counted in ``n_features``, and the load-mode
            cross-check agreeing with itself because declared and
            covered are the same wrong name. Per-row absence is a
            different case and keeps the documented policy.
        """
        mean, std, unlearnable = {}, {}, []
        for name in self.features():
            values = [v for v in (_numeric(_field(row, name)) for row in rows)
                      if v is not None]
            if not values:
                unlearnable.append(name)
                continue
            centre = sum(values) / len(values)
            spread = math.sqrt(sum((v - centre) ** 2 for v in values) / len(values))
            mean[name] = centre
            std[name] = spread if spread > 0.0 else 1.0
        if unlearnable:
            raise ValueError(
                f"{self.key}: not one of the {len(rows)} fit row(s) carries a "
                f"usable number for {unlearnable} — a feature with nothing to "
                "learn from is indistinguishable from a misspelt one, and "
                "standardizing it by 0.0/1.0 would leave the column raw while "
                "every document said it was scaled. Check the spelling against "
                "the rows the upstream node emits, or drop the feature"
            )
        return {"mean": mean, "std": std}

    def apply_state(self, state, rows, params):
        """Centre and scale each row's features by the fitted statistics.

        Pure and row-independent by construction: nothing but the row and
        the state is read.

        Parameters
        ----------
        state : dict
            ``{"mean": ..., "std": ...}``.
        rows : list of dict
            Every row of the stream.
        params : dict
            This node's params; unused — the state carries the numbers.

        Returns
        -------
        list of dict
            One rebuilt row per input row; untouched columns ride along,
            and a feature this row lacks stays as it was.
        """
        mean, std = state["mean"], state["std"]
        out = []
        for row in rows:
            scaled = dict(row)
            for name, centre in mean.items():
                value = _numeric(row.get(name))
                if value is None:
                    continue
                scaled[name] = (value - centre) / std[name]
            out.append(scaled)
        return out


#: kind name -> class, for the registry and the conformance census.
NODE_KINDS = (("standardize", Standardize), ("apply-transform", ApplyTransform))


def register(registry=None):
    """Register the fitted-transform kinds, ``owned=False``.

    Idempotent by SKIPPING any name already present — never shadowing an
    existing registration. Nothing registers at import time; calling this
    is the explicit opt-in.

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means
        :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`.

    Returns
    -------
    NodeKindRegistry
        The same registry, for chaining.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry

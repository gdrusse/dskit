"""Classification before construction — the serving policy seam (ADR-0091).

A served run has an earlier boundary than an ordinary one. The ordinary
path constructs every node at RESOLVE (a ``data`` node's constructor
scans its stream and fingerprints it), and only then executes. A served
tick must know BEFORE anything is constructed which nodes may run at all:
which one is the single mutable read (the entry), which read only
manifest-named values through a release reader, which are pure, and
which are forbidden. That question is asked of a planned DAG — class
metadata and topology, nothing instantiated — and answered by an
:class:`ExecutionPolicy`.

The policy object lives HERE, in the pipeline, because the structural
planner calls it and the pipeline may not import the package that
supplies the serving subclass (the dependency arrow points the other
way: a serving package imports the toolkit, never the reverse). What
this module knows is exactly the closed vocabulary
:data:`~dskit.pipeline.node.SERVING_EFFECTS` and how to walk a plan with
it; entry dominance and the "sole ``entry_read``" rule are the serving
policy's own business and are NOT enforced here.

Import cost: stdlib only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from dskit.pipeline.base import ConfigError
from dskit.pipeline.node import SERVING_EFFECTS

__all__ = ["ExecutionPolicy", "classify_plan"]


class ExecutionPolicy(ABC):
    """What a driver asks about a node before and while executing a served DAG.

    Three hooks. :meth:`classify` is abstract — a policy that cannot
    say what a node's serving effect is cannot construct. :meth:`defer`
    and :meth:`reader` are concrete no-ops, so the search seam's
    behaviour (``policy=None``) is also the behaviour of a policy that
    overrides neither: nothing is deferred and no node receives a
    release reader.

    Examples
    --------
    A policy that trusts every node and defers one key::

        class TrustAll(ExecutionPolicy):
            def classify(self, key, cls, params, evidence):
                return "pure"

            def defer(self, key):
                return key == "bars"

        policy = TrustAll()
        policy.classify("bars", object, {}, {})   # 'pure'
        policy.defer("bars")                      # True
        policy.reader("bars") is None             # True
    """

    @abstractmethod
    def classify(self, key, cls, params, evidence):
        """Name the serving effect of one planned node.

        Parameters
        ----------
        key : str
            The node's key in the document.
        cls : type
            The resolved node class — asked, never instantiated.
        params : dict
            The node's declared params, as the document states them.
        evidence : dict
            What the release verified about the node's training run;
            empty when nothing was.

        Returns
        -------
        str
            A member of :data:`~dskit.pipeline.node.SERVING_EFFECTS`.
        """
        raise NotImplementedError

    def defer(self, key):
        """Say whether ``key`` is executed elsewhere and only SEEDED here.

        Parameters
        ----------
        key : str
            The node's key in the document.

        Returns
        -------
        bool
            ``False`` — the base defers nothing.
        """
        return False

    def reader(self, key):
        """Hand ``key`` its release reader, or nothing.

        Parameters
        ----------
        key : str
            The node's key in the document.

        Returns
        -------
        object or None
            The reader the node receives as ``ctx.release_reader`` —
            an object answering ``get(name)`` with a manifest-named,
            digest-checked value — or ``None`` for no reader, which is
            the base's answer for every key.
        """
        return None


def classify_plan(the_plan, policy, evidence_by_key):
    """Ask ``policy`` for every planned node's serving effect, constructing nothing.

    Walks ``the_plan.order`` and hands the policy each node's resolved
    class, declared params and release evidence. Every answer is held to
    the closed vocabulary, and every out-of-vocabulary answer is
    collected so one refusal names all of them.

    Parameters
    ----------
    the_plan : Plan
        The planned DAG (:func:`~dskit.pipeline.planner.plan`).
    policy : ExecutionPolicy
        The classifier.
    evidence_by_key : dict
        Node key -> the evidence dict for that node; a key absent here
        is classified with ``{}``.

    Returns
    -------
    dict
        Node key -> effect, in plan order.

    Raises
    ------
    ConfigError
        When any answer is not a member of
        :data:`~dskit.pipeline.node.SERVING_EFFECTS` — one problem per
        offending node, raised once.
    """
    effects, problems = {}, []
    for key in the_plan.order:
        effect = policy.classify(
            key,
            the_plan.resolved[key].cls,
            the_plan.document.expanded[key].params,
            evidence_by_key.get(key, {}),
        )
        if effect not in SERVING_EFFECTS:
            problems.append(
                f"pipeline.{key}: the policy answered {effect!r}, which is not "
                f"a serving effect (one of {list(SERVING_EFFECTS)})"
            )
        effects[key] = effect
    if problems:
        raise ConfigError(problems)
    return effects

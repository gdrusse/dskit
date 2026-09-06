"""``accounting`` — the child's books: a live accounting template.

``dskit.production`` ships deterministic paper and recorded accounting.
Live accounting cannot be shipped, because only the child knows how its
venue reports positions, what a "reduction" means for its instruments,
and which monotonic tokens prove that two snapshots are ordered.

Three hooks decide real behaviour:

* :meth:`classify` is what D12 means by "the accounting strategy, not a
  model claim, must prove each proposal cannot increase absolute
  exposure". A proposal that *says* it reduces is still measured.
* :meth:`snapshot` returns the correction-aware evidence the guards size
  against; missing evidence refuses rather than guesses.
* :meth:`value` marks the portfolio for ``tick.nav``, answering ``None``
  when a required mark is missing — a recorded gap, never a guess.

Fail-closed by default: every hook refuses until a child implements it.
"""

from __future__ import annotations

from dskit.production.accounting import Accounting

__all__ = ["LiveBooks"]


class LiveBooks(Accounting):
    """Venue-backed accounting — the evidence every live guard sizes against.

    Parameters
    ----------
    params : dict
        Default-deny knobs; ``max_valuation_age_ms`` arrives from the
        document rather than being restated here.

    Examples
    --------
    Named by path in the serve document, like every child seam::

        # "accounting": {"uses": "yourproject.accounting:LiveBooks",
        #                "params": {}, "max_valuation_age_ms": 60000}
        books = LiveBooks({}, clock=clock, history=history)
        books.__class__.__name__
        # -> 'LiveBooks'
    """

    _PARAMS = ()

    def value(self, state_view, quotes, at_ms):
        """Mark the portfolio for ``tick.nav``.

        Parameters
        ----------
        state_view : StateView
            The fold's frozen projection.
        quotes : QuoteSet
            This tick's quotes.
        at_ms : int
            The instant to value at.

        Returns
        -------
        Decimal or None
            ``None`` when a required mark is missing or balances span
            currencies — the equity curve must be able to say it has a
            hole rather than inventing a number.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement value() to publish tick.nav")

    def classify(self, proposal, state):
        """Prove what this proposal does to risk, against the real book.

        Parameters
        ----------
        proposal : Proposal
            The candidate order.
        state : TickState
            The tick's state; ``state.account`` is the economic authority.

        Returns
        -------
        str
            Exactly one member of ``RISK_EFFECTS`` — ``increase``,
            ``neutral`` or ``reduce``, measured against current positions
            AND working orders, never taken from the proposal's own claim.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement classify() — a reduction is proven, not claimed")

    def snapshot(self, state_view, executor, quotes, at_ms, requirements, calendar):
        """Return the correction-aware evidence the guards demand.

        Parameters
        ----------
        state_view : StateView
            The fold's frozen projection.
        executor : Executor
            For venue-reported facts and monotonic source tokens.
        quotes : QuoteSet
            This tick's quotes; they must satisfy the document's
            ``max_valuation_age_ms``.
        at_ms : int
            The instant every requirement is re-anchored at.
        requirements : tuple of EvidenceRequirement
            The deduplicated union every configured measure declared.
        calendar : Calendar
            Resolves a calendar window to its bounds.

        Returns
        -------
        AccountState
            One fresh ``MeasureEvidence`` per requirement × scope key.
            Missing evidence refuses: a guard that cannot measure must
            not pass.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement snapshot() — guards size against it")

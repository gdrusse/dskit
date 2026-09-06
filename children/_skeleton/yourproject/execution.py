"""``execution`` — the child's venue seam: a live executor template.

The toolkit never learns a venue. ``dskit.production`` ships the shadow,
paper and recorded executors, the guard chain, the ledger and the whole
authority stack; what it cannot ship is the half that knows *your*
broker: the unit conventions, the order types, the error codes, the
dedup semantics and the fencing token the gateway enforces.

This template is deliberately **fail-closed**: :meth:`LiveVenue._submit_native`
refuses, so a child that copies the skeleton and forgets to implement it
cannot move money by accident. Fill it in only when the venue integration
is real, and prove it with ``executor_conformance_suite`` (see
``tests/test_execution.py``) — the same battery that proves the toolkit's
own paper executor.

Import cost: stdlib + dskit. A venue SDK is imported inside the method
that needs it, never at module top, so a document naming this class still
plans on a machine without the SDK installed.
"""

from __future__ import annotations

from dskit.production.executor import Capabilities, LiveExecutor

__all__ = ["LiveVenue"]


class LiveVenue(LiveExecutor):
    """A live venue executor — the one class that can reach real money.

    Everything outward-facing is already decided by the time
    :meth:`_submit_native` is called: the guard chain passed, the decision
    plan and the intent are durable, an ``ActPermit`` was minted against a
    current arm, and the final verifier re-checked every binding. This
    class's whole job is to translate one already-authorised intent into
    one venue call, honour the deadline it is given, and report what
    happened without embellishment.

    Parameters
    ----------
    params : dict
        Venue knobs, default-deny over ``_PARAMS``. Credentials are named
        here by ENV VAR, never carried as values.

    Attributes
    ----------
    _PARAMS : tuple of str
        ``endpoint_env`` (str) — the env var holding the API endpoint;
        ``key_env`` (str) — the env var holding the API key.

    Examples
    --------
    A child names it by path in its serve document, never by kind::

        # configs/serve-live.json
        # "execution": {"uses": "yourproject.execution:LiveVenue",
        #               "params": {"endpoint_env": "VENUE_URL",
        #                          "key_env": "VENUE_KEY"}}
        venue = LiveVenue(
            {"endpoint_env": "VENUE_URL", "key_env": "VENUE_KEY"},
            clock=clock, verifier=verifier, lease=lease,
        )
        venue.capabilities().fencing
        # -> 'submit_token'
    """

    _PARAMS = ("endpoint_env", "key_env")

    def capabilities(self):
        """Declare what this venue can do, so the toolkit gates on facts.

        Returns
        -------
        Capabilities
            ``fencing`` MUST be ``"submit_token"`` for a live executor:
            the gateway has to reject a stale token atomically, or two
            processes could both believe they own submit.
        """
        return Capabilities(
            tifs=("ioc", "gtc"),
            market_orders=False,
            notional=False,
            positions="venue",
            settlements=False,
            stream=False,
            dedupe="rejects",
            units={"qty": "share", "price": "USD", "cash": "USD"},
            position_model="netting",
            fencing="submit_token",
        )

    def check(self, config):
        """Say why this venue is not usable, empty when it is.

        Parameters
        ----------
        config : dict
            The resolved execution config.

        Returns
        -------
        tuple of str
            One problem per broken clause. This performs NO submit.
        """
        return ("yourproject: LiveVenue.check is a template — implement it",)

    def execution_scope(self):
        """Return the venue's own authenticated ownership domain.

        Returns
        -------
        ExecutionScope
            What the VENUE says this credential owns. Startup, every tick
            and the final gate require exact equality among this, the
            document, the release, the lease and the permit — so it must
            be read from the venue, never restated from config.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement execution_scope() against your venue")

    def _submit_native(self, intent, permit, timeout_ms):
        """Send one authorised intent and report the venue's answer.

        The child gateway must atomically enforce three things before the
        request leaves: the ``permit``'s fencing token is current, its
        deadline has not passed, and the ``client_ref`` has not been used.
        Honour ``timeout_ms`` — the caller has already bounded it by the
        permit's remaining life, and a call that outlives its permit is
        exactly the race the fence exists to lose.

        Parameters
        ----------
        intent : Intent
            The canonical intent; its ``client_ref`` is the idempotency key.
        permit : ActPermit
            The binding minted for THIS intent. Never cache it.
        timeout_ms : int
            The hard deadline for the whole call.

        Returns
        -------
        Ack
            What happened. A raise or timeout after the request may have
            left is reported by the caller as ``unknown`` and resolved by
            querying — never by resending.

        Raises
        ------
        NotImplementedError
            Always, in the template: a fail-closed default cannot move money.
        """
        raise NotImplementedError(
            "yourproject: implement _submit_native() before serving at a live rung"
        )

    def order(self, ref):
        """Answer the venue's current state for one client reference.

        This is what resolves an ``unknown`` outcome after a crash or a
        timeout, so it must work even when submit does not.

        Parameters
        ----------
        ref : str
            The client reference.

        Returns
        -------
        OrderState
            The venue's state.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement order() — it is how unknowns resolve")

    def cancel(self, ref):
        """Cancel one working order.

        Parameters
        ----------
        ref : str
            The client reference.

        Returns
        -------
        Ack
            The venue's answer.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement cancel() — halt needs it")

    def open_orders(self):
        """List the venue's working orders, for reconciliation.

        Returns
        -------
        tuple of OrderState
            What the venue believes is working.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement open_orders()")

    def fills(self, since_ms, cursor=None):
        """Page the venue's fills since an instant.

        Parameters
        ----------
        since_ms : int
            Epoch-ms lower bound.
        cursor : object, optional
            The previous page's cursor.

        Returns
        -------
        tuple
            ``(page, next_cursor)``; the reconciler pages to exhaustion.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement fills()")

    def balances(self):
        """Report the venue's balances.

        Returns
        -------
        tuple of Balance
            One per currency.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement balances()")

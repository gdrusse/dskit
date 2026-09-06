"""``coordination`` — the child's fenced lease: who owns submit.

A file lock only protects processes that share a filesystem. Two hosts,
two containers, or an old release that did not quite die need something
stronger: a lease with a **monotonic fencing token** that the venue
gateway checks atomically, so a resumed straggler cannot act on an
authority it no longer holds.

The toolkit ships ``ProcessLease`` for shadow and paper. Every live plan
must resolve a child lease class, because the store that arbitrates
ownership — etcd, DynamoDB, Consul, a database row — is the child's.

The ownership domain is the graded ``ExecutionScope{venue, account}``,
deliberately NOT a release id, so an old release and a new one contend
for the same lease rather than both believing they own it.
"""

from __future__ import annotations

from dskit.production.coordination import Lease

__all__ = ["FencedLease"]


class FencedLease(Lease):
    """A cross-host lease whose token the venue gateway can reject.

    Parameters
    ----------
    params : dict
        Default-deny knobs; ``table_env`` names the env var holding the
        coordination store's locator.

    Attributes
    ----------
    LIVE_CAPABLE : bool
        ``True`` — unlike ``ProcessLease``, this one may back a live rung
        once implemented.
    _PARAMS : tuple of str
        ``table_env`` (str).

    Examples
    --------
    Named by path in the serve document::

        # "coordination": {"scope": {"venue": "yourvenue", "account": "strategy-a"},
        #                  "lease": {"uses": "yourproject.coordination:FencedLease",
        #                            "params": {"table_env": "LEASE_TABLE"}},
        #                  "ttl_ms": 30000, "renew_every_ms": 10000,
        #                  "renew_timeout_ms": 2000}
        lease = FencedLease({"table_env": "LEASE_TABLE"})
        lease.LIVE_CAPABLE
        # -> True
    """

    LIVE_CAPABLE = True
    _PARAMS = ("table_env",)

    def acquire(self, scope, holder, ttl_ms):
        """Take the lease for one ownership domain.

        Parameters
        ----------
        scope : ExecutionScope
            The venue/account domain being claimed.
        holder : str
            This process's identity.
        ttl_ms : int
            How long the claim survives without renewal. The document
            requires ``ttl_ms > 2 * (renew_every_ms + renew_timeout_ms)``,
            so one missed renewal is not a lost lease.

        Returns
        -------
        LeasePermit
            Scope, holder, a MONOTONIC ``fencing_token``, and expiry.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement acquire() with a monotonic token")

    def renew(self, permit):
        """Extend a held lease, refusing an expired or foreign permit.

        Parameters
        ----------
        permit : LeasePermit
            The permit to extend.

        Returns
        -------
        LeasePermit
            The extended permit.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement renew()")

    def current(self, scope):
        """Report who holds this scope right now.

        Parameters
        ----------
        scope : ExecutionScope
            The domain to read.

        Returns
        -------
        LeasePermit or None
            The live permit, or ``None`` when the scope is free.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement current()")

    def release(self, permit):
        """Give the lease up.

        Parameters
        ----------
        permit : LeasePermit
            The permit to release.

        Returns
        -------
        None
            Nothing; a failed release simply lets the TTL expire.

        Raises
        ------
        NotImplementedError
            Always, in the template.
        """
        raise NotImplementedError("yourproject: implement release()")

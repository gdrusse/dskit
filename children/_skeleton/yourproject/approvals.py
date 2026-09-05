"""``approvals`` — the child's trust root: an approval-verifier template.

Moving real money needs a recorded, expiring, independently authenticated
maker-checker act bound to the immutable release hash. The toolkit owns
the *protocol* — what must be signed, that maker and checker differ, that
expiry is bounded, that an allowlist may only narrow and an overlay only
tighten. It cannot own the *cryptography*, because the trust root is
yours.

``deny-all`` is the core default and refuses every proof, which is why a
shadow or paper document needs nothing here and a live document must name
a real class by path. Trust roots are public, content-digested inputs
carried in the release; only private keys stay secret.
"""

from __future__ import annotations

from dskit.production.arming import ApprovalVerifier

__all__ = ["SignedApprovals"]


class SignedApprovals(ApprovalVerifier):
    """Verify an operator's signed proof and name the principal behind it.

    Parameters
    ----------
    params : dict
        Default-deny knobs. ``trust_root_env`` names the env var holding
        the PUBLIC trust root — never a private key, and never the key
        material itself.

    Attributes
    ----------
    _PARAMS : tuple of str
        ``trust_root_env`` (str).

    Examples
    --------
    Named by path in the serve document's graded arming block::

        # "arming": {"max_duration_s": 14400,
        #            "approval": {"uses": "yourproject.approvals:SignedApprovals",
        #                         "params": {"trust_root_env": "OPS_TRUST_ROOT"}}}
        verifier = SignedApprovals({"trust_root_env": "OPS_TRUST_ROOT"})
        verifier.__class__.__name__
        # -> 'SignedApprovals'
    """

    _PARAMS = ("trust_root_env",)

    def verify(self, canonical_bytes, proof, purpose):
        """Verify one proof over exactly these bytes, for exactly this purpose.

        The principal is DERIVED from the proof — never taken from a
        ``--by`` flag or any other free-form identity — which is what
        makes "maker and checker differ" a fact rather than a promise.
        A proof valid for one purpose must not verify for another, or an
        arm request could be replayed as a resume.

        Parameters
        ----------
        canonical_bytes : bytes
            Exactly what was signed.
        proof : bytes
            The operator's signature material.
        purpose : str
            A member of ``APPROVAL_PURPOSES``.

        Returns
        -------
        VerifiedPrincipal
            ``id`` (the derived principal) and ``proof_digest``.

        Raises
        ------
        NotImplementedError
            Always, in the template: an unverified proof must never arm.
        """
        raise NotImplementedError(
            "yourproject: implement verify() against your trust root before any live rung"
        )

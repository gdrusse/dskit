"""Certification — a recorded decision over ONE validation result (ADR-0015).

The separation the design insists on: validation produces EVIDENCE (a
gating and per-rule counts); certification is the human/policy DECISION
over that evidence — ``certified`` or ``refused`` — and a refusal is as
much a record as an approval. Certification consumes the result record,
never the data.

One governance rule is enforced here rather than left to policy: a
result whose gating is ``block`` cannot be certified. The block
threshold exists to gate publication; a certifier who disagrees with a
rule amends the SUITE (a new suite hash, a new result — auditable),
never overrides the gate silently. Refusing a blocked result is fine —
that is the gate working.

Import cost: stdlib + this package.
"""

from __future__ import annotations

from .base import AssetError, _check_str, _raise_if

__all__ = ["DECISIONS", "certify"]

#: The decision vocabulary — closed, like modes.
DECISIONS = ("certified", "refused")


def certify(registry, result_vid, decision, certified_by="", origin="certify") -> str:
    """Record one certification decision; return its version_id.

    Parameters
    ----------
    registry : Registry
        The P2 registry holding the validation result.
    result_vid : str
        The ``validation_result`` record's version_id.
    decision : str
        ``"certified"`` or ``"refused"``.
    certified_by : str
        Who decided — provenance the payload carries (it is part of the
        record's identity: the same result refused by two people is two
        certifications).
    origin : str
        Provenance stamp on the store record.

    Returns
    -------
    str
        The ``certification`` record's version_id.

    Raises
    ------
    AssetError
        If the result is not a ``validation_result``, the decision is
        outside the vocabulary, or the decision is ``certified`` over a
        ``block`` gating.
    """
    errors = []
    _check_str(errors, "result_vid", result_vid)
    _check_str(errors, "certified_by", certified_by, non_empty=False)
    _check_str(errors, "origin", origin)
    if decision not in DECISIONS:
        errors.append(f"decision must be one of {list(DECISIONS)}, got {decision!r}")
    _raise_if(errors)

    result = registry.get(result_vid)
    if result.kind != "validation_result":
        raise AssetError(
            [f"{result_vid!r} is a {result.kind!r}, not a validation_result"]
        )
    gating = result.payload["gating"]
    if decision == "certified" and gating == "block":
        raise AssetError(
            ["a BLOCK result cannot be certified — amend the suite (a new, "
             "auditable identity) if the rule is wrong; the gate is not "
             "overridable in place"]
        )

    snapshot_vid = result.refs["snapshot"]
    return registry.register(
        "certification",
        {
            "name": f"{result.payload['name']}:{decision}",
            "decision": decision,
            "certified_by": certified_by,
        },
        refs={"snapshot": snapshot_vid, "result": result_vid},
        origin=origin,
    )

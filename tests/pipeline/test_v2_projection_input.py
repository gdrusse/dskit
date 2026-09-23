"""ADR-0172: one-shot verified synthetic v2 projection input."""

import json
from types import MappingProxyType

import pytest

from dskit.pipeline import trust
from dskit.production import bundles
from dskit.production import verifier as production_verifier
from tests.pipeline.test_v2_raw_publication import _case


def _policy(roster):
    """Build the independently expected immutable rank policy."""
    authorization = json.loads(roster[0])
    return MappingProxyType({
        "schema_version": "dskit.source-rank-policy/v1",
        "sources": tuple(
            MappingProxyType({"source_id": source_id, "rank": rank})
            for rank, source_id in enumerate(authorization["source_ids"])
        ),
        "policy_sha256": authorization["source_rank_policy_sha256"],
    })


def test_genuine_v2_root_projects_once_to_exact_adr0171_bytes(tmp_path):
    """A genuine ADR-0173 root is the sole input to the pure projector."""
    (
        _roster_publisher,
        raw_publisher,
        fixture,
        environment,
        signed,
        roster,
        _source,
    ) = _case(tmp_path)
    output = raw_publisher.publish_v2(
        fixture, environment, *signed, *roster
    )
    raw_proof = raw_publisher.proof()
    capability = trust._prepare_synthetic_v2_projection_input(
        raw_proof, (*signed, *roster, *output)
    )

    projected = production_verifier._project_verified_synthetic_v2_input(
        capability
    )

    assert projected == bundles._project_v2_event_envelopes(
        fixture._events, _policy(roster)
    )
    with pytest.raises(ValueError, match="spent"):
        production_verifier._project_verified_synthetic_v2_input(capability)

"""Closed raw-event wire declarations shared by trust and bundle validation."""

from types import MappingProxyType

__all__ = (
    "AUTHORIZATION_SCOPE_FIELDS",
    "DATASET_AUTHORIZATION_EVENT_SCHEMAS",
    "RAW_EVENT_FIELDS",
    "ROSTER_AUTHORIZATION_EVENT_SCHEMAS",
)

RAW_EVENT_FIELDS = MappingProxyType({
    "dskit.raw-event/v1": (
        "schema_version",
        "source_id",
        "event_id",
        "source_sequence",
        "availability_ms",
        "payload_sha256",
    ),
    "dskit.raw-event/v2": (
        "schema_version",
        "source_id",
        "event_id",
        "source_sequence",
        "availability_ms",
        "payload_sha256",
        "exchange_ms",
        "receive_ms",
        "source_provenance_tag",
        "source_timezone_tag",
        "correction_position",
        "corrects_event_id",
    ),
})

DATASET_AUTHORIZATION_EVENT_SCHEMAS = MappingProxyType({
    "dskit.dataset-capture-authorization/v1": "dskit.raw-event/v1",
    "dskit.dataset-capture-authorization/v2": "dskit.raw-event/v2",
})

ROSTER_AUTHORIZATION_EVENT_SCHEMAS = MappingProxyType({
    "dskit.roster-bootstrap-authorization/v1": "dskit.raw-event/v1",
    "dskit.roster-bootstrap-authorization/v2": "dskit.raw-event/v2",
})

AUTHORIZATION_SCOPE_FIELDS = MappingProxyType({
    "dskit.raw-event/v1": (
        "availability_start_ms",
        "availability_end_ms",
        "source_provenance_sha256",
    ),
    "dskit.raw-event/v2": (
        "availability_start_ms",
        "availability_end_ms",
        "source_provenance_sha256",
        "tzdata_version_sha256",
    ),
})

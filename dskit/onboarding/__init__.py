"""``dskit.onboarding`` — Acquisition & Onboarding: connectors, snapshots,
validation, certification, publication.

Package 2 of the master specs, designed in ADR-0012…0016 and built on
the assets engine reused as a library (ADR-0013):

- **Evidence lives in a P2-local assets store** governed by the
  ratified :func:`onboarding_model` — kinds as config (ADR-0007), the
  chain ``source_config -> acquisition_job -> snapshot ->
  validation_result -> certification -> published_version`` enforced
  as ref topology.
- **Mode is first-class** (ADR-0014): every pull declares
  ``backfill | live``; checkpoints are keyed per (source, stream,
  mode); every record carries ``(effective_date, acquired_at)``.
- **Raw is WORM** (ADR-0014): each pull is an immutable snapshot with
  a Merkle manifest; ``verify`` re-hashes for tamper evidence.
- **The published root IS the outbox** (ADR-0012): publication writes
  a pointer manifest; P1's ``sync-published`` scan is delivery and
  anti-entropy in one.

The usual session::

    from dskit.onboarding import OnboardingRoot, run_acquisition

    root = OnboardingRoot.create("onboarding_root")
    registry = root.registry()
    # register-source, then:
    run_acquisition(root, registry, "vendor", "prices", "live")

Or from the shell: ``python -m dskit.onboarding --help``.

Import cost: stdlib + :mod:`dskit.assets` (ADR-0013) — nothing else.
"""

from .acquire import find_active_source, run_acquisition
from .base import MODES, AssetError, canonical_hash, file_digest, parse_utc
from .certify import DECISIONS, certify
from .connector import (
    DEFAULT_CONNECTORS,
    MESSAGE_TYPES,
    PROTOCOL,
    RECORD_KINDS,
    Connector,
    check_config,
    check_message,
    resolve_connector,
)
from .coverage import STATUSES, CoverageLedger
from .default_model import onboarding_model
from .layout import OnboardingRoot
from .observations import scan_stream, stream_digest
from .publish import publish_version
from .snapshot import (
    build_manifest,
    find_snapshot_dir,
    read_manifest,
    snapshot_hash,
    verify_snapshot,
    write_snapshot,
)
from .state import load_state, save_state
from .validate import Rule, ValidationSuite, load_suite, run_suite, suite_hash

__all__ = [
    "AssetError",
    "Connector",
    "CoverageLedger",
    "DECISIONS",
    "DEFAULT_CONNECTORS",
    "MESSAGE_TYPES",
    "MODES",
    "OnboardingRoot",
    "PROTOCOL",
    "RECORD_KINDS",
    "Rule",
    "STATUSES",
    "ValidationSuite",
    "build_manifest",
    "canonical_hash",
    "certify",
    "check_config",
    "check_message",
    "file_digest",
    "find_active_source",
    "find_snapshot_dir",
    "load_state",
    "load_suite",
    "onboarding_model",
    "parse_utc",
    "publish_version",
    "read_manifest",
    "resolve_connector",
    "run_acquisition",
    "run_suite",
    "save_state",
    "scan_stream",
    "snapshot_hash",
    "stream_digest",
    "suite_hash",
    "verify_snapshot",
    "write_snapshot",
]

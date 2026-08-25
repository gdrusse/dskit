"""``yourproject`` — a dskit child (copy of ``children/_skeleton``).

The child pattern (ADR-0021): dskit stays generic, THIS package holds the
tier-3 code — connectors, node kinds — and ``configs/`` holds the domain
as JSON. Import = registration: importing this package registers its node
kinds, which is exactly what ``--adapter yourproject`` on the pipeline
CLI does, so a document can name ``yourproject-*`` kinds with no flag
beyond that one import.
"""

from .connectors import SampleConnector
from .nodes import NODE_KINDS, EnrichRecords, SampleRecords

__all__ = [
    "EnrichRecords",
    "NODE_KINDS",
    "SampleConnector",
    "SampleRecords",
]

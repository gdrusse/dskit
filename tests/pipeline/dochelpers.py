"""Shared builders for node-map tests — thin delegation to the package's
own demo builders (:mod:`dskit.pipeline.synthetic_nodes`), so tests and
the ``nodemap`` CLI demo exercise the SAME document."""

from dskit.pipeline.synthetic_nodes import (
    DEMO_SPLITS as BANKING_SPLITS,
)
from dskit.pipeline.synthetic_nodes import (
    demo_document as banking_document,
)
from dskit.pipeline.synthetic_nodes import (
    demo_pipeline as banking_pipeline,
)
from dskit.pipeline.synthetic_nodes import (
    demo_registry as make_registry,
)

DAY = 24 * 60 * 60 * 1000

__all__ = [
    "BANKING_SPLITS",
    "DAY",
    "banking_document",
    "banking_pipeline",
    "make_registry",
]

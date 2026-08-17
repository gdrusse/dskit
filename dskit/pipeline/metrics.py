"""Per-observation scoring rules for the toolkit's generic validate stage.

``ValidationConfig.metric`` names an entry in :data:`METRICS`; membership
is checked at RESOLVE time (fail early, on the machine that runs), not at
validate time. Each metric is a per-observation loss ``metric(q, y) ->
float`` — LOWER is better — where ``q`` is the signal's belief and ``y``
the realized payout per unit mapped through the venue's ``Accounting``
seam. For binary venues ``y`` is exactly 0.0 or 1.0, which is what the two
built-ins assume; a mark-to-market venue registers its own rule via
:func:`register_metric` (e.g. squared return error) rather than bending a
probability score.

Beliefs are clipped to ``[CLIP, 1 - CLIP]`` before the log — a hard 0/1
belief that turns out wrong is a model defect to SEE in a large-but-finite
loss, not an ``inf`` that poisons every aggregate downstream.

Import cost: stdlib only.
"""

from __future__ import annotations

import math

__all__ = ["CLIP", "METRICS", "brier", "logloss", "register_metric"]

#: Belief clip bound for log scoring (symmetric: ``[CLIP, 1 - CLIP]``).
CLIP = 1e-6


def _check_binary(metric, q, y):
    for name, v in (("q", q), ("y", y)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{metric}: {name} must be a number, got {v!r}")
        if not math.isfinite(v):
            raise ValueError(f"{metric}: {name} must be finite, got {v!r}")
    if not (0.0 <= q <= 1.0):
        raise ValueError(f"{metric}: q must lie in [0, 1], got {q!r}")
    if y not in (0.0, 1.0):
        raise ValueError(
            f"{metric}: y must be 0.0 or 1.0 (a binary payout per unit), got {y!r}"
        )


def logloss(q, y) -> float:
    """Per-observation negative log-likelihood, beliefs clipped at ``CLIP``."""
    _check_binary("logloss", q, y)
    qc = min(max(float(q), CLIP), 1.0 - CLIP)
    return -math.log(qc if y == 1.0 else 1.0 - qc)


def brier(q, y) -> float:
    """Per-observation squared error ``(q - y)**2``."""
    _check_binary("brier", q, y)
    return (float(q) - float(y)) ** 2


#: The metric registry ``ValidationConfig.metric`` resolves against.
METRICS = {"logloss": logloss, "brier": brier}


def register_metric(name, fn) -> None:
    """Add a venue/experiment-specific scoring rule. Duplicates raise."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"metric name must be a non-empty string, got {name!r}")
    if not callable(fn):
        raise ValueError(f"metric {name!r} must be callable, got {fn!r}")
    if name in METRICS:
        raise ValueError(f"metric {name!r} is already registered")
    METRICS[name] = fn

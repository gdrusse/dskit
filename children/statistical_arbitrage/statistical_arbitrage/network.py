"""Tier-3 entry point for the generic yearly precision-network engine."""
from __future__ import annotations

from dskit.pipeline.libs.graphical_lasso import RollingPrecisionGraph

__all__ = ["NetworkEngine"]


class NetworkEngine(RollingPrecisionGraph):
    """Use the generic graph contract with this child's JSON universe policy."""

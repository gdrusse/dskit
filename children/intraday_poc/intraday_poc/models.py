"""``models`` — the empty bespoke-architecture seam.

The zoo pack ships the standard nets. This child names ``arch: lstm``
on ``torch-ts-train``; it does not subclass ``nn.Module``. Add a class
here only when the architecture is genuinely bespoke — ADR-0025's
declared seam still exists for that. Until then this module imports
nothing heavy, and nothing in the child's import surface imports it.
"""

from __future__ import annotations

__all__ = []

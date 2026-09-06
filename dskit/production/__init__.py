"""``dskit.production`` — the production layer: serve, guard, act, record, monitor.

An APPLICATION of the toolkit, not part of it: it composes ``dskit.pipeline``,
``dskit.onboarding`` and ``dskit.assets`` into a serve process, and nothing
in ``dskit.pipeline`` ever imports it back. This module is the curated
public surface — re-exports only, no logic; each seam module owns its own
``__all__``.
"""

from dskit.production.base import ProductionError

__all__ = ["ProductionError"]

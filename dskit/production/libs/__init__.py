"""Tier-2 packs — a library behind a seam this package already owns (§8).

One module per library (``parquet.py``; ``sqlite.py`` when the second
ledger lands, and the phase-3 calendar and metric-sink packs after it).
Each pack answers to the same import rule as the core — module level is
stdlib plus the toolkit — and names its library only INSIDE a method, so
importing a pack never imports the library, and a serve process that
declares none pays for none. ``tests/production/test_purity.py`` enforces
both halves, statically and by importing every pack in a fresh
interpreter with its library blocked.

A pack adds a MEMBER to an existing §4.3 family rather than a family of
its own wherever one fits: ``parquet.py`` registers ``run`` into
``monitors.REFERENCE_KINDS``. Import is registration, so a document that
names ``run`` needs the pack imported — which the resolver does when it
sees the name, exactly as it does for a ``pkg.module:Class`` reference.

This ``__init__`` deliberately imports NO pack: importing the production
layer must never pay for a library the serve document does not declare.

Import cost: nothing at all.
"""

__all__ = []

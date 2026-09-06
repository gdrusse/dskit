"""Tier-2 packs — a library behind a seam this package already owns (§8).

One module per library (``parquet.py`` and ``sqlite.py``; the phase-3
calendar and metric-sink packs after them).
Each pack answers to the same import rule as the core — module level is
stdlib plus the toolkit — and names its library only INSIDE a method, so
importing a pack never imports the library, and a serve process that
declares none pays for none. ``tests/production/test_purity.py`` enforces
both halves, statically and by importing every pack in a fresh
interpreter with its library blocked.

A pack adds a MEMBER to an existing §4.3 family rather than a family of
its own wherever one fits: ``parquet.py`` registers ``run`` into
``monitors.REFERENCE_KINDS`` and ``sqlite.py`` registers ``sqlite`` into
``ledger.LEDGER_KINDS``. Import is registration, so a document that names
``run`` or ``sqlite`` needs the pack imported — a child's adapter does it,
or the document names the class by ``pkg.module:Class`` reference, which
the resolver imports itself.

``sqlite3`` ships with Python and is still named only inside a method: the
rule is about what importing the production layer COSTS, not about who
publishes the library, and ``tests/production/test_purity.py`` matches a
pack to the library its own filename names for exactly that reason.

This ``__init__`` deliberately imports NO pack: importing the production
layer must never pay for a library the serve document does not declare.

Import cost: nothing at all.
"""

__all__ = []
